# MAKE-001 Operator Runbook

## Safety boundary

MAKE is opt-in and LIVE remains disabled by default.

Do not run the LIVE command unless real SIG order submission has been explicitly authorized for that session.

The safe development/validation order is:

1. configuration smoke;
2. SHADOW;
3. target-host benchmark;
4. production-like SHADOW soak;
5. explicit operator review;
6. only then, separately authorized LIVE.

## 1. No-network configuration smoke

From the repository root:

```bash
PREDICTIONS_CUP_MAKER_ENABLED=true \
python -m predictions_cup.maker.service --smoke-test
```

Expected:

- canonical mapping loads;
- maker components construct;
- mode prints SHADOW unless explicitly configured otherwise;
- no network calls;
- no SIG order writes.

## 2. SHADOW runtime

Required runtime environment:

```text
PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
PREDICTIONS_CUP_TRADING_ENABLED=false
PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=false

PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<read credential>
PREDICTIONS_CUP_TOURNAMENT_ID=<Cup tournament UUID>
PREDICTIONS_CUP_TOURNAMENT_SLUG=<Cup tournament slug>
```

No trade credential is needed or desired for SHADOW.

Start:

```bash
python -m predictions_cup.maker.service --runtime-env-only
```

The service should:

- reconcile account state;
- initialize SIG market state;
- seed canonical mapped Polymarket token books;
- connect SIG market Realtime;
- connect account Realtime;
- connect Polymarket market WebSocket;
- run one coalescing maker worker;
- never call the real SIG write client.

### SHADOW kill / shutdown

SIGINT or SIGTERM sets the service stop event. Before shutdown the runtime latches the maker kill switch and requests a global quote cancellation pass through the SHADOW adapter.

The process kill latch is one-way. It cannot be reset in the running maker.

## 3. LIVE prerequisites

LIVE has multiple independent gates. All must pass.

Required configuration includes:

```text
PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_EXECUTION_MODE=LIVE
PREDICTIONS_CUP_TRADING_ENABLED=true
PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=false

PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<read credential>
PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=<trade credential>
PREDICTIONS_CUP_TOURNAMENT_ID=<Cup tournament UUID>
PREDICTIONS_CUP_TOURNAMENT_SLUG=<Cup tournament slug>

PREDICTIONS_CUP_RISK_MAX_ORDER_SIZE=<explicit>
PREDICTIONS_CUP_RISK_MAX_GROSS_EXPOSURE=<explicit>
PREDICTIONS_CUP_RISK_MAX_PER_MARKET_EXPOSURE=<explicit>
PREDICTIONS_CUP_RISK_MAX_OPEN_ORDER_EXPOSURE=<explicit>
PREDICTIONS_CUP_RISK_MAX_CONCURRENT_OPEN_ORDERS=<explicit>
```

In addition, the operator must supply the explicit CLI gate:

```bash
python -m predictions_cup.maker.service --runtime-env-only --live
```

Without `--live`, LIVE construction fails.

Supplying `--live` while `PREDICTIONS_CUP_EXECUTION_MODE` is not LIVE also fails.

## 4. LIVE startup sequence

The intended sequence is:

1. construct validated settings;
2. reconcile authoritative account state;
3. initialize SIG state;
4. seed mapped Polymarket books;
5. open BUILD-009 execution journal;
6. issue BUILD-009 LIVE permit only if every interlock passes;
7. run BUILD-009 unresolved-operation recovery;
8. reconcile authoritative account again;
9. reconstruct MAKE quote state only where journal attribution proves the operation belongs to MAKE;
10. start market/account/feed loops;
11. permit new maker quote placement.

Any unresolved recovery state blocks LIVE resume.

## 5. Quote replacement

MAKE never immediately replaces an unresolved quote.

For a material quote change:

1. lifecycle emits CANCEL;
2. the old quote becomes CANCEL_PENDING;
3. cancellation is dispatched through BUILD-009;
4. if outcome is UNCERTAIN, the quote remains risk-bearing and replacement is blocked;
5. authoritative reconciliation resolves the old economic state;
6. only a later maker cycle may emit PLACE.

Do not bypass this manually.

## 6. Feed / trust failure behavior

Expected behavior:

- Polymarket disconnect -> PM feed trust false -> global reevaluation -> maker quotes cancel/suspend.
- SIG disconnect/reconnect -> relevant SIG state becomes untrusted/reconciles -> global reevaluation.
- account revision gap / fill ambiguity / reconnect -> account trust revoked -> maker cancels/suspends until authoritative REST recovery.
- stale PM FV -> cancel/suspend.
- stale SIG BBO -> cancel.
- stale account/inventory -> cancel.
- market settled/not open -> cancel.
- plugin exception/NaN/invalid probability -> suspend.
- critical state task failure -> process kill latch -> best-effort global maker cancel -> process failure.

## 7. Kill switch

The process-level `MakerKillSwitch` is one-way.

Activation prevents new maker orders and forces desired maker state to no quotes. The runtime then performs a global maker reevaluation so resting quotes are cancelled.

Reset requires process restart/reconstruction.

The startup configuration `PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=true` starts MAKE latched. This is the safest default, but it means no new quotes are emitted until the process is restarted with the startup kill gate intentionally disabled.

## 8. Benchmarking

Local/CI smoke:

```bash
python scripts/benchmark_make001.py \
  --iterations 20000 \
  --bursts 100 \
  --journal-iterations 200
```

Target-host evidence must use the exact committed SHA.

Record:

- commit SHA;
- instance type;
- region/AZ;
- architecture;
- Python version;
- CPU affinity;
- background processes/load;
- p50/p95/p99;
- throughput;
- full-universe burst latency;
- journal latency.

Do not edit source on EC2.

Do not create a real economic SIG order merely to benchmark latency.

## 9. Network latency

Safe measurements may include:

- public/read-only endpoint RTT;
- SIG read/cancel endpoints only where there is a valid non-economic target;
- connection establishment versus keepalive reuse;
- TLS/session reuse;
- governor wait contribution;
- jitter/tail latency.

Never create an economic order as a latency probe.

If no safe write-like endpoint exists, record that limitation rather than inventing a benchmark.

## 10. Before competition launch

Verify the exact candidate SHA:

```bash
ruff check .
mypy
pytest -q
python -m predictions_cup.maker.service --smoke-test
python scripts/benchmark_make001.py
```

Then verify SHADOW on the target host:

- PM feed connected;
- SIG market feed connected;
- account state trusted;
- no stale-source churn;
- full canonical maker universe available;
- quote materiality suppresses insignificant replace churn;
- kill switch/global cancellation works;
- restart with unresolved journal fixture/recovery path remains safe;
- no real orders sent.

## 11. Secrets

Read-only runtime processes should continue to use `runtime.env`.

The trade credential belongs only in the separately protected trade-secret path and must not be printed, committed, copied into logs or added to the read-only collector environment.

MAKE's SHADOW path does not need the trade credential.

## 12. Emergency rule

If state is ambiguous, do not infer that an order failed.

Keep economic exposure reserved, stop adding exposure, reconcile authoritatively, and only resume once the BUILD-009/MAKE lifecycle is sufficiently known.
