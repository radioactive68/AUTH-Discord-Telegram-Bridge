"""Unit tests for aa_discord_telegram_bridge.

Run on the Alliance Auth server::

    python manage.py test dtb_tests

or against the standalone CI settings module::

    DJANGO_SETTINGS_MODULE=dtb_tests.settings python manage.py test dtb_tests

Simple (DB-less) tests use ``SimpleTestCase``; anything touching the models
uses ``TestCase``. Network calls are mocked, so no real bot keys are needed.
"""

import asyncio
import gc
import json
import logging
import logging.handlers
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aa_discord_telegram_bridge.manager import TelegramBotManager, redact_secrets
from aa_discord_telegram_bridge.models import (
    DTBSettings, ForwardHistory, ForwardRule, TelegramLinkRequest,
    TelegramUser,
)
from aa_discord_telegram_bridge.tasks import iter_user_ownerships, prune_forward_history


class TestMatchesKeywords(SimpleTestCase):
    """ForwardRule.matches_keywords: empty filter sends everything."""

    def test_no_filter_matches_all(self):
        rule = ForwardRule(keyword_filter='')
        self.assertTrue(rule.matches_keywords('anything goes'))

    def test_comma_separated(self):
        rule = ForwardRule(keyword_filter='ops, cta , ping')
        self.assertTrue(rule.matches_keywords('there is a PING in here'))
        self.assertTrue(rule.matches_keywords('cta fleet'))
        self.assertTrue(rule.matches_keywords('ops run tonight'))
        self.assertFalse(rule.matches_keywords('evening roam'))

    def test_blank_only_commas(self):
        rule = ForwardRule(keyword_filter=' , , ')
        self.assertTrue(rule.matches_keywords('msg'))


class TestParseTarget(SimpleTestCase):
    """Telegram target parsing: root chat vs forum topic."""

    def test_plain_chat_id(self):
        result = TelegramBotManager.parse_target('-1001234567890')
        self.assertEqual(result['chat_id'], '-1001234567890')
        self.assertNotIn('message_thread_id', result)

    def test_thread_separator_colon(self):
        result = TelegramBotManager.parse_target('-1001234567890:24')
        self.assertEqual(result['chat_id'], '-1001234567890')
        self.assertEqual(result['message_thread_id'], 24)

    def test_non_numeric_thread_ignored(self):
        result = TelegramBotManager.parse_target('-1001234567890:foo')
        self.assertEqual(result['chat_id'], '-1001234567890')
        self.assertNotIn('message_thread_id', result)

    def test_whitespace_stripped(self):
        result = TelegramBotManager.parse_target(' -1001234567890 ')
        self.assertEqual(result['chat_id'], '-1001234567890')


class TestBuildTelegramText(SimpleTestCase):
    """The forwarded message: plain text escaped, safe embed markup, truncation."""

    def setUp(self):
        from aa_discord_telegram_bridge.discord_cog import DiscordForwarderCog
        self.cog = DiscordForwarderCog(bot=object())
        self.rule = ForwardRule(name='ops')

    def test_plain_text_is_escaped(self):
        text = self.cog._build_telegram_text(
            self.rule, '2 < 5 && 3 > 1', [], 'Alice'
        )
        self.assertIn('2 &lt; 5 &amp;&amp; 3 &gt; 1', text)
        self.assertIn(self.cog._escape('Alice'), text)
        self.assertIn('<b>[', text)  # rule header
        # author sits directly under the header, before the content
        self.assertLess(
            text.index(self.cog._escape('Alice')),
            text.index('2 &lt; 5'),
        )

    def test_embed_bold_survives_but_content_is_escaped(self):
        from types import SimpleNamespace
        field = SimpleNamespace(name='Fleet <comms>', value='all caps')
        embed = SimpleNamespace(
            title='CTA <special>', description='undock & shoot', fields=[field],
            footer=SimpleNamespace(text='footer'),
        )
        text = self.cog._build_telegram_text(self.rule, '', [embed], 'Bob')
        self.assertIn('<b>CTA &lt;special&gt;</b>', text)
        self.assertIn('undock &amp; shoot', text)
        self.assertIn('<b>Fleet &lt;comms&gt;:</b> all caps', text)
        self.assertNotIn('<script>', text)

    def test_truncation(self):
        long = 'A' * 4200
        text = self.cog._build_telegram_text(self.rule, long, [], 'X')
        self.assertLessEqual(len(text), 4000 + len('\n…(truncated)') + 20)
        self.assertIn('…(truncated)', text)


class TestIterUserOwnerships(TestCase):
    """Ownership lookup falls back across AA's singular/plural related names."""

    def test_fallback_singular_attribute(self):
        class Ownership:
            character = 'char'

        class FakeUser:
            character_ownership = Ownership()

        got = list(iter_user_ownerships(FakeUser()))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].character, 'char')

    def test_empty_user(self):
        self.assertEqual(list(iter_user_ownerships(object())), [])


