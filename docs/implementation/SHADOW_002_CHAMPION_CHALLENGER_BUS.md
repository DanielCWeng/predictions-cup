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
MakerRuntimeLoop snapshot observer
  - exact MakerMarketSnapshot objects used by MAKE
  - synchronous put_nowait only; never awaits SHADOW
        |
        v
bounded ShadowBus ingress
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
ShadowBus internal fan-out
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

`Pred006Candidate` accepts an exact frozen runtime evaluator. CANDIDATE-RUNTIME-001
now supplies `FrozenPred006Evaluator` in production SHADOW wiring. It exposes
frozen provenance/readiness and currently fails closed as
`NOT_READY:model_artifact_missing`; no retraining, threshold change,
approximation or post-hoc feature replacement is performed.

### EXPERIMENT-005F

`Hazard005FCandidate` accepts an exact live hazard evaluator.
CANDIDATE-RUNTIME-001 now supplies `Frozen005FEvaluator` plus an incremental
genuine-BBO state contract. Current production wiring has no exact current-universe
order-book history provider, so it fails closed as
`NOT_READY:required_orderbook_history_unavailable`. Its payload explicitly labels
the output `movement_hazard_not_directional`; it never converts hazard into
directional FV.

### R3 / ETS

`StructuralFairValueCandidate` accepts an optional provider returning market ID,
as-of FV/bounds/confidence, method ID, source count, freshness and quality flags.
The default provider is a no-op and emits `NOT_READY`. Production core does not
import the active R3 research branch.

## Fan-out and failure isolation

Every candidate has its own bounded queue and worker. The production default is
512 pending states, which retains a full 237-market Cup sweep with headroom.
Production ingress is a separate bounded queue (default 4,096). MAKE calls a
synchronous snapshot observer which only performs `put_nowait`; SHADOW therefore
cannot stall the MAKE coordinator, central Risk or execution path. The ingress
worker freezes exactly one `CanonicalShadowSnapshot` for each submitted immutable
`MakerMarketSnapshot` and every enabled candidate receives that same snapshot
object/snapshot ID. Ingress saturation is explicit through `ingress_rejected`;
there is no silent blocking or silent loss.

If a queue is full, the oldest pending state is explicitly coalesced in favor of
the latest state. The counter is exposed in health. There is no unbounded growth.

Each evaluation runs in an isolated worker via `asyncio.to_thread` with a timeout.
A candidate is allowed at most one in-flight evaluation. If it times out, SHADOW
records `TIMEOUT`, quarantines that candidate, drains/skips its pending states and
does not schedule another evaluation until the underlying call actually returns.
The quarantine/in-flight state and counters are exposed in health. Python still
cannot force-kill a stuck thread, but the failure is bounded to one worker call per
candidate rather than accumulating an unbounded number of timed-out threads.

Supported states are:

`OK, ABSTAIN, NOT_READY, STALE_INPUT, UNTRUSTED_INPUT, INVALID_OUTPUT,
TIMEOUT, EXCEPTION, DISABLED`.

NaN, infinite or out-of-range probability outputs fail closed as
`INVALID_OUTPUT`. One candidate exception is persisted and other candidates
continue.

## Persistence

`JsonlEventStore` is the immutable replay journal. It writes two event types:

- one canonical `snapshot` event per decision state;
- one `decision` event per evaluated candidate.

Filesystem writes and fsync run off the candidate/MAKE path. JSONL events are
batched (default 256 records) so durability does not require one fsync per event.
The persistence queue is bounded (default 65,536) and health exposes queue
high-water, batch count and p95 batch-write latency. Persistence slowdown can
backlog SHADOW ingress, but cannot await on MAKE; eventual ingress saturation is
reported explicitly.

When `shadow_capture_mirror_enabled=true`, every CandidateDecision is also emitted
to CAPTURE-001's canonical `strategy_events` Parquet stream through the existing
bounded `ImmutableCaptureSink`. JSONL remains authoritative for exact snapshot
replay; CAPTURE `strategy_events` is the common research/LIVE-LEARN decision
surface. The mirror payload contains `decision_id` and `input_snapshot_id`, so
offline jobs reconcile the two surfaces by those IDs plus market/exchange/time.

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
- ingress queue depth/high-water, rejection count and ingress error;
- candidate enabled/quarantined/in-flight state;
- queue depth/high-water;
- skipped states;
- last success/error;
- p50/p95/p99 evaluation latency;
- p95 queue delay;
- failure, timeout and quarantine counts;
- persistence health, queue high-water, batch count and write-latency p95.

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

## Launch integration

`MakerService` now optionally composes SHADOW when
`PREDICTIONS_CUP_SHADOW_ENABLED=true`. `MakerRuntimeLoop` invokes the
non-blocking snapshot observer on the exact immutable snapshot mapping immediately
before the coordinator sees it. The production path is therefore:

`SIG + PM state -> MakerSourceBridge -> MakerMarketSnapshot -> non-blocking SHADOW
ingress -> CanonicalShadowSnapshot -> candidates -> JSONL + CAPTURE strategy_events`.

SHADOW does not receive a placement/cancel dispatcher, SIG trading client or
execution sink. Enabling SHADOW does not change BUILD-009 Risk authority.

## Sustained acceptance

In addition to the one-shot 237-market benchmark,
`scripts/soak_shadow002.py` drives 237 markets x 6 candidates over repeated cycles
through the real non-blocking MakerSnapshot ingress and durable JSONL persistence.
The soak hard-fails on any ingress rejection, candidate coalescing/drop, persistence
failure, event-count mismatch or JSONL readback mismatch, and reports ingress,
candidate and persistence high-water plus submit overhead, persistence write
latency and event throughput.

## Known limitations

1. PRED-006 has an exact runtime evaluator/feature boundary but remains
   `NOT_READY:model_artifact_missing` until a separately authorized frozen fit
   produces serialized C01/C02 pipelines.
2. 005F has an exact genuine-BBO runtime evaluator/state boundary but remains
   `NOT_READY:required_orderbook_history_unavailable` until exact current-universe
   history is wired and frozen model binaries are recovered/hash-checked.
3. R3/ETS is hook-only until research publishes a stable provider.
4. A permanently wedged Python candidate thread cannot be force-killed in-process;
   quarantine bounds it to one in-flight call. Such a provider should be restarted
   or moved behind a process boundary if it proves unsafe operationally.
5. Final launch readiness still requires a production-host full-stack rehearsal
   with live market feeds, SHADOW enabled, zero ingress rejection and readable
   decision evidence after restart.
