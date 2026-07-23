from django.urls import path
from . import views
from .telegram_handler import telegram_webhook

app_name = 'dtb'

urlpatterns = [
    # Telegram webhook endpoint (used when a webhook URL is configured)
    path('telegram/webhook/', telegram_webhook, name='telegram_webhook'),

    # User-facing views
    path('', views.services_overview, name='services_overview'),
    path('link/', views.link_telegram, name='link_telegram'),
    path('unlink/', views.unlink_telegram, name='unlink_telegram'),
    path('history/', views.forward_history, name='forward_history'),
    path('status/', views.connection_status, name='connection_status'),

    # Admin views
    path('admin/', views.admin_index, name='admin_index'),
    path('admin/rules/', views.admin_rules, name='admin_rules'),
    path('admin/rules/add/', views.admin_rule_add, name='admin_rule_add'),
    path('admin/rules/<int:rule_id>/edit/', views.admin_rule_edit, name='admin_rule_edit'),
    path('admin/rules/<int:rule_id>/delete/', views.admin_rule_delete, name='admin_rule_delete'),
    path('admin/rules/<int:rule_id>/toggle/', views.admin_rule_toggle, name='admin_rule_toggle'),
    path('admin/groups/', views.admin_groups, name='admin_groups'),
    path('admin/validate/', views.admin_validate_now, name='admin_validate_now'),
    path('admin/test/', views.admin_test_connection, name='admin_test_connection'),
    path('admin/settings/', views.admin_settings, name='admin_settings'),
    path('admin/setup/', views.admin_setup, name='admin_setup'),
]
