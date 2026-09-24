"""Unit tests for aa_discord_telegram_bridge.

Run on the Alliance Auth server::

    python manage.py test aa_discord_telegram_bridge

Simple (DB-less) tests use ``SimpleTestCase``; anything touching the models
uses ``TestCase``. Network calls are mocked, so no real bot keys are needed.
"""

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from .manager import TelegramBotManager
from .models import (
    DTBSettings, ForwardRule, TelegramLinkRequest, TelegramUser,
)
from .tasks import iter_user_ownerships


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
        from .discord_cog import DiscordForwarderCog
        self.cog = DiscordForwarderCog(bot=object())
        self.rule = ForwardRule(name='ops')

    def test_plain_text_is_escaped(self):
        text = self.cog._build_telegram_text(
            self.rule, 'ops-chat', '2 < 5 && 3 > 1', [], 'Alice'
        )
        self.assertIn('2 &lt; 5 &amp;&amp; 3 &gt; 1', text)
        self.assertIn(self.cog._escape('Alice'), text)
        self.assertIn('<b>[', text)  # rule header
        self.assertIn(self.cog._escape('ops-chat'), text)

    def test_embed_bold_survives_but_content_is_escaped(self):
        from types import SimpleNamespace
        field = SimpleNamespace(name='Fleet <comms>', value='all caps')
        embed = SimpleNamespace(
            title='CTA <special>', description='undock & shoot', fields=[field],
            footer=SimpleNamespace(text='footer'),
        )
        text = self.cog._build_telegram_text(self.rule, 'ops', '', [embed], 'Bob')
        self.assertIn('<b>CTA &lt;special&gt;</b>', text)
        self.assertIn('undock &amp; shoot', text)
        self.assertIn('<b>Fleet &lt;comms&gt;:</b> all caps', text)
        self.assertNotIn('<script>', text)

    def test_truncation(self):
        long = 'A' * 4200
        text = self.cog._build_telegram_text(self.rule, 'ops', long, [], 'X')
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
        self.assertEqual(got, ['char'])

    def test_empty_user(self):
        self.assertEqual(list(iter_user_ownerships(object())), [])


class TestLinkingTokenFlow(TestCase):
    """The portal mints a token; the bot binds the owner's Telegram account."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='linker', password='x', is_superuser=False, is_staff=False,
        )
        self.user.user_permissions.add(
            Permission.objects.get(name='Can access Discord-Telegram Bridge')
        )
        perm = Permission.objects.get(codename='manage_dtb_rules')
        self.user.user_permissions.add(perm)
        self.user.is_active = True
        self.user.save()

        s = DTBSettings.load()
        s.alliance_id = 99000000
        s.telegram_bot_token = '123:fake'
        s.save()

        self.client = Client()
        self.client.force_login(self.user)

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

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_binds_user_with_valid_token(self, mock_invite):
        request = TelegramLinkRequest.objects.create(
            user=self.user,
            token='valid-token-abc',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from .telegram_handler import _process_linking_code
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
            from .telegram_handler import _process_linking_code
            ok = _process_linking_code(
                'stale-token-abc', '12345', 111222333, 'alice', 'en',
            )
        self.assertFalse(ok)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=111222333).exists())

    @mock.patch('aa_discord_telegram_bridge.telegram_handler._invite_to_groups')
    def test_bot_rejects_unknown_token(self, mock_invite):
        with mock.patch.object(TelegramBotManager, 'send_message', return_value={'ok': True}):
            from .telegram_handler import _process_linking_code
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
            from .telegram_handler import _process_linking_code
            ok1 = _process_linking_code('one-shot-token', '1', 111, 'a1', 'en')
            ok2 = _process_linking_code('one-shot-token', '2', 222, 'a2', 'en')
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(request.__class__.objects.filter(pk=request.pk).count(), 0)


class TestWebhookSecretAuth(TestCase):
    """The webhook endpoint requires the registered secret token."""

    def setUp(self):
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
            from .telegram_handler import _process_plain_start
            _process_plain_start(999888777, '100', 'nobody', 'en')
        self.assertEqual(TelegramLinkRequest.objects.count(), 0)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=999888777).exists())