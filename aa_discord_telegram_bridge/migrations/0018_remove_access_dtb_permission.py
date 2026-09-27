from django.db import migrations


class Migration(migrations.Migration):
    """Drop the never-enforced `access_dtb` permission.

    Service visibility is decided by membership of the alliance configured in
    DTBSettings (see DiscordTelegramBridgeService.service_active_for_user and
    tasks._user_is_dtb_member), so the permission was dead weight that only
    suggested an access gate that did not exist. Django's post-migrate hook
    removes the orphaned permission row, together with any group/user
    assignments pointing at it.
    """

    dependencies = [
        ('aa_discord_telegram_bridge', '0017_linkrequest_token_webhook_secret'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='dtbsettings',
            options={
                'permissions': (
                    ('manage_dtb_rules', 'Can manage DTB rules and settings'),
                    ('view_forward_history', 'Can view forward history'),
                ),
                'verbose_name': 'DTB Settings',
                'verbose_name_plural': 'DTB Settings',
            },
        ),
    ]