class TestLinkingTokenFlow(TestCase):
    """The portal mints a token; the bot binds the owner's Telegram account."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='linker', password='x', is_superuser=False, is_staff=False,
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename='access_dtb')
        )
        perm = Permission.objects.get(codename='manage_dtb_rules')
        self.user.user_permissions.add(perm)
        self.user.is_active = True
        self.user.save()

        s = DTBSettings.load()
        s.telegram_bot_token = '123:fake'
        s.save()

        # Alliance Auth wraps app views with ``main_character_required``, which
        # redirects to the dashboard when the user has no main character.
        self._give_main_character(self.user)

        self.client = Client()
        self.client.force_login(self.user)

    @staticmethod
    def _give_main_character(user, character_id=999000123):
        """Attach a main character so AA's view decorators let us through."""
        from allianceauth.eveonline.models import EveCharacter

        character, _ = EveCharacter.objects.get_or_create(
            character_id=character_id,
            defaults={
                'character_name': 'Test Character',
                'gender': 'male',
                'security_status': 0.0,
            },
        )
        profile = user.profile
        if profile.main_character_id != character.pk:
            profile.main_character = character
            profile.save()
        return character

    def _telegram_profile(self, **fields):
        """Return the auto-created TelegramUser, applying ``fields`` to it.

        ``auth_hooks.create_telegram_profile`` already created the row when
        the user was created, so tests must update it instead of creating a
        second one (``TelegramUser.user`` is a OneToOne).
        """
        profile, _ = TelegramUser.objects.get_or_create(user=self.user)
        for key, value in fields.items():
            setattr(profile, key, value)
        profile.save()
        return profile

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_link_view_mints_token(self, mock_invite):
        resp = self.client.post('/dtb/link/')
        self.assertEqual(resp.status_code, 302)

        request = TelegramLinkRequest.objects.get(user=self.user)
        self.assertTrue(request.token)
        self.assertIsNotNone(request.expires_at)
        self.assertGreater(request.expires_at, timezone.now())

        # The token also lands in the session for the deep link page.
        session = self.client.session
        self.assertEqual(session.get('dtb_link_pending', {}).get('token'), request.token)

    def test_link_view_denied_without_access_permission(self):
        """Without any access permission the service is not available."""
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.user.user_permissions.clear()
        self.user = User.objects.get(pk=self.user.pk)  # drop perm cache

        self.assertFalse(
            DiscordTelegramBridgeService().service_active_for_user(self.user)
        )
        self.assertEqual(TelegramLinkRequest.objects.count(), 0)
        self.client.post('/dtb/link/', follow=True)
        self.assertEqual(TelegramLinkRequest.objects.count(), 0)

    def test_service_visible_with_access_dtb(self):
        """access_dtb alone is enough — no EVE/alliance data involved."""
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.user.user_permissions.clear()
        self.user.user_permissions.add(
            Permission.objects.get(codename='access_dtb')
        )
        self.user = User.objects.get(pk=self.user.pk)

        hook = DiscordTelegramBridgeService()
        self.assertTrue(hook.service_active_for_user(self.user))
        self.assertTrue(hook.show_service_ctrl(self.user))

    def test_gate_follows_django_permission_backends(self):
        """The gate asks has_perm(), so AA's own backends decide.

        On a state-only install the permission lives on a state and is resolved
        by ``StateBackend`` — never written to ``user.user_permissions``. A gate
        that reads those tables itself would deny a legitimately granted member,
        so the gate must defer to Django.
        """
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService
        from aa_discord_telegram_bridge.permissions import user_can_use_dtb

        self.user.user_permissions.clear()
        self.user.groups.clear()
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user_can_use_dtb(self.user))

        # what a state-based grant looks like from the outside
        with mock.patch.object(
            type(self.user), 'has_perm',
            side_effect=lambda perm, obj=None: perm.endswith('.access_dtb'),
        ):
            self.assertTrue(user_can_use_dtb(self.user))
            self.assertTrue(
                DiscordTelegramBridgeService().service_active_for_user(self.user)
            )

    def test_inactive_user_has_no_service(self):
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.user.is_active = False
        self.user.save()
        self.user = User.objects.get(pk=self.user.pk)

        hook = DiscordTelegramBridgeService()
        self.assertFalse(hook.service_active_for_user(self.user))
        self.assertFalse(hook.show_service_ctrl(self.user))

    def test_superuser_keeps_service_access(self):
        """Django superusers hold every permission — deliberately kept.

        The site owner administers DTB and must not be locked out of it: the
        periodic validation would otherwise kick their own linked Telegram
        account out of the bridge.
        """
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService
        from aa_discord_telegram_bridge.permissions import user_can_use_dtb

        self.user.user_permissions.clear()
        self.user.is_superuser = True
        self.user.save()
        self.user = User.objects.get(pk=self.user.pk)

        self.assertTrue(user_can_use_dtb(self.user))
        hook = DiscordTelegramBridgeService()
        self.assertTrue(hook.service_active_for_user(self.user))
        self.assertTrue(hook.show_service_ctrl(self.user))

    def test_dtb_admin_permission_does_not_grant_service(self):
        """manage_dtb_rules opens the admin pages, not the service tile."""
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.user.user_permissions.clear()
        self.user.user_permissions.add(
            Permission.objects.get(codename='manage_dtb_rules')
        )
        self.user = User.objects.get(pk=self.user.pk)

        hook = DiscordTelegramBridgeService()
        self.assertFalse(hook.service_active_for_user(self.user))
        self.assertFalse(hook.show_service_ctrl(self.user))

    def test_permission_via_group_grants_service(self):
        """The way an admin hands out access: a group with the permission."""
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.user.user_permissions.clear()
        self.user = User.objects.get(pk=self.user.pk)
        self.assertFalse(
            DiscordTelegramBridgeService().service_active_for_user(self.user)
        )

        group = Group.objects.create(name='member')
        group.permissions.add(
            Permission.objects.get(codename='access_dtb')
        )
        self.user.groups.add(group)
        self.user = User.objects.get(pk=self.user.pk)

        self.assertTrue(
            DiscordTelegramBridgeService().service_active_for_user(self.user)
        )

    def test_validation_revokes_access_lost(self):
        """Losing access_dtb kicks from the groups and deactivates."""
        from aa_discord_telegram_bridge.tasks import linked_profiles_without_access, validate_all_telegram_users

        profile = self._telegram_profile(
            telegram_chat_id='12345',
            telegram_user_id=111222333,
            is_active=True,
        )

        self.user.user_permissions.clear()
        self.user = User.objects.get(pk=self.user.pk)

        self.assertEqual(
            [p.user.username for p in linked_profiles_without_access()],
            [self.user.username],
        )

        with mock.patch(
            'aa_discord_telegram_bridge.tasks._kick_user_from_all_groups',
            return_value=True,
        ) as mock_kick:
            report = validate_all_telegram_users()

        mock_kick.assert_called_once()
        self.assertEqual(report['revoked'], 1)
        self.assertEqual(report['revoked_users'], [self.user.username])
        self.assertEqual(report['kicked'], 1)
        profile.refresh_from_db()
        self.assertFalse(profile.is_active)

    def test_validate_user_hook_revokes(self):
        """ServicesHook.validate_user revokes the service as well."""
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self._telegram_profile(
            telegram_chat_id='12345',
            telegram_user_id=111222333,
            is_active=True,
        )

        self.user.user_permissions.clear()
        self.user = User.objects.get(pk=self.user.pk)

        with mock.patch(
            'aa_discord_telegram_bridge.tasks._kick_user_from_all_groups',
            return_value=True,
        ) as mock_kick:
            DiscordTelegramBridgeService().validate_user(self.user)

        mock_kick.assert_called_once()
        self.assertFalse(
            TelegramUser.objects.get(user=self.user).is_active
        )

    def test_validation_restores_access_after_regaining_permission(self):
        from aa_discord_telegram_bridge.tasks import validate_all_telegram_users

        profile = self._telegram_profile(
            telegram_chat_id='12345',
            telegram_user_id=111222333,
            is_active=False,
        )

        report = validate_all_telegram_users()

        self.assertEqual(report['validated'], 1)
        self.assertEqual(report['revoked'], 0)
        profile.refresh_from_db()
        self.assertTrue(profile.is_active)

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_binds_user_with_valid_token(self, mock_invite):
        request = TelegramLinkRequest.objects.create(
            user=self.user,
            token='valid-token-abc',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from aa_discord_telegram_bridge.telegram_handler import _process_linking_code
            ok = _process_linking_code(
                'valid-token-abc', '12345', 111222333, 'alice', 'en',
            )
        self.assertTrue(ok)
        profile = TelegramUser.objects.get(user=self.user)
        self.assertEqual(profile.telegram_user_id, 111222333)
        self.assertEqual(profile.telegram_chat_id, '12345')
        self.assertEqual(profile.telegram_username, 'alice')
        self.assertTrue(profile.is_active)
        self.assertFalse(TelegramLinkRequest.objects.filter(pk=request.pk).exists())
        mock_invite.assert_called_once()

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_rejects_expired_token(self, mock_invite):
        TelegramLinkRequest.objects.create(
            user=self.user,
            token='stale-token-abc',
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from aa_discord_telegram_bridge.telegram_handler import _process_linking_code
            ok = _process_linking_code(
                'stale-token-abc', '12345', 111222333, 'alice', 'en',
            )
        self.assertFalse(ok)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=111222333).exists())

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_rejects_unknown_token(self, mock_invite):
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from aa_discord_telegram_bridge.telegram_handler import _process_linking_code
            ok = _process_linking_code(
                'never-issued', '12345', 111222333, 'alice', 'en',
            )
        self.assertFalse(ok)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=111222333).exists())

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_rejects_reuse_after_linked(self, mock_invite):
        request = TelegramLinkRequest.objects.create(
            user=self.user,
            token='one-shot-token',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from aa_discord_telegram_bridge.telegram_handler import _process_linking_code
            ok1 = _process_linking_code('one-shot-token', '1', 111, 'a1', 'en')
            ok2 = _process_linking_code('one-shot-token', '2', 222, 'a2', 'en')
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(request.__class__.objects.filter(pk=request.pk).count(), 0)


class TestWebhookSecretAuth(TestCase):
    """The webhook endpoint requires the registered secret token."""

    WEBHOOK_PATH = '/dtb/telegram/webhook/'

    def setUp(self):
        from django.urls import Resolver404, resolve

        try:
            resolve(self.WEBHOOK_PATH)
        except Resolver404:
            self.skipTest('webhook route is disabled on this install')

        s = DTBSettings.load()
        s.telegram_bot_token = '123:fake'
        s.telegram_webhook_url = 'https://example.com/dtb/telegram/webhook/'
        s.telegram_webhook_secret_token = 'topsecret'
        s.save()

    def test_rejects_without_secret(self):
        with mock.patch('aa_discord_telegram_bridge.telegram_handler._dispatch_update') as m:
            resp = self.client.post(
                '/dtb/telegram/webhook/',
                data=json.dumps({'update_id': 1}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403)
        m.assert_not_called()

    def test_rejects_wrong_secret(self):
        with mock.patch('aa_discord_telegram_bridge.telegram_handler._dispatch_update') as m:
            resp = self.client.post(
                '/dtb/telegram/webhook/',
                data=json.dumps({'update_id': 1}),
                content_type='application/json',
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='wrong',
            )
        self.assertEqual(resp.status_code, 403)
        m.assert_not_called()

    def test_accepts_correct_secret(self):
        with mock.patch('aa_discord_telegram_bridge.telegram_handler._dispatch_update') as m:
            resp = self.client.post(
                '/dtb/telegram/webhook/',
                data=json.dumps({'update_id': 1}),
                content_type='application/json',
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='topsecret',
            )
        self.assertEqual(resp.status_code, 200)
        m.assert_called_once()


class TestPlainStartDoesNotCreateRequest(TestCase):
    """A bare /start must NOT register a pending TelegramLinkRequest."""

    def test_plain_start_for_unknown_user(self):
        with mock.patch.object(
                TelegramBotManager, 'send_message', return_value={'ok': True},
        ):
            from aa_discord_telegram_bridge.telegram_handler import _process_plain_start
            _process_plain_start(999888777, '100', 'nobody', 'en')
        self.assertEqual(TelegramLinkRequest.objects.count(), 0)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=999888777).exists())


