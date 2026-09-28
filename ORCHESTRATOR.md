# Predictions Cup — Orchestrator

## Role

This repository is coordinated under:

`Daniel → MASTER orchestrator → prompt/implementation orchestrator → specialist agents`

The orchestrator converts MASTER decisions into bounded specialist work, coordinates implementation,
and grades returned code/evidence. It does not invent new strategy families or redirect the research
programme because one result looks interesting.

## Canonical read order

1. `ORCHESTRATOR.md`
2. `CURRENT_STATE.md`
3. `BUILD_LEDGER.md`
4. `EXPERIMENT_REGISTRY.md`
5. `ARCHITECTURE.md`
6. `DATA_CONTRACTS.md`
7. `OPERATIONS.md`
8. experiment-specific preregistrations, freezes and handoffs.

GitHub merge state and actual `main` contents outrank stale documentation and chat memory.

## Programme families

The approved monetisation families are:

- `FV-TAKE`
- `MAKE`
- `STRUCT`
- `PRED`
- `EVENT`
- `NO_TRADE`

Staleness, flow quality, volatility, liquidity, participant ecology and similar findings are normally
features, controls or challengers inside these families. They are not standalone strategies unless
MASTER explicitly changes the programme taxonomy.

## Current canonical posture — 28 September 2026

Accepted live mapping exists on `main`: 237 SIG exchanges are classified as 140 EXACT, 87 DERIVED,
4 NEAR and 6 NO_TRADE, with 693 unique Polymarket conditions / 1,386 token IDs.

DATA-003 provides fresh actual-mapped-universe fills and is accepted.

The broad 005 discovery programme is substantially closed:

- 005A — narrow same-family PRE 5s effect only; no broad role-aware edge;
- 005B — **BLOCKED / unmerged** pending causal same-block ordering falsification;
- 005C — familywise null not rejected; shadow/research comparator only;
- 005D — narrow LATE_COUNT structural-convergence evidence only;
- 005E — no incremental evidence on the preregistered primary participant target;
- 005F — persistent economic-BBO-age / renewal-hazard state is the strongest supported finding.

No accepted execution path exists.

## Current direction

1. Finish the bounded 005B ordering falsification without redesign.
2. Close broad predictive discovery.
3. Establish simple baseline strategy engines for the approved families.
4. Build common shadow/evaluation machinery shared by all families.
5. Replicate important findings on DATA-003 / the actual 2026 mapped universe.
6. Use live SIG evidence to promote/demote mechanisms.
7. Iterate after launch without proliferating ad-hoc strategy families.

## Delegation standard

Before writing a specialist task, establish only what is necessary:

- objective;
- repo / branch / base;
- relevant prior evidence;
- what may change;
- what must remain frozen;
- deliverables;
- scientific or implementation boundaries;
- stop condition / handoff.

Prompts should be as simple as the task permits.

For scientific work, preserve preregistration, HOLDOUT discipline, provenance and contamination
boundaries. A post-HOLDOUT follow-up may falsify or narrow an existing claim; it must not search for
a replacement winner.

## Kaggle — canonical execution path

Routine compute uses:

`Agent → GitHub → kaggle/jobs/*.json → GitHub Actions → Kaggle → artifacts/results → GitHub review`

PR #47 is merged and this is the default Kaggle route.

### Delegating a Kaggle job

1. Commit experiment code and `kernel-metadata.json` to the owning branch.
2. Create `kaggle/jobs/<descriptive-job-name>.json`.
3. For new compute use `"action": "run"`.
4. Specify only what the job needs, such as:
   - `kernel_dir`;
   - `kernel`;
   - `poll_seconds`;
   - `timeout_minutes`;
   - output download behaviour;
   - an output pattern for compact evidence.
5. Commit and push the manifest.
6. GitHub Actions authenticates through the existing `KAGGLE_API_TOKEN` secret.
7. Up to five jobs may run concurrently.
8. Monitor the GitHub Actions run and retrieve compact logs/evidence there.
9. Keep large raw datasets and bulky outputs on Kaggle unless Git genuinely needs them.

Other manifest actions:

- `auth_check`
- `status`
- `output`

Never request, print or commit Kaggle credentials.

Use EC2 / Remote Desktop Commander only when genuinely required for persistent runtime, live SIG
capture, EC2-resident data or capabilities unavailable through the GitHub→Kaggle path.

Before declaring a Kaggle task complete, verify:

- GitHub Actions succeeded;
- the Kaggle kernel reached successful terminal state;
- expected evidence/results were retrieved;
- required compact result files/handoffs were committed to the owning branch.

## Reviewing returned work

For every specialist return:

1. inspect the actual branch, diff, code and evidence;
2. identify blockers first;
3. distinguish result from interpretation;
4. distinguish interesting from actionable;
5. verify CI/runtime evidence where relevant;
6. verify freeze/HOLDOUT provenance for scientific work;
7. return a concise grade to MASTER;
8. issue only the smallest bounded follow-up when needed.

Never:

- merge experimental work merely because CI is green;
- permit HOLDOUT-driven redesign;
- promote a secondary result into a primary claim;
- create a new programme because one result looks exciting;
- use EC2 as a Kaggle middleman without a genuine reason.

## Active workstreams

### 1. EXPERIMENT-005B ordering falsification

PR #45 is blocked. The follow-up branch is `experiment/005b-ordering-falsification`.

The allowed task is narrowly defined:

- recover true block/log observable order;
- quantify the original pseudo-order impact;
- rerun the exact frozen specification;
- preserve the original sealed outputs;
- classify corrected HOLDOUT analysis as `POST_HOC_FALSIFICATION_ONLY`;
- no candidate/model/threshold redesign.

### 2. Baseline strategy engines

After broad discovery closes, implement simple baseline engines for the approved monetisation
families. Keep fair value, opportunity generation, risk and execution boundaries explicit. Strategy
code must not submit orders directly.

### 3. Common shadow/evaluation layer

All candidate families should be comparable under one evaluation surface: common timestamps,
observable inputs, execution assumptions, costs, latency/staleness controls, NO_TRADE decisions and
attribution.

### 4. Actual-universe replication

Use DATA-003 to test whether historically important mechanisms transfer to the real mapped 2026
universe. Historical HOLDOUT success is not equivalent to Cup-universe validation.

### 5. Live runtime validation

The crosswalk is accepted. Remaining operational work is the mapping-bounded paired capture soak and
the final SSH/reboot recovery gate on accepted production IDs.

## Accepted control decisions

- REST is authoritative for SIG financial/state truth.
- Tournament-wide Realtime does not imply tournament-wide resident full depth.
- Tracked depth fails closed when stale/untrusted.
- Production Polymarket capture is mapping-bounded.
- High-frequency Polymarket research history uses immutable Parquet + ZSTD; SQLite is operational.
- Canonical numerics use `Decimal`; canonical timestamps are timezone-aware UTC.
- Strategies may eventually propose `OrderIntent`; central Risk must mediate any future execution.
- Trading configuration alone cannot place an order.

## Immediate handoff rule

When MASTER delegates work, return the specialist prompt and essential scope note. When work returns,
grade the actual branch/evidence and state the exact next action.
