# SOL — EXPERIMENT-004C Independent Hypothesis Set

## Provenance

This artifact is the independent Sol hypothesis-generation lane for EXPERIMENT-004C.

It is intentionally isolated from Opus and Astra.

Git PR base: `main@3601bea0e5df9018377da7b6df5f302f774c89f1`  
004B evidence base: `experiment/004b-master-integration@5b49d2ad13ea538fe9222a41643af7cf7d477c86`  
Sol branch: `experiment/004c-sol-hypothesis-generation`

The Sol lane was generated independently from the repaired 004B integration evidence while remaining isolated from Opus and Astra. The PR is based on `main` so it contains only the Sol research artifact; EXPERIMENT-004B itself is tracked separately in PR #30.

Frozen 004B empirical implementation: `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`  
Frozen 004B discovery-spec SHA: `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`

004B discovery used Hungary 2026 and Peru first round only; Colombia first round, Peru runoff and Colombia runoff were sealed from 004B.

## Core interpretation

004B does **not** justify selecting a historical "leader" market. Its strongest result is system-level: related election markets exhibit non-random directed price structure, but individual leader/follower identity is not established.

At the canonical 30s slice, directed price structure exceeded both timing-shift and block-permutation nulls in HUN PRE, Peru PRE and Peru ACTIVE. HUN ACTIVE did not survive the directed nulls.

The most useful additional pattern is topology concentration. In Peru ACTIVE at 30s, same-family price→future-price responses had mean |correlation| ≈ 0.101 versus ≈ 0.021 cross-family, with mean signed correlation ≈ -0.029 versus ≈ -0.002. The same-family magnitude remained larger at 60s, 120s and 300s.
For competitive outcome families, the negative sign has a natural interpretation: information incorporated into one outcome can redistribute probability mass away from competing outcomes. This motivates a topology-defined family mechanism, not a best-pair search.

Two 004B caveats are binding in 004C:
- `ofi_imbalance` is depth-imbalance change, not aggressor OFI.
- v3 semantic-residual half-lives are not exact validated time constants because missing observations were compressed before persistence estimation.

## External-methodology priors

Recent Polymarket microstructure work by Philipp Dubach shows that trade direction inferred from the public feed agrees with on-chain ground truth only about 59% of the time. Current DATA-001 fields therefore do not support a clean aggressor-flow hypothesis.

OpenMarket's synchronized Binance/Polymarket work is a useful warning: measurable response latency can coexist with a model that fails to beat the current Polymarket midpoint out of sample and with negative simulated economics. A lag is not automatically an executable edge.

Ostrovsky-style prediction-market theory also supports separating genuine payoff/topology structure from loose semantic relatedness: information-aggregation guarantees depend on the security structure and assumptions, so semantic similarity alone should not be treated as a probability identity.

Asynchronous-market literature provides a further control: different update rates can create apparent lead-lag. Every serious 004C propagation hypothesis must therefore control for the target's own recent move, common event movement and quote staleness.

References consulted:
- Philipp D. Dubach (2026), *The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book*, arXiv:2604.24366.
- Gregory Young / OpenMarket (2026), synchronized Binance–Polymarket research and public replication repository.
- Michael Ostrovsky (2012), *Information Aggregation in Dynamic Markets With Strategic Traders*, Econometrica.
- Prediction-market ambiguity/separability literature and conventional high-frequency lead-lag work on asynchronous trading.

# PRIMARY HISTORICAL HYPOTHESES

## SOL-H1 — Competitive-Family Probability Redistribution

### Mechanism
Within a verified family of competing outcomes, information incorporated first into some members should subsequently be reflected in the remaining members.
### Predictor
For target market `i`, construct an equal-weight, leave-target-out aggregate 30s price shock from the other verified competing outcomes. No historical edge selection is permitted.

Where the family is genuinely exhaustive, an exact probability-mass interpretation is allowed. Where it is only mutually exclusive/non-exhaustive, retain the competition sign but do not force a sum-to-one identity.

