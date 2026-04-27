# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

Entries should stay short and grouped by meaningful outcomes. Each release should summarize both the user-facing impact and the important technical changes, while avoiding file-by-file or low-level implementation detail unless it affects upgrade steps, operations, or integrations.

## [Unreleased]

### Added

- Compatibility: official support for Alliance Auth 5 (Django 5.2 / django-esi 9) alongside Alliance Auth 4 (Django 4.2 / django-esi 8). The same Indy Hub release now installs and runs unchanged on both stacks.
- ESI client: introduced a small compatibility shim around bravado/aiopenapi3 so blueprint, jobs, assets, structures, location, and corporation roles operations resolve transparently on django-esi 8 and 9, with a unified `HTTPError` tuple replacing the old bravado-only error handling.
- CharLink hook: added the `esi-location.read_online.v1` scope to the personal authorization set so freshly-linked characters keep online-status access without an extra ESI re-authorization round trip.
- Forms: Indy Hub now raises Django's `DATA_UPLOAD_MAX_NUMBER_FIELDS` to 50 000 at startup (configurable via `INDY_HUB_MAX_FORM_FIELDS`) so Material Exchange and craft project configuration pages no longer raise `TooManyFieldsSent` when thousands of EVE market groups or type ids are submitted at once.
- Forms: Indy Hub now also raises Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` to 50 MB at startup (configurable via `INDY_HUB_MAX_REQUEST_BODY_BYTES`) so saving a craft project workspace no longer fails with a generic "Failed to save table" notification when the JSON payload (cached project snapshot, decisions, structures, …) exceeds Django's 2.5 MB default body limit.

### Changed

- Packaging: widened supported version ranges to `allianceauth>=4.6,<6` and `django-esi>=7,<10`, and added the `Framework :: Django :: 5.2` classifier so the package advertises its Alliance Auth 5 compatibility.
- Internals: replaced removed Django 5 timezone helpers (`django.utils.timezone.utc`) with `datetime.timezone.utc` across industry tasks, job notifications, and Material Exchange flows so the same code path runs on Django 4.2 and Django 5.2.
- Migrations: added schema-alignment migrations (`0098`, `0099`) that normalize corporation sharing scope choices and rename a few indexes, eliminating Django `makemigrations` drift on both Django 4.2 and Django 5.2.
- Crafting Projects: the project *Blueprints* tab now organises blueprint cards into one accordion section per product category (Battlecruiser, Battleship, Combat Drone, Module, Charge, …), sorted alphabetically. Cards whose product has no resolved category land in a single "Other" group rendered last. Mixed projects are much easier to scan than the single flat "Project blueprints" list.

### Fixed

- Material Exchange: clicking a stale Discord link to a sell or buy order that has been completed, cancelled, or deleted now lands on a friendly "order no longer available" page (HTTP 404) with a button back to the Material Exchange index, instead of Django's raw 404 debug message. Honors the `next=` query parameter when present and safe (issue #68).
- Material Exchange: saving the hub configuration on `/indy_hub/material-exchange/config/` no longer crashes with `TooManyFieldsSent` when many market groups are toggled at once.
- Crafting Projects: reaction blueprints (e.g. `Carbon Fiber Reaction Formula`) are no longer surfaced as copy candidates in the per-blueprint configuration cards. The copy request UI is hidden for reactions and the server-side `bp_copy_request_create` endpoint now rejects reaction `type_id`s, since reactions cannot be copied in EVE (issue #69).
- Crafting Projects: reaction formulas are kept in the project *Blueprints* tab inside a dedicated *Reactions* section rendered last, so players can still see which formulas the project consumes. The cards show ownership only (*Original owned* / *Copy owned* / *Not available* — never the orange *Available* state) and hide the ME/TE inputs and copy-request controls, since reaction blueprints have fixed ME/TE and cannot be copied (issue #69 follow-up).
- Crafting Projects: fixed the underlying bug that made the previous reaction-blueprint guards a no-op in production. `indy_hub.utils.eve.is_reaction_blueprint()` resolved its SDE model at module-import time, before the `indy_hub` app was registered, which silently bound `EveIndustryActivityProduct` to `None` and made the helper return `False` for every blueprint. The lookup now happens lazily through `apps.get_model("indy_hub", "SDEBlueprintActivityProduct")`, so reaction formulas (`Carbon Fiber`, `Caesarium Cadmide`, `Fermionic Condensates`, …) are correctly identified at runtime and filtered everywhere the helper is consumed (issue #69 follow-up).
- Crafting Projects: EFT fittings whose fit name contains square brackets (for example `[Retribution,   [PRIME] SD 01]`) are now parsed correctly. Previously the header regex bailed out on the inner `]`, the hull was treated as an unknown item and the rest of the fit was discarded.
- Crafting Projects: when importing an EFT fitting and leaving the project name blank, the modal now pre-fills it as `Hull // Fit Name` (for example `Hurricane // s.2012 T2`) instead of just the fit name, making project lists easier to scan.
- Industry Structures: registering an NPC Station with the *Manufacturing (Capitals)* or *Manufacturing (Super-Capitals)* tax flag enabled now succeeds. The synthetic NPC Station catalog entry has no rig sockets, but the form previously coerced its `rig_size` to `0` and rejected those flags silently. The check now only runs for player structures whose rig size is known. The Add / Edit Structure views also surface form validation errors as flash messages so a rejected POST no longer looks like a no-op page reload. Finally, NPC Stations are no longer flagged as `Setup needed` on the registry list because of missing rigs — they have no rig sockets so the rig-completeness check is skipped for them (issue #70).

## [1.16.2] - 2026-04-26

### Fixed

- Crafting Projects: fixed project run scaling, Apply refreshes, save/reopen behavior, and stale cached payload reuse so multi-item craft workspaces stay consistent after changing the final run count.
- Crafting Projects: fixed false `Changes pending` and `Unable to refresh workspace` states when switching craft tabs without real edits by treating pending refresh markers as transient client state.
- Blueprint Sharing: fixed the fulfill workspace on `/indy_hub/bp-copy/fulfill/` so switching between multiple requests in `Awaiting response` works again. A malformed actions block could break the workbench DOM, leaving only the first request panel selectable.
- Industry Structures: removed MySQL-incompatible conditional unique constraints from `IndustryStructure`, replaced them with model validation, and added a migration to silence `models.W036` while preserving duplicate protection for public names and personal tags.

## [1.16.1] - 2026-04-26

### Fixed

- Blueprint Sharing: fixed a fulfill-page crash in copy duration metadata when lazy translated labels were joined with plain strings, which could raise `TypeError: sequence item 1: expected str instance, __proxy__ found` on `/indy_hub/bp-copy/fulfill/`.
- Industry Structures: added `NPC Station` as a selectable structure type on `/indy_hub/industry/structures/add/` and `/indy_hub/industry/structures/<id>/edit/`, and automatically hides the Rig Loadout section for NPC stations because rigs do not apply there.

## [1.16.0] - 2026-04-07

### Added

- Crafting Projects: added a unified craft-table workflow backed by dedicated project models and APIs, with EFT or manual-list imports, aggregated multi-item outputs, saved workspaces, explicit save actions, and per-item production progress tracking.
- Crafting Projects: added a stock-management tab backed by cached character asset snapshots so tables can allocate owned items by character, reduce cash investment, and surface stock coverage directly in planning views.
- Crafting Projects: added a dedicated decision center that centralizes Buy vs Produce recommendations, tolerance-based strategy review, and grouped sourcing analysis in one workspace.
- Industry Structures: added a full structure registry with create, edit, duplicate, delete, bulk update, bulk import, rig guidance, and persisted structure plus solar-system industry datasets for craft calculations.
- Material Exchange: added in-page contract paste-check helpers, sell-order paste import, accepted multi-location support, and explicit item allowlists that extend hub configuration beyond market-group filtering.
- Blueprint Sharing: added structured proposal and counter-proposal handling directly in copy-request negotiations, with tracked confirmation states inside the request workflow.
- Access and integration: added CharLink hook support, per-corporation visibility controls for corporation blueprints and jobs pages, and a dedicated SDE-not-ready page for missing compatibility data.

### Changed

- Crafting workflow: removed the legacy single-blueprint simulation path in favor of project workspaces, redirected old craft entry points into craft tables, renamed simulations into Crafting Projects, and replaced the list view with a project dashboard split between draft and saved tables.
- Crafting workflow: finished the legacy simulation retirement by migrating stored single-blueprint data through the reversible `0096` project migration, removing the old simulation tables and returning explicit `410` responses from deprecated simulation endpoints.
- Craft page UX: reworked the craft frontend around payload-driven workspaces, manual save with unsaved-change warnings, lazy pane hydration, staged loading feedback, persisted session restoration, richer financial planning tools, and a more usable structure planner.
- Craft page UX: replaced the old run-optimized view with the decision center, added a dedicated stock tab, and limited the blueprint configuration tab to blueprints actually used by the current project state.
- Crafting Projects: saved workspaces now keep a cached craft payload snapshot and restore table context, manual prices, stock allocations, and sourcing choices more faithfully across reloads and SDE refreshes.
- Production tracking: redesigned project progress around item-level coverage, linked industry jobs, manual override states, and a clearer modal focused on what is finished, active, or still missing.
- Blueprint Sharing: turned request, history, and fulfill pages into richer negotiation workspaces with better copy-time estimates, stricter production-limit checks, clearer handoff states, and improved chat action visibility.
- Blueprint Sharing: refreshed the copy-request history page to match the newer fulfill workspace styling and tightened fulfill actions so scope-specific decisions stay aligned with the source actually selected.
- Material Exchange: improved contract validation, buy and sell flows, multi-location behavior, bulk actions, transaction history summaries, sell-page character switching, and configuration usability on top of the new hub rules.
- Performance and navigation: reduced repeated location and skill lookups, reused skill and slot snapshots more broadly, improved corporation read-only navigation, and restored native Alliance Auth badge rendering with richer Indy Hub counts.

### Fixed

- Crafting Projects: fixed mono-blueprint project loading by normalizing blueprint and product identities, preserving blueprint context on workspace saves, and keeping project payloads consistent when legacy data is migrated into craft tables.
- Crafting Projects: fixed decision-state drift between the tree, decision table, and blueprint tab so parent-locked items, apply-recommendations flows, and descendant cost comparisons now stay coherent.
- Crafting Projects: fixed stock-aware finance calculations so allocated inventory reduces cash investment without lowering production cost, and surfaced the remaining buy requirement consistently across finance and stock views.
- Industry data: hardened craft payload, structure, timing, import, and EVE helper queries so only `published = 1` SDE rows are used when resolving blueprints, products, materials, rigs, and type names.
- Material Exchange: fixed large form payload and `TooManyFieldsSent` issues, contract structure or item-name resolution edge cases, duplicate transaction processing, and several sell-page submission or rendering regressions.
- Corporation and industry UX: fixed corporation jobs visibility, stale blueprint `bp_type` states, structure-name cache write-through and retry behavior, settings refresh feedback, and several stale location-label inconsistencies.
- Blueprint Sharing: fixed dual-source fulfill queue behavior so rejecting a corporation source no longer hides a request that is still fulfillable with a personal source, and kept request finalization aligned with remaining eligible scopes.
- Navigation and badges: fixed stale Indy Hub badge invalidation, improved cold-cache badge rendering, and ensured new blueprint-sharing or Material Exchange activity is reflected more reliably in the menu.
- Frontend polish: fixed duplicate input bindings, noisy debug exports, lazy-loaded tree rendering, structure edit feedback, and dark-theme readability issues across the refreshed craft and hub views.

### Internal

- Added broad regression coverage for the project workflow, structure registry, system cost index sync, Material Exchange contract handling, badge computation, access-control changes, and the new project progress behaviors shipped in this release.
- Added focused regression coverage for published-only SDE resolution, cached project payload handling, reversible legacy simulation migration, and scope-aware blueprint fulfill rejections.
- Added the supporting migrations, scheduled tasks, and service layers required for structure datasets, system cost indices, craft timing or skill helpers, and the new production-project models.
- Updated release metadata, frontend package versions, and compatibility redirects to complete the `1.16.0` transition away from legacy single-blueprint simulations.

### Ticket closures

- Closes `#50`: `sync_sde_compat` now handles missing local SDE data more safely and the release guidance around SDE bootstrap and sync commands has been clarified.
- Closes `#52`: corporation blueprint and related industry views now resolve structure names more reliably instead of falling back to raw structure IDs in normal cases.
- Closes `#53`: Material Exchange buy flows no longer depend on oversized zero-quantity payloads, avoiding `TooManyFieldsSent` failures on large inventories.
- Closes `#55`: tax visibility is now included in the new Crafting Projects financial workflow that replaces the legacy simulation screen.
- Closes `#56`: Material Exchange configuration now supports multiple accepted locations plus finer buy or sell filtering through market-group rules and explicit item allowlists.
- Closes `#57`: Indy Hub now exposes an optional CharLink hook integration for shared token and scope onboarding flows.
- Closes `#62`: corporation jobs are now accounted for in slot-usage and overview reporting instead of only personal character jobs.
- Closes `#63`: corporation blueprint and jobs pages now support broader per-corporation visibility controls beyond managers only.
- Closes `#64`: Material Exchange sell orders now support a dedicated paste-import mode alongside the ESI asset workflow.
- Closes `#65`: Material Exchange mismatch and anomaly messages now resolve SDE item names in notifications, notes, and detail views instead of showing raw type IDs when names are available.

### Update from 1.15.1

To update an existing `1.15.1` installation to `1.16.0`, use the sequence matching your deployment type.

#### Bare Metal

1. Update Indy Hub:

- `pip install --upgrade indy-hub`

2. Apply database migrations:

- `python manage.py migrate`

3. Refresh static assets:

- `python manage.py collectstatic --noinput`

4. Populate or refresh Indy Hub SDE compatibility data:

- `python manage.py sync_sde_compat`

5. Restart the Alliance Auth server/services.

#### Docker

1. Update the pinned package version in `conf/requirements.txt` to `indy-hub==1.16.0`.

1. Install/upgrade the package in the application container:

- `docker compose exec allianceauth_gunicorn bash -c "pip install --upgrade indy-hub"`

3. Apply database migrations:

- `docker compose exec allianceauth_gunicorn auth migrate`

4. Refresh static assets:

- `docker compose exec allianceauth_gunicorn auth collectstatic --noinput`

5. Populate or refresh Indy Hub SDE compatibility data:

- `docker compose exec allianceauth_gunicorn auth sync_sde_compat`

6. Restart Alliance Auth containers/services as required by your deployment.

## [1.15.1] - 2026-03-03

### Fixed

- SDE compatibility sync is now safer when local data is missing and records sync timestamps more reliably in persisted sync state.
- Material Exchange corporation structure loading is now more robust across user scoping, stale roles, cache misses, and empty asset states.

### Changed

- Simplified SDE compatibility scheduling and state tracking around the local sync metadata table.
- Isolated corporation structure caches per user and reduced unnecessary startup bootstrap work.

### Internal

- Release metadata bump to `1.15.1`.
- Frontend package metadata aligned to `1.15.1` in `package.json` and `package-lock.json`.

## [1.15.0] - 2026-03-02

### Added

- Added Indy Hub SDE compatibility models, sync commands, and related background tasks for the new SDE-backed industry layer.
- Added analytics helpers plus live badge and token-refresh support for the UI and lightweight navigation updates.

### Changed

- Replaced `django-eveuniverse` with `django-eveonline-sde` across the active industry and Material Exchange stack.
- Improved Material Exchange contract handling, configuration UX, sell page UX, and menu or token performance on top of the new data layer.
- Simplified admin and permission formatting while reducing live role-fetch pressure in corporation-aware views.

### Fixed

- Fixed early token-management rendering issues, stale badge rendering, and several Material Exchange naming, image, and submission regressions.
- Hardened SDE compatibility imports and large Material Exchange form handling.

### Internal

- Added supporting analytics instrumentation and related regression coverage.
- Release metadata bump to `1.15.0`.

### Update

To apply this release safely, use the sequence matching your deployment type.

#### Bare Metal

1. Install SDE backend dependency:

- `pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git`

2. Update Indy Hub:

- `pip install --upgrade indy-hub`

3. Apply database migrations:

- `python manage.py migrate`

4. Refresh static assets:

- `python manage.py collectstatic --noinput`

5. Restart the Alliance Auth server/services.
1. Populate new Indy Hub compatibility tables:

- `python manage.py sync_sde_compat`

#### Docker

1. Install/upgrade dependencies in the application container:

- `docker compose exec allianceauth_gunicorn bash -c "pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git && pip install --upgrade indy-hub"`

2. Apply database migrations:

- `docker compose exec allianceauth_gunicorn auth migrate`

3. Refresh static assets:

- `docker compose exec allianceauth_gunicorn auth collectstatic --noinput`

4. Restart Alliance Auth containers:

- `docker compose build && docker compose down && docker compose up -d`

5. Populate new Indy Hub compatibility tables:

- `docker compose exec allianceauth_gunicorn auth sync_sde_compat`

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
