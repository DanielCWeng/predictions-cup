# CANDIDATE-RUNTIME-001 — Frozen research runtime providers

## Scope and provenance

Branch: `build/candidate-runtime-001`

Base: `0b91e5c4e8ed461e7e5e8fcc880a094029f58891`

This lane converts the existing SHADOW-002 PRED-006 and EXPERIMENT-005F
extension points into exact, fail-closed frozen-runtime providers. It does not
perform alpha discovery, fitting, threshold tuning, MAKE integration, or order
placement.

The implementation reads the frozen research contracts rather than translating
the research into a new strategy:

- `data/experiments/pred_006/shortlist/candidate_registry.json`
- `data/experiments/pred_006/shortlist/shortlist_freeze.json`
- `data/experiments/pred_006/final/final_results.json`
- `data/experiments/pred_006/final/make_plugin_spec.json`
- `scripts/kaggle/pred006_final/run.py`
- `docs/experiments/PRED_006_FUTURE_CONFIRMATION.md`
- `data/experiments/experiment_005f/fit_freeze_manifest.json`
- `data/experiments/experiment_005f/holdout_evidence_manifest.json`
- `scripts/kaggle/experiment_005f_fit_freeze/run.py`
- `scripts/kaggle/experiment_005f_holdout/run.py`
- `docs/experiments/EXPERIMENT_005F_FINAL_REPORT.md`

`api-1.json` was also checked. It is the SIG trading/API contract used by the
existing trading DTO/client and is not a fitted research-model artifact.

## Architecture

```text
exact observable upstream evidence
        |
        +--> IncrementalPred006FeatureState
        |      DATA-003 condition/block-end semantics
        |
        +--> IncrementalHazard005FState
               grouped BBO -> genuine transitions
               5s capture bins -> 15s frozen grid
        |
        v
immutable feature vectors
        |
        +--> FrozenPred006Evaluator
        |      injected frozen ProbabilityScorer artifacts
        |
        +--> Frozen005FEvaluator
               injected frozen ProbabilityScorer artifacts
        |
        v
existing Pred006Candidate / Hazard005FCandidate
        |
        v
CandidateDecision -> existing SHADOW journal / LIVE-LEARN
```

The candidate interface is unchanged. The only extension to the existing
evaluator protocols is an explicit `metadata(snapshot)` contract exposing:

- research ID;
- frozen spec version;
- loaded artifact hash;
- expected artifact hash where known;
- feature-schema hash;
- readiness and precise reason;
- freshness;
- quality flags.

All I/O remains outside the evaluator and feature builders. Scorers and feature
providers are dependency-injected.

## PRED-006 hard freeze

Frozen survivors are unchanged:

| ID | Target | Horizon | Model |
|---|---|---:|---|
| PRED006-C01 | next canonical YES price change | 1,800s | HGB max_leaf_nodes=15 |
| PRED006-C02 | next canonical YES price change | 600s | HGB max_leaf_nodes=7 |

The runtime preserves the exact ordered 25-feature schema and does not choose a
new aggregation between C01 and C02. If fitted artifacts eventually exist, both
hazard probabilities are emitted separately in the candidate payload. No
direction, fair value, threshold, or combined score is invented.

### Model artifact status

The committed PRED-006 FINAL runner constructs and fits its sklearn pipeline
inside `evaluate_candidate()`. The committed FINAL output contains metrics and
the plugin specification, but no serialized fitted HGB+median-imputer pipeline.
A recursive repository-tree check found no PRED-006 joblib/pickle model artifact.

Therefore the launch-wired evaluator deliberately returns:

`NOT_READY:model_artifact_missing`

This reason has priority. Arbitrary scorer injection is not authorization.
`FrozenPred006Evaluator` additionally requires a
`Pred006ArtifactManifest` with schema
`pred006-artifact-manifest-v1`. The manifest binds both C01 and C02 to:

- the frozen PRED-006 research ID;
- the exact shortlist freeze/spec identity;
- the exact 25-feature schema hash;
- explicit per-candidate serialized-artifact SHA-256 values;
- non-empty provenance.

With no authorized manifest, injected scorers remain NOT_READY. A manifest hash
mismatch also fails closed. The repository intentionally ships no production
manifest because no authorized serialized PRED-006 fitted artifacts exist.
Tests and the benchmark use clearly marked synthetic-only manifests solely to
exercise the runtime path.

