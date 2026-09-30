# OBSERVE-001 Operator Runbook

## Purpose

OBSERVE-001 records venue/execution mechanics and official competition context without changing
strategy, fair value, MAKE quoting or BUILD-009 Risk.

It is safe to validate in read-only/SHADOW mode. Do not create a real SIG order merely to test
instrumentation.

## Safety boundary

OBSERVE must never become an execution dependency.

Expected failure behavior:

- observation queue full -> observation dropped, counter increments, execution continues;
- observation sink failure -> failure counter increments, execution continues;
- CAPTURE storage failure -> visible storage failure; Risk/execution still remain independent;
- missing official context field -> persist `unavailable`, never approximate it;
- missing server/source timestamp -> persist null, never relabel local time as server time.

**REAL SIG ORDERS SENT: NO** for OBSERVE validation unless a separately authorized LIVE session
already exists for competition trading.

## 1. Exact code check

Before enabling OBSERVE on a runtime host:

```bash
git rev-parse HEAD
git status --short
```

The SHA must match the reviewed OBSERVE-001 head.

Then run:

```bash
ruff check .
mypy
pytest -q
python scripts/benchmark_observe001.py --iterations 100000
```

## 2. Required existing configuration

OBSERVE adds no new environment variables.

The standalone SIG capture path reuses:

```text
PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<participant/read credential>
PREDICTIONS_CUP_TOURNAMENT_ID=<Cup tournament UUID>
PREDICTIONS_CUP_TOURNAMENT_SLUG=<Cup tournament slug, where required>
PREDICTIONS_CUP_SIG_RESEARCH_PATH=<immutable research root>
PREDICTIONS_CUP_SIG_CAPTURE_QUEUE_MAX=<existing CAPTURE queue size>
PREDICTIONS_CUP_SIG_CAPTURE_PARQUET_SHARD_SECONDS=<existing CAPTURE shard interval>
PREDICTIONS_CUP_SIG_CAPTURE_PARQUET_MAX_ROWS_PER_SHARD=<existing CAPTURE row cap>
```

SHADOW validation should keep:

```text
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
PREDICTIONS_CUP_TRADING_ENABLED=false
```

No trade credential is required for read-only capture/context validation.

## 3. Read-only SIG capture

Start through the accepted supervised runtime where possible.

Manual finite smoke:

```bash
python -m predictions_cup.sig.capture   --runtime-env-only   --tournament-id <TOURNAMENT_UUID>   --run-seconds 120   --print-health
```

Expected OBSERVE behavior:

- SIG Realtime initializes through existing authoritative REST flow;
- reconnect/revision-gap observations are emitted when those transitions occur;
- official competition context snapshots are sampled without a separate polling loop;
- `venue_observations/` and `competition_context/` appear under the SIG research root;
- context includes leaderboard/`myRank` if the participant API returns them;
- Super Signal is recorded as unavailable for participant credentials;
- no SIG trade write is sent.

## 4. MAKE SHADOW

The MAKE service now opens an observation-only CAPTURE writer against the same research root.
It does not open the SIG operational capture SQLite database.

Use existing MAKE SHADOW safety configuration:

```text
PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
PREDICTIONS_CUP_TRADING_ENABLED=false
PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=false
PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<participant/read credential>
PREDICTIONS_CUP_TOURNAMENT_ID=<Cup tournament UUID>
PREDICTIONS_CUP_TOURNAMENT_SLUG=<Cup tournament slug>
```

Start:

```bash
python -m predictions_cup.maker.service --runtime-env-only
```

Expected new evidence:

- SIG Realtime reconnect/gap observations;
- account reconciliation observations;
- context snapshots;
- derived local MAKE quote lifecycle markers;
- no real SIG execution writes.

## 5. LIVE integration boundary

OBSERVE does not authorize LIVE.

If a separately authorized LIVE competition session already uses MAKE/BUILD-009, the same
instrumentation records:

- decision observation;
- plan creation;
- request enqueue;
- client-call dispatch;
- client return / parsed response;
- ACK;
- fill evidence;
- cancel request / confirmation;
- typed rejection;
- UNCERTAIN;
- reconciliation;
- final 429 / 5xx / transport-unknown evidence.

