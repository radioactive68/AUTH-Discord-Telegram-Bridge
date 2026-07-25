from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Sync Telegram groups from getUpdates and from user chat_ids.'

    def add_arguments(self, parser):
        parser.add_argument('--fetch-updates', action='store_true', help='Try to discover groups from getUpdates')

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.models import TelegramGroup, TelegramUser
        from aa_discord_telegram_bridge.manager import TelegramBotManager
        import logging

        found = 0

        if options['fetch_updates']:
            self.stdout.write(self.style.MIGRATE_HEADING('Fetching groups from getUpdates...'))
            bot = TelegramBotManager()
            try:
                resp = bot.get_updates(timeout=1)
                if resp.get('ok'):
                    seen = set()
                    for u in resp.get('result', []):
                        for key in ('message', 'my_chat_member', 'chat_join_request', 'channel_post'):
                            msg = u.get(key)
                            if not msg:
                                continue
                            chat = msg.get('chat')
                            if chat and chat.get('type') in ('group', 'supergroup', 'channel'):
                                cid = str(chat.get('id', ''))
                                if cid and cid not in seen:
                                    seen.add(cid)
                                    obj, created = TelegramGroup.objects.get_or_create(
                                        telegram_chat_id=cid,
                                        defaults={
                                            'name': chat.get('title') or chat.get('username', cid),
                                            'chat_type': chat.get('type', 'supergroup'),
                                        },
                                    )
                                    status = 'created' if created else 'exists'
                                    self.stdout.write(f'  {obj.name} ({cid}) [{status}]')
                                    found += 1
                else:
                    self.stdout.write(self.style.WARNING(f'  getUpdates failed: {resp.get("description", "unknown")}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error: {e}'))

        self.stdout.write(self.style.MIGRATE_HEADING('\nChecking linked user chats...'))
        for profile in TelegramUser.objects.filter(is_active=True, telegram_chat_id__isnull=False).exclude(telegram_chat_id=''):
            chat_id = profile.telegram_chat_id
            if TelegramGroup.objects.filter(telegram_chat_id=chat_id).exists():
                continue
            bot = TelegramBotManager()
            try:
                result = bot.get_chat(chat_id)
                if result.get('ok'):
                    chat_info = result['result']
                    chat_type = chat_info.get('type', 'supergroup')
                    if chat_type in ('group', 'supergroup', 'channel'):
                        name = chat_info.get('title') or chat_info.get('username', chat_id)
                        obj, created = TelegramGroup.objects.get_or_create(
                            telegram_chat_id=chat_id,
                            defaults={'name': name, 'chat_type': chat_type},
                        )
                        status = 'created' if created else 'exists'
                        self.stdout.write(f'  {name} ({chat_id}) [{status}]')
                        found += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  Could not fetch {chat_id}: {e}'))

        self.stdout.write(self.style.SUCCESS(f'\nTotal groups in DB: {TelegramGroup.objects.count()}'))
