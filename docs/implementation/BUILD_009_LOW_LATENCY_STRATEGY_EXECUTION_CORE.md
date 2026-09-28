# BUILD-009 — Low-Latency Strategy & Execution Core

## Status

Branch: `build/009-low-latency-strategy-execution-core`

Draft PR: #50

Actual base SHA:

`bbd152eb13f827b9ede36e444ac597eb0274443d`

Authoritative SIG OpenAPI artifact:

`api-1.json`

SHA-256:

`8825112d9413f3b7361773800400704078968c412c47526b251e8b57e982448a`

BUILD-009 is branch-only until independently reviewed and merged. It does not make LIVE trading
canonical on `main`.

## Mission

BUILD-009 supplies the common production runtime through which later approved trading ideas pass.

The invariant is:

> Anything promoted later plugs in as a small pure strategy/kernel without changing execution
> infrastructure.

The common calculation path is:

`RuntimeSnapshot -> strategy/kernel -> Opportunity -> central Risk -> ExecutionPlan`

Only the final sink differs:

`ExecutionPlan -> SHADOW | LIVE`

## Frozen boundaries

BUILD-009 does not modify EXPERIMENT-005B, its preregistration, HOLDOUT, ordering falsification or
results. No 005A/005C/005D/005E/005F result is promoted into a production strategy here.

BUILD-004/005/006 contracts remain the accepted state/replay/governor foundations. BUILD-009 adds
execution boundaries around them rather than creating another runtime stack.

There is no Kafka, Redis, RPC, message broker, database service or microservice boundary. One Python
process remains the target deployment model.

## Strategy taxonomy

Exactly these families are supported:

- `FV-TAKE`
- `MAKE`
- `STRUCT`
- `PRED`
- `EVENT`
- `NO_TRADE`

`NO_TRADE` is a first-class result. No new family is introduced by BUILD-009.

## Hot-path contract

The synchronous calculation modules use compact immutable dataclasses and integer SIG price ticks.
The hot path performs no network call, filesystem access, database access, Parquet work, blocking
I/O or Pydantic model construction.

Strategies receive explicit snapshot/observation time and do not call wall clock time or randomness
implicitly. The runtime shell samples monotonic decision/submission/dispatch/ack timestamps for
latency attribution without introducing time dependence into strategy math.

The hot path terminates at a shared `ExecutionPlan`; durable journaling and network dispatch live in
the I/O shell.

### Numeric representation

SIG's execution tick is exactly `0.005`.

At the execution boundary:

- legal limit prices are represented as integer ticks `1..199`;
- canonical transport conversion uses `Decimal`;
- off-tick values fail visibly;
- float is permitted inside kernels where approximation is intentional and correctness is tested;
- float is not used as the authoritative execution-price identity.

This separates mathematical speed from exact order-wire semantics.

## Mathematical kernel registry

Kernel implementations are registered with:

- MATHS_LEDGER identity;
- implementation name/version;
- input/output contract;
- correctness tolerance;
- one explicit reference implementation.

Representative coverage includes:

- identity;
- M-038 log odds, with ratio and `log1p` formulations;
- M-041 exact binary-CARA reservation probability;
- M-042 first-order binary-CARA approximation.

M-042 remains a distinct approximation rather than being silently treated as mathematically
identical to M-041.

The benchmark harness compares equivalent implementations only after correctness checks. A faster
challenger is not accepted merely because it benchmarks well.

## Central Risk

Every executable proposal passes one central synchronous risk function.

LIVE fails closed when any required fact is absent or untrusted. Controls include:

- per-order quantity cap;
- gross portfolio exposure cap;
- per-market exposure cap;
- aggregate resting/uncertain-order exposure cap;
- concurrent open/uncertain-order count cap;
- accepted mapping/tradeability identity;
- explicit tournament identity;
- account-state trust;
- depth trust/freshness when the strategy requires depth;
- duplicate logical-intent detection;
- global kill switch.

Uncertain orders continue consuming risk capacity until authoritative reconciliation resolves them.

Tournament scope is first-class in the BUILD-009 runtime: `RuntimePosition`,
`RuntimeOrderState`, `RuntimeOrderIntent`, `ExecutionEnvelope` and
`ExecutionJournalEvent` all carry explicit `tournament_id`. A logical execution operation may
not mix tournaments. Journal migration backfills tournament identity only when it is
deterministically recoverable from the stored payload; an ambiguous legacy execution row remains
fail-closed rather than inheriting a default tournament.

