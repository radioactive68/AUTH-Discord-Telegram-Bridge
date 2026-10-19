"""Celery app for the CI harness.

Mirrors ``myauth/celery.py`` of a real Alliance Auth install: the only part
the tests need is the celery_once ``ONCE`` configuration — celery_once reads
``app.conf.ONCE`` lazily, off whatever app is current, and without it a
``QueueOnce`` task that is really enqueued (not mocked) would raise instead
of queuing.

The constructor also makes this app the current Celery app, so tasks defined
via ``shared_task`` (our ``aa_discord_telegram_bridge.tasks``) bind to it.
"""
from celery import Celery

app = Celery('dtb_tests')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.ONCE = {
    'backend': 'allianceauth.services.tasks.DjangoBackend',
    'settings': {},
}
