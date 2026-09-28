# EXPERIMENT-005B-DATA003-TRANSFER — BLOCKED

Status: **BLOCKED — WAITING FOR FINAL ACCEPTED 005B**

This branch is scaffolded only. No DATA-003 transfer evaluation has been run.

## Dependency gate

R1 requires the final accepted EXPERIMENT-005B, including the causal-ordering correction, to be accepted on `main`.

Observed at scaffold time:

- base `main`: `bbd152eb13f827b9ede36e444ac597eb0274443d`
- PR #45 (`EXPERIMENT-005B — Historical price / fill predictive atlas`) is open, draft, and unmerged
- PR #45 is explicitly blocked pending causal-order falsification
- latest orchestrator follow-up reports the remediation implementation has been reviewed but empirical ordering-audit / corrected falsification results are not yet committed
- therefore there is no exact final accepted 005B parent SHA or final carried-forward candidate set to freeze for R1

## Deliberately not created/run

Until the dependency gate clears, this branch must not add or execute:

- `protocol.json`
- DATA-003 block-number enrichment
- DATA-003 economic-fill reconstruction
- frozen feature/target generation
- historical-model reproduction
- transfer evaluation
- Kaggle job manifests or transfer kernels

No DATA-003 outcomes have been used for model selection, tuning, candidate selection, or evaluation in this lane.

## Resume condition

Resume only after final corrected 005B is accepted on `main` and the exact final carried-forward findings/models can be identified and hash-bound.
