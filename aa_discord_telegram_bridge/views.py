import logging
import subprocess
import shlex

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.utils.translation import gettext_lazy as _

from .models import (
    DTBSettings, DTB_VERSION, ForwardRule, TelegramUser, ForwardHistory,
    ConnectionStatus, TelegramGroup, BotStatus, TelegramLinkRequest,
)
from .forms import ForwardRuleForm, TelegramUserLinkForm, DTBSettingsForm
from .manager import TelegramBotManager, DiscordBotManager
from .telegram_handler import _invite_to_groups

logger = logging.getLogger(__name__)


def _has_dtb_permission(user):
    """Check if user has DTB admin permission."""
    return user.has_perm('aa_discord_telegram_bridge.manage_dtb_rules')


def _is_configured():
    """Check if DTB has a configured alliance_id."""
    try:
        from .models import DTBSettings
        s = DTBSettings.load()
        return s.alliance_id is not None
    except Exception:
        return False


# ── User Views ──────────────────────────────────────────────

@login_required
def services_overview(request):
    """Main user page: show Telegram block with link/unlink controls.

    Restricted to members of the configured alliance and DTB admins.
    Everyone else gets 403.
    """
    from .tasks import _user_is_dtb_member

    is_admin = _has_dtb_permission(request.user)
    in_alliance = _user_is_dtb_member(request.user)

    if not in_alliance and not is_admin:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden(_('Permission denied.'))

    profile, created = TelegramUser.objects.get_or_create(user=request.user)

    bot_link = None
    bot_username = None
    try:
        from .manager import TelegramBotManager
        res = TelegramBotManager().get_me()
        if res.get('ok'):
            bot_username = res.get('result', {}).get('username')
            if bot_username:
                bot_link = f'https://t.me/{bot_username}'
    except Exception:
        pass

    return render(request, 'dtb/services_overview.html', {
        'profile': profile,
        'bot_username': bot_username,
        'bot_link': bot_link,
        'in_alliance': in_alliance,
        'is_configured': _is_configured(),
    })


@login_required
@require_POST
def link_telegram(request):
    """Start Telegram linking process.

    If the user already opened the bot and sent /start, a pending link
    request exists and we link automatically — no code needed. Otherwise we
    fall back to the verification-code flow.
    """
    if not _is_configured():
        messages.error(request, _('DTB is not configured. Admin must set alliance_id.'))
        return redirect('dtb:services_overview')

    from .tasks import _user_is_dtb_member
    if not _user_is_dtb_member(request.user):
        messages.error(request, _('You must be a member of the configured alliance to link Telegram.'))
        return redirect('dtb:services_overview')

    profile, created = TelegramUser.objects.get_or_create(user=request.user)

    if profile.telegram_chat_id:
        messages.warning(request, _('Telegram account is already linked. Unlink first.'))
        return redirect('dtb:services_overview')

    form = TelegramUserLinkForm(request.POST)
    if form.is_valid():
        identifier = form.cleaned_data['telegram_username'].strip().lstrip('@')

        # Auto-link if the user already started the bot from this Telegram
        # account. Match by username, or by numeric Telegram ID when the user
        # has no username set. There is no expiry window: a pending request
        # stays valid until it is used.
        if identifier.isdigit():
            pending = TelegramLinkRequest.objects.filter(
                telegram_user_id=identifier,
            ).order_by('-created_at').first()
        else:
            pending = TelegramLinkRequest.objects.filter(
                username__iexact=identifier,
            ).order_by('-created_at').first()

        if pending and pending.telegram_user_id:
            profile.telegram_user_id = pending.telegram_user_id
            profile.telegram_chat_id = pending.chat_id
            profile.telegram_username = pending.username or ''
            profile.is_active = True
            profile.save()

            TelegramLinkRequest.objects.filter(chat_id=pending.chat_id).delete()

            bot = TelegramBotManager()
            try:
                _invite_to_groups(bot, pending.telegram_user_id, chat_id=pending.chat_id)
                bot.send_message(
                    pending.chat_id,
                    '✅ Linked! Your Telegram is now connected to Alliance Auth.\n'
                    'I will forward important Discord pings here.',
                )
            except Exception:
                logger.exception('DTB: error finalizing auto-link')

            if profile.telegram_username:
                messages.success(
                    request,
                    _('Telegram account @%(username)s linked successfully!') % {'username': profile.telegram_username}
                )
            else:
                messages.success(request, _('Telegram account linked successfully!'))
            return redirect('dtb:services_overview')

        # No pending /start found for this identifier.
        messages.error(
            request,
            _('No pending link found for "%(identifier)s". Open the bot, press /start, then click Link Account again.') % {'identifier': identifier}
        )
        return redirect('dtb:services_overview')

    messages.error(request, _('Invalid username. Please try again.'))
    return redirect('dtb:services_overview')