Do not enable LIVE merely to populate OBSERVE.

## 6. Health counters

For the standalone SIG capture command, `--print-health` includes OBSERVE/context state.

For MAKE/FULLSTACK composition, read `MakerService.observation_health()` directly. The returned
typed snapshot contains outer emitter and inner CAPTURE writer health and can be serialized with
`to_dict()`; no log parsing is required.

Check:

- `state` / `reasons`;
- `observe.accepted`;
- `observe.dropped`;
- `observe.sink_failures`;
- `observe.queue_depth`;
- `observe.queue_high_water`;
- `competition_context.failures`;
- `competition_context.last_error`;
- normal CAPTURE `dropped_rows` and `storage_failures`.

Launch expectation:

- combined OBSERVE state = `HEALTHY`;
- OBSERVE `dropped = 0`;
- OBSERVE `sink_failures = 0`;
- CAPTURE `dropped_rows = 0`;
- CAPTURE `storage_failures = 0`.

A non-zero OBSERVE drop count is a forensics-quality degradation. It is not permission to block
Risk/execution or to bypass the bounded emitter.

## 7. Persisted locations

Under `PREDICTIONS_CUP_SIG_RESEARCH_PATH`:

```text
venue_observations/YYYY/MM/DD/HH/*.parquet
competition_context/YYYY/MM/DD/HH/*.parquet
```

Existing CAPTURE streams remain unchanged.

Each observation row includes:

- UTC wall time;
- process-local monotonic time;
- process instance ID;
- canonical operation/intent/idempotency identities where applicable;
- source/provenance/version.

## 8. Reconstruct one execution lifecycle

Use the canonical BUILD-009 logical operation ID as the join key.

Expected conceptual sequence for a successful single placement:

```text
DECISION_OBSERVED
PLAN_CREATED
REQUEST_ENQUEUED
REQUEST_DISPATCHED
RESPONSE_RECEIVED
RESPONSE_PARSED
ACK
[PARTIAL_FILL ...]
[FILL]
```

An uncertain lifecycle can instead show:

```text
REQUEST_DISPATCHED
SERVER_ERROR or TRANSPORT_EXCEPTION
UNCERTAIN
RECONCILIATION_STARTED
RECONCILIATION_RESOLVED
```

Cancellation:

```text
CANCEL_REQUESTED
RESPONSE_RECEIVED
RESPONSE_PARSED
CANCEL_ACK
```

Duplicate ACK/fill evidence is retained and counted by the summary layer; it is not silently
deduplicated away.

## 9. Timestamp interpretation

### Safe duration calculations

Subtract `monotonic_ns` only when `process_instance_id` is identical.

Useful spans include:

- decision -> dispatch;
- enqueue -> ACK;
- dispatch -> client response;
- dispatch -> ACK;
- ACK -> first fill;
- dispatch -> fill;
- cancel -> ACK;
- reconnect duration;
- local MAKE quote lifetime.

### Cross-process / cross-host

Use `observed_at` UTC for chronology.

Do not compare monotonic clocks across services/hosts.

### Source timestamp

Use `source_timestamp` only when the venue/API explicitly supplied it.

Null means unavailable.

## 10. Client-call latency versus network latency

OBSERVE's execution span is the BUILD-009/SIG-client call boundary.

It is valid for:

- end-to-end client-call latency;
- strategy-to-dispatch timing;
- ACK/fill/cancel lifecycle timing.

It is not a raw TCP/TLS measurement.

Do not label it:

- socket send -> first byte;
- TLS handshake latency;
- server processing time.

Cold-connection, TLS and keepalive venue characterization must be measured separately with
non-economic/read-only requests or other safe transport instrumentation.

Never create an economic order as a latency probe.

## 11. Quote mechanics

`QUOTE_PUBLISHED`, `QUOTE_WITHDRAWN` and `QUOTE_REPLENISHED` are derived from our MAKE
lifecycle.

They support analysis of:

- local quote lifetime;
- local cancel/requote frequency;
- time to first observed fill;
- partial-fill sequence;
- refill/replenishment after terminal quote state.

They do not reveal:

- SIG queue position;
- anonymous participant identity;
- exact public order-level cancellation cause.

