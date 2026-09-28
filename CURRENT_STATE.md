# Current State

**As of:** 28 September 2026  
**Canonical main before this documentation pass:** `4efa3dd002abc1cbf1997acdf8734adee2e13f4c`  
**Phase:** ACCEPTED LIVE MAPPING + REPLAY/RESEARCH FOUNDATION; NO TRADING CAPABILITY.

This file describes accepted repository state. GitHub merge state and the actual contents of `main`
outrank stale prose, old branches, or chat history.

## Accepted live universe

The 2026 SIG ↔ Polymarket crosswalk is generated, independently accepted, and present on `main`.

Canonical mapping artifacts:

- `data/mappings/sig_polymarket_2026.json`
- `data/mappings/sig_polymarket_2026.csv`
- `data/mappings/sig_polymarket_2026_acceptance.json`
- `data/mappings/sig_polymarket_2026_summary.json`

Accepted scope:

- **237** SIG exchanges / markets;
- **140 EXACT**;
- **87 DERIVED**;
- **4 NEAR**;
- **6 NO_TRADE**;
- **0 MODEL_ONLY**;
- **693** unique Polymarket condition IDs;
- **1,386** aligned CLOB token IDs;
- zero conflicting duplicate mappings.

The old statement that LIVE-MAPPING-GATE-001 is still outstanding is obsolete. Mapping acceptance is
complete. Runtime configuration must use accepted crosswalk identities rather than guessed or broad
heuristic IDs.

## Accepted implementation on main

The repository contains:

- BUILD-001 through BUILD-008 foundations;
- read-only SIG REST and tournament Realtime capture;
- governed broad SIG scalar/BBO state plus explicit tracked full-depth state;
- accepted MAPPING-001 framework and accepted 2026 live crosswalk;
- supervised mapping-bounded Polymarket capture with immutable ZSTD Parquet research history;
- deterministic observable-time replay and executable markouts;
- canonical research/evaluation machinery with chronological splits, purge/embargo, FDR,
  dependence-aware resampling, stability checks, ablations and execution-stress separation;
- DATA-001 historical replay corpus;
- DATA-002 fee/refund/rebate and role-attribution evidence;
- DATA-003 actual mapped-2026-universe Polymarket fills;
- repository-native GitHub Actions → Kaggle execution via PR #47.

Routine Kaggle compute now uses:

`Agent → GitHub → kaggle/jobs/*.json → GitHub Actions → Kaggle → compact evidence → GitHub`

EC2 is not the default Kaggle middleman.

## Active implementation lane — BUILD-009

Draft PR #50 on `build/009-low-latency-strategy-execution-core` now has its technical BUILD-009
gates complete on benchmarked code SHA `2ae4e24c9c85fd5d51aee6e491b14ae4c9434184`. GitHub CI
#2344 passed lint, strict mypy, 532 tests with 2 skips, application smoke and benchmark smoke.
Target-host 3k/journal and repeated 1m hot-path evidence is recorded in the BUILD-009 implementation
handoff. It is still **not accepted capability on `main`** because independent review/merge has
not occurred.

The branch is intentionally fail-closed: SHADOW remains the default, and LIVE requires explicit
enablement, trade credential, tournament identity, configured central risk caps, kill-switch
release, trusted account state, durable operation identity and authoritative recovery.

No 005 research finding is promoted by this implementation ticket. EXPERIMENT-005B remains frozen
and separate. No real SIG orders were sent during BUILD-009 implementation or benchmarking.

Until PR #50 is independently accepted and merged, the canonical statement remains:

> **main has no order-submission/cancellation capability and no autonomous LIVE trading runtime.**

## DATA-003 — mapped 2026 universe

DATA-003 is accepted on `main` through PR #48.

Key quality facts:

- 132,928 scoped fills;
- 132,928 fee-evidence rows;
- zero duplicate/conflicting fill keys;
- custody-leg conservation passes;
- zero condition/token overlap with the five-family DATA-001/DATA-002 population;
- 231 accepted SIG mappings have observed rows;
- 8 mapped conditions / 16 tokens have zero observed fills in the extracted range;
- trade objects cover 2026-02-28 through 2026-09-21;
- custody objects are absent for 2026-09-20 and 2026-09-21 and are explicitly labelled
  `custody_not_ingested`, never imputed.

DATA-003 is the preferred fresh mapped-universe replication/evaluation surface for findings that need
to transfer from historical election families into the actual Cup universe.

## Research programme state

### EXPERIMENT-004C — complete / frozen

- 004C-A: `INCONCLUSIVE / NO PROMOTED EDGE`.
- 004C-B: `SOFT_COMPETITIVE_EFFECT_ONLY`; one narrow Colombia PRE soft effect.
- 004C-C: `INCONCLUSIVE / NO PROMOTED EDGE`.
- 004C-D: `NO_CONDITIONAL_EDGE`.

The canonical programme handoff remains
`docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md`.

### EXPERIMENT-005A — merged / accepted research record

Disposition:

`NARROW SAME-FAMILY 5s EFFECT ONLY / NO BROAD ROLE-AWARE EDGE`

