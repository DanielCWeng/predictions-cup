# CAPTURE-001 — Launch Capture + First-Hours Forensics

**Status:** implementation branch / pre-review  
**Branch:** `capture/001-launch-forensics`  
**Base:** `71835eb9d6312234eacdce357b5c2be0aeedda28`  
**Launch:** 1 October 2026, 17:00 BST / 16:00 UTC

## Purpose

CAPTURE-001 extends the accepted BUILD-004/005/006/007/009 stack. It does not create a
second market-state engine or a second Polymarket collector.

The launch shape is:

```text
SIG tournament Realtime
  -> accepted state/reconciliation engine
  -> operational SQLite/WAL (existing replay compatibility)
  -> bounded non-blocking capture queue
  -> dedicated immutable ZSTD Parquet writer

SIG authoritative REST
  -> broad scalar/BBO snapshots
  -> tracked authoritative depth only
  -> same operational + immutable research surfaces

accepted Polymarket recorder
  -> operational SQLite health/metadata
  -> immutable observations/book_changes/trades/depth_snapshots Parquet

BUILD-009 / MAKE
  -> canonical execution journal
  -> optional strategy/shadow capture hook

all launch artifacts
  -> predictions_cup.analysis.first_hours
```

Trading/event-loop code never performs Parquet I/O. Research persistence uses `put_nowait`
against a bounded queue. Queue exhaustion raises visibly instead of silently dropping evidence.
The writer uses short immutable Parquet shards published through temp-file write, file fsync,
atomic replace and directory fsync.

## Raw evidence versus interpretation

CAPTURE-001 preserves two separate evidence layers.

### Raw SIG evidence

`raw_events` is the faithful decoded `market_batch` payload delivered to the state-engine
callback. It preserves:

- process `session_id`;
- subscription `connection_epoch`;
- schema version;
- topic/tournament identity when present;
- delivery revision and previous revision;
- source sequence range;
- local UTC receive time;
- monotonic receive time;
- parse/validation time;
- validation error class for rejected payloads;
- compact JSON representation of the complete decoded payload.

This is a faithful decoded representation, **not the original websocket frame bytes**. The current
accepted subscriber exposes the decoded payload to the callback, not an untouched wire-frame
buffer. CAPTURE-001 does not claim byte-for-byte websocket-frame archival.

Malformed decoded payloads are archived before authoritative recovery begins.

### Normalized SIG evidence

`normalized_events` records defensible interpretations while leaving raw evidence untouched.
Provenance values distinguish `SIG_REALTIME`, `SIG_REST_AUTHORITATIVE`,
`LOCAL_STATE_ENGINE` and subscription lifecycle state.

The normalized stream includes delivery metadata, trades, dirty invalidations, settlement signals,
authoritative market snapshots, broad BBO snapshots, authoritative tracked depth snapshots, missing
BBOs, trust transitions and connection boundaries.

## Schema dictionary

All high-frequency research streams are immutable Parquet and carry
`schema_version=capture-001-v1`.

### raw_events

| Field | Meaning |
| --- | --- |
| session_id | process-lifetime capture identity |
| connection_epoch | incrementing socket/subscription epoch |
| source | currently `SIG_REALTIME_DECODED` |
| event_type | `MARKET_BATCH` |
| topic / tournament_id | source identity |
| revision / previous_revision | topic-local delivery continuity |
| source_sequence_from / through | engine provenance, not the gap counter |
| local_receive_at | UTC time callback received the decoded batch |
| monotonic_receive_ns | monotonic receive clock |
| parsed_at | local UTC validation/parse completion |
| validation_error | rejected-payload error class, otherwise null |
| raw_json | complete compact decoded payload |

### normalized_events

Core fields are session/connection/schema/source/event identity plus tournament/exchange/market,
venue timestamp, observable local timestamp, monotonic timestamp, revision, trust state,
provenance, evidence label, scalar price/quantity/BBO/spread, reason and `payload_json`.

### liquidity_events

Authoritative tracked-depth snapshot-to-snapshot transitions:

- `LEVEL_APPEARED`;
- `LEVEL_INCREASED`;
- `LEVEL_DECREASED`;
- `LEVEL_DISAPPEARED`;
- `BBO_MOVED`;
- `SPREAD_WIDENED`;
- `SPREAD_NARROWED`.

Evidence labels are deliberately conservative:

- increases: `OBSERVED_LEVEL_INCREASE`;
- reductions/disappearances: `AMBIGUOUS_DEPTH_DECREASE`;
- BBO/spread transitions: `OBSERVED_STATE_TRANSITION`.

A depth decrease is not called a cancellation.

