# EXPERIMENT-005D — Final Structural / Relative-Value Atlas Report

**Status:** COMPLETE — single frozen HOLDOUT inspected; post-hoc independent-review falsification complete  
**Base:** `ba938bedcf63f562be8b26c9502e828391123867`  
**Branch:** `experiment/005d-structural-rv-atlas`  
**PR:** #42  
**Orders:** disabled throughout

## Executive conclusion

005D found **narrow late-count structural-convergence evidence, not broad structural alpha and not a structural-arbitrage machine**.

The original frozen HOLDOUT still contains three passing cells across two relationships. A downstream `POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION` then overlaid the canonical EXPERIMENT-004A regime windows and tested materially longer dependence blocks without changing the original signal, controls, parameters, shortlist, horizons or FDR family.

That review changes the interpretation materially: **all 13 fixed predictive HOLDOUT cells occur 100% inside canonical LATE_COUNT windows**. None of the predictive HOLDOUT evidence comes from PRE_ELECTION, ELECTION_DAY_PRE_RESULTS or ACTIVE_RESULTS.

The **Fuerza Popular Senate → Chamber most-seats** relationship in Peru therefore remains only as a **late-count structural-convergence candidate**, with 5s and 60s treated as one feature family. It survives the fixed temporal-concentration falsification, but longer-block uncertainty is mixed, so its disposition is **RETAIN WITH WEAK DEPENDENCE ROBUSTNESS**.

The **Abelardo de la Espriella overall winner → Antioquia runoff first-place** relationship in Colombia is also a **late-count structural-convergence candidate**. Its direction is robust to the fixed 30m/1h/4h/8h sensitivity and is not concentrated in one hour, but its economic-scale translation is extremely small and its signal-strength response remains non-monotone.

Everything else either failed the original confirmatory/negative-control gate, remained reconstruction-only, or lacked sufficient hard-structure support.

## Provenance and contamination control

The preregistration was frozen before outcome review. The accepted TRAIN/DEV runner is:

- preregistration SHA-256: `d43a75ff8e0b5df6c717f37721e339116a6297b299c4e26d8b9a5f242b9c7a51`
- TRAIN/DEV runner SHA-256: `525f4678565d80cbceddd7ede298c7f810fc41c4b94652dec34f08cd63ccaa5f`
- PRE_HOLDOUT_FREEZE SHA-256: `6ac0f6ea27b5553267d8fa94a9f5bca906680f18d03de45480269da8b9b21cbe`
- corrected HOLDOUT runner SHA-256: `e65c961b73c56e26b322c8336944ea2fbc048ccf53bdd645cdaf77c53caef7ce`

The prior-exposure manifest records the one incidental pre-freeze search excerpt. No ranked 004B/004C result tables, prior coefficients, best horizons or prior winner rankings were intentionally used to define the 005D search.

A first HOLDOUT runner was invalidated **pre-evidence** because it omitted confirmatory FDR. Its outputs were not opened or downloaded. The corrected runner added only preregistered HOLDOUT accounting/controls; shortlist, fitted parameters, horizons and split remained fixed.

The current Kaggle kernel source was pulled after completion and independently hashes to `e65c961b...`, matching the replacement marker.

## Structural universe

The final inventory contains **239 relationship records**:

- 9 HARD;
- 215 SOFT;
- 15 UNKNOWN.

The dominant sources are 122 generic same-event statistical families, 72 manually audited INDIRECT edges, 14 historical structural nodes, 7 exhaustive partitions and 2 nested-threshold families.

HARD means a logical/payoff relation supported by explicit semantics. SOFT means an economic/statistical relationship. UNKNOWN fails closed. Shared titles or event IDs were never promoted to hard identities.

U.S. 2024 and Canada 2025 were included where DATA-002 and metadata supported generic same-event structural reconstruction. They were not silently treated as exact constituent→aggregate identities.

## TRAIN / DEV atlas

The frozen TRAIN/DEV run emitted:

