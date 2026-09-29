# EXPERIMENT-005B Ordering Falsification — Final Report

**Classification:** POST_HOC_FALSIFICATION_ONLY  
**Parent:** PR #45 / EXPERIMENT-005B  
**Interpretation constraint:** corrected HOLDOUT evidence may demote or falsify parent findings. It cannot promote, reconfirm, or introduce candidates.

## Ordering correction

The parent reconstruction used `timestamp, tx_hash, log_index`. The corrected reconstruction uses the actual upstream Polygon ordering `block_number, log_index`.

The actual block-number hard gate passed for all five families:

- 10,071,855 economic rows
- 8,236,688 distinct transactions
- 3,222,324 distinct Polygon blocks
- 0 missing block numbers
- 0 missing block timestamps
- 0 timestamp disagreements
- 0 duplicate `(block_number, log_index)` groups
- 0 distinct blocks sharing a timestamp

PR #45 outputs remain unchanged.

## Contamination magnitude

The old pseudo-order was materially different from true observable order:

- 8,422,758 rows share a family timestamp/block with another row.
- 7,572,449 rows are in family-level same-second groups containing multiple transactions.
- 6,030,418 rows change family-order rank.
- 4,748,683 rows change event-order rank.
- 3,093,800 rows change market-order rank.

After rebuilding the unchanged 226-feature / 47-target specification:

- **8,953,382 rows (88.90%)** have at least one changed feature value.
- **5,239,927 rows (52.03%)** have at least one changed target value.
- **2,722,969 rows (27.04%)** have at least one changed label endpoint.
- 722,178,421 feature observations changed.
- 77,532,560 target observations changed.
- 8,487,461 label-endpoint observations changed.

This is therefore a substantive falsification test, not a cosmetic reorder.

## Seven clock realised-movement findings

All seven original models are **not falsified by the ordering correction** under the frozen survival rule.

| Horizon | Parent MAE improvement vs persistence | Corrected MAE improvement | Corrected predictive IC | Corrected hierarchical bootstrap lower 2.5% | Beats every named baseline | Positive in all 5 families |
|---:|---:|---:|---:|---:|:---:|:---:|
| 1s | 51.70% | 50.96% | 0.366 | 0.000732 | Yes | Yes |
| 5s | 48.47% | 47.08% | 0.537 | 0.000856 | Yes | Yes |
| 15s | 48.04% | 48.27% | 0.519 | 0.001192 | Yes | Yes |
| 30s | 39.60% | 38.99% | 0.549 | 0.001638 | Yes | Yes |
| 60s | 29.92% | 30.11% | 0.576 | 0.002166 | Yes | Yes |
| 120s | 20.99% | 21.07% | 0.597 | 0.002985 | Yes | Yes |
| 300s | 13.60% | 13.72% | 0.621 | 0.004119 | Yes | Yes |

The ordering correction does **not** materially collapse the movement result. No new feature, target, model, hyperparameter, threshold, candidate, or shortlist entry was introduced.

**Disposition:** carry the unchanged seven-horizon realised-movement hypothesis forward for fresh confirmation on DATA-003 / the actual mapped 2026 SIG universe.

## Event-time directional findings

The corrected event-time scalar relationships retain their original direction: all original promoted scalar candidates for the six prespecified event-time targets keep the TRAIN-sign direction on corrected HOLDOUT.

Model-level evidence remains mixed:

| Target | Corrected result | Verdict |
|---|---|---|
| event sign +1 fill | Accuracy 56.03%; does not beat every named accuracy baseline; bootstrap lower bound -0.00320 | **Not robust / not carried forward** |
| event sign +2 fills | Accuracy 54.66%; beats every named accuracy baseline; bootstrap lower bound +0.00510 | **Not falsified by ordering correction** |
| event sign +5 fills | Accuracy 49.77%; beats every named accuracy baseline; bootstrap lower bound +0.02624 | **Not falsified by ordering correction** |
| event sign +10 fills | Accuracy 47.50%; beats every named accuracy baseline; bootstrap lower bound +0.03984 | **Not falsified by ordering correction** |
| event price change +5 fills | MAE improvement vs persistence -0.065%; bootstrap lower bound negative | **Falsified / weakened** |
| event price change +10 fills | MAE improvement vs persistence +0.450%; beats every named MAE baseline; bootstrap lower bound positive, but family-level improvement is not positive in all five families | **Aggregate signal survives, cross-family portability remains weak** |

These event-time results are reported only as post-hoc falsification diagnostics. They are **not** added to the DATA-003 carry-forward set.

## Final disposition

- Movement horizons surviving: **7 / 7**
- New candidates introduced: **0**
- Original PR #45 overwritten: **No**
- 005B closed as ordering-contaminated discovery: **No**
- DATA-003 fresh confirmation required: **Yes**
- DATA-003 carry-forward set: **the unchanged seven clock realised-movement horizons only**

DATA-003 remains a fresh confirmation dataset. This corrected historical HOLDOUT cannot be used to promote or reconfirm the hypothesis; it only failed to falsify it.
