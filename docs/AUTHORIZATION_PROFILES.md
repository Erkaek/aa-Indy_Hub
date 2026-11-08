# Authorization profiles (prompt 05)

`indy_hub/authorization.py` is the side-effect-free source for consent profiles,
scope components and their permissions. It imports only the standard library;
neither Django setup nor CharLink is required. Frozen profiles and scope sets
cannot be mutated; `scope_list()` returns a fresh, sorted list for native APIs.
Compatibility names in the industry tasks, skill service, user views and
CharLink hook remain available as aliases or independent lists.

## Before/after contract matrix

The baseline was captured from tree `4cfd03d` before production edits (also the
tree of merge `2f90e69`). **Every scope set below is identical before and after.**
The independent [baseline fixture](../indy_hub/tests/core/authorization_scopes_before.json)
enumerates every full scope name for each entry; tests compare the registry and
the actual view requests against it. Indy Hub's request serialization and the
lists supplied to `LoginImport` are sorted, including the formerly unsorted
four-scope configuration request. Native CharLink still owns the union and
serialization of the apps selected in its form.

Scope symbols, used only to keep this matrix readable:

| Symbol | Exact scope                                   |
| ------ | --------------------------------------------- |
| B      | `esi-characters.read_blueprints.v1`           |
| J      | `esi-industry.read_character_jobs.v1`         |
| A      | `esi-assets.read_assets.v1`                   |
| U      | `esi-universe.read_structures.v1`             |
| S      | `esi-skills.read_skills.v1`                   |
| CB     | `esi-corporations.read_blueprints.v1`         |
| CJ     | `esi-industry.read_corporation_jobs.v1`       |
| R      | `esi-characters.read_corporation_roles.v1`    |
| CS     | `esi-corporations.read_structures.v1`         |
| CA     | `esi-assets.read_corporation_assets.v1`       |
| D      | `esi-corporations.read_divisions.v1`          |
| C      | `esi-contracts.read_corporation_contracts.v1` |

Permissions: **base** = `indy_hub.can_access_indy_hub`, **corp** =
`indy_hub.can_manage_corp_bp_requests`, **material** =
`indy_hub.can_manage_material_hub`. Public route names below use the `indy_hub:`
namespace, and URL suffixes use `/indy_hub/`. Alliance Auth applies its native
login/main-character wrapper when including these URLs. No EVE role is required
to start consent. Corporation ownership, authorized-character selection and EVE
roles are still checked by the business consumers before using an authorization.

