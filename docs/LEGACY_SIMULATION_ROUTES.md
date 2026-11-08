# Legacy simulation navigation (prompt 06)

The four retired single-blueprint simulation controllers remain compatibility
entry points. Their shared helper, `views.legacy.redirect_legacy_simulation`,
adds one translated INFO message and returns Django's temporary redirect to
`indy_hub:production_simulations_list`. It never accepts or interprets an old ID.

## Contract before and after

The baseline was inspected on main at `ed2a208`. All contracts below are
unchanged; the extraction only removes repeated message/redirect bodies.
Paths use the existing Alliance Auth `/indy_hub/` mount.

| Route name                     | Path                                         | Public view                                   | Arguments after request | Specific analytics event            |
| ------------------------------ | -------------------------------------------- | --------------------------------------------- | ----------------------- | ----------------------------------- |
| `production_simulations`       | `simulations/legacy/`                        | `views.user.production_simulations`           | none                    | `user.production_simulations`, once |
| `rename_production_simulation` | `simulations/<int:simulation_id>/rename/`    | `views.user.rename_production_simulation`     | `simulation_id`         | none                                |
| `delete_production_simulation` | `simulations/<int:simulation_id>/delete/`    | `views.industry.delete_production_simulation` | `simulation_id`         | none                                |
| `edit_simulation_name`         | `simulations/<int:simulation_id>/edit-name/` | `views.industry.edit_simulation_name`         | `simulation_id`         | none                                |

Every wrapper retains `@indy_hub_access_required` outside `@login_required`.
Anonymous requests retain the existing login redirect; authenticated users
without `indy_hub.can_access_indy_hub` retain their 403 refusal. Alliance Auth's
outer URL wrappers and Django CSRF protection are unchanged.

There is no HTTP-method restriction in these four views. Authorized requests
return **302**, not 301 or 410, with the named destination resolving to
`/indy_hub/projects/`. GET, POST, HEAD, PUT, PATCH, DELETE and OPTIONS retain this
navigation-only behavior; unsafe requests still require valid CSRF at the HTTP
boundary. Missing or colliding historical IDs never select a current project.
An arbitrary `next` parameter cannot replace the fixed destination.

The exact translated message and its level remain:

> Legacy single-blueprint simulations were removed. Use craft tables instead.

The helper has no decorators, usage tracking or analytics. This prevents double
counting and keeps each wrapper's signature, security and analytics visible.
The user listing wrapper alone keeps its existing event before redirecting.

## Modern projects and retained references

`production_simulations_list` is the modern projects list despite its historical
Python name. Its POST bulk-delete behavior is unchanged. Modern deletion remains
`projects/<project_ref>/delete/`: GET renders confirmation, POST deletes only the
owner's project. Modern renaming remains the owner-scoped POST JSON endpoint
`api/production-projects/<project_ref>/rename/`.

Retained historical templates reference these names in their cancel/edit/delete
links. Current navigation, the project registry, `_project_card.html` and its
scripts still use the existing modern names and references. No URL, template,
script, model, import workflow, dependency or migration was changed.

## Scope and tradeoff

This is a small maintenance improvement, not an Alliance Auth compliance fix.
Four explicit wrappers now share one message and destination. A function is
sufficient; a generic redirect registry or class-based view would hide the
wrappers' differences for little gain. One import per consumer and a small module
are the cost. No new user-visible behavior is intended.

## Verification

Public contract regressions cover reverse/resolve, method and ID variants,
access refusals, CSRF, exact INFO messages, historical analytics counts and
non-mutation of current projects/items. They assert observable HTTP behavior,
not the helper's implementation. The same contracts passed before extraction.
Existing project deletion, workspace/import and usage suites remain the modern
behavior checks. Browser evidence and broader results are recorded in the
temporary `/tmp/indyhub-legacy-06` directory; only completed checks qualify the
change. No active installation, real ESI/OAuth or active worker is involved.

### Completed local qualification (2026-11-08)

| Check                                                                | Result                                                                            |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Before extraction: public contracts and existing project regressions | **44 tests PASS**                                                                 |
| After extraction: complete projects and usage-tracking suites        | **107 tests PASS**, no skips                                                      |
| Full tox `py310`, `py311`, `py312`                                   | **868 tests per environment PASS**, 17 explicit skips each; three pip checks PASS |
| Pre-commit, entire repository in a disposable clone                  | **PASS**, including the new files; shfmt present on PATH                          |
| Chrome tooling, headed and headless                                  | **PASS**, sandbox enabled                                                         |
| Real application scenarios in headless Chrome                        | **Seven PASS**, desktop; three useful captures inspected                          |

