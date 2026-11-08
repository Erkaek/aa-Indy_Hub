# ESI limits and retry ownership

Indy Hub uses the public django-esi OpenAPI client and rate limiter. Protocol
limits, workload budgets and business eligibility have different owners. A
successful task execution is not proof that a remote snapshot completed.

The implementation was checked against django-esi 10.1.0, Alliance Auth 5.2.0
and Celery 5.6.3. It does not change the existing dependency constraint,
compatibility date, operation whitelist, minute dispatcher or QueueOnce keys.

## Responsibility matrix

| Mechanism                      | Owner and trigger                                                    | Scope / cache key                                                                                                                                           | TTL and delay units                                                                            | Exception / retry owner                                                                                 |
| ------------------------------ | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Global 420                     | Native OpenAPI response handling and pre-request guard               | All ESI callers sharing `esi_error_limit_reset`                                                                                                             | Value is a duration in seconds, with its own expiry; it is not an epoch timestamp              | `ESIErrorLimitException`; the calling task owns its retry                                               |
| Declared bucket, including 429 | Native `ESIRateLimits`, using the OpenAPI rate-limit extension       | `esi:bucket:{group}:{character}` for authenticated requests; anonymous group otherwise                                                                      | Native bucket window; exhausted guard reports remaining cache TTL + 1 second                   | `ESIBucketLimitException`; calling task retries once                                                    |
| Undeclared 429 fallback        | Facade, only when no native bucket is available                      | Existing `indy_hub:esi_rate_limit_meta:{endpoint}` and `indy_hub:esi_rate_limit_cooldown:{group}:{subject}`; an endpoint group is used if ESI provides none | Metadata 24 hours; cooldown stores an epoch deadline and expires with the delay                | Native bucket exception at the HTTP boundary; existing local guard can raise an error-limit exception   |
| Two-token safety reserve       | Facade, after a response with complete group/limit/remaining headers | Same existing endpoint metadata and group/subject cooldown families                                                                                         | Derived refill delay; metadata expires after 24 hours                                          | Calling task handles the local cooldown; retained because native permits any positive remaining count   |
| Workload budget                | Indy Hub, when a facade call executes in a Celery task               | Existing scope/subject/minute counter and hit keys under `indy_hub:esi_task_scope_budget` / `_hits`                                                         | Counter TTL 120 seconds; delay is seconds to the next minute                                   | `ESIErrorLimitException` identifying the local scope/subject; task owns retry                           |
| Tranquility 5xx                | Facade, after a transient HTTP failure                               | Existing `indy_hub:esi_tranquility_global_cooldown_until`                                                                                                   | Epoch deadline; 5–900 seconds, default 60, without shortening an already recorded longer pause | `ESIClientError` with status/retry metadata; later guarded calls use the existing error-limit exception |
| Private-structure 403          | Name resolvers and business authorization                            | Existing forbidden-structure and tried-character keys                                                                                                       | Forbidden structures 15 days; tried character state about 7 days                               | `ESIForbiddenError`; no global token invalidation or protocol retry                                     |
| Transient HTTP request attempt | Native django-esi / Tenacity                                         | One native operation                                                                                                                                        | Native configured attempts and exponential wait                                                | Native transport; separate from publishing a Celery retry                                               |

Workload counters count logical facade fetches, not every native HTTP page,
consistency probe or transport attempt. Existing scope targets and subject
dimensions are unchanged. A protocol bucket does not replace those budgets.

## Native coverage and retained bridges

`EsiOperation` derives bucket group, maximum tokens and window from the OpenAPI
extension. Authenticated bucket identities include the character. It checks
the shared global error key and bucket before network access, and records the
remaining count after successful responses.

In django-esi 10.1, HTTP 429 is forwarded without updating that bucket. For a
declared bucket, Indy Hub now records exhaustion through the public
`ESIRateLimits.set_bucket()` API. It uses the native full-window policy, or a
longer reset advertised by ESI. It does not write another local 429 cooldown
for that operation. This protects another operation in the same native group,
not merely the URL which returned 429. An undeclared operation retains the
existing local fallback; its native equivalence has not been established.

Native 420 normally supplies a typed exception and populates the shared error
key. Missing reset headers can leave that guard absent; malformed or fractional
`X-RateLimit-Reset` can fail the native integer conversion. The facade bridges
only that identified HTTP 420 cause, retains usable native or legacy headers,
and uses a 60-second fallback when none are usable. It adds the existing native
key without replacing a usable recorded reset or extending its TTL. Unusable
legacy/native reset values are replaced by the bounded guard. It never replays
the request.

