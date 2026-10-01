# EXPERIMENT-005G — Lane Closure Report

**Branch:** `experiment/005g-data003-orderbook-atlas`  
**PR:** #114  
**Final status:** `CLOSED_AFTER_FAILURE_FORENSICS`  
**Analysis boundary:** scientific evidence frozen; economic conversion failed; post-hoc forensics completed; no live promotion.

## Executive summary

EXPERIMENT-005G established that Polymarket order-book state contains robust, transferable information about **renewal and regime transition**, but that this information does **not** function as a standalone maker-toxicity signal.

The strongest sealed scientific findings were:

- `state_dwell_s -> state_transition_h300`: approximately **40.3% relative MSE improvement**;
- `state_dwell_s -> state_transition_h60`: approximately **20.8%**;
- `genuine_age_s -> update_h300`: approximately **26.5% fresh replication**;
- **10 of 12** frozen scientific candidates supported: one fresh replication plus nine independent supported discoveries.

The later preregistered economic conversion did not promote any intervention. On DEV, all 28 frozen WIDTH/SIZE/WAIT/REFRESH candidates reduced net economic value versus the passive baseline.

Final interpretation:

> 005G provides a validated representation of market state and transition hazard. It does not establish directional fair value, maker alpha, or a monotone "high hazard = toxic quote" rule.

The useful surviving role is **context** for shadow/live diagnostics and potentially as an input to a separately validated economic model. It must not independently control live quoting.

## 1. Data and provenance

Primary dataset:

`polyleviathan/sig-cup-data003-orderbooks`, version 1.

Key provenance:

- acquisition SHA-256: `d25f07e450f57647f198a9e2730889a7846b78a2c4d6f84474b5b45bc33f8532`;
- accepted SIG/Polymarket mapping SHA-256: `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`;
- pre-holdout freeze commit: `a9219762fb49faa7c98fd8f7a3c7a888a5352243`;
- pre-holdout freeze SHA-256: `887592885c02ae9219232c40a8b9ce0b263f9a62e04da62c524b16162895cc1c`;
- sealed scientific holdout workflow: `36843408540`;
- sealed scientific holdout artifact SHA-256: `2c5bb46ebcf5b8d79e7368afdb309c849e55992ad44a771ab849740a219ef4d0`.

The scientific panel contained:

- TRAIN: **720,528** rows;
- DEV: **246,545** rows;
- sealed HOLDOUT: **248,221** rows;
- approximately **692 tokens**;
- sequential transition analysis: **126,309** rows across **317 tokens**.

## 2. Scientific result

The primary finding is:

`STATE_DEPENDENT_RENEWAL_AND_TRANSITION_HAZARD`.

The strongest variable was `state_dwell_s`: how long the book has remained in the current state carries substantial information about whether that state will terminate over the next minute/five minutes.

Additional supported context variables included:

- `genuine_age_s`;
- `spread_x_distance`;
- `distance_from_0_5` for renewal;
- `volatility_x_liquidity`;
- `price_change_age_s`.

Explicit exclusions/failures included:

- `ofi_acceleration`;
- `distance_from_0_5 -> abs_h60`;
- `PRE_LIQUIDITY`;
- `ACTIVE_JUMP`.

This evidence supports **state prediction**, not signed future value.

## 3. Frozen downstream bundle

The frozen downstream research bundle is:

`data/experiments/experiment_005g/STATE_HAZARD_V1.json`

Permitted interpretation:

- shadow-state context;
- replay context;
- diagnostic/regime annotation;
- potential input feature for an independently validated economic model.

Prohibited interpretation:

- directional fair-value alpha;
- expected return or Sharpe;
- direct live maker improvement;
- standalone widening/cancelling rule without economic evidence.

## 4. Economic conversion

The preregistered economic conversion used a disjoint post-scientific window:

- TRAIN: **2026-09-06**;
- DEV: **2026-09-07**;
- economic HOLDOUT: **2026-09-08**.

Economic panel sizes:

- TRAIN: **312,709** state rows, 222 observed trade rows;
- DEV: **290,196** state rows, 236 observed trade rows;
- HOLDOUT: **383,796** state rows, 425 observed trade rows.

The DEV passive baseline produced:

