# Pinned CCP industry rules

Industry calculations use a local, versioned numeric rule adapter in
`indy_hub/services/industry_rules.py`. The installed SDE supplies item groups,
categories, Dogma effect names and attribute values; the packaged rule bundle
supplies the missing industry modifier-source/target-filter relationship.
Calculating a user's plan performs no download or SDE synchronization.

## Provenance

The source is the official CCP JSONL archive for build **3670638** (2026-11-04),
checked against build **3693310** (2026-11-12), following the
[CCP static-data contract](https://developers.eveonline.com/docs/services/static-data/).
The two `industryModifierSources.jsonl` and `industryTargetFilters.jsonl` entries
are byte-for-byte identical in those archives. Their SHA-256 hashes are:

| File                          | SHA-256                                                            |
| ----------------------------- | ------------------------------------------------------------------ |
| industryModifierSources.jsonl | `73bcc42e08404fe6c8b6eece355094bbc5932841af0ab5aadee6a68b50e28a9b` |
| industryTargetFilters.jsonl   | `daa27eac84f90e26e6a2d0ad8e35a973f081e225b9db63485d1d843241ecf316` |

The selected 125 type-Dogma records and 46 industrial effect records are also
identical between both builds (record comparison after JSON decoding).

The exact archive URLs, archive hash, and hashes of the full Dogma, item,
group, category and blueprint source entries are recorded in
`indy_hub/data/industry/ccp_rules.json`. Its content-derived `rules_version`
includes numeric rules, filters, per-source bindings, numeric conventions,
security attributes, known effects and build-qualification evidence. The source
archive is never installed into an active Alliance Auth database by generation.

## Authorities and validation

The installed SDE owns product characteristics and current Dogma values. The
schema-2 bundle owns official source/effect/filter relationships unavailable in
the installed ORM. `industry_rules.py` validates this boundary without Django or
network access. Policy such as enabled services, user selections and display
labels remains application code. Raw test snapshots are historical evidence,
not another runtime rule source.

Unsupported schema versions, activities, metrics, operations, references,
numeric conventions and semantic fields raise `IndustryRulesError` with a code
and path. In particular, a missing referenced filter or a `typeIDs` predicate is
an error, never an empty eligibility filter. Absence of a filter is permitted
only when the official modifier has no filter. Duplicate JSON keys and
colliding source/effect associations are rejected. Descriptive metadata follows
the documented field allowlists (`name`/`description` for source/filter editorial
metadata and `provenance` outside the calculation); unknown calculation fields
are not discarded.

Validation is deferred to industrial use so an invalid bundle cannot prevent
unrelated AA features from importing. Resolver boundaries translate errors into
Django configuration diagnostics. Invalid results cannot reuse or populate a
successful bonus cache.

## Resolution

Each generated rule records its actual Dogma effect ID/name, modifying and
modified attribute IDs, industry activity, metric and official target filter.
A product matches a filter when its exact group ID **or** category ID is in
that filter. Labels are display data and cannot grant eligibility. Matching a
container group consequently cannot grant a bonus to every item in its category.
Basic and advanced medium filters intentionally share group 4902.

The percentage reads the effect's own modifying attribute and multiplies it by
2355/2356/2357 for highsec/lowsec/nullsec. The bundle records which of these
attribute IDs are officially declared by each rig. Every declared ID must be
present in the installed SDE before any bonus is calculated, independently of
the requested security band. A missing declared attribute is a configuration
error; a multiplier defaults to one only when that attribute is absent from
the official source, as 2355 is for the 14 reaction rigs 46484 through 46497.
For example Thukker capital effects use 2653; ordinary effects on the same rig
continue to use 2594. Eligible modifiers from distinct sources combine
multiplicatively. Numeric target filters prevent incompatible effects on a
single XL rig from both applying to group 334.

Per-type bindings are retained because the two XL Laboratory Optimization rigs
37182/37183 bind research attributes 2565/2567 to different source activities
than the dedicated research rigs. The industry source row determines the
activity; an English effect name cannot override it. Historical effect-name
aliases remain available for old integrations and test fixtures. An unsupported
industrial effect or a missing modifying attribute must produce an explicit
configuration error rather than a partial set of bonuses.
The complete expected effect set of a known rig must be present in the local
SDE before any bonus cache is written. Empty or partial TypeEffect imports are
configuration errors, including when the remaining attributes are valid.
When a rig ID is supplied, its effect must be bound to that exact published
source in the bundle. A familiar effect name on an unknown/new rig is insufficient
and requires a reviewed rule update. Generic effect lookup is reserved for tools
which omit the source ID.

Known industrial and non-industrial effect sets are recorded per source. A new
name cannot bypass validation merely because it lacks an old English prefix.
Unknown sources/effects, missing declared effects and invalid numeric values
require reviewed data before that source can produce a reliable result.

Hull relationships are generated from their own official source rows. Their
direct multipliers are evaluated separately from rig percentages: no security
multiplier or rig percentage conversion is applied to a hull. Raitaru, Azbel,
Sotiyo and Tatara preserve the baseline of 35 resolved activity/metric rows;
Athanor and other officially bonus-free supported hulls retain zero bonuses.
Missing expected hull data is an error, not a bonus-free hull.

Reaction service families are explicitly biochemical, composite and hybrid,
using official filters 17, 18 and 16. Group 4096 remains biochemical. An unknown
family cannot borrow another family's service or tax; the planner reports it
in `invalid_items`, preserves saved selections and leaves unrelated jobs usable.

## Offline oracle

`indy_hub/tests/industry/fixtures/ccp_industry/` preserves both official industry
JSONL entries intact, raw Dogma rows for the rig subset, and a manifest of
published blueprint/product group/activity cases. The expected-value evaluator
in `test_ccp_industry_oracle.py` traverses each raw per-rig source row, binds its
target attribute to an actual Dogma modifier, and evaluates the raw numeric
filter. It does not call the application's effect correspondence, target
matcher or percentage aggregator to obtain expected values.

The matrix comprises 125 rigs, 46 effects, 4,173 published manufacturing/reaction
products, and 466 group/activity combinations:
125 × 466 × 3 security bands × 2 metrics = **349,500 rule-value comparisons**.
The tolerance is **1e-9 absolute percentage points**. Research, copying and
invention add **144 scalar comparisons**. Failures identify the rig, effects,
representative product, group/category, activity, band, metric and both values.

This mathematical matrix is not a set of legal fitted facilities. Its 1,500
highsec/reaction entries are explicitly marked as invalid facility combinations;
they verify hypothetical scalar rules only and are excluded from claims of
valid reaction configurations. Actual reaction facilities must be outside highsec
and must enable the matching numeric reaction family. Incompatible hull/rig
sizes likewise require the existing configuration validation before installation.

The 3693310 archive contains a larger published blueprint/product set (4,208
products, 467 group/activity combinations). The frozen 3670638 manifest retains
the exact audit scope so comparisons remain reproducible; the shared industrial
filter/source rules have been verified identical in both builds.

Most cells are negative applicability checks, not independent business
scenarios: 332,292 have no applicable source and 17,208 have at least one.
Explicit numerical references and mutation tests complement this matrix.
Hull references are preserved separately in `ccp_hull_references.json`; the
original four JSONL files and `oracle.json` are not regenerated by an update.

Run the oracle without any external service:

```shell
/home/aadev/venv-v5/bin/python runtests.py indy_hub.tests.industry.test_ccp_industry_oracle --verbosity=2
```

## Updating the rules

1. Obtain an official JSONL ZIP and review CCP's schema/change announcements.
   Preserve its original build filename and independently record its SHA-256.
   Downloading or importing it is never part of a user's calculation.

1. Generate into a new directory outside the checkout:

   ```shell
   python scripts/generate_industry_rules.py \
     --archive /path/eve-online-static-data-3670638-jsonl.zip \
     --comparison-archive /path/eve-online-static-data-3693310-jsonl.zip \
     --output-dir /tmp/industry-rules-candidate
   ```

   Outputs are `ccp_rules.json` and `industry_rules_diff.json`. `--reference`
   optionally selects another reference bundle. Existing candidate files are
   not overwritten. Historical oracle snapshots are never written by this tool.

1. Review the diff and qualification evidence: sources, filters, per-source
   effects and `modifierInfo`, operations, relevant attributes/security values
   and numeric conventions. New products covered by an existing category do
   not require invented group rules. A different build number alone does not
   imply a semantic change.

1. For a rule change, review expected results independently before deliberately
   promoting the candidate. Keep the frozen historical corpus; add a separate
   reviewed corpus when a new reference is needed. Do not derive expected
   results from the application output.

1. Run schema/mutation tests, both CCP matrices, project/cache regressions,
   frontend regressions and an isolated authenticated browser workflow.

1. Promote only the reviewed bundle and intended reference changes. Synchronize
   this document with `python scripts/sync_industry_documentation.py`, build the
   wheel/sdist, then run `python scripts/check_distribution.py dist`.

1. At release time, publish the application and bundle together. Restart all
   web and Celery worker processes that imported the bundle. A file replacement
   or SDE cache clear does not reload Python module globals. Coordinate rolling
   deployments; do not flush Redis or replace saved recipes to resolve versions.

Steps 2 and the diff are automatic and offline. Reviewing semantic changes,
approving numerical references, promoting candidates and releasing remain
explicit maintenance decisions. No generator connects to the live SDE, Redis,
ESI or an Alliance Auth installation.

### Qualification states

The installed SDE build, bundle content version and resolver/cache version are
separate. Qualification is supported by stored data fingerprints, not a bare
list of build numbers.

| State                    | Meaning and operational consequence                                                                                                                                                |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `compatible_qualified`   | Relevant official projections were checked; industrial sources still undergo installed-data validation.                                                                            |
| `compatible_conditional` | Supported semantics with explicitly checked conditions; inspect the reason and source validation before relying on calculations.                                                   |
| `unqualified`            | No archive evidence for the installed build; industrial calculations requiring that evidence are unavailable until qualification. Catalogues and unrelated features remain usable. |
| `incompatible`           | Unsupported or inconsistent relevant data; refuse the affected calculation and repair/update the bundle or SDE.                                                                    |

Source fingerprints include their own referenced filters and attribute
definitions. An incompatible rig filter does not disable an unchanged hull.
Conditional builds retain their separately attested numeric values; runtime
checks the relevant installed values against that build's evidence. Missing or
mixed contradictory Dogma section metadata is not a qualified installation.
Navigation sections remain part of freshness tokens but cannot independently
invalidate Dogma qualification.

Use the SDE importer's normal section-metadata updates. Manual SQL changes that
leave those timestamps/builds untouched can retain a warm lookup until targeted
invalidation or process restart; a cold resolver then rejects divergent values.
Do not repair this with a global Redis flush.

### Reproducing the browser fixture

```shell
python scripts/generate_craft_browser_fixture.py \
  --archive /path/eve-online-static-data-3670638-jsonl.zip \
  --output-dir /tmp/industry-browser-candidate
```

For another build, also provide `--expected-archive-sha256 <verified-sha256>`.
The fixed selection and recipe dependencies are documented in that small tool.
It streams/hashes source entries and emits `ccp_browser_sde.candidate.json` plus
`browser_fixture_diff.json`, including added/removed/changed records and order.
Review the diff before manually promoting a candidate. The reference is never
overwritten automatically; the current build reproduces the existing records.

## Derived caches and saved projects

Structure bonuses use cache schema 3. Their signature includes the rule token,
the explicit resolver semantics revision and installed SDE section build/update
metadata. A resolver change invalidates derived values even when CCP's numeric
data remains identical. Process-local industry lookups
check that generation even before a warm cache hit; an updated SDE clears only
those local lookups. Old cached empty results are recalculated, while a current
empty result remains valid. Redis is never flushed.

`resolver-v4` distinguishes the stricter source/hull/numeric semantics. Invalid
bundle availability is included in cache generations; a broken bundle cannot
keep a formerly valid cache merely by retaining its old declared version.

Project payload schema 7 carries rule and calculation-generation tokens. Schema
6 already contains raw per-run recipes, so its structure planner is refreshed
on a copy while its recipe, blueprint metadata and intended SDE snapshot remain
unchanged. Opening a saved project does not write its workspace. Both the page
and the API that loads full structure options follow this policy.
The structure planner also fingerprints live registry profiles, fitted rigs and
the cost indices of their systems. Profile edits, deletes and bulk writes refresh
derived options while retaining the recipe snapshot. The generation is captured
before reading planner inputs, so a concurrent registry edit cannot label old
options with a newer generation. A pending recipe refresh, incompatible schema
or mismatched run count cannot reuse a temporary or saved calculation cache.

Saving stock, prices, Buy/Produce/Useless decisions or structure selections keeps
the saved recipe and its original SDE signatures. Browser normalization of
unchanged ME/TE defaults and output quantities is not a recipe edit. Actual run,
output or blueprint-efficiency edits rebuild the recipe; an explicit
`refresh_sde=1/true/yes` also requests current SDE data. That request is consumed
after one successful save. Ownership and current corporation authorization are
checked before any cached payload is reused. Projects, historical snapshots,
permissions and user choices are not migrated or deleted.
Workspace, item quantities and the replacement payload are saved in one database
transaction: a calculation failure leaves the previous project intact.

Unknown sources or missing rig data produce explicit configuration errors. The
registry retains the profile with its error. The planner excludes invalid
profiles from usable options and reports them in `invalid_structures`; selected
invalid IDs remain saved and cannot reuse a stale bonus or silently fall back
to a different facility.
Deleted selected profiles are reported even when the registry is empty. A
selected service that is unavailable for one product uses `selection_only: true`
with the affected `selected_type_ids`; it does not invalidate that facility's
eligible jobs for other products. Later user choices take precedence over stale
selection metadata returned by an earlier request.

## Regression commands

Run `python runtests.py` with the compatible isolated test interpreter and
`npm test` for the full backend and JavaScript checks. Target the oracle with
`python runtests.py indy_hub.tests.industry.test_ccp_industry_oracle`.

`scripts/test_craft_industry_browser.cjs` exercises the real calculation code in
sandboxed Chromium. `scripts/serve_craft_industry_fixture.py` serves the real
authenticated Django planner using temporary SQLite/Redis, a synthetic user and
the small pinned `tests/craft/fixtures/ccp_browser_sde.json` dataset. It prints a
manifest for `INDY_HUB_CRAFT_MANIFEST=... node scripts/test_craft_industry_workflow.cjs`.
Ctrl-C stops the server and deletes its disposable database. External server
connections are blocked; the browser serves exact local copies of AA's SRI
assets and blocks remaining external requests. Playwright/Chrome and those local
assets are test tools outside the application's dependency contract; configure
their paths with the environment variables documented in the scripts.

The workflow covers real UI decisions, structure changes, partial/full stock,
the rendered tree, purchases/cycles, save/reload, single-use recipe refresh and
all four fuel-block references. Market prices are synthetic and live ESI/OAuth,
workers, native game job installation and the original reporter's workspace
remain outside this qualification.

The build/buy advisor remains a heuristic rather than a global optimization
solver. It compares complete purchase costs before publishing a suggested plan,
so local decisions about shared production batches cannot recommend a more
expensive plan than the current one.

## Documentation, distribution and retained legacy interfaces

This file is the sole editorial source. Run
`python scripts/sync_industry_documentation.py` after editing it;
`--check` verifies the copy without writing and runs in packaging CI.
The canonical documentation is mirrored byte-for-byte in the package at
`indy_hub/data/industry/INDUSTRY_RULES.md`. The wheel carries that document and
all offline CCP fixtures; the source distribution additionally carries this
source document and the generation/test tools. `scripts/check_distribution.py`
checks their presence and exact content against the current checkout.

The historical CSV `data/sde/industryActivityProducts.csv`, `SDE_FOLDER` and
legacy public attribute/catalogue constants have no identified internal reader.
They remain available pending an external-usage/deprecation review. Do not use
them as a second industrial rule authority. Active fallback catalogues and all
published migration constants remain unchanged.
`ENGINEERING_HULL_BONUS_MAPPING` is also retained as a compatibility export;
the active hull resolver uses generated official relationships instead.