- 17,967 candidate/diagnostic rows;
- 12,683 descriptive diagnostic rows;
- 3,500 evaluable OK rows;
- 1,784 unavailable rows;
- 145 raw predictive DEV survivor keys;
- 354 reconstruction survivor groups.

The 145 raw predictive survivors were heavily correlated: they collapse to **7 target contracts across only 3 event windows**. The surviving lane was exclusively **LOO-FAMILY indirect structural graph**; no direct-family projection was relabelled as indirect alpha.

To avoid carrying a near-duplicate model zoo into HOLDOUT, the committed DEV review retained the simplest passing representation for each surviving target relationship and all of that representation's passing horizons. This did not rank on effect size. The frozen confirmatory set was therefore **13 predictive cells across 7 economic relationships**, plus 25 bounded reconstruction-only cells.

### Persistence and stability

The 13 frozen predictive cells had TRAIN AR(1) coefficients from 0.9973 to 0.99994. The implied half-lives ranged from about **21 minutes to 16.9 hours**, with a median of about **4.1 hours**.

That is useful context: these are slow structural residuals with short-horizon response tests layered on top, not millisecond dislocations.

Only 4/13 frozen cells had monotone DEV response across signal-magnitude quartiles. DEV standardized effects were small (median ≈0.019), so the study deliberately relied on the untouched HOLDOUT and negative controls rather than DEV headline magnitude.

## HOLDOUT results

All 13 frozen predictive cells were evaluated exactly once. Confirmatory multiple testing used one HOLDOUT predictive family with BH FDR at 10%, 300-second moving-block bootstrap uncertainty, and the frozen 60-second delayed-state falsification.

**3 of 13 cells passed**, representing only **2 unique economic relationships**.

### 1. Peru — Fuerza Popular Senate → Chamber most-seats

**Representation:** `RAW_INDIRECT_PROB_MEAN`  
**Leakage mode:** LOO-FAMILY

The target is the Fuerza Popular Chamber-most-seats market; the reference is the same party's Senate-most-seats market.

At **5 seconds**:

- HOLDOUT n = 10,841;
- q = 0.01299;
- bootstrap 95% interval for the repair-direction score = [1.56e-8, 7.26e-8];
- standardized effect = 0.0285;
- positive-contribution fraction = 79.2%;
- 60-second delayed-state effect ratio = 0.620;
- confirmatory pass = yes.

At **60 seconds**:

- HOLDOUT n = 9,942;
- q = 0.01818;
- bootstrap 95% interval = [1.21e-8, 3.23e-7];
- standardized effect = 0.0595;
- positive-contribution fraction = 75.4%;
- delayed-state effect ratio = 0.773;
- confirmatory pass = yes.

The 15s and 30s variants also had small FDR-adjusted q-values, but failed the preregistered delayed-state gate because the 60-second delayed signal retained 88.5% and 86.4% of the effect respectively. They are rejected rather than rescued.

The 5s and 60s passes are best understood as **one cross-chamber structural feature family**. Neither had monotone HOLDOUT response across signal-magnitude quartiles, so magnitude sizing should not be inferred from this experiment.

### 2. Colombia — Abelardo overall winner → Antioquia runoff

**Representation:** `AFFINE_INDIRECT_LOGIT`  
**Horizon:** 5 seconds  
**Leakage mode:** LOO-FAMILY

- HOLDOUT n = 17,156;
- q = 0.00649;
- bootstrap 95% interval = [2.38e-10, 6.67e-10];
- standardized effect = 0.00392;
- positive-contribution fraction = 97.9%;
- delayed-state effect ratio = 0.444;
- confirmatory pass = yes.

The direction is statistically stable under the frozen test, but the standardized effect is extremely small and the signal-strength quartiles are non-monotone. The positive-contribution fraction is literally the fraction of `signal × future response > 0`; it is not a classification accuracy metric. This result belongs in a weak-challenger lane, not as a standalone strategy.

## Post-hoc independent-review falsification

This section is downstream of the already-exposed HOLDOUT. It does **not** replace the original confirmatory result and cannot promote a failed candidate.

