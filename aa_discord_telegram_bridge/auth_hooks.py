import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

from allianceauth.services.hooks import ServicesHook
from allianceauth import hooks

from .models import TelegramUser

logger = logging.getLogger(__name__)


@hooks.register('services_hook')
def register_service():
    return DiscordTelegramBridgeService()


class DiscordTelegramBridgeService(ServicesHook):
    """Alliance Auth service hook for the Discord-Telegram Bridge."""

    def __init__(self):
        ServicesHook.__init__(self)
        self.name = 'Discord-Telegram Bridge'
        self.service_ctrl_template = 'dtb/services_ctrl.html'
        self.access_perm = 'dtb.access_dtb'

    @property
    def title(self):
        return 'Discord-Telegram Bridge'

    def service_active_for_user(self, user):
        """Check if service is active for user."""
        try:
            profile = user.telegram_profile
            return profile.is_active and profile.telegram_chat_id
        except TelegramUser.DoesNotExist:
            return False

    def show_service_ctrl(self, user, state):
        """Show service control for members."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        from .models import TelegramUser

        # Show for any user who can potentially link
        return True

    def render_services_ctrl(self, request):
        from django.shortcuts import render
        from django.template.loader import render_to_string

        user = request.user
        profile, _ = TelegramUser.objects.get_or_create(user=user)

        return render_to_string(self.service_ctrl_template, {
            'service_name': self.title,
            'profile': profile,
            'user': user,
        }, request=request)

    def delete_user(self, user, notify_user=False):
        """Remove user's Telegram linkage."""
        try:
            profile = user.telegram_profile
            profile.is_active = False
            profile.notifications_enabled = False
            profile.save()
            logger.info('Deactivated Telegram for user %s', user.username)
            return True
        except TelegramUser.DoesNotExist:
            return False

    def validate_user(self, user):
        """Validate user should have service."""
        if self.service_active_for_user(user):
            from .tasks import _user_in_alliance

            # If user has no characters or left alliance, deactivate
            has_ownership = (
                (hasattr(user, 'character_ownerships') and user.character_ownerships.exists()) or
                (hasattr(user, 'character_ownership'))
            )
            if not has_ownership or not _user_in_alliance(user):
                self.delete_user(user, notify_user=True)


@receiver(post_save, sender=User)
def create_telegram_profile(sender, instance, created, **kwargs):
    """Auto-create TelegramUser profile when User is created."""
    if created:
        TelegramUser.objects.get_or_create(user=instance)
