from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run the Discord->Telegram forwarder bot (blocking).'

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.bot_runner import run_bot
        run_bot()
