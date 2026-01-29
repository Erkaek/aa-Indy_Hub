# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

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
