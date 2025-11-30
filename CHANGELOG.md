# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

_Nothing yet._

## [1.13.0] - 2025-11-15

### Added

- Live and digest preview endpoints for industry job notifications, including reusable serializers and sample scenarios so administrators can validate payloads before enabling production delivery.
- Interactive blueprint copy fulfilment layout with collapsible sections, responsive card grids, and a dedicated `bp_copy_fulfill.js` controller that drives quick chats, notes, and status updates.
- Inline access list summaries for personal characters and corporations on fulfilment cards to surface who can action each blueprint request.
- A confirmation modal for sharing scope changes that warns about the impact of restricting visibility and lets managers opt into automatic clean-up of impacted offers and requests.

### Changed

- Blueprint copy dashboards, fulfilment pages, and request detail views were refactored for clarity, with cached requester identity lookups, richer metadata, and consistent styling across personal, corporate, and alliance sources.
- Navigation badges and dashboard counters now treat unread copy chats and outstanding fulfilment items consistently while excluding rejected offers from the metrics businesses rely on.
- Blueprint copy notifications and chat payloads now embed corporation names and tickers wherever relevant so builders immediately know which organisation owns a request or offer.
- Job notification preferences support immediate or digest cadences with custom weekdays, improved validation feedback, and digest body generation that reuses the live notification builder.
- Sharing scope toggles defer notifications until transactions commit, automatically reject conditional offers that no longer qualify, and reset pending requests to keep dashboards accurate.
- Production simulation, token management, and other blueprint request templates received accessibility-minded heading hierarchy and typography improvements.

### Fixed

- Alliance Auth navigation menu no longer double counts blueprint copy chats when highlighting unread conversations, and job quick actions return users to their previous dashboard.
- Fulfilment counters and alerts ignore rejected blueprint copy offers, preventing ghost badges after negotiations conclude.
- Restricting blueprint sharing scopes now automatically closes conditional offers and cancels pending deliveries that fall outside the new visibility rules, eliminating stale records.

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
