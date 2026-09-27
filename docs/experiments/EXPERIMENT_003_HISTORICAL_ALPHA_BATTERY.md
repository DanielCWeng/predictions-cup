# EXPERIMENT-003 — Historical Alpha Battery

**Status:** empirical run complete; no family promoted; PARTICIPANT-001 confirmatory validity invalidated by protocol deviation
**Branch:** experiment/003-historical-alpha-battery  
**Base:** accepted main at 63d2a71f8c736752a577ab97b6efb3be5d1e8c6a  
**Purpose:** predictive discovery and rejection, not strategy optimisation or execution simulation.

## Scientific question

EXPERIMENT-003 asks which simple forms of historical Polymarket information add reproducible
out-of-sample information about subsequent market repricing beyond simple target-market baselines.

The five primary information families are:

1. LEADLAG-001 — explicit cross-market lead/lag;
2. RV-001 / LOO-FAMILY-001 — event-relative structural residual;
3. MICROSTRUCTURE-001 — observable depth imbalance and microprice displacement;
4. PARTICIPANT-001 — train-frozen participant-conditioned flow;
5. LOWRANK-001 — restrained leave-target-out rank-1 cross-market factor benchmark.

A null or inconclusive result is a valid experiment outcome. Nothing in this ticket enables trading.

## Exact dataset

The empirical runner accepts only DATA-001-HISTORICAL-REPLAY-CORPUS schema 1.
Accepted hashes:

- corpus manifest: e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4
- quality: 354801b67b9c32ae82d419f8a6198b7fd814923e8c424ac8716862b47907ccb5
- identity: e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72

The runner hashes the mounted manifest and identity file and invokes DATA-001's full corpus
validator. A mismatch fails closed.

The task prompt quoted older auxiliary hashes for data_001_sources.json and
data_001_kaggle_run.json. They do not match accepted current main. The preregistration records
current-main hashes instead:

- sources: ce5adfdbce5f8e858162998c47e202196f25c59f86f72324c4fee54504ee9487
- Kaggle reproduction evidence: 0d63a692ea8c9cd0a5117a653b4dc302cc41971edf6f3be98009146c46f62f04

No alternate corpus is substituted.

## Accepted events

| Regime | Window | Event family |
|---|---|---|
| colombia_first_round | 2026-05-29 to 2026-06-03 UTC | COL_2026 |
| colombia_runoff | 2026-06-19 to 2026-06-24 UTC | COL_2026 |
| peru_first_round | 2026-04-10 to 2026-04-15 UTC | PER_2026 |
| peru_runoff | 2026-06-05 to 2026-06-10 UTC | PER_2026 |
| hungary_election | 2026-04-05 to 2026-04-15 UTC | HUN_2026 |
## Preregistration and relationship universe

Canonical preregistration:
data/experiments/experiment_003/preregistration.json

Explicit relationship inventory:
data/experiments/experiment_003/relationship_inventory.csv

The inventory contains 72 directed relationships. Relationships are manually reviewed from
accepted condition/event identity: same-candidate election-stage relationships, same-party
cross-chamber relationships, and explicit regional/national relationships. Similar question text
never creates a relationship automatically.

LOO-PRICE-001 is separately generated as a mechanical reconstruction diagnostic from accepted
same-event peers. It is never presented as indirect alpha. The primary structural family uses the
explicit INDIRECT inventory.

Exactly one canonical Yes token is used per binary condition.

## Common target

The primary target is future log-odds midpoint repricing:

y(i,t,h) = logit(mid(i,t+h)) - logit(mid(i,t))

Common horizons are 1s, 5s, 30s, 60s, and 300s.

Quotes are joined as-of only: latest state observable at or before the requested timestamp, subject
to the preregistered freshness bound. No future quote is borrowed backward. Same-observable-time
book changes with conflicting BBO states are treated as ambiguous and invalidate state until a
later unambiguous observation.

The secondary economic diagnostic is gross executable crossing markout. It is not a fill,
latency, queue, sizing, portfolio, or P&L simulation.
## Models and validation

The common comparison is BASELINE versus CHALLENGER = BASELINE + family-specific information.

Primary metric: delta MSE = MSE_baseline - MSE_challenger. Positive values favour the challenger.

Models are ordinary least squares with an intercept, TRAIN-only scaling, deterministic
numpy.linalg.lstsq, no regularisation search, no nonlinear model, and no final-holdout feature
selection.

Preregistered walk-forward geometry:

- TRAIN: 48 hours
- DEVELOPMENT: 24 hours
- HOLDOUT: 24 hours
- step: 24 hours
- embargo: 300 seconds
- closed-label purge at evaluation boundaries

Coefficients, scalers, participant scores and low-rank loadings are fitted on TRAIN only.

## Family definitions

### Cross-market lead/lag

Primary lookback is 5 seconds; 1/5/30 seconds are fixed sensitivities. Baseline is the target's own
lagged logit move. Challenger adds mean past logit move of explicit related markets. A 300-second
delayed-reference signal is the negative control.

