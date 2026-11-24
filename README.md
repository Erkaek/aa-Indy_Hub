# Indy Hub for Alliance Auth

Indy Hub is an industry and Material Exchange application for
[Alliance Auth](https://allianceauth.readthedocs.io/en/latest/). It helps EVE Online corporations and
alliances manage blueprints, industry jobs, copy requests, production projects,
and internal material trading.

The `1.18.3` tag is the published baseline for this documentation review. Changes
on `main` after that tag are listed under **Unreleased** in the changelog; they
are not installed by selecting `indy-hub==1.18.3`. See the
[upgrade notes](docs/UPGRADE_NOTES.md) for deployment requirements.

## Features

- Personal and corporation blueprint libraries
- Manufacturing, research, invention, and reaction job tracking
- Blueprint copy requests, offers, chat, and delivery tracking
- Crafting projects with structure, stock, blueprint, and financial planning,
  including market sell fees and recursive production demand
- Bundled CCP industry rules with exact rig targeting and saved-recipe preservation
- Industry structure registry and corporation sharing controls
- Material Exchange buy and sell orders
- In-app and optional Discord notifications
- Superuser administration page with account health and usage statistics
- Responsive interface compatible with Alliance Auth 5 themes

For the complete list of changes in each release, see
[CHANGELOG.md](CHANGELOG.md).

## Requirements

- Alliance Auth 5
- Python 3.12 or newer (tested on 3.12, 3.13 and 3.14)
- `django-esi>=10.2,<11`
- Celery workers with a prefork consumer for Indy Hub, and Celery Beat
- A shared Django cache, normally Redis
- `django-eveonline-sde>=2,<3` with its base SDE data loaded

Optional integrations:

- [aa-charlink](https://apps.allianceauth.org/apps/detail/aa-charlink) for guided
  ESI authorization
- [aadiscordbot](https://apps.allianceauth.org/apps/detail/allianceauth-discordbot)
  or [discordnotify](https://apps.allianceauth.org/apps/detail/aa-discordnotify)
  for direct Discord notifications
- Corptools for activity-aware automatic refreshes

## Installation

### Bare metal

Install the packages:

```bash
pip install django-eveonline-sde indy-hub
```

Add both applications to `local.py`:

```python
INSTALLED_APPS = [
    # Existing applications...
    "eve_sde",
    "indy_hub",
]
```

Load the base `eve_sde` data by following the `django-eveonline-sde` setup
instructions, then finish the installation:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
# Restart gunicorn, Celery Beat, and Celery workers.
```

### Docker

Add Indy Hub to `conf/requirements.txt`:

```text
indy-hub==1.18.3
```

Add the applications to `conf/local.py`:

```python
INSTALLED_APPS = [
    # Existing applications...
    "eve_sde",
    "indy_hub",
]
```

Rebuild the containers, then run migrations and collect static files using the
commands provided by your Alliance Auth stack. A common setup uses:

```bash
docker compose build
docker compose up -d
docker compose exec allianceauth_gunicorn auth migrate
docker compose exec allianceauth_gunicorn auth collectstatic --noinput
docker compose restart
```

Make sure the base `eve_sde` data is loaded before using Indy Hub.

### After installation

1. Assign the required permissions in Alliance Auth.
1. Ask users to authorize their personal Indy Hub ESI scopes.
1. Ask corporation managers to authorize the additional corporation scopes they
   need.
1. Restart gunicorn, Celery Beat, and Celery workers.

If `aa-charlink` is installed, it automatically offers separate authorization
options for personal access, corporation administration, and Material Exchange.

## Permissions

Most users only need the base access permission. Administrative permissions
should be assigned only to trusted corporation managers.

| Permission shown in Django Admin | Recommended for                  | Access granted                                     |
| -------------------------------- | -------------------------------- | -------------------------------------------------- |
| `can access Indy_Hub`            | All Indy Hub users               | Personal blueprints, jobs, requests, and orders    |
| `can admin Corp`                 | Corporation blueprint managers   | Corporation blueprints, jobs, and sharing settings |
| `can admin MatExchange`          | Material Exchange administrators | Hub configuration, stock, orders, and transactions |

Corporation administration also requires suitable EVE corporation roles and
authorized corporation tokens.

The `Settings → User admin` page is available only to superusers. It provides
account diagnostics, alternate-character search, filtered CSV export and explicit
maintenance actions for up to 25 selected users: rebuilding diagnostic summaries
or refreshing blueprints/jobs. It does not replace Alliance Auth's native user
and group administration; those screens retain AA's permission labels and rules.

## Configuration

The default settings are suitable for most installations. Add only the options
you need to `local.py`.

### Notifications

```python
# Enable or disable direct Discord messages.
INDY_HUB_DISCORD_DM_ENABLED = True

# aa_only | discord_direct_only | both
INDY_HUB_NOTIFICATION_DISPATCH_MODE = "discord_direct_only"
```

Use `aa_only` when another Alliance Auth service already forwards notifications
to Discord. This avoids duplicate messages.

### Large crafting projects

The defaults support large forms and project files. Increase these values only
if users still reach upload limits:

```python
INDY_HUB_MAX_FORM_FIELDS = 50000
INDY_HUB_MAX_REQUEST_BODY_BYTES = 52428800  # 50 MB
```

### Corporation blueprints in personal projects

Personal projects use personal blueprints only by default. To let users choose
authorized corporation blueprints as a fallback, add this setting:

```python
INDY_HUB_PERSONAL_PROJECTS_ALLOW_CORP_BP = True
```

The `Corp BPs` switch then appears in Crafting Projects. Personal blueprints
remain preferred, and users only see corporation blueprints they are permitted
to access.

### Fixed prices for sparse Material Exchange items

If an item has incomplete Jita market data, a Material Exchange administrator
can give that exact item a fixed member-sale price in
`Material Exchange → Settings`. Enter one item and unit price per line, for
example `Ducinium II-Grade = 4500.00`. No `local.py` change is required.

### Optional global usage tracking

Normal Indy Hub pages already record the usage statistics shown to superusers.
Global middleware tracking is optional.

To enable it, add the middleware after Django's authentication and session
middleware:

```python
MIDDLEWARE = [
    # Existing middleware...
    "indy_hub.middleware.IndyHubUsageTrackingMiddleware",
]

INDY_HUB_USAGE_MIDDLEWARE_ENABLED = True
INDY_HUB_USAGE_MIDDLEWARE_ALLOWED_APP_NAMES = ("indy_hub",)
```

Keep the allowed application list limited to applications you intentionally want
to include in Indy Hub usage statistics.

### Background tasks

Indy Hub creates and repairs its background schedules automatically during
migrations and service restarts. No manual schedule setup is required.

Configure the dedicated prefork worker and task route described in
[worker configuration](docs/deployment/README.md#worker-configuration).
Preserve existing routes and exclude the Indy Hub queue from general threads
workers. The [Supervisor example](docs/deployment/indy_hub-worker.conf) includes
shutdown time sufficient for the longest Indy Hub task.

After installation or an update, always restart Celery Beat and Celery workers.
Corporation roles normally expire after 24 hours. If Celery is stopped in a
development environment, the ESI page can show **Roles unverified** until a role
refresh runs. An expired local role snapshot alone does not require Add corp or
new OAuth consent.

Industry compatibility is checked globally every ten minutes after an SDE
import. Compatible industrial content is accepted without an Indy Hub release
for each CCP build. Results persist in the database; pages perform no download.
With Celery stopped, run `python manage.py check_industry_rules` manually, or use
`--status` for read-only diagnostics. See the
[global compatibility check](docs/INDUSTRY_RULES.md#automatic-global-compatibility-check)
for offline archive checks and failure handling.

## Updating

Read [docs/UPGRADE_NOTES.md](docs/UPGRADE_NOTES.md) first if you are upgrading
from an older release.

### Bare metal

```bash
pip install --upgrade indy-hub
python manage.py migrate
python manage.py collectstatic --noinput
# Restart gunicorn, Celery Beat, and Celery workers.
```

### Docker

Set the desired published version in `conf/requirements.txt` (replace
`<target-version>` before using this example):

```text
indy-hub==<target-version>
```

Then rebuild, migrate, collect static files, and restart the containers:

```bash
docker compose build
docker compose up -d
docker compose exec allianceauth_gunicorn auth migrate
docker compose exec allianceauth_gunicorn auth collectstatic --noinput
docker compose restart
```

## Getting started

1. Open Indy Hub from the Alliance Auth dashboard.
1. Authorize your personal ESI scopes from Indy Hub settings or CharLink.
1. Open the blueprint library or Industry Jobs page to load your data.
1. Configure blueprint sharing or Material Exchange only if your corporation uses
   those features.

Corporation data is available only to users with the appropriate permission,
corporation role, and authorized token.

## Screenshots

These images are historical examples from 1.13.11; current layouts and controls
may differ.

### Dashboard

![Dashboard overview](docs/screenshots/Dashboard_1.13.11.png)

### Blueprint library

![Blueprint library filters and list](docs/screenshots/bp_all_1.13.11.png)

### Blueprint copy requests

![Copy request workflow](docs/screenshots/bp-copy_request_1.13.11.png)

### Material Exchange

![Material exchange overview](docs/screenshots/mat_hub_1.13.11.png)

### Order requests

![Order request details](docs/screenshots/order_request_1.13.11.png)

### Discord notifications

![Discord notification example](docs/screenshots/notif_request_1.13.11.png)

### User settings

![User settings and preferences](docs/screenshots/user_settings_1.13.11.png)

## Support and contributing

Open a GitHub issue or pull request for help, bug reports, or contributions. You
can also contact `erkaek` on Discord.

## Dependency compatibility monitoring

The **Dependency compatibility watch** workflow runs every Monday at 05:23 UTC
and can be launched manually from GitHub Actions after it is merged into the
default branch. It resolves the latest versions allowed by `pyproject.toml`,
including indirect dependencies and the build backend, and tests Python 3.12–3.14
with CharLink plus
separate base/Discord-extra profiles on Python 3.12. Frontend lint/regressions run
against refreshed dependencies allowed by `package.json` on Node 22.

Successful resolved environments become the next comparison baseline. A failure
opens or updates one GitHub issue only when the old environment passes on the
same code and a second candidate run reproduces the failure. Unchanged incidents
do not generate repeated comments or updates. Missing/expired baselines, network
failures and inconclusive results appear in the run summary/artifacts, without
claiming a code regression. An issue closes only after all seven lanes pass.

Dependabot version-update PRs are disabled; security-update PRs remain enabled
for Python, npm and GitHub Actions. Successful Python inventories are submitted
to GitHub's dependency graph, including resolved indirect packages and test tools.
A new version alone is not an actionable compatibility incident. Major versions
outside our supported constraints are not automatically adopted or tested.

Reports/logs are retained for 30 days and passing baselines for 90 days.
Only baselines from this workflow on the default branch are restored.
The first successful run (or a changed dependency contract) initializes a fresh
baseline; after 90 days without a passing run, it must be initialized again. No installed AA instance, real ESI/OAuth, Discord service or production
database is used. Discord extra lanes cover package resolution and the existing
offline Indy Hub tests; they do not qualify a live bot or every third-party app.
Other optional apps not declared as extras, such as Corptools, are outside this
matrix. Detection remains limited by the tests and supported Python/platforms.
