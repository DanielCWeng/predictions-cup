# Predictions Cup — Research Ledger

> Durable research-control record. This ledger records empirical research outcomes; it is not a
> leaderboard and it must retain rejected and inconclusive hypotheses.

## Status vocabulary

- `PLANNED`: specified but not yet run.
- `RUNNING`: evaluation in progress.
- `PROMOTED`: explicitly accepted for the next research/implementation stage after review.
- `REJECTED`: tested and rejected under its declared falsifier/evidence gate.
- `INCONCLUSIVE`: evaluation completed without enough evidence to promote or reject.

`REJECTED` and `INCONCLUSIVE` are successful research outcomes and must not be deleted.

## Canonical entries

| Experiment ID | Hypothesis family | Run ID | Data version | Code revision | Disposition | Headline evidence | Report path | Decision date | Notes |
|---|---|---|---|---|---|---|---|---|---|

BUILD-008 synthetic fixtures are machinery tests only and are intentionally not entered as empirical
alpha experiments.

## Adding a subsequent experiment

1. Declare the research/evaluation specification before inspecting final holdout results.
2. Bind the exact dataset manifest SHA-256 and code revision into the deterministic run ID.
3. Save the compact canonical report in the experiment's evidence path.
4. Add one row here for every completed empirical run selected for a research decision.
5. Preserve rejected/inconclusive rows permanently; superseding work adds a new row rather than
   rewriting history.
