# Changelog

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
