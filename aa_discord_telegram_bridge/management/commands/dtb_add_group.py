from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Add a Telegram group to DTB manually by chat_id.'

    def add_arguments(self, parser):
        parser.add_argument('chat_id', type=str, help='Telegram chat ID (e.g. -1001234567890)')
        parser.add_argument('--name', type=str, default='', help='Group name (auto-detected if empty)')
        parser.add_argument('--type', type=str, default='supergroup', choices=['group', 'supergroup', 'channel'])

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.models import TelegramGroup

        chat_id = options['chat_id']
        name = options['name']
        chat_type = options['type']

        if not name:
            from aa_discord_telegram_bridge.manager import TelegramBotManager
            bot = TelegramBotManager()
            result = bot.get_chat(chat_id)
            if result.get('ok'):
                chat_info = result['result']
                name = chat_info.get('title') or chat_info.get('username', chat_id)
                chat_type = chat_info.get('type', chat_type)
                self.stdout.write(self.style.SUCCESS(f'  Fetched from Telegram: {name} ({chat_type})'))
            else:
                self.stdout.write(self.style.WARNING(f'  Could not fetch chat info: {result.get("description", "unknown")}'))
                name = chat_id

        group, created = TelegramGroup.objects.get_or_create(
            telegram_chat_id=chat_id,
            defaults={'name': name, 'chat_type': chat_type},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'  Created: {group}'))
        else:
            group.name = name
            group.chat_type = chat_type
            group.save()
            self.stdout.write(self.style.SUCCESS(f'  Updated: {group}'))