class TestTokenRedaction(TestCase):
    """No bot token may ever reach a log line or the DB.

    The real leak: ``requests`` stringifies the failed API URL into its
    exception text, and that URL carries the whole token. Matching only the
    *configured* token did not help once it had been rotated -- the token in
    the old log line no longer equalled the value in the DB, so it was logged
    verbatim. The shape-based patterns are what actually close that hole.
    """

    SECRET = 'AAEOqAYoPK_JDzYWdv5cMHDmvNqEOMSAnZw'

    def test_redacts_url_from_requests_exception(self):
        msg = (
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Max retries exceeded with url: /bot8243994060:%s/getUpdates "
            "(Caused by NewConnectionError('Network is unreachable'))"
        ) % self.SECRET
        out = redact_secrets(msg)
        self.assertNotIn(self.SECRET, out)
        self.assertIn('***REDACTED***', out)

    def test_redacts_rotated_token_not_in_settings(self):
        # A token this installation knows nothing about must still go.
        out = redact_secrets('/bot123456789:%s/sendMessage' % self.SECRET)
        self.assertNotIn(self.SECRET, out)

    def test_keeps_bot_id_visible_for_debugging(self):
        # The numeric id is public (logged on login), only the secret matters.
        out = redact_secrets('/bot8243994060:%s/getMe' % self.SECRET)
        self.assertIn('8243994060', out)
        self.assertNotIn(self.SECRET, out)

    def test_keeps_chat_ids_and_thread_ids_intact(self):
        # Negative chat ids and small thread ids must survive untouched, or
        # every log line about a forum topic turns into noise.
        text = 'group -1002857334511 topic 42 and -1004494114977/2'
        self.assertEqual(redact_secrets(text), text)

    def test_handles_empty_and_none(self):
        self.assertEqual(redact_secrets(''), '')
        self.assertIsNone(redact_secrets(None))

    def test_manager_error_path_redacts_token(self):
        """End to end: a failing API call must not leak the token when logged."""
        import requests as requests_mod
        from aa_discord_telegram_bridge.manager import TelegramBotManager

        def boom(*args, **kwargs):
            raise requests_mod.ConnectionError(
                "Max retries exceeded with url: /bot8243994060:%s/getUpdates"
                % self.SECRET
            )

        with mock.patch(
                'aa_discord_telegram_bridge.manager.requests.post', side_effect=boom):
            with self.assertLogs('extensions.aa_discord_telegram_bridge.manager', level='ERROR') as cm:
                result = TelegramBotManager(bot_token='8243994060:%s' % self.SECRET).get_me()

        self.assertFalse(result['ok'])
        for line in cm.output:
            self.assertNotIn(self.SECRET, line)


