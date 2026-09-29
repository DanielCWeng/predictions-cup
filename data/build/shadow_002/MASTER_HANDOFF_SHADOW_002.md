SHADOW-002 status:
IMPLEMENTED — CI / independent acceptance pending

Branch:
build/shadow-002-champion-challenger-bus

Head:
PENDING FINAL CI/FIXUP COMMIT

LIVE order placement:
DISABLED / NOT IMPLEMENTED BY THIS BUILD

Canonical state contract:
CanonicalShadowSnapshot wraps the existing MakerMarketSnapshot/RuntimeSnapshot at one
explicit decision boundary. It carries a deterministic snapshot_id, UTC observable
time, local monotonic time, accepted mapping version, source revision/provenance,
SIG/account/inventory state and mapped Polymarket quotes. External quote mappings are
copied read-only before fan-out.

Candidate decision contract:
CandidateOutput -> CandidateDecision. Durable fields include candidate/version/family,
snapshot provenance, market/exchange/tournament identity, optional FV/bounds/confidence,
direction/score/action/quote intent, status/abstention/quality flags, compute timing and
JSON-safe candidate payload. Decision IDs are deterministic from snapshot/candidate/version.

Candidates wired:
- MAKE: WIRED through MakerCandidate -> existing MakerEngine.quote(); hypothetical only.
- Direct PM: WIRED through existing DirectPolymarketFairValueProvider + accepted mapping.
- PRED-006: ADAPTER WIRED; exact runtime evaluator absent => NOT_READY, no approximation.
- 005F: ADAPTER WIRED; exact live hazard evaluator absent => NOT_READY; never directional FV.
- R3/ETS hook: WIRED as optional provider; default no-op => NOT_READY.

Same-state fan-out test:
IMPLEMENTED — CI PENDING

Candidate-failure isolation:
IMPLEMENTED — CI PENDING

Replay determinism:
IMPLEMENTED — CI PENDING

Backpressure:
IMPLEMENTED — bounded per-candidate queues with explicit oldest-pending coalescing;
CI PENDING

Restart/persistence:
IMPLEMENTED — append-only JSONL snapshot/decision event journal + replay loader;
CI PENDING

237-market benchmark:
scripts/benchmark_shadow002.py implemented. CI result pending.

Known blockers:
- The live launch daemon/event source has not yet been wired to call ShadowBus.publish().
- PRED-006 exact live feature-parity evaluator is not present in merged main.
- 005F exact live feature-parity evaluator is not present in merged main.
- R3/ETS research has not yet published a stable provider implementation.
- Remote EC2/Desktop Commander hosts were offline during implementation, so local host
  acceptance could not be run there; GitHub CI is the executable gate for this branch.
