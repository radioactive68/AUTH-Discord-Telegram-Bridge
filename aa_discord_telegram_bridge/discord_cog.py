import logging
import re
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myauth.settings.local')
django.setup()

import discord
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

    def _get_active_rules(self):
        """Get active rules, with caching."""
        import time
        now = time.time()
        if self._rules_cache is None or (now - self._rules_cache_time) > 60:
            self._rules_cache = list(ForwardRule.objects.filter(is_enabled=True))
            self._rules_cache_time = now
        return self._rules_cache

    def _send_to_telegram(self, rule, channel_name, message_text, message_id, author_name):
        """Send message to Telegram directly (without Celery)."""
        if not rule.matches_keywords(message_text):
            return

        telegram_bot = TelegramBotManager()
        text = (
            f'<b>[{rule.name}]</b>\n'
            f'👤 {author_name}\n\n'
            f'{message_text}'
        )

        target = TelegramBotManager.parse_target(rule.telegram_target)
        result = telegram_bot.send_message(
            chat_id=target['chat_id'],
            text=text,
            message_thread_id=target.get('message_thread_id'),
        )

        ForwardHistory.objects.create(
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
        if message.author.bot:
            return
        if not message.guild:
            return

        channel_id = str(message.channel.id)
        rules = self._get_active_rules()

        for rule in rules:
            if rule.discord_channel_id == channel_id:
                self._send_to_telegram(
                    rule, message.channel.name, message.content,
                    message.id, message.author.display_name,
                )
                for embed in message.embeds:
                    embed_text = self._embed_to_text(embed)
                    if embed_text:
                        self._send_to_telegram(
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
