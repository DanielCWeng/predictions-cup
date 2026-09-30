# CANDIDATE-RUNTIME-001 runbook

## Purpose

Operate and diagnose the frozen PRED-006 / EXPERIMENT-005F SHADOW evaluators.
These providers are observation-only. They have no SIG write client and do not
alter MAKE.

## Expected launch state

Immediately after deployment, inspect SHADOW decisions for:

- `pred-006` -> `NOT_READY:model_artifact_missing`
- `experiment-005f-hazard` ->
  `NOT_READY:required_orderbook_history_unavailable`

Those are healthy fail-closed states with the currently committed dependencies.
Do not convert either reason to a proxy feature or ad-hoc model.

Each decision payload should include `research_id`, `frozen_spec_version`,
`feature_schema_hash`, artifact identity fields, readiness, and freshness when
available.

## Pre-deploy checks

Run:

```bash
ruff check .
mypy
pytest
python scripts/benchmark_candidate_runtime001.py --markets 237 --bursts 20
```

Also run the repository CI smoke/benchmark suite. CANDIDATE-RUNTIME-001 must not
require a trading credential.

Confirm that the diff does not modify:

- MAKE quote mathematics;
- central Risk / ExecutionPlan;
- SIG trading DTO/client behavior;
- live execution interlocks.

## PRED-006 dependency sequence

Do not skip gates.

1. Obtain explicit authorization for the frozen historical fitting step.
2. Fit exactly C01/C02 from historical DATA-003 only using the frozen
   `SimpleImputer(strategy="median") + HistGradientBoostingClassifier`
   specifications.
3. Serialize the complete fitted pipelines, not only tree parameters.
4. Freeze and record artifact SHA-256 values before the future confirmation
   observation window.
5. Create an explicitly authorized `pred006-artifact-manifest-v1` binding both
   C01/C02 hashes to the exact PRED research ID, frozen spec version and feature
   schema hash. Scorer injection without this manifest remains NOT_READY.
6. Provide scorer adapters whose artifact hashes exactly match that manifest.
7. Inject an exact SHADOW -> Polymarket condition scope resolver; do not assume a
   SIG market ID is a condition ID.
8. Feed exact DATA-003-equivalent condition/block observations, including Polygon
   `block_number`, into `IncrementalPred006FeatureState`.
9. Require strictly increasing block number per research scope/window. Equal
   Unix-second timestamps are legal; duplicate/reordered blocks are not.
10. Prove exact custody/fee evidence is observable without future leakage.
11. Check candidate metadata. Any required feature classified
   `NOT_OBSERVABLE_LIVE` or `SEMANTICS_MISMATCH` blocks scoring.
12. Only then collect future-confirmation outcomes under the frozen protocol.

Do not use PM BBO midpoint as `p_yes`, do not drop fee features, and do not
change imputation.

## 005F dependency sequence

1. Complete/verify current-universe order-book capture.
2. Inject the accepted SIG -> PM token scope resolver; do not assume the IDs are
   interchangeable.
3. Feed grouped BBO observations with exact integer `timestamp_ns`, including
   ambiguity status, not individual websocket message age, into
   `IncrementalHazard005FState`.
4. Set the exact `grid_origin_ns` from the applicable frozen regime window.
   Preserve sub-second timestamps through the 300-second continuity gate,
   5-second capture-bin floor, 15-second grid query and `genuine_age_s`.
5. Supply an explicit PRE_ELECTION or ACTIVE_RESULTS regime source.
6. Recover the frozen scaler/model joblib artifacts named by
   `fit_freeze_manifest.json`.
7. Verify every binary against its manifest SHA-256 before constructing scorers.
8. PRE requires only the PRE update-hazard scorer.
9. ACTIVE requires both ACTIVE update-hazard and ACTIVE jump-hazard scorers.
10. Confirm the evaluator reports `ready=true` and finite freshness before
   interpreting a score.

Do not infer `genuine_age_s` from generic quote age.

## Readiness reason guide

| Reason | Meaning | Operator action |
|---|---|---|
| `model_artifact_missing` | PRED authorized artifact manifest and/or fitted pipeline is absent | stop; obtain explicit fitting + manifest authorization |
| `model_artifact_hash_mismatch:<candidate>` | a PRED scorer does not match its authorized manifest hash | disable; recover the exact authorized serialized pipeline |
| `feature_parity_unavailable:...` | exact PRED source semantics fail | fix source parity; do not proxy/drop |
| `required_feature_history_unavailable` | exact PRED block history not available for this scope | continue capture |
| `required_orderbook_history_unavailable` | exact 005F history/grid features are unavailable | continue/repair book capture and grid state |
| `regime_unavailable` | 005F PRE/ACTIVE state not supplied | wire explicit regime source |
| `model_artifact_missing:<coordinate>` | required 005F frozen scorer is absent | recover and hash-check original artifact |
| `model_artifact_hash_mismatch:<coordinate>` | supplied 005F scorer identity does not match the frozen challenger model+scaler hashes | disable it and recover the exact original binaries; never score with the mismatched artifact |

## Restart behavior

The in-memory rolling states are not reconstructed from the SHADOW journal.
After a process restart they require upstream historical replay/warm-up before
becoming ready. Until warm-up is exact and sufficient, `NOT_READY` is correct.

Do not mark a candidate ready merely because the process is healthy.

For parity verification, keep
`tests/fixtures/candidate_runtime001_golden.json` unchanged unless the frozen
research source itself is deliberately re-frozen. The fixture pins the exact
Git blob identities of the original PRED-006 and 005F research runners and tests
equal-second block chronology, 5-second-bin sub-second behavior, fractional
genuine age, and the 300-second ±1ns boundary.

## Rollback

Rollback is code-only; there are no migrations. Revert to the previous SHADOW
runtime commit and restart the process. Existing append-only journal evidence
must not be deleted.

## Incident rule

If a candidate ever emits `OK` while its artifact hash, required feature
history, regime, or parity is unknown, disable that candidate through the
existing SHADOW control plane and treat it as a correctness incident.

A functioning SHADOW score is not authorization to alter MAKE.

REAL SIG ORDERS SENT: NO
