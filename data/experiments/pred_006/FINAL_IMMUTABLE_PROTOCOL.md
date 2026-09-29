# PRED-006 — Immutable One-Shot FINAL Protocol

**Frozen before FINAL predictive outcomes are accessed.**

This protocol is subordinate to `PRED_006_PROTOCOL.md` and the committed Phase-0 split
`data/experiments/pred_006/phase0/split_manifest.json`
(SHA-256 `28d41fbb590f7f9abe59ca24e00841307fe13991c3c695748240fe127847f349`).

## FINAL boundary

- FINAL begins: `2026-09-11T00:00:00Z`.
- Canonical order: `block_number, log_index`.
- Pre-FINAL target-bearing fitting data must end before `FINAL_START - 1800 seconds`.
- The FINAL evaluator may read FINAL predictive targets only after the shortlist freeze exists.
- If the shortlist contains zero candidates, FINAL predictive outcomes remain unopened.

## Refit rule

For each frozen shortlisted candidate, fit the exact frozen model, preprocessing, feature list,
target, horizon and hyperparameters once using every supported target-eligible pre-FINAL
observation before the 1,800-second purge boundary. TRAIN and DEV may therefore both enter this
single final refit. No feature selection, horizon selection, market filtering, participant
selection, threshold selection or hyperparameter tuning occurs in this refit.

All learned imputers/scalers are fit only on that pre-FINAL refit population.

## Baselines

The baseline set is frozen by candidate family before FINAL:

- regression candidates: target-appropriate trivial baseline plus the frozen own-history Ridge;
- hazard candidates: pre-FINAL base-rate baseline plus the frozen own-history logistic model.

For the hostile FINAL comparison, the candidate is compared with the **lowest-loss member of the
frozen baseline set on FINAL**. This is deliberately conservative and does not alter the candidate.

## FINAL support

Evaluation rows must:

- start at or after FINAL start;
- have a fully supported target endpoint within the same DATA-003 capture window;
- preserve the all-market/mapping scope selected before FINAL;
- never be removed because their result is inconvenient.

Markets/conditions absent from FINAL remain absent and are reported.

## Metrics and uncertainty

Regression primary loss: MAE. Report RMSE, rank correlation, calibration slope/intercept and,
for signed-delta targets, directional accuracy as diagnostics.

Hazard primary loss: Brier score. Report log loss, AUC when defined and calibration bins as
diagnostics.

For every candidate, compute per-condition mean primary-loss improvement versus the strongest
frozen baseline and a condition-cluster bootstrap with 1,000 repetitions.

Also report:

- improvement in first and second chronological halves of FINAL;
- fraction of conditions with positive loss improvement;
- concentration of positive condition gain, including the top-decile share;
- mapping-class/market stability where supported;
- row/condition/market support and failure modes.

## Frozen pass rule

A candidate survives only if every condition below holds:

1. relative primary-loss improvement versus the strongest frozen baseline is at least 1%;
2. condition-cluster bootstrap 2.5% lower bound of loss improvement is above zero;
3. first-half loss improvement is above zero;
4. second-half loss improvement is above zero;
5. at least 55% of evaluated conditions have positive loss improvement;
6. the top decile of positively contributing conditions accounts for no more than 75% of total
   positive condition gain.

Any computational invalidity or insufficient support is a fail, not a reason to redefine the test.

## Stop rule

After the one-shot FINAL evaluation, stop. No horizon change, mapping-class subset, bad-period
removal, feature change, model retune, threshold change or relabelling is allowed.

Any idea inspired by FINAL is explicitly a future-data hypothesis.

Survivors are labelled
`CURRENT_UNIVERSE_CANDIDATE_REQUIRES_FUTURE_CONFIRMATION`; otherwise the experiment ends
`TERMINAL_NULL`.
