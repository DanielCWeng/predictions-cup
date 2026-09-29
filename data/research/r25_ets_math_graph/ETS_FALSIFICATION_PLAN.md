# ETS falsification plan

## Preregistration boundary

This plan is written before accessing price/fill history, order books, or resolved election outcomes. The current inputs are frozen candidate membership, accepted crosswalk annotations, and Gamma contract descriptions. Hash and preserve this plan with the source code before later history is opened. Do not pick events, features, windows, or thresholds after viewing outcome history.

The first study is a mapping and information-flow test, not a claim of profit. Polymarket is an external information/reference venue; SIG is the execution venue. Any venue discrepancy is not arbitrage unless an offset can be executed and held under matched settlement rules.

## Test A: structural rule and partition audit

**Question:** Do the accepted exact/derived sources and v2 group descriptions encode the same settlement event and a mathematically valid partition?

**Pre-outcome checks:**

1. Recompute the description SHA-256 and exclude a market if it differs from the frozen inventory until reviewed.
2. Verify office, jurisdiction, election round, party/candidate definition, vote/count universe, boundary convention, tie, recount, runoff, cutoff, and resolution source for each edge called exact.
3. For any sum, prove pairwise disjointness and event coverage by checking all Gamma event children, including off-scope siblings.
4. Recompute the categorical observation matrix and exact rational rank. Check all probability intervals have a feasible nonnegative simplex solution; infeasible aligned intervals falsify the data/partition assumptions.
5. Require D+R+Other mass consistency. Never infer `Other=0` from a binary API outcome or `negRisk` flag.

Any failure downgrades the edge to model-dependent/predictive-only or rejects it, with reason recorded before viewing outcomes.

## Test B: minimum Georgia pilot

**Universe:** four Georgia Senate×Governor joint cells `3729337`–`3729340`, plus the event's explicit Other outcome. Compare against accepted out-of-v2 Georgia Governor margin partitions (`3365590`–`3365594`, `3365585`–`3365589`) and Senate winner markets `630692`/`630693`, mapped to SIG anchors 166/167/258/259 (exchange IDs 855/856/947/948). Keep baseline-source acquisition separate from the 1,279-row v2 specification.

**Primary structural estimand:** D Governor and D Senate marginal probability intervals implied by the joint surface. For example, `p_DD+p_DR <= P(G_D) <= p_DD+p_DR+p_O`. Report the corresponding Senate bound and the coarsened Other residual. Do not force a D/R-only 2×2 table.

**Later empirical question:** With synchronized and non-stale fill-side quotes, does the Polymarket joint surface provide a feasible, better-calibrated constraint on the later SIG quote than the frozen accepted exact/derived baseline alone? Freeze quote statistic, stale cutoff, latency window, fees, interval loss, calibration metric, holdout period, and exclusion criteria before acquiring history.

**Failure conditions:** rule mismatch; description hash drift; non-disjoint cells; infeasible constraints after quote uncertainty; LP bounds no narrower than baseline within a prespecified tolerance; no incremental held-out calibration over the baseline; or effect disappearing when the full `Other` outcome and stale-quote filters are included. No direction of effect or result is assumed.

## Test C: count and joint component falsifiers

- **U.S. Senate × House counts:** test whether the 13 grouped prices form a feasible coarsening over all 16 fine cells. Never score a point control probability from the 50–52 or 208–222 bucket. A model that claims exact control from these bins is falsified by construction.
- **Republican governorship count:** check grouped probabilities and calculate only estimands exactly supported by bucket unions (for example `P(K>=26)`). Test any state-level decomposition against assumption-free LP bounds; if it depends on independence, label and compare that model separately.
- **National House margin:** confirm selected categories plus the explicit Other sibling cover the signed-margin domain under written boundaries. Failure to align the House-wide result definition invalidates a structural join to a district target.
- **Same-race margin families:** test coverage and residual outcomes market-by-market before summing. Missing or overlapping intervals, including a hidden tie bucket, falsify the claimed exact party partition.

## Test D: predictive-only negative controls and holdout

Treat local turnout, voter groups, county outcomes, state-chamber control, within-margin, cross-race comparison, and pre-election chamber control as candidate predictors only. Register a baseline SIG/crosswalk model and one feature family at a time. Split by time and hold out whole event/race clusters so cells from one event cannot leak across partitions. Use outcome-blind feature construction; only open resolution outcomes after the plan, joins, and exclusions are frozen.

Negative controls include: (1) permuted margin-bin labels; (2) shuffled cross-race identity; (3) turnout features matched to the wrong state/office; (4) the Florida regular-vs-special Senate pair; and (5) replacing a four-cell D/R joint surface with an erroneous D/R-complement assumption. A valid workflow should reject these joins or show no stable held-out improvement. Report calibration and interval coverage; do not select the best feature after inspecting the holdout.

## Reporting and stop rules

Report every registered component, including null/negative results, description drift, empty ID responses, exclusions, missing source rows, and event-cluster holdout coverage. Do not expand to off-scope Gamma siblings without a separate universe review. Stop a structural claim if rules cannot be aligned, a required outcome category is absent, or the probability constraints are infeasible. Stop empirical feature claims if they fail the frozen holdout criteria. No trading recommendation follows from this research test.
