from django.db import migrations


def seed_settings(apps, schema_editor):
    from django.conf import settings
    DTBSettings = apps.get_model('aa_discord_telegram_bridge', 'DTBSettings')
    obj, _ = DTBSettings.objects.get_or_create(pk=1)
    # Only fill fields that are currently empty. Never overwrite values the
    # user configured via the admin form (e.g. alliance_id, tokens) — otherwise
    # a re-run of this migration would clobber user settings.
    mapping = {
        'telegram_bot_token': 'DTB_TELEGRAM_BOT_TOKEN',
        'discord_bot_token': 'DTB_DISCORD_BOT_TOKEN',
        'discord_guild_id': 'DTB_DISCORD_GUILD_ID',
        'alliance_id': 'DTB_ALLIANCE_ID',
        'github_repo': 'DTB_GITHUB_REPO',
    }
    changed = False
    for field, setting_name in mapping.items():
        if not getattr(obj, field):
            val = getattr(settings, setting_name, None)
            if val not in (None, ''):
                setattr(obj, field, val)
                changed = True
    if changed:
        obj.save()


def clear_settings(apps, schema_editor):
    DTBSettings = apps.get_model('aa_discord_telegram_bridge', 'DTBSettings')
    DTBSettings.objects.filter(pk=1).update(
        telegram_bot_token='', discord_bot_token='',
        discord_guild_id='', alliance_id=None, github_repo='',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('aa_discord_telegram_bridge', '0002_dtbsettings'),
    ]

    operations = [
        migrations.RunPython(seed_settings, clear_settings),
    ]
