# Current State

**Phase:** READ-ONLY LIVE-DATA + REPLAY/RESEARCH FOUNDATION.

## Operating posture — 28 September 2026

- EXPERIMENT-004C is complete and frozen on `main`; no robust executable alpha was established across its four tested mechanisms.
- The narrow positive result retained for future work is 004C-B's Colombia PRE soft competitive-family structure; it is not a general Cup edge.
- Generic internal pairwise lead-lag, direct mapped PM→SIG transmission and the frozen D conditional-response mechanisms are not promoted from 004C.
- These results are specification-level conclusions only; they do not reject the complete predictive information set, coherent FV broadly, external information broadly, or all state-dependent models.
- PR #38 / EXPERIMENT-005A remains draft/open and unmerged; no 005A empirical discovery run was produced by the 004C close-out.
- New 005 discovery lanes must use `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md` to avoid pre-freeze anchoring to detailed prior winners/magnitudes.
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
- BUILD-008 canonical research evaluation harness;
- DATA-001 accepted historical replay corpus;
- DATA-002 accepted auxiliary fee/refund/rebate evidence dataset;
- EXPERIMENT-004C A/B/C/D empirical evidence, reports and frozen provenance;
- canonical 004C programme handoff at `docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md`.

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

- BUILD-008 / PR #24 is merged/accepted as the canonical evaluation harness.
- DATA-001 / PR #23 is merged/accepted as the historical replay corpus used by the completed research programme.
- DATA-002 / PR #36 is merged/accepted as the separate fee/refund/rebate evidence dataset; it provides raw attribution evidence rather than a derived maker/taker truth label.
- EXPERIMENT-004C-A / PR #34: `INCONCLUSIVE / NO PROMOTED EDGE`.
- EXPERIMENT-004C-B / PR #35: `SOFT_COMPETITIVE_EFFECT_ONLY`; one narrow Colombia PRE soft effect survives, while no hard/exhaustive-family alpha promotes.
- EXPERIMENT-004C-C / PR #37: `INCONCLUSIVE / NO PROMOTED EDGE`.
- EXPERIMENT-004C-D / PR #39: `NO_CONDITIONAL_EDGE`.
- The canonical programme interpretation is `docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md`.
- PR #38 / EXPERIMENT-005A remains draft/open and unmerged. Its scaffold may consume the final 004C freeze later under its own owner and gates; this close-out produced no 005A empirical output.
- HIST-DATA-001 / PR #17 remains closed unmerged and is not accepted capability.

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

Operationally, LIVE-MAPPING-GATE-001 remains the production mapping/capture gate and no 004C result changes the prohibition on execution.

Research-wise, the completed 004C freeze is the base for the separately owned EXPERIMENT-005 programme. PR #38 / 005A remains unmerged; any subsequent broad discovery lane must follow `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md` before detailed prior outcomes are inspected.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge reconciles canonical project state.
- Builders do not merge/accept their own implementation work.
- Code acceptance and live/runtime acceptance are distinct when relevant.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
