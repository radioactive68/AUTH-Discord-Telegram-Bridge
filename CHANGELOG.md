# Changelog

## 1.7.4
- **Access follows Alliance Auth again — state grants work on state-only
  installs.** 1.7.2/1.7.3 stopped using `user.has_perm()` and read
  `user.user_permissions` / groups / state grant tables directly. On a
  state-only AA 5 install that was wrong: the permission is resolved by AA's
  `StateBackend` and never appears in those tables, so members who *were*
  granted `access_dtb` (e.g. on the `Member` state) lost the service tile and
  got 403 on `/dtb/`. The gate asks `has_perm()` again, which is the canonical
  question, and defers to whichever backends AA has configured.
- The 1.7.2 "no implicit access" rule is narrowed to what actually matters:
  `access_dtb` is still the **only** permission that opens the service —
  `manage_dtb_rules` opens the admin pages and nothing else. An active Django
  superuser keeps implicit access, deliberately: the site owner administers DTB,
  and the periodic validation would otherwise kick their own linked Telegram
  account out of the bridge. Inactive users have no access and are revoked.
- Verified on the live portal: `Null Object InSpace` (state `Member`) regains
  the tile, and a user without the grant still has none.

## 1.7.3
- **State grants are read directly — access no longer depends on AA syncing
  states, and works on installs without Auth groups.** On a state-only AA
  install the permission lives in the state grant, and AA only copies it onto
  `user.user_permissions` when it syncs states, so `access_dtb` handed out via
  *Change state → Member* did not show up. All permission checks now go through
  one resolver, `permissions.user_holds_perm()`, which looks at
  1. the user's own permissions,
  2. the permissions of the user's groups,
  3. the permissions carried by an AA state granted to the user — both the
     `StateMembership` grant record and the state definition (so a permission
     added to the state after the grant counts too).
  The field names are read from AA's models instead of being hardcoded, and a
  missing/renamed field degrades to the other sources instead of erroring.
- The same resolver now guards the admin pages: `@permission_required` /
  `has_perm()` are replaced by `dtb_admin_required` (`manage_dtb_rules`) and
  `permissions.user_can_view_history()`, so a DTB admin granted the permission
  through a state is no longer locked out of `/dtb/admin/…`.
- `dtb_setup` and the Members page read access through the same resolver, so
  the report can no longer disagree with the gate.
- Tests added: `access_dtb` on a state grants the service, `manage_dtb_rules` on
  a state membership opens the admin pages (and still does not open the service).

## 1.7.2
- **No implicit access any more — the permission has to be granted explicitly.**
  The service was still visible to users who were never granted `access_dtb`,
  because the gate was built on `user.has_perm()`: Django returns `True` for
  *every* permission when the user is a Django superuser, and `manage_dtb_rules`
  (admin rights) was accepted as access too. DTB now resolves its own
  permission from the actual grants (`_user_holds_perm()` — direct user
  permissions or group permissions, like the groups an AA admin uses for
  member / FC / leadership), so:
  - a superuser without `access_dtb` no longer sees the tile and gets 403 on
    `/dtb/`;
  - `manage_dtb_rules` opens the admin pages only, not the user-facing service;
  - `validate_all_telegram_users` revokes (kicks + unlinks) superusers and DTB
    admins whose `access_dtb` is gone, exactly like any other user.
  Grant `access_dtb` to your own group/state (and to your own account if you
  want to use the bridge yourself).
- Tests added: superuser without the permission sees no service, DTB admin
  rights alone do not grant it, a group holding the permission does.

## 1.7.1
- **DTB checks only its own permissions again**: `1.7.0` also accepted
  `securegroups.access_sec_group` (permission of another app) as an access
  signal. That was wrong — every app owns its permissions, so DTB now gates on
  `aa_discord_telegram_bridge.access_dtb` (or `manage_dtb_rules` for DTB
  admins) exclusively. Grant `access_dtb` to your own group/state (e.g.
  "member", "FC", "leadership"); nothing else is required.
- **Automatic revocation is back**: `1.7.0` turned the 6-hourly
  `validate_all_telegram_users` task into a report. Losing `access_dtb` now
  kicks the user from all tracked Telegram groups and unlinks their Telegram
  account again — the periodic run is the enforcement point (Alliance Auth does
  not notify apps about permission changes), `ServicesHook.validate_user()` and
  `delete_user()` do the same on user state changes, and the admin can trigger
  it instantly with *Validate & Kick Now*.

## 1.7.0
- **Access is now purely permission-based, no `alliance_id`**: DTB no longer
  looks at EVE data — a user may use the bridge when holding a DTB permission
  (`access_dtb`, or `manage_dtb_rules` for admins).
