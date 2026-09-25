# Current State

**Phase:** READ-ONLY LIVE-DATA FOUNDATION.

## Operating posture — 25 September 2026

- Broad strategy/mathematical research is frozen by default; new work should answer a failed test, implementation ambiguity, live venue observation or specific architectural decision.
- Initial empirical strategy work is limited to direct external lead/lag, simple residual/relative value and simple selective market making.
- **Monday 28 September gate:** those initial hypotheses must be runnable through replay using data captured by our own infrastructure; otherwise priority collapses entirely onto data → replay → experiment.
- Competition-history findings and the six-day launch plan are canonical documentation; they add no runtime or trading capability.

This document describes the accepted repository state on `main`.

## Implemented on main

- repository foundation;
- Python tooling and CI;
- typed application configuration;
- environment-driven loading with optional local `.env` support;
- secret-safe read/trade credential representation;
- fail-closed trading configuration;
- canonical core domain models;
- Decimal-based financial/probability values;
- timezone-aware canonical timestamps;
- opaque external identifiers;
- validation invariants;
- runnable non-trading application shell;
- canonical maths ledger;
- competition strategy/execution playbook;
- competition-history research and six-day launch plan;
- authenticated read-only SIG REST client for account health, market/exchange discovery, market-node transport data, prices, orderbooks, price history and trades;
- public read-only Polymarket research recorder with Gamma/CLOB discovery, authoritative REST book seeding, normalized event-time book changes/trades, a lean 1-second top-of-book panel, slower bounded depth snapshots, SQLite/WAL persistence and feed-liveness/reconnect handling.

## In review — not implemented on main

BUILD-004 is open as PR #12 on `build/004-sig-realtime-state`. It is **not accepted
functionality on `main`**. The candidate implementation adds explicit tournament selection,
strict Realtime token validation, one private tournament-level SIG Realtime subscription,
topic-revision gap detection, authoritative REST market/orderbook reconciliation, fail-closed
trusted/untrusted exchange state, normalized SQLite/WAL capture, and runtime health counters.

Realtime is treated only as a best-effort low-latency invalidation/event feed. REST remains the
authoritative state source. A duplicate topic revision is ignored; a revision gap, reconnect,
token refresh or socket error forces authoritative REST resynchronization before state can be
trusted again. Engine source sequence ranges are recorded as provenance and are not used as the
topic-local gap counter.

## Not implemented on main

- SIG realtime/WebSocket ingestion;
- persistent SIG market-state/reconciliation engine;
- production market mapping;
- fair value;
- relationship/constraint engine;
- opportunity scanning;
- risk decisions/calculations;
- execution;
- order submission/cancellation;
- portfolio accounting;
- shadow trading;
- live trading.

## Trading capability

**NONE**

`trading_enabled` defaults to `False`. Setting it to `True` is only configuration intent and requires a separately supplied trade credential; no execution path or order-submission path exists on `main`.

## Next acceptance gate

Independently review and accept/reject BUILD-004 before strategy expansion:

```text
resolve/select active tournament
→ ingest SIG realtime/WebSocket events
→ reconcile realtime state to authoritative REST
→ persist synchronized SIG books/trades with source and observed timestamps
```

PR #12 is the active implementation lane, but it remains outside `main` until independent acceptance. Mapping, fair value, strategy and execution remain downstream until this live-state foundation is reliable.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
