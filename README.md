# Discord-Telegram Bridge for Alliance Auth

A plugin for [Alliance Auth](https://allianceauth.readthedocs.io/) (5.0+) that
links user Telegram accounts to AA characters and manages Telegram group
membership based on EVE Online alliance membership. Optionally forwards Discord
messages to Telegram channels.

**Out-of-the-box** — install, configure tokens, and it works.

## Features

- **Telegram account linking** — one-time signed deep links. The user clicks
  **Generate link** on the portal, taps the resulting
  `t.me/<bot>?start=<token>` link and is auto-linked. Tokens are bound to the
  requesting Auth user and expire after a few minutes; a bare `/start` never
  creates a pending request, so nobody can bind someone else's Telegram account.
- **Permission-based access** — no EVE data lookups. Anyone holding
  `securegroups.access_sec_group` (granted by the alliance smart group of the
  Secure Groups app), `access_dtb` or `manage_dtb_rules` sees the service tile
  and may link Telegram; everyone else gets 403.
- **Auto-invite** — linked users receive Telegram group invitations via one-time
  invite links sent through DM. Periodic invite sync ensures users get invited
  to newly-added groups automatically.
- **Access reporting** — the 6-hourly task refreshes who still has access and
  logs the linked accounts that lost it; the dashboard and Members page flag
  them. Revoking Telegram access is a deliberate action (unlink / kick), so
  nothing is taken away silently. Deactivating a user in Auth still kicks them
  from the Telegram groups.
- **Discord → Telegram forwarding** (optional) — forward messages from Discord
  channels to Telegram based on configurable rules with keyword filtering.
  Supports forum topics via `chat_id:thread_id` format.
- **Telegram-only mode** — works without discord.py or a Discord bot token. If
  only the Telegram token is configured, only Telegram features are activated.
- **Localized bot messages** — bot responses adapt to the user's AA language
  setting (EN, RU, DE, FR, ZH, JA, KO).
- **Group auto-discovery** — groups are automatically registered when the bot
  sends a message to them, from ForwardRule targets on startup, and from
  incoming Telegram updates.
- **Separate systemd service** — the bot runs as its own `aa-dtb-bot.service`,
  independent of gunicorn. Auto-restarts on crash (`Restart=always`).
- **Stale lock detection** — lock files track PID; dead process locks are
  cleaned up automatically.
- **Forward history** — a log of every forwarded Discord → Telegram message.

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

# From git (recommended)
cd /path/to/auth_root
git clone https://github.com/radioactive68/AUTH-Discord-Telegram-Bridge.git dtb
pip install -e dtb --no-deps

# Dependencies (if not already installed)
pip install py-cord asgiref
```

> **Note**: `--no-deps` avoids pulling in `mysqlclient` which is only needed
> for MySQL backends. Install remaining deps manually if needed.

### 2. Add to INSTALLED_APPS

In `settings.py` (or `local.py`):

```python
INSTALLED_APPS = [
    # ... existing apps ...
    'aa_discord_telegram_bridge',
]
```

### 3. Run migrations and setup

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py dtb_setup
```

`dtb_setup` saves settings, syncs known Telegram groups, and reports how many
active users hold a DTB access permission.

> **Note**: DTB does not create or modify Alliance Auth groups. Grant the
> `aa_discord_telegram_bridge.manage_dtb_rules` permission to the users or
> groups who should manage DTB via your normal AA group management. Access for
> regular members comes from the Secure Groups smart group permission
> (`securegroups.access_sec_group`) — nothing else to configure.

Optional arguments:

```bash
# Set everything in one command
python manage.py dtb_setup --tg-token "YOUR_TOKEN" --discord-token "YOUR_DISCORD_TOKEN"

# Or configure later in the admin panel
python manage.py dtb_setup
```

### 4. Set up the DTB bot systemd service

The bot runs as a separate systemd service, independent of gunicorn.
Create `/etc/systemd/system/aa-dtb-bot.service`:

```ini
[Unit]
Description=Alliance Auth DTB Bot (Discord-Telegram Bridge)
After=network.target aa-gunicorn.service
Requires=network.target

[Service]
Type=simple
# Use the same user that owns the Auth project (NOT root); the venv must be
# readable by it. Example: User=allianceserver
User=allianceserver
WorkingDirectory=/path/to/myproject
Environment=DJANGO_SETTINGS_MODULE=myproject.settings
ExecStart=/path/to/venv/bin/python manage.py dtb_run_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
systemctl daemon-reload
systemctl enable aa-dtb-bot
systemctl start aa-dtb-bot
```

### 5. Receiving Telegram updates: webhook vs long polling

By default the bot long-polls Telegram for updates, which works on any box.
Optionally, set the **Telegram webhook URL** (`/dtb/telegram/webhook/`) in the
Django admin under *DTB Settings*. When the URL is configured (or changed,
or removed), DTB automatically registers it with Telegram via `setWebhook`,
generates a random `secret_token` and stores it in settings; every incoming
webhook call is then verified against that token, so only real Telegram
requests are processed. Webhook mode takes precedence over polling.

### 6. Restart services

```bash
# systemd
systemctl restart aa-gunicorn aa-celery aa-celerybeat
# Bot is a separate service — starts with aa-dtb-bot
```

### 7. Create the Telegram bot

1. Open Telegram and find [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Follow the instructions: give the bot a name and username.
4. Copy the received token (you'll enter it in the DTB settings form later).
5. **Important**: disable bot privacy (Bot Settings > Group Privacy > turn off).
6. Add the bot to the needed Telegram groups as an admin with:
   - Delete messages (for kicking users on revoke/unlink)
   - Send messages
   - Invite users (for auto-invite to groups)

### 7. Create the Discord bot (optional)

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

### 8. Configure DTB

Open the DTB Settings page (`/admin/` → DTB Settings) and fill in:

- **Telegram Bot Token** — from @BotFather.
- **Alliance ID** — EVE Alliance ID to enforce membership (e.g. `99003995`).
  Leave empty to disable the membership check.
- **Discord Bot Token** — from the Discord Developer Portal (optional).
- **Discord Guild ID** — your Discord server ID (optional).

After saving, start the bot:

```bash
systemctl start aa-dtb-bot
```

Check that it's running:

```bash
systemctl status aa-dtb-bot
journalctl -u aa-dtb-bot -f
```

## Updating

```bash
# Pull latest code
cd /path/to/dtb
git pull

# Run migrations
python manage.py migrate aa_discord_telegram_bridge

# Restart all services
systemctl restart aa-gunicorn aa-celery aa-celerybeat aa-dtb-bot
```

## Management Commands

| Command | Description |
|---|---|
| `dtb_setup` | First-time setup: set tokens, sync groups, report access state |
| `dtb_setup --tg-token Y --discord-token Z` | Setup with inline config |
| `dtb_add_group <chat_id>` | Manually add a Telegram group by chat_id |
| `dtb_add_group <chat_id> --name "Name"` | Add with custom name |
| `dtb_sync_groups --fetch-updates` | Discover groups from getUpdates, linked users, and ForwardRule targets |
| `dtb_run_bot` | Run the bot manually (for debugging) |

## Permissions

| Permission | Description | Grant to |
|---|---|---|
| `securegroups.access_sec_group` | *External app (Secure Groups).* "Can access sec group requests screen." — held by every member of the alliance smart group | Already granted by the Secure Groups app |
| `aa_discord_telegram_bridge.access_dtb` | **Basic access** — alternative way to grant DTB access outside the smart group | Group/state with the members who may use the bridge |
| `aa_discord_telegram_bridge.manage_dtb_rules` | Access to admin dashboard, rules, groups, settings | DTB admins |
| `aa_discord_telegram_bridge.view_forward_history` | View the forwarding history log | Optionally to directors+ |

> **How access works:** pure permission check, no EVE data and no
> `alliance_id`. Alliance Auth does not enforce a service's `access_perm` for
> you — it only *hands* the permission state to the service hook, so DTB
> checks it itself in `tasks._user_can_use_dtb()`: the user needs
> `securegroups.access_sec_group` **or** `access_dtb` **or**
> `manage_dtb_rules`. That single check drives the tile on `/services/`, the
> DTB pages, linking/unlinking and the Telegram join-request approval. Django
> superusers pass implicitly (they hold every permission).
>
> Losing the permission does **not** silently unlink anybody: the 6-hourly task
> and the dashboard only report it. Revoke access deliberately via the user's
> DTB page (Unlink) or the Members page (kick).

## User flow

1. User opens `/services/` in Alliance Auth.
2. Sees the "Discord-Telegram Bridge" block.
3. Clicks **Generate link** — the portal mints a one-time signed token
   (valid ~15 minutes, bound to the Auth user) and shows a deep link
   `t.me/<bot>?start=<token>` plus a manual fallback command.
4. User taps the link (or types `/start <token>` in the bot) — the bot binds
   the Telegram account *of the person who tapped it* to the requesting Auth
   user, sends a confirmation, then invite links to all tracked Telegram groups.
5. Clicking **Unlink** (or sending `/stop` to the bot) removes the link
   and kicks the user from all groups.

A bare `/start` (no token) only confirms an already linked account or points
the user back to the portal; it never stores a pending link request.

## Admin flow

1. Log in as superuser or user with `manage_dtb_rules` permission.
2. Open `/dtb/admin/` — see bot status, validation, user list.
3. Configure **forwarding rules** (if using Discord forwarding):
   - Discord channel ID, Telegram target (supports `chat_id:thread_id` for forum topics),
     optional keyword filter.
4. Manage **Telegram groups** — toggle auto-invite per group.
5. Grant the `aa_discord_telegram_bridge.manage_dtb_rules` permission to admins
   via your normal AA group management.

## Plugin structure

```
aa_discord_telegram_bridge/
├── __init__.py
├── apps.py              # AppConfig (post_migrate periodic tasks)
├── models.py            # Django models (DTBSettings, TelegramUser, ForwardRule, etc.)
├── admin.py             # Django admin registration
├── views.py             # View functions (services, linking, admin)
├── urls.py              # URL routes
├── forms.py             # Django forms
├── auth_hooks.py        # Alliance Auth service hook + URL hook + menu
├── tasks.py             # Celery tasks (validation report, kick, access check)
├── signals.py           # Django signals (deactivated users)
├── bot_runner.py        # Bot lifecycle, periodic token check, stale lock detection
├── discord_cog.py       # Discord forwarding cog (async-safe, embed support, dedup, keyword filter)
├── manager.py           # Telegram/Discord API managers with auto-group registration
├── telegram_handler.py  # Telegram bot handlers (/start, linking, join requests, group sync)
├── permissions.py       # Custom permissions
├── management/commands/ # dtb_setup, dtb_add_group, dtb_sync_groups, dtb_run_bot
├── templatetags/        # dtb_tags
├── templates/dtb/       # Service overview, admin pages
└── migrations/          # Database migrations
```

## Troubleshooting

### Bot does not start

1. Check that tokens are set in DTB Settings (`/admin/` → DTB Settings).
2. Check bot service: `systemctl status aa-dtb-bot`
3. Check logs: `journalctl -u aa-dtb-bot -f`
4. If tokens are empty, the bot waits and retries every 60 seconds.
5. Stale lock files are auto-cleaned if the previous process died.

### Telegram bot does not respond to /start

1. Make sure bot privacy is disabled (Bot Settings > Group Privacy > off).
2. Check the token in DTB Settings.
3. Check that the bot is running: `systemctl status aa-dtb-bot`

### Users cannot see the DTB block on /services/

Access is permission-only — check, in this order:

1. `securegroups.access_sec_group` (the Secure Groups smart group permission):
   is the user in the alliance smart group on the portal? Secure Groups syncs
   it, so a member has it after the next sync.
2. `aa_discord_telegram_bridge.access_dtb`: granted directly or via a
   group/state, as an alternative to (1).
3. `aa_discord_telegram_bridge.manage_dtb_rules`: DTB admins (Django
   superusers pass implicitly).

The same check guards `/dtb/` (403) and the link endpoint.

### Auto-invite does not send links

1. The bot must be an admin in the Telegram group with "Invite Users" permission.
2. The user must hold an access permission (see above) and have a linked
   Telegram account.
3. The user must have sent `/start` to the bot in Telegram.
4. Periodic invite sync runs every ~60 minutes for linked users.

### Revoking access from Telegram groups

Nothing is kicked automatically any more (except when a user is deactivated in
Auth). To remove someone from the Telegram groups, unlink their account from
their DTB page or kick them from the *Members* page in the DTB admin. The
dashboard shows how many linked accounts currently have no access permission.

### Telegram groups not appearing in admin

Groups are auto-registered when:
- The bot sends a message to them (forwarding rule fires).
- They are listed as ForwardRule targets (registered on bot startup).
- A user sends a message in the group.
- You add them manually: `python manage.py dtb_add_group <chat_id>`

### Discord forwarding does not work

1. Ensure `py-cord` is installed: `pip install py-cord` (or that
   `allianceauth-discordbot` is installed — it provides the same `discord`
   package).
2. Check the Discord bot token in DTB Settings.
3. Ensure the Discord bot is invited to your server with "Send Messages" and
   "Read Message History" permissions.
4. Check bot logs for `SynchronousOnlyOperation` errors — this means the code
   is outdated; update from GitHub.
5. Verify ForwardRules: Discord channel IDs must match exactly (use Developer Mode
   to copy IDs). Telegram targets use `chat_id:thread_id` for forum topics.

## License

GPL-3.0 — see LICENSE
