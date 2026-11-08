# ESI pagination and snapshot guarantees

The supported contract was checked against django-esi 10.1.0, Alliance Auth
5.2.0 and Python 3.10–3.12. The existing `django-esi>=10.1,<11` constraint is
unchanged. No installed package or active service was modified.

## Responsibility and evidence

| Property                      | Native `result()` / `results()`                                            | Indy Hub before reconciliation                                                                                            |
| ----------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Page collection               | `results()` traverses page parameters; only the final response is returned | Keep native collection; no replacement pagination loop                                                                    |
| Cached pages                  | Native response cache and ETags; expired entries are re-fetched            | Existing authorization and scope guards still run before cached data is accepted                                          |
| All 304                       | Native raises `HTTPNotModified`                                            | `ESIUnmodifiedError` preserves the existing snapshot and timestamps                                                       |
| Mixed 304 / changed pages     | Native restarts collection without ETag reuse                              | Preserve native refetch and verify the final aggregate                                                                    |
| Coherent dates                | Native does not compare page `Last-Modified`                               | Multi-page dates must be present, parseable, timezone-aware and equal                                                     |
| Page count                    | Native updates `X-Pages` at every page; no stable-total check              | Require a positive total, final URL page equal to that total, and equal totals on all probes                              |
| Data checked is data returned | Native returns the aggregate plus the final response                       | Each earlier probe must equal its normalized aggregate prefix; final-page body size must account for the remaining suffix |
| Interrupted retrieval         | Native propagates errors/limits; some malformed totals can resemble a 304  | Reject uncertainty before writes; retain typed rate errors and existing retry owners                                      |

The original probes could validate a newer response after cache expiry rather
than the data originally collected. Comparing dates alone was insufficient.
Force refreshes and disabled caches skipped coherence checks entirely. These
are functional corrections, not claims of equivalent refactoring.

Missing `X-Pages` retains the native single-page default. A single response does
not require a cross-page date, but must have a matching final URL/body count.
This cannot detect a server incorrectly omitting pagination metadata on its
first response. Explicit malformed/nonpositive counts are never accepted as a
complete empty snapshot. For multiple pages, missing/invalid dates and changed
page counts/content are explicit `ESIIncompleteSnapshotError` failures.

An unexpected 304 during a probe does not provide the body required to compare
with the aggregate and is refused. This differs from the native all-pages-304
outcome, which preserves the stored snapshot. A genuine complete empty list
remains eligible for scoped deletion.

## Final flow and limited optimization

1. Enforce existing business budgets and global/local cooldowns.
1. Select and validate the current scoped native Token, including before cache use.
1. Call native `results(return_response=True)`; keep its cache/ETag/refetch policy.
1. Normalize its models to the existing dictionaries/Python numeric and datetime types.
1. Check final response URL, body size and page count. For multiple pages, use its date as the coherence anchor.
1. Probe only pages before the final page, through public native `result()` with ETags disabled. Compare metadata and ordered payload with the aggregate.
1. Only then return the complete list to the existing scoped transaction and missing-row cleanup.

The final page no longer needs a probe: its response came directly from native
collection. Public response URL/body evidence binds it to the aggregate suffix;
tests cover page-size changes that could otherwise shift that boundary. Native
10.1 hashes argument dictionaries in insertion order, so probes preserve the
initial argument order and append `page` as native `results()` does. This avoids
unnecessary misses in the freshly populated force-refresh cache without
accessing native private cache methods.

Complete removal of the coherence pass is deferred. A public return contract
exposing every page's response evidence alongside the actual aggregate, or a
native guarantee validating dates and stable totals in every cache/force mode,
would be needed. The public statistics signal has no page/request/owner identity;
it is not used as an authoritative correlation mechanism. No production method
monkeypatch, native subclass or private hook was introduced.

## Measured costs

A logical facade fetch still consumes one workload-budget unit. The table below
counts **native `.result()` invocations**, including those inside `.results()`
and existing probes, separately from cache reads and HTTP requests reaching the
local `httpx2.MockTransport`. Native transport retry settings are unchanged.

Measurements use three pages except the single-page case, controlled expiry
of private synthetic cache entries, and no real ESI traffic.

| Scenario                         | `.result()` before → after | Cache reads before → after | Mock HTTP before → after |
| -------------------------------- | -------------------------- | -------------------------- | ------------------------ |
| Single page                      | 1 → 1                      | 5 → 5                      | 1 → 1                    |
| Cold, cacheable                  | 6 → 5                      | 14 → 13                    | 3 → 3                    |
| Warm, cacheable                  | 6 → 5                      | 11 → 10                    | 0 → 0                    |
| Expired, then cacheable          | 6 → 5                      | 14 → 13                    | 3 → 3                    |
| No stored response (`max-age=0`) | 6 → 5                      | 17 → 15                    | 6 → 5                    |
| Force, cacheable                 | 3 → 5                      | 8 → 10                     | 3 → 3                    |
| Cache disabled                   | 3 → 5                      | 8 → 10                     | 3 → 5                    |

The last two rows add previously missing validation. They are not reductions
against the weaker previous behavior. An integral cached 304 uses native page
checks only and no HTTP. A two-page mixed-304 scenario retains native complete
refetch: four native page invocations plus one earlier-page probe, with one
mock HTTP request when only the changed page has expired.

## Consumers and persistence boundaries

| Consumer                                                   | Retrieval and reconciliation                                                            |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Personal/corporation blueprints                            | Paginated facade, existing atomic upsert and scoped missing-item deletion               |
| Corporation jobs                                           | Paginated facade, existing atomic job upsert/deletion and unchanged notification policy |
| Personal jobs                                              | Single native `result()` list; not covered by the page optimization                     |
| Character/corporation assets                               | Paginated facade before their database cache transaction                                |
| Character/corporation contract lists                       | Paginated facade; corporation workflow reconciles scoped contract rows/history          |
| Contract items                                             | Single native `result()` list; not covered by the page optimization                     |
| Corporation structure registry                             | Paginated facade before existing per-corporation upsert/deletion                        |
| Structure/station names, skills, roles, divisions, systems | Scalar or single-response paths; unchanged                                              |

The personal-asset task previously replaced every user row after a partial
success and treated a true empty inventory as failure. It now commits only
when all attempted characters succeeded or were unchanged. Any failure retains
all previous rows/freshness/version and reports an incomplete refresh in the
sell page. Empty successes remove only successfully fetched character scopes;
unchanged or unfetched characters remain intact. Success progress is published
after the transaction and existing version write, not before SQL replacement.

The synchronous character-asset service likewise propagates retrieval failures
before writing, retains unchanged/unfetched character scopes, and handles empty
successes explicitly. Its cache reader may display the retained inventory after
failure; that read does not advance its persisted freshness or authorize a
snapshot replacement. No new retry or schedule is introduced for coherence
failure: existing manual/periodic refreshes and typed rate/outage handling remain.

## Qualification boundary

Native operations use a local filtered specification and MockTransport. Tests
cover real disposable model deletion, interrupted pages/probes, native cache and
304 behavior, true emptiness, scope isolation and prior-success preservation.
The final PR records actual suite counts and Chrome results. Browser checks
retain real local views, POST/CSRF, task bodies, native operations and ORM, with
only external services and broker delivery simulated.

This is not a qualification of real ESI/OAuth, real worker delivery, distributed
cache races or every future django-esi 10.x release. Equal dates and matching
page evidence are a consistency check, not a server-side transactional snapshot.
