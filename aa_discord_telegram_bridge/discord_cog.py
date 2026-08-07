import html
import logging
import re

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

    async def _send_to_telegram(self, rule, channel_name, message_text, message_id, author_name):
        """Send message to Telegram directly."""
        if not rule.matches_keywords(message_text):
            return

        text = (
            f'<b>[{html.escape(rule.name)}]</b>\n'
            f'\U0001f464 {html.escape(str(author_name))}\n\n'
            f'{html.escape(message_text)}'
        )

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
                await self._send_to_telegram(
                    rule, message.channel.name, message.content,
                    message.id, message.author.display_name,
                )
                for embed in message.embeds:
                    embed_text = self._embed_to_text(embed)
                    if embed_text:
                        await self._send_to_telegram(
                            rule, message.channel.name, embed_text,
                            message.id, message.author.display_name,
                        )

    def _embed_to_text(self, embed: discord.Embed) -> str:
        parts = []
        if embed.title:
            parts.append(f'<b>{embed.title}</b>')
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f'<b>{field.name}:</b> {field.value}')
        if embed.footer and embed.footer.text:
            parts.append(f'---\n{embed.footer.text}')
        return '\n'.join(parts)


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordForwarderCog(bot))
