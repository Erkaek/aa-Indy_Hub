# Indy Hub for Alliance Auth

A modern industry management module for [Alliance Auth](https://allianceauth.org/), focused on blueprint and job tracking for EVE Online alliances and corporations.

______________________________________________________________________

## ✨ Features (Current)

- **Blueprint Library**: View, filter, and search all your EVE Online blueprints by character, type, and efficiency.
- **Industry Job Tracking**: Monitor and filter your manufacturing, research, and invention jobs in real time.
- **Blueprint Copy Sharing**: Request, offer, and deliver blueprint copies (BPCs) within your alliance, with notifications for each step.
- **ESI Integration**: Secure OAuth2-based sync for blueprints and jobs, with periodic background updates (Celery required).
- **Notifications**: In-app alerts for job completions, copy offers, and deliveries. Optional Discord notifications (via aa-discordnotify).
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
- (Optional) aa-discordnotify for Discord alerts

______________________________________________________________________

## Quick Install

1. Add `eveuniverse` and `indy_hub` to `INSTALLED_APPS` in your AA settings.

1. Run migrations: `python manage.py migrate`

1. Populate EveUniverse with industry data `python manage.py eveuniverse_load_data types --types-enabled-sections industry_activities type_materials`.

1. Collect static files: `python manage.py collectstatic`

1. Restart your auth.

1. Assign the `can access indy_hub` permission to users/groups.

______________________________________________________________________

## Usage

- Go to the Indy Hub dashboard in Alliance Auth.
- Authorize ESI for blueprints and jobs.
- View/manage your blueprints and jobs, request/offer BPCs, and receive notifications.

______________________________________________________________________

## Support & Contributing

- Open an issue or pull request on GitHub for help or to contribute.

______________________________________________________________________

## License

MIT License. See [LICENSE](LICENSE) for details.
