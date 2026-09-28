# EXPERIMENT-005D — Independent Review Follow-up

**Status:** COMPLETE  
**Label:** `POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION`  
**Branch:** `experiment/005d-structural-rv-atlas`  
**Kaggle kernel:** `polyleviathan/sig-cup-exp005d-review-followup`, version 1  
**Review runner SHA-256:** `b394bce34097a228281599862799837fb501bb5c9a30cf63c7583c061b2dad9f`  
**Scope:** the existing fixed 13-cell predictive HOLDOUT family only

## Executive finding

The review materially narrows the interpretation of EXPERIMENT-005D.

**All 13 fixed predictive HOLDOUT cells fall 100% inside the canonical EXPERIMENT-004A LATE_COUNT windows.** None of the predictive HOLDOUT evidence occurred in PRE_ELECTION, ELECTION_DAY_PRE_RESULTS, or ACTIVE_RESULTS.

The two original surviving relationships therefore cannot be described as generic structural alpha. They are, at most, **late-count structural-convergence candidates**.

The Peru Fuerza Popular Senate → Chamber relationship remains directionally interesting, but longer-block uncertainty is mixed. It is retained only with **weak dependence robustness**. The Colombia Abelardo overall-winner → Antioquia-runoff relationship is statistically robust to the fixed long-block sensitivity, but the economic-scale translation is extremely small.

No original empirical output changed. No failed candidate was promoted.

## Provenance

The accepted empirical chain remains unchanged:

- base: `ba938bedcf63f562be8b26c9502e828391123867`
- preregistration: `d43a75ff8e0b5df6c717f37721e339116a6297b299c4e26d8b9a5f242b9c7a51`
- TRAIN/DEV runner: `525f4678565d80cbceddd7ede298c7f810fc41c4b94652dec34f08cd63ccaa5f`
- PRE_HOLDOUT_FREEZE: `6ac0f6ea27b5553267d8fa94a9f5bca906680f18d03de45480269da8b9b21cbe`
- corrected HOLDOUT runner: `e65c961b73c56e26b322c8336944ea2fbc048ccf53bdd645cdaf77c53caef7ce`
- corrected HOLDOUT results: `610e4b6bd0ed199c63cc2543206ce70c4900e7cb1b84a24bf0e572f4f975920b`

The follow-up regenerated the accepted HOLDOUT runner byte-for-byte before appending review diagnostics. The frozen review design SHA-256 is `5ba931cea50108ab6f8eab319d7f4078a7cb8f4d5ca7a4a3dcabfb8c007039a9`.

## Temporal attribution

Canonical regime boundaries come only from EXPERIMENT-004A. Assignment uses half-open intervals and the forecast-origin timestamp.

### Peru first round

The structural event panel spans **2026-04-10 00:00:00.100 UTC to 2026-04-14 23:59:55.100 UTC**.

The chronological 005D split was:

- TRAIN: panel start to **2026-04-12 23:54:57.100 UTC**;
- TRAIN→DEV gap: ten minutes centred on the 60% boundary;
- DEV: **2026-04-13 00:04:57.100 UTC to 2026-04-13 23:54:56.100 UTC**;
- DEV→HOLDOUT gap: ten minutes centred on the 80% boundary;
- HOLDOUT: **2026-04-14 00:04:56.100 UTC to 2026-04-14 23:59:55.100 UTC**.

Canonical ACTIVE_RESULTS ended at **2026-04-13 05:00 UTC**. Canonical LATE_COUNT then runs until **2026-05-15 15:04 UTC**.

Therefore every valid Peru HOLDOUT observation in all fixed Peru cells is **LATE_COUNT: 100%**. PRE_ELECTION, ELECTION_DAY_PRE_RESULTS, ACTIVE_RESULTS, POST_RESOLUTION_DIAGNOSTIC and outside-window shares are all zero.

