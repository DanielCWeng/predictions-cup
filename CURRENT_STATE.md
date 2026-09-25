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
- public read-only Polymarket research recorder with Gamma/CLOB discovery, authoritative REST book seeding, normalized event-time book changes/trades, a lean 1-second top-of-book panel, slower bounded depth snapshots, SQLite/WAL persistence and feed-liveness/reconnect handling;
- explicit SIG tournament handling and private tournament-level Realtime ingestion;
- topic revision continuity, duplicate suppression and authoritative REST reconciliation after gaps/reconnects/token refresh/socket errors;
- trusted/untrusted per-exchange SIG state with authoritative recovery before state is trusted again;
- bounded authoritative refresh of trusted open books so silent order expiry cannot leave stale depth trusted indefinitely;
- normalized replayable SIG persistence with source/revision provenance and runtime health state;
- typed SIG ↔ Polymarket mapping contracts;
- EXACT / NEAR / DERIVED / MODEL_ONLY mapping semantics where applicable;
- SAME / COMPLEMENT direction semantics;
- deterministic mapping artifact generation;
- reviewer-owned override validation path;
- deterministic live acceptance-evidence machinery.

## Outstanding operational / acceptance gates

- BUILD-004 live credentialed tournament smoke remains operationally outstanding; the accepted implementation is on `main`.
- The MAPPING-001 framework is accepted on `main`, but the live credentialed 2026 SIG ↔ Polymarket crosswalk has **not** been generated or accepted.
- LIVE-MAPPING-GATE-001 / GitHub issue #13 tracks live SIG exchange enumeration, reviewer promotion/overrides, mapped-token CLOB smoke, acceptance evidence and independent acceptance before mappings are treated as production-ready.

## Not implemented on main

- validated production live 2026 SIG ↔ Polymarket crosswalk;
- deterministic synchronized SIG + Polymarket replay/evaluation foundation;
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

## Next implementation target

The next implementation target is:

> deterministic synchronized replay + experiment/evaluation foundation.

Its purpose is to make captured SIG + Polymarket information testable using observable timestamps and standardized executable markouts.

This is a target only. No BUILD-005 branch or PR is claimed to exist.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
