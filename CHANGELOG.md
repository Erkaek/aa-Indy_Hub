# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

### Added

- Analytics: added safe Alliance Auth analytics helper module (`indy_hub/utils/analytics.py`) with task/event and standardized view-hit emitters.
- Navigation/UI: added live menu badge template (`indy_hub/includes/menuitem_live_badge.html`) with client-side async badge refresh.
- API: added `menu_badge_count` endpoint and route (`indy_hub:menu_badge_count`) for non-blocking badge updates.
- Token Management: added live refresh endpoint and route (`indy_hub:token_management_live_refresh`) plus client-side async refresh/reload logic on the ESI page.
- Tests: added regression tests for sell-order rejection from `anomaly` / `anomaly_rejected` states (`test_material_exchange_reject_sell.py`).

### Changed

- Permissions/Admin: removed global `Permission.__str__` monkeypatch and moved Indy Hub permission label formatting to local Group/User admin forms only.
- Menu performance: menu hook is now cache-first and no longer computes heavy badge queries during navigation render.
- Token Management UX/perf: `/indy_hub/esi/` now renders from lightweight/cached data first, then refreshes in background when changes are detected.
- Corporation scope handling: added non-live role-fetch mode and cache usage for dashboard/token management corp scope summaries to reduce live ESI pressure.
- Material Exchange (contracts): enhanced mismatch diagnostics with explicit missing/surplus item deltas in order/admin notifications.
- Material Exchange (contracts): buy-order validation now supports in-game completion overrides for finished contracts in mismatch scenarios (reference, criteria, items, price).
- Material Exchange (contracts): sell-order near-match handling improved for finished/wrong-reference and rejected-status paths.
- Material Exchange (UI/actions): reject-sell flow now accepts `anomaly` and `anomaly_rejected` as rejectable statuses.
- Documentation: README feature list now includes analytics hooks for Material Exchange lifecycle transitions.

### Fixed

- ESI callback noise/perf: token management flow no longer requires blocking role-scope decorator path on initial page render.
- Navigation responsiveness: Indy Hub menu displays immediately on cold cache and fills badge asynchronously when count becomes available.

### Internal

- Tasks analytics: instrumented major industry, housekeeping, location, notifications, material exchange, and user snapshot Celery tasks with standardized analytics events.
- Views analytics: instrumented hub, industry, material exchange, material exchange config/orders, user/token, and selected API endpoints with standardized view-hit analytics events.
- Utility refactor: extracted menu badge count computation into dedicated helper (`indy_hub/utils/menu_badge.py`) and reused it across API/task paths.
- Test coverage: expanded `test_material_exchange_contracts.py` for in-game override scenarios and mismatch detail propagation.

## [1.14.5] - 2026-02-22

### Added

- Material Exchange (Sell): new status `anomaly_rejected` (`Anomaly - Contract Refused In-Game (Redo Required)`) to keep orders open when an anomalous in-game contract is refused.
- Tests: added regression coverage for anomaly contracts accepted/refused in-game and for recovery after user submits a new compliant contract.
- Material Exchange (Sell): near-match detection for contracts that match expected structure/items/price but use a wrong order reference now raises explicit anomaly notes and notifications.

### Changed

- Material Exchange (Sell): anomaly validation now supports in-game override behavior; if an anomalous contract with the correct order reference is accepted in-game (`finished*`), the order is automatically moved to `validated`.
- Material Exchange (Sell): if an anomalous contract is refused/cancelled/expired/deleted in-game, the order now transitions to `anomaly_rejected` instead of `cancelled`, allowing the user to resubmit a compliant contract.
- Material Exchange (Sell): validation queue now processes `anomaly_rejected` orders the same as other active in-flight statuses so re-submitted contracts are picked up automatically.
- Material Exchange (UI): timelines, badges, progress breadcrumbs, and sell order detail instructions now include and render `anomaly_rejected` consistently.
- Material Exchange (UI): anomaly visibility was reinforced on index and order details (clear anomaly badges/highlighted rows, stronger notes panels, and auto-open anomaly modal on sell detail when notes exist).

