# Discord-Telegram Bridge for Alliance Auth

A plugin for [Alliance Auth](https://allianceauth.readthedocs.io/) (5.0+) that
links user Telegram accounts to AA characters and manages Telegram group
membership based on EVE Online alliance membership. Optionally forwards Discord
messages to Telegram channels.

## Features

- **Telegram account linking** — users send `/start` to the bot, receive a
  verification code, and enter it on the `/services/` page to link their
  Telegram account to their AA character.
- **Alliance membership enforcement** — configurable `alliance_id` ensures only
  members of the specified EVE alliance can stay in Telegram groups. Non-members
  are automatically kicked.
- **Auto-invite** — linked users are automatically invited to configured
  Telegram groups with proper permission checks and invite-link fallback.
- **Auto-kick on unlink / alliance leave** — when a user unlinks their account,
  leaves the alliance, or their character is updated and no longer matches, they
  are kicked from all Telegram groups.
- **Discord → Telegram forwarding** (optional) — forward messages from Discord
  channels to Telegram based on configurable rules with keyword filtering.
- **Telegram-only mode** — works without discord.py or a Discord bot token. If
  only the Telegram token is configured, only Telegram features are activated.
- **Localized bot messages** — bot responses adapt to the user's AA language
  setting (EN, RU, DE, FR, ZH, JA, KO).
- **DTB Admins group** — auto-created Alliance Auth group with `manage_dtb_rules`,
  `access_dtb`, and `view_forward_history` permissions. Join requires approval;
  leave is automatic.
- **Members group** — auto-created group with `request_groups` permission so
  users can browse and join AA groups.
- **Setup wizard** — guided first-time setup page (`/dtb/admin/setup/`) for
  tokens, connection test, and rules.
- **Forward history** — a log of every forwarded Discord → Telegram message.
- **No in-app restart** — the bot starts automatically with the web server.
  Restarting is done at the process level (systemd / supervisor / docker).

## Requirements

- Python 3.9+
- Django 5.2+ (ships with Alliance Auth 5.0+)
- Alliance Auth 5.0+
- A Telegram bot (created via @BotFather)
- Celery with Redis/RabbitMQ (for periodic tasks)
- A Discord bot (only if using Discord → Telegram forwarding)

## Installation

### 1. Install the package

```bash
# Activate your Auth virtualenv
source /path/to/myauth/bin/activate

# From source / git (recommended)
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

### 3. Run migrations and setup

```bash
python manage.py migrate aa_discord_telegram_bridge
python manage.py collectstatic --noinput
python manage.py dtb_setup
```

`dtb_setup` creates:
- **DTB Admins** auth group with DTB management permissions.
- **Members** auth group with `request_groups` permission.
- Adds all alliance members to the Members group (if `alliance_id` is set).

### 4. Restart services

```bash
# Bare Metal
supervisorctl restart myauth:
supervisorctl restart myauth_worker:

# Docker Compose
docker compose restart
```

### 5. Create the Telegram bot

1. Open Telegram and find [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Follow the instructions: give the bot a name and username.
4. Copy the received token (you'll enter it in the DTB settings form later).
5. **Important**: disable bot privacy (Bot Settings > Group Privacy > turn off).
6. Add the bot to the needed Telegram groups as an admin with:
   - Delete messages (for kick on alliance leave)
   - Send messages
   - Invite users (for auto-invite to groups)

### 6. Create the Discord bot (optional)

Only needed if you want Discord → Telegram message forwarding.

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

### 7. Configure DTB

Open the DTB Settings page (`/dtb/admin/settings/`) and fill in:

- **Telegram Bot Token** — from @BotFather.
- **Alliance ID** — EVE Alliance ID to enforce membership (e.g. `99003995`).
  Leave empty to disable the membership check.
- **Discord Bot Token** — from the Discord Developer Portal (optional).
- **Discord Guild ID** — your Discord server ID (optional).
- **Auto-start bot** — enable to run the bot inside Alliance Auth.

## Updating

```bash
# Pull latest code (if installed from git)
cd /path/to/dtb
git pull

# Or reinstall from git
pip install --no-deps git+https://github.com/radioactive68/AUTH-Discord-Telegram-Bridge.git

# Run migrations
python manage.py migrate aa_discord_telegram_bridge

# Restart services
supervisorctl restart myauth:
```

Or use the built-in management command:

```bash
python manage.py dtb_update --repo radioactive68/AUTH-Discord-Telegram-Bridge
python manage.py migrate aa_discord_telegram_bridge
```

## Permissions

| Permission | Description | Grant to |
|---|---|---|
| `dtb.access_dtb` | Shows the DTB block on `/services/` | All alliance members |
| `dtb.manage_dtb_rules` | Access to admin dashboard, rules, groups, settings | DTB admins |
| `dtb.view_forward_history` | View the forwarding history log | Optionally to directors+ |

## User flow

1. User opens `/services/` in Alliance Auth.
2. Sees the "Discord-Telegram Bridge" block.
3. Clicks **Link Telegram** — sees a step-by-step instruction:
   - **Step 1**: Send `/start` to the bot in Telegram.
   - **Step 2**: Copy the verification code from the bot and click **Link Account**.
4. After linking, the user is automatically invited to configured Telegram groups.
5. Clicking **Unlink** removes the link and kicks the user from all groups.

## Admin flow

1. Log in as superuser or user with `manage_dtb_rules` permission.
2. Open `/dtb/admin/` — see bot status, validation, user list.
3. Configure **forwarding rules** (if using Discord forwarding):
   - Discord channel ID, Telegram target, optional keyword filter.
4. Manage **Telegram groups** — add/remove groups for auto-invite.
5. Manage **DTB Admins** group via `/groups/` — approve/reject join requests.

## Plugin structure

```
aa_discord_telegram_bridge/
├── __init__.py
├── apps.py              # AppConfig (auto-start, periodic task registration, group setup)
├── models.py            # Django models (DTBSettings, TelegramUser, ForwardRule, etc.)
├── admin.py             # Django admin registration
├── views.py             # View functions (services, linking, admin)
├── urls.py              # URL routes
├── forms.py             # Django forms
├── auth_hooks.py        # Alliance Auth service hook + URL hook + menu
├── tasks.py             # Celery tasks (validation, kick, alliance check)
├── signals.py           # Django signals (alliance membership, character update, group requests)
├── bot_runner.py        # Bot autostart (Telegram-only or Discord+Telegram)
├── manager.py           # Telegram/Discord API managers
├── discord_cog.py       # Discord.py cog for forwarding (optional)
├── telegram_handler.py  # Telegram bot handlers (/start, linking, join requests)
├── permissions.py       # Custom permissions
├── management/commands/ # dtb_setup, dtb_update, dtb_run_bot
├── templatetags/        # dtb_tags
├── templates/dtb/       # Service overview, admin pages
└── migrations/          # Database migrations
```

## Troubleshooting

### Bot does not start

1. Check that the Telegram bot token is set in DTB Settings.
2. Check logs: `journalctl -u aa_gunicorn | grep DTB`
3. If only Telegram is configured, discord.py is not required.

### Telegram bot does not respond to /start

1. Make sure bot privacy is disabled (Bot Settings > Group Privacy > off).
2. Check the token in DTB Settings.
3. Check that the bot is running: look for "Telegram polling started" in logs.

### Users cannot see the DTB block on /services/

1. Check that the user has the `dtb.access_dtb` permission (via DTB Admins or
   Members group).

### Auto-invite does not work

1. The bot must be an admin in the Telegram group with "Invite Users" permission.
2. Check `alliance_id` is set correctly in DTB Settings.
3. Verify the user's EVE character has the correct alliance in ESI data.

### Kick on alliance leave does not work

1. The bot must be a group admin with the "Ban Users" right.
2. Check the bot token in DTB Settings.
3. Check that `telegram_user_id` is saved correctly on link.

## License

GPL-3.0 — see LICENSE
