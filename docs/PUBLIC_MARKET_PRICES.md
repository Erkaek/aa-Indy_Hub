# Public CCP price references through the shared ESI client

## Transport and operation

`market_prices.fetch_market_price_references()` now calls
`shared_client.fetch_market_prices()`. That facade uses the existing
`esi_provider.client.Market.GetMarketsPrices().result(...)`, without a token,
scope selection or OAuth refresh. Only `GetMarketsPrices` is added to the
provider whitelist; unrelated Market operations remain excluded.

The operation, `Market` namespace and public parameter list were checked against
the installed django-esi 10.1.0 generated `esi/stubs.pyi`, including
`MarketsPricesGetItem`. Its native fields are `type_id: int` and nullable float
price fields. The local OpenAPI fixture exercises this operation with the real
provider/filter/client/HTTP/error machinery. The package floor remains
`django-esi>=10.1,<11`. No compatibility date is raised: the observed shared
provider uses `2025-09-30`, while the generated package stubs describe a newer
specification date. The test fixture explicitly uses the configured provider
date rather than overriding its HTTP header with an unrelated fixture default.

The provider remains lazy. Importing the price service or reading a warm
business cache does not initialize it or load a specification. On a cold native
call, its usual provider initialization/specification cache applies, just as for
other ESI facade calls. Tests use an explicit local spec and never download it.

## Business/cache contract

The public service signatures and return values remain:

```python
dict[int, dict[str, Decimal]]
# {34: {"adjusted_price": Decimal("1.25"), "average_price": Decimal("2.5")}}
```

| Contract                                     | Before                                          | After                                                   |
| -------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| Transport                                    | Direct `requests.get` to ESI                    | Shared django-esi provider/facade                       |
| Business key                                 | `indy_hub:market_prices:esi:v1`                 | Unchanged                                               |
| Business TTL                                 | 3,600 seconds                                   | Unchanged                                               |
| Warm dictionary                              | Returned without network access                 | Unchanged; native client remains lazy                   |
| Cold/expired cache                           | Fetch complete reference list                   | One logical native operation for the complete list      |
| Per-requested-type HTTP                      | None                                            | None                                                    |
| Missing/null price field on valid row        | Decimal zero                                    | Unchanged                                               |
| Invalid/nonpositive row ID or non-object row | Skipped                                         | Unchanged                                               |
| Requested unknown positive type ID           | Decimal zero after a successful reference fetch | Unchanged                                               |
| Empty or wholly invalid selection            | Reference fetch precedes selection filtering    | Unchanged                                               |
| Network/server/limit failure                 | Error, not a successful zero-price result       | `MarketPriceError`, with cause/status/delay information |

The complete list is still cached before selecting requested IDs. Existing
cached dictionaries remain readable; no cache migration or flush is needed.
An actual successful empty list retains its old empty-dictionary behavior.
Transport errors never create such an empty cache entry.

The business cache is deliberately the sole response-body cache on this path.
Native `result()` uses `use_etag=False`, `use_cache=False`, `store_cache=False`
and `return_response=True`. This requests a body without conditional headers,
does not clear other native response caches, and makes expiry/maintenance
invalidation meaningful without a second independently expiring price body.
It does **not** claim ETag/network savings for this endpoint. `esi_status` keeps
its existing bounded cleanup; no global native/shared cache is flushed.

An unexpected 304 raises the existing facade `ESIUnmodifiedError`. Bodyless 304s
which fail native JSON decoding before the usual django-esi exception are
normalized using their public response/status. The service rechecks its business
key once: it may reuse a dictionary populated by another caller while the
request was in flight, without extending its TTL. Otherwise it raises
`MarketPriceError(status_code=304)`. There is no application replay or fabricated
empty body/price zero.

## Amounts, malformed data and native validation

The facade reads JSON from the public response instead of using Pydantic's
float-coerced fields for money. Decimal strings therefore retain their exact
value; there is no newly introduced float conversion before `Decimal(str(...))`.
Unquoted JSON numbers retain the standard JSON decoder's existing float
representation limits, just as with the previous `requests.Response.json()`.
This change does not claim arbitrary precision for those numeric JSON literals.

For this endpoint only, a public `ResponseSchemaError` carrying an actual HTTP
200 response can pass that same response body to the legacy row parser. This
preserves its policy for missing fields and invalid rows, which native whole-list
validation would otherwise reject. Other statuses/transport failures cannot use
this adaptation. It neither patches native validation nor sends another request.
Malformed JSON or a non-list root remains an error.

Invalid numeric prices now raise controlled `MarketPriceError` rather than
leaking `decimal.InvalidOperation`; non-finite prices such as `NaN` are rejected
instead of entering a financial cache. This is an explicit malformed-data
hardening. Normal amounts, negative finite values, selection policy, EIV,
rounding and markups are unchanged. A valid requested ID absent from a
successfully fetched list remains the established zero fallback; a failed fetch
does not become that success case.