### Fixed

- Material Exchange (Notifications): prevented repeated 5-minute notification spam for unchanged sell-order anomaly states.
- Material Exchange (Notifications): throttled buy-order `awaiting_validation` reminder notifications to avoid duplicate sends every cycle.

### Internal

- Migrations: added `0086_materialexchangesellorder_anomaly_rejected_status`.
- Release metadata bump to `1.14.5`.

## [1.14.4] - 2026-02-21

### Added

- Material Exchange (Sell): new order status `anomaly` (`Anomaly - Waiting User/Admin Action`) for contract mismatches requiring user/admin intervention.
- Material Exchange (Config): new Notifications setting to choose whether Material Hub admins are automatically alerted on sell-contract anomalies.
- Material Exchange (Index): superuser warning banner when a superuser is detected without explicit `can_manage_material_hub` assignment.
- Tests: added targeted coverage for Material Exchange configuration checkbox persistence in `test_material_exchange_config_save.py`.

### Changed

- Material Exchange (Sell): contract mismatch cases (wrong structure, wrong price, items mismatch, missing linked character) no longer auto-transition to `rejected`; they now transition to `anomaly`.
- Material Exchange (Sell): anomaly notifications now adapt to configuration; user messages no longer claim admins were alerted when admin-alerting is disabled.
- Material Exchange (Config): added a new "Notifications" step at the bottom of the configuration flow.
- Material Exchange (Sell): removed the contract confirmation modal on submit; sell order creation is now direct from the summary panel.
- Material Exchange (Sell Order Details): "How This Works" instructions remain visible while an order is in `anomaly` status.
- Blueprint Sharing (My Requests): request rows now use the same horizontal card visual style as the Fulfill queue for Active and History tabs.
- Permissions (Admin): custom Indy Hub permission display labels now render as `indy_hub | <permission name>` without the `blueprint` segment in admin permission pickers.

### Fixed

- Material Exchange (Config): saving with `Alert Material Hub admins on sell contract anomalies` unchecked now correctly persists `notify_admins_on_sell_anomaly=False` instead of silently keeping the previous value.
- Material Exchange (Config): checkbox parsing for `enforce_jita_price_bounds` now follows standard HTML form behavior consistently (`on` => true, missing key => false).
- Material Exchange (Darkly): improved readability of quantity shortcut buttons (`Zero`/`Max`) on buy/sell pages using Bootstrap classes only.
- Material Exchange (Darkly): improved readability of market-group dual-list controls and list rendering on the config page (`SELL - Market Groups` / `BUY - Market Groups`) without custom CSS overrides.
- Material Exchange (Order Details/Index): added lightweight attention animations to improve visibility of `How This Works` and superuser warning sections.

### Internal

- Migrations: added `0084_materialexchangesellorder_anomaly_status` (sell anomaly status + config toggle) and `0085_rename_permission_labels` (permission label rename data migration).
- Release metadata bump to `1.14.4`.

## [1.14.2] - 2026-02-19

### Fixed

- ESI sync: blueprint, industry job, and material exchange contract fetches now support forced refresh to recover correctly when local data is empty but ESI responds `304 Not Modified`.
- Blueprint sync: character and corporation refresh paths now force ESI refresh on first-load/empty local datasets to avoid stale-empty states.
- Industry job sync: character and corporation refresh paths now mirror blueprint protections against `304` + empty local cache.
- Material Exchange contracts: corporation contract and contract-item sync now force refresh when local cache is empty, including completion-check flows.

### Internal

- Release metadata bump to `1.14.2`.

## [1.14.1] - 2026-02-17

### Changed

- Blueprint Sharing: fulfill queue cards were redesigned for a cleaner, more readable layout with clearer status labels and compact actions.
- Blueprint Sharing: fulfill cards now show explicit `runs / copy` wording and keep requester copy-name action available.
- Material Exchange (Sell): added character tabs so user assets are split by character instead of mixed together.
- Material Exchange (Buy): removed manual sync buttons from empty state because stock/price sync is already triggered automatically on page load.

