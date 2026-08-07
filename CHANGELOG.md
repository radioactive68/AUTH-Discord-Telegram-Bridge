# Changelog

## 1.4.7
- Link Telegram by numeric user ID: users without a @username can register by
  entering the numeric ID the bot shows in the /start reply. The pending link
  request no longer expires after 15 minutes.
- Removed the broken verification-code fallback (sendMessage to a @username)
  and replaced it with a clear "press /start first" message.
- Services pages now show users who are linked without a username (numeric ID
  is displayed instead).

## 1.4.6
- Settings page "Back" button now returns to the DTB admin dashboard
  (`/dtb/admin/`) instead of the rules list.

## 1.4.5
- Removed the leftover "Auto-start bot" row from the setup wizard status list
  (the `autostart_bot` field was removed in 1.3.0; the bot is managed via
  `systemctl`, not from the portal).

## 1.4.4
- Human-readable Discord mentions in forwarded messages: `<@id>`, `<@&id>`,
  `<#id>` are resolved to nicknames/role/channel names (`clean_content`).
- New users without a Telegram @username get a clear step-by-step /start reply
  telling them to create a username in Telegram Settings first.
- Services page: username field placeholder is now "Telegram @username" and the
  Link Account button sits right next to the field (input-group), same as on
  the DTB page.

## 1.4.3
- Ship translation catalogs (`locale/**`) in the installed package — previously
  `pip install` dropped them, so bot messages always fell back to English.

## 1.4.2
- Combine a Discord message's text and embeds into a single Telegram message
  (previously content and each embed were sent as separate messages).

## 1.4.1
- Forward Discord messages from other bots too (removed the blanket bot-message
  filter). Still drops the plugin's own messages to prevent loops.

## 1.4.0
- Kicking a user from Telegram groups now also auto-unlinks their Telegram from
  the portal, so the periodic validation stops re-notifying/re-kicking on every cycle.
- Removed auto-creation of the "DTB Admins" and "Members" auth groups — the
  plugin no longer modifies Alliance Auth groups. Permissions are granted via
  normal AA group management (`aa_discord_telegram_bridge.manage_dtb_rules`).

## 1.3.0
- Bot moved to dedicated systemd service (`aa-dtb-bot.service`). Removed gunicorn thread approach entirely.
- Removed `autostart_bot` field — bot is managed via `systemctl`.
- Fixed async ORM calls in Discord cog (`sync_to_async`).
- Clean install with AA 5.2.0 from GitHub verified.

## 1.2.0
- Clean install flow via `dtb_setup` management command.
- Telegram group management: auto-register, auto-invite, per-group `auto_invite` toggle.
- Management commands: `dtb_add_group`, `dtb_sync_groups`.
- Stale lock detection, heartbeat with periodic invite sync.
- Alliance membership enforcement: auto-leave, join approval, `has_ownership` check.
- BigAutoField migration, community audit fixes (XSS escape, deprecated API removal).

## 1.1.0
- AA 5.x compatibility: django-celery-beat, services hook, URL hook, menu hook.
- i18n support (gettext_lazy) for all user-facing strings.
- Telegram-only mode (no Discord token required).
- Auto-leave on alliance membership change.

## 1.0.0
- Initial release.
- Discord → Telegram message forwarding with per-rule configuration.
- Telegram bot linking via /start, verification codes.
- Auto-invite to tracked groups, auto-kick on leaving alliance.
- Admin dashboard, forwarding rules, keyword filters.
- Services page integration with link/unlink controls.