DATA-002 role annotation is strong enough to use, but participant identity, maker/taker state,
liquidity response and broad role-aware flow did not produce a robust general mechanism. The sole
surviving same-family PRE 5s cell is economically tiny and is a replication candidate only.

### EXPERIMENT-005B — BLOCKED / NOT ACCEPTED

PR #45 remains open and unmerged.

The original experiment has a strong freeze/HOLDOUT chain and interesting realised-movement results,
but its claimed causal same-second ordering uses `timestamp → tx_hash → log_index`. Transaction hash
is not chronological transaction order, so same-block observations can be misordered.

Required bounded follow-up:

- branch: `experiment/005b-ordering-falsification`;
- remediation implementation is present at `8c99a5e6a2c1f0e2e690b23fcb2c7881bb21bb0d` with a frozen
  POST_HOC_FALSIFICATION_ONLY protocol and block-aware reconstruction code, but no empirical
  falsification results are committed yet;
- reconstruct true observable ordering from block/log order;
- quantify affected observations;
- rerun the exact frozen specification;
- preserve original outputs byte-for-byte;
- treat all corrected HOLDOUT comparison as `POST_HOC_FALSIFICATION_ONLY`.

No 005B finding is canonical until that blocker is resolved.

### EXPERIMENT-005C — merged / accepted negative-downgraded record

Final disposition:

`DOWNGRADE — FAMILYWISE NULL NOT REJECTED`

The strongest US joint-panel cell is descriptively positive, but fails the preregistered
dependence-preserving 35-cell familywise challenge and is temporally concentrated. Retain only as a
shadow/research comparator, not as a promoted central model.

### EXPERIMENT-005D — merged / accepted narrow structural record

Final disposition:

`NARROW LATE_COUNT STRUCTURAL EVIDENCE — RECONSTRUCTION STRONG / PREDICTIVE EVIDENCE WEAK AND REGIME-SPECIFIC`

All 13 predictive HOLDOUT cells are LATE_COUNT. Peru retains weak dependence robustness; the
Colombia relationship is statistically stable but economically tiny. Do not describe this as broad
PRE/ACTIVE structural alpha or a standalone strategy.

### EXPERIMENT-005E — merged / accepted primary null

Final disposition:

`NO_INCREMENTAL_EVIDENCE`

Participant behaviour did not add robust incremental predictive value on the preregistered primary
60-second target. Signed-markout and next-change diagnostics remain follow-up-only.

### EXPERIMENT-005F — merged / accepted state-hazard evidence

Four of 14 confirmatory coordinates passed HOLDOUT.

The strongest reusable finding is **persistent economic-BBO age / renewal-hazard state**:
`genuine_age_s` predicts 300-second update hazard in PRE_ELECTION and ACTIVE_RESULTS and also
predicts ACTIVE jump hazard. Long-delay placebos show that this is a persistent state variable,
not a generic short-lived lead-lag mechanism.

PRE `trade_abs_impact_60` → 15s spread-change support is small and behaves more like a slow
activity/liquidity-regime proxy than fresh trade-impact causality.

## Monetisation families

Programme implementation remains organized around:

- `FV-TAKE`
- `MAKE`
- `STRUCT`
- `PRED`
- `EVENT`
- `NO_TRADE`

Research findings such as staleness, activity, volatility, liquidity and opponent ecology are
features/challengers within these families, not automatically new strategy families.

No strategy is registered for execution yet.

## Trading capability

**NONE**

There is still no accepted:

- production fair-value service;
- baseline strategy engine that can emit live trading decisions;
- central trading risk engine;
- order submission/cancellation implementation;
- portfolio accounting;
- autonomous shadow/paper execution loop;
- live trading path.

`PREDICTIONS_CUP_TRADING_ENABLED=true` remains configuration intent only and cannot place an order.

## Remaining operational gates

Mapping acceptance is complete. The remaining production-runtime work is narrower:

- populate supervised Polymarket runtime IDs from the accepted crosswalk;
- run the mapping-bounded paired live-capture soak on the accepted production universe;
- complete SSH independence and reboot recovery validation;
- retain fail-closed trust semantics for stale/missing depth and mapping identities.

These are runtime-acceptance tasks, not mapping-discovery tasks.

## Immediate programme direction

1. Complete the bounded 005B causal-order falsification without redesign.
2. Close broad predictive discovery.
3. Establish simple baseline engines for the five approved monetisation families.
4. Build common shadow/evaluation machinery.
5. Replicate important historical findings on DATA-003 / the actual mapped 2026 universe.
6. Use live SIG evidence to promote or demote mechanisms after launch.
7. Iterate continuously without turning isolated findings into new strategy programmes.

## Branch state

After the 28 September reconciliation:

- merged: PR #38, #41, #42, #43, #44, #47 and #48;
- open/blocked: PR #45 only;
- historical, backup, preregistration, review and superseded research branches may remain on GitHub
  as provenance refs but are not canonical capability unless merged into `main`.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge reconciles canonical project state.
- Experimental HOLDOUTs remain immutable after consumption.
- Post-HOLDOUT review may demote; it must not rescue/promote through redesign.
- Builders do not treat green CI as scientific acceptance.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents are authoritative.
