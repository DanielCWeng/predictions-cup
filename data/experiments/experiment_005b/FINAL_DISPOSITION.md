# EXPERIMENT-005B — Final Disposition

**Status:** CLOSED  
**Final programme disposition:** HISTORICAL MOVEMENT RELATIONSHIP SURVIVES ORDERING FALSIFICATION; FRESH DATA-003 TRANSFER FAILS 0/7  
**Current SIG-universe alpha claim:** NONE

## Scientific chain

1. **Original historical atlas — PR #45.** The sealed historical HOLDOUT produced strong realised-movement results across seven clock horizons, but its reconstruction used the pseudo-order `timestamp, tx_hash, log_index`.
2. **Ordering correction — PR #51.** Canonical Polygon ordering was reconstructed from `block_number, log_index`. Contamination was material: 59.87% of rows changed family-order rank, 88.90% had at least one changed feature and 52.03% had at least one changed target. The original PR #45 artefacts were preserved.
3. **Post-hoc falsification result.** All seven realised-movement findings survived materially under corrected historical ordering. This evidence is `POST_HOC_FALSIFICATION_ONLY`; it does not constitute fresh confirmation.
4. **Frozen R1 transfer.** Exactly the seven historical movement models were carried forward. Model classes, hyperparameters, feature lists, horizons, baselines and no-reselection/no-tuning/no-subsetting rules were frozen before DATA-003 predictive evaluation.
5. **Fresh DATA-003 confirmation — PR #52.** The pre-evaluation gate passed: 132,928 participant rows; 55,302 transaction-condition groups; 55,274 accepted groups; 77,554 economic fills; 43,222 Polygon blocks; zero missing block numbers/timestamps; zero timestamp mismatches; zero duplicate `(block_number, log_index)` groups.
6. **Fresh transfer result.** **0 / 7 horizons pass.** MAE improvement versus persistence is negative at every horizon and every condition-cluster bootstrap 95% interval versus persistence is entirely below zero.

## Final interpretation

005B identified a historically persistent movement-state relationship, but the frozen historical predictive models do **not** transfer to the accepted DATA-003 mapped 2026 SIG universe. They must not be treated as current-universe predictive alpha or carried into live SIG decision-making.

Positive predictive IC on DATA-003 and the small positive NEAR diagnostic do not rescue the failure. Mapping-class diagnostics were non-selection diagnostics and subsetting was prohibited.

No event-time result transfers as a rescue. No retuning, replacement model, replacement feature, replacement horizon or post-hoc candidate is authorised under 005B.

## Canonical evidence

Historical record:
- `data/experiments/experiment_005b/results/`

Ordering falsification:
- `data/experiments/experiment_005b_ordering_falsification/results/FINAL_REPORT.md`
- `data/experiments/experiment_005b_ordering_falsification/results/actual_block_number_hard_gate.json`
- `data/experiments/experiment_005b_ordering_falsification/results/ordering_falsification_summary.json`

Fresh DATA-003 confirmation:
- `data/experiments/experiment_005b_data003_confirmation/protocol.json`
- `data/experiments/experiment_005b_data003_confirmation/block_gate_report.json`
- `data/experiments/experiment_005b_data003_confirmation/confirmation_summary.json`
- `data/experiments/experiment_005b_data003_confirmation/FINAL_REPORT.md`

This closeout does not alter the underlying #45, #51 or #52 scientific evidence; it consolidates their final accepted interpretation.
