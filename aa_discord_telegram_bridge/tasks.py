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


def _user_in_alliance(user):
    """Check if user has at least one character in the configured alliance.

    Returns False if DTBSettings.alliance_id is None (not configured).
    """
    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        alliance_id = s.alliance_id
    except Exception:
        alliance_id = getattr(__import__('django.conf', fromlist=['settings']).settings, 'DTB_ALLIANCE_ID', None)
    if alliance_id is None:
        return False
    # Trusted Alliance Auth administrators are always considered authorized
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    # Check via AA's CharacterOwnership -> EveCharacter.alliance_id
    # related_name='character_ownerships' (newer AA) or 'character_ownership' (older)
    ownerships = None
    if hasattr(user, 'character_ownerships'):
        ownerships = user.character_ownerships.all()
    elif hasattr(user, 'character_ownership'):
        ownerships = [user.character_ownership]
    if not ownerships:
        return False
    for ownership in ownerships:
        char = getattr(ownership, 'character', None)
        if char and getattr(char, 'alliance_id', None) == alliance_id:
            return True
    return False


def _user_is_dtb_member(user):
    """Strict check used for showing the DTB block on the services page.

    Unlike :func:`_user_in_alliance`, the superuser/staff shortcut is NOT
    applied: non-alliance users must not see the service on the services
    page, matching every other alliance-only service. DTB admins
    (``manage_dtb_rules``) always pass.
    """
    from django.conf import settings

    if user.has_perm('aa_discord_telegram_bridge.manage_dtb_rules'):
        return True

    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        alliance_id = s.alliance_id
    except Exception:
        alliance_id = getattr(settings, 'DTB_ALLIANCE_ID', None)
    if alliance_id is None:
        return False

    ownerships = None
    if hasattr(user, 'character_ownerships'):
        ownerships = user.character_ownerships.all()
    elif hasattr(user, 'character_ownership'):
        ownerships = [user.character_ownership]
    if not ownerships:
        return False
    for ownership in ownerships:
        char = getattr(ownership, 'character', None)
        if char and getattr(char, 'alliance_id', None) == alliance_id:
            return True
    return False


@shared_task(bind=True, max_retries=3)
def validate_all_telegram_users(self):
    """Periodic task: validate all Telegram users are still in valid state.

    Kicks users from Telegram groups if they left the alliance.
    Skips entirely if alliance_id is not configured.
    """
    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        if s.alliance_id is None:
            return {'validated': 0, 'kicked': 0, 'skipped': 'no alliance_id'}
    except Exception:
        return {'validated': 0, 'kicked': 0, 'skipped': 'error'}

    telegram_bot = TelegramBotManager()

    # Process all linked users (with a Telegram chat id) so that users who were
    # previously deactivated can be re-activated when they return to good standing.
    linked_users = TelegramUser.objects.filter(
        telegram_chat_id__isnull=False,
    ).exclude(telegram_chat_id='')

    kicked_count = 0
    validated_count = 0

    for tg_user in linked_users:
        try:
            user = tg_user.user

            # A deactivated Django user always loses Telegram access
            if not user.is_active:
                kicked = _kick_user_from_all_groups(telegram_bot, tg_user)
                tg_user.is_active = False
                tg_user.save()
                if kicked:
                    kicked_count += 1
                continue

            # Trusted Alliance Auth administrators are always authorized
            authorized = user.is_superuser or user.is_staff

            if not authorized:
                # Check if user has any character ownership
                has_ownership = user.character_ownerships.filter(
                    character__alliance_id__isnull=False
                ).exists()
                if not has_ownership:
                    kicked = _kick_user_from_all_groups(telegram_bot, tg_user)
                    tg_user.is_active = False
                    tg_user.save()
                    if kicked:
                        kicked_count += 1
                    continue

                # Check alliance membership
                if not _user_in_alliance(user):
                    logger.info(
                        'User %s no longer in alliance, kicking from Telegram',
                        user.username,
                    )
                    kicked = _kick_user_from_all_groups(telegram_bot, tg_user)
                    tg_user.is_active = False
                    tg_user.save()
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

    logger.info(
        'Telegram validation complete: %d validated, %d kicked',
        validated_count, kicked_count,
    )
    return {
        'validated': validated_count,
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

    groups = TelegramGroup.objects.filter(is_active=True)

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
                    'Auth because you are no longer a member of the alliance. '
                    'If you rejoin, link your account again to restore access.'
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
    groups = list(TelegramGroup.objects.filter(is_active=True))
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
