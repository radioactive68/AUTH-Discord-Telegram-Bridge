from django.apps import AppConfig
import sys


class DtbConfig(AppConfig):
    name = 'aa_discord_telegram_bridge'
    verbose_name = 'Discord-Telegram Bridge'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        import aa_discord_telegram_bridge.signals  # noqa: F401

        skip_commands = {'migrate', 'makemigrations', 'collectstatic', 'test', 'shell', 'dbshell'}
        current_cmd = sys.argv[1] if len(sys.argv) > 1 else ''
        if current_cmd not in skip_commands:
            from .bot_runner import maybe_start_bot
            maybe_start_bot()

        self._register_periodic_tasks()

    def _register_periodic_tasks(self):
        try:
            from django_celery_beat.models import PeriodicTask, CrontabSchedule
            from django.db import connection

            if not connection.tables_exist(['django_celery_beat_periodictask']):
                return

            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute='15',
                hour='*/6',
                day_of_week='*',
                day_of_month='*',
                month_of_year='*',
            )
            PeriodicTask.objects.get_or_create(
                name='dtb_validate_telegram_users',
                defaults={
                    'task': 'aa_discord_telegram_bridge.tasks.validate_all_telegram_users',
                    'crontab': schedule,
                    'enabled': True,
                },
            )
        except Exception:
            pass