### Fixed

- Blueprint Sharing: fixed mismatch where requests could appear in fulfill queue but fail action with "not allowed to fulfill".
- Blueprint Sharing: fulfill queue now aligns with provider authorization rules so non-actionable requests are not shown as actionable.
- Material Exchange (Sell/Buy): market-group filtering now applies strictly; if no categories are configured, no items are shown.
- Material Exchange (Sell): only characters with non-empty sellable assets are shown in tabs; no "All characters" tab fallback.

## [1.14.0] - 2026-02-15

### Added

- Material Exchange: global enable/disable settings with task gating (`MaterialExchangeSettings`).
- Material Exchange: transaction stats history view with monthly charts and top-user aggregates.
- Material Exchange: quick-copy helpers for order reference, assign-to, and contract totals in order detail pages.
- Material Exchange: compact active-order timelines and admin panel improvements (collapsible panel, active-order counters, compact row actions).
- Industry: slot availability overview using skill snapshots, with dedicated UI and refresh tasks.
- ESI: persisted character snapshots for skills, corporation roles, and online status.
- ESI/Services: Fuzzwork pricing helper and shared django-esi OpenAPI provider layer.
- Scheduling: hourly stale-snapshot refresh task and periodic task bootstrap migration.
- Notifications: in-app warnings when required ESI tokens/scopes are missing.
- Token management: explicit missing-scope display for character and corporation coverage.

### Changed

- Dependencies: added `allianceauth-app-utils>=1`, `django-esi>=7,<9`, and `requests>=2.31`.
- ESI integration: moved to shared django-esi OpenAPI provider with compatibility-date driven behavior.
- Token management: simplified coverage tables and stronger missing-scope visibility for characters and corporations.
- Authorization flows: improved return URL handling (`next`) and force reauthorization support.
- Task orchestration: adaptive staggering for large ESI sync batches with per-minute target settings.
- Material Exchange UI: refreshed hub actions, order rows, progress bars, and transaction history presentation.
- Access control: Material Exchange transaction history now requires `can_manage_material_hub`.
- Periodic tasks: setup is now post-migrate aware with safer update/remove behavior on migration plans.
- Material Exchange: hub navigation entry now hides when globally disabled.
- Material Exchange: sync/validation tasks now skip when module is disabled or unconfigured.
- Material Exchange: sell/buy pages now show clearer pricing/update context and refresh timing feedback.
- Scopes: required Material Exchange corp scopes now include divisions/contracts, and unused corp wallet scope was removed.
- Celery: Indy Hub schedules can apply Alliance Auth cron offsets when available.

### Fixed

- Material Exchange: refresh loops now recover from stale running states and time out safely.
- Material Exchange: ESI outage cooldown handling improved with clearer retry behavior.
- Material Exchange: structure/hangar and scope handling reliability improved during stock/assets sync.
- ESI: rate-limit retry-after handling improved across task flows.
- ESI: corporation role/scope validation and token handling hardening across industry/material features.
- UI/Localization: cleaner wording and consistency updates across hub, buy/sell, and transactions screens.
- ESI: 304/not-modified and force-refresh handling improved for roles/assets cache workflows.
- Material Exchange: `refreshed=1` URL cleanup and browser-local time rendering improvements on buy/sell pages.
- Material Exchange: sell/buy loading overlays and refresh state transitions now behave more reliably.

### Internal

- Added migrations:
  - `0079_material_exchange_settings`
  - `0080_industry_skill_snapshot`
  - `0081_character_roles`
  - `0082_character_online_status`
  - `0083_setup_periodic_tasks`
- Refactored task registration/loading and removed obsolete helper/task modules (`esi_helpers.py`, `services/esi_contracts.py`, `tasks/optimization.py`).
- Added scheduled stale snapshot housekeeping task and related setup wiring.

## [1.13.13] - 2026-02-01

### Fixed