@login_required
@require_POST
def verify_link(request):
    """Verify the linking code."""
    if not _is_configured():
        messages.error(request, _('DTB is not configured. Admin must set alliance_id.'))
        return redirect('dtb:services_overview')

    from .tasks import _user_is_dtb_member
    if not _user_is_dtb_member(request.user):
        messages.error(request, _('You must be a member of the configured alliance to link Telegram.'))
        return redirect('dtb:services_overview')

    profile, created = TelegramUser.objects.get_or_create(user=request.user)
    code = request.POST.get('code', '').strip().upper()
    expected = request.session.get('dtb_link_code')
    username = request.session.get('dtb_link_username')

    if not expected or not username:
        messages.error(request, _('Linking session expired. Please try again.'))
        return redirect('dtb:services_overview')

    if code != expected:
        messages.error(request, _('Invalid code. Please try again.'))
        return render(request, 'dtb/verify_link.html', {
            'username': username,
        })

    # Code matches - we need to get the user's chat_id
    profile.telegram_username = username
    profile.is_active = True

    # Try to find the chat_id from a pending link request (user sent /start to bot)
    from .models import TelegramLinkRequest
    pending = TelegramLinkRequest.objects.filter(
        username__iexact=username,
        created_at__gte=timezone.now() - timedelta(minutes=15),
    ).order_by('-created_at').first()

    if pending and pending.telegram_user_id:
        profile.telegram_user_id = pending.telegram_user_id
        profile.telegram_chat_id = pending.chat_id
        profile.save()
        TelegramLinkRequest.objects.filter(chat_id=pending.chat_id).delete()

        bot = TelegramBotManager()
        try:
            from .telegram_handler import _invite_to_groups
            _invite_to_groups(bot, pending.telegram_user_id, chat_id=pending.chat_id)
        except Exception:
            logger.exception('DTB: error inviting user to groups after code-link')
    else:
        profile.save()

    # Clean up session
    for key in ['dtb_link_code', 'dtb_link_username', 'dtb_link_time']:
        request.session.pop(key, None)

    messages.success(request, _('Telegram account @%(username)s linked successfully!') % {'username': username})
    return redirect('dtb:services_overview')


@login_required
@require_POST
def unlink_telegram(request):
    """Unlink Telegram account and kick from tracked groups."""
    from .tasks import _user_is_dtb_member
    if not _user_is_dtb_member(request.user):
        return redirect('services:services')

    profile, created = TelegramUser.objects.get_or_create(user=request.user)
    chat_id = profile.telegram_chat_id
    tg_user_id = profile.telegram_user_id

    if chat_id:
        try:
            bot = TelegramBotManager()
            bot.send_message(
                chat_id,
                '❌ Your Telegram account has been unlinked from Alliance Auth.\n'
                'You will no longer receive forwarded Discord messages.',
            )
        except Exception:
            logger.exception('DTB: failed to send unlink notification')

    # Kick user from all tracked Telegram groups (no extra notification:
    # the unlink message above already told the user what happened)
    if tg_user_id:
        try:
            from .tasks import _kick_user_from_all_groups
            bot = TelegramBotManager()
            _kick_user_from_all_groups(bot, profile, notify=False)
        except Exception:
            logger.exception('DTB: failed to kick user from Telegram groups on unlink')

    profile.is_active = False
    profile.telegram_chat_id = ''
    profile.telegram_user_id = None
    profile.telegram_username = ''
    profile.save()

    messages.info(request, _('Telegram account unlinked.'))
    return redirect('dtb:services_overview')


@login_required
def forward_history(request):
    """View forwarding history (for users with permission)."""
    if not request.user.has_perm('aa_discord_telegram_bridge.view_forward_history'):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Permission denied.')

    history_list = ForwardHistory.objects.select_related('rule').all()
    paginator = Paginator(history_list, 50)
    page = request.GET.get('page')
    history = paginator.get_page(page)

    return render(request, 'dtb/history.html', {'history': history})


