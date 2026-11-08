# Material Exchange singleton: defer native adoption

## Decision

Prompt 07 is resolved by **recommending deferral**, an explicitly permitted
outcome. Keep the existing `MaterialExchangeSettings.get_solo()` API and model.
Do not change installed data, introduce a cache, or generate a migration solely
to inherit django-solo. This is a compatibility decision, not a claim that the
current implementation guarantees singleton creation under concurrency.

The current implementation reads the lowest primary key, or creates a default
enabled row when empty. There is no uniqueness constraint enforcing a single
row. Two first readers can both create rows. Existing duplicates remain stored;
the lowest key determines the effective state. `default_permissions = ()`
remains unchanged. No admin registration for this model exists in Indy Hub.

## Native comparison and dependency contract

The available V5 environment has Alliance Auth 5.2.0, Django 5.2.15 and
django-solo 2.5.1. The available tox environment has Alliance Auth 5.6.0.
Both AA versions declare `django-solo`; Indy Hub declares `allianceauth>=5,<6`
in `pyproject.toml`. The package is therefore a transitive dependency in these
verified AA versions, rather than an accidental install. This is not an audit
of every historical AA 5 release. No new dependency is needed for the diagnostic.

The test project separately includes `solo` in `INSTALLED_APPS`. Package presence
alone does not prove app registration in another host project. An abstract
`SingletonModel` does not itself require a solo database table; native admin and
template integration would require separately reviewing the host setup. No host
settings, packages or admin pages are changed here.

The installed `solo/models.py` implements a fixed `singleton_instance_id = 1`,
`get_or_create(pk=1)`, and a `save()` which forces that identity. The primary key
uniqueness plus `get_or_create` limits duplicate creation through that normal API.
It does not prevent other-key rows through bulk writes or direct SQL. The normal
cache default is **disabled** (`SOLO_CACHE = None`); the following cache risks
apply when a host enables it.

| Existing data          | Direct native adoption                                       | Required future handling                                              |
| ---------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------- |
| Empty table            | Creates enabled key 1                                        | Check initialization and concurrent first access.                     |
| Single disabled key 1  | Keeps disabled state                                         | Preserve identity and timestamps.                                     |
| Single disabled key 42 | Creates enabled key 1 and masks key 42                       | Stop before mutation; prepare reference-preserving normalization.     |
| Equivalent duplicates  | Leaves duplicates; may add default key 1 if missing          | Stop; obtain an explicit retention/archive and identity policy.       |
| Conflicting duplicates | Cannot reconcile decisions; may mask the old effective state | Stop; owner must decide the authoritative state and retained history. |

A change of base class alone is not a safe transition. The required historical
normalization, reference analysis and cache policy cost more than removing the
small local method. No rows were inspected or changed on the active installation;
these cases are synthetic reproductions, not claims about its actual contents.

## Readers, writers and side effects

Repository inventory at this change:

| Location                                               | Use                                                                                                                  |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `models.py`, `MaterialExchangeSettings.get_solo`       | `.first()` and default `.create()`; public entry point.                                                              |
| `views/material_exchange.py`                           | Helpers read enablement, with the existing permissive exception fallback.                                            |
| `views/navigation.py`                                  | Navigation reads via `get_solo`, with the existing permissive exception fallback.                                    |
| `views/user.py`, `_build_settings_hub_context`         | Settings page reads via `get_solo`.                                                                                  |
| `views/material_exchange_config.py`                    | Configuration GET reads; toggle POST reads and `.save(update_fields=...)`.                                           |
| `views/hubs.py`, `_build_settings_admin_users_context` | Reads `.values_list("is_enabled", flat=True).first()` directly; defaults enabled when empty, without creating a row. |
| `tasks/__init__.py`, schedule reconciliation           | Reads via `get_solo` to determine the periodic cycle's enabled state.                                                |
| `tasks/material_exchange_contracts.py`                 | Five entry points read via `get_solo`: contract sync, cycle, buy/sell validation and completed-contract check.       |
| Migration `0079`                                       | Creates the table; remains untouched.                                                                                |
| Existing smoke and ESI-limit tests                     | A setup `.save()` and a mocked read; supplemented by the new characterization tests.                                 |

No production `.update()`, `.bulk_create()` or `.delete()` of this settings model
was found. The direct scalar reader still matters: introducing native cached
reads would create two truth paths. Operator ORM/raw writes also cannot be
assumed to call `save()`. No declared relation targets this settings model in
the repository; external references/triggers remain unknown until a deployment
inventory. This says nothing about `MaterialExchangeConfig` relations.

The existing toggle also synchronizes the business config's active flag and
the `indy-hub-material-exchange-cycle` Beat row. Those behaviors are retained,
with no changes to prices, orders, structures, notifications or task dispatch.
Historical migrations can publish bootstrap work; runtime `post_migrate` and
Beat startup can reconcile schedules. A future migration must account for those
effects independently of singleton data normalization.

## Cache and transaction findings

The current API reads the database on every call. A saved or bulk-updated state
is visible on the next read; rolled-back writes do not become its result.
Native django-solo caches the object after `save()` and deletes the cache before
instance `delete()`. Warm reads can avoid SQL, but:

