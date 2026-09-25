# EXPERIMENT-002 — Lead/Lag, Relative Value and LOO-Family Experiments

## Scope

EXPERIMENT-002 is the first empirical strategy layer on top of BUILD-005. It adds executable,
reproducible relationship experiments without adding order submission, maker-fill simulation,
production fair value, risk or inventory management.

All experiment decisions consume BUILD-005 `ReplayState` through `ReplayRunner`. Observable
time therefore remains the only replay clock and future quotes are never borrowed backward.

## Shared relationship contract

The four implemented families use one `RelationshipExperimentSpec` and one
`RelationshipExperimentRunner`:

- `LEADLAG`
- `RV`
- `LOO-PRICE`
- `LOO-FAMILY`

Every relationship is explicit. Configuration identifies the target and each reference by
`ReplaySource` plus instrument ID. There is no title matching or semantic inference.

References also carry:

- explicit positive weight;
- `SAME` or `COMPLEMENT` direction;
- leakage classification: `INDIRECT`, `DIRECT_EQUIVALENT`, `COMPLEMENT` or
  `MECHANICAL_SIBLING`.

## Price coordinates and reference prices

Feature movement can be measured in probability or logit space.

Probability movement:

```text
delta_p = p_t - p_(t-k)
```

Logit movement:

```text
z = ln(p / (1-p))
delta_z = z_t - z_(t-k)
```

The logit clamp is explicit configuration and is recorded in every feature vector. The default is
`1e-6`.

Reference-price modes are:

- `MIDPOINT`;
- `MICROPRICE_IF_DEPTH_AVAILABLE`, falling back to midpoint when top-level depth is absent;
- `BID`;
- `ASK`;
- `LAST_TRADE`.

`COMPLEMENT` transforms are side-aware: complement BID is `1 - source ASK` and complement ASK
is `1 - source BID`. Symmetric modes such as midpoint, microprice and last trade transform as
`1 - p`.

`LAST_TRADE` freshness is tracked from the observable timestamp of a captured trade event, not
from quote freshness. A fresh trade-only reference is usable; a stale trade does not become fresh
because a later quote arrives. Quote snapshots that merely carry a last-trade value do not invent
a new trade-observation timestamp.

These modes build features only. Economic outcomes always use executable bid/ask crossing.

## LEADLAG

A lead/lag signal is produced only from configured references. At each reference update, the runner
compares the current reference price with the most recent observable reference state at or before
`decision_time - lookback`.

For multiple references:

```text
weighted_delta_p = sum(weight_j * signed_delta_p_j)
weighted_delta_z = sum(weight_j * signed_delta_z_j)
```

A `COMPLEMENT` reference is transformed in probability space as `1 - p` before movement is
calculated.

Signal direction is:

```text
positive impulse -> BUY_YES
negative impulse -> SELL_YES
```

Thresholding is episode-based. Once a signal fires, the runner disarms that direction until the
configured score falls back inside the threshold band or the sign reverses. A configurable
cooldown is an additional guard. This prevents one continuous move from producing an unbounded
stream of nearly identical observations.

## RV / structural residual

The baseline reference estimators are deliberately simple:

```text
weighted probability:
p_ref = sum(w_j * p_j) / sum(w_j)

weighted logit:
z_ref = sum(w_j * logit(p_j)) / sum(w_j)
p_ref = sigmoid(z_ref)
```

Residuals are recorded in both coordinates:

```text
delta_p = p_target - p_ref
delta_z = logit(p_target) - logit(p_ref)
```

A positive residual means the target is rich relative to the configured reference and maps to
`SELL_YES`. A negative residual maps to `BUY_YES`.

Weights are explicit configuration. The runner does not infer weights from volume, depth or
holdout performance.

## LOO-PRICE and LOO-FAMILY

The target quote can never appear inside the predictor set.

`LOO-PRICE` may use configured direct-family/equivalent references. The target quote is excluded
from fair-value construction but remains available as the executable price against which the
prediction is assessed.