Native exception `reset` values are durations in seconds. Numeric strings are
accepted and rounded up before task scheduling. Only HTTP `Retry-After` may be
an HTTP date; that date is converted to remaining seconds. Invalid, NaN and
infinite or unschedulable delay values are ignored in favour of another valid header or the
applicable fallback. Tranquility delay extraction retains its existing bounds.

Native transport retry policy is preserved. With the installed transport,
aiopenapi3 can wrap a connection failure in its own `RequestError`, which is not
the httpx exception tested by django-esi's retry predicate. Regression coverage
compares direct native and facade outcomes and attempt counts; Indy Hub does
not compensate with an additional application request.

The two-token reserve is intentionally retained: the native precheck permits a
positive count, does not reserve two tokens, and native response-cache hits
skip its network guard. Native buckets also do not provide the per-scope
workload targets, Tranquility circuit pause or private-refusal memory.

Existing pagination-consistency probes use the same protocol bridge. An
interrupted probe now rejects the fetched result rather than silently returning
it as a complete snapshot. Limits propagate to the task's retry owner; HTTP
outages keep their cooldown, and other probe failures preserve their cause.
The pagination strategy and number of requested validation pages are unchanged.

## Task retry owners

- Bound blueprint/job tasks keep their explicit `self.retry` branches and the
  native decorator as a fallback for an otherwise uncaught protocol exception.
  `Retry` and exhausted retries must propagate without a second publication.
- Corporation asset-cache refresh uses one explicit task retry with the parsed
  protocol or outage delay. Its generic autoretry excludes handled ESI errors
  and `Reject`, including failure to publish that retry. Unexpected errors keep
  their existing bounded generic autoretry.
- Personal asset refresh and contract synchronization/completion keep their
  explicit rate-limit publications. Redundant native decorators are removed
  from unbound Material Exchange tasks: their first argument was not a Task,
  or they had no arguments at all. External task arguments remain unchanged.
- Contract synchronization returns a deferred result after publishing its
  retry. The enclosing cycle then stops before validation or completion checks.
  The existing periodic cycle performs later validation; no new chain or
  scheduler is introduced. An item-read limit/outage propagates before the
  affected contract is marked current or the corporation cleanup runs.
- Structure synchronization records a retry delay for a limit and schedules
  one continuation even if the failed corporation is the last target. It
  returns `status=deferred`, preserving already completed corporation work.
- Roles and skills retain their explicit publications. System cost indices
  retain their lock, bounded attempts and existing ten-minute outage policy.
  Private structure refusals retain their placeholder/cooldown recovery.

Broker redelivery remains possible. One retry intention per handled error does
not establish exactly-once execution. Existing locks, permissions, snapshot
transactions and unique identities are retained.

## Cache transition and user-visible progress

No migration, cache flush or namespace deletion is required. Existing local
metadata dictionaries, numeric epoch deadlines and native reset strings remain
readable. Native reset strings are normalized for Celery without extending
their cache TTL. Old local cooldowns continue to be honoured until expiry, even
when a new native bucket now owns the protocol limit.

Local deadline updates retain a longer pause already observed in the current
cache read. This does not add an atomic cross-worker compare-and-set guarantee;
native rate bookkeeping and distributed response ordering retain their current
limits.

Personal asset progress distinguishes a scheduled ESI wait from a stuck worker.
Its TTL covers the reset plus the existing 180-second stale-progress grace.
The ordinary execution timeout is unchanged. Deferred progress cannot be
promoted to success merely because rows exist in the database. Old progress
entries without the new retry timestamp keep their previous timeout behaviour.

The sell page displays rate-limit/outage deferral and cached quantities without
discarding entered quantities. The scheduled retry can update the snapshot and
the page after the cooldown. No retry details or credentials are exposed as
product debugging controls.

## Qualification boundary

Tests use disposable SQLite, isolated cache/broker settings, controlled clocks
and a local native OpenAPI specification/HTTP transport. Chrome scenarios use
real Django views, CSRF, task bodies and ORM with synthetic users; ESI, Fuzzwork
and broker delivery are simulated at their boundaries. No real ESI/OAuth,
active service restart or active database/cache access qualifies this change.

The final PR records the actual test counts and outcomes. Distributed Redis
races, worker crashes and real ESI throttling remain unqualified. Future
django-esi versions may close the documented gaps; remove a bridge only after
the matching native-boundary regressions demonstrate equivalent protection.
