import inspect
import logging
from collections import deque

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands

from .models import ForwardRule, ForwardHistory
from .manager import TelegramBotManager

logger = logging.getLogger(__name__)


class DiscordForwarderCog(commands.Cog):
    """Discord cog that listens for messages and forwards them to Telegram."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._rules_cache = None
        self._rules_cache_time = 0
        # LRU-ish set of recently forwarded (channel_id, message_id)
        self._seen_messages = {}
        self._seen_message_keys = deque()

    def _load_rules(self):
        """Sync helper to load rules from DB."""
        return list(ForwardRule.objects.filter(is_enabled=True))

    async def _get_active_rules(self):
        """Get active rules, with caching."""
        import time
        now = time.time()
        if self._rules_cache is None or (now - self._rules_cache_time) > 60:
            self._rules_cache = await sync_to_async(self._load_rules)()
            self._rules_cache_time = now
        return self._rules_cache

    def _send_sync(self, chat_id, text, message_thread_id):
        """Sync helper to send message and create history record."""
        telegram_bot = TelegramBotManager()
        result = telegram_bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
        )
        return result

    def _create_history_sync(self, **kwargs):
        """Sync helper to create ForwardHistory record."""
        return ForwardHistory.objects.create(**kwargs)

    @staticmethod
    def _escape(value):
        from django.utils.html import escape
        return escape(str(value or ''))

    def _embed_to_text(self, embed: discord.Embed) -> str:
        """Render an embed with our own <b> tags around escaped text so the
        markup Telegram prints is safe and never comes from Discord content."""
        parts = []
        if embed.title:
            parts.append(f'<b>{self._escape(embed.title)}</b>')
        if embed.description:
            parts.append(self._escape(embed.description))
        for field in embed.fields:
            parts.append(f'<b>{self._escape(field.name)}:</b> {self._escape(field.value)}')
        if embed.footer and embed.footer.text:
            parts.append(f'---\n{self._escape(embed.footer.text)}')
        return '\n'.join(parts)

    @staticmethod
    def _truncate(text, limit=4000):
        if len(text) > limit:
            return text[:limit] + '\n…(truncated)'
        return text

    def _build_telegram_text(self, rule, channel_name, clean_content, embeds, author_name):
        """Compose the forwarded message: header + escaped text + embeds."""
        header = f"<b>[{self._escape(rule.name)}]</b>\n\U0001f464 {self._escape(channel_name)}"

        parts = []
        if clean_content:
            parts.append(self._escape(clean_content))
        for embed in embeds:
            embed_text = self._embed_to_text(embed)
            if embed_text:
                parts.append(embed_text)
        body = '\n\n'.join(parts)
        text = f'{header}\n\n{body}\n\n\U0001f464 {self._escape(author_name)}'
        return self._truncate(text)

    async def _send_to_telegram(self, rule, channel_name, message_text, message_id, author_name, embeds=None):
        """Send message to Telegram directly."""
        if not rule.matches_keywords(message_text):
            return

        text = self._build_telegram_text(rule, channel_name, message_text, embeds or [], author_name)

        target = TelegramBotManager.parse_target(rule.telegram_target)
        result = await sync_to_async(self._send_sync)(
            chat_id=target['chat_id'],
            text=text,
            message_thread_id=target.get('message_thread_id'),
        )

        await sync_to_async(self._create_history_sync)(
            rule=rule,
            source_channel=f'#{channel_name}',
            target_channel=rule.telegram_target,
            message_preview=message_text[:500],
            discord_message_id=str(message_id),
            success=result.get('ok', False),
            error_message=result.get('description', '') if not result.get('ok') else '',
        )

        if result.get('ok'):
            logger.info('Forwarded message to %s via rule %s', rule.telegram_target, rule.name)
        else:
            logger.error('Failed to forward to %s: %s', rule.telegram_target, result.get('description'))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages in configured Discord channels."""
        if not message.guild:
            return
        if message.author.id == self.bot.user.id:
            return

        channel_id = str(message.channel.id)
        rules = await self._get_active_rules()

        for rule in rules:
            if rule.discord_channel_id == channel_id:
                # Dedup: the same physical message may hit several rules
                # (or re-fire after a reconnect); forward it once.
                seen_key = (channel_id, str(message.id))
                if seen_key in self._seen_messages:
                    continue
                self._seen_messages[seen_key] = None
                self._seen_message_keys.append(seen_key)
                while len(self._seen_message_keys) > 2000:
                    self._seen_messages.pop(self._seen_message_keys.popleft(), None)

                if message.clean_content or message.embeds:
                    await self._send_to_telegram(
                        rule, message.channel.name,
                        message.clean_content or '',
                        message.id, message.author.display_name,
                        embeds=message.embeds,
                    )


async def setup(bot: commands.Bot):
    result = bot.add_cog(DiscordForwarderCog(bot))
    if inspect.isawaitable(result):
        await result
