from django.db import migrations


class Migration(migrations.Migration):
    """Drop `alliance_id` — access is decided by permissions only.

    Alliance membership is tracked by the Secure Groups app: every alliance
    member holds `securegroups.access_sec_group` (granted through its smart
    group), so DTB no longer needs an EVE alliance ID to decide who may use
    the bridge.
    """

    dependencies = [
        ('aa_discord_telegram_bridge', '0019_restore_access_dtb_permission'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='dtbsettings',
            name='alliance_id',
        ),
    ]
