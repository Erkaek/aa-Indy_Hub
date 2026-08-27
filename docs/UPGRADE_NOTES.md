# Indy Hub — Upgrade Notes

This guide lists the actions required to upgrade to Indy Hub 1.18.3. You only
need basic Alliance Auth administration commands; no manual database editing is
required.

> Indy Hub 1.18.3 requires Alliance Auth 5.

## Standard upgrade

Run this cycle once after completing any extra steps for your current version:

```bash
pip install --upgrade indy-hub
python manage.py migrate
python manage.py collectstatic --noinput
# Restart gunicorn, Celery Beat, and Celery workers.
```

For Docker installations, run the equivalent commands in the Alliance Auth
gunicorn container. Depending on your stack, the Django command may be named
`auth` instead of `python manage.py`.

Version 1.18.3 automatically restores missing background schedules during the
normal upgrade and restart. No additional Indy Hub command is required.

If your installation previously used manual file merges and an upgrade leaves
old files behind, follow [PROCESS_RESOLUTION_INSTALL.md](PROCESS_RESOLUTION_INSTALL.md).

## Choose your current version

Complete the section matching the version currently installed, then run the
standard upgrade once.

| Current version   | Instructions                                              |
| ----------------- | --------------------------------------------------------- |
| `1.18.2`          | [Upgrade from 1.18.2](#upgrade-from-1182)                 |
| `1.17.x`          | [Upgrade from 1.17.x](#upgrade-from-117x)                 |
| `1.16.x`          | [Upgrade from 1.16.x](#upgrade-from-116x)                 |
| `1.15.x`          | [Upgrade from 1.15.x](#upgrade-from-115x)                 |
| `1.14.x`          | [Upgrade from 1.14.x](#upgrade-from-114x)                 |
| `1.13.x`          | [Upgrade from 1.13.x](#upgrade-from-113x)                 |
| `1.12.x`          | [Upgrade from 1.12.x](#upgrade-from-112x)                 |
| `1.11.x`          | [Upgrade from 1.11.x](#upgrade-from-111x)                 |
| `1.10.x`          | [Upgrade from 1.10.x](#upgrade-from-110x)                 |
| `1.9.x` and older | [Upgrade from 1.9.x or older](#upgrade-from-19x-or-older) |

## Upgrade from 1.18.2

There are no extra commands. Run the standard upgrade.

After restart, the new administration statistics are prepared automatically
and may take a few minutes to appear.

Personal crafting projects continue to use personal blueprints only. If you
want users to be able to choose authorized corporation blueprints as a fallback,
add this optional setting to `local.py`:

```python
INDY_HUB_PERSONAL_PROJECTS_ALLOW_CORP_BP = True
```

Material Exchange administrators can now set a fixed member-sale price for an
exact item when its Jita market data is incomplete. This is available directly
in `Material Exchange → Settings`; no server setting is needed.

The Structure Registry now explains which activities to review when an NPC
Station is added or edited. Existing saved activities are not changed.

## Upgrade from 1.17.x

Before upgrading:

- Remove any automation that runs `sync_sde_compat` or `indy_sde_compat`; these
  commands are no longer used.
- If another Alliance Auth service already forwards notifications to Discord,
  set `INDY_HUB_NOTIFICATION_DISPATCH_MODE = "aa_only"` in `local.py` to avoid
  duplicate messages.

Then run the standard upgrade. Existing users do not need to re-link characters
for the removed online-location scope.

## Upgrade from 1.16.x

Before running `migrate`, stop Celery Beat and Celery workers. Keep them stopped
until the standard upgrade is complete, then restart all Alliance Auth services.

Optional: if users work with very large catalogues or crafting projects, review
`INDY_HUB_MAX_FORM_FIELDS` and `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

## Upgrade from 1.15.x

Before upgrading:

- Back up the database.
- Stop Celery Beat and Celery workers until the upgrade is complete.

After upgrading:

- Update bookmarks that still use old `simulation*` pages; those pages are no
  longer available.
- Assign the Industry Structures and Crafting Projects permissions to the
  appropriate groups in Django Admin.

## Upgrade from 1.14.x

Before upgrading:

- Back up the database.

- Install the current SDE backend:

  ```bash
  pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
  ```

- Stop Celery Beat and Celery workers until the upgrade is complete.

After upgrading:

- Update bookmarks that still use old `simulation*` pages.
- Assign the Industry Structures and Crafting Projects permissions to the
  appropriate groups in Django Admin.
- Optionally uninstall `django-eveuniverse` after confirming that no other
  Alliance Auth application uses it.

## Upgrade from 1.13.x

Before upgrading:

- Back up the database.

- If you use direct Discord messages, install one supported provider:

  ```bash
  pip install "indy-hub[aadiscordbot]"
  # or
  pip install "indy-hub[discordnotify]"
  ```

- Install the current SDE backend:

  ```bash
  pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
  ```

- Stop Celery Beat and Celery workers until the upgrade is complete.

After upgrading:

- Enable Material Exchange in `Material Exchange → Settings` if you use it.
- Ask corporation directors using Material Exchange to re-link their corporation
  tokens for the division and corporation-contract scopes.
- Update bookmarks that still use old `simulation*` pages.
- Assign the Industry Structures and Crafting Projects permissions to the
  appropriate groups in Django Admin.
- Optionally configure Discord notification webhooks in
  `Django Admin → Indy Hub → Notification Webhooks`.

## Upgrade from 1.12.x

Follow all steps in [Upgrade from 1.13.x](#upgrade-from-113x).

Before upgrading, also notify Material Exchange users that contract titles must
contain the order reference, for example `INDY-123`. Existing open contracts
without a reference should be closed or corrected.

## Upgrade from 1.11.x

Follow all steps in [Upgrade from 1.12.x](#upgrade-from-112x). There are no
additional steps specific to 1.11.x.

## Upgrade from 1.10.x

Follow all steps in [Upgrade from 1.12.x](#upgrade-from-112x).

After upgrading, also:

- Assign the corporation asset and blueprint-copy management permissions to the
  appropriate groups in Django Admin.
- Ask corporation directors to re-link their tokens so Indy Hub can validate
  corporation roles and Material Exchange access.
- Optionally configure corporation token allow-lists in Token Management.

## Upgrade from 1.9.x or older

Follow all steps in [Upgrade from 1.10.x](#upgrade-from-110x). The standard
service restart also reloads all current background schedules; no separate
schedule repair is required.

## After the upgrade

Check that:

- the Indy Hub pages open normally;
- gunicorn, Celery Beat, and Celery workers are running;
- Material Exchange is enabled if your installation uses it;
- the expected groups still have access to Industry Structures and Crafting
  Projects.

For a user-facing summary of changes, see [CHANGELOG.md](../CHANGELOG.md).