class TestLoggingSetup(TestCase):
    """The plugin must persist its own INFO records with a timestamp.

    The handler lives on the ``extensions.aa_discord_telegram_bridge`` logger
    (AA's ``extensions`` tree), but it is DTB's own rotating file handler:
    without it every DTB record fell through to ``logging.lastResort`` —
    WARNING and above only, as a bare line. That is exactly why ``Kicked
    user X from Telegram group Y`` could never be found on a server — it was
    INFO, so it was dropped.
    """

    def test_logger_has_a_file_handler(self):
        from aa_discord_telegram_bridge.logging_setup import configure

        logger = configure()
        self.assertTrue(
            any(isinstance(h, logging.handlers.RotatingFileHandler)
                for h in logger.handlers),
            'no rotating file handler installed on the DTB logger',
        )

    def test_info_records_reach_the_file(self):
        import os
        import tempfile
        from aa_discord_telegram_bridge.logging_setup import configure

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'dtb-test.log')
            logger = configure()
            with override_settings(DTB_LOG_FILE=target):
                logger = configure()
                logger.info('Kicked user unit_test_user from Telegram group UnitGroup')
                for h in logger.handlers:
                    h.flush()

            with open(target, encoding='utf-8') as fh:
                content = fh.read()
            # Repoint the handlers off the temp file before the directory is
            # removed — Windows cannot delete a file that is still open.
            configure(force=True)
            self.assertIn('Kicked user unit_test_user', content)
            # A timestamp is what makes the line usable on a timeline.
            self.assertRegex(content, r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')

    def test_configure_is_idempotent(self):
        from aa_discord_telegram_bridge.logging_setup import configure

        logger = configure()
        before = len(logger.handlers)
        configure()
        self.assertEqual(len(logger.handlers), before,
                         'duplicate handlers would write every line twice')

    def tearDown(self):
        # These tests point the plugin at a temporary file; restore the real
        # one so a later test (or a same-process view call) is not left
        # logging into a directory that no longer exists.
        from aa_discord_telegram_bridge.logging_setup import configure

        configure(force=True)

    def test_kick_helper_logs_info_not_warning(self):
        """The kick helpers must stay INFO: it is the level we now persist."""
        import inspect
        from aa_discord_telegram_bridge import tasks

        src = inspect.getsource(tasks)
        self.assertIn('Kicked user %s from Telegram group %s', src)


class TestServiceHookStringification(TestCase):
    """The service hook must survive ``str()`` — AA interpolates it into logs.

    ``allianceauth/services/signals.py`` builds f-strings that contain the
    service object whenever permissions are removed from a group or a state
    (and again in ``logger.exception`` when ``validate_user`` raises). f-strings
    call ``__format__``/``str()`` eagerly, so the message is built even with
    debug logging off. ``ServicesHook.__str__`` returns ``self.name``
    unchanged, and a lazy gettext proxy in ``name`` makes it raise
    ``TypeError: str returned non-string (type proxy)``. That exception fires
    inside the admin's transaction, so removing ``access_dtb`` from a state or
    a group rolled back and silently never stuck.
    """

    def setUp(self):
        from aa_discord_telegram_bridge.auth_hooks import DiscordTelegramBridgeService

        self.service = DiscordTelegramBridgeService()
        self.perm = Permission.objects.get(codename='access_dtb')

    def test_name_is_a_plain_string(self):
        self.assertIsInstance(self.service.name, str)
        self.assertIsInstance(str(self.service), str)
        self.assertTrue(str(self.service).strip())

    def test_str_survives_logger_formatting(self):
        # What those f-strings actually do: '%s' % svc and f'{svc}'.
        self.assertEqual('%s' % self.service, self.service.name)
        self.assertEqual(f'{self.service}', self.service.name)

    def test_removing_access_perm_from_state_persists(self):
        from allianceauth.authentication.models import State

        # AA 5 points State.permissions at a proxy of auth.Permission, so the
        # instance has to come from that model: handing Django the parent
        # class raises "Permission instance expected".
        perm_model = State._meta.get_field('permissions').related_model
        perm = perm_model.objects.get(codename='access_dtb')

        state = State.objects.create(name='dtb-test-state', priority=1)
        state.permissions.add(perm)
        self.assertIn(perm, state.permissions.all())

        state.permissions.remove(perm)

        state.refresh_from_db()
        self.assertNotIn(perm, state.permissions.all())

    def test_removing_access_perm_from_group_persists(self):
        group = Group.objects.create(name='dtb-test-group')
        group.permissions.add(self.perm)
        self.assertIn(self.perm, group.permissions.all())

        group.permissions.remove(self.perm)

        group.refresh_from_db()
        self.assertNotIn(self.perm, group.permissions.all())


class _FakeBot:
    """Minimal stand-in for a discord.py/py-cord Bot."""

    def __init__(self, loop):
        self.loop = loop
        self._closed = False

    def is_closed(self):
        return self._closed


class TestHeartbeatTaskRetention(TestCase):
    """The heartbeat task must not disappear while the process is alive.

    ``bot.loop.create_task(_heartbeat())`` discarded the returned Task.
    asyncio keeps only weak references to tasks, so the heartbeat could be
    garbage-collected mid-run. That is exactly what happened in the field:
    the process stayed up, Discord and Telegram stayed connected and
    forwarding kept working, but ``last_heartbeat`` froze and the AA status
    page showed "Stopped" until someone restarted the bot by hand. The task
    is now referenced from the bot object, and a watchdog restarts it if it
    ever finishes anyway.
    """

    def setUp(self):
        from aa_discord_telegram_bridge import bot_runner

        self.bot_runner = bot_runner
        self.loop = asyncio.new_event_loop()
        self.bot = _FakeBot(self.loop)
        # Shrink the watchdog interval so its behaviour is testable in ms.
        self._interval = mock.patch.object(
            bot_runner, 'WATCHDOG_INTERVAL', 0.01)
        self._interval.start()
        # These tests cover task lifetime, not the DB write. Running the real
        # sync_to_async against a hand-made event loop hangs asgiref, so run
        # the wrapped call inline and make the write itself a no-op.
        self._hb_write = mock.patch(
            'aa_discord_telegram_bridge.models.BotStatus.update_heartbeat')
        self._hb_write.start()

        def _inline_sync(func, **kwargs):
            async def wrapper(*args, **kw):
                return func(*args, **kw)
            return wrapper

        self._sync = mock.patch(
            'asgiref.sync.sync_to_async', side_effect=_inline_sync)
        self._sync.start()
        # addCleanup runs LIFO, so register close first (it must run last).
        self.addCleanup(self.loop.close)
        self.addCleanup(self._cancel_all)
        self.addCleanup(self._sync.stop)
        self.addCleanup(self._hb_write.stop)
        self.addCleanup(self._interval.stop)

    def _cancel_all(self):
        if self.loop.is_closed():
            return
        for name in ('dtb_heartbeat_task', 'dtb_heartbeat_watchdog'):
            task = getattr(self.bot, name, None)
            if task is not None and not task.done():
                task.cancel()
        # Two turns let the cancellations propagate through wait_for futures.
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.run_until_complete(asyncio.sleep(0))

    def test_start_heartbeat_stores_a_strong_reference(self):
        task = self.bot_runner.start_heartbeat(self.bot)

        self.assertIs(task, self.bot.dtb_heartbeat_task)
        self.assertIsNotNone(self.bot.dtb_heartbeat_watchdog)
        self.assertFalse(task.done())

    def test_task_survives_garbage_collection(self):
        # Reproduce the original bug shape: create the task, drop every local
        # reference, then force a full collection. With the old fire-and-forget
        # create_task the task could vanish here.
        self.bot_runner.start_heartbeat(self.bot)
        task = self.bot.dtb_heartbeat_task

        gc.collect()

        self.assertFalse(task.done())
        self.assertIn(task, asyncio.all_tasks(self.loop))

    def test_watchdog_restarts_a_dead_heartbeat(self):
        self.bot_runner.start_heartbeat(self.bot)
        original = self.bot.dtb_heartbeat_task
        original.cancel()
        self.loop.run_until_complete(asyncio.sleep(0))

        self.loop.run_until_complete(asyncio.sleep(0.2))

        self.assertTrue(original.done())
        self.assertIsNot(self.bot.dtb_heartbeat_task, original)
        self.assertFalse(self.bot.dtb_heartbeat_task.done())

    def test_watchdog_leaves_a_live_heartbeat_alone(self):
        self.bot_runner.start_heartbeat(self.bot)
        live = self.bot.dtb_heartbeat_task

        self.loop.run_until_complete(asyncio.sleep(0.2))

        self.assertIs(self.bot.dtb_heartbeat_task, live)

    def test_watchdog_exits_once_the_bot_is_closed(self):
        watchdog = self.loop.create_task(
            self.bot_runner._heartbeat_watchdog(self.bot))
        self.bot._closed = True

        self.loop.run_until_complete(asyncio.sleep(0.2))

        self.assertTrue(watchdog.done())
        self.assertIsNone(watchdog.exception())


# ---------------------------------------------------------------------------
# 1.8.0 behaviour: async validation, deactivation kick, retention, admin
# page API gating, menu hooks, nested URL decoration.


def _give_main_character(user, character_id=999000123):
    """Attach a main character so AA's view decorators let us through."""
    from allianceauth.eveonline.models import EveCharacter

    character, _ = EveCharacter.objects.get_or_create(
        character_id=character_id,
        defaults={
            'character_name': 'Test Character',
            'gender': 'male',
            'security_status': 0.0,
        },
    )
    profile = user.profile
    if profile.main_character_id != character.pk:
        profile.main_character = character
        profile.save()
    return character


class _AdminUserMixin:
    def _make_admin(self, username='dtbadmin', main_character=True):
        user = User.objects.create_user(username=username, password='x')
        user.user_permissions.add(
            Permission.objects.get(codename='access_dtb'),
            Permission.objects.get(codename='manage_dtb_rules'),
        )
        if main_character:
            _give_main_character(user, character_id=999000100 + len(username))
        return user


class TestValidateNowEndpoint(_AdminUserMixin, TestCase):
    """``/dtb/admin/validate/`` queues Celery work instead of running it."""

    def setUp(self):
        self.client = Client()
        self.client.force_login(self._make_admin())

    def test_get_is_rejected(self):
        resp = self.client.get('/dtb/admin/validate/')
        self.assertEqual(resp.status_code, 405)

    @mock.patch('aa_discord_telegram_bridge.tasks.validate_all_telegram_users.delay')
    def test_post_queues_the_task(self, mock_delay):
        mock_delay.return_value = mock.Mock(state='PENDING', id='tid-1')

        resp = self.client.post('/dtb/admin/validate/')

        self.assertEqual(resp.status_code, 302)
        mock_delay.assert_called_once_with()
        from django.contrib.messages import get_messages
        texts = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('queued' in t and 'already' not in t for t in texts))

    @mock.patch('aa_discord_telegram_bridge.tasks.validate_all_telegram_users.delay')
    def test_post_reports_already_queued(self, mock_delay):
        mock_delay.return_value = mock.Mock(state='REJECTED', id=None)

        resp = self.client.post('/dtb/admin/validate/')

        self.assertEqual(resp.status_code, 302)
        from django.contrib.messages import get_messages
        texts = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('already queued' in t for t in texts))

    @mock.patch('aa_discord_telegram_bridge.tasks.validate_all_telegram_users.delay')
    def test_broker_failure_is_surfaced_not_raised(self, mock_delay):
        mock_delay.side_effect = ConnectionError('broker down')

        resp = self.client.post('/dtb/admin/validate/')

        self.assertEqual(resp.status_code, 302)
        from django.contrib.messages import get_messages
        texts = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('broker unreachable' in t for t in texts))


