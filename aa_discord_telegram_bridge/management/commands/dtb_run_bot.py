from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run the DTB bot with auto-restart and token wait logic.'

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.bot_runner import maybe_start_bot, _periodic_bot_check, run_bot, run_telegram_only
        from aa_discord_telegram_bridge.models import DTBSettings
        import time
        import logging

        logger = logging.getLogger('aa_discord_telegram_bridge.bot_runner')

        while True:
            try:
                s = DTBSettings.load()
                if not s.autostart_bot:
                    print('[DTB] autostart_bot is disabled. Waiting 60s...', flush=True)
                    time.sleep(60)
                    continue
                has_discord = bool(s.discord_bot_token)
                has_telegram = bool(s.telegram_bot_token)
                if not has_discord and not has_telegram:
                    print('[DTB] No tokens configured. Waiting 60s...', flush=True)
                    time.sleep(60)
                    continue
            except Exception:
                time.sleep(60)
                continue

            if has_discord:
                print('[DTB] Starting bot (Discord + Telegram)...', flush=True)
                try:
                    run_bot()
                except Exception as e:
                    print(f'[DTB] Bot crashed: {e}', flush=True)
                    logger.error('DTB: bot crashed: %s', e, exc_info=True)
            else:
                print('[DTB] Starting Telegram-only mode...', flush=True)
                try:
                    run_telegram_only()
                except Exception as e:
                    print(f'[DTB] Bot crashed: {e}', flush=True)
                    logger.error('DTB: bot crashed: %s', e, exc_info=True)

            print('[DTB] Bot exited. Restarting in 5s...', flush=True)
            time.sleep(5)