| Route or CharLink ID                        | Public URL suffix                                   | Scopes before = after | Permission before = after | Coverage / new request                                                                                                      | Return / next               | Force / initial selection |
| ------------------------------------------- | --------------------------------------------------- | --------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------- | --------------------------- | ------------------------- |
| `authorize_assets`                          | `authorize/assets/`                                 | A                     | base                      | all owned characters, one valid grant with A each                                                                           | token page + safe next      | force ignored             |
| `authorize_blueprints`                      | `authorize/blueprints/`                             | B U                   | base                      | all owned characters, one valid grant with B U each                                                                         | token page + safe next      | force ignored             |
| `authorize_jobs`                            | `authorize/jobs/`                                   | J U                   | base                      | all owned characters, one valid grant with J U each                                                                         | token page + safe next      | force ignored             |
| `authorize_all`                             | `authorize/all/`                                    | B J A U S R           | base                      | all owned characters, grants for B U, J U, A, S; different capabilities may use different tokens; R is not a skip criterion | token page + safe next      | `force=1` bypasses skip   |
| `authorize_corp_blueprints`                 | `authorize/corporation/blueprints/`                 | CB U R CS CA          | base + corp               | always new consent                                                                                                          | token page + safe next      | force ignored             |
| `authorize_corp_jobs`                       | `authorize/corporation/jobs/`                       | CJ U R CS CA          | base + corp               | always new consent                                                                                                          | token page + safe next      | force ignored             |
| `authorize_corp_all`                        | `authorize/corporation/all/`                        | CB CJ U R CS CA D C   | base + corp               | always new consent                                                                                                          | token page + safe next      | force ignored             |
| `authorize_material_exchange`               | `authorize/material-exchange/`                      | U R CS CA D C         | base + material           | always new consent                                                                                                          | token page + safe next      | force ignored             |
| `material_exchange_request_assets_token`    | `material-exchange/config/request-assets-token/`    | CA                    | material alone            | always new consent                                                                                                          | configuration, next ignored | force ignored             |
| `material_exchange_request_divisions_token` | `material-exchange/config/request-divisions-token/` | D                     | material alone            | always new consent                                                                                                          | configuration, next ignored | force ignored             |
| `material_exchange_request_contracts_scope` | `material-exchange/config/request-contracts-scope/` | C                     | material alone            | always new consent                                                                                                          | configuration, next ignored | force ignored             |
| `material_exchange_request_all_scopes`      | `material-exchange/config/request-all-scopes/`      | CA D C U              | material alone            | always new consent; narrower than hub profile                                                                               | configuration, next ignored | force ignored             |
| CharLink `personal`                         | `/charlink/`                                        | B J A U S             | base                      | passive, one complete owner-bound token                                                                                     | native CharLink index       | selected initially        |
| CharLink `corporation`                      | `/charlink/`                                        | CB CJ U R CS CA       | base + corp               | passive, one complete owner-bound token                                                                                     | native CharLink index       | unselected initially      |
| CharLink `materialhub`                      | `/charlink/`                                        | U R CS CA D C         | base + material           | passive, one complete owner-bound token                                                                                     | native CharLink index       | unselected initially      |

For personal buttons, a user with no owned character satisfies the empty
coverage check; only `authorize_all?force=1` requests consent anyway. The AA
main-character requirement remains an independent outer guard. A partial token
does not cover a capability, and tokens belonging to another user never cover an
owned character. `require_scopes()` and the user filter precede `require_valid()`.

"Token page" means `indy_hub:token_management` (`/indy_hub/tokens/`, which redirects
to the existing canonical `/indy_hub/esi/` page). A same-host,
scheme-appropriate `next` is stored in the session before native SSO and checked
again on the token page before redirecting. Missing/unsafe next falls back to the
token page. Configuration returns to `indy_hub:material_exchange_config` and does
not interpret next. Existing wrappers preserve usage tracking, base refusals
(403), missing-manager messages/redirects on the hub, configuration refusals
(403), CSRF and native callback behavior. Unknown internal profile identifiers
raise 404; no route accepts a profile ID or arbitrary scopes from the browser.

## Explicit coverage correction

Before this change, CharLink's Python predicate restricted a grant to the AA
owner, while its SQL annotation accepted any user's Token with the same EVE
character ID. A recorded grant from a former owner reproduced **Python false /
SQL true**. The annotation now correlates the Token's character to its current
`CharacterOwnership.user_id`, preserving per-Token grouping before counting
distinct scopes. It matches the Python predicate on complete, partial, split,
absent, foreign-owner and unowned grants, including multiple annotated characters
and all three imports at once. Neither path validates or refreshes OAuth.

The reporting audience returned by CharLink's `get_users_with_perms` still uses
the final management permission for sensitive profiles; the consent/form guard
still requires base **and** management. This historical audience selector is not
an authorization check and has not been broadened into one. Stored CharLink
`AppSettings` selections/ignored flags take precedence over initial defaults;
this refactor does not reset them. The mandatory native Alliance Auth import
adds `publicData` to the final CharLink SSO request, as before. It is separate
from each Indy Hub import's scopes listed in the matrix.

## Boundaries and tradeoffs