The review runner is `b394bce34097a228281599862799837fb501bb5c9a30cf63c7583c061b2dad9f`, executed as Kaggle kernel `polyleviathan/sig-cup-exp005d-review-followup` version 1. The accepted original empirical outputs are unchanged.

### Regime attribution

The original 005D runner split the full observed event-panel span 60/20/20 rather than using EXPERIMENT-004A regimes. Overlaying the frozen 004A boundaries shows:

- all 13 fixed predictive HOLDOUT cells are **100% LATE_COUNT**;
- PRE_ELECTION share = 0%;
- ELECTION_DAY_PRE_RESULTS share = 0%;
- ACTIVE_RESULTS share = 0%;
- POST_RESOLUTION_DIAGNOSTIC share = 0%;
- outside canonical windows share = 0%.

For Peru first round, HOLDOUT runs from **2026-04-14 00:04:56 UTC to 2026-04-14 23:59:55 UTC**; canonical LATE_COUNT began on **2026-04-13 05:00 UTC**.

For Colombia runoff, HOLDOUT runs from **2026-06-23 00:04:56 UTC to 2026-06-23 23:59:55 UTC**; canonical LATE_COUNT began on **2026-06-22 03:11 UTC**.

Therefore the surviving evidence is **late-count convergence evidence**, not evidence of generic election-time structural alpha.

### Longer-block dependence sensitivity

The original 300-second bootstrap remains the confirmatory analysis. Longer blocks are post-hoc robustness diagnostics across the same fixed 13-cell family.

For **Peru 5s**, q-values are 0.0182 at 30m, 0.0325 at 1h, **0.1023 at 4h**, and 0.00162 at 8h. The percentile interval remains positive at all four longer blocks.

For **Peru 60s**, q-values remain small, but the percentile interval crosses zero at **1h** and **4h** before becoming positive again at 8h. The 8h result has a very small effective number of temporal blocks over a one-day HOLDOUT and is not treated as a rescue.

Peru is therefore **not uniformly dependence-robust**.

For **Colombia 5s**, every fixed 30m/1h/4h/8h interval remains positive and every corresponding q-value remains small. Its statistical direction is materially more dependence-robust than Peru's.

### Time concentration

All three original passing cells occupy **24 distinct UTC hours** in one contiguous active period.

Peru 5s is the most concentrated: the top hour contributes 22.2% of positive contribution, the top three 53.5%, and the top six 75.3%. The sign remains positive after removing the single highest-positive hour.

Peru 60s has corresponding shares of 12.4%, 35.4% and 61.8%; Colombia 5s has 9.0%, 26.8% and 52.5%. All three retain a positive score after removing the top positive-contribution hour. The original evidence is therefore **not generated by one isolated hour**, though Peru is stronger in the first temporal half.

### Economic scale

The original primary statistic is **TRAIN-controlled covariance between structural signal residual and future target-price-change residual**.

Using the frozen HOLDOUT beta as a local descriptive translation:

- Peru 5s: a 10pp residual corresponds to about **0.00461 percentage points** of predicted 5s target response;
- Peru 60s: a 10pp residual corresponds to about **0.02087 percentage points** of predicted 60s target response;
- Colombia 5s: a 10pp residual corresponds to about **0.000162 percentage points** of predicted 5s target response.

These are residualized regression translations, **not** PnL, executable spread, RMSE improvement, Sharpe, or demonstrated realizable edge.

The complete review evidence is in `data/experiments/experiment_005d/REVIEW_FOLLOWUP.json`, `regime_attribution.csv`, `dependence_sensitivity.csv`, `time_concentration_by_hour.csv` and `effect_scale.csv`.

## Important falsifications

The controls removed several superficially attractive findings.

- **Juntos por el Perú Chamber → Senate, 5s:** q = 0.00649 and bootstrap evidence was positive, but the delayed-state effect ratio was **1.043**. The stale signal explained at least as much as the contemporaneous signal, so the candidate fails.
- **Fuerza Popular 15s and 30s:** q = 0.0195 and 0.00866 respectively, but delayed-state ratios were 0.885 and 0.864; both fail the frozen <0.80 gate.
- **Colombia first-round Abelardo → overall winner, 30/60/120s:** HOLDOUT response turned negative/null.
- **Iván Cepeda overall winner → Antioquia runoff, 5s:** HOLDOUT response was negative/null.
- **Juntos por el Perú Senate → Chamber, 5s:** HOLDOUT response was negative.