## Limits, errors and timeout compatibility

The facade reuses the existing task/global/endpoint guards and rate metadata.
Native django-esi retries remain its responsibility; no Indy Hub retry layer is
added. 420/429 and 5xx remain identifiable through `MarketPriceError.__cause__`,
`status_code` and `retry_after`. Wire 429 uses the bucket exception; a subsequent
call stopped by the existing local guard uses its `ESIErrorLimitException`
category (420) without another HTTP request. Its reset delay is retained; this
does not mean a second HTTP 420 response was received. 5xx keep the shared
Tranquility cooldown and delay. Other consumers still catch `MarketPriceError`.
If an HTML gateway response fails native content/JSON validation first, the
public response's error status and headers enter that same shared handling.
This classification does not replay the request or treat an error body as data.

**Timeout is an explicit compatibility change.** The service still accepts
`timeout=15`, and callers passing `timeout=10` remain source-compatible. The
public native operation/result APIs do not provide an isolated per-call HTTP
timeout: extra result keywords are OpenAPI parameters, not transport settings.
Therefore the keyword is deprecated, emits `DeprecationWarning` on a cache miss,
and no longer overrides the shared HTTP transport. A warm cache needs no timeout
and emits no warning.

The native `ESI_REQUESTS_CONNECT_TIMEOUT`, `ESI_REQUESTS_READ_TIMEOUT`,
`ESI_REQUESTS_WRITE_TIMEOUT` and `ESI_REQUESTS_POOL_TIMEOUT` settings govern the
call. Installed defaults are 5/10/5/5 seconds, respectively. No global setting,
provider timeout or installed package is mutated. No `timeout` query/header is
sent to ESI. If a deployment needs a distinct price-only deadline, it needs a
separately reviewed native API change; this patch does not introduce a private
hook or a second provider to imitate the old behavior.

## Qualification boundary

Start with `tests.core.test_market_prices`, `test_esi_provider` and
`test_esi_client_contract`, followed by craft/EIV/installation-cost and maintenance
tests and the complete tox matrix. Use a local specification and
`httpx2.MockTransport` with the actual django-esi provider/operation. Check
filtering, headers, token absence, Decimals, malformed data, 304/cache races,
cache cold/warm/expiry, 420/429/5xx/network failures, native-only retry and timeout
extensions. The manufacturing-basket regression uses real native price fetching
and computes EIV 14,507 per run and 290,140 for 20 runs, independently of the
product's price.

Resources must be disposable and isolated before Django startup. Real Chrome
qualification uses actual craft/local price APIs and synthetic SDE data, with
only external price transports/OAuth/analytics/publication controlled. No live
ESI/OAuth qualification, active cache/database change, deployment, import or
service restart is authorized by this source change.

### Results for this change

Qualified on 2026-11-08 with Alliance Auth 5.6.0, Django 5.2.19 and django-esi
10.1.0, using disposable SQLite databases, private Redis sockets, an in-memory
broker and network blocking installed before Django startup:

- Targeted native prices/provider/client contracts: **63 tests passed**.
- Contracts, maintenance and craft consumers: **139 tests passed**.
- Full tox: **914 tests per environment**, Python 3.10/3.11/3.12, **17 existing
  skips per environment**; dependency checks passed. Python 3.12 coverage: 72%.
- Full pre-commit hooks passed in an isolated checkout containing these changes.
- Real Google Chrome for Testing **149.0.7827.55**, Playwright 1.61.1, headless
  with sandbox enabled: **seven scenarios passed** against actual local craft
  views and price APIs. Member, manager, superuser, missing-permission and
  anonymous access were exercised. Tooling checks also passed in headed mode.

Chrome verified a manufacturing-input EIV of **14,507**, refresh to **16,000**
after bounded business-cache expiry, native HTML 429 with no second wire request
during cooldown, a controlled cold-cache bodyless 304, and recovery to **14,507**.
The normal API retained its unknown-ID zero fallback only after a successful
fetch. No token selection or OAuth refresh occurred, and blueprint snapshots
were unchanged. Captures and transport/source attestations are retained under
`/tmp/indyhub-prices-10/browser/`.

There were no JavaScript page errors or unexpected local HTTP failures. Console
and network errors were limited to deliberately blocked external images, the
expected permission-denied responses, and the deliberately induced 503 price
failures (including the existing UI's error message). Actual API response bodies,
cache state and calculation assertions passed in those failure scenarios.

This is native **offline** qualification, including real browser usage. Live ESI,
OAuth and production availability were **not tested**. The active installation,
database, Redis and services were not modified. The isolated browser server and
its private Redis were stopped after qualification. No commit or push was made.
