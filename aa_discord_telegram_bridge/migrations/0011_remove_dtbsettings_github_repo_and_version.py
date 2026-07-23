from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('aa_discord_telegram_bridge', '0010_remove_dtbsettings_verification_days_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='dtbsettings',
            name='github_repo',
        ),
        migrations.RemoveField(
            model_name='dtbsettings',
            name='version',
        ),
    ]
