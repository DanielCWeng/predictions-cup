# Operations

## Current state

- No production deployment exists.
- No trading daemon exists.
- Normal application startup remains finite and network-free.
- EXPERIMENT-001A provides an explicit, separate read-only Polymarket recorder process.

The recorder uses local SQLite/WAL append storage, idempotent market metadata upserts, fresh book
initialization after reconnect/restart, and structured health state. These are experimental capture
properties, not production trading/recovery guarantees.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

Inspect structured logs plus the `ingestion_health` table. Feed activity, book changes and trades
are separate health clocks. If durable snapshot/trade/health recording raises an error, the
recorder surfaces the failure rather than continuing as if capture were healthy.

The database path defaults to `data/polymarket_capture.sqlite3`, which is ignored by Git.

## Eventual operating expectations

Future production operation is expected to provide, at minimum:

- a supervised process;
- automatic restart;
- reconciliation on restart;
- health visibility;
- log rotation;
- no dependency on a developer laptop.

These production mechanisms remain expectations rather than completed trading infrastructure.