- `QuerySet.update()` bypasses native `save()` and leaves a warm object stale.
- Native `save()` publishes the cached object before transaction commit; a
  subsequent rollback leaves a cached value that disagrees with the database.
- Cached reads and the existing direct scalar reader can disagree.

The tests use a test-only proxy inheriting the actual installed
`SingletonModel`, against the disposable settings table. They demonstrate these
native outcomes without implementing a second production singleton or changing
the migration graph. Cache cold/warm reads, saved changes, bulk changes, rollback,
historical identities, duplicates, permissions and the disabled task guard are
covered. Repeated sequential access is tested. **Distributed concurrency and
MySQL/MariaDB behavior are not validated by these SQLite tests.** The current
first/create race remains; no distributed-singleton guarantee is delivered.

## Prepared transition procedure — not an executable migration

1. On an authorized copy of each installation, inventory all rows with primary
   keys, `is_enabled`, `created_at`, `updated_at`, effective lowest-key state,
   inbound database references/triggers and ORM/bulk/raw writers. Record engine,
   aliases, `SOLO_CACHE` and the installed native implementation. Do not call
   `get_solo()` to diagnose an empty table: that would write a default row.
1. Validate **all** rows before mutation. Any duplicate or noncanonical identity
   should stop the initial rollout with a diagnostic containing the alias,
   affected keys and states. Conflicting duplicates need an owner decision about
   state and history; retaining the currently effective state alone does not
   authorize deleting the other decisions. No automatic cleanup is prepared.
1. Once retention/reference policies are approved, design normalization with
   historical `apps.get_model()` and `schema_editor.connection.alias`. Preserve
   effective state and timestamps, rewrite only reviewed references, and retain
   enough original data for a truthful reverse. The latest migration at audit is
   `0117_charactersettings_market_broker_fee_percent`; recheck the graph before
   assigning a new dependency. Never edit `0079` or another published migration.
1. Initially keep caching disabled for this model or design commit-aware cache
   publication/invalidation for every write path, including rollback and bulk
   writes. A global host `SOLO_CACHE` setting must not silently change its
   semantics. Reconcile the direct scalar reader without changing its empty-table
   behavior unintentionally. Do not add a second custom singleton just to wrap
   every native behavior; reassess whether adoption still reduces maintenance.
1. Rehearse forward and genuinely supported reverse operations on disposable
   SQLite **and the target MySQL/MariaDB versions**, including table-empty,
   alternate-key, duplicate and conflicting fixtures, references, sequence
   allocation, cache and concurrent processes. A no-op reverse cannot restore
   discarded duplicate rows. Without preserved originals, abort rather than
   advertise reversible data normalization.
1. Isolate database, cache, broker and migration hooks before startup. Verify
   `post_migrate` schedule/bootstrap effects and the existing toggle with Chrome.
   Run model/migration checks, targeted tests, pre-commit and the complete tox
   matrix. Deployment, maintenance windows and rollback backups require their
   own authorized operational plan.

**Migration status:** no new migration generated, no normalization migration
rehearsed, and no migration applied to the active installation. Only the decision
and procedure above are prepared. The native comparison is a test reproduction,
not a migrated installation. Benefits delivered now are documented compatibility
boundaries and regression evidence; the native singleton's concurrency/cache
benefits remain deferred.

## Isolated qualification (2026-11-08)

The targeted V5 run passed 30 tests: 14 new contract/native-risk cases and 16
existing configuration-save cases. Model/migration consistency passed with
`makemigrations indy_hub --check --dry-run`. Full pre-commit checks passed in a
disposable Git copy, including the new files.

The complete tox matrix passed on Python 3.10, 3.11 and 3.12: **882 tests per
environment, 17 skipped**, with `pip check` also passing in each. Existing tox
environments were reused without package installation; their editable source
points at this repository. This qualifies the available dependency sets, not a
fresh dependency resolution or every supported database backend.

Chrome for Testing 149.0.7827.55 / Playwright 1.61.1 passed seven real application
scenarios in **headless** mode with its sandbox enabled: manager read of disabled
key 42, activation and reload, deactivation and reload, member read/refused write,
anonymous and unprivileged access, superuser settings/admin-user read, and CSRF
refusal. Database identity and the existing Beat enablement flag were checked
after the actual form submissions. Three screenshots were inspected. There were
no JavaScript exceptions or unexpected local HTTP errors; expected forbidden
requests and blocked external image resources appeared in the console.

These runs used temporary SQLite, a private Redis socket, a memory broker and
synthetic accounts on an ephemeral port. External sockets were blocked before
Django startup. Browser tests intercepted external task publication, analytics
and OAuth boundaries; local views and APIs were not simulated. No real OAuth
refresh or ESI call occurred. The disposable browser server was stopped.

Full tox results and logs, source fingerprints, isolation settings, browser
scenarios and captures are retained under `/tmp/indyhub-singleton-07/`. Optional
CharLink tests follow the normal tox skip policy. Services outside this test
boundary and the migration/concurrency/engine cases listed above remain
unqualified.
