import logging
from datetime import timedelta

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
    """Main user page: show Telegram block with link/unlink controls."""
    from .tasks import _user_in_alliance
    profile, created = TelegramUser.objects.get_or_create(user=request.user)
    in_alliance = _user_in_alliance(request.user)

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

    from .tasks import _user_in_alliance
    if not _user_in_alliance(request.user):
        messages.error(request, _('You must be a member of the configured alliance to link Telegram.'))
        return redirect('dtb:services_overview')

    profile, created = TelegramUser.objects.get_or_create(user=request.user)

    if profile.telegram_chat_id:
        messages.warning(request, _('Telegram account is already linked. Unlink first.'))
        return redirect('dtb:services_overview')

    form = TelegramUserLinkForm(request.POST)
    if form.is_valid():
        username = form.cleaned_data['telegram_username'].lstrip('@')

        # Clean up stale pending requests.
        TelegramLinkRequest.objects.filter(
            created_at__lt=timezone.now() - timedelta(minutes=15)
        ).delete()

        # Auto-link if the user already started the bot from this Telegram account.
        pending = TelegramLinkRequest.objects.filter(
            username__iexact=username,
            created_at__gte=timezone.now() - timedelta(minutes=15),
        ).order_by('-created_at').first()

        if pending and pending.telegram_user_id:
            profile.telegram_user_id = pending.telegram_user_id
            profile.telegram_chat_id = pending.chat_id
            profile.telegram_username = pending.username or username
            profile.is_active = True
            profile.save()

            TelegramLinkRequest.objects.filter(chat_id=pending.chat_id).delete()

            bot = TelegramBotManager()
            try:
                _invite_to_groups(bot, pending.telegram_user_id)
                bot.send_message(
                    pending.chat_id,
                    '✅ Linked! Your Telegram is now connected to Alliance Auth.\n'
                    'I will forward important Discord pings here.',
                )
            except Exception:
                logger.exception('DTB: error finalizing auto-link')

            messages.success(
                request,
                _('Telegram account @%(username)s linked successfully!') % {'username': profile.telegram_username}
            )
            return redirect('dtb:services_overview')

        # Fallback: generate a linking code and DM it to the user.
        from django.conf import settings
        bot = TelegramBotManager()
        is_ok, msg = bot.test_connection()

        if not is_ok:
            messages.error(request, _('Telegram bot connection failed: %(msg)s') % {'msg': msg})
            return redirect('dtb:services_overview')

        # Generate a linking code
        import hashlib
        import time
        code = hashlib.sha256(
            f'{request.user.id}:{settings.SECRET_KEY}:{time.time()}'.encode()
        ).hexdigest()[:8].upper()

        # Store code in session for verification
        request.session['dtb_link_code'] = code
        request.session['dtb_link_username'] = username
        request.session['dtb_link_time'] = time.time()

        # Try to send a DM to the user via Telegram
        result = bot.send_message(
            chat_id=username,
            text=(
                f'Your Alliance Auth linking code: <b>{code}</b>\n\n'
                f'Go back to Auth and enter this code to complete linking.'
            ),
        )

        if result.get('ok'):
            messages.info(
                request,
                _('A verification code has been sent to @%(username)s. '
                'Enter it below to complete linking.') % {'username': username}
            )
            return render(request, 'dtb/verify_link.html', {
                'username': username,
            })
        else:
            messages.error(
                request,
                _('Could not send message to @%(username)s. '
                'Make sure you have started a chat with the bot first, '
                'and that the username is correct.') % {'username': username}
            )
            return redirect('dtb:services_overview')

    messages.error(request, _('Invalid username. Please try again.'))
    return redirect('dtb:services_overview')


@login_required
@require_POST
def verify_link(request):
    """Verify the linking code."""
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
    # For now, we ask the user to provide it or we use a webhook
    # In production, the Telegram bot would capture this via /start command
    profile.telegram_username = username
    profile.is_active = True
    profile.save()

    # Clean up session
    for key in ['dtb_link_code', 'dtb_link_username', 'dtb_link_time']:
        request.session.pop(key, None)

    messages.success(request, _('Telegram account @%(username)s linked successfully!') % {'username': username})
    return redirect('dtb:services_overview')


@login_required
@require_POST
def unlink_telegram(request):
    """Unlink Telegram account."""
    profile, created = TelegramUser.objects.get_or_create(user=request.user)
    chat_id = profile.telegram_chat_id

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

    profile.is_active = False
    profile.telegram_chat_id = ''
    profile.telegram_user_id = None
    profile.save()

    messages.info(request, _('Telegram account unlinked.'))
    return redirect('dtb:services_overview')


@login_required
def forward_history(request):
    """View forwarding history (for users with permission)."""
    if not _has_dtb_permission(request.user):
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
    groups = TelegramGroup.objects.all()
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
                        tg_id = str(info.get('id', chat_id))
                        name = info.get('title') or info.get('username', chat_id)
                        chat_type = info.get('type', 'supergroup')
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

        elif action == 'scan':
            added = 0
            try:
                bot = TelegramBotManager()
                res = bot.get_updates(timeout=0)
                if res.get('ok'):
                    seen = set()
                    for update in res.get('result', []):
                        chat = update.get('message', {}).get('chat') or \
                               update.get('my_chat_member', {}).get('chat') or \
                               update.get('chat_join_request', {}).get('chat')
                        if chat and chat.get('type') in ('group', 'supergroup', 'channel'):
                            cid = str(chat.get('id', ''))
                            if cid and cid not in seen:
                                seen.add(cid)
                                _, created = TelegramGroup.objects.get_or_create(
                                    telegram_chat_id=cid,
                                    defaults={
                                        'name': chat.get('title') or chat.get('username', cid),
                                        'chat_type': chat.get('type', 'supergroup'),
                                    },
                                )
                                if created:
                                    added += 1
                    messages.success(request, _('Scan complete. %(added)s new group(s) found.') % {'added': added})
                else:
                    messages.error(request, _('Scan failed: %(desc)s') % {
                        'desc': res.get('description', 'Unknown error')
                    })
            except Exception as e:
                messages.error(request, _('Scan error: %(error)s') % {'error': str(e)})
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
            if form.instance.autostart_bot:
                from .bot_runner import maybe_start_bot
                maybe_start_bot()
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

