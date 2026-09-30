SHADOW-002 status:
LAUNCH_READY_PENDING_FULL_STACK_REHEARSAL

Branch:
build/shadow-002-champion-challenger-bus

CI-validated implementation head:
54d27514995c959c29726f4d1ae0d3e41830cc34

This handoff file is committed after that validated implementation head, so the
branch tip differs by a documentation-only commit.

LIVE order placement:
IMPOSSIBLE THROUGH SHADOW-002.
SHADOW has no SIG write adapter, placement dispatcher, cancel dispatcher or
execution sink. ShadowBus rejects trading_enabled=True. Existing BUILD-009 central
Risk remains authoritative for any separate execution path.

Production launch wiring:
IMPLEMENTED.

Actual path:
SIG + Polymarket in-memory state
-> MakerSourceBridge
-> exact MakerMarketSnapshot used by MAKE
-> MakerRuntimeLoop synchronous non-blocking snapshot observer
-> bounded ShadowBus ingress
-> one CanonicalShadowSnapshot per market decision boundary
-> same snapshot object / snapshot_id to every enabled challenger
-> versioned CandidateDecision
-> JSONL replay journal + CAPTURE strategy_events decision mirror.

The MAKE observer performs bounded put_nowait admission only. It never awaits
SHADOW persistence or challenger computation. SHADOW queue/persistence pressure
therefore cannot block the MAKE coordinator, central Risk or execution path.
Ingress saturation is explicit through ingress_rejected / ingress_error.

Canonical state contract:
CanonicalShadowSnapshot wraps the existing immutable MakerMarketSnapshot /
RuntimeSnapshot decision state. It carries deterministic snapshot_id, UTC observable
time, local monotonic time, mapping version, runtime/source revision and provenance.
Persisted snapshot replay re-fingerprints the state and fails closed on mismatch.

Candidate decision contract:
CandidateOutput -> CandidateDecision with deterministic decision_id and:
candidate/version/family, snapshot provenance, market/exchange/tournament identity,
optional FV/bounds/confidence, direction/score/action/quote intent,
status/abstention/quality flags, compute timing and JSON-safe candidate payload.
NaN/Inf/out-of-range probability outputs fail closed as INVALID_OUTPUT.

Candidates wired:
- MAKE: WIRED through the existing MakerEngine.quote(); hypothetical only.
- Direct PM: WIRED through existing DirectPolymarketFairValueProvider + accepted mapping.
- PRED-006: adapter WIRED; exact runtime evaluator absent => NOT_READY, no approximation.
- 005F: adapter WIRED; exact live hazard evaluator absent => NOT_READY; never directional FV.
- R3/ETS: stable optional provider hook; absent provider => NOT_READY.

Timeout isolation:
PASS.
At most one evaluation may be in flight per candidate. A timeout records TIMEOUT,
quarantines that candidate, drains/skips pending states and blocks new evaluations
until the underlying call actually returns. Health exposes quarantined, in_flight,
timeout_count, quarantine_count and skipped states. Regression coverage proves
repeated publishes after a timeout cannot spawn concurrent timed-out evaluations.

Persistence:
PASS.
- JSONL is the authoritative immutable replay journal for canonical snapshots +
  CandidateDecision events.
- JSONL writer batches records (default 256) and fsyncs per batch, not per event.
- bounded default persistence queue: 65,536.
- health exposes queue depth/high-water, write batches and p95 batch latency.
- CandidateDecision is additionally mirrored into CAPTURE-001 strategy_events via
  the existing bounded ImmutableCaptureSink when shadow_capture_mirror_enabled=true.
- CAPTURE payload_json includes decision_id and input_snapshot_id.
- LIVE-LEARN / first-hours analytics use strategy_events as the common decision
  surface and reconcile exact state to JSONL by decision_id / input_snapshot_id.

Replay/restart:
PASS.
Append-only journal preserves old events across restart, appends new events and
reconstructs canonical snapshots with deterministic fingerprints. Deterministic
candidate replay produces stable semantic hashes.

Same-state / launch-boundary tests:
PASS.
- multiple candidates receive the identical CanonicalShadowSnapshot object.
- MakerRuntimeLoop snapshot observer receives the exact same MakerMarketSnapshot
  objects subsequently supplied to the MAKE coordinator.
- production LiveShadowRuntime test persists exactly one snapshot event for one
  market boundary plus all five configured candidate decisions.

