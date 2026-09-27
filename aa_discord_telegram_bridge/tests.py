"""Unit tests for aa_discord_telegram_bridge.

Run on the Alliance Auth server::

    python manage.py test aa_discord_telegram_bridge

Simple (DB-less) tests use ``SimpleTestCase``; anything touching the models
uses ``TestCase``. Network calls are mocked, so no real bot keys are needed.
"""

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
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
                'race': 'Amarr',
                'blood_type': 'Non-Vorpyre',
                'security_status': 0.0,
                'skill_level': 0,
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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .auth_hooks import DiscordTelegramBridgeService
        from .permissions import user_can_use_dtb

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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .auth_hooks import DiscordTelegramBridgeService
        from .permissions import user_can_use_dtb

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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .tasks import linked_profiles_without_access, validate_all_telegram_users

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
        from .auth_hooks import DiscordTelegramBridgeService

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
        from .tasks import validate_all_telegram_users

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
            from .telegram_handler import _process_plain_start
            _process_plain_start(999888777, '100', 'nobody', 'en')
        self.assertEqual(TelegramLinkRequest.objects.count(), 0)
        self.assertFalse(TelegramUser.objects.filter(telegram_user_id=999888777).exists())