django-esi still owns `sso_redirect`, `CallbackRedirect`, token selection,
validation and exchange. There is no second callback or consent engine. The
shared view helper handles only fixed profiles, permissions, existing personal
coverage, safe return and native SSO; public decorated functions keep their names.
Material Exchange keeps its narrower helper and existing permission response.
The native `tokens_required` decorators on business operations keep their own
capabilities (roles; CA D; R CA; personal B U / J U / their union). Existence of a
valid grant there does not become an all-character coverage rule.

The registry removes divergent request unions and the CharLink task-module
dependency. It adds an explicit table that must evolve with business scopes,
plus compatibility exports that must remain tested. It deliberately does not
unify `authorize_all` with personal CharLink, expand the configuration's four
scopes, require base permission on existing configuration routes, change shared
catalogue access or alter EVE-role/notification policy. Any such change remains a
separate product decision, not an unfinished requirement of this extraction.

## Qualification

Verified on 2026-11-08 using `/home/aadev/venv-v5/bin/python`: Python 3.12.13,
Alliance Auth 5.2.0, Django 5.2.15, django-esi 10.1.0 and optional CharLink
1.14.0. The repository's historical `.venv` was not used. The installed
`LoginImport`, CharLink form/views and native `tokens_required`, `sso_redirect`
and callback implementations were read without modification.

| Check                                                                                                      | Result                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Authorization profiles, CharLink coverage, corporation status and shared-token lifecycle, CharLink enabled | **PASS: 69 tests**, no skips                                                                                                                 |
| Full tox `py312`, `py311`, `py310`                                                                         | **PASS: 864 tests per environment**, 17 skips each; all three `pip check` commands passed                                                    |
| Pre-commit, ten changed/new files only                                                                     | **PASS**; no global formatting                                                                                                               |
| Real Chrome tooling, headed and headless                                                                   | **PASS**, sandbox enabled                                                                                                                    |
| Real application workflows in Chrome                                                                       | **PASS: nine scenarios**, headless, desktop                                                                                                  |
| Preservation                                                                                               | Existing application/test changes retained byte-for-byte; no published migration changed; task/skill-service functions and classes unchanged |

The 17 tox skips are 16 optional CharLink tests and one pre-existing explicitly
skipped blueprint-rejection test. An additional two-test AA/CharLink hook suite
passed with CharLink enabled. Together with the 69 targeted cases, it executes
all 16 optional tests: **71 targeted tests passed with no skips**. Tox reused its existing environments
with `--no-provision --skip-pkg-install` and `PIP_NO_INDEX=1`: no package was
installed or upgraded. SQLite in memory, a fresh private Redis Unix socket,
memory broker/results/email and blocked external sockets were configured before
Django setup. No active database, Redis, broker or installation was used.

The isolated targeted command was:

```sh
PROFILES05_CHARLINK=1 /home/aadev/venv-v5/bin/python /tmp/indyhub-profiles-05/run.py \
  indy_hub.tests.core.test_authorization_profiles \
  indy_hub.tests.core.test_charlink_scope_coverage \
  indy_hub.tests.core.test_corporation_scope_status \
  indy_hub.tests.core.test_shared_token_lifecycle
PROFILES05_CHARLINK=1 /home/aadev/venv-v5/bin/python /tmp/indyhub-profiles-05/run.py \
  indy_hub.tests.test_smoke.AuthHookTests
/home/aadev/venv-v5/bin/python /tmp/indyhub-profiles-05/run_tox.py
node scripts/check_indyhub_chrome.cjs --all-modes
node /tmp/indyhub-profiles-05/browser.cjs
```

These temporary runners supply the isolation; do not transplant their test
labels into an active project's settings. Pre-commit was passed the ten files
listed below with `--files`. No script or template changed, so frontend lint and
mobile layout checks were not rerun for this backend extraction.

Chrome for Testing 149.0.7827.55 / Playwright Core 1.61.1 exercised actual Django
views on an ephemeral loopback port, a separate SQLite file/private Redis and
eight synthetic accounts. Native SSO request creation and callbacks ran; only
authorization URL generation and token exchange were replaced at the external
boundary. A second ephemeral loopback server represented OAuth, avoiding external
contact even on browser-followed redirects. External static assets used local
fixture copies or were blocked. Local application views were never fulfilled
by the browser test.

