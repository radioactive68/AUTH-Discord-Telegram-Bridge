import os
import sys
import time
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger('aa_discord_telegram_bridge.bot_runner')


class Command(BaseCommand):
    help = 'Run the DTB bot with auto-restart and token wait logic.'

    def handle(self, *args, **options):
        from .models import DTBSettings
        from aa_discord_telegram_bridge.bot_runner import run_bot, run_telegram_only

        while True:
            try:
                s = DTBSettings.load()
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
