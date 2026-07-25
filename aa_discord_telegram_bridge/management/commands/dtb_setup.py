from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Quick setup for DTB: create settings, groups, validate config, sync groups.'

    def add_arguments(self, parser):
        parser.add_argument('--alliance-id', type=int, help='EVE Alliance ID')
        parser.add_argument('--tg-token', type=str, help='Telegram Bot Token')
        parser.add_argument('--discord-token', type=str, help='Discord Bot Token')

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.models import DTBSettings, TelegramGroup

        self.stdout.write(self.style.MIGRATE_HEADING('DTB Setup'))

        settings = DTBSettings.load()

        if options['alliance_id']:
            settings.alliance_id = options['alliance_id']
            self.stdout.write(f'  Alliance ID: {options["alliance_id"]}')

        if options['tg_token']:
            settings.telegram_bot_token = options['tg_token']
            self.stdout.write('  Telegram token: set')

        if options['discord_token']:
            settings.discord_bot_token = options['discord_token']
            self.stdout.write('  Discord token: set')

        settings.save()
        self.stdout.write(self.style.SUCCESS('  Settings saved.'))

        self._setup_groups()

        self.stdout.write(self.style.MIGRATE_HEADING('\nSyncing Telegram groups...'))
        try:
            from aa_discord_telegram_bridge.manager import TelegramBotManager
            bot = TelegramBotManager()
            me = bot.get_me()
            if me.get('ok'):
                self.stdout.write(f'  Bot: @{me["result"]["username"]}')
                updates = bot.get_updates(timeout=1)
                if updates.get('ok'):
                    seen = set()
                    for u in updates.get('result', []):
                        chat = u.get('message', {}).get('chat') or u.get('my_chat_member', {}).get('chat')
                        if chat:
                            cid = str(chat['id'])
                            if cid not in seen:
                                seen.add(cid)
                                name = chat.get('title') or chat.get('first_name', cid)
                                obj, created = TelegramGroup.objects.get_or_create(
                                    telegram_chat_id=cid,
                                    defaults={'name': name, 'chat_type': chat.get('type', 'supergroup')},
                                )
                                status = 'created' if created else 'exists'
                                self.stdout.write(f'  Group: {name} ({cid}) [{status}]')
            else:
                self.stdout.write(self.style.WARNING(f'  Telegram API error: {me.get("description", "unknown")}'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Telegram sync failed: {e}'))

        self.stdout.write(self.style.MIGRATE_HEADING('\nValidating user memberships...'))
        from django.contrib.auth.models import User
        from aa_discord_telegram_bridge.tasks import _user_in_alliance
        members = User.objects.filter(is_active=True)
        in_alliance = sum(1 for u in members if _user_in_alliance(u))
        self.stdout.write(f'  {in_alliance}/{members.count()} active users in configured alliance')

        self.stdout.write(self.style.SUCCESS('\nDone! Visit https://<your-domain>/services/ to link Telegram.'))

    def _setup_groups(self):
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        from groupmanagement.models import AuthGroup

        self.stdout.write(self.style.MIGRATE_HEADING('\nSetting up AA Auth Group...'))

        from aa_discord_telegram_bridge.models import DTBSettings
        ct = ContentType.objects.get_for_model(DTBSettings)

        perm_manage = Permission.objects.filter(codename='manage_dtb_rules', content_type=ct).first()
        perm_access = Permission.objects.filter(codename='access_dtb', content_type=ct).first()
        perm_history = Permission.objects.filter(codename='view_forward_history', content_type=ct).first()

        if not perm_manage:
            self.stdout.write(self.style.WARNING('  Permissions not found. Run migrate first.'))
            return

        from django.contrib.auth.models import Group
        group, created = Group.objects.get_or_create(name='DTB Admins')
        if created:
            self.stdout.write(self.style.SUCCESS('  Created Group: DTB Admins'))
        else:
            self.stdout.write('  Group "DTB Admins" already exists')

        for perm in [perm_manage, perm_access, perm_history]:
            if perm and perm not in group.permissions.all():
                group.permissions.add(perm)
                self.stdout.write(f'  Added permission: {perm.codename}')

        auth_group, ag_created = AuthGroup.objects.get_or_create(
            group=group,
            defaults={
                'internal': False,
                'hidden': False,
                'open': False,
                'public': False,
                'restricted': False,
                'description': 'Manage Discord-Telegram Bridge rules and settings.',
            },
        )
        if ag_created:
            self.stdout.write(self.style.SUCCESS('  Created AuthGroup (requestable, requires approval)'))
        else:
            self.stdout.write('  AuthGroup already exists')