Failure isolation:
PASS.
Candidate exception, timeout and invalid output do not stop other candidate workers.
Slow/wedged candidates are bounded through one-in-flight quarantine.

Control plane:
PASS.
Independent candidate enable/disable and global SHADOW pause/resume are implemented.
Health includes candidate state, last success/error, snapshot age, queue
depth/high-water, skipped states, timeouts/quarantines, p50/p95/p99 evaluation
latency, p95 queue delay, ingress metrics and persistence health.

One-shot 237-market benchmark:
PASS on GitHub Actions / Python 3.12.
- 237 markets x 6 candidates = 1,422 decisions
- coalesced: 0
- fan-out throughput: 10,162 decisions/second
- publish overhead p50/p95/p99: 8.81 / 13.21 / 33.95 us
- candidate evaluation p50: approximately 526-534 us
- candidate evaluation p95: approximately 770-798 us
- candidate evaluation p99: approximately 974-1,025 us
- candidate queue high-water: 237
- persistence sample: 32 snapshots / 64 events
- persistence elapsed: 12.01 ms
- persistence queue high-water: 32
- persistence healthy: true

Sustained production-shaped persistence/backpressure soak:
PASS.
Configuration:
- 237 markets
- 6 candidates
- 20 repeated full-universe cycles
- 500 ms between cycles
- 4,740 canonical snapshots
- 28,440 candidate decisions
- 33,180 expected persisted events

Observed:
- ingress_rejected: 0
- coalesced_or_dropped: 0
- persisted_events: 33,180
- JSONL readback events: 33,180
- ingress queue high-water: 237
- candidate queue high-water: 237
- persistence queue high-water: 237
- persistence write batches: 1,606
- persistence p95 batch-write latency: 3.59 ms
- event throughput: 3,280 events/second
- non-blocking MAKE->SHADOW submission p50/p95/p99:
  2.77 / 3.41 / 11.86 us
- drain after producer: 0.14 ms
- persistence health: true

Adversarial overload observation:
An earlier deliberately aggressive 237-market full-universe sweep every 200 ms
produced explicit candidate coalescing (961 states). This was not silent loss and
did not block MAKE. It established the overload boundary and motivated the
production-shaped zero-drop gate above. The accepted sustained gate is still a very
heavy 2 full-universe sweeps/second for 10 seconds. Launch rehearsal must monitor
queue high-water and ingress_rejected rather than assuming unlimited throughput.

CAPTURE evidence:
PASS.
A regression test verifies SHADOW decisions publish into CAPTURE strategy_events
Parquet. JSONL remains the replay source; CAPTURE is the common analytics/evaluation
surface.

CI evidence:
GitHub Actions run 36689038449 — PASS.
- Ruff lint: PASS
- shell validation: PASS
- strict mypy: PASS
- pytest: 648 passed, 3 skipped
- application smoke: PASS
- BUILD-009 benchmark smoke: PASS
- MAKE-001 benchmark smoke: PASS
- MAKE-001 benchmark full: PASS
- MAKE-001 service smoke: PASS
- SHADOW-002 one-shot benchmark: PASS
- SHADOW-002 sustained persistence soak: PASS

Research candidates still intentionally NOT_READY:
- PRED-006 exact live feature-parity evaluator is not present in merged main.
- 005F exact live feature-parity hazard evaluator is not present in merged main.
- R3/ETS has not yet published a stable production provider.
These do not block the baseline MAKE + Direct-PM shadow laboratory and must not be
approximated.

Remaining acceptance before launch:
One production-host full-stack rehearsal with real SIG + mapped Polymarket feeds and
SHADOW enabled. Gate:
- zero SHADOW live-order capability;
- ingress_rejected == 0;
- no unexplained coalescing/timeouts/quarantines;
- bounded queue high-water;
- healthy JSONL and CAPTURE strategy_events;
- restart preserves journal and new events continue;
- MAKE latency/execution behavior materially unchanged.

Morning-review conclusion:
The four review blockers are addressed in code and CI:
1. real launch runtime wiring: IMPLEMENTED + tested;
2. timeout isolation: BOUNDED one-in-flight quarantine + tested;
3. sustained persistence/backpressure: ZERO-DROP soak PASS;
4. canonical evidence surface: CAPTURE strategy_events mirror + documented
   JSONL reconciliation.

Status may now advance from CORE_ACCEPT / LAUNCH_INTEGRATION_REQUIRED to
LAUNCH_READY_PENDING_FULL_STACK_REHEARSAL. PR remains draft and is not merged.
