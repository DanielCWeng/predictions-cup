# R3-FV-001 — Final Report

## Executive conclusion

R3-FV-001 does **not** find a confirmed deployable structural fair-value or directional information-leadership model on the current SIG Cup universe.

The corrected programme used the intended current-universe data path:

- **DATA-004** P0/P1 structural and adjacent Polymarket fills as sources;
- **DATA-003** accepted direct/reference mapped fills as targets;
- strict Polygon `block_number, log_index` chronology;
- the accepted crosswalk and R2.5 semantic graph;
- no DATA-001 fitting/evaluation;
- no Hungary/Colombia/Peru historical analogue fitting/evaluation;
- no P2/P3 rescue;
- no FINAL access.

The first Ridge/HGB structural screen was genuinely run on DATA-004 -> DATA-003, but its original programme-level stop was premature. R3 was therefore reopened and taken through LP/partial identification, QP, KL, MaxEnt, supported count surfaces, corrected next-update residuals, TRAIN-only calibration, scalar event-time lead-lag, and full distribution-shape lead-lag.

After that corrected programme, **zero candidates pass a frozen TRAIN/DEV gate**. FINAL begins at **2026-09-10 00:00 UTC** and remains completely unopened.

## 1. Dataset and causality gate

### DATA-004

Bound source: `polyleviathan/sig-cup-data-004-ets-p0p1-fills`.

Verified accepted source package:

- 231,964 fill rows;
- 298 markets;
- 210 P0 markets;
- 88 P1 markets;
- 596 tokens;
- 343 fill partitions;
- no missing block/log ordering keys;
- strict block/log chronology reconstructable.

### DATA-003

Bound target: `polyleviathan/sig-cup-data-003-sig-actual-fills`.

Accepted block-corrected target surface:

- 77,554 economic fills;
- 43,222 Polygon blocks;
- 231 mapped SIG markets;
- zero missing block numbers;
- zero missing block timestamps;
- zero timestamp mismatches;
- zero duplicate `(block_number, log_index)` groups.

### Frozen split

- TRAIN: before the purged DEV boundary;
- DEV starts: **2026-08-04 00:00 UTC**;
- FINAL starts: **2026-09-10 00:00 UTC**;
- 6-hour boundary purge;
- FINAL rows accessed: **0**.

For target-state inference at target block B, corrected structural models use source state only from blocks strictly less than B. Event-time leadership tests require the target baseline before the source block and the response after it.

## 2. Why the original STOP was reopened

The original correct-universe generic screen found:

| Mode | Baseline MAE | Model MAE | Improvement |
|---|---:|---:|---:|
| LOO-PRICE blend Ridge | 0.005932 | 0.015031 | -0.009100 |
| LOO-FAMILY blend Ridge | 0.005506 | 0.034401 | -0.028895 |

Both bootstrap intervals were entirely below zero.

Those are real negative results, but they did not execute the commissioned structural mathematics. The programme was therefore reopened rather than pretending generic aggregate regressions had already tested LP/QP/KL/MaxEnt/inverse methods.

## 3. Partial identification

### Georgia Senate x Governor joint

The Georgia four named joint cells plus explicit residual/Other produced DEV marginal bounds with:

- 255 DEV target events;
- median interval width: **1.0**;
- direct-price interval coverage: **98.43%**.

The high coverage is not strong information. The interval is usually effectively vacuous.

### Senate x House count surface

The 13 observed categories over the 16 fine Senate-band x House-band state space produced useful structural constraints, but not a successful point FV.

For House references after coherent projection:

- Democratic House direct coverage: ~**96.3%**;
- Republican House direct coverage: ~**98.1%**;
- median interval width: ~**0.229**.

For Senate references, the corresponding hard-threshold interpretation had **0% coverage**. This falsifies treating the SIG `win` wording and the external `control`/seat-threshold wording as an exact identity. It does not prove the source market has no information.

## 4. Coherent point estimates

### Georgia QP/KL

Only 35 fully source-complete DEV rows across two targets supported the point comparison.

- persistence MAE: **0.004514**;
- QP MAE: **0.016599**;
- KL MAE: **0.016239**.

Both are materially worse than persistence.

### Senate x House QP/KL

Across 1,534 DEV rows:

- persistence MAE: **0.005130**;
- QP MAE: **0.120009**;
- KL MAE: **0.120243**.

These point constructions fail decisively.

MaxEnt is retained as an explicit completion rule inside an underidentified set, not as evidence that the completed point is a fair value.

## 5. LOO-PRICE

The broad LOO-PRICE screen is negative.

The corrected settlement-aligned nonliteral `EXACT_EQUIVALENT` structural comparator has **zero usable rows** after literal direct IDs are excluded and the frozen causal/source-age rules are applied.

