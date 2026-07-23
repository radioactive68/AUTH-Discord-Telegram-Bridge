import hashlib
import logging
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import redirect
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import override as translation_override, gettext

from .models import TelegramUser, TelegramLinkRequest
from .manager import TelegramBotManager

logger = logging.getLogger(__name__)


def _get_user_locale(telegram_user_id=None):
    """Get locale for a linked Telegram user from their AA profile language.

    Falls back to English if user is not linked or has no language set.
    """
    if telegram_user_id:
        try:
            tg_profile = TelegramUser.objects.select_related(
                'user__profile'
            ).get(telegram_user_id=telegram_user_id)
            lang = getattr(tg_profile.user.profile, 'language', None)
            if lang:
                return lang
        except Exception:
            pass
    return 'en'


def _send_localized(chat_id, telegram_user_id, text_func):
    """Send a translated message to a Telegram chat.

    text_func must be a callable that returns a translated string when
    called inside a translation override context.
    """
    locale = _get_user_locale(telegram_user_id)
    with translation_override(locale):
        text = text_func()
    bot = TelegramBotManager()
    bot.send_message(chat_id=chat_id, text=text)


def _dispatch_update(data):
    """Process a single Telegram update (used by both webhook and polling)."""
    message = data.get('message') or data.get('my_chat_member')
    if not message:
        # Handle pending join requests (group membership gating)
        join_request = data.get('chat_join_request')
        if join_request:
            _process_join_request(join_request)
        return

    chat = message.get('chat', {})
    user_info = message.get('from', {})
    text = message.get('text', '')

    chat_id = str(chat.get('id', ''))
    user_id = user_info.get('id')
    username = user_info.get('username', '')

    # Handle /start command
    if text.startswith('/start'):
        parts = text.split()
        tg_lang = user_info.get('language_code', 'en')
        if len(parts) > 1:
            code = parts[1].upper()
            _process_linking_code(code, chat_id, user_id, username, tg_lang)
        else:
            _process_plain_start(user_id, chat_id, username, tg_lang)

    # Handle /stop command
    if text.startswith('/stop'):
        _process_unlink(chat_id, user_id)


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Handle incoming Telegram updates via webhook."""
    import json

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    _dispatch_update(data)
    return JsonResponse({'ok': True})


def run_telegram_polling():
    """Long-poll Telegram for updates when no webhook is configured.

    Runs in a background thread. Skipped automatically when a webhook URL
    is set in DTB Settings (webhook mode takes precedence).
    """
    from .models import DTBSettings
    try:
        if DTBSettings.load().telegram_webhook_url:
            logger.info('DTB: webhook URL configured, skipping Telegram polling.')
            return
    except Exception:
        pass

    bot = TelegramBotManager()
    offset = None
    logger.info('DTB: Telegram polling started.')
    while True:
        try:
            resp = bot.get_updates(offset=offset, timeout=30)
            if resp.get('ok'):
                for update in resp.get('result', []):
                    try:
                        _dispatch_update(update)
                    except Exception:
                        logger.exception('DTB: error dispatching Telegram update')
                    offset = update.get('update_id', 0) + 1
            else:
                desc = resp.get('description', '')
                if 'Conflict' in desc or 'webhook' in desc.lower():
                    logger.warning(
                        'DTB: Telegram webhook is set elsewhere (conflict). '
                        'Deleting webhook to enable polling: %s', desc,
                    )
                    try:
                        bot.delete_webhook()
                    except Exception:
                        pass
                else:
                    logger.warning('DTB: getUpdates failed: %s', desc)
                time.sleep(5)
        except Exception as e:
            logger.error('DTB: Telegram polling error: %s', e)
            time.sleep(5)


def _process_linking_code(code, chat_id, user_id, telegram_username, tg_lang='en'):
    """Process a linking code from Telegram /start command."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    import json

    # Search active sessions for matching code
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in active_sessions:
        try:
            data = session.get_decoded()
            if data.get('dtb_link_code') == code:
                user_id_auth = data.get('_auth_user_id')
                if user_id_auth:
                    try:
                        user = User.objects.get(pk=user_id_auth)
                        profile, _ = TelegramUser.objects.get_or_create(user=user)
                        profile.telegram_chat_id = chat_id
                        profile.telegram_user_id = user_id
                        profile.telegram_username = telegram_username
                        profile.is_active = True
                        profile.save()

                        # Send confirmation
                        bot = TelegramBotManager()
                        _invite_to_groups(bot, user_id)
                        _send_localized(
                            chat_id, user_id,
                            lambda: gettext(
                                'Successfully linked!\n\n'
                                'You will now receive notifications from Alliance Auth.\n'
                                'Use /stop to disable notifications.'
                            ),
                        )

                        logger.info(
                            'User %s linked Telegram account @%s (chat_id: %s)',
                            user.username, telegram_username, chat_id,
                        )
                        return True
                    except User.DoesNotExist:
                        logger.error('Auth user %s not found for linking code', user_id_auth)
        except Exception:
            continue

    logger.warning('Linking code %s not found in any active session', code)
    return False


