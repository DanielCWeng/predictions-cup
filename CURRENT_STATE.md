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
- BUILD-006 full-universe Realtime / broad bulk scalar state / explicit tracked-depth state behind one governed REST budget;
- typed SIG ↔ Polymarket mapping contracts;
- EXACT / NEAR / DERIVED / MODEL_ONLY mapping semantics where applicable;
- SAME / COMPLEMENT direction semantics;
- deterministic mapping artifact generation;
- reviewer-owned override validation path;
- deterministic live acceptance-evidence machinery;
- deterministic observable-time SIG + Polymarket replay with bounded capture selection;
- trusted/fresh reconstructed replay state and standardized executable crossing markouts;
- generic experiment/evaluation contracts, chronological splits and deterministic synthetic replay proof.

## Live validation evidence

- BUILD-006 / PR #19 is accepted on `main` and its accepted 60-second credentialed smoke passed at the merged head: 237 known exchanges, 1 tracked / 236 untracked, 0 429s, 0 reconciliation failures, 2 full-book reads, with the tracked book inside the 30-second freshness bound.

## Outstanding operational / acceptance gates

- The MAPPING-001 framework is accepted on `main`, but the live credentialed 2026 SIG ↔ Polymarket crosswalk has **not** been generated or accepted.
- LIVE-MAPPING-GATE-001 / GitHub issue #13 tracks live SIG exchange enumeration, reviewer promotion/overrides, mapped-token CLOB smoke, acceptance evidence and independent acceptance before mappings are treated as production-ready.

## In review — not implemented on main

BUILD-007 / PR #20 is in review on `build/007-ec2-runtime-supervision`. On that branch, the
accepted SIG and Polymarket read-only collectors are supervised by systemd, survive SSH session
loss by running independently of the shell, and are enabled to start after reboot/network-online.
Both services load only the resolved `~/.config/predictions-cup/runtime.env`; `trade.env` and
trade credentials are excluded. SIG tracked depth is externally configured by
`PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS` and defaults to none. BUILD-007 adds no
trading capability.

BUILD-007 validation state:

- implemented on branch: **yes**;
- CI validated: **yes** (lint, shell validation, strict mypy, pytest and application smoke);
- live EC2 validation: **partial** — SIG service/env-file side green; first Polymarket startup hit Gamma HTTP 429; retry/backoff fix implemented on branch; Polymarket output + SSH/reboot rerun still required.

EXPERIMENT-002 is in review as PR #16 on `experiment/002-leadlag-rv-loo`. It builds the first
LEADLAG / response-curve / relative-value / LOO-PRICE / LOO-FAMILY empirical experiment suite on
the accepted BUILD-005 replay foundation. The branch remains non-trading and does not claim any
empirical edge before verified historical/live data are run.

## Not implemented on main

- accepted/live-validated EC2 systemd supervision for the collectors (BUILD-007 remains in review);
- validated production live 2026 SIG ↔ Polymarket crosswalk;
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

The active implementation target is:

> executable lead/lag, response-curve, relative-value and LOO-family experiments over accepted replay.

EXPERIMENT-002 / PR #16 is the current in-review branch for that target. This status does not imply
acceptance or availability on `main`.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