### Primary prediction
After orienting the family consistently, the raw competitor aggregate should predict target repricing in the opposite direction: `beta_competitor < 0`.

### Primary lane
- ACTIVE_RESULTS primary.
- 30s resolution and 30s forward response primary.
- 60/120/300s robustness only.
- PRE_ELECTION is a separate preregistered claim and cannot borrow evidence from ACTIVE.

### Required baselines / controls
The challenger must beat:
- target's own recent price change;
- event-wide average price change;
- unrelated-family aggregate.

Negative controls:
- matched unrelated market family;
- permuted family membership;
- future-reversed source;
- non-related aggregate.

### Falsifier
Reject if the family aggregate contributes no incremental information over target autoregression/common-event movement or if the expected competition orientation fails.

### Why this is not EXPERIMENT-003 rerun
EXPERIMENT-003 tested generic related-market lead-lag and found no FDR-supported incremental improvement. H1 is narrower: regime-specific, topology-defined, family-aggregated and sign-constrained, with no pair selection.

**Priority: PRIMARY**

## SOL-H2 — Relative-Staleness Information Diffusion

### Mechanism
Related markets update asynchronously. A fresh/active family member should be more informative about a related target when that target is relatively stale.
### Model
Conceptually:
`future_target_move ~ oriented_family_shock + relative_staleness + shock×staleness + target_own_move + event_common_move`

Define relative staleness continuously from target quote age versus the contemporaneous family quote-age distribution. Do not search a threshold.

### Prediction
The oriented family-shock effect should strengthen as target relative staleness increases.

### Negative controls
- quote age shuffled within event/regime;
- target quote age without family shock;
- unrelated-family shock × target staleness;
- fresh-target subsample diagnostic.

### Falsifier
If staleness alone explains the apparent lead-lag and the related-market shock adds no incremental information, reject the information-diffusion mechanism.

**Priority: PRIMARY**

## SOL-H3 — Activity-Confirmed Family Diffusion

### Mechanism
A family price shock accompanied by genuine contemporaneous market activity should carry more information than an isolated quote move.

### Primary activity variable
Use unsigned `fill_count`.

Secondary robustness may use BBO update intensity.

Do not use `source_side` as aggressor direction. Do not call depth-imbalance change OFI. Do not use venue-trade activity because 004B discovery panels had zero venue-trade activity.

### Predictor
`ORIENTED_FAMILY_PRICE_SHOCK × SOURCE_FAMILY_FILL_ACTIVITY`

Use activity continuously; no tuned high-activity threshold.

### Required ablations
- family price shock alone;
- activity alone;
- target autoregression;
- event-wide activity.

Use depth-update intensity as a **negative-control-like archive-cadence diagnostic**, because 004B showed unusually broad depth-update synchronization that may partly reflect capture cadence.

### Falsifier
No incremental interaction after the price-only baseline, or comparable/stronger effects from archive-cadence controls.

**Priority: PRIMARY**
# MANDATORY LIVE BASELINES

## SOL-H4 — Exact Polymarket → SIG Price Discovery

The accepted mapping contains 140 EXACT and 4 NEAR one-to-one SIG↔Polymarket mappings.

Hypothesis: an as-of Polymarket price innovation contains incremental information about the subsequent SIG price of the mapped contract.

Primary challenger must beat current SIG price, SIG own recent move and SIG state/spread. Freeze 1s primary, 5s/30s robustness before the live test.

Negative controls:
- SIG→Polymarket reverse direction;
- wrong but category-matched Polymarket contract;
- shuffled/delayed Polymarket innovation.

No trading claim without executable SIG prices and realistic latency.

**Priority: MANDATORY LIVE BASELINE**

## SOL-H5 — Derived Polymarket Basket → SIG Fair Value

The accepted mapping contains 87 DERIVED SIG markets represented by reviewed unions of mutually exclusive Polymarket margin buckets.

Construct synthetic Polymarket bid/ask bounds from the frozen components; midpoint is diagnostic only.

