import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.contrib.auth.models import User

from .models import (
    ForwardRule, TelegramUser, ForwardHistory,
    ConnectionStatus, TelegramGroup, DTBSettings,
)
from .manager import TelegramBotManager, DiscordBotManager

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
            'error_message': '' if is_ok else msg,
        },
    )
    logger.info('Telegram connection test: %s - %s', is_ok, msg)

    # Discord
    bot = DiscordBotManager()
    is_ok, msg = bot.test_connection()
    ConnectionStatus.objects.update_or_create(
        service='discord',
        defaults={
            'is_connected': is_ok,
            'last_checked': timezone.now(),
            'last_success': timezone.now() if is_ok else None,
            'error_message': '' if is_ok else msg,
        },
    )
    logger.info('Discord connection test: %s - %s', is_ok, msg)


def _user_in_alliance(user):
    """Check if user has at least one character in the configured alliance.

    Returns True if DTBSettings.alliance_id is None (no check configured).
    """
    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        alliance_id = s.alliance_id
    except Exception:
        alliance_id = getattr(__import__('django.conf', fromlist=['settings']).settings, 'DTB_ALLIANCE_ID', None)
    if alliance_id is None:
        return True
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


@shared_task(bind=True, max_retries=3)
def validate_all_telegram_users(self):
    """Periodic task: validate all Telegram users are still in valid state.

    Kicks users from Telegram groups if they left the alliance.
    """
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
                _kick_user_from_all_groups(telegram_bot, tg_user)
                tg_user.is_active = False
                tg_user.notifications_enabled = False
                tg_user.save()
                kicked_count += 1
                continue

            # Trusted Alliance Auth administrators are always authorized
            authorized = user.is_superuser or user.is_staff

            if not authorized:
                # Check if user has any character ownership
                has_ownership = (
                    (hasattr(user, 'character_ownerships') and user.character_ownerships.exists()) or
                    (hasattr(user, 'character_ownership'))
                )
                if not has_ownership:
                    _kick_user_from_all_groups(telegram_bot, tg_user)
                    tg_user.is_active = False
                    tg_user.notifications_enabled = False
                    tg_user.save()
                    kicked_count += 1
                    continue

                # Check alliance membership
                if not _user_in_alliance(user):
                    logger.info(
                        'User %s no longer in alliance, kicking from Telegram',
                        user.username,
                    )
                    _kick_user_from_all_groups(telegram_bot, tg_user)
                    tg_user.is_active = False
                    tg_user.notifications_enabled = False
                    tg_user.save()
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
                tg_user.user.username, e,
            )

    logger.info(
        'Telegram validation complete: %d validated, %d kicked',
        validated_count, kicked_count,
    )
    return {
        'validated': validated_count,
        'kicked': kicked_count,
    }


def _kick_user_from_all_groups(telegram_bot, tg_user):
    """Kick a user from all known Telegram groups."""
    groups = TelegramGroup.objects.filter(is_active=True)
    for group in groups:
        try:
            result = telegram_bot.kick_chat_member(
                chat_id=group.telegram_chat_id,
                user_id=tg_user.telegram_user_id,
            )
            if result.get('ok'):
                logger.info(
                    'Kicked user %s from Telegram group %s',
                    tg_user.user.username, group.name,
                )
            else:
                logger.warning(
                    'Failed to kick user %s from group %s: %s',
                    tg_user.user.username, group.name,
                    result.get('description', 'unknown'),
                )
        except Exception as e:
            logger.error(
                'Error kicking user %s from group %s: %s',
                tg_user.user.username, group.name, e,
            )


@shared_task(bind=True, max_retries=3)
def forward_message(self, rule_id, discord_channel_id, discord_channel_name,
                    message_text, message_id, author_name=''):
    """Forward a single message from Discord to Telegram."""
    try:
        rule = ForwardRule.objects.get(pk=rule_id, is_enabled=True)
    except ForwardRule.DoesNotExist:
        logger.warning('Rule %s not found or disabled, skipping forward', rule_id)
        return

    # Check keyword filter
    if not rule.matches_keywords(message_text):
        return

    telegram_bot = TelegramBotManager()

    # Format message
    text = (
        f'<b>[{rule.name}]</b>\n'
        f'👤 {author_name}\n\n'
        f'{message_text}'
    )

    # Send to target (supports chat_id:thread_id for forum topics)
    target = TelegramBotManager.parse_target(rule.telegram_target)
    result = telegram_bot.send_message(
        chat_id=target['chat_id'],
        text=text,
        message_thread_id=target.get('message_thread_id'),
    )

    # Log to history
    ForwardHistory.objects.create(
        rule=rule,
        source_channel=f'#{discord_channel_name}',
        target_channel=rule.telegram_target,
        message_preview=message_text[:500],
        discord_message_id=str(message_id),
        success=result.get('ok', False),
        error_message=result.get('description', '') if not result.get('ok') else '',
    )

    if not result.get('ok'):
        logger.error(
            'Failed to forward message to %s: %s',
            rule.telegram_target,
            result.get('description', 'unknown'),
        )


@shared_task(bind=True, max_retries=1)
def sync_telegram_groups(self):
    """Sync known Telegram groups by querying the bot for joined groups."""
    bot = TelegramBotManager()
    is_ok, _ = bot.test_connection()
    if not is_ok:
        logger.error('Cannot sync groups: Telegram bot not connected')
        return

    # Note: Telegram Bot API doesn't have a direct way to list all groups.
    # Groups are tracked when the bot is added or when users link.
    # This task refreshes info for existing groups.
    for group in TelegramGroup.objects.filter(is_active=True):
        result = bot.get_chat(group.telegram_chat_id)
        if result.get('ok'):
            chat_info = result.get('result', {})
            group.name = chat_info.get('title', group.name)
            group.chat_type = chat_info.get('type', group.chat_type)
            group.save()
        else:
            logger.warning(
                'Could not fetch info for group %s: %s',
                group.name,
                result.get('description', 'unknown'),
            )
