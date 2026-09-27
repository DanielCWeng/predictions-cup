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
This is a predictive-coordinate metric in logit space, not a P&L metric. Equal probability moves
near 0/1 correspond to larger logit moves than equal-sized moves near 0.5, so tail markets can
receive disproportionate squared-loss weight. sqrt(delta MSE) must not be converted directly into
cents.

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

The frozen geometry produces 15 daily HOLDOUT folds. Only one starts on an election day; four of
five regime windows have exclusively post-election HOLDOUT folds. EXPERIMENT-003 therefore provides
predominantly post-election/count/settlement-regime evidence and is **not election-night
validation**. Future election-night claims require event-time/election-time holdouts, preferably at
whole event-family level.

## Family definitions

### Cross-market lead/lag

Primary lookback is 5 seconds; 1/5/30 seconds are fixed sensitivities. Baseline is the target's own
lagged logit move. Challenger adds mean past logit move of explicit related markets. A 300-second
delayed-reference signal is the negative control.

### Structural residual

Primary reference is an equal-weight logit mean over explicit indirect relationships. Baseline is
target autoregression; challenger adds the naive level-gap feature
`-(logit(target level) - mean(logit(reference levels)))`. There are no pair-specific TRAIN-fitted
offsets/slopes or calibrated structural mappings. Reference freshness 60/120/300 seconds is the
frozen sensitivity surface.

The frozen naive equal-weight logit-level-gap specification was tested and performed weakly. A
broader calibrated structural/fair-value residual in the stronger pair-specific sense was **not**
tested. LOO-PRICE-001 is a same-event sibling level-gap diagnostic, not an exact
exhaustive-partition reconstruction. LOO-FAMILY-001 is the genuinely indirect construction.
### Microstructure

Primary information is top-of-book imbalance and microprice displacement divided by spread.
Under the implemented top-level formula, `microprice_displacement_over_spread = imbalance / 2`,
so these two advertised additions represent one degree of information rather than two independent
microstructure signals. Baseline includes own lagged move, spread, top-level depth and quote age.
Primary lookback is 5 seconds with 1/5/30-second sensitivity.

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
through 18 blocks; otherwise 20,000 deterministic Monte Carlo draws are used. These p-values are
conditional on the observed regime windows; they are not cross-election-family randomisation tests.

The object labelled `equal_event_bootstrap` is a 5,000-draw bootstrap over regime-window
`EventEvidence` rows. The five regime windows belong to only three event families
(COL_2026, HUN_2026, PER_2026), so it is **not** an independent-election-family bootstrap and must
not be interpreted as a calibrated cross-election 95% confidence interval. LOWRANK has usable
evidence from only one regime/event family; its equal-event interval is therefore a degenerate
one-window resample and non-inferential for cross-event uncertainty.

Observation-weighted clustered evidence is secondary. Tick count is never presented as the
independent sample size. Future confirmation should aggregate/hold out at event-family level and
add dependence-aware model-comparison evidence plus block-size robustness.

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
one regime where possible, non-isolated parameter stability, and required ablations. The frozen
`events > 1` gate is only a regime-window count; it is weaker than independent event-family
replication. No false promotion occurred in EXPERIMENT-003, but this gate must be strengthened
before reuse.

BUILD-008 can mechanically downgrade a requested promotion to INCONCLUSIVE if required evidence is
missing. REJECTED is used only for an affirmative falsifier; p > 0.05 alone is not rejection.
## DATA-001 limitations

DATA-001 has no FULL_EVENT_REPLAY stream. EXPERIMENT-003 makes no claims about exact queue position,
passive fill probability, cancellation timing, same-millisecond causal order, or sub-millisecond
sequencing.

QuoteSeries is built from `book_changes` only. The 120-second freshness rule therefore requires a
recent change row at the queried timestamp; a quiet unchanged book can become ineligible even if
the feed itself remained healthy. This is not equivalent to independent heartbeat/feed-health
evidence and should be separated in future research.

The frozen ablation layer is also weaker than its labels imply: structural "ablation passed" checks
only that both LOO lanes contain observations; non-structural ablations are recorded `passed=True`
by construction; and the RV zero-residual control algebraically collapses to baseline. No false
promotion occurred, but future BUILD-008 promotion must require substantive ablation evidence.

The 30-second clock grid combined with a 5-second primary lead/lag lookback samples a narrow slice
of impulses. LEADLAG-001 should therefore be read as a weak same-venue historical screen, not a
decisive event-time impulse-response test. The frozen REJECTED rule also has no smallest
economically meaningful effect/futility threshold; future batteries should preregister one.

Historical Polymarket evidence is not proof that the same effect exists on SIG. Any promoted family
still requires live SIG-relevant replication before production use. Full limitations are recorded
in `data/experiments/experiment_003/inference_limitations.json`.

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

