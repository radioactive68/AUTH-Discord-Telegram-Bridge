import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_dtb_settings():
    """Get DTB settings from DB, fallback to Django settings."""
    try:
        from .models import DTBSettings
        return DTBSettings.load()
    except Exception:
        return None


class TelegramBotManager:
    """Manager for interacting with Telegram Bot API."""

    def __init__(self, bot_token: Optional[str] = None):
        if bot_token:
            self.bot_token = bot_token
        else:
            s = _get_dtb_settings()
            if s and s.telegram_bot_token:
                self.bot_token = s.telegram_bot_token
            else:
                self.bot_token = getattr(settings, 'DTB_TELEGRAM_BOT_TOKEN', '')
        self.api_base = f'https://api.telegram.org/bot{self.bot_token}'

    def _request(self, method: str, data: dict = None, timeout: int = 30) -> dict:
        """Make a request to Telegram Bot API."""
        url = f'{self.api_base}/{method}'
        try:
            resp = requests.post(url, json=data or {}, timeout=timeout)
            resp.raise_for_status()
            result = resp.json()
            if not result.get('ok'):
                logger.error('Telegram API error: %s', result)
                return {'ok': False, 'description': result.get('description', 'Unknown error')}
            return result
        except requests.RequestException as e:
            logger.error('Telegram API request failed: %s', e)
            return {'ok': False, 'description': str(e)}

    def get_me(self) -> dict:
        """Get bot info."""
        return self._request('getMe')

    @staticmethod
    def parse_target(target: str) -> dict:
        """Parse telegram target string.

        Supports formats:
          - 'chat_id'             → sends to chat root
          - 'chat_id:thread_id'   → sends to forum topic
        """
        parts = target.split(':')
        result = {'chat_id': parts[0].strip()}
        if len(parts) >= 2 and parts[1].strip().isdigit():
            result['message_thread_id'] = int(parts[1].strip())
        return result

    def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML',
                     message_thread_id: int = None) -> dict:
        """Send a message to a chat, optionally to a forum topic."""
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode,
        }
        if message_thread_id is not None:
            data['message_thread_id'] = message_thread_id
        return self._request('sendMessage', data)

    def kick_chat_member(self, chat_id: str, user_id: int) -> dict:
        """Kick a user from a chat (ban)."""
        return self._request('kickChatMember', {
            'chat_id': chat_id,
            'user_id': user_id,
        })

    def unban_chat_member(self, chat_id: str, user_id: int) -> dict:
        """Unban a user from a chat (allow rejoin)."""
        return self._request('unbanChatMember', {
            'chat_id': chat_id,
            'user_id': user_id,
            'only_if_banned': True,
        })

    def add_chat_member(self, chat_id: str, user_id: int) -> dict:
        """Invite a user to a chat (bot must be an admin with invite rights)."""
        return self._request('addChatMember', {
            'chat_id': chat_id,
            'user_id': user_id,
        })

    def get_updates(self, offset: int = None, timeout: int = 30) -> dict:
        """Receive updates via long polling (used when no webhook is configured)."""
        data = {'timeout': timeout}
        if offset is not None:
            data['offset'] = offset
        return self._request('getUpdates', data, timeout=timeout + 10)

    def get_chat_member(self, chat_id: str, user_id: int) -> dict:
        """Get info about a chat member."""
        return self._request('getChatMember', {
            'chat_id': chat_id,
            'user_id': user_id,
        })

    def get_chat(self, chat_id: str) -> dict:
        """Get chat info."""
        return self._request('getChat', {
            'chat_id': chat_id,
        })

    def approve_chat_join_request(self, chat_id: str, user_id: int) -> dict:
        """Approve a pending join request for a chat."""
        return self._request('approveChatJoinRequest', {
            'chat_id': chat_id,
            'user_id': user_id,
        })

    def decline_chat_join_request(self, chat_id: str, user_id: int) -> dict:
        """Decline a pending join request for a chat."""
        return self._request('declineChatJoinRequest', {
            'chat_id': chat_id,
            'user_id': user_id,
        })

    def get_chat_member_count(self, chat_id: str) -> dict:
        """Get chat member count."""
        return self._request('getChatMemberCount', {
            'chat_id': chat_id,
        })

    def set_webhook(self, url: str) -> dict:
        """Set webhook for receiving updates."""
        return self._request('setWebhook', {
            'url': url,
        })

    def delete_webhook(self) -> dict:
        """Remove webhook."""
        return self._request('deleteWebhook')

    def test_connection(self) -> tuple:
        """Test bot connection. Returns (is_ok, message)."""
        result = self.get_me()
        if result.get('ok'):
            bot_info = result.get('result', {})
            return True, f"Bot connected: @{bot_info.get('username', 'unknown')}"
        return False, f"Connection failed: {result.get('description', 'Unknown error')}"


class DiscordBotManager:
    """Manager for interacting with Discord Bot API via discord.py client.

    This is a helper; actual Discord interaction happens through the discord.py cog.
    """

    def __init__(self, bot_token: Optional[str] = None):
        if bot_token:
            self.bot_token = bot_token
        else:
            s = _get_dtb_settings()
            if s and s.discord_bot_token:
                self.bot_token = s.discord_bot_token
            else:
                self.bot_token = getattr(settings, 'DTB_DISCORD_BOT_TOKEN', '')
        self.api_base = 'https://discord.com/api/v10'

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bot {self.bot_token}',
            'Content-Type': 'application/json',
        }

    def _request(self, method: str, endpoint: str, data: dict = None) -> dict:
        url = f'{self.api_base}{endpoint}'
        try:
            if method == 'GET':
                resp = requests.get(url, headers=self._headers(), timeout=30)
            elif method == 'POST':
                resp = requests.post(url, headers=self._headers(), json=data or {}, timeout=30)
            else:
                return {'error': f'Unsupported method: {method}'}
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error('Discord API request failed: %s', e)
            return {'error': str(e)}

    def get_guild(self, guild_id: str) -> dict:
        return self._request('GET', f'/guilds/{guild_id}')

    def test_connection(self) -> tuple:
        """Test bot connection by fetching guild info."""
        s = _get_dtb_settings()
        guild_id = ''
        if s and s.discord_guild_id:
            guild_id = s.discord_guild_id
        else:
            guild_id = getattr(settings, 'DTB_DISCORD_GUILD_ID', '')
        if not guild_id:
            return False, 'Discord Guild ID not configured'
        result = self.get_guild(guild_id)
        if 'error' in result:
            return False, f"Connection failed: {result['error']}"
        return True, f"Connected to guild: {result.get('name', 'unknown')}"
