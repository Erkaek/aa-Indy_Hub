# Indy Hub for Alliance Auth

A modern industry management module for [Alliance Auth](https://allianceauth.org/), focused on blueprint and job tracking for EVE Online alliances and corporations.

______________________________________________________________________

## ✨ Features

### Core Features

- **Blueprint Library**: View, filter, and search all your EVE Online blueprints by character, corporation, type, and efficiency.
- **Industry Job Tracking**: Monitor and filter your manufacturing, research, and invention jobs in real time.
- **Blueprint Copy Sharing**: Request, offer, and deliver blueprint copies (BPCs) with collapsible fulfillment cards, inline access list summaries, signed Discord quick-action links, and notifications for each step.
- **Flexible Sharing Scopes**: Expose blueprint libraries per character, per corporation, or to everyone at once.
- **Conditional Offer Chat**: Negotiate blueprint copy terms directly in Indy Hub with persistent history and status tracking.
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
- *(Optional)* aa-discordnotify for Discord notifications

______________________________________________________________________

## Installation & Setup

### 1. Install Dependencies

```bash
pip install django-eveuniverse indy_hub
```

### 2. Configure Alliance Auth Settings

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

### 3. Run Migrations & Collect Static Files

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

### 4. Populate Industry Data

```bash
python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials
```

### 5. Set Permissions

Assign permissions in Alliance Auth:

- `indy_hub.can_access_indy_hub` → Pilots who should access the module
- `indy_hub.can_manage_corporate_assets` → Directors managing corporation data

### 6. Restart Services

```bash
# Restart Alliance Auth
systemctl restart allianceauth
```

______________________________________________________________________

## Configuration (Optional)

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

```bash
# Backup your database
python manage.py dumpdata > backup.json

# Update the package
pip install --upgrade indy_hub

# Apply migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Restart services
systemctl restart allianceauth
systemctl restart allianceauth-celery
systemctl restart allianceauth-celery-beat
```

______________________________________________________________________

## Usage

1. **Navigate** to Indy Hub in the Alliance Auth dashboard
1. **Authorize ESI** for blueprints and jobs via the settings
1. **View Your Data**:

- Personal blueprints and industry jobs
- Corporation blueprints (if director)
- Pending blueprint copy requests

1. **Share Blueprints**: Set sharing scopes and send copy offers to alliance members
1. **Receive Notifications**: View job completions and copy request updates in the notification feed

______________________________________________________________________

## Support & Contributing

- Open an issue or pull request on GitHub for help or to contribute

______________________________________________________________________
