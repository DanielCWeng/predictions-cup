# Operations

## Current state

- No production deployment exists.
- No production trading daemon exists.
- Normal application startup remains finite, network-free and non-trading.
- PR #5 contains a separate, explicitly invoked public read-only Polymarket research recorder.

The recorder uses local SQLite/WAL append storage, idempotent market metadata upserts, invalidation plus authoritative REST reseeding after reconnect, and explicit feed/book/trade/storage health clocks. These are experimental capture properties, not production trading/recovery guarantees.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

The recorder writes a lean 1-second scalar panel, normalized event-time book changes and public trade events. Top-20 depth snapshots default to every 60 seconds rather than every second. Feed receive/PONG liveness is bounded; an unhealthy connection is closed and the existing reconnect path invalidates books and REST-reseeds them before accepting new deltas.

The database path defaults to `data/polymarket_capture.sqlite3`, which is ignored by Git. Storage failures surface instead of being silently ignored.

## Eventual operating expectations

Future production operation is expected to provide, at minimum:

- a supervised process;
- automatic restart;
- reconciliation on restart;
- health visibility;
- log rotation;
- no dependency on a developer laptop.

These mechanisms are expectations only and are not implemented by BUILD-001.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