The nine passing scenarios covered:

1. Actual ESI authorization button, its six scopes, native callback and passive listing.
1. Two owned characters with only one covered: consent remains necessary.
1. Covered-character skip, force, valid internal next, external next refusal and callback return.
1. Corporation manager button and separate corporation blueprint/job requests.
1. Configuration's four-scope button, partial requests and distinct six-scope hub request.
1. Anonymous, missing-base/missing-manager refusals, historical management-only configuration access and superuser.
1. CharLink's foreign-owner refusal, personal-only CSRF POST, callback and corrected linked display.
1. Explicit selection of the initially unselected corporation import.
1. Explicit selection of the initially unselected Material Exchange import.

Final browser evidence records zero JavaScript exceptions, zero unexpected local
HTTP failures, zero server 5xx and zero OAuth refresh calls. The 47 console
resource errors consist only of intentionally blocked external fonts/images and
the expected permission 403 responses. Five useful desktop captures were saved;
ESI, corporation/configuration and CharLink before/after captures were inspected.
Earlier failed harness assertions (redirect interception, stale sessions/state,
canonical return URL, submit selector and table column) were retained, corrected
and rerun. Native CharLink's mandatory `publicData` was verified against its
installed AA import, rather than counted as an Indy Hub scope expansion.

Evidence, exact commands and source hashes are in `/tmp/indyhub-profiles-05`;
`browser/browser-report.json`, `targeted-current.log`, `optional-hook-qualified.log`, `tox-current.log`,
`tox-result.json` and `preservation-final.json` record the results. Temporary
cookies are not deliverables. Real CCP consent/refresh, ESI, notifications,
worker delivery, distributed cache races, MySQL/MariaDB and mobile rendering
were **not executed**. Qualification used isolated local infrastructure and
does not qualify a deployment on the active installation.

The browser, synthetic OAuth boundary, test server and their private Redis were
closed after the run; their host PIDs were checked as absent. The synthetic
cookie file was removed. No active service was restarted.

### CI bootstrap follow-up

The initial GitHub run failed while installing ESLint, before running the hooks;
its dependent tox and build jobs were skipped. Previously installed local hook
environments had not exposed this installation failure. The hook configuration
now pins Node 22.22.2 instead of 22.13.1 to satisfy the current transitive
dependencies. No hook was disabled. Follow-up checks run `pre-commit --all-files`
in a disposable clone with a new hook cache, and repeat the isolated full tox
matrix. Temporary EditorConfig download HTTP 504 failures are retained in the
evidence; a subsequent download succeeded without copying an old environment.

## Files in this change

- `indy_hub/authorization.py`: immutable scope components and fixed profiles.
- `indy_hub/views/user.py`: compatible exports and shared native authorization preparation.
- `indy_hub/views/material_exchange_config.py`: fixed partial/configuration requests and common scope constants.
- `indy_hub/thirdparty/charlink_hook.py`: profile consumers and explicit SQL owner-coverage correction.
- `indy_hub/tasks/industry.py`: compatibility exports from the common components.
- `indy_hub/services/industry_skills.py`: common skill-scope constant.
- `indy_hub/tests/core/test_authorization_profiles.py`: consent/permission/coverage/return contracts.
- `indy_hub/tests/core/authorization_scopes_before.json`: independent exact scope baseline.
- `indy_hub/tests/core/test_charlink_scope_coverage.py`: optional integration and Python/SQL agreement.
- `docs/AUTHORIZATION_PROFILES.md`: before/after matrix, decisions and qualification.
- `.pre-commit-config.yaml`: compatible Node runtime for fresh hook installations.

The local permission/ESI knowledge notes also point to this registry and matrix;
`docs/codex/` remains ignored under the repository's existing Git policy.