The same is true for the contribution `signal_residual × future_response_residual`: 100% of the HOLDOUT contribution is generated in LATE_COUNT.

### Colombia runoff

The panel spans **2026-06-19 00:00:00.296 UTC to 2026-06-23 23:59:55.296 UTC**.

The chronological split was:

- TRAIN: panel start to **2026-06-21 23:54:57.296 UTC**;
- DEV: **2026-06-22 00:04:57.296 UTC to 2026-06-22 23:54:56.296 UTC**;
- HOLDOUT: **2026-06-23 00:04:56.296 UTC to 2026-06-23 23:59:55.296 UTC**;
- the same 300-second purge/embargo is applied on both sides of each split boundary.

Canonical ACTIVE_RESULTS ended at **2026-06-22 03:11 UTC**, after which LATE_COUNT runs until **2026-06-25 05:00 UTC**.

Every valid Colombia-runoff HOLDOUT observation is therefore also **LATE_COUNT: 100%**.

The Colombia-first-round failed cells are likewise entirely LATE_COUNT. Across the complete 13-cell fixed family, there is **no predictive HOLDOUT evidence from PRE_ELECTION or ACTIVE_RESULTS**.

## Dependence sensitivity

The original five-minute bootstrap remains the confirmatory result. The following longer blocks are post-hoc robustness diagnostics only. BH q-values are recomputed across the same fixed 13-cell family separately at each block length.

### Peru — Fuerza Popular Senate → Chamber, 5 seconds

| Block | p | q | 95% bootstrap interval |
|---|---:|---:|---:|
| Original 5m | 0.003996 | 0.01299 | [1.56e-8, 7.26e-8] |
| 30m | 0.006993 | 0.01818 | [1.46e-8, 7.00e-8] |
| 1h | 0.01499 | 0.03247 | [1.24e-8, 7.55e-8] |
| 4h | 0.06294 | 0.10227 | [1.18e-8, 9.26e-8] |
| 8h | 0.000999 | 0.001623 | [1.39e-8, 7.04e-8] |

The 4h q-value moves just above 0.10 even though the percentile interval remains positive. The 8h result re-strengthens, but an 8h block over a one-day HOLDOUT has only a very small effective number of temporal blocks and is not treated as a rescue.

### Peru — same relationship, 60 seconds

| Block | p | q | 95% bootstrap interval |
|---|---:|---:|---:|
| Original 5m | 0.006993 | 0.01818 | [1.21e-8, 3.23e-7] |
| 30m | 0.001998 | 0.008658 | [8.76e-9, 3.16e-7] |
| 1h | 0.000999 | 0.002597 | [-1.43e-8, 3.23e-7] |
| 4h | 0.000999 | 0.002165 | [-1.01e-7, 3.26e-7] |
| 8h | 0.000999 | 0.001623 | [1.03e-7, 2.80e-7] |

The null-centred one-sided p/q values remain small, but the percentile interval crosses zero at 1h and 4h. That disagreement matters. The result is **not uniformly dependence-robust**.

### Colombia — Abelardo overall winner → Antioquia runoff, 5 seconds

| Block | p | q | 95% bootstrap interval |
|---|---:|---:|---:|
| Original 5m | 0.000999 | 0.006494 | [2.38e-10, 6.67e-10] |
| 30m | 0.000999 | 0.006494 | [2.55e-10, 6.57e-10] |
| 1h | 0.000999 | 0.002597 | [2.68e-10, 6.55e-10] |
| 4h | 0.000999 | 0.002165 | [1.71e-10, 6.81e-10] |
| 8h | 0.000999 | 0.001623 | [1.46e-10, 6.88e-10] |

The Colombia direction survives every fixed dependence sensitivity. This does not make it economically large.

## Episode / time concentration

The fixed aggregation is UTC clock-hour.

### Peru 5s

