# Current State

**As of:** 29 September 2026  
**Canonical main before this documentation pass:** `bbd152eb13f827b9ede36e444ac597eb0274443d`  
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
- DATA-004 ETS P0/P1 market-graph fills are under review on `data/data004-ets-p0p1-fills`; this branch is not canonical until PR acceptance;
- repository-native GitHub Actions → Kaggle execution via PR #47.

Routine Kaggle compute now uses:

`Agent → GitHub → kaggle/jobs/*.json → GitHub Actions → Kaggle → compact evidence → GitHub`

EC2 is not the default Kaggle middleman.

## Active implementation lane — BUILD-009

Draft PR #50 on `build/009-low-latency-strategy-execution-core` has completed its independent-review
correction pass on benchmarked code SHA `7ea841e0b26a312465a3deb68bc504ea311569ef`. GitHub CI
#2467 passed lint, strict mypy, **547 tests with 2 skips**, application smoke and benchmark smoke.
Target-host evidence now includes the required 3k/journal, explicit 100k and repeated 1m stages.
The 100k median decision path was **39.722 us**; the repeated 1m median mean was **45.585 us** on a
shared host with concurrent Polymarket recorder/SIG capture load, so the old pre-review 0.7% stability
claim is retired. All benchmark correctness checks passed.

The correction closes the review's six safety findings: Risk-bound execution mode + explicit LIVE
permit, synchronous in-flight reservations, worst-case gross accounting, fail-closed unsigned/delayed
Realtime fills, malformed accepted responses -> UNCERTAIN, and depth-aware SHADOW fills. It also adds
reserved HIGH-priority REST burst capacity, longer HTTP keepalive and exact journaled payload bytes.
The PR remains **not accepted capability on `main`** and stays draft pending correction re-review.

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

## DATA-004 — ETS P0/P1 graph universe (ACCEPTED_V2 / PR IN REVIEW)

DATA-004 v2 freezes 298 selected Gamma markets (210 P0 and 88 P1), their 596 outcome tokens, 1,596
market-graph links, and 134 selected SIG exchange IDs. The comparison plan covers 231 accepted SIG
anchors and records their direct/derived/near DATA-003 source identities without copying or
reacquiring those histories. OCI v2 is the source copy at
`research/data004_ets_p0p1/v2/`; Kaggle delivery is pending team upload from the GitHub package under
`data/kaggle_handoff/data004/`. The upload record is in
`data/manifests/fills/data_004_kaggle_run.json`. This branch carries the manifest, gate evidence,
per-market/condition/token/event/date coverage, source-day inventory, residual audit and samples.
The package retains metadata for all 1,279 frozen candidates / 2,558 tokens and the complete
5,422-row market graph; only P0/P1 fill histories were acquired.

The v1 Parquet objects remain unchanged. Its status manifest is `BLOCKED_SUPERSEDED` and points to
v2. v2 reuses all 231,964 v1 rows without rescanning the trade lake. For the 4,989 rows missing
custody blocks on 2026-09-20/21, the unique timestamp mapping agreed with custody on all 9,803
validation rows from 2026-09-15 through 2026-09-19. All 4,989 gap rows map uniquely; v2 has zero
missing/imputed blocks and zero per-token ordering failures.

Addendum 4 verified the `size_shares` precision across all 231,964 rows: the maximum observed is
four decimal places. The 43 groups with an exact ±0.0001-share difference are classified as
`PRECISION_ROUNDING_4DP`; all satisfy `abs(taker_size - maker_size) <= 0.0001 * max(maker_rows, 1)`.
No fill values changed, no residuals remain unexplained, and all v2 quality gates pass with status
`ACCEPTED_V2`. PR #59 remains open, so this branch is not yet canonical on `main`; the separate R3
pilot and any scope expansion remain future decisions.

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

After the 28 September reconciliation and DATA-004 Addendum 4:

- merged: PR #38, #41, #42, #43, #44, #47 and #48;
- open/blocked: PR #45 and #50; PR #59 remains open with DATA-004 v2 accepted and merge pending;
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
