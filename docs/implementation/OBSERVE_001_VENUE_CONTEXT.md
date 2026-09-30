# OBSERVE-001 — Venue + Competition Context Instrumentation

**Status:** implementation branch / pre-review  
**Branch:** `build/observe-001-venue-context`  
**Base SHA:** `0b91e5c4e8ed461e7e5e8fcc880a094029f58891`  
**Launch target:** SIG Predictions Trading Cup, 1 October 2026

## Mission

OBSERVE-001 makes the competition reconstructable from minute zero without adding trading logic.

It extends existing contracts rather than creating parallel infrastructure:

```text
BUILD-009 execution / MAKE / SIG Realtime / account Realtime
    -> tiny typed instrumentation hook
    -> bounded non-blocking ObservationEmitter
    -> CAPTURE-001 immutable Parquet streams

official participant-accessible SIG REST
    -> CompetitionContextProvider
    -> normalized + raw context snapshot
    -> CAPTURE-001 immutable Parquet stream

accepted SIG <-> Polymarket mapping
    + observed economic changes
    -> descriptive cross-venue timing join
    -> later research / first-hours analysis
```

OBSERVE does not decide what to trade, change fair value, tune MAKE, create a second order identity,
or create an economic order merely to measure latency.

## Architecture

### Observation contract

`predictions_cup.observe.contracts.VenueObservation` is an immutable dataclass carrying:

- observation kind;
- UTC wall time;
- process-local monotonic time;
- process instance ID;
- source, source version and provenance;
- tournament / market / exchange identity;
- strategy family / strategy ID when known;
- canonical BUILD-009 logical operation ID;
- canonical logical intent ID when known;
- canonical idempotency key;
- SIG exchange order ID and fill ID when available;
- Realtime revision;
- source/server timestamp when exposed;
- HTTP status when known;
- compact string detail pairs.

OBSERVE never invents a second order identity. Execution observations reuse BUILD-009 identities.

### Non-blocking emitter

`BoundedObservationEmitter` is the only dependency required by execution/market hot paths.

Producer semantics:

- synchronous `queue.put_nowait`;
- no JSON serialization;
- no Parquet write;
- no SQLite commit;
- no network I/O;
- queue-full returns `False` and increments `dropped`;
- sink exceptions increment `sink_failures` and never propagate into Risk/execution;
- queue depth, capacity and high-water are observable.

A broken observer cannot stop BUILD-009 Risk from protecting the account.

### Persistence

OBSERVE extends CAPTURE-001 rather than creating another lake.

New immutable streams under `PREDICTIONS_CUP_SIG_RESEARCH_PATH`:

- `venue_observations/`;
- `competition_context/`.

`LaunchSigRecorder` can persist these streams when the read-only SIG capture process owns the
CAPTURE recorder.

`ObservationCaptureRecorder` reuses the same CAPTURE writer/schema from runtimes such as MAKE
without opening a second SIG operational SQLite database. This preserves the existing rule that
MAKE must not make the operational SIG capture database an execution dependency.

## Venue lifecycle observations

The current instrumentation emits the following kinds where the accepted stack exposes a defensible
boundary:

