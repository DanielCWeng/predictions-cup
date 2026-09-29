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

At process startup:

1. Load the accepted SIG ↔ Polymarket mapping.
2. Build the existing MAKE components.
3. Register the candidates that are actually available.
4. Create one bounded event journal.
5. Start the bus before publishing canonical snapshots.

Conceptual composition:

```python
from pathlib import Path

from predictions_cup.maker import DirectPolymarketFairValueProvider
from predictions_cup.shadow import (
    DirectPmCandidate,
    Hazard005FCandidate,
    JsonlEventStore,
    MakerCandidate,
    Pred006Candidate,
    ShadowBus,
    StructuralFairValueCandidate,
)

provider = DirectPolymarketFairValueProvider(mapping)
store = JsonlEventStore(Path("data/live/shadow_002/events.jsonl"))

bus = ShadowBus(
    (
        MakerCandidate(maker_engine),
        DirectPmCandidate(provider, mapping=mapping),
        Pred006Candidate(),          # NOT_READY until exact runtime evaluator exists
        Hazard005FCandidate(),       # NOT_READY until exact live parity exists
        StructuralFairValueCandidate(),  # hook-only until R3 publishes provider
    ),
    store=store,
    queue_capacity=512,
    candidate_timeout_seconds=0.050,
    trading_enabled=False,
)
await bus.start()
```

## Publishing one decision boundary

Reuse the existing `MakerMarketSnapshot` produced from canonical live state:

```python
canonical = CanonicalShadowSnapshot.freeze(
    maker_snapshot,
    observed_at=wall_now,
    mapping_version=mapping_version,
    source_revision=runtime_revision,
    source_provenance={
        "sig": sig_source_version,
        "polymarket": polymarket_source_version,
    },
)
await bus.publish(canonical)
```

Do not rebuild market state inside individual candidates. The frozen object is the
decision boundary.

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

A candidate failure should not be treated as a reason to stop the other workers.
Investigate the candidate health record and persisted decision.

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
- zero unexplained persistence failures;
- bounded persistence/candidate queues;
- zero or understood timeout/failure counts;
- no sustained coalescing for launch-critical candidates;
- expected enabled/disabled candidate set;
- plausible p50/p95/p99 candidate latency.

## Test and benchmark commands

```bash
pytest tests/test_shadow002.py
ruff check src/predictions_cup/shadow tests/test_shadow002.py scripts/benchmark_shadow002.py
mypy
python scripts/benchmark_shadow002.py \
  --markets 237 \
  --candidates 6 \
  --persistence-events 32
```

The benchmark separates publish/orchestration overhead from candidate evaluation
latency and reports persistence cost separately.

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

## Launch integration boundary

The launch event source should call SHADOW after it has built the canonical
`MakerMarketSnapshot`. Do not let each candidate subscribe independently to SIG or
Polymarket.

If a future candidate is promoted toward execution, translate its decision through
the existing BUILD-009 Opportunity -> central Risk -> ExecutionPlan path. SHADOW
itself remains observation-only.