No coefficient substitute, retraining, refitting, or model reconstruction occurs
in this lane.

The separately frozen future-confirmation protocol permits a pre-window
historical refit, but that is a separate explicitly authorized scientific step
and is intentionally not performed here.

## PRED-006 exact feature reconstruction

`IncrementalPred006FeatureState` consumes an upstream
`Pred006BlockObservation` representing one complete DATA-003-equivalent
condition/block-end economic observation. Each observation now carries its Polygon
`block_number` as first-class chronology/provenance.

The frozen source orders complete observations by Polygon block number. Runtime
state therefore enforces strictly increasing `block_number` independently for
each `scope_id × window_id`. Duplicate or reordered blocks are rejected. Equal
Unix-second timestamps remain legal when block numbers increase; in that case
`since_prev == 0.0`. A timestamp regression across increasing blocks is also
rejected as malformed source evidence. Chronology is never inferred from
second-resolution timestamp alone.

The block number is retained in `Pred006FeatureVector` and emitted in candidate
payload provenance as `feature_block_number`.

It incrementally reproduces the FINAL feature formulas. The state is keyed by the
research condition/window scope; SHADOW-to-research identity is supplied through an
explicit injected scope resolver. There is no implicit assumption that a SIG market
ID equals a Polymarket condition ID.

- probability clipping at `1e-4` for logit only;
- block observation `size_log` / `value_log`;
- previous-observation interval;
- 30/120/600/1800 second inclusive rolling counts;
- rolling square-root sum of squared price deltas;
- current-minus-left-edge momentum;
- exact fee flags;
- signed `log1p` net fee;
- charged/refunded/charge-leg `log1p` features.

The rolling implementation intentionally preserves the research detail that the
squared delta attached to the first observation inside a window may reference
the immediately preceding observation outside that window.

Missing history remains NaN. This is not imputed by the feature builder. The
frozen serialized model pipeline, when available, must own the historical
median-imputation semantics.

### Observable-time parity matrix

This table describes the exact runtime source required by the frozen semantics,
not a claim that the current launch snapshot already contains the source.

| Feature | Runtime parity | Exact runtime source |
|---|---|---|
| `p_yes` | EXACT_BUT_DELAYED | complete DATA-003-equivalent condition/block-end economic fill observation |
| `logit_p` | EXACT_BUT_DELAYED | pure transform of exact `p_yes` |
| `boundary_distance` | EXACT_BUT_DELAYED | pure transform of exact `p_yes` |
| `size_log` | EXACT_BUT_DELAYED | complete condition/block aggregated `size_shares` |
| `value_log` | EXACT_BUT_DELAYED | complete condition/block aggregated `value_usd` |
| `since_prev` | EXACT_BUT_DELAYED | ordered complete block observations in the same frozen window/condition |
| `count_30` | EXACT_BUT_DELAYED | same exact observation history |
| `count_120` | EXACT_BUT_DELAYED | same exact observation history |
| `count_600` | EXACT_BUT_DELAYED | same exact observation history |
| `count_1800` | EXACT_BUT_DELAYED | same exact observation history |
| `vol_30` | EXACT_BUT_DELAYED | same exact price history |
| `vol_120` | EXACT_BUT_DELAYED | same exact price history |
| `vol_600` | EXACT_BUT_DELAYED | same exact price history |
| `vol_1800` | EXACT_BUT_DELAYED | same exact price history |
| `mom_30` | EXACT_BUT_DELAYED | same exact price history |
| `mom_120` | EXACT_BUT_DELAYED | same exact price history |
| `mom_600` | EXACT_BUT_DELAYED | same exact price history |
| `mom_1800` | EXACT_BUT_DELAYED | same exact price history |
| `fee_charged` | NOT_OBSERVABLE_LIVE | DATA-002/DATA-003 active taker-order custody attribution is not present in current SHADOW state |
| `fee_missing` | NOT_OBSERVABLE_LIVE | exact custody-ingestion state is not present in current SHADOW state |
| `fee_no_leg` | NOT_OBSERVABLE_LIVE | exact completed custody scan result is not present in current SHADOW state |
| `fee_net_log` | NOT_OBSERVABLE_LIVE | exact attributed charge/refund custody legs are not present |
| `fee_charge_log` | NOT_OBSERVABLE_LIVE | exact attributed charge custody legs are not present |
| `fee_refund_log` | NOT_OBSERVABLE_LIVE | exact attributed refund custody legs are not present |
| `charge_legs_log` | NOT_OBSERVABLE_LIVE | exact attributed charge-leg count is not present |

