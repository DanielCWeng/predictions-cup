# SHADOW-002 — Champion / Challenger Runtime Bus

## Status

Implemented on `build/shadow-002-champion-challenger-bus` from main
`67d520f23a358c7deaf0f448c6f642795815cd5f`.

SHADOW-002 is a common live research/control plane. It does not submit, cancel, or
modify real SIG orders and it refuses construction when `trading_enabled=True`.

## Architecture

```text
MakerMarketSnapshot / RuntimeSnapshot
        |
        v
CanonicalShadowSnapshot.freeze()
  - deterministic snapshot_id
  - UTC observable time
  - local monotonic decision time
  - accepted mapping version
  - source revision/provenance
  - immutable external-quote mapping
        |
        v
ShadowBus.publish()
        |
        +--> append-only snapshot event
        |
        +--> bounded candidate queue: MAKE
        +--> bounded candidate queue: direct PM reference
        +--> bounded candidate queue: PRED-006
        +--> bounded candidate queue: 005F hazard
        +--> bounded candidate queue: R3/ETS hook
        |
        v
CandidateOutput
        |
        v
CandidateDecision
  - deterministic decision_id
  - candidate/version/family
  - snapshot provenance
  - optional FV/bounds/confidence
  - direction/score/action/quote intent
  - status/failure/quality flags
  - compute latency
        |
        v
append-only decision event -> replay / LIVE-LEARN
```

BUILD-009 and MAKE remain authoritative for real strategy -> central Risk ->
ExecutionPlan -> sink behavior. SHADOW-002 does not add a parallel execution path.

## Canonical state contract

`CanonicalShadowSnapshot` wraps the existing `MakerMarketSnapshot`, which already
contains the compact `RuntimeSnapshot`, SIG book/account/inventory state and
Polymarket external quotes. The external-quote mapping is copied into a read-only
mapping before fan-out. Runtime models are already frozen dataclasses/tuples.

The snapshot ID is a SHA-256 fingerprint over the observable state, UTC observation
time, local monotonic boundary, mapping version and source provenance. Replaying the
same persisted state therefore reconstructs the same snapshot ID.

The journal writes the canonical snapshot once. Candidate decisions only reference
`input_snapshot_id`; giant raw payloads are not copied into each decision.

## Candidate contract

Candidates implement:

```python
class ShadowCandidate(Protocol):
    candidate_id: str
    candidate_version: str
    strategy_family: str

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        ...
```

A candidate may emit FV, bounds, confidence, direction, score, action intent,
quote intent, abstention and a JSON-safe candidate-specific payload. SHADOW adds
the durable `CandidateDecision` envelope and compute timing.

Adding a challenger requires implementing this interface and registering it with
`ShadowBus`; central bus logic does not change.

## Initial adapters

### MAKE-001

`MakerCandidate` calls the merged MAKE `quote()` method. It exposes MAKE's own
trace and desired quote as a hypothetical decision. It does not alter fair-value,
inventory, spread, sizing, eligibility or quote-lifecycle mathematics.

### Direct Polymarket reference

`DirectPmCandidate` reuses `DirectPolymarketFairValueProvider` and the accepted
mapping. SAME / COMPLEMENT handling remains in the existing provider. The adapter
adds mapping class/direction, source age, SIG midpoint and PM-minus-SIG residual.

This is a baseline/reference, not an alpha claim.

### PRED-006

`Pred006Candidate` accepts an exact frozen runtime evaluator. With no evaluator,
it emits `NOT_READY` and `runtime_feature_parity_not_wired`. There is no
retraining, threshold change, approximation or post-hoc feature replacement.

### EXPERIMENT-005F

`Hazard005FCandidate` accepts an exact live hazard evaluator. Without live parity,
it emits `NOT_READY`. Its payload explicitly labels the output
`movement_hazard_not_directional`; it never converts hazard into directional FV.

### R3 / ETS

`StructuralFairValueCandidate` accepts an optional provider returning market ID,
as-of FV/bounds/confidence, method ID, source count, freshness and quality flags.
The default provider is a no-op and emits `NOT_READY`. Production core does not
import the active R3 research branch.

## Fan-out and failure isolation

Every candidate has its own bounded queue and worker. The production default is 512 pending states, which retains a full 237-market Cup sweep with headroom. `publish()` freezes no new
state after the boundary; every eligible candidate receives the same
`CanonicalShadowSnapshot` object/snapshot ID.

