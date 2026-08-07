from django.apps import AppConfig


class DtbConfig(AppConfig):
    name = 'aa_discord_telegram_bridge'
    verbose_name = 'Discord-Telegram Bridge'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import aa_discord_telegram_bridge.signals  # noqa: F401
        from django.db.models.signals import post_migrate
        post_migrate.connect(_on_post_migrate, sender=self)


def _on_post_migrate(sender, **kwargs):
    _register_periodic_tasks()


def _register_periodic_tasks():
    try:
        from django_celery_beat.models import PeriodicTask, CrontabSchedule

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