- **Migration `0020` drops `DTBSettings.alliance_id`** (field, admin page,
  settings form, `dtb_setup --alliance-id`, and the character-update signal
  that watched for alliance changes are gone).

## 1.6.4
- **`access_dtb` is now enforced**: AA only *hands* a service's `access_perm`
  to the service hook, it does not check it — so a user without the permission
  still saw the Discord-Telegram Bridge tile (and could open the page) as long
  as they had a character in the configured alliance. DTB now checks the
  permission itself (`tasks._user_can_use_dtb()`), used by
  `service_active_for_user()`, `show_service_ctrl()`, the DTB overview page and
  the link/unlink views, so the tile, the pages and the linking flow all follow
  the same rule: `access_dtb` **and** membership of the alliance from
  `DTBSettings.alliance_id`; DTB admins (`manage_dtb_rules`) always pass.
  Grant `access_dtb` to the group/state that holds the members who may use the
  bridge — it is not assigned automatically, and the periodic validation task
  unlinks users who do not hold it. Regression test added
  (`test_link_view_denied_without_access_permission`).
- Migration `0019` restores the `access_dtb` permission (removed again in
  1.6.3 by mistake, see below).

## 1.6.3
- **Dead permission removed**: `aa_discord_telegram_bridge.access_dtb` was
  declared in `DTBSettings.Meta.permissions` and set as `access_perm` on the
  service hook, but nothing ever checked it — AA's `ServicesHook` does not
  enforce `access_perm` itself. Removing it was wrong: the permission is the
  intended "basic access" gate, restored and enforced in 1.6.4.

## 1.6.2
- **Secure one-time linking**: the "enter your Telegram username" and
  verification-code flows are gone. Clicking **Generate link** now mints a
  random, signed token bound to the requesting Auth user (15-minute TTL,
  stored in `TelegramLinkRequest.token`/`expires_at`/`user`) and shows a
  deep link `t.me/<bot>?start=<token>`. Only the person who taps it gets
  linked — the old username/ID flow let any member claim another user's
  Telegram account by knowing their @username. A bare `/start` no longer
  stores a pending request (that was the auto-link race). Expired tokens are
  purged automatically and reuse is rejected.
- **Verified webhooks**: when a Telegram webhook URL is set (or changed /
  removed) in Django admin, DTB auto-registers it with Telegram via
  `setWebhook`, generates and stores a random `secret_token` and verifies
  every incoming webhook request against it (`X-Telegram-Bot-Api-Secret-Token`),
  closing the unauthenticated CSRF-exempt endpoint.
- **Ownership fallback**: hard `user.character_ownerships` references
  (validation task, auth hook, character-update signal) replaced with a
  version-agnostic `iter_user_ownerships()`, so installs where AA exposes the
  singular `character_ownership` no longer throw.
- **Forwarder hardening**: Discord embed content is escaped before rendering
  (no double-processing / raw-HTML injection), plain message text is escaped
  once, messages over Telegram's 4096-char limit are truncated, and a recent
  (channel, message_id) cache deduplicates forwards after reconnects.
- **Forwarded-message layout**: the author now appears directly under the rule
  header (`[rule]` / author / text) as before — the hardening pass had moved
  the author to the end of the message and added a redundant `👤 <channel>`
  line; both reverted to the previous compact format.
