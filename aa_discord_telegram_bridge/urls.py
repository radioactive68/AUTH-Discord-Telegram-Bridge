"""Root URLconf for the DTB app.

The member-facing routes (webhook + portal views) live in
``public_urls.py`` and are included as a nested ``include`` — see the
docstring there for why the nesting matters to Alliance Auth's
``decorate_url_patterns``. The admin routes stay flat in this list: with the
public views nested, the decorator's early ``return`` can no longer reach
them, so every admin view keeps ``main_character_required``.
"""
from django.urls import include, path

from . import views

app_name = 'dtb'

urlpatterns = [
    # Member-facing views + Telegram webhook (see public_urls.py).
    path('', include('aa_discord_telegram_bridge.public_urls')),

    # Admin views — always decorated by AA.
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
    path('admin/logs/', views.admin_logs, name='admin_logs'),
    path('admin/members/', views.admin_members, name='admin_members'),
    path('admin/members/<int:user_pk>/info/', views.admin_member_info, name='admin_member_info'),
    path('admin/members/<int:user_pk>/kick/', views.admin_member_kick, name='admin_member_kick'),
    path('admin/members/telegram/<int:tg_id>/kick/', views.admin_tg_kick, name='admin_tg_kick'),
    path('admin/setup/', views.admin_setup, name='admin_setup'),
]
