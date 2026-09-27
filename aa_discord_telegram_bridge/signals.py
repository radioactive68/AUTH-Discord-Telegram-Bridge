import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def on_user_state_changed(sender, instance, **kwargs):
    """Handle user state changes for Telegram management.

    When a user is deactivated in Auth, kick them from Telegram groups.
    """
    if not instance.is_active:
        from .models import TelegramUser
        from .tasks import _kick_user_from_all_groups
        from .manager import TelegramBotManager

        try:
            tg_profile = TelegramUser.objects.get(user=instance)
            if tg_profile.is_active and tg_profile.telegram_chat_id:
                bot = TelegramBotManager()
                _kick_user_from_all_groups(bot, tg_profile)
                tg_profile.is_active = False
                tg_profile.save()
                logger.info(
                    'Deactivated Telegram for inactive user: %s',
                    instance.username,
                )
        except TelegramUser.DoesNotExist:
            pass


# Access is no longer derived from EVE data: it is the DTB permission
# ``access_dtb``. There is no character signal to watch any more — the
# periodic validation task enforces the permission state (Auth does not
# notify apps when a permission is granted or removed).
