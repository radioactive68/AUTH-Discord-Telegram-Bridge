import logging
import time
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.contrib.auth.models import User

from .models import (
    TelegramUser, ConnectionStatus, TelegramGroup, DTBSettings,
)
from .manager import TelegramBotManager, DiscordBotManager, redact_secrets

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def test_connections(self):
    """Periodic task: test Discord and Telegram connections."""
    # Telegram
    bot = TelegramBotManager()
    is_ok, msg = bot.test_connection()
    ConnectionStatus.objects.update_or_create(
        service='telegram',
        defaults={
            'is_connected': is_ok,
            'last_checked': timezone.now(),
            'last_success': timezone.now() if is_ok else None,
            'error_message': '' if is_ok else redact_secrets(msg),
        },
    )
    logger.info('Telegram connection test: %s - %s', is_ok, redact_secrets(msg))

    # Discord
    bot = DiscordBotManager()
    is_ok, msg = bot.test_connection()
    ConnectionStatus.objects.update_or_create(
        service='discord',
        defaults={
            'is_connected': is_ok,
            'last_checked': timezone.now(),
            'last_success': timezone.now() if is_ok else None,
            'error_message': '' if is_ok else redact_secrets(msg),
        },
    )
    logger.info('Discord connection test: %s - %s', is_ok, redact_secrets(msg))


def iter_user_ownerships(user):
    """Yield a user's CharacterOwnership objects on any AA version.

    Newer Alliance Auth exposes ``user.character_ownerships`` (a related
    manager), older installs a single ``user.character_ownership``.
    """
    if hasattr(user, 'character_ownerships'):
        try:
            for ownership in user.character_ownerships.all():
                yield ownership
            return
        except Exception:
            pass
    if hasattr(user, 'character_ownership'):
        ownership = user.character_ownership
        if ownership:
            yield ownership


APP_LABEL = 'aa_discord_telegram_bridge'


def _user_holds_perm(user, codename):
    """Explicitly check one of DTB's own permissions — no implicit access.

    ``user.has_perm()`` is unusable for this: Django returns True for *every*
    permission when the user is a superuser, so the access permission would be
    meaningless (a superuser would see the service — and keep Telegram access —
    without ever being granted it). DTB therefore resolves the permission from
    the actual grants, i.e. exactly what an Alliance Auth admin hands out to
    their member / FC / leadership groups.
    """
    if not getattr(user, 'is_active', True):
        return False
    if user.user_permissions.filter(
        content_type__app_label=APP_LABEL, codename=codename,
    ).exists():
        return True
    return user.groups.filter(
        permissions__content_type__app_label=APP_LABEL,
        permissions__codename=codename,
    ).exists()


def _user_can_use_dtb(user):
    """Access gate for the DTB service entry and every DTB page.

    Alliance Auth does not enforce ``ServicesHook.access_perm`` for a service
    app: it only *hands* the permission state to the service, which has to
    check it. DTB therefore checks **its own** permission — every app owns its
    permissions, so DTB never inspects permissions of other apps:

    * ``access_dtb`` — basic access, granted to the group/state holding the
      members who may use the bridge (e.g. "member"). DTB admin rights
      (``manage_dtb_rules``) do *not* imply it, and neither does being a Django
      superuser: the permission has to be granted explicitly, to everyone who
      should see the service — admins included, since they can administer DTB
      without using the bridge themselves.

    The same check drives the tile on ``/services/``, the DTB pages, the
    link/unlink flow and the Telegram join-request gate, and its loss is what
    revokes the Telegram linkage (see ``validate_all_telegram_users``).
    """
    from .permissions import PERM_ACCESS_DTB

    return _user_holds_perm(user, PERM_ACCESS_DTB)


def linked_profiles_without_access():
    """Linked Telegram profiles whose portal user has no DTB access permission.

    Pure report helper (no side effects) used by the admin dashboard, to show
    what the next validation run will revoke.
    """
    profiles = (
        TelegramUser.objects
        .filter(telegram_chat_id__isnull=False)
        .exclude(telegram_chat_id='')
        .select_related('user')
        .prefetch_related('user__user_permissions', 'user__groups__permissions')
    )
    return [p for p in profiles if not _user_can_use_dtb(p.user)]


