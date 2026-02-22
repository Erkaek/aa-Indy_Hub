# Indy Hub for Alliance Auth

A modern industry and material‑exchange management module for [Alliance Auth](https://allianceauth.org/), focused on blueprint sharing, job tracking, and corp trading workflows for EVE Online alliances and corporations.

______________________________________________________________________

## Table of Contents

- [About](#about)
  - [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Bare Metal](#bare-metal)
  - [Docker](#docker)
  - [Common](#common)
- [Permissions](#permissions)
  - [Base Access (Required for all users)](#base-access-required-for-all-users)
  - [Corporation Management (Optional)](#corporation-management-optional)
  - [Material Exchange Administration (Optional)](#material-exchange-administration-optional)
- [Settings](#settings)
- [Updating](#updating)
- [Usage](#usage)
- [Screenshots](#screenshots)
- [Contributing](#contributing)

______________________________________________________________________

## About

### Features

- **Blueprint Library**: Browse, search, and manage personal and corporation blueprints.
- **Industry Jobs**: Track active and completed manufacturing, research, and invention jobs.
- **Blueprint Copy Requests**: Create requests, receive offers, chat with builders, and follow delivery status.
- **Sharing Controls**: Configure who can see and fulfill blueprint copy requests.
- **Material Exchange**: Submit buy/sell orders and follow validation/processing from one hub.
- **Order Tracking**: View clear statuses, timelines, and history for your requests and orders.
- **Notifications**: Receive in-app updates for key events (offers, deliveries, job updates).
- **Admin Tools**: Manage corp blueprint workflows and Material Exchange operations with dedicated admin views.
- **Modern UI**: Responsive, theme-friendly interface designed for daily operational use.

## Requirements

- **Alliance Auth v4+**
- **Python 3.10+**
- **Django** (as required by AA)
- **Alliance Auth AppUtils**
- **django-esi** (OpenAPI client, >=8)
- **django-eveuniverse** (populated with industry data)
- **Celery** (for background sync and notifications)
- *(Optional)* Director characters for corporate dashboards
- *(Optional)* [`aadiscordbot`](https://apps.allianceauth.org/apps/detail/allianceauth-discordbot) (preferred) or [`discordnotify`](https://apps.allianceauth.org/apps/detail/aa-discordnotify) for Discord notifications

______________________________________________________________________

## Installation

### Bare Metal

```text
pip install django-eveuniverse indy-hub
```

Add to your `local.py`:

```python
INSTALLED_APPS = [
    "eveuniverse",
    "indy_hub",
]

EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
EVEUNIVERSE_LOAD_MARKET_GROUPS = True
```

Run migrations and collect static files:

```text
python manage.py migrate
python manage.py collectstatic --noinput
```

Populate industry data:

```text
python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials
```

Restart services:

```text
systemctl restart allianceauth
```

### Docker

```text
docker compose exec allianceauth_gunicorn bash
pip install django-eveuniverse indy-hub
exit
```

Add to your `conf/local.py`:

```python
INSTALLED_APPS = [
    "eveuniverse",
    "indy_hub",
]

EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
EVEUNIVERSE_LOAD_MARKET_GROUPS = True
```

Add to your `conf/requirements.txt` (Always use current versions)

```text
django-eveuniverse==1.6.0
indy-hub==1.14.5
```

Run migrations and collect static files:

```text
docker compose exec allianceauth_gunicorn bash
auth migrate
auth collectstatic --noinput
exit
```

Restart Auth:

```text
docker compose build
docker compose down
docker compose up -d
```

Populate industry data:

```text
docker compose exec allianceauth_gunicorn bash
auth eveuniverse_load_data types --types-enabled-sections industry_activities type_materials
exit
```

### Common

- Set permissions in Alliance Auth (see [Permissions](#permissions)).
- Authorize ESI tokens for blueprints and industry jobs.

______________________________________________________________________

## Permissions

Assign permissions in Alliance Auth to control access levels:

### Base Access (Required for all users)

- **Visible in admin:** "indy_hub | can access Indy_Hub"
  - View and manage personal blueprints
  - Create and manage blueprint copy requests
  - Use Material Exchange (buy/sell orders)
  - View personal industry jobs
  - Configure personal settings and notifications

### Corporation Management (Optional)

- **Visible in admin:** "indy_hub | can admin Corp"
  - View and manage corporation blueprints (director only)
  - Handle corporation blueprint copy requests (accept/reject corp BP copy sharing)
  - Access corporation industry jobs
  - Configure corporation sharing settings
  - This role is **not** meant for everyone — only for people who manage corp BPs (they can handle contracts for corpmates)
  - Requires ESI director roles for the corporation

### Material Exchange Administration (Optional)

- **Visible in admin:** "indy_hub | can admin MatExchange"
  - Configure Material Exchange settings
  - Manage stock availability
  - View all transactions
  - This role is **not** meant for everyone — only for people who manage the hub (they accept/reject buy and sell orders made to the corp)
  - Admin panel access

**Note**: Permissions are independent and can be combined. Most users only need `can access Indy_Hub`.

______________________________________________________________________

## Settings

Customize Indy Hub behavior in `local.py`:

```python
# Discord notifications
INDY_HUB_DISCORD_DM_ENABLED = True  # Default: True
INDY_HUB_DISCORD_ACTION_TOKEN_MAX_AGE = 86400  # Default: 24 hours

# ESI compatibility date (OpenAPI)
INDY_HUB_ESI_COMPATIBILITY_DATE = "2025-09-30"  # Default: app default

# ESI task staggering (rate-limit friendly scheduling)
INDY_HUB_ESI_TASK_STAGGER_THRESHOLD = 400  # Default: 400
INDY_HUB_ESI_TASK_TARGET_PER_MIN_BLUEPRINTS = 30  # Default: 30
INDY_HUB_ESI_TASK_TARGET_PER_MIN_JOBS = 30  # Default: 30
INDY_HUB_ESI_TASK_TARGET_PER_MIN_SKILLS = 40  # Default: 40
INDY_HUB_ESI_TASK_TARGET_PER_MIN_ROLES = 30  # Default: 30

# Stale refresh thresholds (hours)
INDY_HUB_ONLINE_STATUS_STALE_HOURS = 72  # Default: 72
INDY_HUB_SKILL_SNAPSHOT_STALE_HOURS = 24  # Default: 24
INDY_HUB_ROLE_SNAPSHOT_STALE_HOURS = 24  # Default: 24
INDY_HUB_STRUCTURE_NAME_STALE_HOURS = 24  # Default: 24
```

**Scheduled Tasks** (auto-created):

- `indy-hub-update-all-blueprints` → Daily at 03:30 UTC
- `indy-hub-update-all-industry-jobs` → Every 2 hours
- `indy-hub-refresh-stale-snapshots` → Hourly (skills/roles/online/structures)

______________________________________________________________________

## Updating

### Bare Metal Update

```text
# Update the package
pip install --upgrade indy-hub

# Apply migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Restart services
systemctl restart allianceauth
```

### Docker Update

Update Versions in `conf/requirements.txt` (Always use current versions)

```text
indy-hub==1.14.5


```

Update the Package:

```text
# Exec Into the Container
docker compose exec allianceauth_gunicorn bash

# Update the package
pip install -U indy-hub

# Apply Migrations
auth migrate

# Collect static files
auth collectstatic --no-input

# Restart Services
exit
docker compose build
docker compose down
docker compose up -d
```

If Celery runs in dedicated containers/services in your stack, also restart worker and beat/scheduler containers.

______________________________________________________________________

## Usage

1. **Navigate** to Indy Hub in the Alliance Auth dashboard
1. **Authorize ESI** for blueprints and jobs via the settings
1. **View Your Data**:

- Personal blueprints and industry jobs
- Corporation blueprints (if director)
- Pending blueprint copy requests
- Material Exchange buy/sell orders and transaction history

1. **Share Blueprints**: Set sharing scopes and send copy offers to alliance members
1. **Receive Notifications**: View job completions and copy request updates in the notification feed

______________________________________________________________________

## Screenshots

Below are a few UI highlights from the current release.

### Dashboard Overview

![Dashboard overview](docs/screenshots/Dashboard_1.13.11.png)

### Blueprint Library

![Blueprint library filters and list](docs/screenshots/bp_all_1.13.11.png)

### Blueprint Copy Requests

![Copy request workflow](docs/screenshots/bp-copy_request_1.13.11.png)

### Material Exchange Hub

![Material exchange overview](docs/screenshots/mat_hub_1.13.11.png)

### Order Requests

![Order request details](docs/screenshots/order_request_1.13.11.png)

### Discord Notifications

![Discord notification example](docs/screenshots/notif_request_1.13.11.png)

### User Settings

![User settings and preferences](docs/screenshots/user_settings_1.13.11.png)

______________________________________________________________________

## Contributing

- Open an issue or pull request on GitHub for help or to contribute
  Or contact me on discord: `erkaek`

______________________________________________________________________
