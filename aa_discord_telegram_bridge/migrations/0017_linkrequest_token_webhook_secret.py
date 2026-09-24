from django.db import migrations, models
import django.db.models.deletion


def delete_all_link_requests(apps, schema_editor):
    """Drop transient legacy pending-link records; the token flow never
    created them, so none are being actively used on first deploy."""
    TelegramLinkRequest = apps.get_model(
        'aa_discord_telegram_bridge', 'TelegramLinkRequest'
    )
    TelegramLinkRequest.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('aa_discord_telegram_bridge', '0016_remove_dtbsettings_autostart_bot'),
    ]

    operations = [
        migrations.AddField(
            model_name='dtbsettings',
            name='telegram_webhook_secret_token',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Secret token Telegram sends with every webhook update (generated automatically)',
                max_length=512,
            ),
        ),
        migrations.RunPython(delete_all_link_requests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='telegramlinkrequest',
            name='chat_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='telegramlinkrequest',
            name='expires_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When this link token stops being valid',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='telegramlinkrequest',
            name='token',
            field=models.CharField(default='', help_text='Random signed token the bot matches /start with', max_length=128, unique=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='telegramlinkrequest',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Auth user requesting the link',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='dtb_link_requests',
                to='auth.user',
            ),
        ),
    ]