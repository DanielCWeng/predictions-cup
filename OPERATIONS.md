# Operations

## Current state

- No production deployment exists.
- No production trading daemon exists.
- Normal application startup remains finite, network-free and non-trading.
- The accepted repository includes a separate, explicitly invoked public read-only Polymarket research recorder.
- BUILD-004 SIG Realtime capture is **IN REVIEW in PR #12** and is not an accepted `main` capability.

The recorder uses local SQLite/WAL append storage, idempotent market metadata upserts, invalidation plus authoritative REST reseeding after reconnect, and explicit feed/book/trade/storage health clocks. These are experimental capture properties, not production trading/recovery guarantees.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

The recorder writes a lean 1-second scalar panel, normalized event-time book changes and public trade events. Top-20 depth snapshots default to every 60 seconds rather than every second. Feed receive/PONG liveness is bounded; an unhealthy connection is closed and the existing reconnect path invalidates books and REST-reseeds them before accepting new deltas.

The database path defaults to `data/polymarket_capture.sqlite3`, which is ignored by Git. Storage failures surface instead of being silently ignored.

## BUILD-004 SIG Realtime capture — IN REVIEW

The candidate SIG capture process is explicit; normal application startup does not launch it.
A read credential and an explicit tournament UUID are required.

Enumerate accessible tournaments for operator selection:

```bash
python -m predictions_cup.sig.capture --list-tournaments
```

Run capture for the selected tournament:

```bash
python -m predictions_cup.sig.capture --tournament-id <TOURNAMENT_UUID>
```

Finite manual credentialed smoke:

```bash
python -m predictions_cup.sig.capture \
  --tournament-id <TOURNAMENT_UUID> \
  --run-seconds 30
```

The default recorder path is `data/sig_realtime.sqlite3`. The candidate recorder uses
SQLite/WAL and normalized tables for Realtime deliveries, trades, book/settlement
invalidation events, authoritative REST book observations, and trust/reconciliation transitions. Retention defaults to 14 days.

Operational trust rules are fail-closed: the process mints/refreshes its token, performs the
required authoritative REST seed/resync, then subscribes. Reconnect, token refresh, socket error,
malformed payload or topic revision gap triggers authoritative REST reconciliation. A failed
reconciliation leaves the affected exchange untrusted. `bookDirty` entries are coalesced by
exchange within the batch before REST refresh; settlement refetches authoritative market state.
SIG order expiry emits no Realtime event, so trusted open books are invalidated and
authoritatively refreshed once their last successful REST observation reaches the configurable
freshness bound (30 seconds by default). The maintenance loop checks once per second; books are
marked untrusted before any refresh request is awaited.

See `docs/implementation/BUILD_004_SIG_REALTIME.md` for exact timestamp, revision, health and
storage semantics. This section describes an unmerged candidate until PR #12 is accepted.

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
