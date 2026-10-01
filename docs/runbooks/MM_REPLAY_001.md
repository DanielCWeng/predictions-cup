# MM-REPLAY-001 — Historical fair-value-anchored market-making replay

## Current status

```text
IMPLEMENTATION_READY
DATA_STATUS=BOUND
PRIMARY_SCOPE=baseline_sep
EXECUTION_UNIVERSE=140 EXACT / SAME
SCIENTIFIC_RESULT=PENDING_RUNNING_REPLAY
REAL SIG ORDERS SENT: NO
```

The landed DATA-003-linked corpus is bound. The canonical strict-as-of replay is still pending completion/review; no scientific conclusion should be inferred from job status alone.

No DATA-001, Hungary, Colombia, Peru, or unrelated Polymarket sample is an allowed scientific substitute. Tiny deterministic fixtures are engineering tests only.

## Accepted contracts reused

- DATA-003 relation is mandatory in the input manifest.
- The accepted SIG↔Polymarket mapping remains the mapping authority.
- 005F features are produced through
  `predictions_cup.shadow.frozen_runtime.IncrementalHazard005FState`; the replay
  does not recreate the frozen feature state from memory.
- Frozen 005F joblib artifacts are scored only when their SHA-256 hashes match the
  accepted `fit_freeze_manifest.json`.
- MAKE-001's binary-CARA reservation-price shape and SIG tick are reused.
- The replay never calls the SIG order adapter or BUILD-009 live execution boundary.

## Dataset contract

`data/experiments/mm_replay_001/input_manifest.json` is now bound to the landed corpus. The scientific contract requires:

- exact Kaggle dataset slug and version;
- source, source schema version, acquisition version, and explicit DATA-003 relation;
- explicit `book_encoding` of `SNAPSHOT` or `DELTA`;
- explicit canonical `column_map`;
- file hashes when the acquisition publishes them;
- coverage/time metadata when known;
- exact per-market 005F grid origins;
- explicit source venue / replay-laboratory interpretation;
- an explicit 005F regime if frozen artifact scoring is requested;
- explicit maker fee and terminal unwind-cost assumptions before net P&L is populated;
- an explicit edge-grid reference latency if the compact edge-threshold grid is run;
- the approved Kaggle dataset containing the original 005F joblib artifacts, if used.

The audit reports formats, schemas, identity/time columns, BBO reconstructability,
trade evidence, external-FV availability, queue support, and hash mismatches.
A failed audit stops before scientific replay.

## Execution assumptions

The conservative fill model requires an observed aggressive trade at/through the
passive quote. A price touch alone never fills. The queue-aware model is only enabled
when explicit queue-ahead fields exist. A stricter trade-through sensitivity model is
also reported. Simulated assumptions are never labelled as observed fills.

The compact policy family is B0 local-mid, B1 external FV, B2 external FV plus
inventory, and B3 external FV plus frozen 005F toxicity. B3 fails closed when a valid
toxicity score is unavailable. A predeclared 0–4 tick minimum-edge grid is emitted for
sensitivity; FINAL is not used for rescue tuning.

## Outputs

The kernel writes the requested audit, frozen-transfer, fill, policy, markout,
toxicity, fair-value-convergence, latency and market-breakdown artifacts plus
`FINAL_REPORT.md` and the empirical handoff. `MM_CANDIDATE_CONFIG.json` is optional
and is not emitted automatically: live promotion requires a separate evidence review.

## Binding / replay workflow

The dataset has already landed and been bound. The commands below document the reproducible binding path rather than a current waiting-state instruction:

```bash
python scripts/mm_replay_001.py bind-dataset <owner/slug> \
  --version <version> \
  --source "<source>" \
  --source-venue <venue> \
  --schema-version <schema-version> \
  --acquisition-version <acquisition-version>
```

Then populate the dataset-derived fields in the manifest: `book_encoding`,
`column_map`, hashes/coverage where available, `grid_origin_ns_by_market`, and
the 005F regime/artifact dataset only when justified by provenance.

If the dataset is materialized locally, the same gate can be checked before submission:

```bash
python scripts/mm_replay_001.py audit <dataset-root>
```

Prepare a runnable Kaggle job only after the binding is complete:

```bash
python scripts/mm_replay_001.py prepare-job
git add data/experiments/mm_replay_001/input_manifest.json \
        scripts/kaggle/mm_replay_001/kernel-metadata.json \
        kaggle/jobs/mm-replay-001.json
git commit -m "MM-REPLAY-001: bind current DATA-003 order-book corpus"
git push
```

GitHub Actions uses `kaggle/jobs/mm-replay-001.json`. The Kaggle kernel runs the input audit first; only a passing audit reaches 005F/MM replay. Do not edit a runnable job manifest merely to retrieve outputs while Kaggle slots are saturated. Post-run output-only manifest examples are stored under `data/experiments/mm_replay_001/` and can be copied into `kaggle/jobs/` after the target kernel is COMPLETE.

To return the branch to a non-runnable waiting state:

```bash
python scripts/mm_replay_001.py reset-waiting
```

## Research interpretation

Gross spread capture is never the profitability metric by itself. The replay keeps
gross spread, future-FV markouts, cash/inventory terminal accounting, fees and unwind
costs visible separately. Cancellation latency is part of the fill simulation itself
for the 0/25/50/100/250/500/1000ms sweep; it is not merely a chart layered on an
instant-cancel replay. Fair-value
convergence is descriptive and must not be called causal lead-lag without additional
identification. Latency output is an exposure sensitivity unless the input data
supports stronger execution inference.
