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
    from .telegram_handler import _invite_to_groups
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

        # Only act on users that have a linked Telegram account
        if not tg_profile.telegram_chat_id:
            return

        in_alliance = _user_in_alliance(user)

        if in_alliance:
            # User is (back) in the alliance: restore access if it was revoked
            if not tg_profile.is_active:
                bot = TelegramBotManager()
                _invite_to_groups(bot, tg_profile.telegram_user_id)
                tg_profile.is_active = True
                tg_profile.save()
                logger.info(
                    'Re-activated Telegram for %s: in alliance',
                    user.username,
                )
        else:
            # User left the alliance: revoke access
            if tg_profile.is_active:
                bot = TelegramBotManager()
                _kick_user_from_all_groups(bot, tg_profile)
                tg_profile.is_active = False
                tg_profile.save()
                logger.info(
                    'Kicked %s from Telegram: left alliance',
                    user.username,
                )
    except Exception:
        pass
