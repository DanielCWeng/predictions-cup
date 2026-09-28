# EXPERIMENT-005F — Rich Microstructure / Liquidity / Event-Time Atlas

## Status

**INITIAL DESIGN FREEZE — outcome-blind with respect to detailed prior microstructure cells.**

Starting commit: `ba938bedcf63f562be8b26c9502e828391123867`  
Branch: `experiment/005f-microstructure-liquidity-atlas`  
Namespace: `005f_microstructure_liquidity_atlas`

This freeze was written after reading the permitted 005 prior-work boundary, DATA-001 semantics,
PMXT V1/V2 timestamp/capture limitations, EXPERIMENT-004A regime definitions, and MATHS_LEDGER
definitions. Detailed prior result cells, winning horizons, ranked markets, coefficients, and
post-hoc candidate lists were intentionally not inspected before this freeze.

## Scientific question

Given only information observable by time `t`, which book/trade-state variables predict the next
price, quote, liquidity, volatility, or adverse-selection state beyond own-price/history baselines?

005F is discovery. It does not identify participant skill or maker/taker economics.

## Hard evidence constraints

- Order all historical inputs by DATA-001 `observed_at`; never order research by venue
  `source_timestamp`.
- V1 has archive receive time only. V2 venue time is provenance, not the research clock.
- Same-millisecond order is unknowable. No within-tie causal ordering is manufactured.
- No queue position, exact cancel timing, passive fill probability, hidden liquidity, or
  same-millisecond event sequencing is claimed.
- Raw record arrival and economic book change are separate processes.
- Crossed/invalid states are counted and flagged, not silently repaired.
- If full-depth reconstruction cannot be justified from source semantics, depth-dependent objects
  that require it are marked `UNTESTABLE_WITH_CURRENT_HISTORICAL_DATA`.

## Canonical book-state reconstruction

The primary unit is a binary market condition canonicalized to YES probability.

For the YES token:
- `yes_bid = token_bid`
- `yes_ask = token_ask`.

For the NO token, when used as an implied YES observation:
- `yes_bid = 1 - no_ask`
- `yes_ask = 1 - no_bid`.

YES- and NO-derived observations are never averaged merely because they share a condition. The
source token remains in provenance; paired-token disagreement is itself a diagnostic, not an
extra free observation.

For each token, process observable-time groups. A group is:
- `RECORD_ARRIVED` whenever a valid archive row arrived;
- `BOOK_ECONOMICALLY_CHANGED` only when post-group BBO or reconstructible depth differs from the
  last valid economic state.

Repeated identical BBO states are deduplicated from the economic-change series but retained in
capture-rate diagnostics.

A same-timestamp group is **ambiguous** for BBO sequencing when its rows imply more than one
distinct post-change BBO. It is excluded from sequential OFI / next-event direction calculations.
If multiple rows for the same side/price imply conflicting sizes within one tie, depth
reconstruction fails closed until the next authoritative depth snapshot.

State flags carried forward:
`genuine_bid_change`, `genuine_ask_change`, `genuine_bbo_change`, `spread_change`,
`depth_change`, `invalid_or_crossed`, `same_timestamp_ambiguity`, `continuity_gap`,
`quote_age_s`, `bid_age_s`, `ask_age_s`, `depth_snapshot_age_s`, `raw_record_age_s`,
`source_version`, and capture-cadence summaries.

A continuity gap flag uses the accepted DATA-001 material-gap threshold of 300 seconds. It is a
diagnostic, not automatic proof of feed failure or a reason to backfill state.

## Frozen feature atlas

All rolling features use trailing information only. Clock windows are
`{5, 15, 30, 60, 120, 300}` seconds unless a smaller set is named below.

### A. Price / BBO / spread

- midpoint and clipped logit-midpoint;
- bid, ask, probability-space spread;
- logit/odds-space spread;
- distance of midpoint from 0.5;
- one-sided BBO movement indicators;
- quote/bid/ask ages;
- time since last genuine BBO change;
- genuine BBO-change counts and rates over 5/15/30/60/120/300s;
- raw-record arrival counts/rates over the same windows;
- repeated-identical-state arrival counts/rates;
- genuine/raw update-rate ratio;
- own midpoint and logit-mid returns over 1/5/15/30/60/120/300s.

