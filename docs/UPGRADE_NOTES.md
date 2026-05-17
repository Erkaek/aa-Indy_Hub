# Indy Hub — Upgrade Notes

Per-version **extra** steps to run **in addition to** the standard cycle:

```
pip install --upgrade indy-hub
python manage.py migrate
python manage.py collectstatic --noinput
# restart Alliance Auth (gunicorn + celery beat + celery workers)
```

Docker equivalent: prefix commands with `docker compose exec allianceauth_gunicorn auth …`
(or `bash -c "…"` when chaining with `pip`).

> Indy Hub 1.17 supports **both Alliance Auth 4 and 5** from the same package.
> `pip install --upgrade indy-hub` does **not** declare `allianceauth`, `django-esi` or `django-eveonline-sde` as runtime dependencies anymore, so an AA4 install stays on AA4 and an AA5 install stays on AA5.
> The `[aa4]` / `[aa5]` extras only exist for **fresh installs** where pip needs to resolve a full stack — never use them on an existing deployment.

---

## How to use this document

1. Find the section that matches **your current Indy Hub version** below.
2. Run the steps **in order, top to bottom** — every section is cumulative and includes all intermediate releases up to 1.17.0.
3. The standard cycle (`pip install` / `migrate` / `collectstatic` / restart) is run **once at the end** of the merged step list, unless an intermediate step explicitly says otherwise (e.g. "stop workers before `migrate`").

Quick map:

