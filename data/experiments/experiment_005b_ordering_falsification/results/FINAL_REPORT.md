# EXPERIMENT-005B Ordering Falsification — Final Report

**Classification:** POST_HOC_FALSIFICATION_ONLY  
**Parent:** PR #45 / EXPERIMENT-005B  
**Interpretation constraint:** corrected HOLDOUT evidence may demote or falsify parent findings. It cannot promote, reconfirm, tune, replace, or introduce candidates.

## Ordering provenance and correction

The parent reconstruction used the pseudo-order `timestamp, tx_hash, log_index`. Canonical observable ordering is the upstream Polygon order `block_number, log_index`.

The archival Polygon hard gate passed across all five historical families:

- 10,071,855 economic rows
- 8,236,688 distinct transactions
- 3,222,324 distinct Polygon blocks
- 0 missing block numbers
- 0 missing block timestamps
- 0 timestamp disagreements
- 0 duplicate `(block_number, log_index)` groups
- 0 distinct blocks sharing a timestamp

Because the audited block timestamp is one-to-one and monotone with `block_number` over the covered observations, the corrected reconstruction's `timestamp, log_index` sequence is sequence-equivalent to `block_number, log_index`; `tx_hash` is retained only as an impossible-tie guard after `log_index`. Scientific ordering is therefore bound to the upstream block/log evidence, not inferred from lexical transaction hashes.

Source identifiers, per-family transaction/block-map checksums and coverage are recorded in `results/actual_block_number_hard_gate.json`.

## Contamination magnitude and cause

The old pseudo-order was materially different from true observable order:

- 8,422,758 / 10,071,855 rows (**83.63%**) share a family timestamp/block with another row.
- 7,572,449 / 10,071,855 rows (**75.18%**) are in same-block groups containing multiple transactions.
- 850,309 rows (**8.44%**) are tied within single-transaction groups; `log_index` already preserves those within-transaction sequences.
- 6,030,418 rows (**59.87%**) change family-order rank.
- 4,748,683 rows (**47.15%**) change event-order rank.
- 3,093,800 rows (**30.72%**) change market-order rank.

The defect is therefore specifically cross-transaction chronology inside a shared block timestamp: the old key sorted different transactions lexically by `tx_hash` before considering their global block `log_index`. Multiple logs inside one transaction are not the primary defect.

The exposure is strongest in the highest-collision family. Family-order rank changes are US_2024 **64.46%**, PER_2026 **46.98%**, HUN_2026 **46.92%**, COL_2026 **38.92%**, and CAN_2025 **37.77%**. Multi-transaction same-block exposure is US_2024 **80.84%**, PER_2026 **59.32%**, HUN_2026 **58.67%**, CAN_2025 **51.03%**, and COL_2026 **46.22%**.

After rebuilding the unchanged 226-feature / 47-target specification:

- **8,953,382 rows (88.90%)** have at least one changed feature value.
- **5,239,927 rows (52.03%)** have at least one changed target value.
- **2,722,969 rows (27.04%)** have at least one changed label endpoint.
- 722,178,421 feature observations changed.
- 77,532,560 target observations changed.
- 8,487,461 label-endpoint observations changed.

A separate diagnosis-only audit measured the exact frozen finding evaluation populations. Across the seven realised-movement findings, **28,917,151 / 43,504,528 row-target evaluations (66.47%)** were affected by changed evaluation membership, selected-model inputs, target values, or label endpoints. Across the six reported event-time findings, **28,122,302 / 48,395,989 (58.11%)** were affected. The permanent compact evidence is `results/finding_evaluation_impact_summary.json`.

This is a substantive falsification test, not a cosmetic reorder.

## Seven clock realised-movement findings

All seven original realised-movement findings **survive materially** under the corrected observable ordering. This means only that the prior findings were not falsified by the ordering correction; it is not fresh confirmation.

| Horizon | Parent MAE improvement vs persistence | Corrected MAE improvement | Change | Direction change? | Corrected affected evaluation rows | Classification |
|---:|---:|---:|---:|:---:|---:|---|
| 1s | 51.70% | 50.96% | -0.74 pp | No | 3,330,240 / 4,230,634 (78.72%) | **Survives materially** |
| 5s | 48.47% | 47.08% | -1.39 pp | No | 3,825,513 / 5,279,352 (72.46%) | **Survives materially** |
| 15s | 48.04% | 48.27% | +0.23 pp | No | 4,077,198 / 6,093,501 (66.91%) | **Survives materially** |
| 30s | 39.60% | 38.99% | -0.61 pp | No | 4,200,050 / 6,506,868 (64.55%) | **Survives materially** |
| 60s | 29.92% | 30.11% | +0.19 pp | No | 4,330,325 / 6,845,106 (63.26%) | **Survives materially** |
| 120s | 20.99% | 21.07% | +0.08 pp | No | 4,463,951 / 7,116,061 (62.73%) | **Survives materially** |
| 300s | 13.60% | 13.72% | +0.12 pp | No | 4,689,874 / 7,433,006 (63.10%) | **Survives materially** |

