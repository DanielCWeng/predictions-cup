# EXPERIMENT-004C-A — Information Propagation / Freshness

## Result

**Formal disposition: INCONCLUSIVE.**

The frozen 30-second full-grid test does **not** provide evidence that incremental source-market information survives the freshness/update controls in a way that meets the preregistered promotion standard.

That is not the same as proving that staleness explains all of 004B. The challenge set lacks usable PER_2026 replication under the frozen pair/coverage rules, and the standardized source-coefficient collapse is not consistently large across regimes.

## Primary A3 result

| Regime | 30s challenger vs baseline | Timing-null result | Economic gate |
| --- | ---: | --- | --- |
| PRE_ELECTION | MSE improvement `1.1127e-08`; aggregate relative MSE improvement `1.2479%`; RMSE gain `4.2300e-06` | Circular-shift q=`0.7968`; block-permutation q=`0.7968` | **FAIL** — RMSE gain is far below frozen `0.00025` threshold |
| ACTIVE_RESULTS | MSE delta `-1.0785e-07`; relative MSE improvement `-0.3524%`; RMSE gain `-1.0627e-05` | Circular-shift q=`0.1355`; block-permutation q=`0.1355` | **FAIL** — challenger is worse than baseline |

PRE's matched-unrelated A3 control produced a larger MSE improvement (`1.2614e-08`) than the real source (`1.1127e-08`). ACTIVE's matched-unrelated control is not fully comparable for Colombia first-round because it retained only ~31% of rows; no claim is made from that control.

## Hazard / mark decomposition

A1 and A2 are **observed-record update / timing diagnostics**. Their update timing is defined from observed valid `book_changes` records. The implementation does **not** reconstruct a deduplicated genuine-quote-renewal process that removes repeated observations of an unchanged BBO state.

In ACTIVE_RESULTS, these observed-record A1 update-timing and A2 conditional-mark diagnostics both beat the two stochastic timing nulls after BH correction (A1 q-values `0.0319` and `0.0159`; A2 q-values `0.0239` and `0.0239`).

This significance is **not evidence of genuine economic quote renewal, source-to-target excitation, or causal cross-market transmission**. Repeated same-state observations and archive/capture timing remain plausible explanations for the observed-record timing result.

These diagnostics did **not** translate into a positive A3 full-grid forecast: ACTIVE A3 was worse than baseline and did not survive the timing-null family. This is exactly why the preregistration made A3 the headline object.

PRE_ELECTION A1/A2 did not survive their timing-null families.

A deduplicated genuine-change renewal process is a separate unresolved mechanism. It should be tested only in a separately preregistered follow-up rather than inferred from EXPERIMENT-004C-A.

## How much of the 004B-style source association disappeared?

Using the preregistered standardized source-return coefficient comparison on discovery OOS data:

- PRE_ELECTION collapse: **45.43%**.
- ACTIVE_RESULTS collapse: **0.013%**.
- Simple mean across the two regimes: **22.72%**.

The collapse is therefore regime-dependent rather than a clean universal staleness explanation.

## Replication / coverage

The frozen challenge registry did not yield usable evidence from both COL_2026 and PER_2026 in either regime.

- Colombia first round: usable in PRE and ACTIVE.
- Colombia runoff: zero PRE feature rows; usable ACTIVE.
- Peru runoff: no PRE semantic pairs and zero ACTIVE feature rows.

Accordingly, the preregistered cross-family replication gate cannot pass. The historical challenge events remain labelled `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`, not globally pristine holdouts.

## Interpretation

The strongest defensible statement is:

> Simple 30-second source-to-target transmission is **not confirmed** after target history, quote age, update timing, common-event movement and activity/liquidity controls. The primary effect is economically negligible in PRE and negative in ACTIVE, and neither A3 regime beats the frozen timing nulls. Coverage limitations prevent a stronger conclusion that asynchronous staleness fully explains the 004B structure.

No longer horizon, subgroup, pair leaderboard or post-hoc threshold was used to rescue the primary result.

## Provenance

- Branch: `experiment/004c-a-information-freshness`
- Base: `4e094d13b06d9e558cbc404cc4d472316d7d5bbb`
- Terminal preregistration freeze: `4aafab4bd77d58fd82a510fa2b95842689f49ba2`
- Final empirical implementation: `5fb423fa2cc811d2ba58c236254ebe4c54fb5df8`
- Raw artifact commit: `3494dfee9202eab75b38292252b14baff81012d1`
- Kaggle kernel: `polyleviathan/sig-cup-exp004c-a-information-freshness`, version 7
- Kaggle output namespace: `004c_a_freshness`
- DATA-001 manifest SHA-256: `e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4`
- Raw outputs: `data/experiments/experiment_004c/a/kaggle_v7/`
- Raw artifact hash verification: **16/16 PASS**
- Declared stochastic null denominators: **250/250 in all 12 task/regime/null cells**
- Real order placement: **disabled**
