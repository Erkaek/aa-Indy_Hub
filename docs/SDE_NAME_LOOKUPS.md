# SDE type names through the ORM

## Scope and preserved contract

`indy_hub.utils.eve.get_type_name()` and `batch_cache_type_names()` now share
`_sde_english_names()`. It inspects model metadata, disables multilingual query
rewriting when that public API is available, and selects an annotation built
with `Coalesce("name_en", "name")`. Without a registered English field, it reads
the base `name`, which the native SDE importer populates from English.

This replaces the type-name raw SQL and its physical-column introspection.
The shared helper does not import modeltranslation or make it a new mandatory
Indy Hub dependency. No SDE schema, import routine or dependency floor changes.
SQL in industry calculations and historical migrations is outside this change.

| Input or stored value                         | Unit lookup      | Batch lookup                   |
| --------------------------------------------- | ---------------- | ------------------------------ |
| No input / zero                               | Empty string     | Omitted from input set         |
| Invalid textual ID                            | Original text    | Existing `ValueError` behavior |
| Unknown ID 987654321                          | `"987654321"`    | `{987654321: "987654321"}`     |
| `name_en="Tritanium"`, active French/German   | `"Tritanium"`    | `"Tritanium"`                  |
| `name_en=NULL`, `name="Base"`                 | `"Base"`         | `"Base"`                       |
| `name_en=""`, `name="Base"`                   | ID string        | Empty string                   |
| English and base empty                        | ID string        | Empty string                   |
| Unpublished type with a name                  | Its English name | ID string                      |
| SDE model unavailable in a test configuration | ID string        | ID strings                     |

These intentionally different empty-name and publication rules preserve the old
helpers. The batch query always checks `published=True`, even if a unit lookup
has already cached an unpublished name. Unit lookups can use a warm English
cache without querying. Batch reads remain authoritative database reads, as
before. Numeric placeholders are not cached as resolved names: a later SDE row
can replace an earlier missing-ID fallback. Positive names keep the existing
process-cache lifetime; this change does not implement distributed invalidation
after an SDE import.

Batch resolution deduplicates and sorts IDs, selects only IDs/names with
`values_list()`, and splits input by the backend's parameter limit, capped at
1,000. A 2,501-ID fixture performs three queries on SQLite's 999-parameter
limit, rather than one query per type. Empty input performs none. The backend
used for the limit is the queryset's routed database alias.

## Native APIs and version qualification

The declared floor remains `django-eveonline-sde>=0.0.1b11`. The version available
in V5 at qualification is 1.0.0, with Alliance Auth 5.6.0, Django 5.2.19 and
django-modeltranslation 0.19.17. The old environment's 0.2.0 inspection is
historical evidence, not the current runtime version.

Both 0.0.1b11 (wheel extracted under `/tmp`, not installed over V5) and 1.0.0
provide `ItemType`, its base `name` and `published` fields, and native
translation registration. With modeltranslation enabled, their manager exposes
`rewrite(False)` and registered `name_en`. Its native `values_list("name")`
follows the active locale; the new annotated query reads English independently.
The compiled query selects `COALESCE(name_en, name)` without replacing the base
operand by the active-language field. Tests also check actual database results,
not just this SQL shape.

The normal test project omits modeltranslation registration. Its real SDE model
therefore uses the base English field. Additional unmanaged test projections
exercise a plain manager with nullable English data. Translated runs use the
actual native manager and French/German fields. AA's configured French code is
`fr-fr`; tests cover it as well as `fr`, `en` and `de`. The bare `fr` code can
fall back to English in that configuration; `fr-fr` and `de` prove that native
localized reads really differ from the helper's English result.

SQLite is the executed database backend. Django generates the SQL and binds its
parameters; this is not a live MySQL/MariaDB qualification or proof covering
every intervening/future SDE release. A different SDE schema must be qualified
before removing compatibility code or changing the supported version range.

## Consumers

Direct consumers retain their existing APIs:

- `views/user.py`: dashboard and blueprint/copy information.
- `views/industry.py`: craft workspace entry, copy requests/offers/chat and
  project hero product names.
- `views/material_exchange.py`: accepted material names and sell paste catalog,
  using batch warm-up followed by unit reads.
- `services/production_projects.py` and `industry_skills.py`: craft/project and
  skill labels; other craft SQL/name paths remain outside this helper change.
- Industry, Material Exchange, notification and structure-sync services/tasks:
  persisted display labels and messages.
