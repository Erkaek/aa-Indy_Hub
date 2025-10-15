# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

### Added

- Discord DM notifications now favour `aadiscordbot` and fall back to `discordnotify`, controllable via `INDY_HUB_DISCORD_DM_ENABLED`.

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