Do not infer queue priority from aggregate depth.

## 12. Competition context

Expected normalized fields:

- tournament name/status;
- start/end time;
- initial balance;
- tournament `myBalance`;
- account balance;
- joined-at;
- leaderboard;
- participant rank / `myRank`;
- leaderboard total.

Raw tournament/account/leaderboard responses are retained in the same context record.

### Super Signal

The supplied official `api-1.json` marks the Super Signal analytics endpoint admin-only and
unavailable to participant API keys.

Expected context representation:

```text
classification = unavailable
reason = ADMIN_ONLY_PARTICIPANT_KEY_UNSUPPORTED
```

Do not replace this with an inferred signal based on holdings, leaderboard ranks or unrelated
trader data.

## 13. Cross-venue timing

OBSERVE's pure join aligns accepted SAME/COMPLEMENT mappings and measures:

```text
PM economic change observed
    -> next observed mapped SIG economic change
    -> elapsed wall-clock time
```

Treat this as descriptive response timing only.

Do not promote it to causal lead-lag without separate research controls for:

- sampling cadence;
- clock error;
- stale books;
- common information shocks;
- multiple venue updates inside the join window;
- mapping semantics.

The CAPTURE first-hours workflow remains the production stored-data analysis path.

## 14. Backpressure drill

Regression tests explicitly cover one blocked sink with a tiny bounded queue.

Operationally, if `observe.dropped > 0`:

1. keep Risk/execution running;
2. record the affected time window;
3. inspect queue high-water and sink failures;
4. verify CAPTURE writer/storage health;
5. reduce nonessential observation load or repair storage;
6. restart the observation-producing process only if operationally safe;
7. mark that interval as incomplete for forensic analysis.

Do not increase queue size blindly while storage is failing.

## 15. Context-source failure

If context sampling fails:

- execution continues;
- failure counter increments;
- `last_error` records the exception class;
- the next deadline may retry.

If a source is unsupported by the official participant contract, represent it as unavailable rather
than treating it as a runtime error.

## 16. Restart implications

A restart creates a new observation process/session identity.

This is correct. Do not splice process-local monotonic values across the restart.

After restart verify:

- new Parquet shards appear;
- old shards remain readable;
- process/session identity changed;
- context snapshots resume;
- OBSERVE drop/failure counters remain zero;
- BUILD-009 recovery resolves any pre-existing uncertain economic state before new LIVE exposure.

## 17. Benchmark

Run both:

```bash
python scripts/benchmark_observe001.py --iterations 100000
python scripts/benchmark_observe001_pipeline.py --iterations 10000
```

Record:

- exact commit SHA;
- Python version;
- host architecture;
- OFF p50/p95/p99;
- ON p50/p95/p99;
- incremental median;
- accepted/dropped/sink failures.

Acceptance requires very small synchronous enqueue overhead and zero drops/failures under this
synthetic no-op-sink benchmark.

The benchmark performs no network or file I/O and sends no SIG order.

## 18. Pre-launch checklist

- [ ] exact reviewed OBSERVE-001 SHA deployed;
- [ ] full CI green;
- [ ] `venue_observations` Parquet readable;
- [ ] `competition_context` Parquet readable;
- [ ] participant rank/leaderboard available or explicitly unavailable with reason;
- [ ] Super Signal explicitly unavailable for participant key;
- [ ] `observe.dropped = 0`;
- [ ] `observe.sink_failures = 0`;
- [ ] CAPTURE storage failures = 0;
- [ ] Realtime reconnect/gap test green;
- [ ] 429/503/transport tests green;
- [ ] cancellation test green;
- [ ] partial-fill/duplicate evidence tests green;
- [ ] cross-process monotonic guard test green;
- [ ] cross-venue descriptive timing test green;
- [ ] benchmark recorded for exact head;
- [ ] no real SIG order sent for instrumentation validation.

## 19. Emergency rule

If economic order state is ambiguous, OBSERVE does not resolve it by inference.

BUILD-009 remains authoritative:

- keep exposure reserved;
- stop adding exposure;
- reconcile authoritatively;
- only resume when the execution lifecycle is known.

**REAL SIG ORDERS SENT: NO**
