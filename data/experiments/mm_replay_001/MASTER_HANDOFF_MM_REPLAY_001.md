# MASTER HANDOFF — MM-REPLAY-001

Branch: `research/mm-replay-001-spread-capture`

## Preparatory outcome

```text
IMPLEMENTATION_READY
DATA_STATUS=WAITING_FOR_DATA
SCIENTIFIC_RESULT=NOT_RUN
REAL SIG ORDERS SENT: NO
```

The requested launch-ready analytical harness is implemented without substituting an
old dataset. Scientific conclusions have not been generated.

## Built now

- manifest-driven DATA-003 provenance gate;
- schema/capability/hash audit;
- snapshot and explicit price-level delta top-of-book interfaces;
- exact existing 005F feature-state adapter;
- hash-gated original 005F artifact scoring path;
- external-FV/local-mid/inventory/toxicity maker replay;
- conservative, queue-aware and strict trade-through fill assumptions;
- 1s/5s/15s/30s/60s/5m markouts;
- fair-value convergence study;
- 0/25/50/100/250/500/1000ms latency sensitivity;
- compact predeclared 0–4 tick minimum-edge grid;
- chronological TRAIN/DEV/FINAL labels for replay evidence;
- required output/report schemas;
- deferred Kaggle job template and GO CLI;
- deterministic engineering tests.

## Kaggle paths

Kernel: `scripts/kaggle/mm_replay_001`

Deferred job template: `scripts/kaggle/mm_replay_001/job-template.json`

Real job created at GO: `kaggle/jobs/mm-replay-001.json`

The canonical GitHub Actions runner stages the current repository
`predictions_cup` package and frozen manifests into the ephemeral Kaggle payload,
so the kernel executes the same accepted 005F implementation tested in CI.

## Fields still waiting on arriving data

- `kaggle_dataset_slug`
- `dataset_version`
- `source`
- `schema_version`
- `acquisition_version`
- exact `relation_to_data003`
- `book_encoding`
- explicit `column_map`
- file hashes where available
- market/token/time coverage
- `grid_origin_ns_by_market`
- `005f_regime_default` only when a frozen regime is justified
- `frozen_005f_artifact_dataset_slug` when original artifacts are supplied

No DATA-001/Hungary/Colombia/Peru result has been used as evidence for this branch.
