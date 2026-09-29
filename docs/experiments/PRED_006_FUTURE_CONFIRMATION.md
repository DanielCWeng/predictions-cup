# PRED-006 — Future CAPTURE-001 Confirmation Protocol

This protocol applies only to frozen survivors `PRED006-C01` and `PRED006-C02`.

## Purpose

Test whether the PRED-006 movement-hazard result transfers to genuinely future live data. This is confirmation, not a new search phase.

## Frozen candidates

- C01: 1,800-second next-price-change hazard; HGB `max_leaf_nodes=15`.
- C02: 600-second next-price-change hazard; HGB `max_leaf_nodes=7`.
- Exact feature definitions: `data/experiments/pred_006/shortlist/candidate_registry.json`.
- Plugin contract: `data/experiments/pred_006/final/make_plugin_spec.json`.

No feature, horizon, market, participant, threshold, model, hyperparameter or missingness changes are permitted based on future outcomes.

## Observable-time parity gate

Before scoring outcomes, verify that every feature is observable at the signal timestamp with semantics equivalent to DATA-003. In particular, fee-evidence fields must be available without future custody information or delayed reconciliation leakage.

If parity fails, classify the candidate `NOT_LIVE_REPRODUCIBLE_AS_FROZEN`; do not silently substitute or drop features.

## Refit

Before the future confirmation window begins, refit each exact frozen specification using historical DATA-003 only. All preprocessing/imputation is fit on historical data only. Once the future confirmation window starts, model parameters remain fixed.

## Future observation and targets

- Observation unit: condition-block-end.
- Chronology: Polygon `block_number, log_index`.
- Target begins strictly after the current block.
- C01: any canonical YES-price change within 1,800 seconds.
- C02: any canonical YES-price change within 600 seconds.
- Score a row only once its full future horizon is observable.

## Baselines and metrics

Preserve the frozen base-rate and own-history logistic baselines. Compare against the lower-Brier baseline on the future confirmation sample.

Primary metric: Brier score. Also report log loss, AUC, condition-cluster bootstrap, chronological halves, fraction of conditions positive, concentration, mapping/SIG-market stability and calibration.

Use the same frozen pass rule:
1. relative Brier improvement >= 1%;
2. condition-cluster bootstrap 2.5% lower bound > 0;
3. both chronological halves positive;
4. at least 55% of conditions positive;
5. top decile <= 75% of total positive condition gain.

Require at least 300 supported rows and 25 conditions. Below that, report `INSUFFICIENT_FUTURE_SUPPORT` and collect more data without changing the model.

## Decision states

- `FUTURE_CONFIRMED_CANDIDATE`: all frozen criteria pass.
- `FUTURE_CONFIRMATION_FAILED`: sufficient support, one or more criteria fail.
- `INSUFFICIENT_FUTURE_SUPPORT`: not enough future support yet.
- `NOT_LIVE_REPRODUCIBLE_AS_FROZEN`: observable-time feature parity fails.

Only `FUTURE_CONFIRMED_CANDIDATE` may proceed to a separate MAKE integration decision. Confirmation itself does not authorize live trading.
