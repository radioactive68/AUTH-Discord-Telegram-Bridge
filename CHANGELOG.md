# Changelog

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
