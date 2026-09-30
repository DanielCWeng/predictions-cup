# SHADOW-002 runbook

## Safety boundary

SHADOW-002 is observation/evaluation only.

Required acceptance environment:

```bash
export PREDICTIONS_CUP_TRADING_ENABLED=false
```

`ShadowBus(..., trading_enabled=True)` fails immediately. SHADOW has no SIG order-write
adapter and does not accept a trade credential.

## Startup composition

SHADOW is disabled by default. Production composition is owned by `MakerService`;
do not create a second independent SIG/Polymarket subscriber.

Recommended SHADOW settings:

```text
PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_SHADOW_ENABLED=true
PREDICTIONS_CUP_SHADOW_JOURNAL_PATH=data/live/shadow_002/events.jsonl
PREDICTIONS_CUP_SHADOW_CANDIDATE_QUEUE_CAPACITY=512
PREDICTIONS_CUP_SHADOW_INGRESS_QUEUE_CAPACITY=4096
PREDICTIONS_CUP_SHADOW_PERSISTENCE_QUEUE_CAPACITY=65536
PREDICTIONS_CUP_SHADOW_PERSISTENCE_BATCH_SIZE=256
PREDICTIONS_CUP_SHADOW_CANDIDATE_TIMEOUT_MS=50
PREDICTIONS_CUP_SHADOW_CAPTURE_MIRROR_ENABLED=true
```

When enabled, `MakerService` builds SHADOW from the accepted mapping and the
already-constructed MAKE engine. `MakerRuntimeLoop` passes the exact immutable
`MakerMarketSnapshot` objects used for the MAKE decision through a synchronous
non-blocking observer. That observer only performs bounded `put_nowait` admission
into SHADOW; it never awaits persistence or challenger work.

The production path is:

```text
SIG + PM state
    -> MakerSourceBridge
    -> MakerMarketSnapshot
    -> MakerRuntimeLoop snapshot observer (non-blocking)
    -> ShadowBus ingress
    -> CanonicalShadowSnapshot
    -> candidates
    -> JSONL replay journal + CAPTURE strategy_events mirror
```

No candidate rebuilds live state and SHADOW never subscribes independently to SIG
or Polymarket.

## Control actions

```python
bus.disable_candidate("pred-006")
bus.enable_candidate("pred-006")
bus.pause()
bus.resume()
health = bus.health()
```

Disabling a candidate drains its pending queue. It does not modify central Risk,
MAKE, execution mode or any order state.

## Backpressure policy

Each candidate owns a bounded queue. The production default is 512 entries so one
237-market global sweep is retained with headroom. When full, SHADOW coalesces the
oldest pending state and keeps the newest state. It increments explicit skipped/coalesced counters.

Interpretation:

- a slow research challenger cannot block another candidate;
- the queue cannot grow without bound;
- skipped states are observable;
- do not increase queue sizes blindly to hide a slow candidate.

If a latency-sensitive candidate shows sustained queue depth/high-water or skips,
disable/fix the slow path rather than allowing hidden backlog.

## Failure response

Candidate statuses:

- `OK`: usable candidate output;
- `ABSTAIN`: candidate intentionally produced no trade/quote;
- `NOT_READY`: adapter exists but exact runtime capability is absent;
- `STALE_INPUT` / `UNTRUSTED_INPUT`: source-quality failure;
- `INVALID_OUTPUT`: malformed, NaN, Inf or invalid probability output;
- `TIMEOUT`: evaluation exceeded its budget;
- `EXCEPTION`: candidate raised;
- `DISABLED`: control-plane state; disabled candidates are not evaluated.

A candidate failure does not stop the other workers. On timeout the candidate is
quarantined with at most one underlying in-flight call. Pending states for that
candidate are skipped until the timed-out call returns; other candidates continue.
Inspect `quarantined`, `in_flight`, `quarantine_count`, timeout count and skipped
states before restarting a provider.

## Journal/restart check

Use a dedicated append-only path:

```text
data/live/shadow_002/events.jsonl
```

Before restart:

1. `await bus.flush()`;
2. record file size and last complete event;
3. `await bus.close()`.

After restart:

1. construct a new `JsonlEventStore` pointing at the same file;
2. start a new bus;
3. publish a fresh canonical state;
4. flush;
5. verify the file begins with the pre-restart bytes and new events append.

Replay smoke:

```python
snapshots = load_persisted_snapshots(path)
result = ShadowReplayRunner(candidates).replay(snapshots)
print(result.semantic_hash)
```

Run the same deterministic candidate set twice; semantic hashes must match.

## Health gate

Morning review should inspect:

```python
health = bus.health()
```

Gate on:

- `health.running is True`;
- recent `last_snapshot_age_ns`;
- `ingress_rejected == 0` and no `ingress_error`;
- bounded ingress, persistence and candidate queue high-water;
- zero unexplained persistence/CAPTURE mirror failures;
- zero or understood timeout/quarantine/failure counts;
- no sustained coalescing for launch-critical candidates;
- expected enabled/disabled candidate set;
- plausible p50/p95/p99 candidate latency.

## Test and benchmark commands

```bash
pytest tests/test_shadow002.py tests/test_make001_runtime.py
ruff check src/predictions_cup/shadow tests/test_shadow002.py scripts/benchmark_shadow002.py scripts/soak_shadow002.py
mypy
python scripts/benchmark_shadow002.py --markets 237 --candidates 6 --persistence-events 32
python scripts/soak_shadow002.py --markets 237 --candidates 6 --cycles 20 --cycle-pause-ms 500
```

The one-shot benchmark measures orchestration/evaluation overhead. The sustained
soak uses production non-blocking MakerSnapshot ingress plus JSONL persistence,
requires exact event readback, and fails on any ingress rejection, coalescing/drop
or persistence failure.

## Adding a challenger

Implement `ShadowCandidate` only:

```python
class NewCandidate:
    candidate_id = "new-candidate"
    candidate_version = "frozen-v1"
    strategy_family = "PRED"

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        ...
```

Then register it at startup. Do not edit central fan-out, persistence or replay logic.

A candidate must:

- use only the supplied snapshot;
- avoid hidden wall-clock state;
- avoid network/filesystem I/O on evaluate;
- version every semantic/model change;
- fail closed when exact live features are unavailable;
- never place/cancel orders.

## Evidence reconciliation

JSONL is the authoritative replay journal because it stores both canonical
snapshots and CandidateDecision events. With the CAPTURE mirror enabled, each
decision is also written as a `SHADOW_DECISION` row in CAPTURE-001
`strategy_events`.

For LIVE-LEARN / first-hours joins:

1. read CAPTURE `strategy_events` for the common decision analytics surface;
2. parse `payload_json` and retain `decision_id` and `input_snapshot_id`;
3. join to JSONL decisions by `decision_id`;
4. recover the exact canonical observable state from the JSONL snapshot with the
   matching `input_snapshot_id`;
5. cross-check candidate/version, market/exchange and observable time.

A CAPTURE mirror failure is health-visible and never grants execution permission.

## Launch integration boundary

The launch connection is implemented inside `MakerService` / `MakerRuntimeLoop`.
Do not add another market-data subscriber and do not await SHADOW from MAKE.

If a future candidate is promoted toward execution, translate its decision through
the existing BUILD-009 Opportunity -> central Risk -> ExecutionPlan path. SHADOW
itself remains observation-only.

