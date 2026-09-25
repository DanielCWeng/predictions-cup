# Current State

**Phase:** FOUNDATION / DOMAIN MODELS — active integration work remains off-main.

## Operating posture — 25 September 2026

- Broad strategy/mathematical research is frozen by default; new work should answer a failed test, implementation ambiguity, live venue observation or specific architectural decision.
- Initial empirical strategy work is limited to direct external lead/lag, simple residual/relative value and simple selective market making.
- **Monday 28 September gate:** those initial hypotheses must be runnable through replay using data captured by our own infrastructure; otherwise priority collapses entirely onto data → replay → experiment.
- Competition-history findings and the six-day launch plan are canonical documentation; they add no runtime or trading capability.

This document describes what exists on `main`, then separately records active unmerged implementation work.

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
- competition strategy/execution playbook.
- competition-history research and six-day launch plan.

## Active / pending integration

### PR #4 — BUILD-003 — SIG authenticated read-only REST client

Implementation exists on `build/003-sig-rest-client`, not on `main`.

Current review state: **IN REVIEW**.

On this branch, the recursive `MarketNode` transport blocker has been corrected and focused regression coverage added. The branch is reconciled onto current `main`, but BUILD-003 remains unmerged and is not accepted `main` capability until independent review and merge.

If accepted, BUILD-003 adds authenticated **read-only** SIG REST access for account health, market/exchange discovery, market-node transport data, prices, orderbooks, price history and trades. It adds no order submission, cancellation, realtime/WebSocket path or trading capability.

### PR #5 — EXPERIMENT-001A — Polymarket live data capture foundation

Implementation exists on `experiment/001a-polymarket-live-capture`, not on `main`.

Current review state: **IN REVIEW**.

This branch is integrated against corrected BUILD-003 head `986bd52a7338f790eb00b3e8e333c10e41acd7b0` while PR #4 awaits independent review. The focused recorder revision is complete: normalized event-time book changes are durable, REST book batches retain receipt-time observations, the 1-second panel is scalar/lean with depth on a slower configurable cadence, receive/PONG liveness forces reconnect, and `last_trade_price` updates current book state.

EXPERIMENT-001A remains unmerged public read-only research infrastructure. It adds no wallet/signing, order submission, fair value, strategy, risk or execution capability.

## Not implemented on main

- SIG HTTP integration or authenticated SIG requests;
- SIG realtime/WebSocket ingestion;
- persistent market-state/reconciliation engine;
- Polymarket or other external live-data adapters/recorders;
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

## Immediate integration queue

```text
independent review PR #4
→ merge PR #4 if accepted
→ refresh PR #5 onto accepted #4/main history if required
→ independent review PR #5
→ merge PR #5 if accepted
→ next implementation ticket
```

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
