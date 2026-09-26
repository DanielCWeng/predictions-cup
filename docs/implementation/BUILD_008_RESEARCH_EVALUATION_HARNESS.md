# BUILD-008 — Research Evaluation Harness

## Purpose

BUILD-008 is the common scientific-control layer above BUILD-005 replay and EXPERIMENT-002. It
does not discover alpha. It standardizes how later hypotheses bind data/config/code, enforce
observable-time safety, split samples, quantify dependence-aware uncertainty, control multiple
testing, assess parameter stability, compare controls/ablations, stress execution, and serialize
research evidence.

## Architecture

- `research_spec.py`: immutable canonical research contract, dataset-version binding, SHA-256
  config/run identity, and the standard 1s/5s/30s/1m/5m panel.
- `validation.py`: common observation adapter, as-of gate, chronological folds, purge/embargo,
  chronological event/family holdout and explicitly non-chronological leave-group-out diagnostics.
- `statistics.py`: deterministic Benjamini-Hochberg FDR plus moving-block and event bootstrap.
- `stability.py`: parameter-surface neighbourhood diagnostics for plateau vs isolated optimum.
- `evaluation_harness.py`: run identity enforcement, ablation/control comparability and named
  execution stress.
- `reporting.py`: deterministic report schema plus explicit research dispositions/ledger rows.

Existing experiment observations remain the underlying evidence object; BUILD-008 adapters do not
rewrite BUILD-005 or EXPERIMENT-002 logic.

## Canonical workflow

`hypothesis -> data version -> as-of-safe features -> target/horizon -> event-aware split -> predictive
result -> FDR -> bootstrap -> stability -> execution stress -> ablation/control -> disposition`.

The research specification is serializable and contains no Python callable identity. Dataset
identity includes dataset ID, schema/version, manifest SHA-256 and optional source version. The
caller supplies the exact code revision. The canonical config binds the complete evaluation
protocol, including walk-forward windows/step, purge/embargo, predeclared FDR family/search space
and alpha, bootstrap method/weighting, stability tolerance, parameter grid, control parameters,
ablation definitions, execution-stress parameters and disposition evidence policy. The run ID is
SHA-256 over that canonical config hash, dataset hash and code revision; time, UUIDs, object
identity and file mtimes are excluded.

## Split and leakage semantics

All timestamps are timezone-aware. The hard feature invariant is
`feature_available_at <= decision_time`; future-dated features raise rather than being clipped.

Chronological train/development/holdout assignment is deterministic. Training labels are purged
against the development boundary and development labels are independently purged against the
holdout boundary. BUILD-005 consumes target state at the horizon itself, so the label interval is
treated as closed: labels ending exactly at the next evaluation boundary are purged
(`label_end_time >= evaluation_start`). Both stages support an explicit pre-boundary embargo and
report before/purge/embargo/remaining counts.

Chronological event/family holdout only trains on rows strictly earlier than the held-out unit's
first observation, purges earlier label intervals that cross into the held-out event/family and
supports the same embargo rule. `leave_group_out_diagnostic()` remains separate because a pure
group diagnostic can include future groups and must not be described as chronological OOS
evidence.

Every common observation also enforces `label_end_time == decision_time + horizon`. Harness
validation checks the declared hypothesis family, horizon, market/event/family universes and a
deterministic observation fingerprint. Duplicate fingerprints fail before statistics so repeated
fills/rows cannot inflate evidence counts.

## Statistical semantics

Benjamini-Hochberg operates only against the predeclared FDR family and complete predeclared
hypothesis-ID search space. Supplied p-values carry explicit test metadata: test name, null
hypothesis, test statistic and dependence assumption. Missing or silently narrowed hypotheses fail
the FDR call. Ties break by hypothesis ID. Results report raw p, rank, threshold, monotone adjusted
q-value, family size and reject decision. BUILD-008 never synthesizes p-values from confidence
intervals.

Moving-block bootstrap samples contiguous blocks. Event bootstrap exposes two distinct estimands:
`OBSERVATION_WEIGHTED_CLUSTER` resamples event clusters and then pools their observations, while
`EQUAL_EVENT` resamples event-level means so each event has equal weight regardless of tick count.
The selected weighting is recorded in the result and canonical bootstrap protocol. Default seeds
derive deterministically from run ID plus component ID. Intervals are uncertainty diagnostics, not
proof of independence.

## Parameter stability

The parameter surface records each cell, rank, Manhattan-adjacent neighbours, sign stability,
distance from peak, local dispersion, and fraction of neighbours within an explicitly configured
relative tolerance of the peak. A peak with no neighbour inside tolerance is reported as isolated.
Parameter selection belongs to train/development or walk-forward folds; final holdout must not be
used repeatedly to choose a cell.

## Controls, ablation and execution stress

Controls and ablations are named variants. `assert_variant_comparability()` refuses a silent
dataset-hash or fold-ID change. The framework does not impose future feature names.

Execution stress supports explicit additional per-share cost. A non-zero execution-delay request
raises unless the caller re-runs replay at the later observable time; BUILD-008 does not pretend to
model latency by subtracting a constant. Maker queue simulation remains out of scope.

Predictive result, gross executable markout and net executable markout are separate fields. Missing
net economics remains `null`; gross is not relabelled as zero-cost net profit.

## Report and research ledger

`ResearchReport` binds schema/run/config/data/code identity, universe, fold and purge evidence,
observation/market/event/family counts, horizon results, predictive/gross/net metrics, bootstrap,
raw tests, FDR, stability, controls, ablations, stresses, invalidities, limitations and explicit
disposition. A hash-bound `EvidencePolicy` specifies both required evidence classes and promotion criteria,
including minimum FDR discoveries, optional bootstrap lower-bound threshold, non-isolated
parameter stability, and required pass states for controls, ablations and execution stresses.
If a report requests `PROMOTED` while required evidence is absent or declared criteria fail, the
serialized report records the requested disposition and failed/missing gate but mechanically
forces the effective disposition to `INCONCLUSIVE`. Canonical JSON serialization is stable.

`RESEARCH_LEDGER.md` is the durable decision record. PROMOTED, REJECTED and INCONCLUSIVE are all
first-class. Synthetic BUILD-008 machinery fixtures are never recorded as empirical alpha.

## Existing integration

`adapt_experiment_observation()` wraps BUILD-005 `ExperimentObservation`.
`adapt_relationship_observation()` wraps EXPERIMENT-002 `RelationshipObservation`.
Both retain the original object and independently apply the BUILD-008 as-of gate.

The common horizon panel is 1s, 5s, 30s, 1m and 5m. Experiments may carry additional horizons such
as EXPERIMENT-002's 2s/10s observations; unavailable standard horizons belong in reports as
unavailable rather than disappearing.

## DATA-001 and live-mapping boundaries

BUILD-008 has no dependency on DATA-001/PR #23 and embeds none of its paths or hashes. The
`DatasetVersion` interface is where an accepted DATA-001 manifest can later attach.

BUILD-008 also has no dependency on LIVE-MAPPING-GATE-001 or production mapped IDs. Synthetic or
explicit manual event/market identities are sufficient for harness validation.

## Limitations

- BUILD-008 does not define experiment-specific predictive tests or invent p-values; it does
  require supplied tests to declare their statistical metadata and predeclared FDR search space.
- It does not fit models or select alpha parameters.
- Event IDs/family IDs must be supplied by experiment/data adapters.
- The parameter-neighbour topology is a simple integer-grid Manhattan adjacency contract.
- Latency stress requires replay support at the delayed time.
- No maker-fill, queue, portfolio sizing, production fair value or live trading is implemented.
