# Shared Alliance Auth administration

Indy Hub no longer registers or replaces Django's concrete `auth.User` and
`auth.Group` admins. Alliance Auth owns its existing proxy screens, forms,
restrictions, inlines and actions. Indy Hub's industrial admins and its separate
`settings_admin_users` business console retain their existing behavior.

## Why the registrations were removed

The former code read the private `admin.site._registry`, unregistered/re-registered
the concrete models and installed custom forms. Its only additional behavior was
shortening three Indy Hub permission labels. No authentication feature depended
on these classes. Once AA had replaced the concrete admins with proxies, importing
Indy Hub recreated two extra screens for the same underlying tables.

This was more than a visual difference in the isolated reproduction. With the
old code, a **staff account already granted concrete `auth.change_user` and
`auth.change_group` permissions** could submit valid forms to the extra routes.
The test persisted `is_staff=True`, `is_superuser=True` and a direct permission
on a disposable target user, and changed a disposable group's permissions. The
native AA forms reserve these fields for a superuser on existing objects. This
demonstrates a conditional alternative-form bypass in that fixture, not access
by an anonymous user or proof of exploitation on an installed site.

The fix removes those registrations and label-only classes. It does not
unregister AA proxies, patch AA methods/forms, or take ownership of another
module's concrete admin. A separate-site import test preserves host-supplied
concrete registrations exactly, including the original admin instances.

## Routes and permission presentation

| Former Indy Hub concrete screen  | AA-owned destination                        |
| -------------------------------- | ------------------------------------------- |
| `/admin/auth/user/`              | `/admin/authentication/user/`               |
| `/admin/auth/user/add/`          | `/admin/authentication/user/add/`           |
| `/admin/auth/user/<id>/change/`  | `/admin/authentication/user/<id>/change/`   |
| `/admin/auth/group/`             | `/admin/groupmanagement/group/`             |
| `/admin/auth/group/add/`         | `/admin/groupmanagement/group/add/`         |
| `/admin/auth/group/<id>/change/` | `/admin/groupmanagement/group/<id>/change/` |

The same destination prefixes apply to existing-object `delete/` and `history/`
URLs. These paths assume the usual `/admin/` mount; use the host's actual admin
prefix if different. Object primary keys refer to the same underlying tables.

There are **no new redirects**, especially for editing POSTs. In the qualified
AA setup, removed concrete reverse names no longer exist. Old literal URLs hit
the normal admin catch-all: staff receive 404; anonymous/non-staff requests are
first redirected to admin login. Update bookmarks rather than replaying old
form data against another model's endpoint. If the host independently registers
a concrete model, its own behavior and URLs remain its responsibility.

The three stored codenames, names, content types, memberships and assignments
are unchanged:

- `indy_hub.can_access_indy_hub`
- `indy_hub.can_manage_corp_bp_requests`
- `indy_hub.can_manage_material_hub`

Permission selectors use AA/Django's native label, which can include the model
name (`Indy Hub | Blueprint | can access Indy_Hub`) instead of the removed
short form (`indy_hub | can access Indy_Hub`). No data or migration is changed
to hide this presentation difference. No new permission-management panel exists.

## Native permission matrix

The executed environment is Alliance Auth 5.6.0 / Django 5.2.19 from
`/home/aadev/venv-v5`, not a residual package version or the repository `.venv`.
AA uses different permission rules for its two proxy admins:

- User view permission follows the proxy's `authentication.view_user` or
  `authentication.change_user`. User add/change/delete methods explicitly check
  concrete `auth.add_user`, `auth.change_user` and `auth.delete_user`. A change
  permission also grants access to the change/list/history screens even when
  the separate view method returns false.
- Group admin permissions follow `groupmanagement.*`. The AuthGroup inline has
  its own concrete `auth.change_group` condition. These permissions are not
  interchangeable, and this fix does not rename or remap them.

Staff who used the removed concrete Group screen still need the AA proxy's
existing permission requirements to use its replacement. The fix does not
automatically grant broader or supposedly equivalent permissions.