`LOO-FAMILY` is stricter. Construction fails before replay if any predictor:

- is the target;
- is listed in `direct_family`;
- is listed in `excluded_family`;
- is classified as `DIRECT_EQUIVALENT`;
- is classified as `COMPLEMENT`;
- is classified as `MECHANICAL_SIBLING`.

This makes the distinction a typed configuration invariant rather than a naming convention.

## Feature contract

Every emitted observation carries a typed `FeatureVector` containing:

- aggregate probability movement;
- aggregate logit movement;
- target spread;
- top-of-book depth when available;
- target quote age;
- probability residual;
- logit residual;
- number of active references;
- configured logit clamp.

Flow features are intentionally not implemented in this ticket. They can be added later without
changing replay/economic semantics.

## Freshness and invalidity

A configured reference must be available, valid and fresh. The runner surfaces invalid samples
instead of silently dropping them.

EXPERIMENT-002 adds these explicit invalidity reasons to BUILD-005:

- `reference_unavailable`;
- `insufficient_predictor_coverage`;
- `invalid_spread`.

Existing BUILD-005 reasons remain in force, including SIG trust/staleness, external staleness,
data gaps, missing executable quotes and dataset end.

## Horizons and response curves

Default horizons are:

```text
1s
2s
5s
10s
30s
60s
300s
```

The original BUILD-005 horizons are a subset and remain fully supported.

For BUY-YES:

```text
entry = decision ask
future executable exit = future bid
gross = future_bid - entry_ask
```

For SELL-YES:

```text
entry = decision bid
future executable cover = future ask
gross = entry_bid - future_ask
```

The output also records future bid/ask, midpoint change and logit-midpoint change as diagnostics.

`response_curve()` groups results by experiment, signal direction, horizon and regime and reports
valid/invalid counts plus executable markout statistics.

## Costs

Optional explicit cost inputs are:

- `fee_per_share`;
- `latency_adjustment` placeholder;
- `depth_cost` callback.

If no explicit cost input is supplied, `net_markout` is `None`. Gross results are not relabeled
as net profitability.

## Regimes

Configuration may provide explicit event windows. A decision inside a window is tagged `EVENT`;
all others are `NORMAL`. There is no inferred regime detector in this ticket.

## Statistical discipline

`split_relationship_observations()` uses the BUILD-005 chronological boundaries and never
shuffles observations.

Summaries optionally report a deterministic moving-block bootstrap interval for mean executable
markout when there are enough observations. This is a lightweight dependence-aware uncertainty
diagnostic, not a claim that event observations are iid.

No Sharpe ratio is produced.

## Serialization

`serialize_relationship_observations()` sorts its own input before writing stable JSON records,
so determinism is an API property rather than a caller-order assumption. Financial values are
serialized as decimal strings and observations are deterministically ordered by decision time,
experiment, target, horizon and direction.

Large empirical result sets should stay outside the repository. Compact JSON/CSV summaries are
appropriate repository evidence; Parquet can be used by downstream data jobs when that dependency
is available.

## Synthetic acceptance coverage

The deterministic regression suite covers:

- external move -> delayed target repricing;
- no borrowing a target update from after a requested horizon;
- BUY-YES and SELL-YES directionality;
- continuous-event de-duplication;
- rich-target RV convergence;
- LOO-PRICE vs LOO-FAMILY leakage separation;
- stale-reference invalidity;
- multi-reference equal-weight aggregation;
- complement direction;
- event regime tagging;
- byte-identical serialization across input ordering.

The synthetic fixtures prove mechanics only. They do not establish empirical edge.

## Data gates

Immediately runnable once historical or live capture provides explicit instrument mappings:

- LEADLAG response curves;
- RV residual convergence;
- LOO-PRICE;
- LOO-FAMILY;
- NORMAL vs EVENT comparisons;
- gross executable markouts and explicitly-costed net markouts.

Still data-gated:

- any claim of predictive edge;
- measured latency adjustment;
- production parameter choices;
- production trading decisions.
