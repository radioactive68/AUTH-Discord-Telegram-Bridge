# Discord-Telegram Bridge for Alliance Auth

A plugin for [Alliance Auth](https://allianceauth.readthedocs.io/) (5.0+) that
forwards Discord messages/pings to Telegram groups or channels and manages
Telegram membership (auto-kick on alliance leave).

## Features

- **Message forwarding** — automatically forward messages from Discord channels
  to Telegram based on configurable rules.
- **Keyword filtering** — only forward messages that contain specific keywords.
- **Membership management** — automatically kick users from Telegram when they
  leave the alliance (or re-invite when they return).
- **User management** — a Telegram block on the `/services/` page (enable/disable).
- **Forward history** — a log of every forwarded message.
- **Connection check** — test Discord and Telegram bot connectivity.
- **Admin panel** — manage forwarding rules and groups.
- **Built-in bot** — the Discord forwarder bot can run as a management command
  or be auto-started inside Alliance Auth (no separate process required).
- **Setup wizard** — a guided first-time setup page (tokens, connection test,
  rules) so another server owner can get running in a few clicks.

## Requirements

- Python 3.9+
- Django 4.2+ (ships with Alliance Auth 5.0+)
- Alliance Auth 5.0+
- A Discord bot (created in the Discord Developer Portal)
- A Telegram bot (created via @BotFather)
- Celery with Redis/RabbitMQ (for periodic tasks)

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

### 2. Add to INSTALLED_APPS

In `myauth/settings/local.py`:

```python
INSTALLED_APPS = [
    # ... existing apps ...
    'aa_discord_telegram_bridge',
]
```

### 3. Run migrations

```bash
python manage.py migrate aa_discord_telegram_bridge
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
   - Click "Reset Token" to get the token.
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
4. Copy the received token (you'll enter it in the DTB settings form later).
5. **Important**: disable bot privacy (Bot Settings > Group Privacy > turn off).
6. Add the bot to the needed Telegram groups as an admin with:
   - Delete messages (for kick on alliance leave)
   - Send messages

### 7. Configure DTB

Open the DTB Settings page (`/dtb/admin/settings/`) and fill in:

- **Telegram Bot Token** — from @BotFather.
- **Discord Bot Token** — from the Discord Developer Portal.
- **Discord Guild ID** — your Discord server ID.
- **Alliance ID** (optional) — EVE Alliance ID to enforce membership. Leave
  empty to disable the membership check.
- **Auto-start bot** — enable to run the Discord forwarder inside Alliance Auth.

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
  dtb_bot`), or stop the process and run the command again.

## First-time setup (Setup Wizard)

After installation, open the **DTB Setup** page (link in the DTB menu or via
`/dtb/admin/setup/`). The wizard lets you:

1. Enter the Discord and Telegram bot tokens.
2. Test the bot connections.
3. Add your first forwarding rule.
4. Enable auto-start.

Once saved, forwarding works immediately.

## Permissions

| Permission | Description | Grant to |
|---|---|---|
| `dtb.access_dtb` | Shows the DTB block on `/services/` | All alliance members |
| `dtb.manage_dtb_rules` | Access to admin dashboard, rules, groups, settings | DTB admins (via Groups or States) |
| `dtb.view_forward_history` | View the forwarding history log | Optionally to directors+ |

## Usage

### Administrator

1. Log in to Auth as a superuser or a user with the `manage_dtb_rules` right.
2. Open the DTB admin page (`/dtb/admin/`).
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

## Plugin structure

```
aa_discord_telegram_bridge/
├── __init__.py
├── apps.py              # AppConfig (auto-start, periodic task registration)
├── models.py            # Django models
├── admin.py             # Django admin registration
├── views.py             # View functions
├── urls.py              # URL routes
├── forms.py             # Django forms
├── auth_hooks.py        # Alliance Auth service hook
├── tasks.py             # Celery tasks
├── signals.py           # Django signals (alliance membership check)
├── bot_runner.py        # Discord forwarder bot (run_bot / autostart)
├── manager.py           # Telegram/Discord API managers
├── discord_cog.py       # Discord.py cog for forwarding
├── telegram_handler.py  # Telegram webhook handling
├── permissions.py       # Custom permissions
├── management/commands/ # dtb_run_bot
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
2. Check the token in DTB Settings.
3. Make sure the bot is added to the group as an admin.

### Telegram kick does not work

1. The bot must be a group admin with the "Ban Users" right.
2. Check the bot token in DTB Settings.
3. Check that `telegram_user_id` is saved correctly on link.

## License

GPL-3.0 — see LICENSE
