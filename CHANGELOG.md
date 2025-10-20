# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

- Nothing yet.

## [1.11.0] - 2025-10-20

### Added

- Corporation ownership support for blueprints and industry jobs, including the `CorporationSharingSetting` model, director dashboards, and the `can_manage_corporate_assets` permission.
- Conditional offer chat for blueprint copy negotiations with persistent history, modal UI, and buyer/seller decision tracking.
- Shared UI components (`base.html`, chat modal/preview partials, `components.css`, `chat.css`, `bp_copy_chat.js`) for consistent styling across pages.

### Changed

- Blueprint copy fulfilment and my-requests views now render three cards per row, collapse conditional offers into accordions, and surface quick chat launchers.
- Token management and corporation dashboards highlight director scope coverage and allow per-corporation copy sharing toggles.
- Background sync reuses director tokens, validates required corporation roles, and records blueprint/job ownership metadata for corporate filters.
- Alliance Auth administrators must assign the new Indy Hub permissions in Django admin to grant member, copy-manager, and corporate-director access levels.

### Fixed

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
