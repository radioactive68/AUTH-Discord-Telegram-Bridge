import asyncio
import html
import logging
import os
import tempfile
import threading

logger = logging.getLogger(__name__)


def _pid_is_alive(pid):
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _acquire_lock():
    """Acquire a cross-process lock so only one bot runs at a time.

    If a stale lock file exists (PID in it is dead), it is removed and
    a new lock is acquired.

    Returns the open lock file handle on success, or None if another
    instance already holds the lock.
    """
    lock_path = os.path.join(tempfile.gettempdir(), 'dtb_discord_bot.lock')

    # Stale lock detection: if lock file exists, check if the PID inside is alive
    if os.path.exists(lock_path):
        try:
            with open(lock_path, 'r') as f:
                old_pid = int(f.read().strip())
            if not _pid_is_alive(old_pid):
                logger.info('DTB: removing stale lock file (PID %d is dead)', old_pid)
                os.remove(lock_path)
        except (ValueError, IOError, OSError):
            pass

    try:
        if os.name == 'nt':
            import msvcrt
            f = open(lock_path, 'w')
            f.write(str(os.getpid()))
            f.flush()
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        else:
            import fcntl
            f = open(lock_path, 'w')
            f.write(str(os.getpid()))
            f.flush()
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
    except (IOError, OSError):
        return None


def get_dtb_token():
    from .models import DTBSettings
    return DTBSettings.load().discord_bot_token


def run_bot():
    """Run the Discord->Telegram forwarder bot (blocking).

    Assumes Django settings are already configured by the caller.
    A cross-process lock ensures only a single bot instance (Discord client
    + Telegram polling) runs at a time, even if the command is launched more
    than once (e.g. Django's auto-reloader spawning a child process).
    """
    import discord
    from discord.ext import commands

    token = get_dtb_token()
    if not token:
        logger.error('DTB: Discord bot token not configured. Bot not started.')
        print('[DTB] Discord bot token not configured. Set it in DTB Settings.', flush=True)
        return

    # Single-instance guard: only one bot (Discord + Telegram polling) at a time.
    lock = _acquire_lock()
    if lock is None:
        logger.info('DTB: bot already running elsewhere (lock held), exiting.')
        return

    try:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        bot = commands.Bot(command_prefix='!', intents=intents)

        async def setup_hook():
            from .discord_cog import DiscordForwarderCog
            await bot.add_cog(DiscordForwarderCog(bot))
            print('DTB cog added!', flush=True)

            async def _heartbeat():
                from .models import BotStatus
                from asgiref.sync import sync_to_async
                import os as _os
                invite_counter = 0
                while True:
                    try:
                        await sync_to_async(BotStatus.update_heartbeat)(_os.getpid())
                    except Exception:
                        pass
                    invite_counter += 1
                    if invite_counter >= 120:
                        invite_counter = 0
                        try:
                            from .telegram_handler import sync_invites_for_all_users
                            await sync_to_async(sync_invites_for_all_users)()
                        except Exception:
                            pass
                    await asyncio.sleep(30)

            bot.loop.create_task(_heartbeat())
            print('DTB heartbeat task started.', flush=True)

        @bot.event
        async def on_ready():
            print(f'Logged in as {bot.user} (ID: {bot.user.id})', flush=True)
            print(f'Guilds: {[(g.name, g.id) for g in bot.guilds]}', flush=True)
            print(f'DTB cog active, listening for messages...', flush=True)

        bot.setup_hook = setup_hook
        print(f'Token: {token[:10]}...', flush=True)

        # Start Telegram update polling (handles /start, linking, join requests)
        # in a background thread so the bot can receive user messages without a
        # publicly reachable webhook.
        from .telegram_handler import run_telegram_polling
        threading.Thread(
            target=run_telegram_polling, name='dtb-telegram-poll', daemon=True
        ).start()
        print('[DTB] Telegram polling thread started.', flush=True)

        bot.run(token)
    finally:
        try:
            lock.close()
        except Exception:
            pass


def run_telegram_only():
    """Run Telegram polling without Discord (blocking).

    Used when only a Telegram bot token is configured.  Handles /start,
    account linking and join-request approvals without requiring discord.py.
    """
    from .models import DTBSettings
    s = DTBSettings.load()
    if not s.telegram_bot_token:
        logger.error('DTB: Telegram bot token not configured.')
        return

    lock = _acquire_lock()
    if lock is None:
        logger.info('DTB: bot already running elsewhere (lock held), exiting.')
        return

    try:
        from .telegram_handler import run_telegram_polling

        def _heartbeat():
            import os as _os
            import time as _time
            from .models import BotStatus
            invite_counter = 0
            while True:
                try:
                    BotStatus.update_heartbeat(_os.getpid())
                except Exception:
                    pass
                invite_counter += 1
                if invite_counter >= 120:
                    invite_counter = 0
                    try:
                        from .telegram_handler import sync_invites_for_all_users
                        sync_invites_for_all_users()
                    except Exception:
                        pass
                _time.sleep(30)

        threading.Thread(target=_heartbeat, name='dtb-tg-heartbeat', daemon=True).start()

        logger.info('DTB: starting Telegram polling (no Discord).')
        print('[DTB] Telegram-only mode. Starting polling...', flush=True)
        run_telegram_polling()
    finally:
        try:
            lock.close()
        except Exception:
            pass
