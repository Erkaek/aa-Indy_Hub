# Post-audit maintenance and Python support

Indy Hub now requires **Python 3.12 or newer**. Tox and CI test ordinary CPython
3.12, 3.13 and 3.14, with CharLink installed and enabled in every environment.
The free-threaded Python builds are not part of this matrix. Python 3.14 requires
Django 5.2.8 or newer; a conditional dependency prevents an older Django being
resolved for that interpreter. The deployed V5 interpreter remains Python 3.12.

## Automated checks

Run `tox` for the complete Python matrix. Missing interpreters fail the run.
The runner creates a private Redis Unix socket, uses SQLite in memory and memory
Celery/email backends, and blocks external socket connections before Django
startup. It never downloads an ESI specification during tests. `redis-server`
must be available on the test host. Coverage files live inside each tox env.

CharLink remains optional for deployments; adding it to test dependencies does
not add it to Indy Hub's runtime dependencies. The old corporate rejection test
is enabled again with its original assertions. `npm test` runs both lint and the
five craft/Material Exchange/blueprint JavaScript regressions.

The CI also runs the complete suite on disposable MySQL 8.4 and MariaDB 10.11
services under Python 3.12 and 3.14, and exercises a real prefork worker using synthetic tasks and a
private Redis. The worker qualification asserts soft and hard limits, routing
isolation, and successful work after the killed child has been replaced.

The SQL matrix also exercises previously SQLite-only fixture assumptions:
the fake cursor uses Django's backend-specific end-of-read value, and the
historical migration fixture uses a project reference within its ten-character
field limit. Historical token/configuration fixtures explicitly allocate their
integer keys within the private, serial test database: unlike SQLite/MariaDB,
MySQL does not populate generated keys after `bulk_create()`. They still bypass
model saves and signals, preserving the historical ownership and avoiding task
dispatch. A regression also exercises this limitation on SQLite. All original
business assertions and published migrations remain.

For a separately provisioned disposable SQL server, select `py312-mysql` and set
`INDY_HUB_TEST_DB_HOST`, `INDY_HUB_TEST_DB_PORT`, `INDY_HUB_TEST_DB_USER` and
`INDY_HUB_TEST_DB_PASSWORD`. A Unix socket can be supplied as HOST. This is never
a command for the active AA database: the harness creates/drops
`test_indyhub_test`. An explicit server is required; it does not default to an
active local MySQL service.

## Worker configuration

Indy Hub's declared Celery time limits require a pool supporting them. Keep other
apps' workers as configured and use a dedicated prefork consumer for Indy Hub:

```python
app.conf.task_routes = {
    # Preserve all existing host routes, including the Discord services queue.
    "discord.*": {"queue": "services"},
    "indy_hub.tasks.*": {"queue": "indy_hub"},
}
```

Add [the Supervisor block](deployment/indy_hub-worker.conf) to the host config and
include `worker_indy_hub` in its process group. Exclude this queue from a general
threads worker with `-X indy_hub`. Existing workers with explicit `-Q` selections
must not add it. The services/Discord worker keeps its current pool and queue.
The supplied block waits 960 seconds for shutdown, longer than the existing
900-second cycle limit. Task names, locks, budgets, schedules and priorities are
unchanged; no application retry layer is added.

Configuration takes effect when the corresponding publishers and workers load
it. Do not run duplicate consumers or discard queued messages. Previously
published tasks in the old queue retain their original routing; review/drain
that backlog during an ordinary maintenance window. A new routing rule does
not retroactively move messages. This change does not restart active services.

The repository's test `scripts/test_prefork_time_limits.py` runs no real Indy Hub
task and cannot validate business idempotence, an external ESI service or every
failure mode of a distributed deployment.

## Packaging and cleanup

- The public `aadiscordbot` extra now installs `allianceauth-discordbot`; the
  Python import remains `aadiscordbot`. CI checks its resolution separately from
  a successful package build.
- Chart.js **4.6.1** is served locally with its MIT license. Its npm tarball
  integrity is verified when vendoring; a page no longer loads an unversioned
  CDN script. Upgrades require another explicit asset/test review.
- Temporary prompts now live in `docs/temporary_prompt/`, outside the Python
  package. A directory symlink preserves old repository paths under
  `indy_hub/temporary_prompt/`. Native Flit builds do not traverse that symlink.
  Built wheel/sdist contents are checked by `scripts/check_distribution.py`.
- The unused token-cache helpers and obsolete SDE market-group helpers are
  removed. The active scope profiles, snapshots, SDE ORM lookups, industrial
  calculations and applicable SQL remain unchanged.

Python interpreters are separate from virtual environments. Removing obsolete
tox 3.10/3.11 environments does not remove the operating system's Python or
change the interpreter of the active V5 environment. Existing 3.13/3.14
interpreters can host their own tox environments alongside V5.

## Local qualification

On 2026-11-08, ordinary CPython 3.12.13, 3.13.14 and 3.14.6 passed the complete
916-test suite with CharLink enabled and no skips, under AA 5.6.0, Django 5.2.19,
django-esi 10.1.0 and SDE 1.0.0. The complete suite also passed on a disposable
MariaDB 10.11.18 server with Python 3.12, including historical migration cases.
No published migration was edited. MySQL 8.4 and SQL under Python 3.14 are
covered by the added CI jobs rather than claimed as locally executed here.

Pre-commit, npm lint/five regressions, archive-content checks, Discord-extra
resolution and real prefork timeout/recovery checks passed. Chrome for Testing
149.0.7827.55 with sandbox passed ten isolated real-view scenarios, including
local charts with known totals, period selection, desktop/mobile captures,
permission refusals and the existing price/cache/failure/recovery cases.

The available local MySQL 8.0 binary was refused by AA's EOL check; that check
was not disabled. All SQL qualification instead used the supported private
server. The active V5 database, packages and services were not upgraded or
restarted. No live ESI/OAuth/Discord service was used for qualification.