Atomic structural bundles are risked as one `ATOMIC_MULTI_LEG` operation. Partial success is not
silently treated as acceptable for an atomic opportunity.

## SHADOW / LIVE gates

SHADOW is the default configuration.

LIVE requires all of the following:

- `PREDICTIONS_CUP_EXECUTION_MODE=LIVE`;
- `PREDICTIONS_CUP_TRADING_ENABLED=true`;
- an explicit SIG trade credential;
- explicit tournament ID and slug;
- all central risk caps;
- global kill switch disabled;
- trusted account state;
- explicit LIVE invocation through the interlock layer.

A trade credential by itself never enables network-capable trading.

## SIG write adapter

The write adapter is implemented directly from the hashed `api-1.json` artifact.

Covered participant routes:

- `POST /orders`;
- `POST /orders/batch`;
- `POST /orders/multi-leg`;
- `DELETE /orders/{id}`;
- `POST /orders/cancel-all`.

The adapter preserves the documented limits:

- single/batch/multi-leg client idempotency keys;
- batch up to 50;
- atomic multi-leg up to 10;
- optional `relationshipConstraint`;
- exact tournament scope on each order;
- persistent HTTP connection pooling;
- shared high-priority REST-governor acquisition.

### Retry and uncertainty semantics

Placement identity is fixed before dispatch. The journal stores the logical operation ID,
idempotency key, canonical payload JSON and payload SHA-256 before the first network write.

Retries reuse the identical resolved payload and the same idempotency key.

The adapter handles:

- `429 RATE_LIMITED`;
- `409 REQUEST_IN_FLIGHT`;
- `503 TX_CONFLICT`;
- `503 SERVICE_UNAVAILABLE`;
- `502 ORDER_STATUS_UNKNOWN`;
- transport failure after dispatch.

A placement whose economic outcome cannot be proven becomes `UNCERTAIN`; it is never assumed to
have failed.

For single-order cancellation, a documented `409` means the order is already closed because it was
filled or cancelled concurrently. BUILD-009 therefore treats that result as reconciliation-required
rather than labelling the cancel rejected.

For `cancel-all`, `207` and `422` remain structured outcomes with error members; callers must not
assume all target orders are gone unless authoritative open-order state proves it.

## Execution lifecycle and crash safety

Operation-level lifecycle states are explicit:

- `PENDING`;
- `ACKED`;
- `OPEN`;
- `PARTIALLY_FILLED`;
- `FILLED`;
- `CANCEL_PENDING`;
- `CANCELLED`;
- `UNCERTAIN`;
- `RECONCILING`;
- `RECONCILED`;
- `REJECTED`.

The SQLite/WAL execution journal is an I/O-shell component, not a hot-path component. It persists
logical identity before dispatch and rejects reuse of the same logical operation with a changed
payload.

Lifecycle transitions are validated rather than accepted arbitrarily.

Startup recovery:

1. obtains authoritative tournament account state;
2. loads unresolved journal entries;
3. replays unresolved idempotent placements with their original payload/key when needed;
4. reconciles single-cancel races through order + fill reads;
5. checks cancel-all scope through authoritative open-order reads;
6. reconciles account state again;
7. refuses LIVE resume while any operation remains unresolved.

## Account Realtime and authoritative recovery

The existing private Realtime subscriber can consume both `market_batch` and `account_batch`.

The account transport models cover:

- fills;
- order updates;
- settlements;
- refunds;
- collateral changes;
- topic-local delivery revisions.

Realtime remains best-effort. It accelerates state only.

Account trust is dropped on:

- revision gaps;
- malformed payloads;
- reconnect/token/socket recovery boundaries;
- an unknown resting order update.

Authoritative REST reconciliation restores trust using explicit tournament order/position reads.
No default-tournament portfolio endpoint is substituted for an explicitly selected non-default
tournament.

## Event-driven runtime orchestration

`EventDrivenCoordinator` is the I/O-shell trigger layer around the synchronous `DecisionRuntime`.
It evaluates only strategy bindings affected by an observable exchange/market state change or an
explicit scheduled strategy trigger. There is no arbitrary high-frequency strategy polling loop and
no task-per-market design.

The coordinator captures the current immutable `RuntimeSnapshot`, executes strategy and central
Risk synchronously, and only awaits at the final sink dispatch boundary. This preserves the
single-asyncio-loop / synchronous-calculation baseline.

