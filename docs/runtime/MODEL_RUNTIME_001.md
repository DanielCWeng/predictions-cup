# MODEL-RUNTIME-001 — Hot-swappable model runtime

## Purpose

MODEL-RUNTIME-001 is the production bridge from an accepted research model to PAPER observation and, after independent approval, the existing LIVE execution stack.

The invariant is deliberately narrow:

```text
CanonicalShadowSnapshot
        |
      MODEL
        |
  ModelDecision
        |
 central sizing
        |
     RISK-002
        |
 BUILD-009 ExecutionPlan
        |
 accepted service dispatch
```

A model never owns a venue client, credential, journal, position ledger, account reconciler, order retry loop, or risk bypass.

## Add/remove contract

A model implementation lives in one explicit `src/predictions_cup/models/model_<name>.py` file and exposes a `ModelSpec` plus `evaluate(CanonicalShadowSnapshot) -> ModelDecision`.

Registration is one explicit entry in `default_model_registry()` in `models/registry.py`. There is no filesystem discovery, dynamic import per tick, plugin scan, or runtime code injection.

Removing a model is the inverse: delete the model file and remove its one registry entry. Historical candidate IDs/version/hash values remain durable in SHADOW/LIVE-LEARN evidence.

## Model capabilities

`ModelCapability` is explicit: `CONTEXT_ONLY`, `DIRECTIONAL`, `FAIR_VALUE`, `EXECUTION`, or `QUOTING`.

A `CONTEXT_ONLY` provider can produce context/urgency/score but central sizing returns zero economic quantity. Direction on a non-directional decision is rejected by the typed contract.

The reference files are architecture fixtures only. Every reference `ModelSpec.live_eligible` is `False`; this lane introduces no production alpha and authorizes no reference strategy for LIVE.

## PAPER path

Set the startup allowlist:

```text
PREDICTIONS_CUP_PAPER_MODEL_IDS=model_a,model_b
```

PAPER providers are adapted directly onto SHADOW-002. They receive the same immutable `CanonicalShadowSnapshot` generated from MAKE's live in-memory market state. Their `CandidateDecision` records therefore flow through the existing SHADOW JSONL/capture mirror and LIVE-LEARN mirror when enabled.

The PAPER adapter applies the same central model sizing routine and records the resulting hypothetical RISK-002 disposition when a risk context is available. It never builds or dispatches an economic execution plan.

## LIVE path

There are exactly two model-level authorization gates:

1. `ModelSpec.live_eligible=True` in the model file.
2. the model ID appears in `PREDICTIONS_CUP_LIVE_MODEL_IDS` on the host.

An ENV request for a model whose code gate is false is a startup configuration error. Unknown model IDs and malformed/duplicate allowlists also fail closed.

Those two gates only make the model eligible to enter the central LIVE pipeline. They do not override platform safety. The service constructs an effective LIVE model runtime only after the existing BUILD-009/RISK-002 live composition has established its normal execution authority. Every economic decision then passes through central `evaluate_risk()`, `build_execution_plan()`, the shared execution reservation book and `SigLiveSink`.

A model never sees the SIG trade credential.

## Platform safety is not a third model switch

Existing safeguards remain authoritative: account/mark trust, reconciliation, global and strategy halts, session loss, drawdown, gross/market/open-order/tournament/event-group exposure, execution recovery, credentials and other BUILD-009 interlocks.

If any of those reject a proposal, the model receives no execution plan even when both model gates are on.

## Central sizing

`models/sizing.py` owns bounded model sizing. It considers the model's explicit risk envelope, current signed inventory, market exposure, open/UNCERTAIN orders and optional available budget. Any UNCERTAIN exposure in the target market blocks new model exposure.

Confidence is never multiplied directly into position size. The default policy is a small explicit `base_order_size` clamped by `max_order_size`, `max_model_position`, `max_market_exposure` and optional model strategy budget. RISK-002 independently applies the tighter global limits afterward.

## Hot path

Startup performs all registry/import/allowlist validation and freezes the dispatch maps. Evaluation contains no network I/O, disk I/O, environment reads, JSON parsing, pandas/DataFrame work, dynamic imports, logging formatting or database calls.

The accepted immutable snapshot and integer-tick book representation are reused. Only scalar/dataclass work occurs before central Risk.

`benchmarks/model_runtime.py` separately measures registry/router overhead, one model, model-to-candidate normalization and 1/5/10 PAPER providers. It reports median/p95/p99/max and evaluations/second. The target for a lightweight scalar model is <=25 us median and <=50 us p99; CI treats a >=1 ms p99 as a regression guard rather than falsifying model science to meet a machine-specific target.

## Failure isolation

Provider exceptions produce an `EXCEPTION` decision and no exposure. Repeated failures quarantine only that model for the process/session. Quarantine recovery is explicit through `ModelRuntime.recover(model_id)`.

Stale input, missing market/book, invalid model output, sizing failure, absent Risk, or Risk denial all produce no new economic exposure. SHADOW/capture can continue.

Infrastructure failure on the economic service bridge is different: it propagates so the service fails closed rather than converting an execution-path failure into a zero signal.

## Provenance

Every durable candidate decision carries:

- model ID;
- version;
- source/config hash;
- PAPER/LIVE mode;
- decision kind/reason;
- hypothetical/effective size;
- RISK disposition;
- BUILD-009 logical operation ID when an economic plan is created.

LIVE strategy IDs also embed model ID, version and source-hash prefix for execution-journal attribution.

## Operator surface

`scripts/cupctl models status` inspects the explicit registry and host allowlists without displaying secrets. It reports PAPER, code gate, env gate, platform configuration state and configuration errors.

The offline command deliberately does **not** claim dynamic `PLATFORM_LIVE_READY`: account trust, marks and reconciliation cannot be proven from env files. The live service is the authority for those runtime safety conditions.

## Scope

No new research was run. No model was tuned. No Kaggle or EC2 operation was performed. No SIG order was sent. This lane extends the accepted runtime rather than replacing SHADOW, LIVE-LEARN, OBSERVE, RISK-002 or BUILD-009.
