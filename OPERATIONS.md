# Operations

## Current state

- No production deployment exists.
- No production trading daemon exists.
- Normal application startup remains finite, network-free and non-trading.
- The accepted repository includes a separate, explicitly invoked public read-only Polymarket research recorder.
- BUILD-004 SIG Realtime capture is accepted on `main`. Its first credentialed tournament smoke exposed a scaling defect in the all-exchange full-depth fallback; BUILD-006 / PR #19 is the corrective candidate.

The recorder uses local SQLite/WAL append storage, idempotent market metadata upserts, invalidation plus authoritative REST reseeding after reconnect, and explicit feed/book/trade/storage health clocks. These are experimental capture properties, not production trading/recovery guarantees.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

The recorder writes a lean 1-second scalar panel, normalized event-time book changes and public trade events. Top-20 depth snapshots default to every 60 seconds rather than every second. Feed receive/PONG liveness is bounded; an unhealthy connection is closed and the existing reconnect path invalidates books and REST-reseeds them before accepting new deltas.

The database path defaults to `data/polymarket_capture.sqlite3`, which is ignored by Git. Storage failures surface instead of being silently ignored.

## SIG Realtime capture — BUILD-004 baseline / BUILD-006 corrective candidate

The SIG capture process is explicit; normal application startup does not launch it. A read credential and an explicit tournament UUID are required.

Enumerate accessible tournaments for operator selection:

    python -m predictions_cup.sig.capture --list-tournaments

BUILD-006 keeps tournament-wide Realtime and broad scalar/BBO capture while making full-depth maintenance opt-in. A conservative smoke with a small explicit tracked set is:

    python -m predictions_cup.sig.capture \
      --tournament-id <TOURNAMENT_UUID> \
      --tracked-exchange-id <EXCHANGE_ID_1> \
      --tracked-exchange-id <EXCHANGE_ID_2> \
      --run-seconds 60 \
      --print-health

With no --tracked-exchange-id arguments, the process deliberately maintains no resident trusted full depth and logs that fact. It still records the full tournament Realtime tape and broad bulk-price observations.

The BUILD-006 live path uses one governed REST client. The default is 3 requests/second, chosen conservatively from live observation; SIG does not publish a numeric REST limit in the supplied contract. HIGH tracked dirty/recovery work can overtake BACKGROUND bulk-price work, and a 429 creates shared cooldown for callers using the same governed client.

The broad universe uses GET /exchanges/prices in batches of at most 100. At 237 exchanges one complete scalar/BBO sweep is three requests. Those observations are stored separately from authoritative full books and can never make depth trusted.

Tracked books remain fail-closed. A tracked bookDirty removes trust before HIGH-priority authoritative reconciliation. An untracked bookDirty is persisted but does not trigger a full-book request. Reconnect, token refresh, socket error and revision-gap recovery reseed only tracked full depth and refresh broad scalar state through the bulk endpoint.

SIG documents that order expiry emits no Realtime event. Because the documented aggregate exchange-orderbook response has no per-order expirationDate, the 30-second tracked-book refresh default remains a project expiry-safety fallback rather than a SIG requirement. It applies only to tracked open books and removes trust before waiting for the refresh.

The default recorder path is data/sig_realtime.sqlite3. SQLite/WAL persistence includes Realtime deliveries/trades/invalidations, authoritative market/full-book observations, compact broad price/BBO observations and depth/trust transitions.

See docs/implementation/BUILD_004_SIG_REALTIME.md for the accepted historical baseline and docs/implementation/BUILD_006_SIG_REST_GOVERNOR.md for the corrective candidate.

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
