# EXPERIMENT-004C-A — Information Propagation / Freshness

## Frozen question

Does source-market information observable at time `t` improve the next 30-second target forecast after controlling for target self-movement, actual quote age, update timing, target liquidity/activity and a target-and-source-excluded common-event factor?

This is a sceptical identification battery. A failure is informative: it means the 004B 30-second directed structure is primarily consistent with asynchronous catch-up rather than incremental source information.

## Evidence and provenance

The branch is based on `main@4e094d13b06d9e558cbc404cc4d472316d7d5bbb`, after PR #30 and PR #33 were merged. The frozen 004B implementation remains `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`; the frozen 004B discovery-spec SHA-256 remains `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`.

004B found aggregate directed 30-second price structure beyond circular/block nulls in Hungary PRE, Peru PRE and Peru ACTIVE, but not Hungary ACTIVE. Individual edges were never FDR-confirmed. Peru ACTIVE coverage was sparse, depth-update activity may partly reflect archive cadence, and PRE/ACTIVE edge stability was weak. Those are reasons to control timing and freshness, not reasons to pick leaders.

The canonical Opus, Astra and Sol 004C artifacts under `data/experiments/experiment_004c/research/` were read before freeze. Astra is explicitly prior-only/paused; no missing post-004B Astra result is fabricated.

## Frozen pair universe

No pair is selected using 004B effect size. `scripts/build_experiment_004c_a_metadata.py` constructs the registry from the accepted EXPERIMENT-003 relationship inventory using only `MANUAL_INDIRECT`, `INDIRECT`, `LEADLAG` relationships. Discovery pairs must also be admitted by frozen 004B for the same event/regime. Challenge pairs must satisfy the identical metadata rule plus 004A.2 usability.

The three challenge events are labelled `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`. They are locked post-preregistration challenge data, not globally pristine holdouts.

## Model

The baseline contains only target/event information. The common-event factor excludes both the target and the candidate source, so source information cannot leak into the baseline through the factor.

The challenger adds source innovation, source freshness/update timing, source activity/liquidity and the prespecified `source_return × relative_staleness` interaction. Continuous age variables are primary; there is no tuned stale/fresh threshold.

All transformations are fit on training data only. Models are deliberately simple and fixed: ridge-penalized logistic regression for update hazard and ridge regression for conditional mark/full-grid response. There is no hyperparameter search.

## Decomposition and headline

A1 predicts whether the target receives a valid unambiguous BBO update within 30 seconds. A2 predicts signed movement at the first such update and is explicitly conditional/selection-biased. A3 predicts the actual 30-second full-grid state change, including zero movement when the target does not update. A3 is the headline object.

The primary wall-clock horizon is 30 seconds. 60 and 120 seconds are frozen robustness checks only and cannot rescue failure at 30 seconds. Event-time/next-update results remain secondary.

## Falsification

Four frozen source nulls are used: circular time shift, 300-second block time permutation, matched unrelated market replacement, and a 300-second delayed-source placebo. Each requires 250 valid draws where a distribution is defined; denominator shortfalls fail closed and are reported.

BUILD-008 supplies run identity semantics, BH FDR and deterministic block/event bootstrap machinery. PRE and ACTIVE are separate. Row count is never treated as independent election evidence.

## Disposition

`TRANSMISSION_SURVIVES` requires meaningful A3 improvement and survival against the timing nulls on locked challenge evidence with support spanning both COL_2026 and PER_2026. `STALENESS_EXPLAINS_STRUCTURE` is a valid success state if the naive source association collapses and controlled source information does not beat the baseline/nulls. Otherwise the battery is `INCONCLUSIVE`.

The machine-readable source of truth is `data/experiments/experiment_004c/a/preregistration.json`. Challenge outcomes must not be inspected before the freeze commit containing that file plus the frozen metadata registry.