Targeted tests and browser use `/home/aadev/venv-v5/bin/python`: Python 3.12.13,
Alliance Auth 5.2.0, Django 5.2.15 and django-esi 10.1.0. Tox uses its existing
Python 3.10.12/3.11.15/3.12.13 environments with AA 5.6.0, Django 5.2.19 and
django-esi 10.1.0. No environment or package was installed or upgraded. The tox
skips are 16 optional CharLink cases and one existing blueprint-rejection case;
the four new legacy contract tests are executed in every environment.

SQLite is disposable, Redis is a fresh private Unix-socket instance, and
broker/results/email use memory backends. External socket connections are
blocked before Django setup. The browser server also captures publication and
analytics at their external boundaries; local views, middleware, forms, CSRF,
database reads and modern project mutations remain real.

The isolated commands were:

```sh
/home/aadev/venv-v5/bin/python /tmp/indyhub-legacy-06/run.py indy_hub.tests.projects indy_hub.tests.core.test_user_usage_tracking
/home/aadev/venv-v5/bin/python /tmp/indyhub-legacy-06/run_tox.py
/home/aadev/venv-v5/bin/python /tmp/indyhub-legacy-06/tox-py310/run_tox.py
/home/aadev/venv-v5/bin/python /tmp/indyhub-legacy-06/tox-py311/run_tox.py
/home/aadev/venv-v5/bin/python /tmp/indyhub-legacy-06/tox-py312/run_tox.py
node scripts/check_indyhub_chrome.cjs --all-modes
node /tmp/indyhub-legacy-06/browser.cjs
```

These temporary runners provide isolation and must not be replaced with active
project settings. Full pre-commit uses the disposable `precommit-checkout` clone,
including the new helper/test/documentation as staged files in that copy only.
It uses the existing hook cache and temporary shfmt executable already qualified
for prompt 05. No global formatting or staging was applied to the user worktree.

Chrome for Testing 149.0.7827.55 / Playwright Core 1.61.1 ran with sandbox and
four synthetic authenticated roles plus anonymous access on an ephemeral port.
The seven scenarios verify:

1. Four historical GET routes: 302, unchanged INFO message, fixed destination and preserved project/item rows.
1. Four real browser POST requests with CSRF: identical navigation and no mutation from a colliding current ID.
1. Missing IDs and another owner's IDs: ignored without mutation.
1. A forged unsafe request without CSRF: 403 without mutation.
1. Anonymous login, missing-base 403, superuser access and another owner's isolated project list.
1. Modern owner-scoped JSON rename and persisted workspace naming; foreign-owner rename remains 404.
1. Modern GET delete confirmation and actual UI POST deletion: only the owner's project/items are removed.

Final evidence records zero JavaScript exceptions, unexpected local HTTP errors,
server 5xx or OAuth refresh calls. The 84 console resource errors consist only
of 78 blocked external font/image requests and the expected 403/404 refusals.
All three captures were opened: legacy redirect with INFO notice, modern delete
confirmation and the owner's empty list after modern deletion. Browser, server
and private Redis were stopped and synthetic cookies removed. Source hashes
match the actual files served. AST comparison confirms that only the four
legacy function bodies changed: signatures, decorator order and every modern
function are unchanged. URL/model/API/migration files retain their original
hashes.

An initial broader invocation referenced a nonexistent decorator-test module;
that label error is retained in `targeted-first-label-error.log`. The corrected
107-test run passes. Other evidence includes `baseline.log`, `targeted.log`,
`tox-full.log`, `tox-result.json`, `precommit-all-files.log`,
`browser/browser-report.json`, `browser/backend-proof-final.json` and
`preservation.json` under `/tmp/indyhub-legacy-06`.
The global orchestration command received signal 143 after tox wrote three
successful environment reports. Each complete environment was then rerun with
its own SQLite/cache/coverage/report paths: all three commands exited **0**, with
868 tests and 17 skips each. Their final logs and JSON reports are under
`tox-py310/`, `tox-py311/` and `tox-py312/`. No test was disabled or changed to
obtain those clean exits.

Real ESI/OAuth, notifications, worker delivery, distributed caches, other database
engines and mobile rendering were **not executed**. No template, JavaScript or
CSS changed; full layout qualification is outside this backend extraction.
Qualification used isolated local infrastructure; deployment on the active
installation is outside these checks.

## Files

- `views/legacy.py`: the small shared message/temporary-redirect helper.
- `views/user.py`, `views/industry.py`: imports and four preserved public wrappers.
- `tests/projects/test_legacy_simulation_routes.py`: four public-contract regressions.
- `docs/LEGACY_SIMULATION_ROUTES.md`: inventory, contracts and evidence.

The local ignored architecture note was corrected from the obsolete 410 claim
to the verified 302 contract; it remains ignored under the existing Git policy.
