# PRED-007 — Event-Triggered Election Index → Held-Out Market Repricing

**Status:** FROZEN DESIGN BEFORE PRED-007 EMPIRICAL OUTCOME CALCULATION  
**Branch:** `experiment/pred-007-event-triggered-index`  
**Base:** `249e3ec4cebcb2e6495e88f9ab93d0c8ce90326a`  
**Classification:** `HISTORICAL_MECHANISM_EXPLORATION_ONLY`

## Question

Does a leave-target-out election-level index, updated only when its source markets genuinely change, contain information that appears in a held-out prediction market 1–10 seconds later?

The intended launch mechanism is:

`many related external markets → aggregate/index shock → SIG target`

rather than:

`one external market → another external market → SIG target`.

This is specifically an event-time latency experiment. It is not another 30-second clock-grid low-rank test.

## Prior-work boundary

EXPERIMENT-003 LOWRANK-001 already tested a TRAIN-only rank-1 SVD factor on a 30-second grid. Its usable evidence collapsed to one Peru regime; its only positive primary-rank cell was small and non-significant at 30 seconds, and stability failed.

EXPERIMENT-004C later demoted generic pairwise internal lead/lag after stronger controls.

PRED-007 therefore may not claim novelty from “cross-market factor predicts another market” in general. The new hypothesis is narrower:

1. source observations are **genuine source BBO-change events**, coalesced to one-second decision buckets;
2. target response horizons are **1, 2, 5, 10 seconds**; 30 seconds is secondary;
3. the source index excludes the target and every source sharing the target's Polymarket `event_id`, preventing same-event partition siblings from creating a mechanical reconstruction;
4. the aggregate must beat both target-own-history and a fixed single-source attention comparator.

All historical DATA-001 event families used here have already been exposed by prior research. A positive result is exploratory mechanism evidence only and cannot be described as fresh confirmation.

## Frozen data

Accepted DATA-001 historical replay corpus:

- corpus manifest SHA-256: `e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4`;
- market identity SHA-256: `e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72`.

Frozen metadata is reused from EXPERIMENT-003:

- preregistration SHA-256: `64af4c1427afa983a13da6a11d5721b1b6496b0306ce1b07ab6e2f19df9e7e70`;
- relationship inventory SHA-256: `e93033568ac3d3045399e6b630d37bb30a829c9c2fad3ef5da8ac31a7516732e`;
- code marker: `b2ba5e2c3e63d57bd6e74c0c5861bad67c983e1b`.

No market may be added because its PRED-007 result looks good.

## Frozen universe construction

For each DATA-001 regime, start from the union of tokens already declared in EXPERIMENT-003's manual relationship inventory and frozen low-rank universe. Retain only canonical Yes tokens present in accepted `market_identity.csv`.

For each target:

- remove the target itself from the source pool;
- remove every source with the same Polymarket `event_id` as the target;
- require at least three remaining source markets;
- require target and source quotes to pass the accepted DATA-001 ambiguity/freshness handling.

This is the primary anti-leakage rule. Same-event siblings may be described in coverage diagnostics but may not enter the primary index.

## Event clock

PRED-007 is sparse event time, not a complete clock grid.

For each target/source pool:

1. collect source `book_changes.observed_at` timestamps;
2. map each source update to its UTC one-second bucket;
3. retain one decision timestamp at the **end** of each bucket in which at least one source changed;
4. all index features are sampled as-of that decision timestamp;
5. no source change after the decision timestamp may enter the feature.

The target is not allowed to create an observation timestamp.

## Features

Quote freshness is fixed at 120 seconds. Coordinates are logit(midpoint), clamped exactly as EXPERIMENT-003.

For each decision timestamp compute:

- target own 1-second and 5-second logit moves;
- each source's 1-second and 5-second logit moves;
- equal-weight source index at 1 second and 5 seconds;
- attention-weighted source index at 1 second and 5 seconds.

Attention weights are outcome-blind and TRAIN-only:

`w_j ∝ sqrt(1 + source_j genuine BBO-change count in TRAIN)`.