### B. Depth

Where full-depth state is valid:
- best-bid size, best-ask size, total top size, top depth imbalance;
- side depth and total depth within 1c, 2c, and 5c of midpoint;
- depth imbalance within 1c/2c/5c;
- concentration `D_1c / D_5c`;
- depth slope `(D_5c - D_1c) / 0.04`;
- changes in bid/ask/total depth at 1c/2c/5c;
- log depth shocks using `log1p(D_t)-log1p(D_prev)`;
- depth snapshot age and explicit depth-missing/stale flags;
- side-specific depth-to-consume / VWAP impact for `Q={10,50,100,500}` shares when supported.

### C. Proper top-of-book OFI

The frozen Cont-style event contribution is

`e_n = 1[b_n>=b_{n-1}]q^b_n - 1[b_n<=b_{n-1}]q^b_{n-1}
      - 1[a_n<=a_{n-1}]q^a_n + 1[a_n>=a_{n-1}]q^a_{n-1}`.

This is computed only when best-price quantities are reconstructible on both sides and the
observable-time transition is not ambiguous.

Derived features:
- instantaneous OFI;
- rolling OFI sums over 5/15/30/60s;
- depth-normalized OFI using contemporaneous `q_bid + q_ask`;
- EWMA OFI with 5s and 30s half-lives;
- OFI sign;
- OFI × spread and OFI × quote-age interactions.

The old `ΔQ_bid-ΔQ_ask` quantity is retained only as a labelled primitive baseline if feasible.

### D. Microprice / learned micro-FV

Primitive microprice baseline:
`m_micro=(ask*q_bid + bid*q_ask)/(q_bid+q_ask)`.

It is reported as algebraically related to imbalance and never counted as independent information
when equivalent.

Learned micro-FV predicts `E[mid_{t+h}|B_t]`.
Candidate feature blocks are frozen as:
1. own-price/history only;
2. BBO/spread/age;
3. + depth;
4. + OFI;
5. + trade/activity;
6. all observable microstructure.

### E. Trade / activity state

No participant identity and no maker/taker inference.

PMXT V2 venue trades and on-chain fills remain separate provenance families. On-chain fills have
one-second block time; same-second ordering is not used.

Features where supported:
- count, total quantity/value, mean/median/max size;
- inter-arrival median and coefficient of variation;
- burstiness `(std_dt-mean_dt)/(std_dt+mean_dt)` when at least 3 intervals exist;
- activity acceleration: 15s rate minus trailing 60s rate;
- unsigned recent trade-price-to-prior-mid displacement;
- trade-event rate / genuine-quote-event rate;
- explicit source-availability and missingness indicators.

No trade sign is used unless its aggressor semantics are independently established before outcome
inspection. Otherwise signed-trade features are `UNTESTABLE_WITH_CURRENT_HISTORICAL_DATA`.

### F. Liquidity shock / resilience

Continuous shock variables:
- spread change and log spread ratio;
- 1c/2c/5c depth log change;
- side-specific depth loss/addition;
- top-size loss/addition.

A **large observable depth shock** is a >=50% drop in valid 2c total depth between two valid
reconstructible states. This threshold is fixed before outcome inspection.

For such shocks report:
- `R_h = D_2c(t+h)/D_2c(t^-)`;
- recovery fraction `(D_2c(t+h)-D_post)/(D_pre-D_post)`, clipped only for presentation;
- replenishment half-life = first observable time recovery fraction reaches 0.5;
- censoring / no-recovery indicator.

No depth drop is called a cancellation or taker trade without direct evidence.

### G. Volatility / event risk

- clock-time realised variance of logit-mid changes over 15/60/300s;
- event-time realised variance over the last 5 and 20 genuine BBO changes;
- mean absolute probability innovation over the same windows;
- activity-normalized volatility
  `sqrt(sum(Δz^2)/N_genuine)` over 15/60/300s when `N_genuine>0`;