- `management/commands/cache_esi_data.py`: local batch warm-up;
  `populate_location_names.py` and its existing name helpers remain available.

Blueprint/job snapshots, private-structure caches, authorization checks, lookup
budgets and 403/rate-limit cooldowns are unchanged. There is no new ESI call.

## Separate decision: historical location fallback

`_resolve_sde_location_name()` still has a distinct caller in
`resolve_location_name()`. Its optional legacy table
`eve_sde_mapdenormalize` is not supplied by the inspected minimum/current SDE
models or migrations. Those versions instead have public models such as
`NPCStation`, `SolarSystem`, `Planet`, `Moon` and `Stargate`; 1.0.0 additionally
has `Star`. This does not establish the contents of existing deployments or
every version allowed by the open-ended dependency range.

The type-name replacement is therefore delivered first. The legacy location
fallback and its cached `_get_table_columns()` helper are retained separately
to preserve the existing public-location fallback contract. The removed
`_sde_english_name_expression()` no longer has a type-name consumer. No search
across all modern map tables is introduced on display, and private structure
resolution is never replaced by a public SDE lookup.

A later location-only change should first inventory actual legacy-table usage
on an authorized copy, decide which public location kinds need local resolution,
and test bounded model selection for stations, systems, celestials and gates.
Only then remove this remaining legacy introspection. The private token/budget,
cached-placeholder and 403/cooldown paths must retain their current behavior.
This prompt does not claim that all raw SQL or introspection in `utils/eve.py`
has disappeared.

## Reproducible qualification

Start with `indy_hub.tests.core.test_utils_eve`, then maintenance/name-resolution
tests and the affected craft/Material Exchange suites. In addition to the normal
test settings, run a separate process with `modeltranslation` inserted into
`INSTALLED_APPS` **before Django startup**. A late `override_settings` does not
register model fields correctly. Repeat the name tests against an isolated
extraction of the minimum supported SDE wheel.

Use a disposable database, dedicated cache, memory broker/email and outgoing
network blocking before startup. Historical migrations and `post_migrate` can
otherwise publish work. Run full pre-commit in a disposable Git copy so unrelated
work is not reformatted, then the complete tox matrix. Qualify real Chrome
consumers with synthetic accounts, native modeltranslation and multiple locale
cookies on an ephemeral test port; only external service boundaries are mocked.
Neither a successful SQLite run nor browser output validates the active
installation or live ESI/OAuth.

### Results on 2026-11-08

- The 10 name/publication tests passed against the minimum SDE 0.0.1b11 with
  native modeltranslation enabled. Initial current-version runs passed with and
  without registration; the final current-version tests also ran in the suites
  below.
- 382 affected tests passed with SDE 1.0.0 and native modeltranslation enabled
  (2 existing skips), covering names, localization, maintenance, craft,
  Material Exchange and the smoke/name-resolution suite.
- Full tox passed on Python 3.10, 3.11 and 3.12: **889 tests per environment,
  17 existing skips**, plus successful `pip check`. Existing tox environments
  were reused without installing packages; all used SDE 1.0.0, AA 5.6.0 and
  Django 5.2.19.
- Full pre-commit passed in a disposable Git copy containing the task changes.
  Model/migration consistency passed with no generated changes.
- Google Chrome for Testing 149.0.7827.55 / Playwright 1.61.1, **headless with
  sandbox**, passed seven scenarios through the real local views: cold/warm
  craft and Material Exchange names under `en`, `fr-fr` and `de`, unchanged
  blueprint catalogue, late-arriving synthetic SDE data after a numeric fallback,
  manager/superuser access, and anonymous/unprivileged refusal. There were no
  JavaScript exceptions or unexpected local HTTP errors in the successful run.
  Expected 403 responses and blocked external image resources remained visible
  in the console. Matching AA 5.6 Bootstrap/Flatly 5.3.8 assets were served from
  local fixtures after SHA512 verification against the installed templates.

The browser instance used a private SQLite copy, Redis socket, memory broker,
synthetic accounts and native translation registration. The actual local
craft/price APIs were exercised; outbound Fuzzwork/market-price HTTP responses,
analytics and task publication were controlled at external boundaries. OAuth
refresh was forbidden and never called. Asset snapshots remained unchanged.
Evidence, source fingerprints, isolation settings and screenshots are under
`/tmp/indyhub-sde-08-resume/`. No active SDE/database/service was modified, and
no commit, push or deployment was performed by this prompt.