The non-fee features are **not** reconstructed from Polymarket BBO midpoint.
That would be a semantics mismatch: PRED-006 was fit on DATA-003 economic
condition/block observations.

Once a legal model artifact exists, the next readiness gates are exact feature
parity and availability of exact history. A provider that declares
`NOT_OBSERVABLE_LIVE` or `SEMANTICS_MISMATCH` on any required feature cannot
score.

## EXPERIMENT-005F freeze

The runtime implements only the accepted `genuine_age_s` coordinates:

| Coordinate | Challenger columns | Frozen model |
|---|---|---|
| PRE_ELECTION update_h300 | `genuine_15, genuine_60, genuine_age_s` | HGB_D3_LR0.03 |
| ACTIVE_RESULTS update_h300 | `genuine_15, genuine_60, genuine_age_s` | HGB_D2_LR0.1 |
| ACTIVE_RESULTS jump_h300 | `abs_ret_15, rv_60, genuine_age_s` | HGB_D2_LR0.03 |

The fitted 005F manifest records exact SHA-256 hashes for the scaler/model
joblibs. Those binaries are not committed under
`data/experiments/experiment_005f/artifacts/`; only their manifest hashes are
present. The evaluator therefore requires an injected scorer whose artifact
identity can be checked by the future artifact loader. No fitting is performed.

### Genuine BBO semantics

`IncrementalHazard005FState` does not equate websocket age, receive age, or
quote age with `genuine_age_s`.

It reconstructs the research state needed by the accepted coordinates:

1. consume grouped BBO observations after exact token resolution; an injected
   scope resolver must map the SHADOW snapshot to the research PM token, and the
   runtime never assumes SIG market ID equals token ID;
2. apply the frozen grouped-BBO validity gate: finite values, bid > 0,
   ask < 1, bid <= ask, and no ambiguous same-timestamp group;
3. preserve the grouped observation time as exact integer nanoseconds throughout
   runtime state;
4. a transition is contiguous only when both current and previous BBO are valid
   and the exact nanosecond gap is <=300s;
5. a genuine change is a contiguous best-bid or best-ask change;
6. establish/re-establish transitions are not genuine changes;
7. genuine changes are assigned to 5-second bins by integer-nanosecond floor;
8. evaluation query time is aligned to the frozen 15-second grid from an exact
   `grid_origin_ns`;
9. `genuine_15` / `genuine_60` use the same left-open/right-closed capture-bin
   boundary as the research `rolling_counts`;
10. `genuine_age_s` is computed from the exact nanosecond difference and may
    therefore be fractional;
11. price returns use logit midpoint and require the same state segment;
12. `rv_60` is the square root of the rolling sum of up to four 15-second
    returns with at least two finite observations.

The grid origin is mandatory. The runtime will not silently choose an origin,
because the historical grid was anchored at the regime-window start. The
candidate payload records `grid_time_ns`, not a truncated second.

Top-depth-only state updates are not required by the three accepted coordinates:
they do not change midpoint, segment, genuine counts, or last genuine-BBO time.

### 005F readiness

Launch wiring currently has no exact current-universe grouped order-book-history
provider feeding this state. The production evaluator therefore returns:

`NOT_READY:required_orderbook_history_unavailable`

This is deliberately checked before model-artifact availability. Once exact
history exists, readiness additionally requires:

- explicit PRE_ELECTION or ACTIVE_RESULTS regime;
- exact grid origin;
- the appropriate frozen scorer artifact(s);
- finite frozen feature vector.

PRE_ELECTION emits only the frozen update-hazard coordinate. It does not invent
a PRE jump-hazard score. ACTIVE_RESULTS emits update and jump hazards separately.

## SHADOW and MAKE boundary

`build_live_shadow_runtime()` now registers
`FrozenPred006Evaluator` and `Frozen005FEvaluator` behind the existing
`Pred006Candidate` / `Hazard005FCandidate` adapters.

The adapters persist evaluator provenance/readiness into the existing
`CandidateDecision` payload. No new candidate interface, journal, queue, or
execution path was added.