## Structured execution audit

Every approved execution plan carries a non-wire `ExecutionAudit` containing:

- strategy family;
- strategy ID;
- scalar signal value (the opportunity gross edge at this boundary);
- fair value when defined;
- decision observation monotonic timestamp.

Before the first LIVE network write, the durable `SUBMISSION` journal event records that metadata
alongside logical intent identity and exchange identity. Its timestamp is sampled before the
durable WAL write. After that write completes, `NETWORK_DISPATCH` is sampled immediately before the
HTTP call; the event is persisted after the network outcome so benchmark/audit I/O does not sit
between the dispatch timestamp and the actual request. Subsequent ACK, fill, Realtime order/fill and
terminal reconciliation events share the same logical operation/order identity.

The audit therefore keeps distinct monotonic timestamps for source observation, approved
decision/plan creation, durable-submission start, logical network dispatch, and local
acknowledgement/fill observation. This is sufficient to reconstruct
signal -> decision -> durable submission -> network dispatch -> acknowledgement/fill timing without
putting analytics metadata into SIG order payloads.

The SQLite journal performs a forward-compatible column check on startup so branch-created
pre-audit journals acquire the new structured fields rather than silently losing attribution.

## Account Realtime controller

`AccountRealtimeController` binds the private `user:{profile_id}` / `account_batch` channel to
`AccountRealtimeStateEngine`.

Each initial subscription and every revision-gap, reconnect, token-refresh or socket-error recovery
boundary performs authoritative REST reconciliation before account state becomes trusted again.
The account socket is established first while state remains untrusted. REST reconciliation then runs
with the subscription active. If any account batch arrives while that snapshot is in flight, the
snapshot is treated as potentially raced, trust stays revoked, and reconciliation repeats until a
quiet subscribed snapshot completes. This removes the blind REST-to-subscribe window.

A revision gap or malformed/unknown-order update immediately exits the current subscription path and
forces resynchronization; new LIVE exposure therefore cannot continue on a broken account stream.

## SHADOW semantics

SHADOW receives exactly the same post-risk intents and execution envelope shape as LIVE.

The conservative simulator:

- marks immediately executable trusted-depth crosses as filled;
- does not invent passive queue position;
- leaves non-crossing passive orders open;
- remains deterministic for the same state and intent.

A null sink exists for internal latency measurement without simulated fill work.

## Telemetry

Hot-path telemetry is bounded in memory and performs no I/O. It records counters and nanosecond
latency observations for calculation stages.

The benchmark report separately records:

- internal strategy calculation;
- risk;
- execution-plan construction;
- end-to-end internal decision to null sink;
- exact Decimal/tick conversion;
- representative kernel alternatives;
- optional durable journal pre-dispatch cost.

SIG documents market/account Realtime coalescing in approximately 250 ms windows. That upstream
batching is not attributed to the Python strategy engine and is reported separately from internal
processing latency.

## Benchmark methodology

The first-class harness lives at:

`python -m predictions_cup.benchmarks`

It records:

- git SHA;
- Python version;
- OS/architecture/CPU;
- process affinity where available;
- call and warm-up counts;
- timer-harness overhead;
- mean, median, p95 and p99 per-call timing;
- throughput;
- optional tracemalloc peak;
- correctness/tolerance outcomes.

Required BUILD-009 acceptance runs are:

1. correctness + approximately 3,000-iteration representative timings;
2. warm-up followed by at least 1,000,000 iterations for hot-path kernels/decision loop;
3. a repeated million-iteration run to check stability.

The EC2 host is used only for these speed measurements. Repository edits and correctness CI are
performed directly on the GitHub branch.

### Final acceptance evidence

Benchmarked code SHA:

`8a56dec7ed2c0992ed9c86a911edf3a82786cc74`

That exact SHA passed GitHub CI run #2380:

- Ruff: pass;
- shell validation: pass;
- strict mypy: pass;
- pytest: **533 passed, 2 skipped**;
- application smoke: pass;
- BUILD-009 benchmark smoke: pass.

Target host:

- AWS EC2 `t4g.small`;
- availability zone `us-east-1f`;
- ARM64 / `aarch64`;
- Linux `6.18.48-109.150.amzn2023.aarch64`;
- Python 3.12.14;
- process affinity CPUs 0 and 1.

Commands used:

```bash
python -m predictions_cup.benchmarks \
  --calls 3000 --warmup 3000 --repeats 3 \
  --include-journal --journal-calls 1000

python -m predictions_cup.benchmarks \
  --calls 1000000 --warmup 3000 --repeats 3
```