If a queue is full, the oldest pending state is explicitly coalesced in favor of
the latest state. The counter is exposed in health. There is no unbounded growth.

Each evaluation runs in an isolated worker via `asyncio.to_thread` with a timeout.
A timeout does not block another candidate worker. Python cannot kill a timed-out
thread, so candidates must remain pure/read-only; a timed-out computation may finish
in the executor after SHADOW has already recorded `TIMEOUT`.

Supported states are:

`OK, ABSTAIN, NOT_READY, STALE_INPUT, UNTRUSTED_INPUT, INVALID_OUTPUT,
TIMEOUT, EXCEPTION, DISABLED`.

NaN, infinite or out-of-range probability outputs fail closed as
`INVALID_OUTPUT`. One candidate exception is persisted and other candidates
continue.

## Persistence

`JsonlEventStore` is an immutable append-only event journal. It writes two event
types:

- one canonical `snapshot` event per decision state;
- one `decision` event per evaluated candidate.

Filesystem writes and fsync run in a background thread, not in the strategy
calculation worker. The persistence queue is bounded. Saturation creates explicit
backpressure rather than unbounded memory growth.

A JSONL event journal is used here instead of adding a third giant database. It is
the durable event surface; downstream compaction to Parquet belongs outside the
SHADOW hot path.

## Replay

`load_persisted_snapshots()` reconstructs and re-fingerprints snapshot events.
A fingerprint mismatch fails closed.

`ShadowReplayRunner` preserves captured snapshot order and candidate registration
order. Deterministic candidates produce a semantic hash that excludes runtime
compute timings. Randomized future candidates must freeze their seed in their
candidate version/configuration; SHADOW does not silently provide wall-clock
randomness.

## Health/control plane

`ShadowBus.health()` exposes:

- running / paused state;
- last snapshot age;
- snapshots processed and dropped/coalesced;
- candidate enabled state;
- queue depth/high-water;
- skipped states;
- last success/error;
- p50/p95/p99 evaluation latency;
- p95 queue delay;
- failure and timeout counts;
- persistence health and queue high-water.

Control actions are `enable_candidate`, `disable_candidate`, `pause` and
`resume`. They never enable trading.

## BUILD-009 integration

SHADOW outputs are observations, not execution permission. A future adapter may
translate a candidate decision into a BUILD-009 `Opportunity`, but central Risk
and the existing execution interlocks remain authoritative. That admission step is
deliberately outside SHADOW-002.

## LIVE-LEARN downstream contract

The journal has stable keys for:

- snapshot/candidate/version;
- market/exchange/tournament;
- observable time;
- FV/bounds/confidence;
- direction/score;
- action and quote intent;
- coverage/abstention/failure;
- compute latency and provenance.

LIVE-LEARN can therefore join future 5m/15m/1h outcomes to decisions without
strategy-specific journal formats.

## Tests

`tests/test_shadow002.py` covers:

- identical same-state fan-out;
- candidate exception isolation;
- timeout isolation;
- invalid output rejection;
- enable/disable and pause;
- bounded queue coalescing;
- observable-time ordering;
- deterministic replay;
- append/restart journal preservation;
- direct PM mapping/residual adapter;
- fail-closed PRED-006/005F/R3 hooks;
- refusal to run with live trading enabled.

## Benchmark

`scripts/benchmark_shadow002.py` measures a 237-market multi-candidate burst,
publish overhead p50/p95/p99, decision throughput, per-candidate latency,
queue high-water/coalescing and append-only persistence cost.

No artificial microsecond acceptance target is encoded. The benchmark reports the
observed numbers so morning review can judge whether orchestration is small relative
to venue/network latency and whether MAKE is materially delayed.

## Known limitations

1. SHADOW is not yet wired into the launch daemon/event source; this build provides
   the production-quality bus/contracts/adapters and a clear integration surface.
2. PRED-006 and 005F remain `NOT_READY` until exact live feature-parity evaluators
   are supplied.
3. R3/ETS is hook-only until research publishes a stable provider.
4. JSONL is the durable event journal; Parquet compaction/evaluation belongs to
   LIVE-LEARN or an offline process.
5. Timed-out Python thread work cannot be force-killed. Candidate code must stay
   pure and side-effect free.