@login_required
def connection_status(request):
    """View connection status."""
    if not _has_dtb_permission(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Permission denied.')

    statuses = ConnectionStatus.objects.all()

    bot_status = BotStatus.objects.first()
    now = timezone.now()
    bot_running = bool(
        bot_status and bot_status.last_heartbeat
        and (now - bot_status.last_heartbeat).total_seconds() < 120
    )
    bot_last_seen = None
    if bot_status and bot_status.last_heartbeat:
        bot_last_seen = int((now - bot_status.last_heartbeat).total_seconds())

    return render(request, 'dtb/status.html', {
        'statuses': statuses,
        'bot_running': bot_running,
        'bot_last_seen': bot_last_seen,
    })


# ── Admin Views ─────────────────────────────────────────────

@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_rules(request):
    """List and manage forwarding rules."""
    rules = ForwardRule.objects.all()
    return render(request, 'dtb/admin_rules.html', {'rules': rules})


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_rule_add(request):
    """Add a new forwarding rule."""
    if request.method == 'POST':
        form = ForwardRuleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Rule created.'))
            return redirect('dtb:admin_rules')
    else:
        form = ForwardRuleForm()

    return render(request, 'dtb/admin_rule_form.html', {
        'form': form,
        'title': _('Add Forward Rule'),
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_rule_edit(request, rule_id):
    """Edit a forwarding rule."""
    rule = get_object_or_404(ForwardRule, pk=rule_id)

    if request.method == 'POST':
        form = ForwardRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            messages.success(request, _('Rule updated.'))
            return redirect('dtb:admin_rules')
    else:
        form = ForwardRuleForm(instance=rule)

    return render(request, 'dtb/admin_rule_form.html', {
        'form': form,
        'title': _('Edit Rule: %(name)s') % {'name': rule.name},
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
@require_POST
def admin_rule_delete(request, rule_id):
    """Delete a forwarding rule."""
    rule = get_object_or_404(ForwardRule, pk=rule_id)
    name = rule.name
    rule.delete()
    messages.success(request, _('Rule "%(name)s" deleted.') % {'name': name})
    return redirect('dtb:admin_rules')


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
@require_POST
def admin_rule_toggle(request, rule_id):
    """Toggle rule enabled/disabled."""
    rule = get_object_or_404(ForwardRule, pk=rule_id)
    rule.is_enabled = not rule.is_enabled
    rule.save()
    state = _('enabled') if rule.is_enabled else _('disabled')
    messages.info(request, _('Rule "%(name)s" %(state)s.') % {'name': rule.name, 'state': state})
    return redirect('dtb:admin_rules')


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_groups(request):
    """Manage known Telegram groups."""
    from .models import DTBSettings, TelegramGroup
    from .forms import TelegramGroupForm
    groups = TelegramGroup.objects.exclude(chat_type='private')
    form = TelegramGroupForm()

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_group':
            form = TelegramGroupForm(request.POST)
            if form.is_valid():
                chat_id = form.cleaned_data['chat_id'].strip()
                try:
                    bot = TelegramBotManager()
                    res = bot.get_chat(chat_id)
                    if res.get('ok'):
                        info = res['result']
                        chat_type = info.get('type', 'supergroup')
                        if chat_type == 'private':
                            messages.error(request, _('Cannot add private chats. Only groups and channels are supported.'))
                            return redirect('dtb:admin_groups')
                        tg_id = str(info.get('id', chat_id))
                        name = info.get('title') or info.get('username', chat_id)
                        TelegramGroup.objects.get_or_create(
                            telegram_chat_id=tg_id,
                            defaults={'name': name, 'chat_type': chat_type},
                        )
                        messages.success(request, _('Group "%(name)s" added.') % {'name': name})
                    else:
                        messages.error(request, _('Could not find chat: %(desc)s') % {
                            'desc': res.get('description', 'Unknown error')
                        })
                except Exception as e:
                    messages.error(request, _('Error: %(error)s') % {'error': str(e)})
                return redirect('dtb:admin_groups')

        elif action == 'toggle_invite':
            group_id = request.POST.get('group_id')
            try:
                g = TelegramGroup.objects.get(id=group_id)
                g.auto_invite = not g.auto_invite
                g.save(update_fields=['auto_invite'])
            except TelegramGroup.DoesNotExist:
                pass
            return redirect('dtb:admin_groups')

        elif action == 'scan':
            bot = TelegramBotManager()
            verified = 0
            errors = 0
            for g in groups:
                res = bot.get_chat(g.telegram_chat_id)
                if res.get('ok'):
                    info = res['result']
                    g.name = info.get('title') or info.get('username', g.name)
                    g.chat_type = info.get('type', g.chat_type)
                    g.is_active = True
                    g.save()
                    verified += 1
                else:
                    g.is_active = False
                    g.save()
                    errors += 1
            messages.success(
                request,
                _('Scan complete. %(verified)s group(s) verified, %(errors)s unreachable.') % {
                    'verified': verified, 'errors': errors,
                },
            )
            return redirect('dtb:admin_groups')

    return render(request, 'dtb/admin_groups.html', {
        'groups': groups,
        'form': form,
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_validate_now(request):
    """Run the Telegram membership validation/kick task immediately."""
    from .tasks import validate_all_telegram_users
    result = validate_all_telegram_users.apply()
    info = getattr(result, 'result', None)
    if isinstance(info, dict):
        messages.success(
            request,
            _('Validation complete: %(validated)s validated, %(kicked)s kicked.') % {
                'validated': info.get('validated', 0),
                'kicked': info.get('kicked', 0),
            },
        )
    else:
        messages.success(request, _('Validation task executed.'))
    return redirect('dtb:admin_groups')


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_index(request):
    """Central DTB admin dashboard."""
    from .models import DTBSettings, BotStatus
    s = DTBSettings.load()
    now = timezone.now()
    bot_status = BotStatus.objects.first()
    bot_running = bool(
        bot_status and bot_status.last_heartbeat
        and (now - bot_status.last_heartbeat).total_seconds() < 120
    )
    bot_last_seen = None
    if bot_status and bot_status.last_heartbeat:
        bot_last_seen = int((now - bot_status.last_heartbeat).total_seconds())

    ctx = {
        'version': DTB_VERSION,
        'bot_running': bot_running,
        'bot_last_seen': bot_last_seen,
        'rules_count': ForwardRule.objects.count(),
        'groups_count': TelegramGroup.objects.count(),
        'is_configured': s.alliance_id is not None,
    }
    return render(request, 'dtb/admin_index.html', ctx)


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
@require_POST
def admin_test_connection(request):
    """Test Discord and Telegram connections."""
    service = request.POST.get('service', 'both')
    results = {}

    if service in ('telegram', 'both'):
        bot = TelegramBotManager()
        is_ok, msg = bot.test_connection()
        status, created = ConnectionStatus.objects.update_or_create(
            service='telegram',
            defaults={
                'is_connected': is_ok,
                'last_checked': timezone.now(),
                'last_success': timezone.now() if is_ok else None,
                'error_message': '' if is_ok else msg,
            },
        )
        results['telegram'] = {'ok': is_ok, 'message': msg}

    if service in ('discord', 'both'):
        bot = DiscordBotManager()
        is_ok, msg = bot.test_connection()
        status, created = ConnectionStatus.objects.update_or_create(
            service='discord',
            defaults={
                'is_connected': is_ok,
                'last_checked': timezone.now(),
                'last_success': timezone.now() if is_ok else None,
                'error_message': '' if is_ok else msg,
            },
        )
        results['discord'] = {'ok': is_ok, 'message': msg}

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(results)

    for svc, res in results.items():
        if res['ok']:
            messages.success(request, _('%(svc)s: %(msg)s') % {'svc': svc.title(), 'msg': res["message"]})
        else:
            messages.error(request, _('%(svc)s: %(msg)s') % {'svc': svc.title(), 'msg': res["message"]})

    return redirect('dtb:connection_status')


# ── Settings ─────────────────────────────────────────────────

@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_settings(request):
    """Edit DTB plugin settings."""
    s = DTBSettings.load()

    if request.method == 'POST':
        form = DTBSettingsForm(request.POST, instance=s)
        if form.is_valid():
            form.save()
            messages.success(request, _('Settings saved.'))
            return redirect('dtb:admin_settings')
    else:
        form = DTBSettingsForm(instance=s)

    return render(request, 'dtb/admin_settings.html', {
        'form': form,
        'current_version': DTB_VERSION,
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_setup(request):
    """Guided first-time setup wizard."""
    from .models import ForwardRule, ConnectionStatus
    from .forms import ForwardRuleForm

    s = DTBSettings.load()
    settings_form = DTBSettingsForm(instance=s)
    rule_form = ForwardRuleForm()
    test_results = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'save_tokens':
            settings_form = DTBSettingsForm(request.POST, instance=s)
            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, _('Settings saved.'))
                return redirect('dtb:admin_setup')
        elif action == 'add_rule':
            rule_form = ForwardRuleForm(request.POST)
            if rule_form.is_valid():
                rule_form.save()
                messages.success(request, _('Forwarding rule added.'))
                return redirect('dtb:admin_setup')
        elif action == 'test':
            from .manager import TelegramBotManager, DiscordBotManager
            test_results = {}
            for svc, mgr in (('telegram', TelegramBotManager()),
                             ('discord', DiscordBotManager())):
                ok, msg = mgr.test_connection()
                ConnectionStatus.objects.update_or_create(
                    service=svc,
                    defaults={
                        'is_connected': ok,
                        'last_checked': timezone.now(),
                        'last_success': timezone.now() if ok else None,
                        'error_message': '' if ok else msg,
                    },
                )
                test_results[svc] = {'ok': ok, 'message': msg}

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(test_results)

    conn_status = {c.service: c for c in ConnectionStatus.objects.all()}
    ctx = {
        'settings_form': settings_form,
        'rule_form': rule_form,
        'test_results': test_results,
        'rules_count': ForwardRule.objects.count(),
        'conn_status': conn_status,
        'current_version': DTB_VERSION,
    }
    return render(request, 'dtb/admin_setup.html', ctx)


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_logs(request):
    """View bot service logs.

    Tries systemd journal first (``journalctl -u aa-dtb-bot``); if no such
    unit exists (e.g. the bot runs under supervisor, or on systems without
    journald) it falls back to a plain log file so the page still works.
    """
    import os
    import subprocess

    line_count = int(request.GET.get('lines', 100))
    line_count = max(20, min(line_count, 500))
    errors_only = request.GET.get('errors', '') == '1'
    service_name = 'aa-dtb-bot'

    # Candidate paths for supervisor / plain-file deployments.
    candidate_paths = []
    try:
        from django.conf import settings as _s
        configured = getattr(_s, 'DTB_BOT_LOG_FILE', '') or ''
    except Exception:
        configured = ''
    if configured:
        candidate_paths.append(configured)
    candidate_paths += [
        '/var/log/supervisor/dtb-bot.log',
        '/var/log/myauth/dtb-bot.log',
        '/var/log/dtb-bot.log',
        '/var/log/aa-dtb-bot.log',
    ]

    output = ''
    source = None

    # 1) systemd journal
    try:
        cmd = ['journalctl', '-u', service_name, '-n', str(line_count),
               '--no-pager', '--no-hostname']
        if errors_only:
            cmd += ['-p', 'err']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            source = f'journalctl -u {service_name}'
            output = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        output = f'Error running journalctl: {e}'

    # 2) fallback: tail a plain log file
    if source is None:
        for path in candidate_paths:
            if os.path.isfile(path):
                try:
                    with open(path, 'r', errors='replace') as f:
                        lines = f.readlines()
                except OSError as e:
                    output = f'Cannot read log file {path}: {e}'
                    continue
                if errors_only:
                    lines = [
                        l for l in lines
                        if 'error' in l.lower() or 'exception' in l.lower() or 'traceback' in l.lower()
                    ]
                tail = [l for l in lines[-line_count:]]
                if tail:
                    source = path
                    output = ''.join(tail).strip()
                    break

    if source is not None:
        output = f'# {source}\n{output}'
    elif not output:
        output = (
            'No supported log source found. Tried: '
            f'journalctl -u {service_name}'
            + (''.join(f', {p}' for p in candidate_paths))
        )

    return render(request, 'dtb/admin_logs.html', {
        'log_output': output,
        'line_count': line_count,
        'errors_only': errors_only,
        'service_name': service_name,
        'log_source': source,
    })


def _member_character_info(user):
    """Return (character_name, alliance_ticker, corp_name) for a user's main char."""
    try:
        profile = getattr(user, 'profile', None)
        main = getattr(profile, 'main_character', None)
        if main:
            return (
                getattr(main, 'character_name', '') or '',
                getattr(main, 'alliance_ticker', '') or '',
                getattr(main, 'corporation_name', '') or '',
            )
    except Exception:
        pass
    # Fallback: first character ownership
    try:
        ownerships = None
        if hasattr(user, 'character_ownerships'):
            ownerships = user.character_ownerships.all()
        elif hasattr(user, 'character_ownership'):
            ownerships = [user.character_ownership]
        if ownerships:
            for ownership in ownerships:
                char = getattr(ownership, 'character', None)
                if char:
                    return (
                        getattr(char, 'character_name', '') or '',
                        getattr(char, 'alliance_ticker', '') or '',
                        getattr(char, 'corporation_name', '') or '',
                    )
    except Exception:
        pass
    return ('', '', '')


def _fetch_tg_member_details(bot, tg_user, group_chats):
    """Fetch Telegram display name, bot flag and group-admin status for a user.

    Tries the user's own chat first (if known), then each tracked group.
    Returns ``(name, is_bot, admin_groups)`` and never raises.
    """
    name = tg_user.telegram_username or ''
    is_bot = False
    admin_groups = []

    candidate_chats = []
    if tg_user.telegram_chat_id:
        candidate_chats.append((tg_user.telegram_chat_id, None))
    for g in group_chats:
        candidate_chats.append((g.telegram_chat_id, g.name))

    seen = set()
    for cid, gname in candidate_chats:
        if cid in seen:
            continue
        seen.add(cid)
        try:
            res = bot.get_chat_member(cid, tg_user.telegram_user_id)
            if not res.get('ok'):
                continue
            result = res.get('result') or {}
            user = result.get('user') or {}
            first = (user.get('first_name') or '').strip()
            last = (user.get('last_name') or '').strip()
            if first or last:
                name = f'{first} {last}'.strip()
            if user.get('is_bot'):
                is_bot = True
            status = result.get('status')
            if gname and status in ('creator', 'administrator'):
                admin_groups.append(gname)
        except Exception:
            continue
    return name, is_bot, admin_groups


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_members(request):
    """List all linked Telegram users (portal nickname, status, kick).

    Renders instantly from the DB; Telegram display names, bot flags and
    group-admin status are fetched lazily per member via ``admin_member_info``
    (AJAX) so the page never blocks on the Telegram API.
    """
    members = []
    profiles = TelegramUser.objects.select_related('user').exclude(
        telegram_user_id__isnull=True,
    ).order_by('-is_active', 'user__username')
    for p in profiles:
        char_name, alliance_ticker, corp_name = _member_character_info(p.user)
        members.append({
            'user_pk': p.user.pk,
            'portal_name': p.user.username,
            'char_name': char_name,
            'alliance_ticker': alliance_ticker,
            'corp_name': corp_name,
            'tg_username': p.telegram_username or '',
            'tg_user_id': p.telegram_user_id,
            'is_active': p.is_active,
            'is_dtb_admin': _has_dtb_permission(p.user),
        })
    return render(request, 'dtb/admin_members.html', {
        'members': members,
        'members_count': len(members),
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
def admin_member_info(request, user_pk):
    """AJAX: return Telegram name, bot flag and group-admin status for a user."""
    from .models import TelegramGroup
    profile = get_object_or_404(TelegramUser, user=user_pk)
    bot = TelegramBotManager()
    group_chats = list(TelegramGroup.objects.filter(is_active=True))
    name, is_bot, admin_groups = _fetch_tg_member_details(bot, profile, group_chats)
    return JsonResponse({
        'name': name,
        'is_bot': is_bot,
        'is_tg_admin': bool(admin_groups),
        'admin_groups': admin_groups,
    })


@login_required
@permission_required('aa_discord_telegram_bridge.manage_dtb_rules', raise_exception=True)
@require_POST
def admin_member_kick(request, user_pk):
    """Kick a linked user from all Telegram groups and unlink their profile."""
    from .tasks import _kick_user_from_all_groups
    profile = get_object_or_404(TelegramUser, user=user_pk)
    bot = TelegramBotManager()
    try:
        kicked = _kick_user_from_all_groups(bot, profile, notify=False)
        if kicked:
            messages.success(
                request,
                _('User %(name)s kicked from Telegram groups and unlinked.') % {'name': profile.user.username},
            )
        else:
            messages.warning(
                request,
                _('Failed to kick %(name)s from any group. Profile kept linked; will retry automatically.') % {'name': profile.user.username},
            )
    except Exception as e:
        messages.error(request, _('Kick failed: %(error)s') % {'error': str(e)})
    return redirect('dtb:admin_members')