All figures below are the **median across the three independent repeats**. Percentiles are the
median of the per-repeat percentile estimates.

#### Correctness

| Check | Maximum absolute error | Acceptance tolerance | Result |
|---|---:|---:|---|
| SIG tick round-trip | 0 | exact | PASS |
| M-038 ratio vs `log1p` logit | 8.88e-16 | 1e-12 | PASS |
| M-041 stable vs naive exact CARA | 1.73e-18 | 1e-12 | PASS |

#### Representative 3k + durable-journal battery

| Component | Mean | p50 | p95 | p99 | Throughput |
|---|---:|---:|---:|---:|---:|
| Decimal/tick round-trip | 0.862 us | 0.821 us | 0.846 us | 0.862 us | ~1.16m/s |
| M-038 reference logit | 0.215 us | 0.178 us | 0.181 us | 0.181 us | ~4.64m/s |
| M-041 exact CARA | 0.433 us | 0.395 us | 0.397 us | 0.397 us | ~2.31m/s |
| Strategy evaluation | 4.570 us | 4.518 us | 4.671 us | 4.754 us | ~219k/s |
| Central Risk | 7.716 us | 7.536 us | 7.933 us | 8.079 us | ~130k/s |
| Execution-plan construction | 19.099 us | 19.221 us | 20.121 us | 20.352 us | ~52.4k/s |
| Decision -> null sink | **37.570 us** | **37.306 us** | **38.265 us** | **38.604 us** | **~26.6k/s** |
| SQLite/WAL pre-dispatch durability | **1.545 ms** | **1.226 ms** | **2.377 ms** | **2.695 ms** | **~647/s** |

The WAL cost is intentionally outside the calculation hot path. It is the measured price of
persisting execution identity with `synchronous=FULL` before a LIVE network write.

#### Clean million-call stability battery

| Component | Mean | p50 | p95 | p99 | Throughput |
|---|---:|---:|---:|---:|---:|
| Decimal/tick round-trip | 0.834 us | 0.818 us | 0.889 us | 0.950 us | ~1.20m/s |
| M-038 reference logit | 0.202 us | 0.178 us | 0.179 us | 0.189 us | ~4.95m/s |
| M-041 exact CARA | 0.429 us | 0.407 us | 0.411 us | 0.459 us | ~2.33m/s |
| Strategy evaluation | 4.573 us | 4.459 us | 4.768 us | 5.167 us | ~219k/s |
| Central Risk | 7.800 us | 7.685 us | 8.149 us | 9.025 us | ~128k/s |
| Execution-plan construction | 19.692 us | 19.365 us | 23.476 us | 38.103 us | ~50.8k/s |
| Decision -> null sink | **38.466 us** | **37.584 us** | **39.521 us** | **43.014 us** | **~26.0k/s** |

The three million-call end-to-end means were **38.538 us, 38.270 us and 38.466 us**. The
peak-to-peak spread was ~0.268 us, about **0.7%** of the median run, so the internal path was stable
on the target host.

SIG's documented ~250 ms Realtime batching remains an upstream feed property and is not included in
these internal processing figures. These benchmarks likewise do not claim Internet/SIG HTTP
round-trip latency.

The million-call run is deliberately not a generic GitHub-hosted hard latency gate.

## Remaining acceptance work

Technical BUILD-009 acceptance is complete on the benchmarked code SHA above:

- canonical decision/execution path reconciled;
- lint/type/test/application/benchmark smoke green;
- startup and account-stream recovery regressions green;
- 3k representative/journal and repeated 1m target-host benchmarks complete;
- real SIG orders sent: **NO**.

The PR remains **draft** until independent review. A bounded real placement/cancel smoke is a
separate MASTER-authorized gate and was not performed as part of BUILD-009 implementation.

## Scope exclusions

BUILD-009 does not:

- invent alpha;
- register unapproved 005 findings as production strategies;
- modify the frozen 005B lane;
- introduce maker queue claims unsupported by observable data;
- place real orders during tests or benchmarks;
- enable LIVE by default.


## Live-order statement

**REAL SIG ORDERS SENT: NO**

BUILD-009 implementation, CI and benchmark work use mocked writes, SHADOW/null sinks and read-only
state where applicable. A bounded real placement/cancel smoke remains a separate MASTER-authorized
gate and has not been executed.