The fixed single-source comparator is the source with the largest TRAIN genuine BBO-change count; ties break by token ID. It is **not** chosen using target returns.

## Chronological split

Each regime is split by wall-clock time:

- first 60%: TRAIN;
- final 40%: EVALUATION;
- a 30-second embargo separates TRAIN and EVALUATION.

No hyperparameter or source selection uses EVALUATION target outcomes.

Because these event families were previously studied, the EVALUATION label does not mean pristine holdout.

## Shock definition

The attention-index shock threshold is the TRAIN 90th percentile of absolute 1-second attention-index moves, computed separately for each target/source pool.

Primary evaluation uses only EVALUATION source-event timestamps whose absolute attention-index 1-second move is at least that frozen threshold.

All-event results are secondary diagnostics.

If support is insufficient, the cell is unavailable; the threshold is not relaxed.

## Models

Models are ordinary least squares with TRAIN-only standardization and deterministic least-squares fitting.

For every target and horizon:

- **OWN:** target own 1s + 5s moves;
- **TOP_SOURCE:** OWN + fixed highest-attention source 1s + 5s moves;
- **EW_INDEX:** OWN + equal-weight index 1s + 5s moves;
- **ATTN_INDEX:** OWN + attention-weighted index 1s + 5s moves;
- **DELAYED_ATTN:** OWN + the same attention-index features delayed 300 seconds.

No nonlinear models, rank search, regularization search, threshold search or target-specific tuning are permitted.

## Horizons and metrics

Primary horizons: **1, 2, 5, 10 seconds**.  
Secondary diagnostic horizon: **30 seconds**.

Primary loss is squared error of future target logit repricing. For a model A against model B:

`delta_mse(A→B) = MSE(A) - MSE(B)`.

Positive values favour B.

PRED-007 reports:

- absolute and relative MSE improvement versus OWN;
- ATTN_INDEX improvement versus TOP_SOURCE;
- EW_INDEX improvement versus TOP_SOURCE;
- prediction/target correlation;
- sign accuracy on non-zero future moves;
- support by target, regime and event family.

## Dependence-aware uncertainty

For pooled horizon/model comparisons, observations are clustered by `regime_id × UTC-minute`. Loss differences are averaged inside each cluster before deterministic 1,000-draw cluster bootstrap.

The primary uncertainty interval is the 2.5%–97.5% percentile interval of the mean cluster loss improvement.

Event-family sign breadth is reported separately; row counts are not treated as independent sample size.

## Primary gate

A horizon is a PRED-007 mechanism survivor only if **ATTN_INDEX** simultaneously:

1. has positive relative MSE improvement versus OWN;
2. has bootstrap lower 95% bound above zero versus OWN;
3. beats TOP_SOURCE with bootstrap lower 95% bound above zero;
4. is positive versus OWN in at least two independent event families;
5. has at least 1,000 pooled shock-evaluation observations and at least 10 target markets;
6. beats DELAYED_ATTN on pooled MSE.

No aggregate score is allowed to compensate for a failed condition.

If no 1/2/5/10-second horizon passes, disposition is `NO_MACHINE_SPEED_INDEX_SIGNAL`.

A 30-second-only effect is labelled `SLOW_INDEX_EFFECT_ONLY`, not machine-speed alpha.

Any primary-horizon survivor is labelled `HISTORICAL_INDEX_MECHANISM_CANDIDATE_REQUIRES_FUTURE_LIVE_CONFIRMATION`.

## Stop rule

After the first PRED-007 execution:

- no source-universe rescue;
- no shock-percentile rescue;
- no horizon rescue;
- no alternate weighting rescue;
- no same-event-sibling inclusion;
- no feature/model retuning.

Post-result diagnostics may demote or explain the result but may not promote a failed lane.

## Production boundary

PRED-007 cannot authorize live trading or MAKE integration.

A historical survivor may only justify a frozen future confirmation on genuinely future CAPTURE-001 / competition-period evidence and, if confirmed, a plug-in signal contract for MAKE.
