# Indy Hub for Alliance Auth

A modern industry management module for [Alliance Auth](https://allianceauth.org/), focused on blueprint and job tracking for EVE Online alliances and corporations.

______________________________________________________________________

## ✨ Features (Current)

- **Blueprint Library**: View, filter, and search all your EVE Online blueprints by character, corporation, type, and efficiency.
- **Industry Job Tracking**: Monitor and filter your manufacturing, research, and invention jobs in real time.
- **Blueprint Copy Sharing**: Request, offer, and deliver blueprint copies (BPCs) within your alliance, complete with signed Discord quick-action links, auto-launching chats for conditional offers, and notifications for each step.
- **Flexible Sharing Scopes**: Expose blueprint libraries per character, per corporation, or to everyone at once, all controlled from the sharing dashboard and allow-list toggles.
- **Conditional Offer Chat**: Negotiate blueprint copy terms directly in Indy Hub with persistent history, status indicators, and decision tracking.
- **Corporate Command Center**: Track corporation blueprints and jobs, configure sharing scopes, and review director token coverage from a dedicated dashboard.
- **ESI Integration**: Secure OAuth2-based sync for blueprints and jobs (Celery required), including director-level corporation scopes and staggered background refreshes.
- **Notifications**: In-app alerts for job completions, copy offers, chat messages, and deliveries, featuring rich job completion digests with activity-aware thumbnails, optional Discord embeds (via aa-discordnotify), dedicated preview endpoints for both live and digest payloads, and an Alliance Auth sidebar badge that stays in sync with outstanding fulfil requests and unread chats.
- **Modern UI**: Responsive Bootstrap 5 interface, theme-compatible, with accessibility and i18n support.

______________________________________________________________________

## 🚧 In Development

- **Alliance-wide Blueprint Library**: Browse all blueprints available in the alliance (admin-controlled visibility).
- **Advanced Copy Request Fulfillment**: Streamlined workflows for fulfilling and tracking copy requests.
- **Improved Job Analytics**: More detailed job statistics, filtering, and export options.
- **Better Admin Tools**: Enhanced dashboards and management commands for admins.

______________________________________________________________________

## 🛣️ Planned / Coming Soon

- **Blueprint Lending/Loan System**: Track and manage temporary blueprint loans between members.
- **Production Cost Estimation**: Integrated cost calculators and market price lookups.
- **More ESI Scopes**: Support for additional ESI endpoints (e.g., assets, wallet, reactions).
- **API/Export**: Public API endpoints and improved CSV/Excel export for all lists.
- **More Notifications**: Customizable notification rules and Discord webhooks.

______________________________________________________________________

## Requirements

- Alliance Auth v4+
- Python 3.10+
- Django (as required by AA)
- django-eveuniverse (populated with industry data)
- Celery (for background sync)
- (Optional) Director characters with `esi-corporations.read_blueprints.v1`, `esi-industry.read_corporation_jobs.v1`, and `esi-characters.read_corporation_roles.v1` to unlock corporate dashboards.
- (Optional) aa-discordnotify for Discord alerts

______________________________________________________________________

## Quick Install

1. `pip install django-eveuniverse` and `pip install indy_hub`

1. Add `eveuniverse` and `indy_hub` to `INSTALLED_APPS` in your AA settings.

1. Add to your `local.py`:

- `EVEUNIVERSE_LOAD_TYPE_MATERIALS = True`
- `EVEUNIVERSE_LOAD_MARKET_GROUPS = True`

1. Run migrations: `python manage.py migrate`

1. Collect static files: `python manage.py collectstatic`

1. Restart your auth.

1. Populate EveUniverse with industry data `python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials`.

1. Assign the `can access indy_hub` permission to pilots, and grant `can_manage_corporate_assets` to directors who should manage corporation data.

______________________________________________________________________

## Configuration

These settings are optional and let you tune background behaviour:

- `INDY_HUB_DISCORD_DM_ENABLED` (bool, default: `True`): enable Discord DM notifications via `aadiscordbot`, falling back to `discordnotify` when available.
- `INDY_HUB_MANUAL_REFRESH_COOLDOWN_SECONDS` (int, default: `3600`): minimum delay (in seconds) before the same user can trigger another manual sync for blueprints or jobs.
- `INDY_HUB_BULK_UPDATE_WINDOW_MINUTES` (int, default: `720`): maximum window (in minutes) used to stagger large background synchronisations. You can further refine the cadence with:
  - `INDY_HUB_BLUEPRINTS_BULK_WINDOW_MINUTES` (default: `720`, twelve hours).
  - `INDY_HUB_INDUSTRY_JOBS_BULK_WINDOW_MINUTES` (default: `120`, two hours).
- Blueprint copy sharing scopes are configured in the Indy Hub UI. Character settings and corporation dashboards now provide an "Everyone" option alongside character/corporation allow-lists so you can expose libraries broadly without manual maintenance.

Scheduled tasks are automatically created or updated on startup:

- `indy-hub-update-all-blueprints` runs daily at 03:00 UTC and spreads user refreshes across the configured window.
- `indy-hub-update-all-industry-jobs` runs every two hours and staggers its workload across the job window.

After upgrading, restart your Celery workers and Celery Beat to apply the new schedule.

- Assign the `indy_hub.can_manage_corporate_assets` permission to directors who should access the corporation dashboard and manage cross-corporation sharing.
- The corporation dashboard now exposes per-corporation copy sharing settings backed by `CorporationSharingSetting`.

______________________________________________________________________

## Usage

- Go to the Indy Hub dashboard in Alliance Auth.
- Authorize ESI for blueprints and jobs.
- View/manage your blueprints and jobs, request/offer BPCs, and receive notifications.
- Use `/indy_hub/personnal-jobs/notification_test/` or the live/digest preview endpoints under `/indy_hub/personnal-jobs/notifications/preview/` to validate job completion payloads before enabling production Discord routing.

______________________________________________________________________

## Support & Contributing

- Open an issue or pull request on GitHub for help or to contribute.

______________________________________________________________________

## License

MIT License. See [LICENSE](LICENSE) for details.