@shared_task(bind=True, max_retries=3)
def validate_all_telegram_users(self):
    """Periodic task: enforce the access state of every linked Telegram user.

    Users whose ``access_dtb`` permission is gone (and who are not DTB admins)
    are kicked from all tracked Telegram groups and unlinked; users who still
    hold it are (re)activated. Auth does not notify apps about permission
    changes, so this task is the enforcement point for access revocation;
    deactivating a user in Auth is handled immediately by ``signals.py``.
    """
    # Process all linked users (with a Telegram chat id) so that users who were
    # previously deactivated can be re-activated when they regain access.
    linked_users = TelegramUser.objects.filter(
        telegram_chat_id__isnull=False,
    ).exclude(telegram_chat_id='')

    telegram_bot = TelegramBotManager()
    revoked: list[str] = []
    validated_count = 0
    kicked_count = 0

    for tg_user in linked_users:
        try:
            user = tg_user.user

            # A deactivated Django user always loses Telegram access
            if not user.is_active:
                if tg_user.is_active:
                    kicked = _kick_user_from_all_groups(telegram_bot, tg_user)
                    tg_user.is_active = False
                    tg_user.save()
                    if kicked:
                        kicked_count += 1
                continue

            if not _user_can_use_dtb(user):
                logger.info(
                    'User %s no longer has the DTB access permission, '
                    'kicking from Telegram',
                    user.username,
                )
                kicked = _kick_user_from_all_groups(telegram_bot, tg_user)
                tg_user.is_active = False
                tg_user.save()
                revoked.append(user.username)
                if kicked:
                    kicked_count += 1
                continue

            # User is in good standing: ensure the profile is active.
            # This also recovers users that were deactivated earlier.
            if not tg_user.is_active:
                tg_user.is_active = True
            tg_user.last_validated = timezone.now()
            tg_user.save()
            validated_count += 1

        except Exception as e:
            logger.error(
                'Error validating Telegram user %s: %s',
                tg_user.user.username, redact_secrets(str(e)),
            )

    if revoked:
        logger.info(
            'Revoked DTB access for %d user(s): %s',
            len(revoked), ', '.join(sorted(revoked)),
        )
    logger.info(
        'Telegram validation complete: %d validated, %d revoked, %d kicked',
        validated_count, len(revoked), kicked_count,
    )
    return {
        'validated': validated_count,
        'revoked': len(revoked),
        'revoked_users': sorted(revoked),
        'kicked': kicked_count,
    }


