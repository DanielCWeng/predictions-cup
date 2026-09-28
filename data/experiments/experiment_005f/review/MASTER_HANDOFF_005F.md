# EXPERIMENT-005F — MASTER Handoff

- **Status: PASS — empirical programme complete; first/only HOLDOUT sealed.**
- Start commit: `ba938bedcf63f562be8b26c9502e828391123867`.
- Outcome-blind design freeze: `b5bf4cfe7482a58161523b66097910903050fc6b`.
- Pre-HOLDOUT freeze: `0f2c65c08c80c734cdbb0c92e60db7656719b9dd`.
- Canonical fit manifest SHA: `b6375379b5c2bc47ac29407436edfbbb7e4810dc558b8af08f59de26823c8363`.
- HOLDOUT runner SHA: `73ddd72a4e7cc5d369d425fff7dc211b15f230eb9249cf5d43fc95e1ddcf2ff4`.
- All 120 fit artifacts matched their frozen hashes before HOLDOUT. All seven committed HOLDOUT files match Kaggle byte-for-byte. Independent disposition recomputation is exact.
- 17 frozen coordinates: **14 confirmatory-eligible + 3 shadow-only**.
- HOLDOUT: **4 supported, 10 confirmatory not supported, 3 shadow-only**.
- No real order placement. No HOLDOUT rerun. No post-HOLDOUT tuning.

## Supported findings

1. **ACTIVE_RESULTS — update hazard, `genuine_age_s`, 300s.**
   - 14,177 obs / 61 markets / 5 events / 3 families.
   - MSE improves **35.16%** vs frozen own-history baseline.
   - sign-flip p=0.000443; bootstrap lower=0.04125.
   - Capture-controlled increment +0.05293.
   - All leave-market/event/family means positive.

2. **PRE_ELECTION — update hazard, `genuine_age_s`, 300s.**
   - 297,384 obs / 108 markets / 5 events / 3 families.
   - MSE improves **35.35%**.
   - p=0.000244; bootstrap lower=0.02763.
   - Capture-controlled increment +0.02730.
   - All leave-market/event/family means positive.

3. **ACTIVE_RESULTS — jump hazard, `genuine_age_s`, 300s.**
   - 14,037 obs / 61 markets / 5 events / 3 families.
   - MSE improves **16.04%**.
   - p=0.0115; bootstrap lower=0.00423.
   - Capture-controlled increment +0.01374.

4. **PRE_ELECTION — spread change, `trade_abs_impact_60`, 15s.**
   - 91,739 obs / 60 markets / 3 events / 2 families.
   - MSE improves **0.74%**.
   - p=0.000976; bootstrap lower=5.49e-8.
   - Capture-controlled increment +2.60e-7.

## Interpretation that MASTER should preserve

**The headline is stale-economic-state / renewal hazard, not generic microstructure alpha.**

`genuine_age_s` replicates for update hazard in PRE and ACTIVE and survives raw-capture controls. However, 300s and 1800s delayed versions remain predictive, so this is a persistent state variable rather than a short-lived propagation effect.

ACTIVE jump hazard is more local: the 1800s shift collapses from +0.01412 primary loss improvement to roughly +0.000008.

PRE liquidity is formally supported but should be treated cautiously. The 300s and 1800s delayed `trade_abs_impact_60` values retain almost all of the tiny primary lift. It is more consistent with a slow activity/liquidity regime proxy than a fresh trade-impact mechanism.

## Strong negatives

Do **not** promote:

- ACTIVE `snapshot_ofi_norm` depth-vol or micro-FV: zero HOLDOUT increment; capture-confounded.
- ACTIVE event-time `qbid` price/volatility: negative HOLDOUT performance.
- PRE price `rv_60`: negative HOLDOUT performance; capture-confounded.
- PRE `rv_300` volatility: bootstrap fails.
- PRE depth slope / imbalance: dependence-aware tests fail.
- PRE event-time `qask` volatility: aggregate mean positive, but market/event/family leave-outs fail.
- The three `SHADOW_ONLY_NO_FDR` coordinates remain non-promotable regardless of HOLDOUT.

## Boundary

005F supports predictive state variables only. It does not establish causality, executable PnL, participant skill, maker/taker role, queue position, passive fill probability, exact cancellation timing, or within-millisecond ordering.

Queue-dependent ledger objects remain `UNTESTABLE_WITH_CURRENT_HISTORICAL_DATA`.

## Canonical artifacts

- Final report: `docs/experiments/EXPERIMENT_005F_FINAL_REPORT.md`
- Raw HOLDOUT: `data/experiments/experiment_005f/holdout/`
- Evidence manifest: `data/experiments/experiment_005f/holdout_evidence_manifest.json`
- Pre-HOLDOUT freeze: `data/experiments/experiment_005f/pre_holdout_freeze.json`
- Fit manifest: `data/experiments/experiment_005f/fit_freeze_manifest.json`

**No scientific blocker remains. Independent review should focus on provenance, the stringent promotion gate, and whether downstream strategy work keeps the state/hazard interpretation narrow.**
