import os
import subprocess
import sys

from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Update DTB plugin from GitHub and run migrations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--migrate', action='store_true', default=True,
            help='Run migrations after pull',
        )
        parser.add_argument(
            '--branch', default='main',
            help='Branch to pull from (default: main)',
        )

    def handle(self, *args, **options):
        from aa_discord_telegram_bridge.models import DTBSettings

        s = DTBSettings.load()
        repo = s.github_repo
        if not repo:
            self.stderr.write(self.style.ERROR(
                'GitHub repo not configured. Set it in DTB Settings admin page.'
            ))
            return

        plugin_dir = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ))
        branch = options['branch']

        self.stdout.write(f'Pulling from {repo} ({branch})...')
        r = subprocess.run(
            ['git', 'pull', 'origin', branch],
            cwd=plugin_dir, capture_output=True, text=True, timeout=60,
        )
        self.stdout.write(r.stdout)
        if r.stderr:
            self.stderr.write(r.stderr)
        if r.returncode != 0:
            self.stderr.write(self.style.ERROR('Git pull failed'))
            return

        if options['migrate']:
            self.stdout.write('Running migrations...')
            manage_py = os.path.join(settings.BASE_DIR, '..', 'manage.py')
            python_exe = sys.executable
            r2 = subprocess.run(
                [python_exe, manage_py, 'migrate', 'aa_discord_telegram_bridge', '--no-input'],
                capture_output=True, text=True, timeout=120,
            )
            self.stdout.write(r2.stdout)
            if r2.stderr:
                self.stderr.write(r2.stderr)

        from aa_discord_telegram_bridge.models import DTB_VERSION
        s.version = DTB_VERSION
        s.save()

        self.stdout.write(self.style.SUCCESS(f'Update complete. Version: {DTB_VERSION}'))