- 24 distinct UTC hours, one contiguous active period;
- top 1 hour contributes **22.2%** of positive contribution;
- top 3 hours: **53.5%**;
- top 6 hours: **75.3%**;
- first temporal-half score: **5.92e-8**;
- second temporal-half score: **1.51e-8**;
- removing the single highest-positive hour leaves score **+2.88e-8**.

The signal is stronger in the first half and moderately concentrated, but it is not generated by one hour.

### Peru 60s

- 24 distinct UTC hours, one contiguous active period;
- top 1 hour: **12.4%** of positive contribution;
- top 3 hours: **35.4%**;
- top 6 hours: **61.8%**;
- first-half score: **2.61e-7**;
- second-half score: **6.95e-8**;
- after removing the highest-positive hour: **+1.71e-7**.

Again, the sign survives the fixed concentration falsification.

### Colombia 5s

- 24 distinct UTC hours, one contiguous active period;
- top 1 hour: **9.0%** of positive contribution;
- top 3 hours: **26.8%**;
- top 6 hours: **52.5%**;
- both temporal halves are positive;
- removing the highest-positive hour leaves the score positive at **4.54e-10**.

Colombia is not meaningfully dependent on one short convergence episode.

## Economic scale

The primary statistic is **TRAIN-controlled covariance between the structural signal residual and future target-price-change residual**.

The following are local descriptive translations of the frozen HOLDOUT regression beta. They are not trading-return estimates.

| Relationship / horizon | 1pp residual | 5pp residual | 10pp residual | Standardized effect |
|---|---:|---:|---:|---:|
| Peru 5s | 0.000461 pp | 0.002303 pp | 0.004606 pp | 0.0285 |
| Peru 60s | 0.002087 pp | 0.010437 pp | 0.020873 pp | 0.0595 |
| Colombia 5s | 0.000016 pp | 0.000081 pp | 0.000162 pp | 0.00392 |

All three original passing cells remain **non-monotone** across signal-magnitude quantiles. Their delayed-state ratios remain 0.620 for Peru 5s, 0.773 for Peru 60s and 0.444 for Colombia 5s.

These quantities must not be described as PnL, executable spread, Sharpe, RMSE improvement or directly realizable price edge.

## Corrected dispositions

### Peru

Scientific label: **`LATE_COUNT_STRUCTURAL_CONVERGENCE_CANDIDATE`**

Disposition: **RETAIN WITH WEAK DEPENDENCE ROBUSTNESS**

The original direction survives the fixed temporal-concentration falsification and several longer-block checks, but long-block uncertainty is not uniformly robust. In particular, the 60s percentile interval crosses zero at 1h and 4h, while the 5s 4h q-value is marginally above 0.10.

Peru should still enter the later central-model tournament, but only as **one late-count structural-convergence feature family**, with 5s and 60s treated as correlated horizons. It should not enter as generic structural alpha or as a demonstrated standalone trading edge.

### Colombia

Scientific label: **`LATE_COUNT_STRUCTURAL_CONVERGENCE_CANDIDATE`**

Disposition: **RETAIN — LATE_COUNT STRUCTURAL CONVERGENCE CANDIDATE**

The statistical direction is robust across the fixed long-block sensitivity and is not concentrated in one hour. However, the effect-scale translation is extremely small and non-monotone. It remains a secondary diagnostic challenger only.

## Programme-level disposition

**NARROW LATE_COUNT STRUCTURAL EVIDENCE — RECONSTRUCTION STRONG / PREDICTIVE EVIDENCE WEAK AND REGIME-SPECIFIC**

This replaces the earlier generic `QUALIFIED POSITIVE` interpretation.

005D does not establish PRE_ELECTION or ACTIVE_RESULTS structural alpha. It establishes strong reconstruction evidence and a narrow pair of **late-count convergence** relationships, with Peru carrying meaningful dependence uncertainty and Colombia carrying negligible economic scale.

No live order placement is enabled. No sibling 005 lane was modified.
