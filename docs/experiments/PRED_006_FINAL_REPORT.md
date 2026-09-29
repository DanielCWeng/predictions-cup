# PRED-006 — DATA-003 Hail Mary Predictive Discovery — Final Report

## Disposition

**CURRENT_UNIVERSE_CANDIDATE_REQUIRES_FUTURE_CONFIRMATION**

PRED-006 produced two frozen hazard candidates that passed the one-shot FINAL gate. They are not classified as proven alpha and must not be integrated into MAKE until genuinely future CAPTURE-001 data confirms the exact frozen specifications.

## Scientific controls

- Dataset: `polyleviathan/sig-cup-data-003-sig-actual-fills`.
- Canonical chronology: Polygon `block_number, log_index`.
- Split manifest SHA-256: `28d41fbb590f7f9abe59ca24e00841307fe13991c3c695748240fe127847f349`.
- DEV starts: 2026-08-09T00:00:00Z.
- FINAL starts: 2026-09-11T00:00:00Z.
- Phase-0 target-eligible rows: TRAIN 41,549; DEV 22,338; FINAL 13,637.
- FINAL remained unopened through search, ablation, placebo and shortlist freeze.
- Shortlist freeze SHA-256: `5deff02e70bbb63a7355390c4babfc1ed63dd8d5b112087a2e9ff9dddfe835f6`.
- No post-FINAL rescue, retuning, subsetting, horizon change, feature change or candidate reselection is permitted.

Early row-level search attempts were invalidated before they could enter scientific selection. The accepted search uses condition-block-end observations and strictly post-block targets.

## Search breadth

Primary TRAIN/DEV search executed **365 model/target evaluations**, before separate hazard ablation and placebo work:

| Family | Evaluations | Result |
|---|---:|---|
| Temporal/state | 70 | No DEV candidate |
| Participant/flow | 70 | No DEV candidate |
| Cross-market | 70 | No DEV candidate |
| Controlled interactions | 70 | No DEV candidate |
| Event-target atlas | 55 | No DEV candidate |
| Corrected hazard/fee | 30 | 3 DEV candidates |

The event-target atlas included next-price-change direction and conditional material-move direction. Several models had positive headline improvement, but none passed the full hostile DEV screen.

Corrected hazard candidates then underwent feature ablation plus a permuted-label placebo. The exact full-feature variants remained selected; placebo improvements were negative for the two frozen finalists.

## Frozen shortlist

**PRED006-C01:** 1,800-second next-price-change hazard; HGB `max_leaf_nodes=15`; DEV Brier improvement **10.75%**; 74.49% of conditions positive; first/second halves **8.98% / 12.51%**; permuted-label improvement **-4.98%**.

**PRED006-C02:** 600-second next-price-change hazard; HGB `max_leaf_nodes=7`; DEV Brier improvement **10.61%**; 72.45% of conditions positive; first/second halves **9.58% / 11.65%**; permuted-label improvement **-1.98%**.

Exact feature lists and handling rules are frozen in `data/experiments/pred_006/shortlist/candidate_registry.json`.

## One-shot FINAL

| Candidate | Horizon | FINAL rows | Conditions | SIG markets | Baseline Brier | Candidate Brier | Relative improvement | AUC | Bootstrap 2.5% | Conditions positive | First half | Second half | Pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| C01 | 1800s | 6,924 | 462 | 198 | 0.22206 | 0.20389 | **8.18%** | 0.712 | 0.007876 | 59.96% | 11.15% | 5.16% | PASS |
| C02 | 600s | 6,967 | 466 | 198 | 0.19090 | 0.17494 | **8.36%** | 0.697 | 0.005791 | 58.37% | 12.12% | 4.29% | PASS |

Both candidates cleared every frozen FINAL criterion.

The effect is not uniform. Among SIG markets with at least 10 supported observations, C01 was positive in 75.2% and C02 in 73.5%; their 10th-percentile market improvements were negative (-7.7% and -11.3%). The second FINAL half was also materially weaker than the first, although still positive. Mapping-class performance stayed positive for EXACT, DERIVED and NEAR observations. Calibration is imperfect in some bins; no post-FINAL recalibration is allowed.

## Interpretation

This is a **movement-hazard** signal, not a directional fair-value forecast. It estimates whether the mapped Polymarket YES price is likely to change within the next 10 or 30 minutes after an observable condition-block-end state.

That can potentially inform quote aggressiveness, refresh/cancel urgency, or confidence in stale external fair value. It does not predict the direction of the move.

The frozen features combine current probability/state, recent activity/volatility/momentum, and contemporaneous fee evidence. Fee-evidence feature parity and latency must be proven in live capture before deployment.

## Deployment boundary

Do **not** integrate either candidate into MAKE yet. The only permitted next scientific step is genuinely future confirmation on CAPTURE-001 data using the frozen definitions. The interface contract is persisted in `data/experiments/pred_006/final/make_plugin_spec.json`.

## Execution evidence

- Phase-0 split/audit: `36583184544`.
- Non-predictive universe audit: `36582983565`.
- Block-safe temporal/flow/cross/interactions wave: `36590503658`.
- Event-target atlas: `36597529912`.
- Corrected block-end hazard search: `36597808967`.
- Hazard ablation/placebo: `36598428233`.
- Immutable shortlist freeze: `36598785362`.
- One-shot FINAL launch wrapper: `36599964960`.
- Canonical FINAL status probe: `36603524903` -> COMPLETE.
- Read-only FINAL output/log recovery: `36603691581`.

Kaggle canonicalized the FINAL notebook title to `polyleviathan/pred006-one-shot-final`. The launch wrapper had been polling the legacy declared slug `polyleviathan/pred006-final`, so the wrapper appeared stuck even though the single Kaggle execution had completed. Outputs were recovered read-only; **FINAL was not rerun**.

## Final scientific statement

PRED-006 did not recover a robust directional price or cross-market forecasting model. It did recover two related current-universe price-change hazard models that independently survived the frozen FINAL gate.

They remain **candidates requiring future confirmation**, not live edge. There will be **no post-FINAL rescue** inside DATA-003.