| Current version | Section |
|---|---|
| `1.16.x` | [Upgrading from 1.16.x to 1.17.0](#upgrading-from-116x-to-1170) |
| `1.15.x` | [Upgrading from 1.15.x to 1.17.0](#upgrading-from-115x-to-1170) |
| `1.14.x` | [Upgrading from 1.14.x to 1.17.0](#upgrading-from-114x-to-1170) |
| `1.13.x` | [Upgrading from 1.13.x to 1.17.0](#upgrading-from-113x-to-1170) |
| `1.12.x` | [Upgrading from 1.12.x to 1.17.0](#upgrading-from-112x-to-1170) |
| `1.11.x` | [Upgrading from 1.11.x to 1.17.0](#upgrading-from-111x-to-1170) |
| `1.10.x` | [Upgrading from 1.10.x to 1.17.0](#upgrading-from-110x-to-1170) |
| `1.9.x` and older | [Upgrading from 1.9.x to 1.17.0](#upgrading-from-19x-to-1170) |

---

## Upgrading from 1.16.x to 1.17.0

1. **Before `migrate`** — stop celery beat and workers. Migration `0100_repair_blueprint_bp_type_classification` rewrites `bp_type` for legacy BPO/BPC rows and can take a few minutes on a large fleet.
2. `pip install --upgrade indy-hub`
3. `python manage.py migrate`
4. `python manage.py collectstatic --noinput`
5. `python manage.py sync_sde_compat`
6. Restart gunicorn + celery beat + workers.
7. Ask users to re-link their characters via CharLink or Token Management to grant the new `esi-location.read_online.v1` scope (additive — existing tokens keep working otherwise). The Indy Hub navbar badge now increments by **one per character** that is linked but missing at least one required personal scope, so affected pilots will see the warning immediately.
8. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` (default `50000`) and `INDY_HUB_MAX_REQUEST_BODY_BYTES` (default 50 MB) in `local.py` if your users push very large catalogues / craft workspaces.

---

## Upgrading from 1.15.x to 1.17.0

This path crosses the 1.16.0 Crafting Projects rewrite.

1. **Backup the database** (the legacy simulation → project data migration is reversible but slow).
2. Stop celery beat and workers (data migrations `0096` for 1.16 and `0100` for 1.17 both run during the same `migrate`).
3. `pip install --upgrade indy-hub`
4. `python manage.py migrate`
5. `python manage.py collectstatic --noinput`
6. `python manage.py sync_sde_compat`
7. Restart gunicorn + celery beat + workers.
8. Update internal links / bookmarks: deprecated `simulation*` endpoints now return `410 Gone`.
9. In `Django Admin`, assign the new Industry Structures / Crafting Project permissions to the relevant groups.
10. Ask users to re-link their characters to grant the new `esi-location.read_online.v1` scope.
11. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

---

## Upgrading from 1.14.x to 1.17.0

This path crosses the 1.15.0 SDE backend swap and the 1.16.0 Crafting Projects rewrite.

1. **Backup the database.**
2. **Install the new SDE backend first**:
    ```
    pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
    ```
3. Stop celery beat and workers.
4. `pip install --upgrade indy-hub`
5. `python manage.py migrate` (chains the SDE compat tables, periodic-task bootstrap, project migration `0096`, structure constraint cleanup, and BPC repair `0100`).
6. `python manage.py collectstatic --noinput`
7. `python manage.py sync_sde_compat` (mandatory — pages otherwise display "SDE not ready").
8. Restart gunicorn + celery beat + workers.
9. Update internal links / bookmarks: deprecated `simulation*` endpoints now return `410 Gone`.
10. In `Django Admin`, assign the new Industry Structures / Crafting Project permissions to the relevant groups.
11. Ask users to re-link their characters to grant the new `esi-location.read_online.v1` scope.
12. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.
13. (Optional) `django-eveuniverse` is no longer used by Indy Hub — uninstall once you confirm no other AA module depends on it.

---

## Upgrading from 1.13.x to 1.17.0

This path crosses the 1.14.0 Material Exchange refactor, the 1.15.0 SDE backend swap and the 1.16.0 Crafting Projects rewrite.

1. **Backup the database.**
2. If you rely on Discord DMs, make sure one of the providers is still installed (they became optional extras in 1.13.12):
    - `pip install "indy-hub[aadiscordbot]"` *or* `pip install "indy-hub[discordnotify]"`.
3. Install the new SDE backend (introduced in 1.15.0):
    ```
    pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
    ```
4. Stop celery beat and workers.
5. `pip install --upgrade indy-hub`
6. `python manage.py migrate`
7. `python manage.py collectstatic --noinput`
8. `python manage.py sync_sde_compat`
9. Restart gunicorn + celery beat + workers (mandatory beat restart — new schedules from `0083`, plus the updated 1.10.2 timers if you skipped them earlier).
10. In `Material Exchange → Settings`, tick `enabled` to turn the module on (disabled by default since 1.14.0).
11. Ask corporation directors using Material Exchange to re-link their corp tokens. New scopes since 1.14.0: `esi-corporations.read_divisions.v1`, `esi-contracts.read_corporation_contracts.v1`. Removed: corp wallet scope.
12. Update internal links / bookmarks: deprecated `simulation*` endpoints now return `410 Gone`.
13. In `Django Admin`, assign the new Industry Structures / Crafting Project permissions to the relevant groups.
14. Ask users to re-link their characters to grant the new `esi-location.read_online.v1` scope.
15. (Optional) Configure Discord notification webhooks in `Django Admin → Indy Hub → Notification Webhooks` (introduced in 1.13.4).
16. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

---

## Upgrading from 1.12.x to 1.17.0

This path crosses the 1.13.0 Material Exchange order-reference rule, then all of the 1.13.x → 1.17.0 steps.

1. **Backup the database.**
2. Notify Material Exchange users in advance: since 1.13.0, ESI contract titles **must** include the order reference (e.g. `INDY-123`). Open contracts created before the upgrade should be closed or amended.
3. If you rely on Discord DMs, install the right provider extra (optional since 1.13.12):
    - `pip install "indy-hub[aadiscordbot]"` *or* `pip install "indy-hub[discordnotify]"`.
4. Install the new SDE backend (1.15.0):
    ```
    pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
    ```
5. Stop celery beat and workers.
6. `pip install --upgrade indy-hub`
7. `python manage.py migrate`
8. `python manage.py collectstatic --noinput`
9. `python manage.py sync_sde_compat`
10. Restart gunicorn + celery beat + workers.
11. (Optional) Configure Discord notification webhooks in `Django Admin → Indy Hub → Notification Webhooks` (1.13.4).
12. In `Material Exchange → Settings`, tick `enabled`.
13. Ask corp directors using Material Exchange to re-link their corp tokens (new scopes from 1.14.0).
14. Update bookmarks: deprecated `simulation*` endpoints now return `410 Gone`.
15. Assign the new Industry Structures / Crafting Project permissions in `Django Admin`.
16. Ask users to re-link their characters for `esi-location.read_online.v1`.
17. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

---

## Upgrading from 1.11.x to 1.17.0

The 1.11→1.12 step itself has no extra requirement, so this path matches the 1.12.x section.

Apply the steps from [Upgrading from 1.12.x to 1.17.0](#upgrading-from-112x-to-1170).

---

## Upgrading from 1.10.x to 1.17.0

This path crosses the 1.11.0 corporation blueprints / permissions overhaul.

1. **Backup the database.**
2. Same advance communication as above (Material Exchange order references since 1.13.0).
3. Install the Discord provider extra if needed, and the new SDE backend (1.15.0):
    ```
    pip install "indy-hub[aadiscordbot]"   # or [discordnotify], optional
    pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
    ```
4. Stop celery beat and workers.
5. `pip install --upgrade indy-hub`
6. `python manage.py migrate` (backfills `owner_kind` on blueprints/jobs from the 1.11.0 migration, then chains 1.13–1.17 migrations).
7. `python manage.py collectstatic --noinput`
8. `python manage.py sync_sde_compat`
9. Restart gunicorn + celery beat + workers.
10. In `Django Admin → Auth → Groups` (or per user) assign the new Indy Hub permissions introduced in 1.11.0:
    - `can_manage_corporate_assets`
    - copy-manager / corporate-director permissions (see README for the full mapping).
11. Ask corporation directors to re-link their tokens — since 1.11.0 the corp roles scope is validated up front and incomplete tokens are rejected.
12. (Optional) Configure corp token allow-lists in Token Management.
13. In `Material Exchange → Settings`, tick `enabled`.
14. Ask corp directors using Material Exchange to re-link again if scopes were rejected (1.14.0 scope changes).
15. (Optional) Configure Discord notification webhooks (1.13.4).
16. Update bookmarks: `simulation*` endpoints return `410 Gone`.
17. Assign the new Industry Structures / Crafting Project permissions in `Django Admin`.
18. Ask users to re-link their characters for `esi-location.read_online.v1`.
19. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

---

## Upgrading from 1.9.x to 1.17.0

Same as [Upgrading from 1.10.x to 1.17.0](#upgrading-from-110x-to-1170), with one addition: the 1.10.2 release rewrote the existing `PeriodicTask` rows (daily bulk blueprint sync at 03:00 UTC, jobs every 2 h, with staggering). The chained `migrate` applies that change too — **just make sure celery beat is restarted after the upgrade** so it reloads the rewritten schedules.

Steps:

1. **Backup the database.**
2. Same advance communications (Material Exchange order references from 1.13.0).
3. Optional Discord provider extra + new SDE backend (1.15.0):
    ```
    pip install "indy-hub[aadiscordbot]"   # or [discordnotify], optional
    pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git
    ```
4. Stop celery beat and workers.
5. `pip install --upgrade indy-hub`
6. `python manage.py migrate`
7. `python manage.py collectstatic --noinput`
8. `python manage.py sync_sde_compat`
9. Restart gunicorn + celery beat (mandatory — rewritten schedules) + workers.
10. (Optional) Set `INDY_HUB_DISCORD_DM_ENABLED = False` in `local.py` to disable Discord DMs (introduced in 1.10.2).
11. Assign the 1.11.0 corporation/copy-manager permissions in `Django Admin → Auth → Groups`.
12. Ask corp directors to re-link their tokens for the corp roles scope (1.11.0) and the new Material Exchange scopes (1.14.0).
13. (Optional) Configure corp token allow-lists in Token Management.
14. In `Material Exchange → Settings`, tick `enabled`.
15. (Optional) Configure Discord notification webhooks (1.13.4).
16. Update bookmarks: `simulation*` endpoints return `410 Gone`.
17. Assign the new Industry Structures / Crafting Project permissions in `Django Admin`.
18. Ask users to re-link their characters for `esi-location.read_online.v1`.
19. (Optional) Tune `INDY_HUB_MAX_FORM_FIELDS` / `INDY_HUB_MAX_REQUEST_BODY_BYTES` in `local.py`.

---

## Reusable cheat sheet

```bash
# 0. Backup DB
pg_dump / mysqldump …

# 1. Stop workers (and beat for any release with data migrations: 0096, 0100, etc.)
supervisorctl stop auth_celery_beat auth_celery_worker

# 2. Install / update the SDE backend if jumping over 1.15.0
pip install git+https://github.com/Solar-Helix-Independent-Transport/django-eveonline-sde.git

# 3. Upgrade Indy Hub (never touches your AA / django-esi version)
pip install --upgrade indy-hub

# 4. Migrate
python manage.py migrate

# 5. Sync SDE compat (mandatory when crossing 1.15.0 or later)
python manage.py sync_sde_compat

# 6. Static files
python manage.py collectstatic --noinput

# 7. Restart everything
supervisorctl restart auth_gunicorn auth_celery_beat auth_celery_worker
```

When in doubt, the per-version `### Update from X.Y.Z` blocks of `CHANGELOG.md` are authoritative.
