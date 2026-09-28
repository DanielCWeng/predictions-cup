# EXPERIMENT-005F — HOLDOUT EXECUTION PROTOCOL

Status: **FROZEN BEFORE CANONICAL TRAIN/DEV RESULTS**

This protocol defines how EXPERIMENT-005F moves from the canonical TRAIN/DEV discovery run to
HOLDOUT. It does not contain candidate identities or result values.

## Stage 1 — canonical TRAIN/DEV selection

The canonical TRAIN/DEV kernel must:

- read no rows after each event/regime DEV boundary;
- write the complete feature screen, model tournament and `dev_selection.json`;
- mark candidates as either `CONFIRMATORY_ELIGIBLE` or `SHADOW_ONLY_NO_FDR`;
- select no more than one coordinate per dataset × regime × target family;
- preserve the frozen support, stability and FDR gates.

Engineering smoke kernels are not scientific evidence and their result values are ignored.

## Stage 2 — fit freeze

A separate Kaggle fit-freeze kernel is run only after the canonical TRAIN/DEV outputs are committed.

It may read TRAIN and DEV only. It must not read HOLDOUT rows.

For every frozen selected coordinate it must:

1. reproduce the exact selected feature/feature-block, target and horizon;
2. reproduce the exact selected model class and hyperparameters;
3. use the frozen deterministic thinning policy;
4. fit the final feature scaler on TRAIN+DEV rows used for the final model;
5. fit the final challenger on those same TRAIN+DEV rows;
6. fit the corresponding own-history baseline on the corresponding TRAIN+DEV sample;
7. serialize the fitted scaler/model objects and exact feature-column order;
8. record package/library versions;
9. SHA-256 every serialized artifact.

The fit-freeze kernel must output a machine-readable manifest. That manifest and all model hashes are
committed to the 005F branch before HOLDOUT is accessed.

No candidate, feature, horizon, model class or hyperparameter may be changed in this stage.

## Stage 3 — immutable pre-HOLDOUT freeze

Before any HOLDOUT kernel is launched, commit
`data/experiments/experiment_005f/pre_holdout_freeze.json`.

It must contain:

- canonical TRAIN/DEV Kaggle kernel slug + version;
- canonical TRAIN/DEV output hashes;
- exact selected coordinates and their selection class;
- exact selected model/hyperparameter per coordinate;
- fit-freeze Kaggle kernel slug + version;
- fitted artifact SHA-256 hashes;
- exact baseline/challenger feature-column order;
- exact data, identity, regime and code hashes;
- inference settings and block definitions;
- explicit `holdout_read: false`.

This commit is the scientific gate. Any change after it that affects a scientific choice creates a
new experiment version and cannot be called confirmatory 005F HOLDOUT evidence.

## Stage 4 — one-shot HOLDOUT evaluation

The HOLDOUT kernel:

- mounts only the frozen code/model bundle plus DATA-001;
- loads only the minimum trailing context needed to calculate lagged features, plus HOLDOUT;
- never tunes or selects anything;
- evaluates every frozen coordinate exactly once;
- preserves `CONFIRMATORY_ELIGIBLE` versus `SHADOW_ONLY_NO_FDR` labels;
- cannot promote a shadow-only coordinate regardless of HOLDOUT performance.

For clock-time labels, rows whose target extends beyond the factual event/regime boundary are
discarded. For event-time labels, the future k-th genuine event must occur inside the same HOLDOUT
regime. Context rows before HOLDOUT may supply trailing features but never evaluation labels.

## HOLDOUT evidence

For each coordinate report at minimum:

- observations, markets, event windows and election families;
- baseline loss and challenger loss;
- incremental loss improvement;
- regression MAE/RMSE where applicable;
- Brier score for hazard/jump classification;
- 30-minute-block sign-flip/randomisation evidence;
- moving/block-bootstrap uncertainty;
- leave-market-out and leave-event-window-out stability;
- capture-process placebo diagnostics when the selected feature is not itself a capture-process
  feature;
- source-version and continuity-gap stratification where supported.

Tick count is never treated as iid sample size.

## Promotion language

A `CONFIRMATORY_ELIGIBLE` candidate can be described as HOLDOUT-supported only if it preserves a
positive incremental effect and passes the predeclared falsification/stability checks. A single
market cannot promote a claim.

A `SHADOW_ONLY_NO_FDR` candidate is always exploratory/falsification evidence. Strong HOLDOUT
performance does not retroactively repair the DEV multiplicity failure.

Historical Polymarket evidence is not production SIG execution evidence.

No real order placement is enabled anywhere in 005F.