005F remains explicitly non-directional. No 005F output is wired into MAKE's
`ToxicityProvider`, spread policy, sizing, or eligibility. PRED-006 also has
no MAKE promotion.

## Tests

`tests/test_candidate_runtime001.py` covers:

- PRED rolling feature parity fixture;
- PRED missing-history NaN semantics;
- per-feature current live parity classification;
- mandatory PRED `model_artifact_missing` even when arbitrary scorers are injected;
- PRED manifest hash authorization and mismatch rejection;
- equal-second/increasing-block chronology plus duplicate/reorder rejection;
- separate C01/C02 output without invented aggregation;
- exact 005F genuine-age and 5s-bin boundary fixture;
- 005F sub-second genuine changes and fractional `genuine_age_s`;
- exact continuity immediately below and above 300 seconds;
- mandatory 005F grid origin;
- missing order-book history fail-closed path;
- separate ACTIVE update/jump coordinates;
- PRE update-only behavior without invented jump output;
- committed golden parity vectors for PRED-006 and 005F.

The golden fixture is
`tests/fixtures/candidate_runtime001_golden.json`. Its provenance pins the
original research source files by Git blob SHA:

- `scripts/kaggle/pred006_final/run.py` ->
  `a146521068b0091b9096b5b94a535f4b8c9fd20c`, function `add_features`;
- `scripts/kaggle/experiment_005f_fit_freeze/run.py` ->
  `b31606371c528775c355807a6ccfd147cd422ecc`, including
  `rolling_counts`, `asof_from_states`, and `build_clock`.

The fixture includes equal-second PRED blocks, the complete expected frozen
25-feature vector, sub-second 005F BBO changes straddling a 5-second bin
boundary, fractional genuine age, and 300-second ±1ns continuity cases. A test
recomputes the Git blob identities from the checked-out original research source
before accepting the fixture provenance.

Existing SHADOW tests continue to cover unwired generic extension points,
candidate isolation, durability, and non-trading construction.

## Performance design

Both feature builders are incremental and market-keyed.

PRED maintains four bounded rolling deques per scope and rolling sums of squared
price deltas. It does not rebuild a dataframe.

005F maintains compact retained BBO state, genuine capture-bin counts, and uses
binary-search as-of lookup for the four 15-second returns required by the frozen
coordinates. It does not create a task per market and performs no I/O.

The lane benchmark exercises 237-market bursts through the feature/evaluator
boundaries with deterministic injected scorers; it is a runtime plumbing
benchmark, not scientific evidence.

GitHub Actions run `36729039542` executed the corrected parity runtime at
`2659ddfa9512961f8fab4ab1919a0205ee8a0dcb` over 20 x 237-market bursts, or
9,480 evaluator calls:

- elapsed: 0.417991s;
- throughput: 22,679.9 evaluator calls/s;
- evaluator latency: p50 52.299us, p95 55.183us, p99 62.143us;
- 237-market two-evaluator burst: p50 20.819ms, p95/p99 21.218ms.

The same run passed Ruff, shell validation, strict Mypy over 227 source files,
all existing smoke/benchmark stages, and pytest with 674 passed / 3 skipped.

No performance acceptance threshold changes research semantics.

## Known limitations / external dependencies

1. PRED-006 has no legal serialized fitted FINAL model artifact. Required next
   step: a separately authorized frozen historical fitting step exactly as
   specified by the future-confirmation protocol.
2. Current SHADOW state does not expose exact DATA-003 custody-attribution
   evidence. PRED cannot become ready by substituting websocket/CLOB fields.
3. Current live wiring does not feed the 005F exact grouped order-book history
   state and does not provide the frozen regime-grid origin.
4. The 005F manifest names fitted artifacts, but the joblib binaries are not
   committed in the repository. They must be recovered exactly and hash-checked;
   they must not be refit in this lane.
5. Golden feature-vector parity is now committed and source-pinned, but model
   score parity still cannot be claimed without the original serialized fitted
   artifacts.

## Configuration and migration

No environment variables, schema migrations, or database migrations are added.

Deploying this commit changes SHADOW decisions for the two launch-wired research
candidates from generic placeholder reasons to exact dependency reasons. A
process restart is required to load the new evaluator classes. Existing SHADOW
journals remain append-only and compatible.

REAL SIG ORDERS SENT: NO