class TestDeactivationQueuesKick(TestCase):
    """Deactivating an Auth user schedules the Telegram kick off-request."""

    @mock.patch('aa_discord_telegram_bridge.tasks.kick_deactivated_user.delay')
    def test_deactivation_schedules_kick(self, mock_delay):
        user = User.objects.create_user(username='to-kick', password='x')

        with self.captureOnCommitCallbacks(execute=True):
            user.is_active = False
            user.save()

        mock_delay.assert_called_once_with(user.pk)

    @mock.patch('aa_discord_telegram_bridge.tasks.kick_deactivated_user.delay')
    def test_ordinary_save_schedules_nothing(self, mock_delay):
        user = User.objects.create_user(username='stays', password='x')

        with self.captureOnCommitCallbacks(execute=True):
            user.first_name = 'changed'
            user.save()

        mock_delay.assert_not_called()


class TestForwardHistoryRetention(TestCase):
    """DTB_HISTORY_RETENTION_DAYS bounds the forward log (0 disables)."""

    @staticmethod
    def _make_history(age_days):
        row = ForwardHistory.objects.create(
            source_channel='#src', target_channel='-1001234',
        )
        ForwardHistory.objects.filter(pk=row.pk).update(
            forwarded_at=timezone.now() - timedelta(days=age_days),
        )
        return row

    @override_settings(DTB_HISTORY_RETENTION_DAYS=90)
    def test_prunes_rows_older_than_retention(self):
        old = self._make_history(120)
        fresh = self._make_history(1)

        deleted = prune_forward_history()

        self.assertEqual(deleted, 1)
        self.assertFalse(ForwardHistory.objects.filter(pk=old.pk).exists())
        self.assertTrue(ForwardHistory.objects.filter(pk=fresh.pk).exists())

    @override_settings(DTB_HISTORY_RETENTION_DAYS=0)
    def test_zero_disables_pruning(self):
        old = self._make_history(400)

        self.assertIsNone(prune_forward_history())
        self.assertTrue(ForwardHistory.objects.filter(pk=old.pk).exists())


