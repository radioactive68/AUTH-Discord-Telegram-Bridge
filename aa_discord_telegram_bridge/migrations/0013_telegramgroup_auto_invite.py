from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aa_discord_telegram_bridge', '0012_remove_telegramuser_notifications_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramgroup',
            name='auto_invite',
            field=models.BooleanField(
                default=True,
                help_text='Send an invite link to this group when a user links their account.',
            ),
        ),
    ]
