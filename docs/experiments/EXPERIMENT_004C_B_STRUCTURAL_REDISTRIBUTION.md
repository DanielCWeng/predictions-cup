# EXPERIMENT-004C-B — Structural / Competitive-Family Redistribution

## Scientific boundary

This battery tests family-level probability redistribution, not generic pairwise lead-lag.

Primary claim: after projecting a frozen mechanically valid family state `p(t)` onto its feasible set `C`, future prices should move against the structural residual `r(t)=p(t)-Projection_C[p(t)]`.

The family registry is frozen from wording, settlement rules, accepted identity metadata and explicit economic logic before any 004C-B challenge outcomes are opened.

## Frozen projection rules

- Full exhaustive partition: unweighted Euclidean projection to the probability simplex.
- Usability-limited subset of an exhaustive parent: unweighted Euclidean projection to `sum(p)<=1`; never renormalize to one.
- Nested at-least thresholds: unweighted isotonic projection enforcing nonincreasing probability with increasing threshold.
- Semantic-only competitors: no feasibility projection and no probability-conservation language.

All projections are fixed before challenge access. Liquidity weighting is intentionally excluded from the primary projection so performance cannot choose the weighting rule.

## Primary estimand

At the frozen 30-second panel and 30-second future horizon:

`future_delta_p_i = alpha + beta_structural * (-r_i) + frozen_controls + error`

The economically meaningful effect threshold is `beta_structural >= 0.10`, corresponding to at least 10% correction of the contemporaneous inconsistency over 30 seconds.

PRE_ELECTION and ACTIVE_RESULTS are separate claims and separate FDR lanes.

## Controls

Controls are target own prior-30s move, target level, broad event prior-30s movement excluding the entire tested family, inside spread and quote age. The frozen freshness ceiling is 300 seconds, matching the 004B as-of discipline.

## Soft competitive mechanism

For semantic competitive family target `i`:

`S_-i(t) = -mean(sibling price changes over the prior 30s)`.

The target and the entire target family are excluded from the broad-event control. The signal must contain no target information directly or indirectly. A positive coefficient predicts opposite-direction target adjustment. This is explicitly a soft competitive effect, not probability conservation.

## Frozen falsification stack

Each eligible family is tested against 1,000 deterministic time circular shifts and 1,000 ten-minute block permutations. Membership randomisation preserves family size and matches quote-update activity quartile where feasible. Wrong-family groups and away-from-feasible-set direction are negative controls.

Benjamini-Hochberg discovery control is `q <= 0.01`. Hard mechanical, soft competitive, conditional/nested and robustness-horizon tests are separate FDR families. Unavailable preregistered cells remain recorded.

## Evidence scopes

Discovery: Hungary election and Peru first round.

Locked challenge: Colombia first round, Peru runoff and Colombia runoff. All three carry the label `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`; they are not globally pristine holdouts.

004A/004A.2 chronology, half-open regime windows and no-cross-boundary labels remain authoritative.

## Coverage consequence discovered before empirical testing

The semantic registry contains nine mechanically proven parent structures, but no full hard partition is 004A.2-usable in any primary regime. Historical hard tests therefore operate only on valid mutually-exclusive nonexhaustive subsets when at least two members are frozen usable. The constraint is `sum(p)<=1`; missing members are never imputed.

This is a binding pre-result limitation, not a post-hoc design change.

## Promotion

`STRUCTURAL REDISTRIBUTION SUPPORTED` requires a hard 30s effect in the predicted direction, q<=0.01, beta>=0.10, survival of null/control checks, incremental information beyond frozen controls, and reasonable stability across at least two valid scopes.

`SOFT COMPETITIVE EFFECT ONLY` is used when only semantic competitive families pass their own frozen gate.

`NO STRUCTURAL EDGE` is used when adequately powered valid tests fail. `INCONCLUSIVE` is used when coverage, violation incidence, independent scopes or null construction are inadequate.

Robustness horizons cannot rescue failure at 30 seconds. Generic pairwise lead-lag cannot rescue this battery.
