# AA App Checklist Review — aa-discord-telegram-bridge (DTB)

## 1. Header

- **App:** aa-discord-Telegram Bridge (DTB)
- **Repo:** https://github.com/radioactive68/AUTH-Discord-Telegram-Bridge
- **State reviewed:** commit `47e4407` (version 1.7.7). No git tags exist locally or on the remote (`git tag -l`, `git ls-remote --tags` both empty), so the review is pinned to the commit per ground rule 0.1.5.
- **Review date:** 2026-10-18
- **Method:** code review only (read-only). Tests were not run, the app was not installed (ground rule 0.1.1). Target-environment version data was collected with read-only probes on the production host igc-pi.
- **Not checked:** runtime behaviour beyond read-only version probes; Bootstrap/FontAwesome rendering in a browser; end-to-end webhook delivery; performance under load; failover behaviour of broker/DB; PyPI dependency metadata of AA versions other than 5.0.0; Python 3.9/3.11 compatibility (target is 3.12.3 only).

## 2. Short description

DTB bridges EVE-alliance **Telegram groups** and **Discord**:

1. **Discord → Telegram forwarding:** rules map Discord channels to Telegram chats; an optional Discord bot (py-cord) forwards messages (`discord_cog.py`, `tasks.py` heartbeat-triggered).
2. **Telegram membership management:** users link their Telegram account via a token flow; the AA permission `access_dtb` controls auto-invite to and kick from configured Telegram groups (periodic validation task + AA services hook).
3. **Management UI** under `/dtb/`: rules, groups, members, forward history, settings, logs.

**External parts belonging to it:** a Telegram bot (long-polling process `dtb_run_bot`, or Telegram webhook mode), an optional Discord bot, two beat-scheduled Celery tasks, and a dedicated systemd/supervisor service for the polling bot. Without the Telegram bot process the app is non-functional (status page reports it).

## 3. Assessment per requirement

| Mandatory criterion | Rating | Reason |
|---|---|---|
| AA 5.0+ compatible | ✅ | `allianceauth>=5.0` (pyproject.toml:28); no private AA APIs; verified live against AA 5.4.0 / Django 5.2.17 on the target host |
| "jobs" implemented as tasks | ⚠️ | Two Celery tasks exist (tasks.py:18, tasks.py:93), but their beat entries are auto-created in `post_migrate` (apps.py:25-61) and kick work also runs synchronously inside a `User.post_save` signal (signals.py:10-33) |
| Bootstrap | ✅ | All full-page templates extend `allianceauth/base-bs5.html`; minor Bootstrap-4 leftovers: `form-group` (admin_rule_form.html:21), `badge badge-*` (dtb_tags.py:11,19,27) |
| no unnecessary ESI calls | ✅ | No ESI usage at all (grep `esi|providers` → none) |
| use of available app data | ➖ | Deliberately uses no EVE/corp data (README claim "no EVE data lookups"); display uses AA core `user.profile.main_character` only — nothing needed from the installed apps in 1.3 |
| tiered permissions (member / CEO / admin) | ⚠️ | Member tier ✅ (own data), admin tier ✅ (all data), no CEO/corp tier — the app holds no corp-scoped data; `view_forward_history` acts as an optional middle tier (README:241-247) |
| app version | ⚠️ | 1.7.7 in pyproject.toml:7 **and** duplicated in models.py:7 (`DTB_VERSION`); no `__version__` (`__init__.py` empty); no git tag (1.2.2) |
| proper README | ⚠️ | Comprehensive install/upgrade/permissions docs, but: internal contradiction on auto-kick (README:19-22 vs README:377-381), webhook prerequisite (`APPS_WITH_PUBLIC_VIEWS`) undocumented, no uninstall section, beat tasks undocumented |
| no auto-updating of any kind | ✅ | No update checks, pip calls or telemetry; `subprocess` only for `journalctl` in an admin-gated log view (views.py:635, views.py:676) |
| menu hook | ✅ | `MenuItemHook` registered (auth_hooks.py:146-167); visibility limited to admins (auth_hooks.py:156-162), members enter via the `/services/` tile — see 3.2.2 |
| no hidden creation of tasks, groups, roles or states | ❌ | 2 `PeriodicTask` + 2 `CrontabSchedule` rows created in `post_migrate` (apps.py:29-59); not in `local.py`, not documented |

