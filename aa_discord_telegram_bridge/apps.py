from django.apps import AppConfig
import sys


class DtbConfig(AppConfig):
    name = 'aa_discord_telegram_bridge'
    verbose_name = 'Discord-Telegram Bridge'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        import aa_discord_telegram_bridge.signals  # noqa: F401
        from django.db.models.signals import post_migrate
        post_migrate.connect(_ensure_dtb_group, sender=self)

        is_gunicorn = 'gunicorn' in sys.argv[0] if sys.argv else False
        is_celery = 'celery' in sys.argv[0] if sys.argv else False
        is_runserver = sys.argv[1:2] == ['runserver'] if len(sys.argv) > 1 else False

        if is_gunicorn or is_celery or is_runserver:
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


def _ensure_dtb_group(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    try:
        from .models import DTBSettings
        s = DTBSettings.load()
    except Exception:
        return

    ct = ContentType.objects.get_for_model(DTBSettings)
    perm_manage = Permission.objects.filter(codename='manage_dtb_rules', content_type=ct).first()
    if not perm_manage:
        return

    group, _ = Group.objects.get_or_create(name='DTB Admins')

    for codename in ['manage_dtb_rules', 'access_dtb', 'view_forward_history']:
        perm = Permission.objects.filter(codename=codename, content_type=ct).first()
        if perm and perm not in group.permissions.all():
            group.permissions.add(perm)

    is_configured = bool(s.alliance_id)

    try:
        from allianceauth.groupmanagement.models import AuthGroup
        AuthGroup.objects.update_or_create(
            group=group,
            defaults={
                'internal': not is_configured,
                'hidden': not is_configured,
                'open': False,
                'public': False,
                'restricted': False,
                'description': 'Manage Discord-Telegram Bridge rules and settings.'
                    if is_configured else 'DTB Admins - configure alliance_id to enable.',
            },
        )
    except Exception:
        pass

    _ensure_members_group()


def _ensure_members_group():
    from django.contrib.auth.models import Group, Permission
    from allianceauth.groupmanagement.models import AuthGroup

    perm = Permission.objects.filter(codename='request_groups').first()
    if not perm:
        return

    members, _ = Group.objects.get_or_create(name='Members')
    if perm not in members.permissions.all():
        members.permissions.add(perm)

    AuthGroup.objects.get_or_create(
        group=members,
        defaults={
            'internal': False,
            'hidden': False,
            'open': False,
            'public': True,
            'restricted': False,
            'description': 'All authenticated users.',
        },
    )
