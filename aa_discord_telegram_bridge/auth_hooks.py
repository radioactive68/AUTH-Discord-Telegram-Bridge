import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.urls import NoReverseMatch, reverse

from allianceauth import hooks
from allianceauth.menu.hooks import MenuItemHook
from allianceauth.services.hooks import ServicesHook
from django.utils.translation import gettext_lazy as _

from .models import TelegramUser

logger = logging.getLogger(__name__)


@hooks.register('services_hook')
def register_service():
    return DiscordTelegramBridgeService()


class DiscordTelegramBridgeService(ServicesHook):
    """Alliance Auth service hook for the Discord-Telegram Bridge."""

    def __init__(self):
        ServicesHook.__init__(self)
        self.name = _('Discord-Telegram Bridge')
        self.service_ctrl_template = 'dtb/services_ctrl.html'
        self.access_perm = None

    @property
    def title(self):
        return _('Discord-Telegram Bridge')

    def service_active_for_user(self, user):
        """Check if service is active for user."""
        try:
            profile = user.telegram_profile
            return profile.is_active and profile.telegram_chat_id
        except TelegramUser.DoesNotExist:
            return False

    def show_service_ctrl(self, user):
        """Show service control for members."""
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        from .models import TelegramUser

        # Show for any user who can potentially link
        return True

    def render_services_ctrl(self, request):
        from django.template.loader import render_to_string

        user = request.user
        profile, created = TelegramUser.objects.get_or_create(user=user)

        bot_link = None
        bot_username = None
        try:
            from .manager import TelegramBotManager
            bot = TelegramBotManager()
            res = bot.get_me()
            if res.get('ok'):
                bot_username = res.get('result', {}).get('username')
                if bot_username:
                    bot_link = f'https://t.me/{bot_username}'
        except Exception:
            pass

        return render_to_string(self.service_ctrl_template, {
            'service_name': self.title,
            'profile': profile,
            'user': user,
            'bot_link': bot_link,
            'bot_username': bot_username,
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


class DTBMenu(MenuItemHook):
    def __init__(self):
        MenuItemHook.__init__(
            self,
            'DTB',
            'fa-solid fa-comments',
            'dtb:admin_index',
            navactive=['dtb:'],
        )

    def render(self, request):
        if request.user.has_perm('aa_discord_telegram_bridge.manage_dtb_rules'):
            try:
                return MenuItemHook.render(self, request)
            except (NoReverseMatch, Exception):
                return ''
        return ''


@hooks.register('menu_item_hook')
def register_menu():
    return DTBMenu()