| Kind | Exact meaning | Evidence class |
| --- | --- | --- |
| `DECISION_OBSERVED` | strategy/source observation monotonic timestamp copied from BUILD-009 audit metadata | normalized |
| `PLAN_CREATED` | BUILD-009 execution-plan creation monotonic timestamp | observed local boundary |
| `REQUEST_ENQUEUED` | immediately before durable journal-before-dispatch work begins | observed local boundary |
| `REQUEST_DISPATCHED` | immediately before entering the SIG trading-client call | observed local client-call boundary |
| `RESPONSE_RECEIVED` | trading client returned a validated response to BUILD-009 | observed local client-return boundary |
| `RESPONSE_PARSED` | same accepted DTO-return boundary in the current adapter | normalized |
| `ACK` | accepted SIG order response mapped to canonical operation/intent/order identity | normalized |
| `PARTIAL_FILL` | observable/account evidence shows positive fill quantity without terminal full-fill proof | normalized |
| `FILL` | response/replay evidence proves terminal fill for the recorded operation | normalized |
| `CANCEL_REQUESTED` | immediately before SIG cancel client call | observed local client-call boundary |
| `CANCEL_ACK` | cancellation response accepted by BUILD-009 | normalized |
| `REJECTED` | final typed client/API rejection | normalized |
| `UNCERTAIN` | BUILD-009 classified post-dispatch economic outcome as uncertain | normalized |
| `RECONCILIATION_STARTED` | authoritative account/startup reconciliation started | observed local boundary |
| `RECONCILIATION_RESOLVED` | authoritative reconciliation resolved the recorded uncertainty/state | normalized |
| `RATE_LIMIT` | final observed HTTP 429 at the execution boundary | observed/normalized |
| `SERVER_ERROR` | final observed 5xx classified by the execution adapter | observed/normalized |
| `TRANSPORT_EXCEPTION` | final transport outcome unknown after dispatch | normalized transport failure |
| `RECONNECT_STARTED` | SIG Realtime recovery preparation begins | observed local boundary |
| `RECONNECT_RESOLVED` | authoritative recovery/resubscribe preparation completes | observed local boundary |
| `REALTIME_REVISION_GAP` | delivery.previousRevision does not match last accepted revision | observed protocol state |
| `QUOTE_PUBLISHED` | SHADOW: conclusive local placement state; LIVE: authoritative per-intent ACK with usable order identity accepted into the QuoteRegistry | normalized/derived |
| `QUOTE_WITHDRAWN` | MAKE local lifecycle observed a successful cancellation result | derived |
| `QUOTE_REPLENISHED` | MAKE placed after terminal quote state with lifecycle reason `terminal_quote_refill` | derived |

### Important latency boundary limitation

`REQUEST_DISPATCHED -> RESPONSE_RECEIVED` is currently the complete SIG trading-client call
latency from BUILD-009's perspective. The hook sits outside `httpx`, so it includes governor wait,
bounded retries and response validation performed inside `SigTradingClient`.

It is **not** claimed to be:

- raw socket send -> first byte;
- a TCP/TLS handshake timer;
- per-attempt HTTP RTT;
- server processing time.

Those require separate transport-level/read-only characterization. The existing hook deliberately
does not mislabel a client-call boundary as wire latency.

`RESPONSE_RECEIVED` and `RESPONSE_PARSED` currently share the same monotonic sample because the
accepted trading-client method returns only after DTO validation. A future transport observer can
separate them without changing the durable observation schema.

HTTP status is retained only when the accepted adapter still exposes it. Single placement,
atomic multi-leg and single cancel have known successful status 200. Best-effort batch result rows
retain their documented per-result status. The current validated DTO returned for the overall
best-effort batch and cancel-all response does not retain whether the outer success was 200, 207 or
422, so those outer response observations persist `status_code=null` rather than inventing 200.

## Timestamp semantics

Every observation carries two clocks when applicable.

### `observed_at`

Timezone-aware UTC wall time sampled by the emitting process. Use this for cross-process and
cross-host chronology, accepting normal host-clock synchronization error.

### `monotonic_ns`

Process-local monotonic clock. Use this for durations only when `process_instance_id` matches.

Never subtract monotonic values from two processes or hosts.

### `source_timestamp`

Timestamp explicitly supplied by SIG/venue evidence, for example account fill `executedAt`.
Null means the accepted source did not expose a server/event timestamp for that evidence.

Missing source timestamps are retained as null. They are never silently replaced by local receive
time and relabeled as server time.

## Derived latency spans

`VenueSpanCollector` derives only same-process spans:

- decision -> dispatch;
- request enqueue -> ACK;
- dispatch -> client response;
- dispatch -> ACK;
- ACK -> first fill;
- dispatch -> terminal fill;
- cancel request -> cancel ACK;
- reconnect start -> reconnect resolution;
- local derived quote-published -> withdrawal/fill lifetime.

