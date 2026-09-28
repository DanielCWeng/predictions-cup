# EXPERIMENT-005D — Independent Review Follow-up

**Status:** PRE-EXECUTION FROZEN  
**Label:** `POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION`  
**Branch:** `experiment/005d-structural-rv-atlas`  
**Scope:** fixed 13-cell predictive HOLDOUT family only

## Purpose

This follow-up does not reopen EXPERIMENT-005D discovery. The original corrected HOLDOUT is already exposed and remains the only confirmatory HOLDOUT. The purpose here is to determine where that evidence occurred in election time, how sensitive its uncertainty is to materially longer dependence blocks, whether the three original passing cells are concentrated in a small number of temporal episodes, and how to translate the frozen residualized regression coefficient into an interpretable probability-response scale.

Nothing in this follow-up may promote a failed candidate, introduce a new relationship, representation, horizon, signal, control, fitted parameter, outcome, timestamp or FDR family.

## Accepted evidence chain

The follow-up regenerates and verifies the accepted corrected HOLDOUT runner before appending review-only diagnostics:

- base: `ba938bedcf63f562be8b26c9502e828391123867`
- preregistration: `d43a75ff8e0b5df6c717f37721e339116a6297b299c4e26d8b9a5f242b9c7a51`
- TRAIN/DEV runner: `525f4678565d80cbceddd7ede298c7f810fc41c4b94652dec34f08cd63ccaa5f`
- PRE_HOLDOUT_FREEZE: `6ac0f6ea27b5553267d8fa94a9f5bca906680f18d03de45480269da8b9b21cbe`
- corrected HOLDOUT runner: `e65c961b73c56e26b322c8336944ea2fbc048ccf53bdd645cdaf77c53caef7ce`
- corrected HOLDOUT results: `610e4b6bd0ed199c63cc2543206ce70c4900e7cb1b84a24bf0e572f4f975920b`

The review runner is frozen at `b394bce34097a228281599862799837fb501bb5c9a30cf63c7583c061b2dad9f`.

## Canonical event-time attribution

Regime boundaries come only from EXPERIMENT-004A's frozen `regime_definitions.json` and `event_timeline.json`. Intervals are half-open. Each valid HOLDOUT observation is attributed by its signal/forecast-origin timestamp to one of:

- PRE_ELECTION
- ELECTION_DAY_PRE_RESULTS
- ACTIVE_RESULTS
- LATE_COUNT
- POST_RESOLUTION_DIAGNOSTIC
- OUTSIDE_ALL_CANONICAL_WINDOWS

For every fixed cell the review records the full event-panel span, TRAIN/DEV/HOLDOUT spans, both 300-second purge/embargo gaps, valid HOLDOUT count, regime counts/shares, and contribution shares for `signal_residual × future_response_residual`.

## Dependence sensitivity

The original 300-second result is preserved as the reference result. Post-hoc robustness uses the same null-centering principle and the same fixed 13-cell BH family with these block lengths, chosen before review output is inspected:

- 30 minutes
- 1 hour
- 4 hours
- 8 hours

The 8-hour block is the single additional longer sensitivity chosen in advance. Each block length gets its own bootstrap interval and descriptive BH q-value across the same 13 cells. These are robustness diagnostics, not replacement confirmatory p-values.

## Temporal concentration

Only the three original confirmatory-pass cells receive concentration diagnostics.

The aggregation rule is fixed **before** inspection: UTC clock-hour bins aligned to the top of the hour. A contiguous active period is a run of adjacent occupied UTC hours.

The review reports distinct occupied hours, contiguous periods, hourly contribution, positive/absolute contribution share of the top 1/3/6 hours, first-versus-second temporal-half score, and the score after removing the single hour with the largest positive contribution. No alternative temporal partition will be searched for a better-looking result.

## Economic-scale translation

The original primary statistic is the TRAIN-controlled covariance between the structural signal residual and future target-price-change residual.

For each original passing cell, the frozen HOLDOUT beta is translated into the local probability response associated with 1pp, 5pp and 10pp residualized structural signals. These translations are descriptive regression-scale diagnostics only. They are **not** PnL, executable spread, Sharpe, RMSE improvement or directly realizable edge.

## Execution rule

Real-data attribution and dependence sensitivity run only on Kaggle. EC2 is limited to repository operations, hashes, generation, compilation and synthetic smoke testing.

The original empirical outputs are immutable. The follow-up can only retain, weaken, downgrade or reject the existing claims.