### strategy_events

Forward-compatible optional MAKE/shadow surface:

- strategy ID/version;
- FV provider/version;
- signal provider/version;
- exchange/market/tournament;
- observable and monotonic time;
- opaque versioned payload.

MAKE owns quote/fill simulation semantics. CAPTURE does not invent passive queue position.

### ets_state

Optional future aggregate/ETS surface:

- graph ID;
- model version;
- composite FV;
- uncertainty;
- component values;
- constituent freshness;
- constituent provenance.

ETS completion is not required for launch.

## SIG capability and limitation findings

The supplied accepted API/feed semantics support the following tournament market tape:

- `trades[]`: exchange ID, market ID, tournament ID, price, quantity, execution time;
- `bookDirty[]`: exchange ID, market ID, tournament ID, source `at`;
- `marketSettled[]`: market identity, tournament identity, outcome and source `at`;
- `delivery`: revision, previous revision, correlation ID and source sequence provenance;
- broad REST scalar state through `GET /exchanges/prices`;
- authoritative aggregate depth through the accepted exchange-orderbook REST path;
- authoritative market status/settlement through REST.

The market feed does **not** expose enough information to claim:

- order-level add/cancel/replace events;
- persistent anonymous participant/account identity;
- maker/taker/aggressor account identity;
- public market order IDs tied to aggregate depth;
- passive queue position;
- per-order expiration timestamps in the aggregate orderbook.

`bookDirty` is an invalidation signal, not an order-book delta. SIG also documents silent order
expiry. The aggregate participant orderbook does not expose expiry metadata, so BUILD-006's tracked
30-second refresh remains a project safety policy, not an exchange-provided exact expiry clock.

The authoritative aggregate REST orderbook has no venue/server timestamp; its canonical observation
time is the local UTC time immediately after the response is received.

Our own account/order identity is available through BUILD-009/account Realtime and the execution
journal and must not be confused with anonymous public participant identity.

## Full-depth policy

CAPTURE-001 preserves BUILD-006's finite REST-budget model.

- Tournament-wide Realtime remains full universe.
- Tournament-wide broad scalar/BBO state uses the bulk price endpoint, max 100 IDs/request.
- Full depth is maintained only for explicit tracked exchange IDs.
- Tracked dirty/recovery work uses the shared REST governor.
- Untracked `bookDirty` is recorded but does not trigger a full-book request.
- Depth trust remains `UNTRACKED_DEPTH`, `TRACKED_UNTRUSTED` or `TRACKED_TRUSTED`.
- Missing/stale/reconciling state is recorded rather than silently cleaned away.

The accepted default REST governor remains 2 request starts/second because SIG does not publish a
numeric limit in the supplied contract. Do not configure a tracked universe whose freshness demand
fails BUILD-006's capacity check.

## Polymarket synchronization

CAPTURE-001 reuses the accepted Polymarket recorder. It already writes:

- 1-second observable BBO/scalar observations;
- normalized book changes;
- public trades;
- periodic depth snapshots;
- source timestamps and local observation timestamps;
- immutable ZSTD Parquet.

Production IDs must come from:

`data/mappings/sig_polymarket_2026.json`

The two venue collectors do not pretend to share a venue clock. Cross-venue analysis uses each
source's event time where supplied and the local receive/observation clocks for observable-time
ordering.

Later ETS/aggregate Polymarket markets can be added to the accepted Polymarket universe without
changing the CAPTURE-001 schemas.

## Own execution / MAKE integration

BUILD-009 remains canonical for real/shadow execution lifecycle and timing. Its durable journal
already links logical operation, idempotency identity, strategy family/ID, signal, FV, exchange,
submission, dispatch, acknowledgement, fill and reconciliation with monotonic timestamps.

CAPTURE-001 does not duplicate or weaken that crash-safe journal. The first-hours command reads it
directly. The optional `record_shadow_make` surface exists for MAKE-specific hypothetical quote,
replacement, inventory and simulation metadata that does not belong in the wire execution journal.

Passive hypothetical fills require an explicit MAKE simulation rule. CAPTURE never assumes queue
priority.

## Replay

The existing BUILD-005 replay loaders continue to consume operational SIG SQLite and immutable
Polymarket capture with observable-time semantics. CAPTURE-001 preserves that surface while adding
the immutable raw/normalized SIG research tape.

Future replay readers may use the Parquet streams directly, but CAPTURE-001 does not remove or
silently alter the accepted replay input.

## First-hours forensics

Run:

