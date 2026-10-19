from django.apps import AppConfig


class DtbConfig(AppConfig):
    name = 'aa_discord_telegram_bridge'
    verbose_name = 'Discord-Telegram Bridge'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import aa_discord_telegram_bridge.signals  # noqa: F401
        from .logging_setup import configure as configure_logging

        # Attach our own handlers before anything logs: AA configures no
        # logger for third-party apps, so without this every INFO line is
        # dropped and the survivors have no timestamp.
        configure_logging()
