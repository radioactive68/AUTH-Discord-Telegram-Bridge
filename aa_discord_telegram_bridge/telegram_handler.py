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


def _record_chat(data):
    """Extract chat info from an update and auto-register groups in DB."""
    for key in ('message', 'my_chat_member', 'chat_join_request'):
        msg = data.get(key)
        if not msg:
            continue
        chat = msg.get('chat')
        if chat and chat.get('type') in ('group', 'supergroup', 'channel'):
            cid = str(chat.get('id', ''))
            if cid:
                try:
                    from .models import TelegramGroup
                    TelegramGroup.objects.get_or_create(
                        telegram_chat_id=cid,
                        defaults={
                            'name': chat.get('title') or chat.get('username', cid),
                            'chat_type': chat.get('type', 'supergroup'),
                        },
                    )
                except Exception:
                    pass


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
    _record_chat(data)
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


def sync_groups_from_updates():
    """Discover and register Telegram groups.

    Sources:
    1. Buffered Telegram updates (getUpdates)
    2. ForwardRule targets in DB
    """
    from .models import TelegramGroup, ForwardRule, DTBSettings
    from .manager import TelegramBotManager
    try:
        if DTBSettings.load().telegram_webhook_url:
            return
    except Exception:
        pass

    bot = TelegramBotManager()

    # 1. Register groups from ForwardRule targets
    for rule in ForwardRule.objects.filter(is_enabled=True):
        target = rule.telegram_target.split(':')[0].strip()
        if target and not TelegramGroup.objects.filter(telegram_chat_id=target).exists():
            try:
                result = bot.get_chat(target)
                if result.get('ok'):
                    chat_info = result['result']
                    chat_type = chat_info.get('type', 'supergroup')
                    if chat_type in ('group', 'supergroup', 'channel'):
                        TelegramGroup.objects.get_or_create(
                            telegram_chat_id=target,
                            defaults={
                                'name': chat_info.get('title') or chat_info.get('username', target),
                                'chat_type': chat_type,
                            },
                        )
                        logger.info('DTB: registered group from rule %s: %s', rule.name, target)
            except Exception as e:
                logger.warning('DTB: could not register group %s: %s', target, e)

    # 2. Register groups from getUpdates
    try:
        resp = bot.get_updates(timeout=1)
        if resp.get('ok'):
            seen = set()
            for u in resp.get('result', []):
                for key in ('message', 'my_chat_member', 'chat_join_request', 'channel_post'):
                    msg = u.get(key)
                    if not msg:
                        continue
                    chat = msg.get('chat')
                    if chat and chat.get('type') in ('group', 'supergroup', 'channel'):
                        cid = str(chat.get('id', ''))
                        if cid and cid not in seen:
                            seen.add(cid)
                            TelegramGroup.objects.get_or_create(
                                telegram_chat_id=cid,
                                defaults={
                                    'name': chat.get('title') or chat.get('username', cid),
                                    'chat_type': chat.get('type', 'supergroup'),
                                },
                            )
            logger.info('DTB: group sync complete, total groups: %d', TelegramGroup.objects.count())
    except Exception as e:
        logger.error('DTB: group sync failed: %s', e)


def sync_invites_for_all_users():
    """Check all linked users and send invites to groups they are missing.

    Runs periodically so that users get invited to newly-added groups
    without requiring a re-link.
    """
    from .models import TelegramUser, TelegramGroup
    linked = TelegramUser.objects.filter(is_active=True, telegram_user_id__isnull=False)
    groups = TelegramGroup.objects.filter(is_active=True, auto_invite=True)
    if not linked.exists() or not groups.exists():
        return

    bot = TelegramBotManager()
    for profile in linked:
        uid = profile.telegram_user_id
        chat_id = profile.telegram_chat_id
        if not uid:
            continue
        for group in groups:
            try:
                member = bot.get_chat_member(group.telegram_chat_id, uid)
                if member.get('ok'):
                    status = member['result'].get('status', '')
                    if status in ('member', 'administrator', 'creator'):
                        continue
            except Exception:
                pass

            try:
                bot.unban_chat_member(group.telegram_chat_id, uid)
                result = bot.add_chat_member(group.telegram_chat_id, uid)
                if result.get('ok'):
                    logger.info('DTB: invited user %s to %s', uid, group.name)
                    continue
            except Exception:
                pass

            if chat_id:
                try:
                    link_result = bot.create_chat_invite_link(
                        group.telegram_chat_id,
                        name=f'DTB invite {uid}',
                        member_limit=1,
                    )
                    if link_result.get('ok'):
                        invite_url = link_result['result'].get('invite_link')
                        if invite_url:
                            bot.send_message(
                                chat_id=chat_id,
                                text=f'You have been invited to <b>{group.name}</b>:\n{invite_url}',
                            )
                            logger.info('DTB: invited user %s to %s via link', uid, group.name)
                except Exception:
                    pass


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

    sync_groups_from_updates()

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
                        _invite_to_groups(bot, user_id, chat_id=chat_id)
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


def _invite_to_groups(bot, telegram_user_id, chat_id=None):
    """Invite a (linked) Telegram user to all tracked active groups.

    Skips groups where the user is already a member. Tries addChatMember
    first; falls back to creating a one-time invite link and sending it
    to the user via DM.
    """
    from .models import TelegramGroup
    for group in TelegramGroup.objects.filter(is_active=True, auto_invite=True):
        try:
            member = bot.get_chat_member(group.telegram_chat_id, telegram_user_id)
            if member.get('ok'):
                status = member['result'].get('status', '')
                if status in ('member', 'administrator', 'creator'):
                    logger.info(
                        'User %s already in group %s, skipping invite',
                        telegram_user_id, group.name,
                    )
                    continue
        except Exception:
            pass

        try:
            bot.unban_chat_member(group.telegram_chat_id, telegram_user_id)
            result = bot.add_chat_member(group.telegram_chat_id, telegram_user_id)
            if result.get('ok'):
                logger.info(
                    'Invited user %s to Telegram group %s via addChatMember',
                    telegram_user_id, group.name,
                )
                continue
        except Exception:
            pass

        # Fallback: send an invite link to the user via DM
        if chat_id:
            try:
                link_result = bot.create_chat_invite_link(
                    group.telegram_chat_id,
                    name=f'DTB invite {telegram_user_id}',
                    member_limit=1,
                )
                if link_result.get('ok'):
                    invite_url = link_result['result'].get('invite_link')
                    if invite_url:
                        bot.send_message(
                            chat_id=chat_id,
                            text=f'You have been invited to <b>{group.name}</b>:\n{invite_url}',
                        )
                        logger.info(
                            'Invited user %s to Telegram group %s via invite link',
                            telegram_user_id, group.name,
                        )
                        continue
            except Exception:
                pass

        logger.warning(
            'Could not invite user %s to group %s',
            telegram_user_id, group.name,
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
                    '4. Click Link - you will receive an invite to the alliance Telegram group(s)\n\n'
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
    _invite_to_groups(bot, user_id, chat_id=chat_id)
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
