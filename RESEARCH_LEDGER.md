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

| 004C-A | Internal information propagation / freshness | `004C-A v7` | DATA-001 manifest `e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4` | `2782277a46ad3b2147a6e85788c85f2ce77405fd` | INCONCLUSIVE | Headline A3 did not establish incremental source-market transmission; observed-record timing is not genuine-renewal evidence | `data/experiments/experiment_004c/a/MASTER_HANDOFF_004C_A.md` | 2026-09-28 | Formal programme wording: INCONCLUSIVE / NO PROMOTED EDGE; demote generic pairwise lead-lag only |
| 004C-B | Structural / competitive-family redistribution | `004C-B Kaggle v3` | DATA-001 historical replay corpus + frozen family registry | `65bd8a81a7e4ef36e920df5e092a37b51cc00eb7` | INCONCLUSIVE | One narrow Colombia PRE soft competitive-family effect survived; no hard/exhaustive-family alpha promoted | `data/experiments/experiment_004c_b/review/MASTER_HANDOFF_004C_B.md` | 2026-09-28 | Formal lane disposition: SOFT_COMPETITIVE_EFFECT_ONLY; carry as narrow discovery, not general edge |
| 004C-C | Mapped Polymarket → SIG price discovery | `Kaggle v8 / d8d427af680a60467d92b242` | `data/experiments/experiment_004c/crossvenue/forward_snapshot_final_manifest.json` | `fbc8c2214969c8d9c7bd7afa7a7b20d248ecb742` | INCONCLUSIVE | Raw 30s EXACT improvement failed mapping-specific falsification; wrong-contract control stronger; executable crossing negative | `data/experiments/experiment_004c/crossvenue/MASTER_HANDOFF_004C_C.md` | 2026-09-28 | No promoted cross-venue edge; common event-state structure remains open |
| 004C-D | Conditional age/activity/genuine-renewal response | `004C-D Kaggle v4` | DATA-001 historical replay corpus + frozen D registry | `3a27d1d54f392b860358f950e558ff5f7f433b70` | REJECTED | Frozen D1/D2 mechanisms failed predictive-gain/sign gates in the adequately supported Colombia PRE cells | `data/experiments/experiment_004c_d/results/MASTER_HANDOFF_004C_D.md` | 2026-09-28 | Rejection is specification-limited; Peru runoff PRE was untestable and broader state-dependent models remain open |

BUILD-008 synthetic fixtures are machinery tests only and are intentionally not entered as empirical
alpha experiments.

## Adding a subsequent experiment

1. Declare the research/evaluation specification before inspecting final holdout results.
2. Bind the exact dataset manifest SHA-256 and code revision into the deterministic run ID.
3. Save the compact canonical report in the experiment's evidence path.
4. Add one row here for every completed empirical run selected for a research decision.
5. Preserve rejected/inconclusive rows permanently; superseding work adds a new row rather than
   rewriting history.