```bash
python -m predictions_cup.analysis.first_hours \
  --input data/sig_research \
  --polymarket-root data/polymarket_research \
  --execution-journal data/execution_journal.sqlite3 \
  --mapping data/mappings/sig_polymarket_2026.json \
  --output data/first_hours
```

Outputs:

- `summary.json` — machine-readable integrity/market/PM/execution/cross-venue summary;
- `report.md` — analyst-readable first pass;
- `market_activity.csv` — event/activity ranking by exchange;
- `market_microstructure.csv` — update/trade rates, spread, BBO lifetime and movement diagnostics;
- `markouts.csv` — maker-perspective multi-horizon markouts for defensibly classified trades;
- `depth_summary.csv` — tracked depth and top-level concentration;
- `activity_15m.csv` — burst/regime inspection buckets;
- `cross_venue_diagnostics.csv` — direct mapped economic-change response lags and direction;
- `cross_venue_latest.csv` — latest direction-aligned direct SIG/Polymarket discrepancies;
- `execution_latency.csv` — per-operation monotonic execution-lifecycle timing.

The microstructure analysis includes:

- spread distribution and BBO availability;
- economic BBO lifetime and renewal/update intensity;
- market activity ranking;
- trade-arrival intensity and size;
- observable aggressor classification only when a trade is at/through a recent captured BBO;
- midpoint move and jump diagnostics;
- tracked depth distribution and concentration;
- maker-perspective 1s/5s/30s/300s markouts for classified trades;
- adverse-selection rate;
- the actual BBO sampling delay used for each nominal markout horizon;
- latest direct mapped SIG/Polymarket discrepancy after `SAME`/`COMPLEMENT` alignment;
- PM→SIG and SIG→PM nearest-subsequent economic BBO-change lag within 60 seconds;
- same-direction response rate for those matched economic changes;
- BUILD-009 observation→decision, decision→submission, submission→network-dispatch,
  dispatch→ACK/fill and observation→ACK/fill timing where journal fields are available.

A nominal horizon is never silently treated as exact if the next captured BBO arrives later. The
markout table reports sampling-delay percentiles and rejects observations more than 30 seconds late.

The cross-venue lag table is an observable-time response diagnostic only. It does not claim that
the earlier venue caused the later change, and its interpretation must account for the very
different SIG/Polymarket sampling/update cadences.

This is descriptive launch forensics. It is not a confirmed 005F transfer or a promoted trading
signal.

## Data quality and failure behaviour

Continuously reconstructible/observable evidence includes:

- delivery revisions and previous revisions;
- duplicate revisions;
- malformed decoded batches;
- reconnect/subscription epochs;
- trust transitions;
- stale/tracked/untracked depth state through accepted engine records;
- authoritative reconciliation observations;
- raw and normalized local clocks;
- research writer queue depth/capacity;
- published rows/shards;
- dropped-row counter;
- storage-failure counter;
- last successful Parquet publication time;
- PM operational health in the existing recorder;
- execution lifecycle/trust in BUILD-009.

A full research queue raises `CaptureBackpressureError`. A failed writer causes subsequent
producers to raise `CaptureStorageError`. Neither path silently discards evidence.

Already-published Parquet shards are immutable. Restart creates new uniquely named shards. Temporary
files are never published as final shards.


## Synthetic persistence burst evidence — 29 September 2026

The new SIG immutable writer was exercised on the current ARM64 EC2 runtime host at benchmark code
head `27f2a4b4aa308320597defb9e13efe8413795296`:

```bash
python -m predictions_cup.analysis.capture_benchmark \
  --output /tmp/capture001_bench \
  --rows 50000 \
  --queue-max 200000 \
  --max-rows-per-shard 5000 \
  --shard-seconds 60
```

Observed result:

- 50,000 raw SIG batches emitted and **50,000 / 50,000 read back**;
- producer throughput: **5,099.7 rows/s**;
- end-to-end producer + immutable flush throughput: **5,039.4 rows/s**;
- enqueue/serialization latency: **p50 94.1 us, p95 228.2 us, p99 1,084.5 us**;
- queue high-water: **807 / 200,000**; depth before close: 43;
- dropped rows: **0**;
- storage failures: **0**;
- published files: 11; published bytes: 1,401,208;
- Python `tracemalloc` peak: 6,723,063 bytes;
- Parquet readback scan: ~5.1 ms.

This is a deliberately adversarial local persistence burst, not a claim about SIG network throughput.
It validates bounded producer/backpressure behaviour, row-cap shard rolling, atomic publication and
readback on the launch-class host. It does **not** replace the final credentialed paired
SIG/Polymarket soak, systemd restart, SSH-independence and reboot gate below.

## Launch operation

Recommended runtime values:

