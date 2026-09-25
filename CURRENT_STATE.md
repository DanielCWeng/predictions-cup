# Current State

**Phase:** FOUNDATION / DOMAIN MODELS — active integration work remains off-main.

This document describes what exists on `main`, then separately records active unmerged work.

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

## Active / pending integration

### PR #4 — BUILD-003 — SIG authenticated read-only REST client

Implementation exists on `build/003-sig-rest-client`, not on `main`.

Current review state: **BLOCKED / IN REVIEW**.

The remaining code blocker is strict recursive `MarketNode` transport validation for `/markets/{id}/nodes`. The branch must also reconcile its project-control documentation with the cleaned `main` state before merge.

### PR #5 — EXPERIMENT-001A — Polymarket live data capture foundation

Implementation exists on `experiment/001a-polymarket-live-capture`, not on `main`.

Current review state: **BLOCKED / IN REVIEW**.

Required revision covers durable normalized event-time book changes, honest REST batch observation timestamps, leaner storage growth, missing-PONG/feed-liveness detection, and `last_trade_price` consistency. It must be updated/rebased after PR #4 lands before final review.

### PR #6 — competition research / six-day launch-plan documentation

Documentation work exists on `docs/competition-research-launch-plan`, not on `main`.

It adds `docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md` and updates operating-priority documentation. It does **not** add runtime or trading capability.

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
PR #4 correction/review
→ merge PR #4
→ update/rebase PR #5
→ PR #5 correction/review
→ merge PR #5
→ next implementation ticket
```

PR #6 is a documentation-only stream and may be reviewed independently, but it must reconcile any overlapping control-document changes against the latest `main` before merge.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
