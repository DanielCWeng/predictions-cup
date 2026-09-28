# MASTER HANDOFF — EXPERIMENT-005E

**Status:** COMPLETE / NO_INCREMENTAL_EVIDENCE  
**Branch:** experiment/005e-participant-ecology-atlas  
**Primary target:** y_60  
**Frozen DEV spec:** 60f642ec50a1a9ffa5aebfba73d8a66960f6afb2c318e7d747e584df81ca6de1

## Bottom line

The preregistered participant-ecology hypothesis does not survive sealed HOLDOUT. DEV selected specialisation + cross-market breadth, but HOLDOUT weighted MSE gain is 0.000124837276195 with p=0.776, q=1.0. Full behaviour, identity-history, HGB and archetypes are negative versus baseline.

There is **no 005E participant-conditioned 60-second price alpha to deploy**.

## What is interesting

Secondary frozen targets deserve a new experiment, not promotion:
- signed 60s markout: gain 0.00924532001477, positive in all five families;
- signed 300s markout: gain 0.0123103062455, positive in all five families;
- next-change classifier: log-loss gain 0.0106166562552, accuracy 0.584708 -> 0.604946.

Interpretation: participant state may be more useful for **quality-weighting an observed participant action** than for unconditional price forecasting.

## Review anchors

- data/experiments/experiment_005e/preregistration.json
- data/experiments/experiment_005e/pre_holdout_freeze.json
- data/experiments/experiment_005e/results/train_dev/dev_selection_report.json
- data/experiments/experiment_005e/results/holdout/holdout_evaluation.json
- data/experiments/experiment_005e/results/holdout/falsification_concentration_report.json
- data/experiments/experiment_005e/results/candidate_list.json
- data/experiments/experiment_005e/results/FINAL_REPORT_005E.md

## Provenance

TRAIN/DEV and raw HOLDOUT panel construction ran on Kaggle. The two full HOLDOUT kernels were killed after panel construction but before evaluation output; no results from them were inspected. Their byte-verified panel Parquets were promoted to a private Kaggle dataset and evaluated by a separate column-pruned Kaggle kernel. Final sealed evaluation disposition emitted by Kaggle: NO_INCREMENTAL_EVIDENCE.

Do not merge until independent review is complete.