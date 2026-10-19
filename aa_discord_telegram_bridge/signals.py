from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from allianceauth.services.hooks import get_extension_logger

logger = get_extension_logger(__name__)


@receiver(post_save, sender=User)
def on_user_state_changed(sender, instance, **kwargs):
    """When a user is deactivated in Auth, queue their Telegram kick.

    This receiver deliberately does no I/O itself: it runs inside Auth's own
    ``User.save()`` transaction, and Telegram API calls there would hold that
    transaction open for seconds — and be rolled back if the save later
    fails. It only schedules the work; the kicks happen in a celery worker.

    Every failure is caught and logged: an exception escaping this receiver
    would break user saves across Alliance Auth.
    """
    if instance.is_active or kwargs.get('raw'):
        return
    try:
        from .tasks import kick_deactivated_user

        user_pk = instance.pk
        transaction.on_commit(lambda: kick_deactivated_user.delay(user_pk))
    except Exception:
        logger.exception(
            'DTB: could not queue the deactivation kick for user %s',
            instance.username,
        )


# Access is no longer derived from EVE data: it is the DTB permission
# ``access_dtb``. There is no character signal to watch any more — the
# periodic validation task enforces the permission state (Auth does not
# notify apps when a permission is granted or removed).
