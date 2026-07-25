import asyncio
import logging
import os
import tempfile
import threading
import traceback

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


def get_active_rules():
    from .models import ForwardRule
    return list(ForwardRule.objects.filter(is_enabled=True))


def forward_to_telegram(rule, channel_name, content, message_id, author):
    from .models import ForwardHistory
    from .manager import TelegramBotManager

    # Idempotency: skip if this exact Discord message was already forwarded
    # for this rule (Discord can deliver the same event more than once, e.g.
    # for forum threads / system messages, which would duplicate in Telegram).
    if ForwardHistory.objects.filter(
        rule=rule, discord_message_id=str(message_id), success=True
    ).exists():
        logger.info(
            'DTB: skipping already-forwarded message %s for rule %s',
            message_id, rule.id,
        )
        return False

    telegram_bot = TelegramBotManager()
    text = (
        f'<b>[{rule.name}]</b>\n'
        f'\U0001f464 {author}\n\n'
        f'{content}'
    )
    target = TelegramBotManager.parse_target(rule.telegram_target)
    result = telegram_bot.send_message(
        chat_id=target['chat_id'],
        text=text,
        message_thread_id=target.get('message_thread_id'),
    )
    ForwardHistory.objects.create(
        rule=rule,
        source_channel=f'#{channel_name}',
        target_channel=rule.telegram_target,
        message_preview=content[:500],
        discord_message_id=str(message_id),
        success=result.get('ok', False),
        error_message=result.get('description', '') if not result.get('ok') else '',
    )
    return result.get('ok', False)


def run_bot():
    """Run the Discord->Telegram forwarder bot (blocking).

    Assumes Django settings are already configured by the caller.
    A cross-process lock ensures only a single bot instance (Discord client
    + Telegram polling) runs at a time, even if the command is launched more
    than once (e.g. Django's auto-reloader spawning a child process).
    """
    import discord
    from discord.ext import commands
    from asgiref.sync import sync_to_async

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

        class DTBForwarder(commands.Cog):
            def __init__(self, bot):
                self.bot = bot

            @commands.Cog.listener()
            async def on_message(self, message):
                if message.author.bot:
                    return
                if not message.guild:
                    return

                channel_id = str(message.channel.id)
                print(f'[MSG] #{message.channel.name} ({channel_id}): {message.content[:80]}', flush=True)

                rules = await sync_to_async(get_active_rules)()
                for rule in rules:
                    if rule.discord_channel_id == channel_id:
                        print(f'[FWD] Matched rule: {rule.name} -> {rule.telegram_target}', flush=True)
                        try:
                            ok = await sync_to_async(forward_to_telegram)(
                                rule, message.channel.name, message.content,
                                message.id, message.author.display_name,
                            )
                            print(f'[FWD] Telegram result: {ok}', flush=True)
                        except Exception as e:
                            print(f'[ERROR] {e}', flush=True)
                            traceback.print_exc()

        @bot.event
        async def on_ready():
            print(f'Logged in as {bot.user} (ID: {bot.user.id})', flush=True)
            print(f'Guilds: {[(g.name, g.id) for g in bot.guilds]}', flush=True)
            rules = await sync_to_async(get_active_rules)()
            for r in rules:
                print(f'  Rule: {r.name} | discord:{r.discord_channel_id} -> tg:{r.telegram_target}', flush=True)
            print(f'Listening for messages in {len(rules)} rule(s)...', flush=True)

        async def setup_hook():
            await bot.add_cog(DTBForwarder(bot))
            print('DTB cog added!', flush=True)

            async def _heartbeat():
                from .models import BotStatus
                from asgiref.sync import sync_to_async
                import os as _os
                while True:
                    try:
                        await sync_to_async(BotStatus.update_heartbeat)(_os.getpid())
                    except Exception:
                        pass
                    await asyncio.sleep(30)

            bot.loop.create_task(_heartbeat())
            print('DTB heartbeat task started.', flush=True)

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
            while True:
                try:
                    BotStatus.update_heartbeat(_os.getpid())
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


def maybe_start_bot():
    """Start the bot in a background daemon thread if autostart is enabled.

    run_bot() itself holds a file lock so that only a single bot instance
    runs even when Django is served by multiple workers or the command is
    launched directly.
    """
    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        if not s.autostart_bot:
            return
        has_discord = bool(s.discord_bot_token)
        has_telegram = bool(s.telegram_bot_token)
        if not has_discord and not has_telegram:
            return
    except Exception:
        return

    if has_discord:
        try:
            import discord  # noqa: F401
        except ImportError:
            logger.warning(
                'DTB: discord.py is not installed. '
                'Install it with: pip install discord.py'
            )
            has_discord = False
            if not has_telegram:
                return
            logger.info('DTB: starting Telegram-only mode.')

    def _target():
        try:
            if has_discord:
                run_bot()
            else:
                run_telegram_only()
        except Exception:
            logger.error('DTB: bot thread crashed', exc_info=True)

    t = threading.Thread(target=_target, name='dtb-discord-bot', daemon=True)
    t.start()
    logger.info('DTB: bot autostart thread started.')