- net 60-second markout-P&L proxy: **1.6715**;
- **100 filled sides**;
- **36 filled markets**;
- turnover proxy: approximately **41.728**;
- fee proxy: **0** in this historical replay.

Frozen policy families:

| Family | Candidates | Passed |
|---|---:|---:|
| WIDTH_ONLY | 8 | 0 |
| SIZE_ONLY | 8 | 0 |
| WAIT_ONLY | 4 | 0 |
| REFRESH_ONLY | 8 | 0 |

There was therefore **no DEV champion**. No candidate was promoted onto the economic holdout. Sep 8 remained baseline-only for diagnostics, with no post-holdout tuning or rescue.

Successful economic replay:

- workflow: `36859479989`;
- artifact ID: `11161197062`;
- artifact SHA-256: `ecd0a08ef52c5c38bc14e408d472739fb69ef6fe869f4f04cfef685b10ac36f8`.

## 5. Failure forensics

The final post-hoc forensic task was descriptive only and used TRAIN/DEV, not the economic holdout for selection.

The hazard continued to predict its intended targets on DEV:

- future BBO update h60 AUC: **0.675**;
- future state-transition h300 AUC: **0.740**.

But it did not rank passive-fill toxicity:

- adverse-fill AUC: **0.456**;
- hazard versus signed maker markout Spearman: **+0.346**;
- hazard versus absolute markout Spearman: **+0.520**.

The highest hazard quintile was economically strong in the DEV proxy:

- **30 fills**;
- highest fill probability;
- approximately **+0.05017 net markout per fill**;
- **86.7% positive markouts**.

This explains the conversion failure. The economic interventions interpreted high transition/activity hazard as "quote less." In this sample, that often removed the most valuable fills.

The cleanest example is WAIT q90:

- 19 baseline fills removed;
- 17 favourable;
- 2 adverse;
- adverse loss avoided: **0.3000**;
- favourable markout discarded: **1.8000**;
- net delta: **-1.5000**.

Across all **28** frozen economic candidates, favourable-fill value sacrificed exceeded adverse-fill value avoided.

The failure-forensics classification is:

`MIXED`, dominated by **`STATE_ONLY`**, with **`CONDITIONAL_CONTEXT`** and **`PROXY_LIMITED`** as secondary qualifications.

## 6. What survived

Retain:

- `RETAIN_FOR_SHADOW_CONTEXT`;
- `RETAIN_FOR_LIVE_DIAGNOSTICS`;
- `ARCHIVE_PREDICTIVE_EVIDENCE`.

Do not:

- use 005G as a standalone MAKE controller;
- map high hazard monotonically to widen/size-down/wait/refresh-faster;
- treat `state_dwell_s` or `spread_x_distance` as signed adverse-selection signals;
- retrain the same renewal target and call it a toxicity model;
- reopen DEV/HOLDOUT for threshold or market-subset rescue;
- start another 005G strategy experiment.

The sensible future use is as **context inside a separate economic signal stack**. For example, another model may learn interactions between fair-value discrepancy, directional evidence, fill probability and 005G state variables. That would be a new independent experiment, not a reopening of 005G.

## 7. Final lesson

005G was scientifically successful and economically disciplined.

It found a real property of the book:

> **state duration/freshness predicts when the market state is likely to change.**

It then correctly rejected an invalid stronger claim:

> **state transition hazard is not automatically maker adverse-selection hazard.**

That distinction is valuable. The lane delivered a reusable state representation and prevented a superficially plausible but economically harmful quoting rule from reaching production.

## Canonical files

- `docs/experiments/EXPERIMENT_005G_FINAL.md`
- `docs/experiments/EXPERIMENT_005G_FAILURE_FORENSICS.md`
- `data/experiments/experiment_005g/FINAL_RESULT.json`
- `data/experiments/experiment_005g/STATE_HAZARD_V1.json`
- `data/experiments/experiment_005g/economic_replay/RESULT.json`
- `data/experiments/experiment_005g/FAILURE_FORENSICS.json`
- `data/experiments/experiment_005g/status.json`

## Closure

`005G_STATUS: CLOSED`

`LIVE_MAKE_PROMOTION: NO`

`REAL_SIG_ORDERS_SENT: NO`
