from django.contrib import admin
from .models import (
    DTBSettings, ForwardRule, TelegramUser, ForwardHistory,
    ConnectionStatus, TelegramGroup,
)


@admin.register(DTBSettings)
class DTBSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not DTBSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ForwardRule)
class ForwardRuleAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'discord_channel_name', 'telegram_target',
        'is_enabled', 'priority', 'created_at',
    )
    list_filter = ('is_enabled',)
    search_fields = ('name', 'discord_channel_name', 'telegram_target')
    list_editable = ('is_enabled', 'priority')


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'telegram_chat_id', 'telegram_username',
        'is_active', 'linked_at', 'last_validated',
    )
    list_filter = ('is_active',)
    search_fields = ('user__username', 'telegram_username', 'telegram_chat_id')


@admin.register(ForwardHistory)
class ForwardHistoryAdmin(admin.ModelAdmin):
    list_display = (
        'rule', 'source_channel', 'target_channel',
        'forwarded_at', 'message_preview',
    )
    list_filter = ('rule', 'forwarded_at')
    search_fields = ('source_channel', 'target_channel', 'message_preview')
    readonly_fields = ('forwarded_at',)
    date_hierarchy = 'forwarded_at'


@admin.register(ConnectionStatus)
class ConnectionStatusAdmin(admin.ModelAdmin):
    list_display = (
        'service', 'is_connected', 'last_checked', 'last_success',
        'error_message',
    )
    list_filter = ('service', 'is_connected')


@admin.register(TelegramGroup)
class TelegramGroupAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'telegram_chat_id', 'chat_type', 'is_active',
    )
    list_filter = ('chat_type', 'is_active')
    search_fields = ('name', 'telegram_chat_id')
