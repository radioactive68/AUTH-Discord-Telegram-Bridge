import logging

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.urls import NoReverseMatch, reverse

from allianceauth import hooks
from allianceauth.menu.hooks import MenuItemHook
from allianceauth.services.hooks import ServicesHook, UrlHook
from django.utils.translation import gettext_lazy as _

from . import urls
from .models import TelegramUser
from .permissions import PERM_ACCESS_DTB, user_can_use_dtb, user_is_dtb_admin

logger = logging.getLogger(__name__)


@hooks.register('services_hook')
def register_service():
    return DiscordTelegramBridgeService()


class DiscordTelegramBridgeService(ServicesHook):
    """Alliance Auth service hook for the Discord-Telegram Bridge."""

    def __init__(self):
        ServicesHook.__init__(self)
        # Must stay a plain str, never a lazy gettext proxy: AA's
        # services/signals.py interpolates the service object into f-strings
        # (group/state permission changes, logger.exception around
        # validate_user), and f-strings call str() eagerly even when the log
        # level is off. ServicesHook.__str__ returns self.name unchanged, so a
        # proxy there raises TypeError inside the caller's transaction and the
        # change silently rolls back. AA's own services put a short id here
        # and the display name in title.
        self.name = 'dtb'
        self.service_ctrl_template = 'dtb/services_ctrl.html'
        self.access_perm = PERM_ACCESS_DTB

    @property
    def title(self):
        return _('Discord-Telegram Bridge')

    def service_active_for_user(self, user):
        """Show the service only to users holding ``access_dtb``.

        AA hands the permission state to the service hook but does not
        enforce it, so the check lives here (see
        ``permissions.user_can_use_dtb``).
        """
        return user_can_use_dtb(user)

    def show_service_ctrl(self, user):
        """Render the service control row under the same access rules."""
        return user_can_use_dtb(user)

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
        """Revoke DTB access: kick from Telegram groups and unlink the account.

        Called by Alliance Auth when the user must not have the service any
        more (no ``access_dtb`` permission, deactivated account, …).
        """
        from .tasks import _kick_user_from_all_groups
        from .manager import TelegramBotManager

        try:
            profile = user.telegram_profile
        except TelegramUser.DoesNotExist:
            return False

        if profile.telegram_chat_id:
            try:
                _kick_user_from_all_groups(
                    TelegramBotManager(), profile, notify=notify_user,
                )
            except Exception as e:
                logger.error(
                    'Error revoking Telegram access for %s: %s',
                    user.username, e,
                )
        if profile.is_active:
            profile.is_active = False
            profile.save()
        logger.info('Revoked Telegram access for user %s', user.username)
        return True

    def validate_user(self, user):
        """Revoke the service when the user lost the access permission."""
        if not self.service_active_for_user(user):
            self.delete_user(user, notify_user=True)


@receiver(post_save, sender=User)
def create_telegram_profile(sender, instance, created, **kwargs):
    """Auto-create TelegramUser profile when User is created."""
    if created:
        TelegramUser.objects.get_or_create(user=instance)


@hooks.register('url_hook')
def register_urls():
    return UrlHook(
        urls,
        'dtb',
        r'^dtb/',
        excluded_views=[
            'aa_discord_telegram_bridge.views.services_overview',
            'aa_discord_telegram_bridge.views.link_telegram',
            'aa_discord_telegram_bridge.views.unlink_telegram',
            'aa_discord_telegram_bridge.views.forward_history',
            'aa_discord_telegram_bridge.views.connection_status',
            'aa_discord_telegram_bridge.telegram_handler.telegram_webhook',
        ],
    )


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
        if user_is_dtb_admin(request.user):
            try:
                return MenuItemHook.render(self, request)
            except (NoReverseMatch, Exception):
                return ''
        return ''


@hooks.register('menu_item_hook')
def register_menu():
    return DTBMenu()
