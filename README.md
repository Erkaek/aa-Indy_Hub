# 🚧 IN ACTIVE DEVELOPPEMENT / DO NOT USE 🚧

# Indy Hub for Alliance Auth

A comprehensive industry management module for [Alliance Auth](https://allianceauth.org/), designed to streamline blueprint management, job tracking, and industrial collaboration for EVE Online alliances and corporations.

______________________________________________________________________

## Features

- **Blueprint Management**: Track, share, and request blueprint copies (BPCs) within your alliance.
- **Industry Job Tracking**: View, filter, and manage all your EVE Online industry jobs in one place.
- **Copy Sharing & Requests**: Members can offer, request, and deliver blueprint copies, with notifications for all steps.
- **ESI Integration**: Automatic synchronization of blueprints and jobs using ESI (EVE Swagger Interface).
- **Notifications**: In-app notifications for job completions, copy offers, and requests. Optional Discord notifications via [aa-discordnotify](https://apps.allianceauth.org/apps/detail/aa-discordnotify).
- **Celery Tasks**: Periodic background updates for ESI data and job status.
- **Admin Tools**: Management commands for ESI cache and status, and admin dashboard for oversight.
- **Modern UI**: Responsive dashboard and user flows (screenshots section below).

______________________________________________________________________

## Requirements

- **Alliance Auth**: v4.0 or higher
- **Python**: 3.10+
- **Django**: As required by your AA version
- **django-eveuniverse**: Must be installed and populated (see below)
- **Celery**: For background tasks
- **Optional**: [aa-discordnotify](https://apps.allianceauth.org/apps/detail/aa-discordnotify) for Discord notifications

______________________________________________________________________

## Installation

1. **Install the app**

```bash
pip install django-eveuniverse
# (or add to your requirements.txt)
```

2. **Add to `INSTALLED_APPS` in your AA settings:**

```python
INSTALLED_APPS = [
    # ...existing apps...
    "eveuniverse",
    "indy_hub",
]
```

3. **Configure EveUniverse for industry data**

Add these lines to your `local.py` before running the data load command:

```python
EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
EVEUNIVERSE_LOAD_INDUSTRY_ACTIVITIES = True
```

4. **Populate EveUniverse with industry data**

```bash
python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials
```

5. **Migrate database**

```bash
python manage.py migrate
```

6. **(Optional) Enable Discord notifications**

Install and configure [aa-discordnotify](https://apps.allianceauth.org/apps/detail/aa-discordnotify) if you want Discord notifications for industry events.

7. **Collect static files**

```bash
python manage.py collectstatic
```

______________________________________________________________________

## Configuration

- No special configuration is required beyond the steps above.
- All ESI scopes and settings are managed via Alliance Auth.
- For Discord notifications, follow the aa-discordnotify setup guide.

______________________________________________________________________

## Permissions

- Only one permission is required: `can access indy_hub`.
- Assign this permission to the groups/users who should access the module.
- (Note: The app models will be updated to use only this permission.)

______________________________________________________________________

## Usage

### User

- View your blueprints and industry jobs on the dashboard.
- Request blueprint copies from other members.
- Offer and deliver blueprint copies.
- Receive notifications for job completions and copy transactions.

### Admin

- Access the admin dashboard for oversight of all blueprints, jobs, and copy requests.
- Use management commands to refresh ESI data and check ESI status.

______________________________________________________________________

## Notifications

- In-app notifications for:
  - Job completions
  - Blueprint copy offers and requests
  - Delivery of copies
- Optional Discord notifications (requires aa-discordnotify)

______________________________________________________________________

## ESI Integration

- Uses ESI to synchronize blueprints and industry jobs for all linked characters.
- Requires appropriate ESI scopes (handled by Alliance Auth).
- ESI data is cached and periodically refreshed via Celery tasks.

______________________________________________________________________

## Celery & Periodic Tasks

- Background tasks update ESI data and job statuses automatically.
- Ensure Celery is running for timely updates.

______________________________________________________________________

## Templates & Static Files

- Custom templates for dashboards, job lists, and copy management.
- Static files (JS, CSS) for interactive UI features.

______________________________________________________________________

## Management Commands

- `python manage.py cache_esi_data` — Manually refresh ESI data cache.
- `python manage.py esi_status` — Check ESI integration status.

______________________________________________________________________

## Screenshots

*Screenshots of the dashboard, job list, and copy management will be added here.*

______________________________________________________________________

## Updating

- Pull the latest version and run migrations as needed.
- Re-run EveUniverse data load if new industry types are added.

______________________________________________________________________

## Contributing

Direct contributions are welcome! Please open a pull request on GitHub.

______________________________________________________________________

## Support

For support, open an issue on GitHub.

______________________________________________________________________

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