| Synthetic role                        | Native user list/change/history GET | User add/delete GET | Native group routes GET |
| ------------------------------------- | ----------------------------------- | ------------------- | ----------------------- |
| Anonymous                             | Login redirect                      | Login redirect      | Login redirect          |
| Authenticated, not staff              | Login redirect                      | Login redirect      | Login redirect          |
| Staff, no relevant permission         | 403                                 | 403                 | 403                     |
| Staff, concrete auth permissions only | 200                                 | 200                 | 403                     |
| Staff, proxy permissions only         | 200, view only                      | 403                 | 200                     |
| Staff, concrete and proxy permissions | 200                                 | 200                 | 200                     |
| Superuser                             | 200                                 | 200                 | 200                     |

For existing objects, non-superuser forms keep `is_staff`, `is_superuser` and
`user_permissions` read-only in the AA user admin, and `permissions` read-only
in the AA group admin. When its inline is available, `restricted` remains
read-only for non-superusers as well.

Successful native user/group POSTs also change a permitted ordinary field to
prove they reached a real save, while forged reserved-field values are ignored
and database privileges remain unchanged. Removed concrete URLs are tested
with GETs and forged POSTs across the same role set. This qualifies these exact
paths and fields; it is not a general security audit of all AA administration.

## Verification boundary

Before changing the code, separate test processes with and without Indy Hub
recorded registrations, all four `has_*_permission` results, five route kinds,
read-only/form fields, inlines and actions for seven roles. After the change,
the AA-owned registry and matrix match the without-Indy process exactly. No
active registry was cleared or reset. Startup and writes used a private SQLite
copy, dedicated cache, memory broker/email, blocked external connections and
intercepted external task publication. The active project/database and V5
packages were untouched.

Regression tests live in `tests/core/test_shared_aa_admin.py`. They exercise
the real AA applications/admin through Django and django-webtest, including
CSRF-bearing native forms, native labels, retained Indy Hub grants/business
guards, and an actual save through the industrial Blueprint admin. The targeted
suite runs before broader admin-console/permission tests and the complete tox
matrix. Chrome qualification must use real AA forms, synthetic accounts and an
ephemeral port; no local admin view/API may be simulated.

The probe is not evidence for the old audit's AA 5.2.0 or every supported host
customization. The deployed site must review its own grants and bookmarks;
there is no active permission cleanup, migration, service restart or deployment
in this source change.

## Qualification results (2026-11-08)

- Nine new behavioral regressions passed; the broader admin-console,
  visibility, bulk-action and permission suites passed **71 tests**.
- Full tox passed on Python 3.10, 3.11 and 3.12: **898 tests per environment,
  17 existing skips**, and successful `pip check` in each. Existing isolated
  tox environments were reused without installing packages.
- Full pre-commit passed in a disposable Git copy. Model/migration consistency
  passed with no changes detected. All 16 remaining top-level industrial admin
  classes have the same AST as before the fix; only the label-only shared-model
  integration was deleted from production code.
- Google Chrome for Testing 149.0.7827.55 / Playwright 1.61.1 passed seven real
  application scenarios, **headless with sandbox enabled**. They covered seven
  roles, native User/Group forms, successful ordinary writes containing forged
  privileged fields, native superuser selectors/labels/inlines, removed-route
  GETs and valid-CSRF POSTs, industrial Blueprint editing and the unchanged
  Indy Hub user-administration console.
- Browser checks found no JavaScript exceptions or unexpected local HTTP
  errors. Expected 403/404 refusals and deliberately blocked external image
  resources appeared in the console. Captures of restricted and superuser
  screens and the saved industrial object were inspected.

The browser used an ephemeral loopback port, synthetic sessions, a private
SQLite database, dedicated Redis socket, memory broker/email and external
connection blocking. Only external publication/analytics/OAuth boundaries were
controlled; local admin forms and writes were real. No OAuth refresh occurred.
The browser/server/cache were stopped after qualification. Probes, complete
matrices, logs, source fingerprints and captures are retained under
`/tmp/indyhub-admin-09/`, outside version control.

This does not qualify an active installation, every AA version/host customization,
or live ESI/OAuth/Discord and worker delivery. No active data, stored permission
assignments, V5 packages or published migrations were changed. No commit, push
or deployment was performed by this prompt.
