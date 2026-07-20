from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def telegram_status_badge(is_active):
    """Render a status badge for Telegram connection."""
    if is_active:
        return mark_safe('<span class="badge badge-success">Active</span>')
    return mark_safe('<span class="badge badge-secondary">Inactive</span>')


@register.filter
def rule_status_badge(is_enabled):
    """Render a status badge for rule enabled state."""
    if is_enabled:
        return mark_safe('<span class="badge badge-success">Enabled</span>')
    return mark_safe('<span class="badge badge-warning">Disabled</span>')


@register.filter
def connection_badge(is_connected):
    """Render a badge for connection status."""
    if is_connected:
        return mark_safe('<span class="badge badge-success">Connected</span>')
    return mark_safe('<span class="badge badge-danger">Disconnected</span>')