def _invite_to_groups(bot, telegram_user_id):
    """Invite a (linked) Telegram user to all tracked active groups.

    Unbans first so a previously-kicked user can be re-added, then invites.
    """
    from .models import TelegramGroup
    for group in TelegramGroup.objects.filter(is_active=True):
        try:
            bot.unban_chat_member(group.telegram_chat_id, telegram_user_id)
            result = bot.add_chat_member(group.telegram_chat_id, telegram_user_id)
            if result.get('ok'):
                logger.info(
                    'Invited user %s to Telegram group %s',
                    telegram_user_id, group.name,
                )
            else:
                logger.warning(
                    'Could not invite user %s to group %s: %s',
                    telegram_user_id, group.name, result.get('description', 'unknown'),
                )
        except Exception as e:
            logger.error(
                'Error inviting user %s to group %s: %s',
                telegram_user_id, group.name, e,
            )


def _process_plain_start(user_id, chat_id, username, tg_lang='en'):
    """Handle a bare /start command (no linking code).

    If the chat is already linked, this just re-verifies access.
    Otherwise it records a pending link request so the portal can finish
    linking without requiring the user to type a verification code.
    """
    try:
        profile = TelegramUser.objects.get(telegram_user_id=user_id)
    except TelegramUser.DoesNotExist:
        # Not linked yet: remember this chat so the portal can auto-link.
        TelegramLinkRequest.objects.update_or_create(
            chat_id=str(chat_id),
            defaults={
                'telegram_user_id': str(user_id),
                'username': username or '',
                'created_at': timezone.now(),
            },
        )
        bot = TelegramBotManager()
        if username:
            with translation_override(tg_lang):
                text = gettext(
                    'Hello! To link your account:\n'
                    '1. Open Alliance Auth -> Discord-Telegram Bridge\n'
                    '2. Click "Link Account"\n'
                    '3. Enter your Telegram username: @%(username)s\n'
                    '4. Click Link - you will be connected automatically\n\n'
                    'I will send a confirmation here once it is done.'
                ) % {'username': username}
            bot.send_message(chat_id=chat_id, text=text)
        else:
            with translation_override(tg_lang):
                text = gettext(
                    'Hello! To link your account, open Alliance Auth -> '
                    'Discord-Telegram Bridge -> Link Account and enter your '
                    'Telegram username. (You need a Telegram @username set in '
                    'your profile to link.)'
                )
            bot.send_message(chat_id=chat_id, text=text)
        return

    profile.telegram_chat_id = str(chat_id)
    if username:
        profile.telegram_username = username
    profile.is_active = True
    profile.save()

    bot = TelegramBotManager()
    _invite_to_groups(bot, user_id)
    bot.send_message(
        chat_id=chat_id,
        text='✅ Verified! Your access to the alliance Telegram groups is confirmed.',
    )


def _process_join_request(req):
    """Gatekeep group join requests: approve only authorized (linked) Auth users."""
    chat = req.get('chat', {})
    chat_id = str(chat.get('id', ''))
    user_info = req.get('from', {})
    user_id = user_info.get('id')
    username = user_info.get('username', '')

    bot = TelegramBotManager()
    authorized = False
    try:
        from .tasks import _user_in_alliance
        profile = TelegramUser.objects.get(telegram_user_id=user_id)
        user = profile.user
        if user.is_active and _user_in_alliance(user):
            authorized = True
            profile.is_active = True
            profile.telegram_chat_id = str(chat_id)
            if username:
                profile.telegram_username = username
            profile.save()
    except TelegramUser.DoesNotExist:
        authorized = False

    if authorized:
        bot.approve_chat_join_request(chat_id, user_id)
        logger.info('Approved join request for authorized user %s', user_id)
    else:
        bot.decline_chat_join_request(chat_id, user_id)
        logger.info('Declined join request for unauthorized user %s', user_id)
        try:
            bot.send_message(
                chat_id=user_id,
                text=(
                    '⛔ Access denied. Your Telegram account is not linked to an '
                    'authorized Alliance Auth member, or you are no longer in the '
                    'alliance. Link your account in Auth first.'
                ),
            )
        except Exception:
            pass


def _process_unlink(chat_id, user_id):
    """Process /stop command - disable notifications."""
    try:
        profile = TelegramUser.objects.get(
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
        )
        profile.save()

        bot = TelegramBotManager()
        bot.send_message(
            chat_id=chat_id,
            text='🔕 Notifications disabled.\nUse /start to re-enable.',
        )
    except TelegramUser.DoesNotExist:
        pass
