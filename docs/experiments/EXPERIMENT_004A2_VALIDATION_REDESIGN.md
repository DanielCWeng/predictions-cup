# EXPERIMENT-004A.2 — Event-Time Validation Redesign

**Status:** ready for independent review; scientific-control architecture only.

## Purpose

EXPERIMENT-004A.2 replaces generic pooled/calendar-time interpretation with a frozen
event-time validation contract for election prediction-market research. It does not search for
alpha, rerun EXPERIMENT-003, alter DATA-001, move 004A boundaries, or simulate trading.

The accepted upstream package is:

- 004A package: `004A-event-time-v2`
- regime SHA-256:
  `57a102d64778be7c1460638bb4da63be7eeafe0b9494a4287339674bcad0a741`
- independent election families: `COL_2026`, `PER_2026`, `HUN_2026`

All event windows are loaded from committed 004A evidence and hash-verified before use.

## Why EXPERIMENT-003 is not enough for the next stage

EXPERIMENT-003 remains valid for its original frozen battery, but its evidence cannot be
silently upgraded into the stronger claims needed next. PRE_ELECTION and ACTIVE_RESULTS are
different economic/data-generating regimes. Five historical round windows are only three
independent election families. Tick count does not create independent elections.

EXPERIMENT-003 already records that its five-regime equal-event bootstrap is not an
independent-election-family bootstrap. 004A.2 makes that limitation mechanical for subsequent
research: cross-family evidence is collapsed to COL/PER/HUN before aggregation, and a
`LOW_INDEPENDENT_FAMILY_COUNT` flag is emitted when only these three families are available.

## Condition-level research universe

004A contains 2,600 token rows, exactly 1,300 event-scoped binary conditions. 004A.2 projects
exactly one research side per condition using the accepted DATA-001 token whose outcome label is
exactly `Yes`. Complementary tokens are retained as diagnostic metadata and never counted as
independent markets.

The accepted identity file contains 1,187 exact `Yes` / `No` pairs and 113 uppercase
`YES` / `NO` pairs: 43 in Peru first round, 43 in Peru runoff, and 27 in Hungary. The
protocol is intentionally case-sensitive, so those 113 conditions fail closed rather than being
silently normalized. They remain in the matrix with `FAILED_CLOSED` and a deterministic reason
code. This is a source-label limitation, not permission to select the better-covered side.

The 25 accepted token-pair usability disagreements are explicitly retained. A disagreement never
promotes a condition: condition usability/classification is derived only from the canonical
exact-`Yes` token.

## Claimed regimes

Every predictive claim declares one regime:

- `PRE_ELECTION`
- `ELECTION_DAY_PRE_RESULTS`
- `ACTIVE_RESULTS`
- `LATE_COUNT_DIAGNOSTIC`

PRE_ELECTION and ACTIVE_RESULTS are separate primary research lanes. They are never pooled for
primary effect estimation, hypothesis testing, FDR, bootstrap confidence, or promotion evidence.

A future hypothesis targeting more than one regime must preregister separate claims, for example
`HYPOTHESIS_X__PRE_ELECTION` and `HYPOTHESIS_X__ACTIVE_RESULTS`. Evidence in one lane cannot
rescue the other. LATE_COUNT is diagnostic unless a later preregistration explicitly changes its
research role.

## Regime membership and as-of safety

A primary observation belongs to a claimed regime only when:

```text
regime_start <= decision_time < regime_end
label_end_time < regime_end
feature_available_at <= decision_time
label_end_time == decision_time + horizon
```

004A intervals are half-open. A label ending exactly at the regime end is therefore rejected with
`LABEL_CROSSES_REGIME_END`. Cross-boundary labels are invalid for the claimed regime; they are
not clipped, backfilled, or reassigned.

BUILD-008's closed-label purge remains authoritative: any earlier row with
`label_end_time >= evaluation_start` is purged. TRAIN→DEVELOPMENT and
DEVELOPMENT→HOLDOUT protection remains intact. For event/family holdouts, 004A.2 uses the frozen
event-time window start as the purge boundary rather than the first observed market tick, preventing
a quiet opening gap from leaking held-out time into training.

Embargo is explicit, non-negative, deterministic, and must be hash-bound by downstream
preregistration. There is no result-dependent hidden default.

## Validation hierarchy

### WITHIN_EVENT_TEMPORAL_OOS

Chronological earlier-to-later validation inside one event and one claimed regime. Overlapping
labels are purged and the declared embargo applies. This supports only temporal persistence within
that event/regime; it does not establish cross-election generalisation.

### FORWARD_EVENT_HOLDOUT

The complete claimed-regime window for an event is HOLDOUT. Only observations strictly earlier
than the frozen held-out window start may train or develop. The fold records whether earlier
training contains another round from the same election family. If it does, the result is forward
event evidence but not an independent-family replication.

### CROSS_ROUND_SAME_FAMILY

Colombia first round → Colombia runoff and Peru first round → Peru runoff are explicitly supported
where chronology permits. They are labelled same-family forward transfer. The later round is
genuinely unseen, but it is not a second independent election-family confirmation.

### FORWARD_FAMILY_HOLDOUT

This is the preferred evidence for a cross-election-family claim. The entire held-out family is
excluded from fitting, development, scaling, parameter selection, feature selection, and
relationship discovery. Training may use only other-family observations strictly earlier than the
first relevant frozen window for the held-out family.

When no prior independent family exists the fold returns
`INSUFFICIENT_PRIOR_FAMILIES`. The protocol does not fabricate a symmetric fold by using future
history.

