from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aa_discord_telegram_bridge', '0015_bigautofield'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='dtbsettings',
            name='autostart_bot',
        ),
    ]