The quote lifetime is a MAKE lifecycle duration. It is **not** passive queue-position evidence.

## Quote mechanics

MAKE emits quote lifecycle markers only where identity is conclusive. SHADOW publication remains
derived from a conclusive simulated placement state. LIVE publication is emitted at the same
per-intent boundary that activates an authoritative quote in `QuoteRegistry`: an ACK must have a
positive exchange-order ID and a matching canonical logical intent. Aggregate
`BEST_EFFORT_BATCH=ACKED` is never treated as proof for all legs; each leg is considered
independently, so rejected or identity-ambiguous legs emit no publication marker.

Every quote lifecycle observation carries a deterministic `quote_key` comprising operation,
exchange and side. LIVE `QUOTE_PUBLISHED` also carries the canonical BUILD-009 logical intent ID
and authoritative exchange order ID. Quote lifetime is matched by canonical intent/order identity
when possible and otherwise by the side-specific quote key for our own withdrawal evidence. A fill
without an unambiguous intent/order match never terminates a quote lifetime.

This supports later reconstruction of:

- local quote lifetime;
- time to first observed fill;
- partial-fill sequence;
- terminal refill/replenishment;
- cancel/requote frequency;
- local resting duration.

The public SIG aggregate orderbook does not expose queue position or public order-level identity.
OBSERVE therefore does not claim:

- queue position;
- anonymous public participant identity;
- exact public-order add/cancel/replace sequence;
- exact reason for aggregate depth decreases.

CAPTURE-001 liquidity evidence remains conservative: depth reductions are ambiguous unless our own
order/account evidence resolves them.

## PM -> SIG observational timing

`predictions_cup.observe.cross_venue.join_pm_to_sig` consumes:

- accepted mapping identity/version;
- Polymarket economic value changes with observable timestamps;
- mapped SIG economic value changes with observable timestamps.

For each PM economic change it finds the next observed mapped SIG economic change inside the
configured horizon and records:

- PM token;
- SIG exchange;
- PM observation time;
- SIG observation time;
- elapsed seconds;
- PM delta;
- SIG delta;
- same-direction flag;
- mapping direction/class/version.

Complement mappings align Polymarket as `1-p` before computing the change.

Interpretation is deliberately fixed as:

> observed subsequent response; not causal lead-lag evidence

This layer never promotes alpha and never claims that PM caused the SIG move.

The accepted CAPTURE first-hours analysis remains the broad production path for stored PM/SIG
cross-venue diagnostics. OBSERVE supplies the typed pure join for replay/research reuse.

## Competition context

`SigOfficialCompetitionContextProvider` uses official participant-accessible endpoints only.

The supplied `api-1.json` establishes that participant keys can access:

- `/account`;
- tournaments;
- leaderboards;
- participant `myRank`.

The supplied API contract also marks
`/dmm/trader-analytics/signals` (Super Signal) as admin-only and unavailable to participant keys.
OBSERVE records this explicitly instead of approximating Super Signal from unrelated inventory or
trader data.

### Context fields

| Field | Classification | Source |
| --- | --- | --- |
| tournament name | normalized | SIG tournaments |
| tournament status | normalized | SIG tournaments |
| start/end date | normalized | SIG tournaments |
| initial balance | normalized | SIG tournaments |
| tournament `myBalance` | normalized | SIG tournaments |
| joined-at | normalized | SIG tournaments |
| account balance | normalized | SIG account |
| leaderboard rows | normalized | SIG tournament leaderboard |
| participant rank / `myRank` | normalized | SIG tournament leaderboard |
| leaderboard total | normalized | SIG tournament leaderboard |
| raw tournament response | observed/raw retained | SIG participant API |
| raw account response | observed/raw retained | SIG participant API |
| raw leaderboard response | observed/raw retained | SIG participant API |
| Super Signal | unavailable | admin-only DMM endpoint |

