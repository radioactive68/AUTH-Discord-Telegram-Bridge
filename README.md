# Discord-Telegram Bridge for Alliance Auth

A plugin for [Alliance Auth](https://allianceauth.readthedocs.io/) that forwards
Discord messages/pings to Telegram groups or channels and manages Telegram
membership (kick on alliance leave).

## Features

- **Message forwarding** — automatically forward messages from Discord channels
  to Telegram based on configurable rules.
- **Keyword filtering** — only forward messages that contain specific keywords.
- **Membership management** — automatically kick users from Telegram when they
  leave the alliance.
- **User management** — a Telegram block on the `/services/` page (enable/disable).
- **Forward history** — a log of every forwarded message.
- **Connection check** — test Discord and Telegram bot connectivity.
- **Admin panel** — manage forwarding rules and groups.
- **One-click update** — pull the latest code from GitHub and run migrations
  directly from the admin settings page.
- **Built-in bot** — the Discord forwarder bot can run as a management command
  or be auto-started inside Alliance Auth (no separate process required).
- **Setup wizard** — a guided first-time setup page (tokens, connection test,
  rules) so another server owner can get running in a few clicks.

## Requirements

- Python 3.9+
- Django 3.2+ (ships with Alliance Auth)
- Alliance Auth 3.0+
- A Discord bot (created in the Discord Developer Portal)
- A Telegram bot (created via @BotFather)
- Celery with Redis/RabbitMQ (for background tasks)

## Installation

### 1. Install the package

```bash
# Activate your Auth virtualenv
source /path/to/myauth/bin/activate

# From PyPI (recommended)
pip install aa-discord-telegram-bridge

# Or from source / git
pip install git+https://github.com/radioactive68/AUTH-Discord-Telegram-Bridge.git
```

### 2. Configure Django settings (`local.py`)

Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ... existing apps ...
    'aa_discord_telegram_bridge',
]
```

Add the following to the end of `local.py`:

```python
# ── Discord-Telegram Bridge Settings ──────────────────────

# Telegram Bot (create via @BotFather)
DTB_TELEGRAM_BOT_TOKEN = ''

# Discord Bot (create at https://discord.com/developers/applications)
DTB_DISCORD_BOT_TOKEN = ''
DTB_DISCORD_GUILD_ID = ''  # your Discord server ID

# Discord Bot Intents (Message Content Intent is required)
# Enable in Discord Developer Portal -> Bot -> Privileged Gateway Intents:
#   - MESSAGE CONTENT INTENT: ✅
#   - SERVER MEMBERS INTENT:  ✅

# Celery beat schedule (add to the existing one)
from celery.schedules import crontab

CELERYBEAT_SCHEDULE['dtb.test_connections'] = {
    'task': 'dtb.tasks.test_connections',
    'schedule': crontab(minute=0, hour='*/6'),  # every 6 hours
}

CELERYBEAT_SCHEDULE['dtb.validate_all_telegram_users'] = {
    'task': 'dtb.tasks.validate_all_telegram_users',
    'schedule': crontab(minute=30, hour='*/1'),  # every hour
}

CELERYBEAT_SCHEDULE['dtb.sync_telegram_groups'] = {
    'task': 'dtb.tasks.sync_telegram_groups',
    'schedule': crontab(minute=0, hour='*/12'),  # every 12 hours
}
```

### 3. Run migrations

```bash
python manage.py migrate dtb
python manage.py collectstatic --noinput
```

### 4. Restart services

```bash
# Bare Metal
supervisorctl restart myauth:
supervisorctl restart myauth_worker:

# Docker Compose
docker compose restart
```

### 5. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click "New Application" and give it a name (e.g. "Alliance Auth Bridge").
3. Open the **Bot** section:
   - Click "Reset Token" to get the token → copy it into `DTB_DISCORD_BOT_TOKEN`.
   - Enable **Message Content Intent** (Privileged Gateway Intents).
   - Enable **Server Members Intent**.
4. Open **OAuth2 > URL Generator**:
   - In scopes select `bot`.
   - In bot permissions select: Read Messages/View Channels, Send Messages,
     Read Message History.
   - Copy the generated URL and open it in a browser.
   - Invite the bot to your Discord server.
5. Enable **Developer Mode** in Discord (Settings > Advanced) to copy channel IDs.

### 6. Create the Telegram bot

1. Open Telegram and find [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Follow the instructions: give the bot a name and username.
4. Copy the received token → paste into `DTB_TELEGRAM_BOT_TOKEN`.
5. **Important**: disable bot privacy (Bot Settings > Group Privacy > turn off).
6. Add the bot to the needed Telegram groups as an admin with:
   - Delete messages (for kick on alliance leave)
   - Send messages

### 7. (Optional) Telegram webhook

To handle the `/start` and `/stop` commands in Telegram, configure a webhook:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -d "url=https://your-auth-domain.com/dtb/telegram/webhook/"
```

