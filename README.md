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
- [Contributing](#contributing)

______________________________________________________________________

## About

### Features

- **Blueprint Library**: View, filter, and search all your EVE Online blueprints by character, corporation, type, and efficiency.
- **Industry Job Tracking**: Monitor and filter your manufacturing, research, and invention jobs in real time.
- **Blueprint Copy Sharing**: Request, offer, and deliver blueprint copies (BPCs) with collapsible fulfillment cards, inline access list summaries, signed Discord quick-action links, and notifications for each step.
- **Flexible Sharing Scopes**: Expose blueprint libraries per character, per corporation, or to everyone at once.
- **Conditional Offer Chat**: Negotiate blueprint copy terms directly in Indy Hub with persistent history and status tracking.
- **Material Exchange**: Create buy/sell orders with order references, validate ESI contracts, and review transaction history.
- **Material Exchange UX**: Compact order detail headers with quick-copy helpers (order reference, buyer/corporation, EVE-friendly totals).
- **ESI Integration**: Secure OAuth2-based sync for blueprints and jobs with director-level corporation scopes.
- **Notifications**: In-app alerts for job completions, copy offers, chat messages, and deliveries, with configurable immediate or digest cadences.
- **Modern UI**: Responsive Bootstrap 5 interface with theme compatibility and full i18n support.

______________________________________________________________________

## Requirements

- **Alliance Auth v4+**
- **Python 3.10+**
- **Django** (as required by AA)
- **django-eveuniverse** (populated with industry data)
- **Celery** (for background sync and notifications)
- *(Optional)* Director characters for corporate dashboards
- *(Optional)* aadiscordbot (recommended) or discordnotify for Discord notifications

______________________________________________________________________

## Installation

### Bare Metal

```bash
pip install django-eveuniverse indy-hub
```

Add to your `local.py`:

```python
# Add to INSTALLED_APPS
INSTALLED_APPS = [
    "eveuniverse",
    "indy_hub",
]

# EveUniverse configuration
EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
EVEUNIVERSE_LOAD_MARKET_GROUPS = True
```

Run migrations and collect static files:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

Populate industry data:

```bash
python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials
```

Restart services:

```bash
# Restart Alliance Auth
systemctl restart allianceauth
```

### Docker

```bash
docker compose exec allianceauth_gunicorn bash
pip install django-eveuniverse indy-hub
exit
```

Add to your `conf/local.py`:

```python
# Add to INSTALLED_APPS
INSTALLED_APPS = [
    "eveuniverse",
    "indy_hub",
]

# EveUniverse configuration
EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
EVEUNIVERSE_LOAD_MARKET_GROUPS = True
```

Add to your `conf/requirements.txt` (Always use current versions)

```bash
django-eveuniverse==1.6.0
indy-hub==1.13.9
```

Run migrations and collect static files:

```bash
docker compose exec allianceauth_gunicorn bash
auth migrate
auth collectstatic --noinput
exit
```

Restart Auth:

```bash
docker compose build
docker compose down
docker compose up -d
```

Populate industry data:

```bash
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

- **`indy_hub.can_access_indy_hub`** → "Can access Indy Hub"
  - View and manage personal blueprints
  - Create and manage blueprint copy requests
  - Use Material Exchange (buy/sell orders)
  - View personal industry jobs
  - Configure personal settings and notifications

### Corporation Management (Optional)

- **`indy_hub.can_manage_corp_bp_requests`** → "Can manage corporation indy"
  - View and manage corporation blueprints (director only)
  - Handle corporation blueprint copy requests (accept/reject corp BP copy sharing)
  - Access corporation industry jobs
  - Configure corporation sharing settings
  - This role is **not** meant for everyone — only for people who manage corp BPs (they can handle contracts for corpmates)
  - Requires ESI director roles for the corporation

### Material Exchange Administration (Optional)

- **`indy_hub.can_manage_material_hub`** → "Can manage Mat Exchange"
  - Configure Material Exchange settings
  - Manage stock availability
  - View all transactions
  - This role is **not** meant for everyone — only for people who manage the hub (they accept/reject buy and sell orders made to the corp)
  - Admin panel access

**Note**: Permissions are independent and can be combined. Most users only need `can_access_indy_hub`.

______________________________________________________________________

## Settings

Customize Indy Hub behavior in `local.py`:

```python
# Discord notifications
INDY_HUB_DISCORD_DM_ENABLED = True  # Default: True

# Manual refresh cooldown (seconds between user refreshes)
INDY_HUB_MANUAL_REFRESH_COOLDOWN_SECONDS = 3600  # Default: 1 hour

# Background sync windows (minutes)
INDY_HUB_BLUEPRINTS_BULK_WINDOW_MINUTES = 720  # Default: 12 hours
INDY_HUB_INDUSTRY_JOBS_BULK_WINDOW_MINUTES = 120  # Default: 2 hours
```

**Scheduled Tasks** (auto-created):

- `indy-hub-update-all-blueprints` → Daily at 03:00 UTC
- `indy-hub-update-all-industry-jobs` → Every 2 hours

______________________________________________________________________

## Updating

### Bare Metal Update

```bash
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

```bash
indy-hub==1.13.9
```

Update the Package:

```bash
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

## Contributing

- Open an issue or pull request on GitHub for help or to contribute

______________________________________________________________________
