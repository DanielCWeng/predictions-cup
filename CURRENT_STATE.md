# Current State

**Phase:** READ-ONLY LIVE-DATA + REPLAY/RESEARCH FOUNDATION.

## Operating posture — 26 September 2026

- Broad strategy/mathematical research is frozen by default unless it answers a failed test,
  implementation ambiguity, live venue observation or specific architectural decision.
- Initial empirical strategy work is limited to direct external lead/lag, response/half-life,
  simple residual/relative value, LOO structure where mapping supports it, and selective market
  making only after evidence justifies it.
- **Monday 28 September gate:** those hypotheses should be runnable through replay using data
  captured by our own infrastructure; otherwise priority collapses back onto data -> replay ->
  experiment.
- GitHub merge state and the actual contents of `main` outrank stale documentation or chat memory.

This document describes the accepted repository state on `main`. For project intent/dependency
order, read `ORCHESTRATOR.md` first.

## Implemented on main

- repository foundation, Python tooling and CI;
- typed configuration, secret-safe credential representation and canonical domain models;
- authenticated read-only SIG REST client;
- public read-only Polymarket research recorder;
- SIG tournament Realtime ingestion, authoritative REST reconciliation and replayable persistence;
- BUILD-006 full-universe Realtime / bulk scalar state / explicit tracked-depth state behind one
  governed REST budget;
- MAPPING-001 typed SIG ↔ Polymarket mapping framework with EXACT / NEAR / DERIVED / MODEL_ONLY
  and SAME / COMPLEMENT semantics;
- BUILD-005 deterministic observable-time replay and experiment/evaluation foundation;
- EXPERIMENT-002 lead/lag, response-curve, relative-value and LOO-PRICE / LOO-FAMILY experiment
  machinery;
- BUILD-007 read-only EC2 systemd supervision for SIG + Polymarket collectors;
- strict externally supplied supervised Polymarket universe with no hard-coded Cup IDs;
- high-frequency Polymarket research history in immutable ZSTD Parquet shards;
- small operational Polymarket SQLite for markets/tokens/ingestion health;
- canonical Parquet replay support including deterministic hashed-trade de-duplication;
- Gamma rate-limit resilience and fail-soft periodic metadata refresh after valid startup.

## Live validation evidence

### BUILD-006 / PR #19

Accepted 60-second credentialed smoke at the merged head: 237 known exchanges, 1 tracked / 236
untracked, 0 HTTP 429s, 0 reconciliation failures, 2 full-book reads, with the tracked book
inside the 30-second freshness bound.

### BUILD-007 / PR #20

Merged/accepted at head `1fc3383ac2471466ef440b5f050559ba0a37deed`; merge commit
`153116bb84bc64f202b4cd6dc7748e11d1a84e8b`. The ARM64 EC2 pre-merge gate passed on the
actual host:

- PyArrow 25.0.1 imported successfully on `aarch64`;
- strict smoke universe held at 3 markets / 6 tokens;
- service remained active through the bounded soak;
- observations, book changes, depth snapshots and trades all produced ZSTD Parquet shards;
- published Parquet files read back successfully;
- scheduled Gamma refresh remained healthy;
- 429 retries showed the corrected positive 1-second floor rather than the prior zero-delay burst;
- manual service restart succeeded;
- post-restart capture returned healthy with `websocket_connected=True`,
  `markets_subscribed=3`, `tokens_subscribed=6`, `gamma_last_status=OK` and
  `storage_failures=0`;
- Parquet file count advanced after restart and prior shards remained readable.

The three-market/six-token universe was a temporary compatibility test only. It is not production
mapping evidence and must not be promoted into production configuration.

## Outstanding operational / acceptance gates

- MAPPING-001 is an accepted framework, but the live credentialed 2026 SIG ↔ Polymarket
  crosswalk has **not** been generated or accepted.
- LIVE-MAPPING-GATE-001 / issue #13 remains the primary production gating task.
- After mapping acceptance, populate `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS` only from the
  accepted crosswalk and run the mapping-bounded paired capture soak.
- Production runtime acceptance still needs the final mapping-bounded SSH disconnect/reconnect and
  reboot recovery checks.
- Until the production mapping is accepted, Polymarket may remain disabled/stopped; temporary smoke
  IDs are test-only.

## Research / experiment state

- EXPERIMENT-002 / PR #16 is merged/accepted as experiment machinery. It does **not** establish
  empirical alpha by itself; accepted real data still needs to be run through it.
- HIST-DATA-001 / PR #17 was closed unmerged. It must not be described as accepted repository
  capability. Historical research remains a useful parallel workstream and should reuse accepted
  data/replay contracts.

## Not implemented on main

- validated production live 2026 SIG ↔ Polymarket crosswalk;
- accepted empirical trading edge;
- production fair-value model;
- relationship/constraint engine used for live decisions;
- opportunity scanning used for live orders;
- risk decisions/calculations for trading;
- execution;
- order submission/cancellation;
- portfolio accounting;
- shadow/paper trading engine;
- live trading.

## Trading capability

**NONE**

`trading_enabled` defaults to `False`. Setting it to `True` remains configuration intent only and
requires a separately supplied trade credential; no accepted execution or order-submission path
exists on `main`.

## Next implementation / acceptance target

The primary gating target is:

> LIVE-MAPPING-GATE-001 — generate and independently accept the live 2026 SIG ↔ Polymarket
> crosswalk, then use it to start the mapping-bounded paired capture lane.

In parallel, historical/live accepted data should be pushed through EXPERIMENT-002 rather than
adding new strategy machinery without evidence.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge reconciles canonical project state.
- Builders do not merge/accept their own implementation work.
- Code acceptance and live/runtime acceptance are distinct when relevant.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