This is why the final result is much narrower than the DEV atlas.

## Hard coherence and arbitrage

The experiment generated 953 hard/coherence-related TRAIN/DEV rows:

- 522 coherent-projection rows;
- 252 threshold-surface rows;
- 174 partition-reconstruction rows;
- 5 top-of-book hard-coherence diagnostics.

The 948 predictive hard/coherence rows were **unavailable under the frozen support requirements**, not promoted. This is a data-support limitation rather than proof that the mathematics has no value.

The executable hard lane was also too sparse for an arbitrage claim. Four partition top-of-book diagnostics and one threshold dominance diagnostic were produced; none was depth-certified, none claimed arbitrage, and only the Colombia runoff-pair partition had any complete TRAIN top-of-book states (6, with 0 over/under-pricing flags). No fee/depth/all-leg-size execution certificate was available.

Therefore: **no hard arbitrage result is claimed by 005D**.

## Reconstruction-only evidence

The HOLDOUT evaluated 22 of 25 frozen reconstruction cells; three Peru cells were unavailable under the frozen support threshold and were not rescued.

Median HOLDOUT RMSE improvement versus the declared naive same-event baseline was:

- Canada 2025: **+47.0%** across 5 evaluated cells, but heterogeneous (two cells worsened materially);
- Colombia 2026: **+96.3%** across 5;
- Hungary 2026: **+99.8%** across 5;
- Peru 2026: **+97.8%** across 2;
- U.S. 2024: **+96.0%** across 5.

These figures establish that coherent/same-event information can reconstruct many prices extremely well. They do **not** establish tradable alpha because these are LOO-PRICE / generic same-event reconstructions and may retain mechanically or statistically close information.

Their value is as fair-value scaffolding, consistency diagnostics and potential features for a central model.

## What belongs in the central model tournament

### Peru late-count challenger

Retain **Peru Fuerza Popular Senate → Chamber `RAW_INDIRECT_PROB_MEAN`** as one **late-count structural-convergence feature family**. Carry the 5s and 60s targets as correlated horizons, not separate discoveries.

It still deserves a tournament slot because the original direction survives the delayed-state gate and fixed temporal-concentration falsification, but it must carry the label **RETAIN WITH WEAK DEPENDENCE ROBUSTNESS**. It is not evidence of PRE_ELECTION or ACTIVE_RESULTS alpha.

### Colombia secondary diagnostic challenger

The **Colombia Abelardo overall → Antioquia runoff `AFFINE_INDIRECT_LOGIT` 5s** signal may remain in a secondary/diagnostic late-count lane. Its statistical direction is robust to the longer-block sensitivity, but the local effect scale is extremely small and non-monotone.

### Do not promote

Do not promote the other five DEV relationships, hard/coherent projection variants, threshold challengers or generic reconstruction families as predictive alpha from this experiment.

Generic reconstruction results may still be supplied to the central model as **reconstruction/fair-value features**, with their label kept separate from predictive evidence.

## Final disposition

005D programme-level disposition:

**NARROW LATE_COUNT STRUCTURAL EVIDENCE — RECONSTRUCTION STRONG / PREDICTIVE EVIDENCE WEAK AND REGIME-SPECIFIC**

Specifically:

- 005D does **not** establish PRE_ELECTION or ACTIVE_RESULTS structural alpha;
- all fixed predictive HOLDOUT evidence is late-count;
- Peru remains a tournament-worthy late-count feature family, but with weak dependence robustness;
- Colombia remains statistically robust but economically tiny;
- hard arbitrage is not established;
- reconstruction remains strong across several election families but is reconstruction, not predictive alpha.

No live order placement is enabled. The branch is ready for independent review.