- Material Exchange: totals are now consistent between buy/sell form pages and order detail pages (whole-ISK rounding).
- Material Exchange: prevent partial order creation when submitted quantities don't match current assets/stock.
- Material Exchange: enforce Jita price bounds consistently for sell/buy pricing and order totals (display + order creation).

## [1.13.12] - 2026-02-01

### Changed

- Dependencies: moved Discord DM providers to optional extras (`aadiscordbot` preferred, `aa-discordnotify` fallback).
- Documentation: linked the official Alliance Auth app pages for Discord bot and Discord notify.
- Material Exchange: Discord webhook embeds now follow notification level colors (aligned with MP).

### Removed

- Dependencies: removed unused `pytz` from requirements.

### Fixed

- Material Exchange: sell order pending reminders now wait 24 hours before notifying.
- Material Exchange: sell order contract matching now uses full `order_reference` (fallback `INDY-{id}`).

## [1.13.11] - 2026-01-31

### Added

- Logging: added operational logs for management commands, hub views, simulations, Discord action tokens, and material exchange order views.
- Logging: added error-focused logging around cache preloads, location population, permission cleanup, ESI status checks, and simulation aggregation.
- Notifications: added Discord webhook message edit support.
- Blueprint Sharing: added a manager-only history page for copy requests (with filters, metrics, and acceptor display).

### Changed

- Logging: aligned app logging to Alliance Auth extension logger conventions.
- Notifications: blueprint copy request webhooks are now edited (strikethrough) when accepted by a non-corporate owner.
- Notifications: edited blueprint copy request webhooks now show a gray embed with a "request closed" footer line.
- Blueprint Sharing: fulfill queue now shows personal ownership alongside corporation sources and prompts for personal vs corporation actions when both apply.
- Blueprint Sharing: history access is restricted to users with the manage BP permission.
- Blueprint Sharing: history button moved out of the fulfill header and styled as a primary action.

### Fixed

- URLs: removed duplicate `esi_hub` route registration.
- Navigation: set an explicit menu order for the Indy Hub menu hook.
- Notifications: personal owners now still receive notifications when a corporation webhook is configured for a blueprint they own.
- Blueprint Sharing: webhook edits now run for accept actions from the fulfill queue and no longer skip corporate members.
- Migrations: merged blueprint copy request acceptor tracking into the existing source-scope migration and removed the redundant migration file.

## [1.13.10] - 2026-01-29

### Changed

- Material Exchange: manual refresh cooldowns now respect scope (personal vs corporation) to avoid cross-scope throttling.
- Material Exchange: when configured but disabled, the hub page now shows a disabled state and hides the configuration CTA.
- Documentation: expanded Docker installation and update steps in README.

### Fixed

- Migrations: hardened blueprint copy request dedupe and unique constraint handling (including SQLite-compatible drops).
- Migrations: corporation asset fields now add safely on fresh databases when historical model state lacks the fields.
- Material Exchange: config save now tolerates empty/locale decimal inputs for markup percentages.
- Industry jobs: coerce lazy translation values in digest payloads so JSON serialization does not fail.
- Industry jobs: retry writes on MySQL deadlocks during job sync to reduce task failures.

## [1.13.9] - 2026-01-24

### Added

- Notifications: Discord webhook messages are now tracked so they can be deleted when buy orders or blueprint copy requests are cancelled.
- Notifications: webhook configuration can optionally ping @here per webhook.

### Changed

- Notifications: Discord webhook embeds now use type-specific styling (title and color).
- Notifications: MP and webhook embeds now show a short "clic here" CTA instead of full URLs.
- Material Exchange: pending buy order reminders now trigger after 24 hours instead of immediately.

### Fixed

- Notifications: Discord webhook payload content now matches the in-app message content.
- Material Exchange: webhook/admin links now route to my-orders detail pages with admin panel return.

## [1.13.8] - 2026-01-24

### Internal

- Notifications: add missing database indexes for Notification Webhooks.

## [1.13.7] - 2026-01-24

### Changed

- Notifications: Discord webhook delivery now retries up to 3 times before falling back to in-app messages.

### Fixed