- volatility-of-volatility: trailing std of 15s realised-vol estimates over 300s;
- local jump-state indicator using the TRAIN-only 95th percentile of absolute logit innovations,
  frozen separately by claim regime and then applied unchanged to DEV/HOLDOUT;
- PRE_ELECTION / ACTIVE_RESULTS regime indicator and time to/from factual regime anchor.

## Frozen target atlas

### Clock-time horizons

`H_clock={1,5,15,30,60,120,300}` seconds.

At `t+h`, clock-time state means the last valid observable state at or before `t+h`; no future
interpolation is used. Targets crossing a documented regime boundary are not used for that regime.

Price:
- midpoint change;
- logit-mid change;
- sign of non-zero midpoint change.

Volatility:
- absolute midpoint/logit movement;
- realised future variance over `(t,t+h]`;
- jump occurrence using the frozen TRAIN threshold.

Liquidity:
- future spread;
- spread-widening indicator;
- future 1c/2c/5c depth where valid;
- >=50% 2c-depth withdrawal indicator;
- any genuine BBO update within `h`.

Event-conditioned:
- post-depth-shock midpoint movement and volatility;
- post-spread-widening midpoint movement and recovery;
- fill/trade-conditioned absolute future midpoint movement where timestamp alignment supports it.

### Event-time horizons

`H_event={1,2,5,10}` genuine BBO changes.

Targets:
- midpoint/logit-mid change after k genuine BBO changes;
- next genuine price-change direction;
- cumulative absolute movement over the next k changes;
- spread/depth state after k changes where depth is valid.

Depth-shock and trade event-time panels are reported separately from the BBO-event panel; they are
not interleaved into a fake exact event sequence across ambiguous same-time observations.

## Frozen split policy

Use accepted EXPERIMENT-004A claim regimes. PRE_ELECTION and ACTIVE_RESULTS are never pooled for
selection.

Within each factual `(event, claim_regime)` window, split by wall-clock time:
- TRAIN = first 60%;
- DEV = next 20%;
- HOLDOUT = final 20%.

Split boundaries are event-level, not token-level, so late-starting tokens do not receive a
different future partition.

Purge rows whose target extends across TRAIN→DEV or DEV→HOLDOUT. For event-time labels, purge the
last 10 eligible economic events before each split boundary per token. For clock labels, purge the
maximum 300-second forward horizon.

HOLDOUT is not read until the DEV selection and immutable pre-HOLDOUT freeze are committed.

## Frozen model ladder

For each target family, use the simplest applicable models first:

0. persistence/no-change;
1. own-price/history baseline;
2. single-feature linear or logistic model;
3. multivariate OLS/logistic;
4. Ridge with standardized features and `alpha in {0.1,1,10}`;
5. ElasticNet with `alpha in {0.001,0.01,0.1}`, `l1_ratio in {0.1,0.5,0.9}`;
6. learned micro-FV using the frozen feature blocks above;
7. small tree challenger: gradient boosting / XGBoost with depth in `{2,3}`, learning rate
   `{0.03,0.1}`, <=300 trees, no unrestricted tuning.

Hyperparameters are selected on DEV only and then frozen. Hawkes is not in the default ladder. It
may be activated only if simple count/EWMA/hazard residual diagnostics on TRAIN/DEV show material
remaining serial structure; activation must be recorded before HOLDOUT.

Update-hazard models:
- empirical base hazard;
- logistic discrete-time hazard;
- optional survival-style challenger only if support is adequate.

## Metrics

Regression:
- MAE, RMSE, out-of-sample R2;
- improvement versus persistence and own-price/history baseline;
- Spearman rank correlation as a secondary diagnostic.

Classification:
- Brier score, log loss, ROC AUC where both classes have support;
- calibration by decile on DEV/HOLDOUT.

Learned micro-FV:
- future-mid MAE/RMSE versus current midpoint and own-history baseline;
- directional hit rate is secondary and never substitutes for forecast error.

Liquidity/volatility models are allowed to promote even when directional-return prediction fails.

## Screening and multiplicity

TRAIN is broad discovery; DEV is model/feature/horizon selection.

Related univariate screens are grouped into predeclared families:
`BBO_AGE`, `DEPTH`, `OFI`, `MICROPRICE`, `TRADE_ACTIVITY`,
`LIQUIDITY_SHOCK`, `VOLATILITY`, `CAPTURE_PROCESS`.