- **Connection status**: the `dtb_test_connections` hourly periodic task is
  now auto-registered (previously `test_connections` was never scheduled, so
  the status page showed stale data for installs that didn't schedule it).
- **Dead code removed**: `verify_link` view/route/template and the
  `TelegramUserLinkForm` flow are gone; unused imports (`hashlib`, `shlex`,
  duplicate `subprocess`, `html`, `re`, session code plumbing) cleaned up.
- **Tests added**: `tests.py` covers keyword matching, target parsing (incl.
  forum topics), message rendering/escaping/truncation, the token link flow
  (valid/expired/unknown/reused tokens), webhook secret auth, and the
  no-pending-request property of a bare `/start`. Run with
  `python manage.py test aa_discord_telegram_bridge`.
- **Link flow wording/order**: the "Successfully linked!" confirmation is now
  sent before the group invite links and mentions that invite links follow
  (a bot cannot reliably know when a user actually joins via an invite link).
- **l10n fix**: multi-line strings in the `.po` files were stored with literal
  `\\n` instead of newline escapes, so those translations never matched and
  users outside the English/zh locales saw the English fallback. Fixed for the
  link-confirmation message.
- **Dependency fix**: `pyproject.toml` now declares `py-cord>=2.0` instead of
  `discord.py>=2.0`. The two distributions share the `discord/` namespace;
  a `discord.py` requirement clobbered `py-cord` and broke
  `allianceauth-discordbot`'s authbot (`ImportError: ApplicationContext`) on
  standard AA installs.
- **cog load fix**: `await bot.add_cog(...)` crashed under py-cord (its
  `add_cog` is synchronous and returns `None`), aborting `on_ready` before the
  heartbeat task started — the Discord forwarder stayed dead and the status
  page showed "Stopped". The add is now awaited only when the call returns a
  coroutine, so both discord.py and py-cord work.
- **py-cord compatibility**: the Discord cog is now loaded from `on_ready`
  instead of overriding `setup_hook`. py-cord (installed when
  `allianceauth-discordbot` is present) never calls `setup_hook`, so on such
  installs the forwarder cog never loaded. Works with both discord.py and
  py-cord.
- **Bot token redaction**: Telegram/Discord API error strings (which can
  stringify the API URL containing the bot token) are scrubbed before they
  are written to log lines or stored in the `error_message` columns of
  `ConnectionStatus` / `ForwardHistory`.
- **Kick = ban + immediate unban**: removed users are no longer banned
  permanently — they can rejoin the groups (previously they sat banned in
  every group until they re-linked).
- **No unlink on failed kick**: the Telegram profile is only unlinked when a
  kick actually succeeded (or there are no groups). If every ban fails, the
  profile stays linked so the periodic validation retries, instead of
  Alliance Auth believing the user was unlinked while they are still present
  in the groups.
- Admin Chat Members page and the bot's `/stop` now report when no group
  kick succeeded.
- Bot Logs page now falls back to reading a plain log file (supervisor-style
  deployments) when no `aa-dtb-bot` systemd unit exists, instead of showing
  an empty page. Configure the path via the `DTB_BOT_LOG_FILE` Django
  setting; by default it probes common locations like
  `/var/log/supervisor/dtb-bot.log`.

## 1.6.0
- Chat Members page no longer blocks while fetching data: the page renders
  instantly from the DB and Telegram display names, bot flags and group-admin
  status are loaded lazily per member via an AJAX endpoint, with a progress
  bar ("X / N") and bounded concurrency to avoid hammering the Telegram API.
- The page header now shows the total member count, a live "Bots: X" counter,
  DTB admin badges (from `manage_dtb_rules`), TG admin badges (group
  `creator`/`administrator`), and bots are sorted to the end of the list.
  Localised for all 6 languages.
- `unlink_telegram` now also requires the alliance-membership gate: users
  outside the configured alliance (and non-DTB admins) can no longer POST to
  the unlink endpoint; everything DTB-related is now hidden from
  non-alliance users.

## 1.5.1
- The DTB block on the Alliance Auth services page now uses a strict
  membership check: it is hidden for non-alliance users (previously any
  `is_staff`/`is_superuser` account skipped the alliance check and saw the
  block). DTB admins (`manage_dtb_rules`) still always see it. The same
  strict gate now applies to `/dtb/`, `link_telegram` and `verify_link`.

## 1.5.0
- New "Chat Members" page in the admin dashboard (`/dtb/admin/members/`)
  listing every Telegram account linked to Alliance Auth (including old /
  archived users): Telegram username + ID, portal username with main
  character name / alliance ticker / corporation, live Telegram display name
  (fetched via `getChatMember`) and registration status. Each row has a
  "Kick" button that removes the user from all bot-managed Telegram groups
  and unlinks their account. Localised for all 6 languages.

## 1.4.11
- Restrict access: the `/dtb/` services page now returns 403 unless the user
  is a member of the configured alliance OR has the DTB admin permission
  (`manage_dtb_rules`). The legacy `verify_link` flow enforces the same
  guard. Admin pages/buttons were already permission-gated.

## 1.4.10
- Telegram polling thread now force-closes its DB connections after every
  update dispatch, fixing "Lost connection to MySQL server during query" in
  the long-running background thread. Without this, `/start` and other
  updates would occasionally fail (and the reply be lost) once the MySQL
  idle connection went stale.

## 1.4.9
- Admin "Back" buttons on Rules and Groups pages now return to the admin
  dashboard (`/dtb/admin/`) instead of the user services page.
- New "Bot Logs" page in the admin dashboard (`/dtb/admin/logs/`) that shows
  the `aa-dtb-bot` journalctl output, with line-count and errors-only options
  and localisation for all 6 languages.

## 1.4.8
- Link Account form now says "username or ID" (label, placeholder and step 2
  instructions) so users registering by numeric ID are not confused.

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
