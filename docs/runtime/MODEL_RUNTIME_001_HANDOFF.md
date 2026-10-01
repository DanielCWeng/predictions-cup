# MODEL-RUNTIME-001 handoff

```text
STARTING_MAIN_SHA=662969df4e481f1cbd9417c923ae35d3a0aa9f9f
FINAL_BRANCH_SHA=SEE_PR_HEAD
```

A commit cannot contain its own final SHA without changing that SHA. The immutable review head is therefore recorded by the PR itself; the implementation SHA and final PR head are also returned in the lane completion report.

## MODEL_INTERFACE

`src/predictions_cup/models/contracts.py`

Tiny typed `ModelProvider`, `ModelSpec`, capability, decision and model-local risk envelope. Input is the accepted `CanonicalShadowSnapshot`.

## MODEL_REGISTRY

`src/predictions_cup/models/registry.py`

Explicit startup registry. No filesystem discovery or dynamic hot-path imports.

## MODEL_FILES

Architecture fixtures only:

```text
model_no_trade.py
model_fixture_direction.py
model_fixture_context.py
```

All have `live_eligible=False`.

## PAPER_ACTIVATION_PATH

`PREDICTIONS_CUP_PAPER_MODEL_IDS` → explicit registry → `PaperModelCandidate` → existing SHADOW-002 event store → CAPTURE/LIVE-LEARN mirrors when configured.

## CODE_LIVE_GATE_LOCATION

Each model file's explicit immutable `ModelSpec.live_eligible`.

## ENV_LIVE_GATE_LOCATION

Host `~/.config/predictions-cup/runtime.env`:

```text
PREDICTIONS_CUP_LIVE_MODEL_IDS=...
```

No production value is committed.

## TRADE_CREDENTIAL_LOCATION

Host `~/.config/predictions-cup/trade.env`:

```text
PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=...
```

Model code never receives it.

## POSITION_SIZING_PATH

`src/predictions_cup/models/sizing.py`

Small bounded sizing; confidence is not directly converted into a large position.

## RISK_PATH

`ModelRuntime.evaluate()` → accepted `predictions_cup.risk.core.evaluate_risk()`.

## EXECUTION_PATH

Only an approved RISK-002 result becomes an accepted BUILD-009 `ExecutionPlan`. `LiveModelCoordinator` reserves through the existing `ExecutionReservationBook` before the service-owned `SigLiveSink` dispatch.

## OBSERVE_PATH

LIVE execution remains inside BUILD-009/service observation and execution journalling. Model decisions use SHADOW's durable `CandidateDecision` event surface.

## LIVE_LEARN_PATH

PAPER models are SHADOW candidates. LIVE model decisions are persisted directly through the same accepted SHADOW event store, whose existing composite mirror feeds LIVE-LEARN when enabled.

## BENCHMARK_RESULTS

Validated on frozen-research extension head:

```text
9a34131fc01d58f9a8e1e7124564d4b80ce9d292
```

Benchmark surface: `benchmarks/model_runtime.py --iterations 20000`.

```text
registry/router                 median 0.100 us   p99   0.223 us
single model evaluation         median 2.183 us   p99   2.859 us
model -> candidate contract     median 25.088 us  p99  38.294 us
1 PAPER model                   median 9.924 us   p99  16.765 us
5 PAPER models                  median 47.588 us  p99  58.036 us
10 PAPER models                 median 94.548 us  p99 150.436 us
```

Engineering targets:

```text
simple scalar model median <= 25 us
simple scalar model p99    <= 50 us
>= 1 ms p99 is a CI regression guard
```

The lightweight model cleared the latency target comfortably. The benchmark explicitly recorded `real_sig_orders_sent=false`.

## TEST_RESULTS

Exact implementation-head CI was green:

```text
ruff:       PASS
shell:      PASS
mypy:       PASS — 300 source files
pytest:     PASS — 883 passed, 3 skipped
app smoke:  PASS
BUILD-009:  PASS
RISK-002:   PASS
MAKE-001:   PASS
CANDIDATE:  PASS
MODEL:      PASS
OBSERVE:    PASS
SHADOW:     PASS
LIVE-LEARN: PASS
FULLSTACK:  PASS
```

Deterministic MODEL-RUNTIME tests cover registry/config failure, PAPER isolation, the two-key truth table, central Risk denials, sizing, stale/missing state, model exception quarantine/recovery, provenance and reservation-before-dispatch.

## FROZEN RESEARCH PAPER EXTENSION

Validated code head:

```text
9a34131fc01d58f9a8e1e7124564d4b80ce9d292
```

The launch-time measurement extension adds six automatic SHADOW/LIVE-LEARN
context providers. Every provider is `CONTEXT_ONLY` and
`live_eligible=False`; none emits direction, quote intent, size, fair value or
an execution plan.

```text
005i_recent_5m_reversal_context       OK after exact completed-minute warmup
005i_price_discovery_context          NOT_READY: raw quote-event count + prior depth hierarchy
005i_liquidity_stress_context         NOT_READY: PM top-5 depth unavailable
005i_withdrawal_replenishment_context NOT_READY: PM minute depth changes unavailable
005i_depth_normalised_ofi_context     NOT_READY: exact depth/imbalance/OFI/count inputs unavailable
005f_renewal_state_context            OK when existing exact 005F state is ready
```

The 005I recent-five-minute provider reproduces only the parity-safe frozen
minute definitions: midpoint, spread, relative spread, one-minute return,
five-minute return and sample-standard-deviation `rv_5m`. It uses the existing
pre-coalescing PM BBO observer and fails closed on ambiguous/untrusted/non-WS
minute endings or incomplete warmup.

PRICE_DISCOVERY is deliberately not approximated: the frozen hierarchy checks
LIQUIDITY_STRESS first, and the live SHADOW BBO observer does not expose the
research-equivalent PM top-five depth or raw-row `quote_events` count.
LIQUIDITY_STRESS, withdrawal/replenishment and depth-normalised OFI retain the
same fail-closed boundary. The OFI provider records the exact available input
subset but leaves the frozen output uncomputed.

005F renewal context directly reuses `Live005FStateProvider`; no second
reconstruction was introduced. Full parity rationale is documented in
`docs/runtime/FROZEN_RESEARCH_PAPER_CONTEXT.md`.

No MAKE, sizing, RISK, execution, credential or LIVE-activation path was changed
by this extension.

## KNOWN_LIMITATIONS

- No frozen research context in this extension is promoted to a trading strategy.
- The offline `cupctl models status` command cannot prove dynamic account/mark/reconciliation readiness and therefore refuses to display `PLATFORM_LIVE_READY=true` from env files alone.
- Model-local sizing uses accepted runtime exposure/account state; it does not invent a separate free-capital ledger.
- A strategy-specific position attribution is only as rich as accepted account/execution attribution. Central RISK-002 remains authoritative when attribution-dependent caps are configured.

## OPEN_DEPENDENCIES

A future accepted research model needs one model file, one registry entry and independent scientific/live approval. No runtime redesign is required.

```text
REAL_SIG_ORDERS_SENT=NO
EC2_DEPLOYED=NO
```