Therefore:

- broad generic LOO-PRICE: **negative**;
- corrected exact-equivalent structural LOO-PRICE: **insufficient support**.

It is not valid to call the latter a failed model.

## 6. LOO-FAMILY / genuinely indirect information

The broad correct-universe LOO-FAMILY screen is strongly negative:

- 11,365 TRAIN events across only 5 target markets;
- 4,389 DEV events across 29 target markets;
- persistence MAE: **0.005506**;
- best model MAE: **0.034401**;
- improvement: **-0.028895**;
- full target-market cluster bootstrap interval below zero.

Priority coherent QP/KL constructions are also negative.

The severe 5-TRAIN-market versus 29-DEV-market support imbalance means a large learned global latent model would primarily be a cross-market extrapolation exercise. After simpler structural methods already fail, R3 does not escalate to an unconstrained high-dimensional latent rescue model.

## 7. Supported P0 component audit

The P0 chronology audit finds 37 event groups and seven groups with genuine source TRAIN+DEV plus linked DATA-003 target support.

The strongest untouched source families were:

| Event | TRAIN source rows | DEV source rows | TRAIN target events | DEV target events |
|---|---:|---:|---:|---:|
| Republican Senate seat distribution | 12,341 | 549 | 3,539 | 823 |
| Republican House seat distribution | 3,777 | 249 | 3,514 | 742 |
| Republican governorship count | 2,810 | 404 | 21 | 195 |
| Kamala-state Senate+Governor Republican count | 696 | 869 | 6,933 | 1,905 |
| Senate x House count surface | 456 | 644 | 15,486 | 3,771 |
| Core-four Democratic Senate joint | 346 | 190 | 3,373 | 887 |
| Biden-Trump-state Republican any-win joint | 90 | 34 | 6,933 | 1,905 |

This audit matters because it distinguishes a negative model from a model that simply cannot be fit honestly.

## 8. Supported House/Senate count fair values

### House

Using the 10-category Republican House seat distribution, the frozen 218-seat partial-identification / MaxEnt construction has 923 TRAIN and 48 DEV source-complete target rows.

Contemporaneous level:

- persistence MAE: **0.003958**;
- QP point MAE: **0.072890**;
- KL point MAE: **0.069453**.

Corrected literal-next-target-update residual:

- QP MAE improvement: **-0.000302**;
- KL MAE improvement: **-0.000317**;
- both bootstrap intervals entirely below zero;
- direction accuracy: **18.75%**.

### Senate

Using the 11-category Republican Senate seat distribution, the frozen T50 variant has 1,357 TRAIN and 80 DEV source-complete target rows.

Contemporaneous level:

- persistence MAE: **0.004375**;
- QP T50 MAE: **0.015943**;
- KL T50 MAE: **0.013173**.

Corrected next-update residual:

- QP MAE improvement: **-0.000081**;
- KL MAE improvement: **-0.000063**;
- both bootstrap intervals below zero;
- direction accuracy 25% / 35%.

The T51 variant is much worse as a level estimate and cannot be selected from DEV performance because the settlement semantics are unresolved.

## 9. TRAIN-only calibration / direct + structural error correction

To test whether the problem was merely level calibration, R3 fit an affine `direct = a + b * structural` map using TRAIN only and then fit a TRAIN-only error-correction coefficient.

This also fails.

House calibrated ECM:

- QP improvement: **-0.001721**;
- KL improvement: **-0.001915**;
- both bootstrap intervals below zero.

Senate T50 calibrated ECM:

- QP improvement: **-0.000347**;
- KL improvement: **-0.000190**;
- both bootstrap intervals below zero.

The structural surface is therefore not rescued by a simple TRAIN-only calibration of its scale.

## 10. Information leadership / lead-lag

R3 then separated the timing question from the level question.

### Scalar seat shock -> next target update

Frozen primary horizon: first direct target update within 300 seconds after a source shock.

House:

- hundreds of source shocks;
- only one DEV response in the primary 300s window;
- insufficient support.

Senate:

- QP: 20 TRAIN responses, **1 DEV response / 1 day**;
- KL: 20 TRAIN responses, **1 DEV response / 1 day**;
- that single response is favorable under both geometries;
- QP MAE improvement on the one row: **+0.000823**;
- KL MAE improvement: **+0.001347**.

This is not alpha. The frozen gate required at least 50 TRAIN rows, 30 DEV rows, four DEV day clusters, positive bootstrap lower bound, both-half stability, and >50% direction accuracy.

### Full distribution-shape CLR shock -> next target update

Seven source-target pairs were frozen:

- House count -> Republican House;
- Senate count -> Republican Senate;
- cross-chamber -> D/R House and D/R Senate;
- core-four joint -> Democratic Senate.

No pair passes.

The strongest-looking isolated case is Senate KL:

- 33 TRAIN responses;
- **1 DEV response / 1 day**;
- +0.007529 MAE improvement on that row.

But Senate QP on the same one-row DEV support is **negative** (-0.001848 improvement). The result is therefore both statistically unsupported and geometry-inconsistent.

The main empirical fact is not “one row looked good”; it is that historical fill-based direct-target response coverage inside 300 seconds is extremely sparse.

## 11. Dynamic / latent / flow interpretation

No qualified historical dynamic structural signal emerges.

A larger hierarchical latent election model is not opened because:

1. the core point estimators fail;
2. broad LOO-FAMILY TRAIN target coverage is only five markets;
3. supported inverse families have serious chronology/support limitations;
4. escalating model complexity after these nulls would be rescue search.

Flow and PRED-006 hazard augmentation are also not opened. The commission defined them as predictive additions after a structural core exists. No such core survived.

PRED-006 hazard remains useful elsewhere as update-likelihood evidence, not as R3 directional fair value.

## 12. Stability and falsification

The important negative results are not driven by one geometry.

- QP and KL agree on the major static failures.
- House static/residual failures are negative in both DEV halves.
- Senate static/residual failures have bootstrap intervals below zero even when one half is marginally positive.
- TRAIN-only calibration fails.
- the residual-horizon implementation bug was corrected before reading attempt-1 performance; corrected attempt 2 remained negative.
- semantic hard-edge mistakes were corrected without tuning outcomes.
- the isolated positive Senate timing rows fail support and QP/KL consistency requirements.

See `STABILITY_RESULTS.csv` and `FALSIFICATION_RESULTS.csv`.

## 13. FINAL

**FINAL was not opened.**

`FINAL_FREEZE.json` contains:

- champion: null;
- challengers: [];
- DEV candidates passed: 0;
- final_opened: false;
- final_rows_accessed: 0.

`FINAL_RESULTS.json` is therefore `NOT_RUN_NO_CHAMPION`.

This is the correct scientific result. Opening FINAL after no DEV candidate passed would turn the holdout into search data.

## 14. Answers to the commissioned handoff questions

### Does DATA-004 contain additional fair-value information beyond direct mapping?

It clearly contains **structural information**: hard and soft relationships, count distributions, joint markets, dependence information, and occasionally informative partial-identification bounds.

R3 does **not** find evidence that the tested transformations of that information produce a robust superior point FV or directional next-repricing model on historical DATA-003.

### Is that information genuinely independent rather than redundant?

Some R2.5 edges are genuinely nonredundant by construction, and seven P0 families have real source/target chronology support.

However, the empirical LOO-FAMILY tests do not establish a robust independent predictive edge. The strongest broad test is substantially worse than persistence.

### What mechanism survives?

No production alpha mechanism survives.

Research mechanisms that remain valid:

- partial-identification constraints;
- semantic coherence diagnostics;
- live information-leadership monitoring.

### Is it robust across model classes?

No positive candidate exists to test for robustness. Negative evidence is broad across Ridge/HGB screen, QP, KL, MaxEnt point completion, raw residual, calibrated ECM, scalar timing, and CLR distribution timing.

### Which markets/components matter most?

For future live research:

- Republican House seat distribution;
- Republican Senate seat distribution;
- Senate x House joint distribution;
- selected multi-race count/joint markets.

They have the best structural support, not a confirmed edge.

### How should MAKE use the result?

Do not use an R3 structural point FV or directional adjustment pre-launch.

Use accepted direct FV as the R3 baseline. Keep structural bounds/relationships in a research monitor. See `IMPLEMENTATION_SPEC.md`.

### What should definitely not be used?

Do not deploy:

- raw Georgia joint point FV;
- Senate x House QP/KL point FV;
- House/Senate count point FV;
- raw structural residuals;
- calibrated seat ECM;
- historical scalar or CLR lead-lag;
- isolated favorable Senate timing rows;
- flow/hazard as a post-hoc R3 rescue.

## 15. Bottom line

R3 found a rich structural graph but **not a historical deployable structural alpha**.

That is still useful for launch: it prevents MAKE from being contaminated by an attractive but empirically bad fair-value model, preserves an untouched holdout, and tells the live-learning system exactly what remains worth measuring.

The live opportunity, if one exists, is now more narrowly stated:

> observe actual live Polymarket structural/order-book changes and test whether they precede SIG repricing with enough events, days and executable latency to matter.

That is a new live hypothesis. It is not a positive conclusion from R3-FV-001.