## 4. Permissions table

| Codename | Display name | Effect | Recommendation |
|---|---|---|---|
| `access_dtb` | Can access Discord-Telegram Bridge | Service tile, member pages (`/dtb/`), Telegram linking, join-request approval; controls Telegram group membership (invite/kick via services hook, auth_hooks.py:46-119) | Grant to member states/groups — as-is ✅ |
| `manage_dtb_rules` | Can manage DTB rules and settings | All `/dtb/admin/` pages (`@dtb_admin_required`, views.py:43-61) | Leadership only — as-is ✅ |
| `view_forward_history` | Can view forward history | Forward-history page (views.py:224) | Optional — as-is ✅ |

Note: `default_permissions = ()` is **not** set on any of the 9 models, so Django's default add/change/delete/view permissions are also created for every model (checklist 4.1).

## 5. Complete list of interfaces

### 8.1 Public endpoints without login
- `telegram_webhook` (telegram_handler.py:107, `csrf_exempt`) is listed in `excluded_views` (auth_hooks.py:135-142). **However:** AA 5.4 only honours `excluded_views` when the app's URL namespace (`app_name='dtb'`, urls.py) is listed in `settings.APPS_WITH_PUBLIC_VIEWS` (AA source `allianceauth/urls.py:24-30`). The target's list does not contain `dtb`, so **all** DTB views (including the webhook) are decorated with `main_character_required` and webhook mode is broken on a stock install. Additionally, AA's `decorate_url_patterns` returns at the first excluded view of a flat list (`allianceauth/authentication/decorators.py:37`); the webhook is the first URL pattern, so once `dtb` is added to `APPS_WITH_PUBLIC_VIEWS`, **no** view gets the decorator at all (mitigated: every view has its own `@login_required` + permission check).

