# EXPERIMENT-005G — Sealed HOLDOUT Protocol

Status: FROZEN BEFORE HOLDOUT READ

## Data boundary

The only HOLDOUT used by EXPERIMENT-005G is the fresh V3 DATA-003-linked September
baseline interval:

- dataset: `polyleviathan/sig-cup-data003-orderbooks`
- dataset version: `1`
- HOLDOUT: `2026-09-05T00:00:00Z` through `2026-09-06T00:00:00Z`
- source files: exactly 24 hourly `baseline_sep/date=2026-09-05/hour=HH/events.parquet`
- accepted SIG↔Polymarket mapping SHA-256:
  `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`

The sealed evaluator may also receive Sep 1–4 files as TRAIN+DEV context/refit data.
No source outside the frozen five-day baseline window is allowed in that job.

## Candidate admission

Before any Sep 5 file is supplied, `PRE_HOLDOUT_FREEZE.json` must exist, be committed,
and assert `holdout_read=false`.

Lane A admission is mechanical:

- admit a strict-replication coordinate only if it cleared its frozen TRAIN/DEV support,
  dependence-aware sign-flip, block-bootstrap and leave-market gates;
- do not rescue a coordinate by changing the feature, target, threshold, horizon, model,
  block definition or split after seeing DEV;
- the ACTIVE EV18 update coordinate remains
  `REPLICATION_INCONCLUSIVE_DEPENDENCE_UNDERPOWERED` and is not admitted;
- the ACTIVE jump coordinate remains `NOT_REPLICABLE_WITH_THIS_DATA`;
- the ACTIVE price negative and PRE liquidity coordinates do not enter HOLDOUT;
- `005F_REPL_PRE_UPDATE` is admitted because its frozen Sep TRAIN/DEV gate passed.

Lane B admission is also mechanical:

- only rows labelled `promotion_gate_pass=true` by the frozen discovery runner may enter;
- each admitted coordinate keeps its exact target, feature, feature family, target family,
  baseline and model family;
- TRAIN/DEV discoveries remain `DISCOVERY_ONLY` until independent HOLDOUT support;
- no manual replacement candidate is allowed after the discovery screen is observed.

## Frozen refit and evaluation

The evaluator is `scripts/research/experiment_005g_holdout.py`.

For each frozen candidate it:

1. verifies the exact pre-holdout freeze SHA-256;
2. reconstructs observable features using `timestamp_received` and backward-only as-of joins;
3. refits the frozen model form on TRAIN+DEV only;
4. scores HOLDOUT only;
5. reports observation support, distinct markets, baseline/challenger MSE,
   30-minute block sign-flip evidence, block-bootstrap lower bound and leave-market stability.

No feature selection, model tournament, hyperparameter search, threshold change, horizon change,
replacement candidate or post-HOLDOUT tuning is permitted.

## Final labels

Lane A:

- `FRESH_REPLICATION`: positive incremental HOLDOUT loss improvement, sign-flip p ≤ 0.10,
  block-bootstrap lower bound > 0, and leave-market minimum > 0.
- `REPLICATION_FAILED`: evaluated but the conjunctive frozen gate fails.
- `REPLICATION_INCONCLUSIVE`: support/observability prevents an adequate frozen test.
- `NOT_REPLICABLE_WITH_THIS_DATA`: the exact old object cannot be formed without changing
  its scientific definition.

Lane B:

- `INDEPENDENTLY_SUPPORTED_DISCOVERY`: the same conjunctive HOLDOUT gate passes.
- `DISCOVERY_NOT_CONFIRMED`: evaluated but the frozen HOLDOUT gate fails.
- support/semantics failures remain explicit and are not converted into negative alpha claims.

## Economic routing

005G does not modify MAKE and sends no SIG orders.

- fair-value / price candidates -> R2/R3-FV and SHADOW;
- maker spread / toxicity / resilience candidates -> MM-REPLAY-001;
- diagnostic state variables -> LIVE-DIAG / LIVE-LEARN;
- anything requiring queue or passive-fill assumptions remains outside 005G.

REAL SIG ORDERS SENT: NO
