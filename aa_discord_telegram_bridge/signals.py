import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def on_user_state_changed(sender, instance, **kwargs):
    """Handle user state changes for Telegram management.

    When a user is deactivated or loses membership, kick them from Telegram groups.
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
                tg_profile.notifications_enabled = False
                tg_profile.save()
                logger.info(
                    'Deactivated Telegram for inactive user: %s',
                    instance.username,
                )
        except TelegramUser.DoesNotExist:
            pass


@receiver(post_save, sender='eveonline.EveCharacter')
def on_character_update(sender, instance, **kwargs):
    """Check alliance membership when character data is updated by model update task.

    If the character's alliance_id changed and no longer matches DTBSettings.alliance_id,
    kick the user from Telegram groups.
    """
    from .models import DTBSettings, TelegramUser
    from .tasks import _kick_user_from_all_groups, _user_in_alliance
    from .manager import TelegramBotManager

    try:
        s = DTBSettings.load()
        if s.alliance_id is None:
            return
    except Exception:
        return

    try:
        ownership = instance.character_ownership
        user = ownership.user
        tg_profile = TelegramUser.objects.get(user=user)

        if tg_profile.is_active and tg_profile.telegram_chat_id:
            if not _user_in_alliance(user):
                bot = TelegramBotManager()
                _kick_user_from_all_groups(bot, tg_profile)
                tg_profile.is_active = False
                tg_profile.notifications_enabled = False
                tg_profile.save()
                logger.info(
                    'Kicked %s from Telegram: left alliance',
                    user.username,
                )
    except Exception:
        pass