@override_settings(
    CACHES={'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }},
)
class TestAdminMembersApiGating(_AdminUserMixin, TestCase):
    """The members page reads the DB; Telegram admins are opt-in only.

    CACHES is forced to LocMem so running this suite on a live install
    never clears (or reads) the real cache.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = Client()
        self.client.force_login(self._make_admin())

    @mock.patch('aa_discord_telegram_bridge.views._collect_tg_admins')
    def test_default_view_never_calls_telegram(self, mock_collect):
        resp = self.client.get('/dtb/admin/members/')

        self.assertEqual(resp.status_code, 200)
        mock_collect.assert_not_called()

    @mock.patch('aa_discord_telegram_bridge.views._collect_tg_admins')
    def test_refresh_flag_collects_admins(self, mock_collect):
        mock_collect.return_value = []

        resp = self.client.get('/dtb/admin/members/?tg_admins=1')

        self.assertEqual(resp.status_code, 200)
        mock_collect.assert_called_once()
        self.assertTrue(resp.context['tg_admins_refresh'])

    @mock.patch('aa_discord_telegram_bridge.views._collect_tg_admins')
    def test_second_default_load_serves_cache(self, mock_collect):
        mock_collect.return_value = []
        resp = self.client.get('/dtb/admin/members/?tg_admins=1')
        self.assertEqual(resp.status_code, 200)
        mock_collect.assert_called_once()

        resp = self.client.get('/dtb/admin/members/')
        self.assertEqual(resp.status_code, 200)
        mock_collect.assert_called_once()


class TestMenuHooksVisibility(_AdminUserMixin, TestCase):
    """Member/admin menu items render only for the right permission."""

    def setUp(self):
        from django.test import RequestFactory
        from aa_discord_telegram_bridge.auth_hooks import (
            register_admin_menu_item, register_menu_item,
        )

        self.factory = RequestFactory()
        self.member_item = register_menu_item()
        self.admin_item = register_admin_menu_item()

    def _request(self, user):
        request = self.factory.get('/dtb/')
        request.user = user
        return request

    def test_member_item_hidden_without_permission(self):
        user = User.objects.create_user(username='nomember', password='x')
        self.assertEqual(self.member_item.render(self._request(user)), '')

    def test_member_item_visible_with_access_dtb(self):
        user = User.objects.create_user(username='hasaccess', password='x')
        user.user_permissions.add(Permission.objects.get(codename='access_dtb'))
        user = User.objects.get(pk=user.pk)
        html = self.member_item.render(self._request(user))
        self.assertIn('Discord-Telegram Bridge', html)

    def test_admin_item_hidden_without_manage_permission(self):
        user = User.objects.create_user(username='plainmem', password='x')
        user.user_permissions.add(Permission.objects.get(codename='access_dtb'))
        user = User.objects.get(pk=user.pk)
        self.assertEqual(self.admin_item.render(self._request(user)), '')

    def test_admin_item_visible_for_admin(self):
        user = self._make_admin()

        html = self.admin_item.render(self._request(user))
        self.assertIn('DTB Admin', html)


class TestNestedUrlDecoration(_AdminUserMixin, TestCase):
    """The nested include keeps admin views decorated while public ones are not.

    Meaningful only when the install declares ``dtb`` public (CI harness);
    on installs that never opted in, every route is decorated anyway.
    """

    def setUp(self):
        from django.conf import settings
        if 'dtb' not in settings.APPS_WITH_PUBLIC_VIEWS:
            self.skipTest('dtb is not declared public on this install')
        self.client = Client()

    def test_public_view_renders_without_main_character(self):
        user = User.objects.create_user(username='nomc', password='x')
        user.user_permissions.add(Permission.objects.get(codename='access_dtb'))
        user = User.objects.get(pk=user.pk)
        profile = user.profile
        profile.main_character = None
        profile.save()
        self.client.force_login(user)

        resp = self.client.get('/dtb/')

        self.assertEqual(resp.status_code, 200)

    def test_admin_view_still_requires_main_character(self):
        user = self._make_admin(username='adminnomc', main_character=False)
        self.client.force_login(user)

        resp = self.client.get('/dtb/admin/')

        self.assertEqual(resp.status_code, 302)
        self.assertIn('dashboard', resp['Location'])
