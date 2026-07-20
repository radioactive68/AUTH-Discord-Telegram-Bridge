from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


DTB_VERSION = '1.0.1'

DEFAULT_GITHUB_REPO = 'radioactive68/AUTH-Discord-Telegram-Bridge'


class DTBSettings(models.Model):
    """Singleton model for plugin settings (managed via admin page)."""
    telegram_bot_token = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Telegram Bot API token',
    )
    discord_bot_token = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Discord Bot token',
    )
    discord_guild_id = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Discord Guild (Server) ID',
    )
    alliance_id = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='EVE Alliance ID to enforce membership. Leave empty to disable.',
    )
    telegram_webhook_url = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Telegram webhook URL for receiving updates (for user linking)',
    )
    github_repo = models.CharField(
        max_length=300, blank=True, default=DEFAULT_GITHUB_REPO,
        help_text='GitHub repo for updates, e.g. user/repo',
    )
    autostart_bot = models.BooleanField(
        default=False,
        help_text='Auto-start the Discord forwarder bot inside Alliance Auth '
                  '(no separate process needed). Requires a restart to take effect.',
    )
    version = models.CharField(max_length=50, default=DTB_VERSION)

    class Meta:
        verbose_name = 'DTB Settings'
        verbose_name_plural = 'DTB Settings'
        permissions = (
            ('access_dtb', 'Can access Discord-Telegram Bridge'),
            ('manage_dtb_rules', 'Can manage DTB rules and settings'),
            ('view_forward_history', 'Can view forward history'),
        )

    def __str__(self):
        return 'DTB Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        if not obj.github_repo:
            obj.github_repo = DEFAULT_GITHUB_REPO
            obj.save(update_fields=['github_repo'])
        return obj


class TelegramGroup(models.Model):
    """A Telegram group/channel that the bot is a member of."""
    name = models.CharField(max_length=255)
    telegram_chat_id = models.CharField(max_length=100, unique=True)
    chat_type = models.CharField(
        max_length=20,
        choices=[
            ('supergroup', 'Supergroup'),
            ('group', 'Group'),
            ('channel', 'Channel'),
        ],
        default='supergroup',
    )
    is_active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Telegram Group'
        verbose_name_plural = 'Telegram Groups'
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.telegram_chat_id})'


class ForwardRule(models.Model):
    """Rule for forwarding Discord messages to Telegram."""
    TARGET_TYPE_CHOICES = [
        ('chat_id', 'Chat ID'),
        ('username', 'Username/@'),
    ]

    name = models.CharField(
        max_length=100,
        help_text='Short name for this rule (e.g. "ops", "CTA")',
    )
    discord_channel_id = models.CharField(
        max_length=100,
        help_text='Discord channel ID to listen to',
    )
    discord_channel_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Human-readable Discord channel name (for display)',
    )
    telegram_target = models.CharField(
        max_length=255,
        help_text='Telegram chat ID or @username to forward to',
    )
    telegram_target_type = models.CharField(
        max_length=20,
        choices=TARGET_TYPE_CHOICES,
        default='chat_id',
    )
    keyword_filter = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text='Comma-separated keywords. If set, only messages containing '
                  'one of these words trigger forwarding. Leave empty for all messages.',
    )
    is_enabled = models.BooleanField(default=True)
    priority = models.IntegerField(
        default=0,
        help_text='Higher priority rules are checked first',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Forward Rule'
        verbose_name_plural = 'Forward Rules'
        ordering = ['-priority', 'name']

    def __str__(self):
        status = 'ON' if self.is_enabled else 'OFF'
        return f'[{status}] {self.name}: {self.discord_channel_name} -> {self.telegram_target}'

    def matches_keywords(self, message_text: str) -> bool:
        """Check if message matches keyword filter."""
        if not self.keyword_filter:
            return True
        keywords = [k.strip().lower() for k in self.keyword_filter.split(',') if k.strip()]
        if not keywords:
            return True
        text_lower = message_text.lower()
        return any(kw in text_lower for kw in keywords)


class TelegramUser(models.Model):
    """Links an Auth user to their Telegram account."""
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='telegram_profile',
    )
    telegram_chat_id = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Telegram user chat ID (obtained automatically)',
    )
    telegram_user_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text='Telegram numeric user ID',
    )
    telegram_username = models.CharField(
        max_length=100,
        blank=True,
        default='',
    )
    is_active = models.BooleanField(
        default=False,
        help_text='Whether the user has enabled Telegram notifications',
    )
    notifications_enabled = models.BooleanField(
        default=True,
        help_text='Master toggle for all notifications',
    )
    linked_at = models.DateTimeField(auto_now_add=True)
    last_validated = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Telegram User'
        verbose_name_plural = 'Telegram Users'

    def __str__(self):
        return f'{self.user.username} -> @{self.telegram_username or "not linked"}'


class TelegramLinkRequest(models.Model):
    """Short-lived record created when a user sends /start to the bot.
    Lets the portal auto-link the account without requiring a verification code."""

    chat_id = models.CharField(max_length=64, unique=True)
    telegram_user_id = models.CharField(max_length=64, blank=True, default='')
    username = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Telegram Link Request'
        verbose_name_plural = 'Telegram Link Requests'

    def __str__(self):
        return f'LinkRequest {self.chat_id} (@{self.username})'


class ForwardHistory(models.Model):
    """Log of forwarded messages."""
    rule = models.ForeignKey(
        ForwardRule,
        on_delete=models.SET_NULL,
        null=True,
        related_name='history',
    )
    source_channel = models.CharField(max_length=255)
    target_channel = models.CharField(max_length=255)
    message_preview = models.TextField(
        max_length=500,
        blank=True,
        default='',
    )
    discord_message_id = models.CharField(max_length=100, blank=True, default='')
    forwarded_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Forward History'
        verbose_name_plural = 'Forward History'
        ordering = ['-forwarded_at']

    def __str__(self):
        status = 'OK' if self.success else 'FAIL'
        return f'[{status}] {self.source_channel} -> {self.target_channel} at {self.forwarded_at}'


class ConnectionStatus(models.Model):
    """Track connection status of Discord and Telegram bots."""
    SERVICE_CHOICES = [
        ('discord', 'Discord Bot'),
        ('telegram', 'Telegram Bot'),
    ]

    service = models.CharField(
        max_length=20,
        choices=SERVICE_CHOICES,
        unique=True,
    )
    is_connected = models.BooleanField(default=False)
    last_checked = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default='')
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'Connection Status'
        verbose_name_plural = 'Connection Statuses'

    def __str__(self):
        status = 'Connected' if self.is_connected else 'Disconnected'
        return f'{self.get_service_display()}: {status}'

    @classmethod
    def check_daily(cls) -> bool:
        """Check if we should run daily connection test."""
        status = cls.objects.filter(service__in=['discord', 'telegram']).first()
        if not status or not status.last_checked:
            return True
        return (timezone.now() - status.last_checked).total_seconds() > 86400


class BotStatus(models.Model):
    """Singleton tracking whether the standalone Discord forwarder bot is alive.

    The bot writes a heartbeat here every ~30s; the status page reads it to
    show whether the forwarder process is currently running.
    """

    last_heartbeat = models.DateTimeField(null=True, blank=True)
    pid = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'Bot Status'
        verbose_name_plural = 'Bot Status'

    def __str__(self):
        return f'Bot heartbeat: {self.last_heartbeat}'

    @classmethod
    def update_heartbeat(cls, pid=None):
        cls.objects.update_or_create(
            pk=1,
            defaults={'last_heartbeat': timezone.now(), 'pid': pid},
        )
