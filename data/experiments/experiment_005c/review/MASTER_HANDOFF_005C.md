# EXPERIMENT-005C — MASTER HANDOFF

Disposition: REGIME-SPECIFIC JOINT CANDIDATE
Frozen design: ea7ed06dd47cb8240cc8a1e5dedabf00d009843f / prereg b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5
Implementation: 94512d3c9b785efcbf04bcabc5c57c416e5ad524
Pre-HOLDOUT freeze SHA-256: 20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933

## Reconstruction

- US_2024: 4,468,573 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- CAN_2025: 297,906 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- COL_2026: 239,771 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- HUN_2026: 260,802 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- PER_2026: 346,114 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.

## Search / selection

- Eligible metadata/coverage-defined panels: 45.
- DEV model rows evaluated: 16006.
- Frozen DEV-promoted joint selections: 35.
- HOLDOUT evaluations: 35.

## Headline

Strongest HOLDOUT row by pooled MSE improvement: M2 on US_2024_event_6e91434eb80f at grid 15s / horizon 15s; pooled improvement vs B2 0.065207, median target improvement 0.041804, target breadth 1.000, effective sources 4.84.

## Interpretation rules

- B2 is the frozen own-history + leave-target-out common-state baseline.
- Missing/quiet returns are never treated as observed zero; zero imputation occurs only after an explicit mask is carried.
- HOLDOUT was not scored until PRE_HOLDOUT_FREEZE.json existed and had been hashed.
- No fee, participant, wallet, book-microstructure, execution or P&L variables enter 005C.
- Probability-space robustness, source concentration, ablations and block uncertainty are separate outputs.

## Outputs

- reconstruction_audit.csv
- panel_coverage.csv
- dev_model_candidates.csv
- train_singular_spectra.json
- PRE_HOLDOUT_FREEZE.json
- holdout_results.csv
- probability_coordinate_robustness.csv
- ablation_null_results.csv
- run_manifest.json