If the requested tournament is not returned by the participant API, tournament/rank fields are
persisted as `unavailable` with `TOURNAMENT_NOT_RETURNED`.

### Sampling

Competition context is deadline-driven and single-flight.

The sampler is triggered from existing maintenance activity and:

- does not create task-per-market work;
- does not busy-poll;
- skips a trigger while a snapshot is already in flight;
- isolates provider/persistence exceptions;
- exposes failure count, last error and next due time.

Current runtime interval is 60 seconds.

## Raw context retention

`competition_context` stores both normalized field metadata and raw accepted API responses:

- `fields_json`;
- `raw_tournament_json`;
- `raw_account_json`;
- `raw_leaderboard_json`.

This allows future normalization changes without losing the response evidence used at the time.

## Machine-readable summaries

`summarize_observations()` reports:

- event counts;
- operation count;
- p50/p95/p99 same-process latency spans;
- 429 count/rate;
- 5xx count/rate;
- transport exception count;
- uncertainty count/rate;
- reconnect count;
- Realtime revision-gap count;
- replenishment observation count;
- duplicate ACK evidence;
- duplicate fill evidence.

`summarize_cross_venue()` reports:

- number of matched PM -> SIG economic responses;
- p50/p95/p99 observed response delay;
- same-direction response rate;
- explicit non-causal interpretation.

`replay_operation()` returns the durable ordered observation lifecycle for one canonical
BUILD-009 logical operation ID.

Competition context is already machine-readable in each `competition_context` Parquet row.

## Runtime health surface

`ObservationHealthProvider.health()` returns an immutable typed
`ObservationHealthSnapshot` combining the outer emitter and inner CAPTURE writer.

The snapshot exposes:

- emitter queue depth/capacity/high-water, accepted, dropped, sink failures and worker liveness;
- CAPTURE queue depth/capacity/high-water, written rows/shards, dropped rows, storage failures and writer liveness;
- an explicit `HEALTHY`, `DEGRADED` or `BLOCKED` state plus machine-readable reasons.

MAKE exposes the current in-process snapshot through `MakerService.observation_health()`.
For process-boundary consumers, `ObservationHealthStatusPublisher` atomically writes the same
canonical `ObservationHealthSnapshot.to_dict()` payload to a small JSON status document at:

```text
<parent of PREDICTIONS_CUP_SIG_RESEARCH_PATH>/runtime/status/observe.json
```

The status envelope includes UTC `observed_at`, the observation process instance ID and
`owner="predictions-cup-maker.service"`. This matches FULLSTACK-001's default
`PREDICTIONS_CUP_FULLSTACK_STATUS_DIR=data/runtime/status` convention when the SIG research root
uses its canonical `data/sig_research` location. Publication is immediate on health-state/counter-signature change and otherwise
bounded to a one-second cadence. The atomic temp-write/fsync/replace path is deliberately outside
the execution hot path.

`read_observation_health_status()` gives FULLSTACK/operator tooling a typed cross-process reader.
It returns explicit `MISSING`, `STALE`, `OWNER_MISMATCH`, `PROCESS_MISMATCH` or `INVALID`
states instead of treating absent/stale evidence as healthy. Observation health remains
informational/operational evidence only and cannot bypass or stop BUILD-009 Risk. Publication
failure is caught by MAKE and does not affect BUILD-009 execution; failed attempts are themselves
cadence-bounded.

## Backpressure and failure policy

There are two bounded stages in runtimes that persist OBSERVE:

1. `BoundedObservationEmitter` protects execution from observer persistence;
2. CAPTURE-001's existing bounded immutable writer protects storage.

If the OBSERVE queue fills:

- the execution caller returns immediately;
- the observation is dropped;
- `dropped` increments.

If the observation sink raises:

- execution continues;
- `sink_failures` increments.

This differs intentionally from CAPTURE's primary lossless research feed, where queue exhaustion is
an explicit capture failure. OBSERVE cannot be allowed to become a Risk/execution dependency.