### 8.2 Protection of public endpoints
- Webhook: `X-Telegram-Bot-Api-Secret-Token` verified with `secrets.compare_digest` (telegram_handler.py:113-124); check is skipped while the stored secret is empty (models.py:28-31, generated on first webhook setup). No rate limiting, no body-size limit beyond WSGI defaults. Replay protection: none beyond the secret (updates are idempotent-ish; linking tokens are one-time, 15 min TTL). Cache required by the app itself: none (AA's Redis covers AA).

### 8.3 Web routes (urls.py), grouped by role
- **Public (intended):** `telegram/webhook/` → `telegram_webhook`.
- **User (login + `access_dtb`):** `services_overview`, `link_telegram` (POST), `unlink_telegram` (POST), `forward_history` (+ `view_forward_history`).
- **Admin (`manage_dtb_rules` via `@dtb_admin_required`):** `admin_index`, `admin_rules`, `admin_rule_add`, `admin_rule_edit`, `admin_rule_delete` (POST), `admin_rule_toggle` (POST), `admin_settings`, `admin_setup`, `admin_test_connection` (POST), `admin_validate_now` (⚠️ no `require_POST`, views.py:430-432), `admin_groups`, `admin_groups_sync` (POST), `admin_members`, `admin_member_info`, `admin_member_kick` (POST), `admin_tg_kick` (POST), `admin_logs`, `connection_status` (admin perm, views.py:239).

### 8.4 Django admin models (admin.py)
Registered (6 of 8): `DTBSettings` (add-once, no delete, bot tokens editable in plain text by superusers), `ForwardRule` (`list_editable` is_enabled/priority), `TelegramUser`, `ForwardHistory` (forwarded_at read-only), `ConnectionStatus`, `TelegramGroup`. Not registered (auto-managed): `TelegramLinkRequest`, `BotStatus`.

### 8.5 Settings
`DTB_TELEGRAM_BOT_TOKEN`, `DTB_DISCORD_BOT_TOKEN`, `DTB_DISCORD_GUILD_ID`, `DTB_ALLIANCE_ID`, `DTB_GITHUB_REPO` (migration seed), `DTB_BOT_LOG_FILE`, `DTB_LOG_DIR`, `DTB_LOG_FILE`, `DTB_LOG_LEVEL`, `DTB_LOG_MAX_BYTES`, `DTB_LOG_BACKUP_COUNT`, `LOG_DIR`, `BASE_DIR` (all via `getattr(settings, …)`).

### 8.6 Signal listeners
- `post_save User` ×2: `create_telegram_profile` (auth_hooks.py:122-126, auto-creates `TelegramUser`), `on_user_state_changed` (signals.py:10-33, synchronous kick when a user is deactivated).
- `post_migrate` (apps.py:17-18): creates beat entries (see 5.1).

### 8.7 Outgoing connections
- Telegram Bot API via `requests` (manager.py) — get_me, getChatAdministrators, invite/kick/send.
- Discord: py-cord gateway + REST (optional bot), plus a Discord REST call in manager.py.
- `journalctl` subprocess (admin log view, views.py:672-676).
- No ESI, no webhooks outbound.

### 8.8 External counterparts
- Telegram bot: created by the admin via @BotFather (README:165-174) — must exist; polling process `dtb_run_bot` (README:203-214, systemd `aa-dtb-bot`; on igc-pi it runs under supervisor).
- Optional Discord bot (README:175-191). Both counterparts are documented and exist on the target.

## 6. Numbered review catalogue

### 1. Compatibility and packaging

| Item | Rating | Finding |
|---|---|---|
| 1.1.1 | ✅ | `allianceauth>=5.0` (pyproject.toml:28) — meets ">=5.0,<6 or wider" (no upper bound; see pitfall 8) |
| 1.1.2 | ✅ | Only stable Django APIs (signals, ORM, class-based views, `translation_override`); target runs Django 5.2.17 (read-only probe); AA 5.0.0 requires `Django>=5.2,<6` (PyPI JSON) |
| 1.1.3 | ✅ | Installed AA 5.4.0 already satisfies `>=5.0` — `pip install` will not upgrade AA (probe + pyproject.toml:28); README also recommends `--no-deps` (README:69) |
| 1.1.4 | ❌ | No CI: no `.github/`, `tox.ini`, or `.gitlab-ci.yml` in the repo (root listing) — no AA/Python/DB matrix |
| 1.2.1 | ❌ | No `__version__` (`aa_discord_telegram_bridge/__init__.py` is empty); version exists twice, unlinked: pyproject.toml:7 and models.py:7 (`DTB_VERSION = '1.7.7'`) |
| 1.2.2 | ❌ | Reviewed state has no git tag (`git tag -l` and `git ls-remote --tags` empty) |
| 1.2.3 | ✅ | CHANGELOG.md present, entries through 1.7.7 |
| 1.2.4 | ❌ | Not on PyPI (pypi.org JSON → 404); available via Git only |
| 1.3.1 | ⚠️ | MANIFEST.in:1-5 lists templates/migrations/static/templatetags but **not** `locale`; pyproject.toml:44-51 includes it → sdist would ship without translations |
| 1.3.2 | ❌ | Tests ship inside the package: `tests.py` is part of `aa_discord_telegram_bridge`, and `packages.find` includes `aa_discord_telegram_bridge*` (pyproject.toml:41-42) |
| 1.3.3 | ➖ | `static/dtb/` exists but contains no files (listing empty) → `collectstatic` is harmless but effectively unnecessary (README:88 still instructs it) |

### 2. Conformity with AA standards

| Item | Rating | Finding |
|---|---|---|
| 2.1.1 | ✅ | `menu_item_hook` registered — see 3.2 |
| 2.1.2 | ⚠️ | `UrlHook` with 6 `excluded_views` (auth_hooks.py:129-143); inert on stock AA because `dtb` is absent from `APPS_WITH_PUBLIC_VIEWS` (see 8.1); flat-list return-at-first-excluded pitfall applies once enabled |
| 2.1.3 | list | `validate_user` (auth_hooks.py:116-119) deletes the link when `access_dtb` is lost; `delete_user` (auth_hooks.py:86-114) kicks from all groups and deactivates the profile; `update_groups` ❌ not implemented; `sync_nickname` ❌ not implemented (both default to no-op) |
| 2.1.4 | ➖ | No `dashboard_hook`, charlink or `secure_group_filters`; extra service callbacks: `service_active_for_user` / `show_service_ctrl` / `render_services_ctrl` (auth_hooks.py:46-84) |
| 2.2.1 | ⚠️ | `ready()` (apps.py:9-18): registers signals ✅, connects `post_migrate` ✅, and installs its own logging handlers at startup (apps.py:11-16 — writes a log file, no DB) |
| 2.2.2 | ❌ | Database writes on the startup path: `PeriodicTask`/`CrontabSchedule.get_or_create` in `post_migrate` (apps.py:25-61) |
| 2.3 | ❌ | Uses `logging.getLogger(__name__)` in 9 modules (e.g. views.py:27, signals.py:7); `get_extension_logger` not used at all (grep: 0 hits). Token redaction ✅ (`redact_secrets`, manager.py; covered by tests) |
| 2.4 | ➖ | No `django.core.checks` `@register` checks exist (grep: none) |
| 2.5 | list | `dtb_setup` (set tokens, sync groups, report access state), `dtb_add_group`, `dtb_sync_groups`, `dtb_run_bot` (auto-restart loop, waits for tokens) — matches README:230-239 |
| 2.6 | ⚠️ | Rebuilds a singleton settings model (models.py:10-52) instead of using installed `django-solo`; no `QueueOnce`/celery-once usage (grep: 0) though AA ships it; no ESI (n/a) |
| 2.7 | ➖ | No EVE static data or ESI needs; does not read corptools/eveuniverse data — none needed |

### 3. User interface

| Item | Rating | Finding |
|---|---|---|
| 3.1.1 | ✅ | All 12 full templates extend `allianceauth/base-bs5.html` (grep `extends`); `services_ctrl.html` is a partial without extends (expected) |
| 3.1.2 | ⚠️ | Bootstrap 5 classes dominate (129 matches); leftovers: `form-group` (admin_rule_form.html:21, BS4), `badge badge-success/secondary/warning/danger` in dtb_tags.py:11,19,27 (BS5 requires `bg-*` → badges render unstyled); icons use `fas fa-*` / `fa-solid fa-comments` (auth_hooks.py:151) — FA6-compatible prefixes, not verified in a browser |
| 3.1.3 | ➖ | No third-party CDN CSS/JS (grep `<script src` / `cdn` → none) |
| 3.2.1 | ⚠️ | `MenuItemHook` targets `dtb:admin_index` and is visible only when `user_is_dtb_admin` (auth_hooks.py:156-162) |
| 3.2.2 | ❌ | Members holding `access_dtb` have **no menu entry** — they reach the app only via the `/services/` service tile; the checklist requires menu reachability for every role |
| 3.2.3 | ⚠️ | `render()` runs `user_is_dtb_admin` → `has_perm` DB queries on every page load (auth_hooks.py:157) |
| 3.3 | ✅ | English default + de, fr, ja, ko, ru, zh_Hans (locale/); bot messages pick the user's language (telegram_handler.py:42-57); other languages fall back to English; README claim EN/RU/DE/FR/ZH/JA/KO matches |

### 4. Permissions

| Item | Rating | Finding |
|---|---|---|
| 4.1 | ⚠️ | 3 custom permissions (models.py:36-40) — see section 4 table; `default_permissions` not disabled on any of 9 models → Django default CRUD permissions are also created |
| 4.2.1 | ✅ | Every view has `@login_required` plus a permission gate (user views check `access_dtb` e.g. views.py:85,150,183; history requires `view_forward_history` views.py:224; all 17 admin views use `@dtb_admin_required` views.py:43-61); webhook is public by design (4.2.3) |
| 4.2.2 | ⚠️ | Mutating actions are POST-only (link/unlink/delete/toggle/sync/test/kick ✅); **`admin_validate_now` lacks `@require_POST`** (views.py:430-432) → state-changing action reachable via GET/CSRF |
| 4.2.3 | ✅ | Single `csrf_exempt`: `telegram_webhook` (telegram_handler.py:107) — Telegram cannot send CSRF tokens; protected instead by the secret-token header with `secrets.compare_digest` (telegram_handler.py:113-124) |
| 4.3.1 | ✅ | Superusers always pass `has_perm` — intended and documented (permissions.py:39-44, README:267-271) so the site owner is never kicked from groups |
| 4.3.2 | ✅ | `access_dtb` controls real external effects (Telegram membership via services hook, auth_hooks.py:86-119), not just visibility |
| 4.3.3 | ✅ | Group/state reorganisation is handled by AA backends (`has_perm` defers); the 6h validation task revokes within one cycle (tasks.py:128-140); explicit comment that Auth does not notify permission changes (signals.py:36-39) |
| 4.4.1 | ✅ | Members: own link/unlink and overview only |
| 4.4.2 | ➖ | No corp-scoped data exists in the app (no CEO tier); `view_forward_history` is an optional intermediate tier (README:241-247) |
| 4.4.3 | ✅ | Admins: full member list, all rules/groups/settings/logs via `manage_dtb_rules` |
| 4.5 | ⚠️ | 6 of 8 models in Django admin (admin.py): `DTBSettings` — bot tokens editable in plain text by superusers, add-once, no delete; `ForwardRule` with `list_editable`; `TelegramUser`; `ForwardHistory` (forwarded_at read-only); `ConnectionStatus`; `TelegramGroup`. Not registered (auto-managed): `TelegramLinkRequest`, `BotStatus` |

### 5. Hidden creations and side effects

| Item | Rating | Finding |
|---|---|---|
| 5.1 | ❌ | `PeriodicTask` ×2 (`dtb_validate_telegram_users` every 6h at :15, `dtb_test_connections` hourly at :05) + `CrontabSchedule` ×2 created in code via `post_migrate` (apps.py:29-59); not in `CELERYBEAT_SCHEDULE`/local.py, not documented in the README |
| 5.2 | ✅ | No `Group`/`State`/`Permission` auto-creation (grep outside tests: none; `create_dtb_permissions` is an intentional no-op stub, permissions.py:6-9) |
| 5.3 | ✅ | RunPython ×2, both touch only the app's own tables: 0003_seed_settings.py:44 (own `DTBSettings`), 0017_linkrequest_token_webhook_secret.py:31 (own `TelegramLinkRequest`); both reversible/safe |
| 5.4 | ✅ | No update checks, pip calls, telemetry or `importlib.reload`; `subprocess` only for `journalctl` in an admin-gated view with fixed argv and timeout=10 (views.py:635, views.py:676) |

### 6. Tasks and jobs

| Item | Rating | Finding |
|---|---|---|
| 6.1 | list | `test_connections` (tasks.py:18, `bind=True`, `max_retries=3` but `self.retry` never called) — hourly; `validate_all_telegram_users` (tasks.py:93, same retry pattern) — every 6h. No `QueueOnce` → overlap theoretically possible (low risk given the schedules) |
| 6.2 | list | Triggers: `post_migrate` beat rows (apps.py:36-59); `User.post_save` signal → immediate kick (signals.py:10-33); admin button `admin_validate_now` → `.apply()` in the web request (views.py:435); heartbeat thread → invite sync every ~60 min (bot_runner.py:84-91, 236-243) |
| 6.3.1 | ❌ | Long-running work outside tasks: (a) kick runs synchronously inside the `User.post_save` signal including `time.sleep(1)` per group (tasks.py:232 via signals.py:25) — blocks e.g. an AA admin deactivating a user; (b) `admin_validate_now` runs the full validation/kick cycle synchronously in a web request (views.py:435); (c) `admin_members` performs live Telegram `get_chat_administrators` per group on render (views.py:855-863); (d) `/services/` and `services_overview` call `get_me()` per page load (auth_hooks.py:67-76, views.py:93-101) |
| 6.3.2 | ✅ | Web views do not depend on the broker: external calls go through the manager directly and `admin_validate_now` uses `.apply()` (runs locally) — no synchronous fallback failure when the broker is down |
| 6.4 | ⚠️ | Expired link requests are cleaned on each `/link` flow (telegram_handler.py:319-327, 376); `ForwardHistory` is **never** pruned (rows created in discord_cog.py:49; no retention policy) → unbounded growth |

### 7. ESI and use of existing data

| Item | Rating | Finding |
|---|---|---|
| 7.1 | ➖ | No ESI endpoints, scopes or tokens (grep `esi|providers` → none) |
| 7.2 | ✅ | Uses AA core `user.profile.main_character` for display only (views.py:736-767); does not touch `EveCharacter`/`EveCorporationInfo`/`EveAllianceInfo` — none needed |
| 7.3 | ➖ | Collects nothing that the installed apps in 1.3 already hold; no dependency on them |

### 8. Interfaces

Covered completely in section 5 above (8.1-8.8).

### 9. Documentation (README)

| Item | Rating | Finding |
|---|---|---|
| 9.1 | ⚠️ | Installation, `local.py` block, permissions, commands, upgrade and bot-service setup are documented; **missing:** uninstall section, beat-task documentation (they are auto-created instead — see 5.1), the `APPS_WITH_PUBLIC_VIEWS += ['dtb']` prerequisite for webhook mode |
| 9.2 | ⚠️ | Verified against code: commands (README:230-239), permission codenames (README:241-247 = models.py:36-40), locales (README:249 = locale/), 6h schedule (README:19-22 = apps.py:29-35), 60-min invite sync (README vs bot_runner.py). **Contradiction:** Features (README:19-22) promise "auto-kick … every 6 hours" while Troubleshooting (README:377-381) claims "Nothing is kicked automatically any more" — the code kicks (tasks.py:128-140); Troubleshooting is wrong. Plugin-structure listing omits `logging_setup.py` (README:318-339). "Python 3.9+" claim (README) is below AA's actual floor (3.10) — harmless |
| 9.3 | ⚠️ | Gaps: webhook-mode configuration prerequisite, GDPR/data-retention notes, secrets-stored-in-DB note, uninstall/leftovers, beat entries left after removal |
| 9.4 | ➖ | Documentation in English only (README, CHANGELOG) |

### 10. Security and operational risks

| Item | Rating | Finding |
|---|---|---|
| 10.1 | ⚠️ | Critical logic depends on external Telegram/Discord APIs and on the separate bot process; if `dtb_run_bot` is down, linking and forwarding are dead (the status page reports it) |
| 10.2 | ⚠️ | Linking binds only the tapper's Telegram account to the token-bound user; tokens are 256-bit (`secrets.token_urlsafe(32)`), TTL 15 min, single-use, previous tokens revoked (views.py:160-170); a bare `/start` never creates a request (telegram_handler.py:489-511 + tests); join-request approval requires a linked profile **and** `access_dtb` (telegram_handler.py:540-569). Weak spot: `admin_validate_now` triggers state changes via GET (views.py:430) |
| 10.3 | ⚠️ | Bot tokens stored in **plain text** in `DTBSettings` (models.py:12-19) — typical for the AA ecosystem but visible to any superuser in Django admin; webhook secret stored plain text (needed for `compare_digest`); link tokens stored plain text but ephemeral and single-use; tokens never appear in logs (`redact_secrets` + tests) |
| 10.4 | ⚠️ | Singleton `save()` forces `pk=1` (models.py:45-47) — benign race; invite loops have no locks → duplicate one-time invites possible (`member_limit=1` makes them harmless); kick-then-unlink sequence has explicit failure semantics (link kept if all kicks fail, tasks.py:181-189) |
| 10.5 | ⚠️ | Signal handler catches only `TelegramUser.DoesNotExist` (signals.py:21-33) — any other exception (DB/network) propagates into the caller's `User.save()` transaction and can roll it back; `create_telegram_profile` likewise unguarded (auth_hooks.py:122-126). No private Django or AA APIs are used otherwise |
| 10.6 | ⚠️ | `post_save User` listeners do cheap pre-checks (`created`, `is_active`) before any query ✅; but `/services/`, `services_overview` and `admin_members` make live Telegram HTTP calls per page render (auth_hooks.py:67-76, views.py:93-101, views.py:855-863) → latency and rate-limit exposure on busy installs; the validation task prefetches correctly (tasks.py:83-90) |
| 10.7 | ⚠️ | Personal data stored: telegram_user_id, username, chat IDs, link timestamps; `ForwardHistory.message_preview` keeps Discord message text (including content of users without accounts) **indefinitely** (models.py:235-243) — no retention/cleanup; unlink removes the linkage but history rows persist |
| 10.8 | ⚠️ | The app never assigns `APPS_WITH_PUBLIC_VIEWS` itself (no `=` trap in code ✅), but the README never mentions that webhook mode requires `APPS_WITH_PUBLIC_VIEWS += ['dtb']` — and the value must be the **URL namespace** `dtb`, not the package name `aa_discord_telegram_bridge` |

### 11. Maturity and migration

| Item | Rating | Finding |
|---|---|---|
| 11.1 | ⚠️ | 165 commits since 2026-07-20; authors on main: radioactive68 (164), IGC Auth deploy (1); a real test suite exists (50 tests, previously run: OK, 3 skipped); **no CI** |
| 11.2 | ➖ | No predecessor-app migration; internal migrations 0001-0017 with own-table-only RunPython (see 5.3) |
| 11.3 | ⚠️ | No uninstall documentation; leftovers after removal: 2 `PeriodicTask` + 2 `CrontabSchedule` rows (apps.py), content types/permissions, 9 tables, log files (`log/dtb-bridge.log`), lock file (`/tmp/dtb_discord_bot.lock`), bot systemd/supervisor unit |

## 7. Further pitfalls (biggest risk first)

1. **Webhook mode is broken on a stock AA install** (2.1.2 / 8.1 / 10.8): `excluded_views` is inert unless `APPS_WITH_PUBLIC_VIEWS` contains `dtb` (the URL namespace — not the package name); and once enabled, AA's flat-list `return` pitfall strips `main_character_required` from *all* DTB views (mitigated by the app's own login/permission checks on every view).
2. **Hidden beat entries** (5.1): two tasks appear without any `local.py` entry and are left behind after uninstall (11.3).
3. **Synchronous kick inside `User.post_save`** (6.3.1 / 10.5): blocks AA user-save requests with `time.sleep(1)` per group, and non-`DoesNotExist` exceptions can roll back AA's transaction.
4. **`admin_validate_now` reachable via GET** (4.2.2): state-changing action without `require_POST`, CSRF-triggerable.
5. **No CI and no tags** (1.1.4 / 1.2.2 / 11.1): regressions on AA upgrades go undetected; the reviewed state cannot be pinned by tag.
6. **Unbounded `ForwardHistory`** (6.4 / 10.7): stores message text forever — disk growth and GDPR exposure.
7. **README self-contradiction on auto-kick** (9.2): Features promise 6h auto-kick, Troubleshooting denies it — code kicks; operators may wrongly conclude revocation is broken.
8. **`allianceauth>=5.0` without an upper bound** (1.1.1): a future AA 6 would silently satisfy the constraint.
9. **Bootstrap-4 badge classes** (3.1.2): `badge badge-*` in dtb_tags.py renders unstyled on BS5.
10. **Plain-text bot tokens in DB**, editable by superusers in Django admin (10.3).

## 8. Conclusion

**Install with conditions.** The app is well-built for its purpose (complete permission model, CSRF-safe webhook design, tests, thorough README) and runs on the target (AA 5.4.0, Django 5.2.17), but it violates two hard checklist rules — hidden `PeriodicTask` creation (5.1) and inert `excluded_views` making documented webhook mode non-functional out of the box (8.1) — plus a synchronous kick signal that can block AA user saves.

**Conditions:**
1. Keep the current **polling** setup; only switch to webhook mode after adding `APPS_WITH_PUBLIC_VIEWS += ['dtb']` (URL namespace, not package name) and verifying delivery end-to-end.
2. On uninstall, delete the beat entries by name (`dtb_validate_telegram_users`, `dtb_test_connections`) — they are not removed automatically.
3. Recommended upstream fixes before wider rollout: `@require_POST` on `admin_validate_now`, a broad exception guard in `signals.py`, retention for `ForwardHistory`, `bg-*` badge classes, `__version__` + git tags, and a CI matrix.

*Not checked: see section 1. Review was read-only; tests were not executed as part of this review.*
