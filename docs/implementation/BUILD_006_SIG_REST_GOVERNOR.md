# BUILD-006 — SIG Live REST Governor and Tracked-Depth Universe

**Status:** IN REVIEW — PR #19  
**Branch:** build/006-sig-rest-governor  
**Accepted on main:** no

## Why this corrective ticket exists

The first credentialed tournament smoke observed 237 open markets and 237 open exchanges. The accompanying shell probe used blocking curl calls followed by sleep 0.25. Because each request itself took roughly 0.17–0.26 seconds, request starts were actually spaced about 0.42–0.51 seconds apart: roughly 2.0–2.4 request starts per second.

That observation is enough to reject BUILD-004's assumption that all 237 full books can safely fit inside one 30-second freshness window, but it does not establish a 3–4 requests/second sustainable rate.

SIG does not publish a numeric REST limit in the supplied contract. Until a fixed-cadence live probe schedules request starts independently of response latency, the deployment default in this branch is a deliberately conservative configurable 2 requests/second.

## Corrected runtime model

Tournament-wide Realtime capture and tournament-wide resident authoritative depth are separate concerns.

    tournament:{tournament_id} Realtime
            |
            +-- trades/bookDirty/marketSettled/delivery for every known exchange
            |
            +-- shared governed REST
                  |
                  +-- HIGH: tracked dirty/recovery/settlement work
                  +-- NORMAL: tracked seed and expiry-safety depth refresh
                  +-- BACKGROUND: broad bulk price/BBO refresh

Every known exchange has one explicit depth state:

- UNTRACKED_DEPTH — deliberately no resident authoritative depth claim;
- TRACKED_UNTRUSTED — selected for depth, but unusable by depth-sensitive logic until REST recovery succeeds;
- TRACKED_TRUSTED — selected and backed by the latest accepted authoritative full-depth observation.

The safe default is an empty tracked-depth set. The capture command emits an operator-visible warning when run with no --tracked-exchange-id arguments.

## Broad scalar state

The supplied OpenAPI contract defines GET /exchanges/prices for at most 100 exchange IDs per request. It returns latest price, best bid, best ask and spread, with response data preserving request order and unresolved IDs reported separately.

BUILD-006 uses the existing SigRestClient.get_bulk_prices() path. A 237-exchange universe therefore requires exactly three bulk requests (100 + 100 + 37) per broad scalar sweep.

Scalar/BBO observations are persisted separately in price_observations. They never create or upgrade full-depth trust. If an exchange is reported in missingIds, any prior resident scalar latest-price/BBO/spread fields and scalar observation timestamp are cleared immediately so stale scalar state cannot remain usable.

## Full-depth tracking

Only exchange IDs supplied explicitly to the state engine/capture command receive continuous authoritative full-depth maintenance.

Startup is:

    authoritative tournament market enumeration
            -> broad bulk price/BBO seed
            -> authoritative full books for tracked IDs only
            -> tournament Realtime subscription

Recovery after reconnect, token refresh, socket error or revision gap similarly invalidates and reseeds tracked books only, while broad scalar state is refreshed through the bulk endpoint.

An untracked bookDirty is still persisted with revision/source provenance but does not trigger a full-book REST request.

A tracked bookDirty immediately transitions the exchange to TRACKED_UNTRUSTED, queues HIGH-priority authoritative reconciliation, and restores TRACKED_TRUSTED only after a successful identity-checked response.

## Silent expiry fallback

SIG documents that order expiry emits no Realtime event. Its Realtime prose advises refetching at the earliest expirationDate represented in the held book, but the documented participant aggregate endpoint GET /exchanges/{id}/orderbook exposes only aggregate price/quantity levels and no per-order expiration timestamp.

The Realtime prose also refers to GET /markets/{id}/orders for reconciliation, while that route is not defined as a participant REST path in the supplied OpenAPI paths. BUILD-006 does not invent that endpoint or an expiration value.