Or in `local.py`:

```python
DTB_TELEGRAM_WEBHOOK_URL = 'https://your-auth-domain.com/dtb/telegram/webhook/'
```

### 8. Running the Discord forwarder bot

The forwarder bot can run in two ways. Both are built in — there is **no
in-app restart button by design**; restarting is always done at the
process/server level so every installation stays consistent. A cross-process
file lock guarantees that only one bot instance ever runs, regardless of the
mode you choose.

**A. Auto-start inside Alliance Auth (recommended, no separate process)**

Open the DTB Settings admin page and enable **Auto-start the Discord forwarder
bot**, then restart the web server.

- **To restart:** restart the web server only (see step 4 above — `supervisorctl
  restart myauth:`, `docker compose restart`, or your systemd unit). The bot
  comes back up together with Alliance Auth.

**B. As a standalone management command**

```bash
python manage.py dtb_run_bot
```

Run this as a supervised service (supervisor / systemd / nssm).

- **To restart:** restart the supervised service (e.g. `supervisorctl restart
  dtb_bot`), or stop the process and run the command again. On Windows you can
  use a small `.bat` that kills the `dtb_run_bot` process and starts it anew.

## First-time setup (Setup Wizard)

After installation, open the **DTB Setup** page (link in the DTB menu or via
`/dtb/admin/setup/`). The wizard lets you:

1. Enter the Discord and Telegram bot tokens.
2. Test the bot connections.
3. Add your first forwarding rule.
4. Enable auto-start.

Once saved, forwarding works immediately.

## Usage

### Administrator

1. Log in to Auth as a superuser or a user with the `manage_dtb_rules` right.
2. Open the DTB page (menu link or `/services/`).
3. Configure **forwarding rules**:
   - Discord channel ID (right click > Copy ID)
   - Telegram chat ID or @username of the channel/group
   - A rule name (e.g. "ops", "CTA")
   - Optional keyword filter
4. Test the bot connections.
5. Add the bots to the relevant channels/groups.

### User

1. Open `/services/`.
2. Find the "Discord-Telegram Bridge" block.
3. Click "Link Account" and follow the instructions.
4. Toggle notifications on/off.

## Updating

Open the DTB Settings admin page:

- The page shows the **installed version** and the **latest version on GitHub**.
- If an update is available, a banner appears with an **Update Now** button.
- Use **Check for updates** to force a fresh check (bypasses CDN cache).
- Clicking **Update Now** runs `git pull`, migrations and `collectstatic`
  automatically.

To make a new version available, bump `DTB_VERSION` in
`aa_discord_telegram_bridge/models.py`, commit and push to the `main` branch.

## Plugin structure

```
aa_discord_telegram_bridge/
├── __init__.py
├── apps.py              # AppConfig (auto-start hook)
├── models.py            # Django models
├── admin.py             # Django admin registration
├── views.py             # View functions
├── urls.py              # URL routes
├── forms.py             # Django forms
├── auth_hooks.py        # Alliance Auth service hook
├── tasks.py             # Celery tasks
├── signals.py           # Django signals
├── bot_runner.py        # Discord forwarder bot (run_bot / autostart)
├── manager.py           # Telegram/Discord API managers
├── discord_cog.py       # Discord.py cog for forwarding
├── telegram_handler.py  # Telegram webhook handling
├── permissions.py       # Custom permissions
├── management/commands/ # dtb_run_bot, dtb_update
├── templatetags/
│   └── dtb_tags.py
├── templates/dtb/
└── migrations/
```

## Troubleshooting

### Bot does not forward messages

1. Check that `Message Content Intent` is enabled in the Discord Developer Portal.
2. Check that the forwarding rules are active (toggle on).
3. Check the logs: `journalctl -u myauth_worker -f`

### Telegram bot does not send messages

1. Make sure the user started a chat with the bot (pressed Start).
2. Check the token in settings.
3. Make sure the bot is added to the group as an admin.

### Telegram kick does not work

1. The bot must be a group admin with the "Ban Users" right.
2. Check that `DTB_TELEGRAM_BOT_TOKEN` is correct.
3. Check that `telegram_user_id` is saved correctly on link.

## License

GPL-3.0 — see LICENSE