- Notifications: webhook payloads now handle lazy translation titles without raising `TypeError`.
- Blueprint Sharing: webhook failures now fall back to MP notifications after retries.

## [1.13.6] - 2026-01-23

### Changed

- Material Exchange: buy/sell order totals are now rounded up to whole ISK for contract matching and display.
- Material Exchange: admin lists, order details, and contract instructions now show integer totals.

### Fixed

- Material Exchange: ESI contract validation now matches the rounded totals consistently for both buy and sell orders.
- Material Exchange: buy orders are now validated even when the contract is already finished before the next sync.
- Material Exchange: buy orders now refresh status on rejected/cancelled contracts even when no sell orders are pending.

### Internal

- Added migrations to backfill rounded totals on existing buy/sell orders.

## [1.13.5] - 2026-01-23

### Internal

- Version metadata bump to 1.13.5 (post-release fix after 1.13.4).

## [1.13.4] - 2026-01-23

### Added

- Notifications: Discord webhook support via Notification Webhooks (Django admin configurable).
- Blueprint Sharing: optional corporation-scoped Discord webhooks for copy request notifications.

### Changed

- Material Exchange: redesigned buy/sell order detail headers with a compact summary card (reference / buyer or corporation / total) and quick-copy actions.
- Material Exchange: order detail pages now hide raw IDs in contract instructions and item tables, and show notes under the timeline for a cleaner layout.
- Notifications: Material Exchange admin notifications can now be routed to a Discord webhook when configured.
- Notifications: admin recipient selection for Material Exchange and corp blueprint copy requests is now based on explicit permissions (instead of including staff/superusers).

### Fixed

- Material Exchange: copying the total amount now uses an EVE-friendly numeric format (e.g. `34002000.00`) while keeping localized display formatting on screen.

## [1.13.3] - 2026-01-22

### Changed

- Documentation updates (README structure, permissions, and install commands).

## [1.13.2] - 2026-01-22

### Fixed

- Material Exchange: ensure sell/buy markup percent falls back to defaults when unset.

## [1.13.1] - 2026-01-21

### Added

- Industry job notifications: support custom hourly digests.

### Changed

- Industry job notification frequency options were expanded and migrations consolidated.

## [1.13.0] - 2026-01-19

### Added

- Industry job notifications: preview endpoints plus digest scheduling options.
- Blueprint copy fulfilment UX improvements (dashboards, chats, counters, and sharing workflows).
- Material Exchange: buy/sell orders with order references, contract assignment/validation, and admin history.
- Material Exchange: improved asset refresh tooling and structure/station name resolution for clearer UI.

### Changed

- Blueprint copy pages and notifications were refined for clarity, with improved counters and richer corporation context.
- Job notification settings and Discord payload formatting were improved.
- Material Exchange contract matching now requires the contract title to include the order reference (e.g. `INDY-123`).
- Material Exchange templates were refreshed for clarity and filtering.

### Fixed

- Navigation badges no longer double count blueprint copy chats, and fulfilment counters ignore rejected offers.
- Restricting blueprint sharing scopes now cleans up impacted offers/requests to avoid stale dashboards.
- Indy Hub task registration now loads Celery tasks more reliably during app initialization.
- Material Exchange contract completion detection now prefers the stored ESI contract id (with more robust fallback parsing of validation notes).

## [1.12.2] - 2025-11-01

### Added

- Rich job completion notifications now include activity-aware thumbnails, detailed result summaries, and location context for both in-app and Discord delivery.
- Added `/indy_hub/personnal-jobs/notification_test/` so admins can preview the Discord embed formatting and verify notification routing without waiting for live jobs to finish.

### Changed

- Discord embeds use the new payload structure and automatically pick the correct image suffix (bp, bpc, icon) based on the underlying industry activity.
- Job notification blueprint resolution now prefers the latest blueprint records by using the existing `last_updated` field when finding a match.

### Fixed

- Resolved a `FieldError` that could appear while building job completion notifications when the resolver attempted to order by a non-existent `updated_at` column.

## [1.12.1] - 2025-11-01

### Added