```text
PREDICTIONS_CUP_SIG_RESEARCH_PATH=data/launch_20261001/sig
PREDICTIONS_CUP_POLYMARKET_RESEARCH_PATH=data/launch_20261001/polymarket
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<accepted mapping IDs>
PREDICTIONS_CUP_TRADING_ENABLED=false
```

SIG read-only launch command:

```bash
python -m predictions_cup.sig.capture \
  --runtime-env-only \
  --tournament-id <TOURNAMENT_UUID> \
  --research-root data/launch_20261001/sig \
  --print-health
```

Tracked depth remains explicit through
`PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS` or repeated
`--tracked-exchange-id`. Do not make every tournament market tracked merely for research.

Polymarket remains the accepted supervised recorder:

```bash
python -m predictions_cup.external.polymarket.recorder \
  --runtime-env-only \
  --require-explicit-universe
```

For normal EC2 operation use the accepted BUILD-007 systemd units and runtime.env rather than
interactive shells.

## Validation evidence — 29 September 2026

Local quality and artifact validation on the launch branch:

- Ruff: **PASS**;
- strict mypy: **PASS** across 192 source files;
- full pytest: **577 passed, 3 skipped**;
- focused CAPTURE-001 + SIG Realtime regression battery: **18 passed**;
- normal application startup: **PASS**;
- application smoke mode: **PASS**;
- restart regression: previously published immutable shards remain readable and a new recorder
  session appends distinct shards without publishing temp files;
- first-hours report: **PASS** against a credentialed live SIG soak, producing report/summary,
  market activity, microstructure, markout, depth and 15-minute activity outputs.

Synthetic bounded-writer stress evidence:

- 100,000 decoded raw events submitted;
- producer rate: approximately **35,584 events/second**;
- producer phase: **2.8102 seconds**;
- drain/close: **0.1978 seconds**;
- immutable Parquet shards: **10**;
- rows read back: **100,000 / 100,000**;
- queue depth after producer phase: **20,000 / 200,000**;
- dropped rows: **0**;
- storage failures: **0**;
- process peak RSS: approximately **142 MiB**.

A finite credentialed **read-only** SIG soak was then run from this branch without loading
`trade.env`, with trading forced false and tracked full depth deliberately empty. Observed:

- tournament universe: **237 markets / 237 exchanges**;
- authoritative market enumeration: successful;
- broad scalar/BBO seed: **3** bulk-price calls;
- total governed REST requests: **7**;
- HTTP 429s: **0**;
- reconciliation failures: **0**;
- tracked-depth exchanges: **0 by deliberate policy**;
- queue high-water: **100 / 200,000**;
- dropped rows: **0**;
- storage failures: **0**;
- normalized research rows published during the short soak: **475**;
- published shards during the run: **1** before final close flush;
- first-hours command consumed the resulting capture immediately after stop.

The short credentialed window happened to receive no market Realtime batch, so it does **not**
constitute live evidence for a raw `market_batch` arrival. Raw accepted/malformed batch capture,
revision provenance and replayable decoded-payload publication are covered by direct regression
tests and the 100,000-event writer stress test. A longer pre-launch paired soak should obtain live
Realtime events before the branch is called fully production-validated.

The first-hours live-soak report observed 237 broad BBO rows, a 73.4% two-sided BBO availability
rate in that snapshot, no revision gaps, and no depth rows because full depth was intentionally
untracked. The depth CSV now emits a schema header even when policy yields zero rows.

## Launch gate / soak checklist

Before 1 October 17:00 BST, verify on the intended launch host:

1. exact reviewed branch/head installed;
2. runtime.env contains read-only capture settings and no trade credential;
3. SIG tournament ID resolves and full universe seeds;
4. accepted mapped Polymarket universe resolves;
5. both supervised collectors remain active across a finite soak;
6. raw SIG and normalized SIG Parquet shards appear and read back;
7. PM observations/book changes/trades/depth shards appear and read back;
8. queue depth remains bounded with `dropped_rows=0` and `storage_failures=0`;
9. revision-gap, malformed-payload, 429 and reconnect regression tests are green;
10. restart leaves prior shards readable and produces new shards;
11. disconnect/reconnect SSH does not affect services;
12. operator-controlled reboot returns both configured collectors;
13. `first_hours` runs successfully against the resulting directories immediately after stop/restart;
14. no capture process loads `trade.env`.

A credentialed CAPTURE-001-specific live soak/reboot result must be recorded before claiming the new
SIG Parquet layer production-validated. Existing BUILD-006/007 live evidence validates the accepted
underlying state/governor/Parquet mechanisms but is not substituted for that final new-layer gate.