def _kick_user_from_all_groups(telegram_bot, tg_user, notify=True):
    """Kick a user from all known Telegram groups and unlink their profile.

    Uses Telegram's kick semantics — ``banChatMember`` followed shortly by
    ``unbanChatMember`` — so the user is removed from the group but is NOT
    banned permanently: they can still rejoin (e.g. when they come back to
    the alliance).

    The Telegram linkage is only cleared when at least one group kick
    actually succeeded (or there are no groups to kick). If every kick
    failed the profile stays linked, so the periodic validation keeps
    retrying — otherwise Alliance Auth would believe the user was unlinked
    while they are still present in the Telegram groups.

    Returns True if the user was actually removed (or there was nothing to
    do), False if every kick attempt failed.
    """
    from django.utils.translation import gettext, override as translation_override

    groups = TelegramGroup.objects.filter(is_active=True)\
        .exclude(telegram_chat_id__contains='/')\
        .exclude(telegram_chat_id__contains=':')

    # Send notification before kicking
    if notify and groups and tg_user.telegram_chat_id:
        try:
            # Get user locale from their AA profile
            lang = 'en'
            try:
                lang = getattr(tg_user.user.profile, 'language', 'en') or 'en'
            except Exception:
                pass

            with translation_override(lang):
                text = gettext(
                    'You have been removed from the alliance Telegram groups '
                    'and your Telegram account has been unlinked from Alliance '
                    'Auth because you no longer have access to the '
                    'Discord-Telegram Bridge. If your access is restored, '
                    'link your account again.'
                )
            telegram_bot.send_message(
                chat_id=tg_user.telegram_chat_id,
                text=text,
            )
        except Exception:
            pass  # Best effort — user may have blocked the bot

    kicked_any = False
    for group in groups:
        user_id = tg_user.telegram_user_id
        try:
            result = telegram_bot.ban_chat_member(
                chat_id=group.telegram_chat_id,
                user_id=user_id,
            )
            if result.get('ok'):
                # Immediately lift the ban so the user can rejoin later.
                # The short pause lets Telegram record the ban first.
                time.sleep(1)
                try:
                    unban_result = telegram_bot.unban_chat_member(
                        chat_id=group.telegram_chat_id,
                        user_id=user_id,
                    )
                except Exception as e:
                    unban_result = {'ok': False, 'description': redact_secrets(str(e))}
                if unban_result.get('ok'):
                    kicked_any = True
                    logger.info(
                        'Kicked user %s from Telegram group %s',
                        tg_user.user.username, group.name,
                    )
                else:
                    logger.warning(
                        'Kicked but could not lift the ban for user %s in group %s: %s',
                        tg_user.user.username, group.name,
                        redact_secrets(unban_result.get('description', 'unknown')),
                    )
            else:
                logger.warning(
                    'Failed to kick user %s from group %s: %s',
                    tg_user.user.username, group.name,
                    redact_secrets(result.get('description', 'unknown')),
                )
        except Exception as e:
            logger.error(
                'Error kicking user %s from group %s: %s',
                tg_user.user.username, group.name, redact_secrets(str(e)),
            )

    if not groups:
        kicked_any = True
    elif not kicked_any:
        logger.error(
            'Could not kick user %s from any Telegram group; keeping the '
            'profile linked so validation can retry.',
            tg_user.user.username,
        )
        return False

    # Unlink the Telegram profile so the user is no longer tracked and must
    # re-link through the portal if they rejoin the alliance.
    tg_user.telegram_chat_id = ''
    tg_user.telegram_user_id = None
    tg_user.telegram_username = ''
    tg_user.is_active = False
    tg_user.save(update_fields=[
        'telegram_chat_id', 'telegram_user_id', 'telegram_username', 'is_active',
    ])
    logger.info('Unlinked Telegram for user %s', tg_user.user.username)
    return True


def _kick_telegram_id_from_all_groups(telegram_bot, user_id):
    """Kick a Telegram user (with no portal link) from all tracked groups.

    Uses the same ban+unban kick semantics as ``_kick_user_from_all_groups``,
    so the user is removed without a permanent ban. The caller handles any
    feedback; this only reports whether at least one kick succeeded.

    Returns ``(kicked_any, error_msg)``.
    """
    groups = list(TelegramGroup.objects.filter(is_active=True)\
              .exclude(telegram_chat_id__contains='/')\
              .exclude(telegram_chat_id__contains=':'))
    if not groups:
        return False, 'No active Telegram groups to kick from.'

    kicked_any = False
    for group in groups:
        try:
            result = telegram_bot.ban_chat_member(
                chat_id=group.telegram_chat_id,
                user_id=user_id,
            )
            if not result.get('ok'):
                logger.warning(
                    'Failed to kick Telegram user %s from group %s: %s',
                    user_id, group.name,
                    redact_secrets(result.get('description', 'unknown')),
                )
                continue
            time.sleep(1)
            try:
                telegram_bot.unban_chat_member(
                    chat_id=group.telegram_chat_id,
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(
                    'Kicked but could not lift the ban for user %s in group %s: %s',
                    user_id, group.name, redact_secrets(str(e)),
                )
            kicked_any = True
            logger.info('Kicked Telegram user %s from group %s', user_id, group.name)
        except Exception as e:
            logger.error(
                'Error kicking Telegram user %s from group %s: %s',
                user_id, group.name, redact_secrets(str(e)),
            )
    return kicked_any, ''
