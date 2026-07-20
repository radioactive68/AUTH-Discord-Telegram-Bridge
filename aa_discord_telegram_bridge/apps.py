from django.apps import AppConfig


class DtbConfig(AppConfig):
    name = 'aa_discord_telegram_bridge'
    verbose_name = 'Discord-Telegram Bridge'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        import aa_discord_telegram_bridge.signals  # noqa: F401
        from .bot_runner import maybe_start_bot
        maybe_start_bot()
        self._register_periodic_tasks()

    def _register_periodic_tasks(self):
        try:
            from django.conf import settings
            from celery.schedules import crontab
            schedule = getattr(settings, 'CELERYBEAT_SCHEDULE', None)
            if schedule is not None:
                schedule.setdefault('dtb_validate_telegram_users', {
                    'task': 'aa_discord_telegram_bridge.tasks.validate_all_telegram_users',
                    'schedule': crontab(hour='*/6', minute=15),
                })
        except Exception:
            # Celery may not be configured in all environments; ignore.
            pass