### LEAVE_FAMILY_OUT_RETROSPECTIVE_DIAGNOSTIC

All other families may train while one complete family is held out. This is useful for
transportability diagnostics but can use chronologically later data. Every such fold is marked
`NON_TEMPORAL_RETROSPECTIVE_DIAGNOSTIC` and evidence scope `RETROSPECTIVE_ONLY`; it is never
prospective OOS evidence.

## Evidence scope and disposition

The existing BUILD-008 dispositions `PROMOTED`, `REJECTED`, and `INCONCLUSIVE` are not
redefined. 004A.2 adds evidence scope:

- `WITHIN_EVENT_SUPPORTED`
- `SAME_FAMILY_FORWARD_SUPPORTED`
- `CROSS_FAMILY_FORWARD_SUPPORTED`
- `RETROSPECTIVE_ONLY`

A cross-event-family claim without genuine forward family evidence remains `INCONCLUSIVE`
regardless of tick-level significance. Historical evidence can still justify live replication
without constituting proof of production alpha.

## Multiplicity and uncertainty

FDR is lane-specific. PRE_ELECTION hypotheses belong to a PRE_ELECTION family and ACTIVE_RESULTS
hypotheses to an ACTIVE_RESULTS family. ELECTION_DAY_PRE_RESULTS gets a separate family if later
preregistered. Unavailable cells remain in the predeclared search space; they cannot disappear
after outcomes are observed.

Within a regime window, contiguous moving-block/bootstrap/randomisation methods may describe
temporal uncertainty. Reports must expose the effect, observation count, condition count, time
coverage, invalid count, block-level uncertainty and direction for each individual held-out
event/regime window.

Cross-election evidence is then collapsed first to the independent family:
`COL_2026`, `PER_2026`, or `HUN_2026`. Family reporting includes each family effect, eligible
family count, same-direction count, equal-family mean/median and range. If hierarchical resampling
is later used, the hierarchy is family → event/round → contiguous temporal block. EXPERIMENT-003's
five-window `EQUAL_EVENT` bootstrap is not cross-family confidence.

With only three independent election families, cross-family inferential precision is intrinsically
weak. The framework reports `LOW_INDEPENDENT_FAMILY_COUNT` rather than manufacturing precision
from millions of observations.

## Hash-bound protocol and fold inventory

The generated `validation_protocol.json` binds the accepted 004A package, condition universe,
canonical-token rule, regime membership, boundary policy, claim scopes, fold rules, purge/embargo,
family grouping, uncertainty hierarchy, FDR lane policy and evidence-scope policy. Its SHA-256 is
deterministic; changing the 004A regime hash or condition-universe hash changes validation identity.

`validation_fold_inventory.csv` is data-independent with respect to future alpha outcomes. It
materializes what can and cannot be claimed before a new battery is run: within-event folds,
forward-event holdouts, forward-family holdouts, same-family transfers, retrospective diagnostics,
and explicit insufficiency states.

The committed generator reads only accepted repository artefacts. It does not rescan the 1.19GB
DATA-001 corpus.

## Frozen 004A.2 identity

The deterministic build currently yields:

- condition universe version: `004A2-condition-universe-v1`
- condition universe SHA-256:
  `1f449f394b9a81743632b1f699188e5649769c48af7939f9a52db631712e37cb`
- validation protocol version: `004A2-event-time-validation-v1`
- validation protocol SHA-256:
  `e079ba40557b8d5d23d99f2b7834ed9a581f359ec63b3fb120480ca5a171a9d4`
- 1,300 conditions total; 1,187 exact-`Yes` canonical; 113 fail closed
- 25 token-pair disagreement cases retained explicitly
- 72 data-independent fold-plan rows: 20 within-event, 20 forward-event, 12 forward-family,
  8 same-family transfer, and 12 retrospective family diagnostics

Of the forward folds, 16/20 event holdouts and 8/12 family holdouts are feasible under chronology.
All eight first-round→runoff same-family transfers are feasible. Four family folds fail closed with
`INSUFFICIENT_PRIOR_FAMILIES`.

## Generated package

```text
data/experiments/experiment_004a2/
    condition_usability_matrix.csv
    condition_universe_summary.json
    validation_protocol.json
    validation_fold_inventory.csv
    validation_summary.json
```

Implementation and verification live in:

```text
src/predictions_cup/learning/event_time_validation.py
scripts/build_experiment_004a2.py
tests/test_experiment_004a2_validation.py
```

The build is deterministic and intentionally compact. No large derived observation matrix is
committed.

## Non-goals

004A.2 does not search predictive features, inspect correlations for alpha, rerun the 003 battery,
build response matrices, run PCA/SVD discovery, search lead/lag, choose strategies, tune thresholds,
simulate trading/execution, build maker models, change 004A event boundaries, or rewrite DATA-001.

## Relationship to EXPERIMENT-004B

EXPERIMENT-004B may be scaffolded while this PR is reviewed, but empirical market-structure
discovery must not run until 004A.2 is independently accepted and its protocol hash is frozen.
Downstream 004B/004C research must reference the accepted 004A regime hash, 004A.2 condition
universe hash and 004A.2 validation-protocol hash.

## Review boundary

This branch does not self-accept or merge. Independent review should verify the committed universe,
hashes, fold inventory, synthetic controls, and CI, with particular attention to the 113 exact-label
fail-closed conditions and the distinction between forward-family evidence and same-family transfer.