Hypothesis: changes in the mapped synthetic basket contain incremental information for the corresponding SIG contract.

Negative controls:
- wrong state;
- wrong party basket where not mechanically complementary;
- incomplete component set;
- shuffled component membership.

Only accepted DERIVED mappings may participate. No result-dependent basket construction.

**Priority: MANDATORY LIVE BASELINE**

# SECONDARY HYPOTHESES

## SOL-H6 — Cross-Institution Same-Party Propagation

004B contains reviewed semantic links between upper- and lower-chamber most-seats markets for the same party. Peru ACTIVE's limited estimable responses were predominantly positive, while PRE did not show a comparable stable pattern.

Test only explicitly reviewed same-party relationships. Treat this as shared election-state information, not a probability identity.

**Priority: SECONDARY**

## SOL-H7 — Unsigned Activity → Future Movement / Refresh

Hypothesis: family fill/BBO activity shocks predict future absolute target price movement and/or probability of target quote refresh, without making a directional claim.

This is primarily a volatility/attention or risk-gating hypothesis.

**Priority: SECONDARY / RISK-GATING**
# DO NOT CARRY FORWARD

Reject as standalone Alpha Battery II mechanisms:
- generic pairwise lead-lag;
- historical "best leader" edge selection;
- semantic residual mean reversion;
- PCA/low-rank residual alpha as a primary lane;
- aggressor OFI from current data;
- participant-flow rerun without genuinely new evidence;
- pooled PRE/ACTIVE parameters.

Reasoning:
- EXPERIMENT-003 already falsified broad lead-lag as an incremental generic model.
- 004B individual response cells were not cell-level FDR discoveries.
- 004B found no mechanical residual edges and no stable semantic-gap convergence.
- Peru PRE was multi-factor-like; Peru ACTIVE had no complete all-market factor panel; Hungary was too small.
- trade direction is not supported cleanly.
- PRE↔ACTIVE pairwise stability was weak.

# HOLDOUT / PRIOR-EXPOSURE NOTE

The 004B-sealed events are not globally pristine historical holdouts.

EXPERIMENT-003 previously used:
- Colombia first round;
- Colombia runoff;
- Peru runoff;
in one or more alpha-family evaluations.

004C should therefore materialize a `prior_exposure_manifest.json` and label these:
`004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`

They are still valid as a locked historical challenge set because the new 004C hypotheses are frozen before their 004C outcomes are inspected, but they should not be described as globally untouched confirmation.

The genuinely clean OOS proof is prospective live/shadow data collected after the 004C protocol is frozen.

# SUGGESTED 004C TESTING ARCHITECTURE

Historical primary:
- 30s resolution;
- 30s horizon primary;
- 60/120/300s robustness;
- robustness cannot rescue a failed primary.

For H1–H3, baseline includes target own recent change and event-wide common movement.

Apply BH/FDR separately to PRE_ELECTION and ACTIVE_RESULTS.

Only after predictive evidence, nulls/FDR, negative controls and stability should executable crossing markouts be evaluated. Do not use execution performance to select the specification retroactively.

# SOL DISPOSITION

Carry to MASTER preregistration:
- PRIMARY: SOL-H1 competitive-family redistribution; SOL-H2 relative-staleness diffusion; SOL-H3 activity-confirmed diffusion.
- MANDATORY LIVE BASELINES: SOL-H4 exact PM→SIG price discovery; SOL-H5 derived-basket PM→SIG fair value.
- SECONDARY: SOL-H6 cross-institution same-party propagation; SOL-H7 unsigned activity→future movement/refresh.

Central mechanism:
> Information enters an economically coherent market family unevenly; active/fresh members move first, and probability mass subsequently redistributes into related stale members.

This mechanism best reconciles the non-random directed structure, strong family-local concentration, negative within competitive families, weak PRE↔ACTIVE edge stability, lack of semantic residual mean reversion and lack of a dominant common factor.

**SOL 004C HYPOTHESIS SET READY FOR MASTER PREREGISTRATION**