- Attribution initially appeared concentrated in the highest-volume identities. The two dominant
  hashes are now confirmed as **Polymarket NegRisk exchange contracts, not traders**:
  `6dd717a425ce` maps to NegRisk CTF Exchange V1
  (`0xc5d563a36ae78145c45a50134d48a1215220f80a`) and `229cefd48266` maps to NegRisk CTF
  Exchange V2 (`0xe2222d279d744050d28e00520010520000310f59`). They account for roughly 20–27% of
  EXP004A volume in the regimes where each is the top identity. The evidence must therefore not be
  described as trader-skill concentration.
- Robustness uses **leave-one-regime-window-out**, not leave-one-election-family-out. Every such
  estimate remains positive, but omitting a first round still leaves the corresponding runoff in
  sample. It does not establish independent election-family generalisation.
- Twenty post-hoc participant-score assignment permutations produce an empirical upper-tail rate
  of approximately `0.0476` versus the real 300s delta MSE.
- EXP004C's 100 raw-identity null draws are themselves poorly calibrated as challenger-vs-baseline
  p-value tests: 29/100 have raw p < 0.05 and 87/100 have positive delta MSE. The real effect still
  ranks unusually high (empirical upper tail approximately `0.0198`), but that ranking does not
  isolate trader identity skill because the permutation also destroys protocol-contract
  identity-to-fill-type association.
- EXP004D's role-agnostic null shows the same issue: 30/100 null draws have raw p < 0.05 and 92/100
  have positive delta MSE. The real role-agnostic effect exceeds all 100 null draws (empirical upper
  tail approximately `0.0099`), but this remains participant/**protocol**-identity-conditioned
  exploratory structure, not clean trader-skill evidence.
- The executed participant negative control is itself nominally significant at 30s, 60s and 300s
  (approximately p=0.0252, 0.00065 and 0.0333 respectively).
- Tightening minimum TRAIN history from 5 to 10 cuts the 300s delta MSE from approximately
  `1.53e-5` to `3.98e-6`; the equal-regime-window bootstrap interval then crosses zero.

These diagnostics motivate a separately preregistered participant-identity follow-up, but they are
post-hoc and cannot promote or repair EXPERIMENT-003. The strongest defensible current description
is **participant/protocol-identity-conditioned exploratory structure**. The next experiment must
exclude known infrastructure/exchange/adapter contracts (or model them explicitly as a separate
nuisance class), cross-fit participant encodings within TRAIN, add activity × momentum nuisance
structure, use a nuisance-preserving identity-null primary contrast, and perform true
leave-one-event-family-out checks.

A snapshot of PolyLeviathan's canonical infrastructure registry is now vendored under
`data/reference/polymarket_infrastructure/` with source commit/blob provenance. EXP004A's
maker-only/taker-only variants refer only to source matching role and must not be interpreted as
passive/aggressive trading behaviour. Fee-derived economic maker/taker classification is a separate
future analysis and does not alter this post-hoc validity correction.

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
the amended code revision. The amendment operationalises "minimum 3 references" as **exactly the
first three references in the frozen coverage ordering**. That is a documented label-free design
choice, not the unique meaning of "at least 3"; with usable evidence from only Peru first round,
LOWRANK remains severely underpowered.

## Execution environment

The accepted DATA-001 output remains on Kaggle. EC2 is only the control plane used to publish the
frozen code package and inspect compact outputs. The empirical Kaggle kernel mounts accepted
DATA-001 directly and fails closed on its manifest hash.

Large observation/intermediate matrices remain outside Git. Only deterministic compact reports,
their hashes, registry/ledger updates and this documentation are committed.

The corrected canonical Kaggle run used frozen Kaggle code dataset
`polyleviathan/sig-cup-exp003-code`, dataset ID `12219381`, **version 2**, plus the accepted
DATA-001 mount. Two separate reruns using that package matched every compact JSON report and the run
manifest byte-for-byte.

A fresh authenticated download of dataset version 2 reproduces the recorded wheel SHA-256
`b2895b1237ef4331f5ec381037983bf9db251e99f64cff4118f5f7a15a3ac39a`. However, the
historical canonical launcher asserted `COMMIT.txt`, preregistration, relationship inventory and
corpus manifest before installation but **did not runtime-assert the wheel SHA itself**. The exact
launcher is preserved unchanged. The current evidence therefore binds the retained Kaggle v2
package retrospectively and demonstrates deterministic reproducibility, but it must not be
described as runtime cryptographic wheel binding or independent reimplementation. Future launchers
must assert the wheel hash before installation.

Exact evidence is recorded in
`data/experiments/experiment_003/results/reproduction_evidence.json`.