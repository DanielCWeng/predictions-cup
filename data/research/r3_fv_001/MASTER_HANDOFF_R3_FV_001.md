# MASTER HANDOFF — R3-FV-001 Structural Fair Value

## Status

**CLOSED — NO CONFIRMED INDEPENDENT STRUCTURAL SIGNAL. FINAL UNOPENED.**

Branch: `research/r3-fv-001-structural-fair-value`

Empirical scope:

- source: DATA-004 P0/P1;
- target: DATA-003 accepted direct/reference observations;
- DATA-001/historical analogue fitting: none;
- P2/P3 rescue: none;
- FINAL access: none.

## What changed from the earlier stop

The earlier Ridge/HGB `STOP_NO_INDEPENDENT_SIGNAL` was too broad as a programme conclusion. It was a valid generic screen on the correct DATA-004 -> DATA-003 universe, but it had not exercised the commissioned structural mathematics.

R3 was reopened and completed:

- assumption-free LP/partial-identification bounds;
- coherent QP projections;
- KL/I projections;
- MaxEnt point completion;
- corrected LOO-PRICE semantic eligibility;
- supported House/Senate count surfaces;
- corrected literal-next-update residuals;
- TRAIN-only affine calibration + error correction;
- scalar aggregate event-time lead-lag;
- full probability-distribution CLR lead-lag;
- P0 chronology/support audit.

The final conclusion remains negative, but it is now a defensible conclusion from the commissioned programme rather than a premature regression stop.

## Strongest findings

### Negative

1. **Broad LOO-FAMILY fails badly.**
   - persistence MAE 0.005506;
   - best blend-Ridge MAE 0.034401;
   - improvement -0.028895;
   - bootstrap interval entirely below zero.

2. **Priority coherent point FV fails.**
   - Georgia QP/KL worse than persistence;
   - Senate x House QP/KL dramatically worse.

3. **Supported seat-count FV fails.**
   - House point MAE ~0.069-0.073 vs persistence 0.00396;
   - Senate T50 point MAE ~0.013-0.016 vs persistence 0.00438.

4. **Corrected next-update residual fails.**
   - House QP/KL MAE improvement about -0.00030/-0.00032;
   - Senate T50 about -0.000081/-0.000063.

5. **TRAIN-only calibration does not rescue.**
   - calibrated levels remain worse;
   - calibrated error-correction remains worse.

6. **Historical machine-speed lead-lag lacks support.**
   - scalar seat and full-shape CLR tests have hundreds/thousands of source observations;
   - but almost no DEV direct-target fills inside the frozen 300s response window;
   - no frozen pair passes.

### Structural / research value retained

1. R2.5 semantic graph is useful.
2. LP bounds can expose underidentification and semantic failures.
3. Seven P0 groups have genuine TRAIN+DEV source/target support.
4. House/Senate count surfaces are the best live structural-monitor candidates.
5. The historical fill corpus is too sparse for a convincing machine-speed direct-target leadership conclusion.

## The tempting result that must NOT be promoted

Both scalar Senate QP/KL models improve the same single DEV response within 300 seconds.

Full-shape Senate KL also looks strongly positive on its single DEV response.

This is **not evidence of alpha**:

- one DEV row;
- one UTC day;
- support far below the frozen gate;
- CLR QP disagrees with CLR KL.

Treat as a reason to monitor live, not a reason to trade.

## FINAL

FINAL starts at `2026-09-10T00:00:00Z`.

`FINAL_FREEZE.json`:

- champion: null;
- challengers: [];
- DEV candidates passed: 0;
- final_opened: false;
- final_rows_accessed: 0.

`FINAL_RESULTS.json`: `NOT_RUN_NO_CHAMPION`.

Do not open R3 FINAL post hoc.

## MAKE decision

**No R3 structural directional FV goes into MAKE for launch.**

Use accepted direct FV. Keep structural graph/bounds and source-shock timing as monitor/research outputs only.

Do not use:

- Georgia structural point FV;
- Senate x House point FV;
- House/Senate seat point FV;
- structural residuals;
- calibrated ECM;
- historical structural lead-lag;
- isolated Senate timing rows;
- PRED-006 hazard as a directional R3 adjustment;
- flow as an R3 rescue.

See `IMPLEMENTATION_SPEC.md`.

## What to do live

The next valid experiment is not another historical rescue model.

Build/retain a live monitor for:

`Polymarket structural/order-book source move -> later SIG quote/trade move`

Focus first on:

- Republican House seat distribution;
- Republican Senate seat distribution;
- Senate x House joint distribution.

Measure actual observation-to-SIG latency at 1s / 2s / 5s / 10s / 30s / 300s, with enough independent events/days and executable markouts.

That live test is a **new hypothesis** and must not be described as confirmed by R3.

## Canonical R3 artefacts

- `DATASET_BINDING.json`
- `R3_SPLIT_MANIFEST.json`
- `METHOD_REGISTRY.json`
- `SEARCH_LEDGER.json`
- `STRUCTURAL_IDENTIFICATION.csv`
- `STRUCTURAL_BOUNDS.csv`
- `LOO_PRICE_RESULTS.csv`
- `LOO_FAMILY_RESULTS.csv`
- `DIRECT_PLUS_STRUCT_RESULTS.csv`
- `STABILITY_RESULTS.csv`
- `FALSIFICATION_RESULTS.csv`
- `FINAL_FREEZE.json`
- `FINAL_RESULTS.json`
- `IMPLEMENTATION_SPEC.md`
- `FINAL_REPORT.md`

## Review note

The research branch contains older broad CI lint/style debt from the DATA-004/R3 worktree. Kaggle scientific runs cited by the evidence artefacts completed successfully. Review scientific provenance/results separately from unrelated branch-wide Ruff debt.
