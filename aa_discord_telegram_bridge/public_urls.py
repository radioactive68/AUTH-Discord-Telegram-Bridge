"""URL patterns that may be reachable without a main character.

Kept in their own module so Alliance Auth's ``decorate_url_patterns`` can
decide per view: it stops decorating at the first excluded view it finds
*within one pattern list*. Mixed into the flat ``urlpatterns`` (where the
admin views follow), that ``return`` used to skip the admin views entirely,
leaving them without ``main_character_required`` on installs that list
``dtb`` in ``APPS_WITH_PUBLIC_VIEWS``.

Order matters here: ``connection_status`` is a normal authenticated view and
must be decorated, so it comes first; the excluded views follow, and
``telegram_webhook`` (excluded as well — Telegram cannot log in) closes the
list.

This module must NOT define ``app_name`` — it is included from
``urls.py``, which already owns the ``dtb`` namespace.
"""
from django.urls import path

from . import views
from .telegram_handler import telegram_webhook

urlpatterns = [
    # Authenticated member view — gets decorated first.
    path('status/', views.connection_status, name='connection_status'),

    # Telegram webhook: protected by its own secret token, never by AA auth.
    path('telegram/webhook/', telegram_webhook, name='telegram_webhook'),

    # User-facing views excluded from main_character_required.
    path('', views.services_overview, name='services_overview'),
    path('link/', views.link_telegram, name='link_telegram'),
    path('unlink/', views.unlink_telegram, name='unlink_telegram'),
    path('history/', views.forward_history, name='forward_history'),
]
