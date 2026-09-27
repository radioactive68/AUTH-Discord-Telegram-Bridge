from django.db import migrations


class Migration(migrations.Migration):
    """Re-add `access_dtb` and actually enforce it.

    Alliance Auth does not check `ServicesHook.access_perm` for a service
    app; the permission information is only handed to the service, which has
    to verify it. `access_dtb` is the "basic access" permission an admin
    grants to the group/state that holds the members who may use the bridge.
    """

    dependencies = [
        ('aa_discord_telegram_bridge', '0018_remove_access_dtb_permission'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='dtbsettings',
            options={
                'permissions': (
                    ('access_dtb', 'Can access Discord-Telegram Bridge'),
                    ('manage_dtb_rules', 'Can manage DTB rules and settings'),
                    ('view_forward_history', 'Can view forward history'),
                ),
                'verbose_name': 'DTB Settings',
                'verbose_name_plural': 'DTB Settings',
            },
        ),
    ]
