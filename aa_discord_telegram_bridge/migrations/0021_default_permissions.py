from django.db import migrations


MODEL_OPTIONS = {
    'dtbsettings': {
        'permissions': (
            ('access_dtb', 'Can access Discord-Telegram Bridge'),
            ('manage_dtb_rules', 'Can manage DTB rules and settings'),
            ('view_forward_history', 'Can view forward history'),
        ),
        'verbose_name': 'DTB Settings',
        'verbose_name_plural': 'DTB Settings',
        'default_permissions': (),
    },
    'telegramgroup': {
        'verbose_name': 'Telegram Group',
        'verbose_name_plural': 'Telegram Groups',
        'ordering': ['name'],
        'default_permissions': (),
    },
    'forwardrule': {
        'verbose_name': 'Forward Rule',
        'verbose_name_plural': 'Forward Rules',
        'ordering': ['-priority', 'name'],
        'default_permissions': (),
    },
    'telegramuser': {
        'verbose_name': 'Telegram User',
        'verbose_name_plural': 'Telegram Users',
        'default_permissions': (),
    },
    'telegramlinkrequest': {
        'verbose_name': 'Telegram Link Request',
        'verbose_name_plural': 'Telegram Link Requests',
        'default_permissions': (),
    },
    'forwardhistory': {
        'verbose_name': 'Forward History',
        'verbose_name_plural': 'Forward History',
        'ordering': ['-forwarded_at'],
        'default_permissions': (),
    },
    'connectionstatus': {
        'verbose_name': 'Connection Status',
        'verbose_name_plural': 'Connection Statuses',
        'default_permissions': (),
    },
    'botstatus': {
        'verbose_name': 'Bot Status',
        'verbose_name_plural': 'Bot Status',
        'default_permissions': (),
    },
}

MODEL_NAMES = tuple(MODEL_OPTIONS)

DEFAULT_CODENAMES = tuple(
    f'{action}_{name}' for name in MODEL_NAMES for action in
    ('add', 'change', 'delete', 'view')
)


def drop_default_permissions(apps, schema_editor):
    """Remove the now-orphaned add/change/delete/view permission rows.

    ``default_permissions = ()`` only stops Django from *creating* them on
    ``post_migrate``; the rows created by earlier migrations stay in the
    database (and in the admin permission checklist) until deleted here.
    Assignments to users/groups go away with the permission rows — that is
    the point, nothing inside DTB ever checks them.
    """
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ct_ids = ContentType.objects.filter(
        app_label='aa_discord_telegram_bridge',
        model__in=MODEL_NAMES,
    ).values_list('id', flat=True)
    Permission.objects.filter(
        content_type_id__in=list(ct_ids),
        codename__in=DEFAULT_CODENAMES,
    ).delete()


class Migration(migrations.Migration):
    """Stop auto-creating the default CRUD permissions for DTB models.

    ``default_permissions = ()`` keeps Django from adding ``view/add/change/
    delete`` entries for every DTB model to the global permission list: DTB is
    gated by its own ``access_dtb``/``manage_dtb_rules``/``view_forward_
    history`` permissions, and the per-model CRUD entries only clutter the
    permission checklist. The schema itself does not change — only model
    state and the permission rows created by earlier migrations.
    """

    dependencies = [
        ('aa_discord_telegram_bridge', '0020_remove_alliance_id'),
    ]

    # AlterModelOptions replaces every option key it knows about that is not
    # passed, so each model's full option set has to be listed here.
    operations = [
        migrations.AlterModelOptions(name=name, options=options)
        for name, options in MODEL_OPTIONS.items()
    ] + [
        migrations.RunPython(
            drop_default_permissions,
            migrations.RunPython.noop,
            elidable=True,
        ),
    ]
