# MODEL-RUNTIME-001 — LIVE gate contract

## The two model-level gates

A model may enter the LIVE pipeline only when both statements are true:

```text
CODE_GATE = ModelSpec.live_eligible is True
ENV_GATE  = model_id is in PREDICTIONS_CUP_LIVE_MODEL_IDS

MODEL_LIVE_AUTHORIZED = CODE_GATE AND ENV_GATE
```

There is no wildcard and no registry default that turns models live.

Truth table:

| Code gate | Host env gate | Model result |
|---|---|---|
| OFF | OFF | NOT_LIVE |
| ON | OFF | NOT_LIVE |
| OFF | ON | BLOCKED_CONFIGURATION; startup fails closed |
| ON | ON | eligible for the central LIVE pipeline |

The code gate lives in the model's own `ModelSpec` and defaults to `False`. Changing it is a reviewed Git change.

The env gate is one host allowlist: `PREDICTIONS_CUP_LIVE_MODEL_IDS`. Production values belong in the accepted host runtime configuration and are not committed.

## Platform safety remains mandatory

Eligibility is not permission to bypass central safety. RISK-002 and BUILD-009 still decide whether an economic order can exist.

Account/mark trust, capital state, global/strategy halt, session loss, drawdown, open/UNCERTAIN orders, market/gross/event/tournament exposure, execution recovery, credentials and accepted live interlocks are platform requirements. They are **not additional per-model activation knobs**.

If central Risk is missing or denies, the model creates no `ExecutionPlan`.

## Credential boundary

The SIG trade credential remains outside Git in:

```text
~/.config/predictions-cup/trade.env
PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=...
```

It is not stored in a model file or registry and is never used as a model-selection mechanism. Model code never receives the credential.

## PAPER

PAPER uses the independent allowlist:

```text
PREDICTIONS_CUP_PAPER_MODEL_IDS=model_a,model_b
```

PAPER can run multiple providers and is permanently routed through SHADOW/LIVE-LEARN evidence. It cannot obtain LIVE execution authority.