- Signed Discord quick-action links for blueprint copy requests let builders accept, decline, or send conditions directly from notifications, with token validation before redirecting into Alliance Auth.
- Added an "Everyone" sharing scope for blueprint copy sharing so corporations and characters can expose their blueprint libraries without maintaining manual allow-lists.

### Changed

- Conditional offer responses now launch the copy-request chat automatically and drop the inline textarea so negotiations stay inside the dedicated conversation thread.
- Refreshed copy-sharing dashboards and helper text to surface the new sharing scope and clarify how visibility works across characters and corporations.

### Fixed

- Normalised lingering French error strings and inline comments to English for consistent end-user messaging and debugging output.

## [1.11.0] - 2025-10-20

### Added

- Manual corporation token allow-lists that limit blueprint and job syncing to explicitly approved directors per corporation. Token management now surfaces whitelisted pilots and warns when no authorised characters are selected.
- Corporation ownership support for blueprints and industry jobs, including the `CorporationSharingSetting` model, director dashboards, and the `can_manage_corporate_assets` permission.
- Conditional offer chat for blueprint copy negotiations with persistent history, modal UI, and buyer/seller decision tracking.
- Shared UI components (`base.html`, chat modal/preview partials, `components.css`, `chat.css`, `bp_copy_chat.js`) for consistent styling across pages.

### Changed

- Blueprint copy fulfilment and my-requests views now render three cards per row, collapse conditional offers into accordions, and surface quick chat launchers.
- Token management and corporation dashboards highlight director scope coverage and allow per-corporation copy sharing toggles.
- Background sync reuses director tokens, validates required corporation roles, and records blueprint/job ownership metadata for corporate filters.
- Corporation token storage now validates director roles up front and rejects tokens that lack the corporation roles scope before they can be used.
- Alliance Auth administrators must assign the new Indy Hub permissions in Django admin to grant member, copy-manager, and corporate-director access levels.

### Fixed

- Director-only ESI tokens are revoked scope-by-scope when mandatory corporation permissions are missing, preventing unrelated tokens from being deleted.
- Corporation sharing settings without explicit allow-lists once again authorise all characters by default, matching legacy behaviour.
- Backfilled `owner_kind` on existing blueprints and jobs to keep new filters accurate, and normalised legacy accepted offers for the new decision workflow.
- Template indentation adjustments keep EditorConfig and pre-commit hooks passing.

## [1.10.2] - 2025-10-15

### Added

- Discord DM notifications now favor `aadiscordbot` and fall back to `discordnotify`, configurable via `INDY_HUB_DISCORD_DM_ENABLED`.
- Manual blueprint and job refresh actions honor a configurable one-hour cooldown and surface feedback to the triggering user.

### Changed

- Bulk blueprint updates now run daily at 03:00 UTC and stagger user syncs across a configurable window; industry job sweeps occur every two hours with their own spread to ease ESI pressure.
- Existing Celery periodic tasks are updated in place during installation so deployments automatically pick up the new timers and staggering behaviour.

## [1.9.11] - 2025-10-15

### Added

- Onboarding progress tracking with `UserOnboardingProgress` model, admin, and dashboard checklist.
- Guided “journey” cards across blueprint request, fulfilment, and simulation pages to explain the flow.
- Gradient job progress visual styles and action cards for the industry jobs view.
- Manual onboarding controls with new endpoints to mark checklist items complete or hide the widget.

### Changed

- Industry job sync now normalizes timestamps, caches location lookups with a configurable budget, and falls back to placeholders when exhausted.
- Periodic job updates now run every 30 minutes with aligned Celery priorities.
- Blueprint copy cancellation reuses the caller’s `next` URL when it is safe, improving navigation.
- Dashboard copy-sharing cards and onboarding panels highlight remaining actions for new pilots.

### Fixed

- Completed job notifications gracefully parse string `end_date` values before comparing them to the current time.
- Copy request cancellation redirects back to the “My Requests” page when invoked there.
- Added regression coverage for onboarding flows and legacy request notes to keep the suite green.
