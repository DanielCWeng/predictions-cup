# EXPERIMENT-005B — Full Historical Price / Fill Predictive Atlas

**Branch:** `experiment/005b-historical-predictive-atlas`  
**Canonical base:** `ba938bedcf63f562be8b26c9502e828391123867`  
**Scope:** identity-blind scalar forecasting from transaction prices and anonymous fill/activity history.

005B is a broad discovery census, not a hand-picked mechanism test. The feature universe, targets, splits, screening policy and model ladder are frozen before detailed prior-outcome inspection. The machine-readable source of truth is `data/experiments/experiment_005b/preregistration.json`.

## Canonical economic trade series

DATA-002 is participant-side `OrderFilled` evidence rather than an economic-trade table. 005B groups rows by `family, condition_id, tx_hash`, requires exactly one active-order aggregate row and one or more passive rows, and uses the passive legs as the economic fills. The active row exists only for conservation audit and is never double-counted as another trade.

Every binary leg is mapped to a YES-probability axis: YES uses `p_yes=p`; NO uses `p_yes=1-p`. OTHER/non-binary rows fail closed. Multi-maker matches remain multiple passive economic fills in deterministic `timestamp, tx_hash, log_index` order.

Role, fee and participant fields are reconstruction/audit metadata only. They are removed before predictors are built.

## Features and targets

Backward price/movement/activity windows are 5, 15, 30, 60, 120, 300 and 900 seconds. The atlas covers price/logit history, momentum and reversal, streak/acceleration/jumps, high-low distance, slopes, realised movement/stasis/change state, anonymous count/notional/share/size/inter-arrival/burst state, plus mechanically target-excluding election-family and event activity/movement/dispersion/direction aggregates.

Large-trade thresholds and normal-activity references are TRAIN-only.

Clock targets use 1, 5, 15, 30, 60, 120 and 300 seconds. A target is valid only when at least one later economic trade occurs inside the horizon. No later trade is `NO_FUTURE_OBSERVATION`, not zero movement. Event-time targets use the next 1, 2, 5 and 10 economic trades.

## Split and leakage discipline

Each family receives one synchronized chronological TRAIN/DEV/HOLDOUT split at 60/20/20 of timestamp coverage, shared by all markets in that family. Label intervals crossing the next boundary are purged and a 300-second embargo applies at both boundaries.

HOLDOUT remains sealed until TRAIN census, redundancy collapse, DEV selection, model specifications and hyperparameters are frozen and committed.

## Census, redundancy and model ladder

Supported TRAIN feature × target × horizon cells report Pearson/Spearman association, directional and decile response, monotonicity, standardized effect, support, market/block counts, concentration and sign consistency. Raw p-value is not a selection criterion.

TRAIN-only redundancy uses absolute Spearman >= 0.95 plus explicit algebraic equivalence. Regression models are persistence, own-history, univariate linear, OLS, Ridge, ElasticNet and one small histogram-gradient-boosting challenger. Classification uses majority and L2 logistic baselines. Hyperparameter grids are frozen in the preregistration and selected on DEV only.

005C owns reduced-rank whole-panel models; 005B will not absorb that work.

## Compute discipline

The empirical namespace is `005b_historical_predictive_atlas`. Kaggle has five shared concurrent batch-CPU slots; 005B may launch while fewer than five jobs are running or queued. EC2 is limited to Git/repository work, hashes, packaging, Kaggle CLI and synthetic/unit tests. Historical feature census, target generation, model fitting and statistics do not run on EC2.
