SHADOW-002 status:
IMPLEMENTED / REVIEW REQUIRED — core SHADOW acceptance tests and full GitHub CI PASS.
Not declared launch-ready: live daemon wiring and exact PRED-006 / 005F / R3
providers remain explicit blockers.

Branch:
build/shadow-002-champion-challenger-bus

Head:
CI-validated implementation head:
634ff0431b8ff06e1bdcd2f773b0c352f3b90c8a

This handoff file is committed after that validated implementation head, so the
branch tip will differ by the handoff-only documentation commit.

LIVE order placement:
DISABLED / NOT IMPLEMENTED BY THIS BUILD

Canonical state contract:
CanonicalShadowSnapshot wraps the existing MakerMarketSnapshot/RuntimeSnapshot at
one explicit decision boundary. It carries a deterministic snapshot_id, UTC
observable time, local monotonic time, accepted mapping version, source
revision/provenance, SIG/account/inventory state and mapped Polymarket quotes.
External quote mappings are copied read-only before fan-out. Persisted snapshot
reconstruction re-fingerprints the observable state and fails closed on mismatch.

Candidate decision contract:
CandidateOutput -> CandidateDecision. Durable fields include
candidate/version/family, snapshot provenance, market/exchange/tournament identity,
optional FV/bounds/confidence, direction/score/action/quote intent,
status/abstention/quality flags, compute timing and JSON-safe candidate payload.
Decision IDs are deterministic from snapshot/candidate/version. NaN/Inf/out-of-range
probability outputs fail closed as INVALID_OUTPUT.

Candidates wired:
- MAKE: WIRED through MakerCandidate -> existing MakerEngine.quote(); hypothetical only.
- Direct PM: WIRED through existing DirectPolymarketFairValueProvider + accepted mapping.
- PRED-006: ADAPTER WIRED; exact runtime evaluator absent => NOT_READY, no approximation.
- 005F: ADAPTER WIRED; exact live hazard evaluator absent => NOT_READY; never directional FV.
- R3/ETS hook: WIRED as optional provider; default no-op => NOT_READY.

Same-state fan-out test:
PASS

Candidate-failure isolation:
PASS — exception, timeout and invalid-output paths are isolated and persisted while
other candidates continue.

Replay determinism:
PASS — deterministic candidates replay to identical semantic hashes; persisted
snapshots reconstruct to the same fingerprint.

Backpressure:
PASS — bounded per-candidate queues; production default 512 pending states, enough
for one 237-market Cup sweep with headroom. Explicit capacity=1 test proves
oldest-pending coalescing, bounded memory and skip accounting. The 237-market CI
benchmark observed 0 coalesced states and queue high-water 237.

Restart/persistence:
PASS — append-only JSONL journal preserves prior events across restart and appends
new snapshot/decision events; replay loader reconstructs both pre- and post-restart
snapshots.

237-market benchmark:
PASS on GitHub Actions / Python 3.12:
- markets: 237
- candidates: 6
- decisions: 1,422
- coalesced: 0
- fan-out throughput: 9,490.16 decisions/second
- publish overhead p50: 8.94 us
- publish overhead p95: 11.31 us
- publish overhead p99: 24.08 us
- candidate evaluation p50: approximately 574-580 us
- candidate evaluation p95: approximately 684-700 us
- candidate evaluation p99: approximately 880-974 us
- candidate queue high-water: 237
- persistence sample: 32 snapshots / 64 events / 66,476 bytes
- persistence elapsed: 36.46 ms
- persistence queue high-water: 56
- persistence healthy: true

CI evidence:
GitHub Actions run 36646010486 — PASS
- Ruff lint: PASS
- shell validation: PASS
- strict mypy: PASS
- pytest: 643 passed, 3 skipped
- application smoke: PASS
- BUILD-009 benchmark smoke: PASS
- MAKE-001 benchmark smoke: PASS
- MAKE-001 benchmark full: PASS
- MAKE-001 service smoke: PASS
- SHADOW-002 237-market benchmark: PASS

Safety evidence:
- ShadowBus refuses trading_enabled=True.
- SHADOW imports no SIG write client/sink and exposes no order-placement method.
- CandidateDecision remains hypothetical and cannot bypass BUILD-009 central Risk.
- PRED-006, 005F and R3/ETS fail closed as NOT_READY without exact stable providers.

Known blockers:
- The live MakerService / launch event source has not yet been wired to publish the
  canonical MakerMarketSnapshot sequence into ShadowBus. The production bus,
  contracts, adapters and integration runbook exist, but this final launch-runtime
  connection still requires review/implementation.
- PRED-006 exact live feature-parity evaluator is not present in merged main.
- 005F exact live feature-parity hazard evaluator is not present in merged main.
- R3/ETS research has not yet published a stable structural-FV provider.
- Remote EC2/Desktop Commander hosts were offline during this implementation, so
  host-local acceptance was not executed there. GitHub CI is the executed software
  acceptance evidence for this branch.

Independent morning-review answers:
1. Same canonical snapshot delivered identically to every challenger? YES — tested.
2. Every wired challenger can emit a versioned persisted decision? YES; unavailable
   research providers emit versioned NOT_READY decisions rather than fabricated output.
3. One challenger can crash without affecting the others? YES — tested.
4. Slow challengers isolated without blocking other candidate workers? YES — timeout
   and per-candidate bounded queues tested. Python cannot force-kill a timed-out worker
   thread, so candidate evaluators must remain pure/read-only.
5. Outputs replay deterministically? YES — tested for deterministic candidates.
6. One strategy can be disabled independently? YES — tested.
7. LIVE-LEARN can consume one common decision stream without bespoke journal formats?
   YES — stable common snapshot/decision envelope implemented.
8. Observable-time provenance preserved? YES — snapshot and decision contracts carry
   UTC observable time plus monotonic local time and source/mapping provenance.
9. Runtime bounded under load? YES — bounded queues, explicit coalescing and benchmark.
10. Real order placement impossible through SHADOW-002 itself? YES — no write path and
    trading_enabled=True is rejected.

Morning-review conclusion:
Core SHADOW-002 bus is reviewable and CI-green. Do not promote this PR to
launch-ready until the live canonical-snapshot feed into ShadowBus is wired and
reviewed. PRED-006 / 005F / R3 may remain NOT_READY without blocking the baseline
MAKE + Direct-PM shadow laboratory.