Benjamini-Hochberg FDR uses `q=0.10` within each family/target class where inferential p-values are
used. FDR is not a substitute for chronological DEV/HOLDOUT.

A DEV candidate must show:
- positive incremental performance versus the own-history baseline on its target metric;
- same-direction incremental effect in at least 3 temporal blocks;
- support from at least 10 markets for a regime-specific claim, unless the target itself is
  structurally rarer (e.g. large depth shocks), in which case the lower support is reported and
  the claim cannot exceed `LOW_SUPPORT_CANDIDATE`.

Cross-election consistency is desirable but not mandatory for a
`REGIME_SPECIFIC_CANDIDATE`. No individual market can promote a feature.

## Dependence / uncertainty

Never use tick rows as iid N.

Always report observations, markets, temporal blocks, events, and election families.

Primary uncertainty is a moving/block bootstrap over contiguous time blocks, with block length
chosen on TRAIN from the larger of 5 minutes or the first lag where target autocorrelation falls
below 0.1, capped at 60 minutes. The chosen block length is frozen before HOLDOUT. Leave-market-out
and leave-event-out stability are separate falsifications, not iid replications.

## Capture-placebo tests

Every timing/intensity candidate is compared with:
- raw record arrival rate;
- repeated-identical-state arrival rate;
- depth snapshot cadence;
- raw-record age;
- 300s continuity-gap flag;
- PMXT source version.

If economic-update intensity loses incremental DEV/HOLDOUT value once capture-process controls are
included, it is labelled capture-confounded, not microstructure alpha.

## Predeclared falsifications for promoted DEV candidates

- delayed-feature placebo (feature shifted backward by one maximum feature window);
- circular/block time shift within market/regime;
- raw-record vs genuine-change comparison;
- feature-sign permutation where direction is meaningful;
- depth/activity-matched null where applicable;
- leave-market-out;
- leave-event-out;
- leave-family-out;
- high-activity-market removal;
- PMXT source-version stratification;
- continuity-gap exclusion;
- alternate quote-freshness rule: exclude states with raw-record age >300s.

## MATHS_LEDGER tractability boundary

Potentially testable in 005F with current data:
- M-064/065 proper OFI / depth-normalized OFI;
- M-073/074 primitive microprice / learned micro-FV;
- M-084 probability innovation variance;
- M-098/099 static depth / price-impact curves;
- M-100/101 observable resilience / replenishment where reconstruction is valid;
- M-103 activity-normalized volatility;
- M-126 Hawkes only as a conditional challenger;
- M-127 signal half-life after a candidate signal exists.

Not claimed by 005F without stronger role/queue evidence:
- M-059/060 signed maker fill/information markouts;
- M-061 executable maker liquidation markout;
- M-063 maker bad-markout probability;
- M-075/076 passive fill hazard / queue depletion;
- M-079 fill-conditioned passive quote value;
- queue-loss component of M-082 quote-churn economics.

These are marked `UNTESTABLE_WITH_CURRENT_HISTORICAL_DATA` here rather than approximated under
their original names.

## Holdout gate

Before reading HOLDOUT, commit an immutable file that contains:
- selected feature blocks and exact columns;
- selected target(s), horizons, and claim regime(s);
- fitted transform/scaler rules;
- final model class and hyperparameters;
- support thresholds;
- bootstrap block length;
- exact candidate list and claim scope;
- hashes of code, DATA-001 manifest, event-time regime artefacts, and TRAIN/DEV outputs.

HOLDOUT is then run once. Any post-HOLDOUT amendment creates a new version and cannot be described
as confirmatory.

## Required outputs

The implementation must produce:
- book-state/capture audit;
- genuine-change reconstruction specification and diagnostics;
- feature atlas;
- target atlas;
- TRAIN discovery report;
- DEV selection report;
- immutable pre-HOLDOUT freeze;
- HOLDOUT report;
- capture-placebo report;
- falsification report;
- exact Kaggle provenance;
- `MASTER_HANDOFF_005F.md`.

No order placement is part of this experiment.
