# EXPERIMENT-005E — Participant Ecology / Behavioural Atlas

**Branch:** `experiment/005e-participant-ecology-atlas`  
**Base:** `main @ ba938bedcf63f562be8b26c9502e828391123867`  
**Namespace:** `005e_participant_ecology_atlas`  
**State:** pre-HOLDOUT protocol frozen before intentional inspection of prior result-ranked participant evidence. Protocol revision 2 records a transaction-shape correction made before empirical response inspection.

## Question

005E tests whether **past-observable participant state adds predictive information beyond generic market and activity state**. It is not a wallet leaderboard and it does not assume that recurrence, size, clustering, or historical markout implies informed trading.

The participant observation is one DATA-002 participant-side `OrderFilled` row. Known exchange contracts, adapters, fee/reward modules, protocol/system addresses and other confirmed infrastructure from the canonical registry are excluded from participant-skill/ecology claims. Unknown identity is **not** asserted to be human.

## Revision-2 transaction-shape amendment

DATA-002 can represent one matched transaction as one active aggregate order plus multiple passive maker-order rows. Therefore the economic market stream is reconstructed by `family + condition_id + tx_hash`: a valid group must contain exactly one active aggregate row and one or more passive rows. **Each passive row is one economic fill; the active row is audit-only and is never counted as another trade.** Active size and canonical YES-axis notional must reconcile to the passive legs within the implementation tolerance, otherwise the group fails closed and is reported.

This amendment was made before empirical response inspection. It corrects transaction semantics; it does not add or remove a predictive feature based on results.

## Frozen semantics

For a binary market, canonical YES price is `price` on a YES row and `1-price` on a NO row. Canonical participant YES pressure is +1 for YES BUY / NO SELL and -1 for YES SELL / NO BUY. This is owner-direction semantics only; it is not aggressor status.

Clock-time market state at horizon H is the last canonical economic-fill YES price observed at or before `t+H`, **provided at least one later economic fill occurs in `(t,t+H]`**. Otherwise the target is `NO_FUTURE_OBSERVATION`, not zero movement. Event-time targets use the 2nd and 10th subsequent economic fill.

## Split and holdout discipline

Within each family, timestamp-only 70% / 15% / 15% chronological cut points define TRAIN / DEV / HOLDOUT. Labels touching the next split are purged and a 300-second embargo is applied. TRAIN fits behavioural transforms and archetypes. DEV may choose only among the model/feature families already written into the machine-readable preregistration. HOLDOUT is opened once after the DEV freeze.

All eligible history is used to construct past-only participant state. Supervised fitting uses an outcome-independent deterministic hash sample capped at 750k observations per family; US_2024 must contribute at least 600k target rows when support exists.

## Participant feature atlas

The frozen atlas contains recurrence/lifetime state; market/event specialisation; cumulative size behaviour; canonical directional behaviour; session/timing behaviour; strictly matured historical markout; and lightweight participant↔market bipartite features. Expensive graph/community features may be omitted only if they are computationally pathological, with the omission recorded before outcome-based selection.

Historical markout uses a 300-second label and becomes eligible for a current score only after that label has fully matured. The running score is empirical-Bayes shrunk toward zero with λ=20. Unseen participants and unsupported sparse states map to neutral/default values.

## Behavioural archetypes

Archetypes are unsupervised and frozen independently of predictive performance:

`TRAIN StandardScaler -> PCA(6) -> MiniBatchKMeans(k=6)`.

The fit population is TRAIN participant end-state rows with at least 20 prior fills and two active days. DEV and HOLDOUT are assigned through the frozen TRAIN transformation/centroids. Sparse participants receive `SPARSE_DEFAULT`. Cluster stability is reported using deterministic TRAIN bootstrap refits; k is not chosen from HOLDOUT performance.

## Targets and incremental baseline

Primary supervised target: 60-second own-market logit change. Secondary targets are own-market logit change at 15s and 300s; participant-signed markout at 60s/300s; absolute 60s move; future 60s economic-trade count; 2/10-economic-trade logit change; and next economic-price-change sign.

The generic baseline includes price/logit level, anonymous own-market returns, economic-trade count/value, current fill value, broader event activity, probability level, and common-event movement where it can be constructed without ambiguity. The model ladder compares baseline only, baseline + identity-history state, baseline + full behavioural state, baseline + frozen archetype state, and one bounded histogram-gradient-boosting challenger. Ridge/logistic hyperparameters are selected on DEV only.

## Falsification and concentration

Every positive result faces participant-state permutation inside market/time/activity strata, frequency-matched pseudo-identities, infrastructure exclusion, top-activity removal, frequency/size controls, grouped market/event/family diagnostics, historical-score shuffle and a delayed-state placebo where computationally feasible.

Concentration is reported with anonymised audit IDs only: top-1/top-5/top-10/top-1% contribution shares, effective number of contributors, unseen-wallet performance, sparse-wallet performance and repeat-wallet coverage.

005E may return `NO_INCREMENTAL_SIGNAL`, `REGIME_SPECIFIC_CANDIDATE`, `CROSS_FAMILY_CANDIDATE`, `TOURNAMENT_FEATURE_CANDIDATE`, or `INCONCLUSIVE`. None is a named-wallet recommendation or an execution authorization.