The configurable 30-second tracked-book freshness fallback is therefore a project expiry-safety policy, not a SIG contractual requirement. It applies only to tracked open books. Once the deadline is crossed, trust is removed before the refresh request is awaited.

## Shared REST governor

Live capture uses GovernedSigRestClient, a thin subclass of the accepted BUILD-003 client. Every HTTP attempt, including retries and Realtime-token minting, consumes one shared per-client request budget.

The governor provides:

- configurable sustainable pacing (default 2 requests/second pending fixed-cadence live validation);
- FIFO ordering inside HIGH / NORMAL / BACKGROUND priority classes;
- HIGH work can overtake BACKGROUND work waiting for capacity;
- shared 429 cooldown so concurrent callers do not independently retry into one per-key limit;
- server Retry-After seconds when supplied, otherwise bounded exponential cooldown with jitter;
- request, 429, cooldown and pending-priority health counters;
- injected sleep/monotonic/random functions for deterministic tests.

BUILD-003 retry semantics remain unchanged for safe GETs: bounded retry for 429 RATE_LIMITED, 503 TX_CONFLICT, 503 SERVICE_UNAVAILABLE, and transport failures/timeouts. Permanent 400/401/403/404 errors are not broadened into retries.

## Coalescing and scheduling

Tracked reconciliation is one task per exchange. Repeated invalidations while work is queued/in flight update the exchange generation and strongest pending priority instead of creating uncontrolled concurrent reads. If another invalidation arrives during an authoritative fetch, that stale response is never allowed to restore trust; the loop performs the newer reconciliation before returning to trusted state.

Expiry-safety work is deadline-driven by the last successful authoritative observation. Live maintenance schedules due exchanges individually rather than enqueuing every tracked book at one wall-clock boundary.

The configuration also rejects a tracked freshness requirement whose periodic demand alone exceeds 50% of the configured governed rate. The remaining capacity is intentionally reserved for dirty/recovery, account/trading and other urgent work.

## Persistence and health

Existing raw/replayable Realtime and authoritative observations remain. BUILD-006 adds compact normalized scalar/BBO observations rather than mislabelling them as books.

Health now exposes, among other fields:

    known_exchange_count
    tracked_depth_exchange_count
    tracked_trusted_count
    tracked_untrusted_count
    untracked_depth_exchange_count
    rest_governor_rate
    rest_requests_total
    rest_429_count
    rest_shared_cooldown_count
    pending_high_priority_reads
    pending_background_reads
    bulk_price_refresh_count
    full_book_refresh_count
    oldest_tracked_book_age_seconds

## Explicit operation

Normal application startup remains network-free.

Tracked depth is opt-in:

    python -m predictions_cup.sig.capture \
      --tournament-id <TOURNAMENT_UUID> \
      --tracked-exchange-id <EXCHANGE_ID> \
      --tracked-exchange-id <EXCHANGE_ID> \
      --print-health \
      --run-seconds 60

Running without --tracked-exchange-id still captures the complete tournament Realtime tape and broad scalar/BBO state but deliberately maintains no resident trusted full depth.

## Acceptance coverage

The branch tests:

- 237 known exchanges => exactly three broad price batches;
- initialization does not fetch 237 full books;
- only configured tracked exchanges receive depth seeds;
- scalar state never upgrades depth trust, and missingIds clears previously resident scalar/BBO state;
- tracked/untracked bookDirty behavior;
- tracked stale-book fail-closed refresh and failed recovery;
- revision-gap/reconnect recovery without a full-universe book storm;
- same-exchange reconciliation coalescing;
- capacity rejection for impossible freshness settings;
- aggregate governor pacing under concurrency;
- HIGH priority overtaking queued BACKGROUND work;
- shared 429 cooldown;
- bounded retry integration;
- governor cancellation/shutdown;
- compact scalar persistence and retention.

No trading, order placement/cancellation, fair value, mapping selection or strategy membership logic is introduced by BUILD-006.