Operators must therefore monitor OBSERVE drop/failure counters and treat sustained drops as a
forensics-quality incident, not an execution-safety incident.

## Tests

OBSERVE-specific tests cover:

- lifecycle ordering/replay;
- duplicate ACK/fill evidence;
- missing source/server timestamp;
- partial fill;
- cancellation timing;
- UNCERTAIN;
- reconciliation markers;
- 429;
- 503;
- transport-outcome-unknown;
- Realtime revision gap;
- reconnect timing;
- PM -> SIG descriptive timing;
- complement mapping;
- unsupported Super Signal source;
- participant leaderboard/rank;
- backpressure/drop accounting;
- observation sink failure isolation;
- CAPTURE Parquet persistence;
- no cross-process monotonic subtraction.

BUILD-009 and MAKE regression suites remain the authority for economic safety/recovery semantics.

## Hot-path benchmark

The committed microbenchmark is:

```bash
python scripts/benchmark_observe001.py --iterations 100000
```

CI runs instrumentation OFF through `NullObservationEmitter` and ON through
`BoundedObservationEmitter` with a no-op sink, reporting:

- OFF median/p95/p99 synchronous call cost;
- ON median/p95/p99 synchronous enqueue cost;
- incremental median overhead;
- accepted count;
- dropped count;
- sink failure count.

The benchmark contains no network call, no file I/O and no real SIG order.

The combined producer/persistence benchmark is:

```bash
python scripts/benchmark_observe001_pipeline.py --iterations 10000
```

It exercises the outer bounded emitter, typed combined health surface and the inner CAPTURE Parquet
writer together. Final measured values belong in the PR/handoff for the exact final head SHA.

## Configuration

OBSERVE introduces no new environment variable.

It reuses existing CAPTURE/runtime settings:

- `PREDICTIONS_CUP_SIG_RESEARCH_PATH`;
- `PREDICTIONS_CUP_SIG_CAPTURE_QUEUE_MAX`;
- `PREDICTIONS_CUP_SIG_CAPTURE_PARQUET_SHARD_SECONDS`;
- `PREDICTIONS_CUP_SIG_CAPTURE_PARQUET_MAX_ROWS_PER_SHARD`;
- `PREDICTIONS_CUP_TOURNAMENT_ID`;
- `PREDICTIONS_CUP_TOURNAMENT_SLUG`;
- existing SIG read credential for context reads;
- existing trade credential only when the separately authorized LIVE MAKE runtime already requires it.

No new secret is introduced.

## Restart / migration implications

Schema change is additive.

New CAPTURE stream directories are created automatically under the existing research root.
No database migration is required.

A process restart creates a new observation process/session identity. This is desirable because
monotonic clocks must never be compared across process lifetime boundaries.

Deployment implications:

- install the exact reviewed code;
- restart the affected SIG capture service to enable context/reconnect observations;
- restart MAKE to enable BUILD-009/account/quote lifecycle observations;
- no trading-mode change is required;
- no real SIG order is required to validate OBSERVE.

## Known limitations

- client-call dispatch/return timing is not raw socket first-byte timing;
- TLS/connection-setup and keepalive decomposition remain separate venue-characterization work;
- response-receive and response-parsed are not yet separable at the BUILD-009 adapter;
- public SIG queue position is unavailable;
- aggregate-depth disappearance is not sufficient to infer a cancellation;
- Super Signal is unavailable to participant credentials in the supplied official contract;
- cross-venue elapsed time is descriptive observable-time sequencing, not causal lead-lag;
- context sampling currently uses a fixed 60-second deadline;
- dropped observation payloads themselves cannot be reconstructed after the fact, but their loss
  counters/state are externally published while the owner process is alive.

## Safety statement

OBSERVE-001 contains no strategy promotion and no code path whose purpose is to create an economic
order for instrumentation.

**REAL SIG ORDERS SENT: NO**