Additional frozen survival diagnostics remain positive for all seven: each beats every named HOLDOUT MAE baseline, each has a positive hierarchical-bootstrap lower 2.5% bound, and each is positive in all five historical families. Corrected predictive IC ranges from 0.366 to 0.621.

No candidate, feature, target, model class, hyperparameter, threshold, horizon, sampling rule, shortlist entry, multiple-testing rule or decision rule was changed.

## Event-time directional findings

All original promoted event-time scalar candidates retain their TRAIN-sign direction on corrected HOLDOUT; no promoted scalar direction reverses. Model-level evidence is nevertheless mixed:

| Finding | Corrected-order result | Falsification disposition |
|---|---|---|
| event sign +1 fill | Accuracy 56.03%; does not beat every named accuracy baseline; bootstrap lower bound -0.00320 | **Materially weakened / not robust** |
| event sign +2 fills | Accuracy 54.66%; beats every named accuracy baseline; lower bound +0.00510 | **Directionally intact; not falsified** |
| event sign +5 fills | Accuracy 49.77%; beats every named accuracy baseline; lower bound +0.02624 | **Directionally intact; not falsified** |
| event sign +10 fills | Accuracy 47.50%; beats every named accuracy baseline; lower bound +0.03984 | **Directionally intact; not falsified** |
| event price change +5 fills | MAE improvement vs persistence -0.065%; bootstrap lower bound negative | **Fails / prior model claim disappears** |
| event price change +10 fills | MAE improvement vs persistence +0.450%; beats every named MAE baseline; positive lower bound, but not positive in all five families | **Aggregate result survives; portability materially weakened** |

The broad event-time claim therefore **partially survives but is narrowed**: three sign horizons remain directionally intact, +1 sign is not robust, +5 price-change fails, and +10 price-change remains aggregate-only. None is eligible for R1 transfer.

## R1 transfer freeze

Because the realised-movement family survives materially, Outcome B applies.

The governing pre-existing transfer freeze is:

`data/experiments/experiment_005b_ordering_falsification/data_003_confirmation_protocol.json`

It was present at PR #51 head `faffc01ed6b4438e625fdc9a784dba3a20e65757`, which is the exact base SHA from which the downstream R1 branch was created. No DATA-003 confirmation result was used to select or alter that protocol.

For audit readability, `r1_transfer_resolution.json` mechanically expands the immutable references in that governing freeze into the exact seven targets, feature lists, model classes, hyperparameters, preprocessing, 300s embargo, deterministic historical fit sampling, named baselines, bootstrap seeds, success/failure rule, exclusions, and multiple-testing treatment. It is explicitly non-governing and cannot rewrite the earlier freeze.

The R1 package remains exactly the seven clock realised-movement horizons. No event-time candidate is transferred.

## Reproducibility and preservation

Original PR #45 artefacts remain untouched under their original paths. Corrected artefacts are namespaced beneath `data/experiments/experiment_005b_ordering_falsification/` and are explicitly post-hoc.

Key reproducibility evidence:

- original final head: `232c7c396edd18c93df72b309235110a89b5143a`
- original shortlist freeze SHA256: `44025aa2c5e5bddd97d88c81e649d86d6bf55d6cccec1d57c2aa7af12a9a7ec6`
- original HOLDOUT result SHA256: `94db3e2319d376078e9f80f5e28d04215dcade53d76c1ec749af8bcc0fa8972a`
- original evaluator reference commit: `76a52913f3a38a7c573ee7f9f53db6d651f301b1`
- corrected evaluator equivalence: `evaluator_equivalence.json`
- corrected stage-2 no-design-drift audit: `stage2_equivalence_audit.json`
- upstream block provenance: `results/actual_block_number_hard_gate.json`
- ordering exposure: `results/ordering_exposure_rank_audit.json`
- finding-row impact audit: `results/finding_evaluation_impact_summary.json`
- final corrected scientific summary: `results/ordering_falsification_summary.json`

Execution followed the canonical path `branch -> kaggle/jobs/*.json -> GitHub Actions -> Kaggle -> outputs/artifacts -> GitHub`. The runner uses Ubuntu + Python 3.12; scientific kernels are Python CPU kernels with GPU/TPU/internet disabled. The frozen evaluator uses seed `505005`, 1,000 bootstrap replicates and the original model hyperparameters. Exact job/kernel paths are committed beside the results. No EC2 compute was used for this completion pass.

## Final scientific disposition

- Movement horizons surviving post-hoc ordering falsification: **7 / 7**
- Event-time evidence: **partially survives / narrowed**
- New candidates introduced: **0**
- Original PR #45 artefacts overwritten: **No**
- 005B closed as ordering-contaminated discovery: **No**
- Eligible for fresh R1 confirmation under the pre-frozen seven-horizon transfer package: **Yes**
- DATA-003 used to make or alter this ordering-correction conclusion: **No**
