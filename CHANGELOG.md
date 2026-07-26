# Changelog

## 1.3.0
- Remove `autostart_bot` field from DTBSettings (migration 0016). Bot is now managed exclusively via systemd (`aa-dtb-bot.service`).
- Remove dead code: `_try_start_bot`, `_periodic_bot_check`, `maybe_start_bot` from bot_runner.py.
- Simplify `dtb_run_bot` management command — no more autostart checks, just token wait + auto-restart loop.
- Fix relative import in dtb_run_bot management command (`from .models` → absolute import).

## 1.2.8
- Move bot to dedicated systemd service (`aa-dtb-bot.service`) — no longer runs as a daemon thread inside gunicorn workers.
- Remove `threading.Timer` from `DtbConfig.ready()` (apps.py). Gunicorn workers are clean now.
- Rewrite `dtb_run_bot` management command: `while True` loop with auto-restart on crash, token wait logic.
- Fix `discord_cog.py` async: wrap all ORM calls in `sync_to_async` / `asyncio.to_thread` (fix `SynchronousOnlyOperation` in `on_message`).
- Add explicit `fields` tuple to `DTBSettingsAdmin` (fix Django admin form for singleton model with password fields).
- Update README with systemd service setup instructions, journalctl, troubleshooting.

## 1.2.7
- Community audit fixes:
  - Add `verify_link` URL to urls.py, form posts to `dtb:verify_link`.
  - Replace deprecated `kickChatMember` with `banChatMember`.
  - Escape all user content via `html.escape()` in Telegram messages (XSS prevention).
  - Remove self-update (`dtb_update` management command, update button, check_update endpoint).
  - Remove dead Celery tasks (`forward_message`, `sync_telegram_groups`).

## 1.2.6
- Migrate all models from `AutoField` to `BigAutoField` (migration 0015).
- Defer DB queries where possible.

## 1.2.5
- Auto-register Telegram groups on `send_message`, from ForwardRule targets at bot startup, and via `_record_chat`.
- Auto-invite sync in heartbeat (periodic invite to all tracked groups).

## 1.2.3
- Add `dtb_add_group` management command (add group by chat_id or link).
- Add `dtb_sync_groups` management command (sync known groups from bot).

## 1.2.0
- `autostart_bot` default changed to `True`.
- Stale lock detection: lock file stores PID; if process is dead, lock is auto-removed.
- Add migration 0014 for autostart_bot.

## 1.1.x
- Clean install flow: `dtb_setup` management command with `--alliance-id`, `--tg-token`, `--discord-token`.
- Heartbeat task: periodic health checks.
- `auto_invite` field on `TelegramGroup` — per-group toggle, default `True`.
- Private chats hidden from Manage Telegram Groups page.
- Auto-leave via raw SQL in `on_dtb_group_request` signal.
- Sync join approval: non-alliance users rejected, auto-approved users leave, alliance users pending for admin approval.
- `has_ownership` check: `user.character_ownerships.filter(character__alliance_id__isnull=False).exists()`.

## 1.0.5
- Bot autostart via `threading.Timer` in `DtbConfig.ready()` (now removed in 1.2.8).
- `url_hook` registered — DTB pages accessible at `/dtb/`.
- `services_hook.access_perm = 'aa_discord_telegram_bridge.access_dtb'`.
- Telegram-only mode: `run_telegram_only()` if no Discord token.
- Members group auto-creation via `post_migrate`.
- Auto-leave on user state change: kick from all Telegram groups.

## 1.0.4
- Fix dashboard crash when DTB URL namespace is not registered: DTBMenu.render() now catches NoReverseMatch gracefully.
- Add i18n (gettext_lazy) to all models, forms, views, auth_hooks, and templates for translation support.
- Permission prefix corrected from `dtb.*` to `aa_discord_telegram_bridge.*` (matching Django's auto-label from AppConfig.name).

## 1.0.3
- Remove self-update functionality (GitHub update button, check_update endpoint, dtb_update management command).
- Remove `github_repo` and `version` fields from DTBSettings model.
- Service block on /services/ visible to all users; admin tools remain permission-gated.

## 1.0.2
- Migrate periodic task registration from CELERYBEAT_SCHEDULE hack to django_celery_beat.PeriodicTask (AA 5.x compatible).
- Service block on /services/ visible to all users; admin tools remain permission-gated.

## 1.0.1
- Fix `is_active` getting stuck `False` and never recovering after a user leaves/returns to the alliance.
- `_user_in_alliance` now treats Alliance Auth staff/superusers as authorized.
- `validate_all_telegram_users` and the `on_character_update` signal re-activate access (and re-invite to groups) when a user returns to good standing.
- Removed the stale "send /start to keep access" text from the overview page.

## 1.0.0
- Initial release of the Discord-Telegram Bridge (DTB) for Alliance Auth.
- Forwards Discord pings/CTAs to Telegram channels with automatic Telegram group membership enforcement (auto-invite on link, auto-kick on leaving the alliance).
- Code-free Telegram linking via the bot's /start, admin-only group invites, and a Telegram polling listener (no public webhook required).
- In-server bot mode with a single-instance lock; optionally auto-started inside Alliance Auth.
- Admin dashboard, per-rule forwarding, keyword filters, connection tests, and update checks from GitHub.