### Structural residual

Primary reference is an equal-weight logit mean over explicit indirect relationships. Baseline is
target autoregression; challenger adds the negative target-versus-reference residual. Reference
freshness 60/120/300 seconds is the fixed sensitivity surface.

LOO-PRICE-001 is a same-event mechanical reconstruction diagnostic. LOO-FAMILY-001 is the
genuinely indirect construction.
### Microstructure

Primary information is top-of-book imbalance and microprice displacement divided by spread.
Baseline includes own lagged move, spread, top-level depth and quote age. Primary lookback is
5 seconds with 1/5/30-second sensitivity.

Depth features exist only at genuine depth snapshots. Conflicting same-time snapshots are excluded.
No queue depletion, cancellation inference, passive-fill probability, VPIN, Kyle lambda, MRR or
aggressor inference is implemented. A 300-second delayed feature is the negative control.

### Participant-conditioned flow

The frozen preregistration specifies an identity-blind baseline of recent fill count/value,
maker-activity value, taker-activity value, target own logit move and spread. The challenger adds a
TRAIN-frozen participant-conditioned continuation score at participant × event × source matching-role
level. The frozen negative control is a deterministic frequency-preserving participant-label
permutation.

The executed implementation does **not** exactly match that frozen protocol. Its baseline uses recent
fill count/value, unique maker count, unique taker count, exchange-taker value, target own move and
spread. Its negative control permutes fitted participant score values across participant identities
within source matching role instead of permuting raw participant labels. This material mismatch was
detected after outcome inspection, so PARTICIPANT-001 is retained for audit but its confirmatory
validity is invalidated. See
`data/experiments/experiment_003/protocol_deviation_001_participant.json`.

The fill stream's maker/taker fields are source matching roles only. They are not interpreted as
passive versus aggressive order classification, order type, or BUY/SELL direction. `source_side` is
likewise never interpreted as aggressor BUY/SELL. Fills from the same block-second as a decision are
excluded. No wallet de-anonymisation or insider labelling is performed.

### Low-rank benchmark

A 30-second as-of panel is constructed in logit-change space. Rank 1 is primary and rank 2 is the
only sensitivity. Scaling and SVD loadings are TRAIN-only with deterministic sign convention.
Each target is excluded from its own factor predictor.

Baseline already contains target own lagged move and equal-weight cross-market mean. The factor
must add information beyond both. A 300-second delayed factor is the negative control.
## Inference and multiplicity

The primary search space is exactly five families × five common horizons = 25 hypotheses.

Benjamini-Hochberg FDR uses alpha 0.05 across the full preregistered family. Unavailable cells
remain unavailable but continue to count in family size; the runner does not invent p-values.

The primary paired test is a one-sided sign-flip/randomisation test of baseline-minus-challenger
squared-error differences aggregated into fixed 30-minute UTC blocks. Exact enumeration is used
through 18 blocks; otherwise 20,000 deterministic Monte Carlo draws are used.

Primary uncertainty is a 5,000-draw equal-event bootstrap. Observation-weighted clustered evidence
is secondary. Tick count is never presented as the independent sample size.

## BUILD-008 contract

Every family receives its own ResearchEvaluationSpec and ResearchEvaluationHarness. Its run ID
binds accepted DATA-001, exact code-freeze revision, all horizons, split geometry, full FDR
protocol, bootstrap/stability controls, negative controls, ablations, and the complete
preregistration SHA-256.

Final family reports are serialized ResearchReport objects and validated through BUILD-008.
require_execution_stress is false because this is predictive research, not execution research.

## Disposition

Promotion requires a positive OOS lift at a preregistered horizon, global-FDR survival,
non-negative equal-event lower uncertainty, negative-control passage, evidence across more than
one regime where possible, non-isolated parameter stability, and required ablations.

BUILD-008 can mechanically downgrade a requested promotion to INCONCLUSIVE if required evidence is
missing. REJECTED is used only for an affirmative falsifier; p > 0.05 alone is not rejection.
## DATA-001 limitations

DATA-001 has no FULL_EVENT_REPLAY stream. EXPERIMENT-003 makes no claims about exact queue position,
passive fill probability, cancellation timing, same-millisecond causal order, or sub-millisecond
sequencing.

Historical Polymarket evidence is not proof that the same effect exists on SIG. Any promoted family
still requires live SIG-relevant replication before production use.

## Empirical results

The corrected post-amendment run executed on code revision
`b2ba5e2c3e63d57bd6e74c0c5861bad67c983e1b`. The low-rank amendment changes only the
implementation of the already-preregistered minimum-reference coverage rule; the original
`7e94bb7` low-rank output is invalidated and retained only as audit history.

The canonical BUILD-008 reports mechanically return `INCONCLUSIVE` for all five executed lanes, but
the protocol audit adds a stricter validity distinction:

| Family | Report disposition | Confirmatory validity | Main result |
|---|---|---|---|
| LEADLAG-001 | INCONCLUSIVE | retained | No horizon is rejected in the computed global BH table; stability fails. |
| RV-001 / structural residual | INCONCLUSIVE | retained for primary estimates; stability diagnostic caveat | No evidence of positive predictive lift under the primary specification. |
| MICROSTRUCTURE-001 | INCONCLUSIVE | retained | Stability passes, but no horizon is rejected in the computed global BH table. |
| PARTICIPANT-001 | INCONCLUSIVE | **INVALIDATED** | Executed baseline and placebo differ materially from the frozen preregistration. |
| LOWRANK-001 | INCONCLUSIVE | retained after documented pre-result amendment | No horizon is rejected in the computed global BH table; stability fails. |

Under the executed implementation, PARTICIPANT-001 at 300 seconds is the only nominal rejection in
the original 25-test BH table (raw p approximately `0.0014`, BH q approximately `0.035`, positive
delta MSE). Because the participant lane does not exactly implement the frozen baseline and placebo,
that rejection is retained as audit evidence **only** and is not a valid preregistered discovery
claim. The 25-test BH table itself is preserved unchanged; it is not post-hoc recomputed to remove
the invalid lane.

### Post-hoc participant falsification

Because the executed PARTICIPANT-001@300s lane produced the only nominal BH rejection, bounded
post-hoc diagnostics were run as falsification and hypothesis-generation work. They cannot repair
the preregistration deviation or change EXPERIMENT-003's confirmatory status.

- Attribution: the effect weakens materially when the highest-volume participants are removed,
  indicating meaningful concentration rather than a uniformly distributed participant effect.
- Robustness: every leave-one-election-out estimate remains positive, although Peru first round
  contributes a large share of the observed lift.
- Twenty post-hoc participant-score assignment permutations produce an empirical upper-tail rate
  of approximately `0.0476` versus the real 300s delta MSE.
- A 100-draw raw-identity permutation null preserves fill timing, value, market activity and
  participant-frequency structure while destroying identity-to-time association. The real effect
  exceeds 99 of 100 draws (empirical upper-tail approximately `0.0198`).
- A separate role-agnostic reconstruction discards maker/taker matching-role semantics entirely,
  uses each address only as a participant identity, and still produces positive 300s lift
  (`delta MSE approximately 1.47e-5`, raw p approximately `0.0139`). It exceeds all 100
  role-agnostic identity permutations (empirical upper-tail approximately `0.0099`).

These diagnostics motivate a separately preregistered participant-identity follow-up, but they are
post-hoc and cannot promote or repair EXPERIMENT-003. Historical Polymarket evidence still requires
fresh validation before any SIG strategy use. EXP004A's maker-only/taker-only variants refer only to
the source matching role and must not be interpreted as passive/aggressive trading behaviour.

Canonical compact reports are under `data/experiments/experiment_003/results/`. Post-hoc diagnostic
outputs are under `data/experiments/experiment_003/posthoc/`, and the exact Kaggle runner scripts,
kernel metadata, hashes and kernel versions are preserved under
`scripts/kaggle/experiment_003_posthoc/` and the post-hoc runner manifest. Exact executed Python
wrappers are archived with a `.py.txt` suffix so repository lint does not rewrite or reinterpret
those immutable historical source bytes.

## Protocol deviations and validity

Two deviations were found during the post-result audit:

1. `EXPERIMENT-003-DEVIATION-001` is material and affects PARTICIPANT-001. The executed participant
   baseline and placebo differ from the frozen preregistration. Because this was detected after
   outcome inspection, the participant lane's confirmatory validity is invalidated rather than
   retroactively amended.
2. `EXPERIMENT-003-DEVIATION-002` affects only the RV-001 stability diagnostic: its 60/120/300-second
   sensitivity changes target and reference freshness together, while the preregistration labels the
   surface as reference-freshness sensitivity. Primary RV-001 estimates at the frozen 120-second
   freshness are unaffected; the stability surface must not be read as reference-only.

The low-rank coverage correction is different: it is recorded in
`amendment_001_lowrank_coverage.json` as a pre-result eligibility/implementation amendment, the
invalidated earlier run is not mixed with the corrected run, and the complete battery was rerun on
the amended code revision.

## Execution environment

The accepted DATA-001 output remains on Kaggle. EC2 is only the control plane used to publish the
frozen code package and inspect compact outputs. The empirical Kaggle kernel mounts accepted
DATA-001 directly and fails closed on its manifest hash.

Large observation/intermediate matrices remain outside Git. Only deterministic compact reports,
their hashes, registry/ledger updates and this documentation are committed.

The corrected canonical Kaggle run was rerun twice in separate private kernels using the same
frozen code package and accepted DATA-001 mount. Every compact JSON report and the run manifest
matched the canonical run byte-for-byte by SHA-256. This demonstrates deterministic reproducibility
of the frozen implementation; it is not an independent reimplementation. Exact evidence is recorded
in `data/experiments/experiment_003/results/reproduction_evidence.json`.