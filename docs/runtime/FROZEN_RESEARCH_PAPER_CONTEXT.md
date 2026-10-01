# Frozen research PAPER context — 005I / 005F

This extension to MODEL-RUNTIME-001 wires the highest-value frozen research signals into the existing SHADOW/LIVE-LEARN evidence path for launch-time measurement only.

It does **not** promote a strategy. It does not modify MAKE, sizing, RISK, BUILD-009 execution, credentials, or live activation.

## Providers

All providers are ModelCapability.CONTEXT_ONLY and ModelSpec.live_eligible=False.

| Provider | Launch status | Exact live parity |
|---|---|---|
| 005i_recent_5m_reversal_context | PAPER context | YES, after completed-minute warmup |
| 005i_price_discovery_context | PAPER NOT_READY | NO: raw quote-event count + prior LIQUIDITY_STRESS hierarchy check unavailable |
| 005i_liquidity_stress_context | PAPER NOT_READY | NO: PM top-5 depth unavailable on SHADOW BBO observer |
| 005i_withdrawal_replenishment_context | PAPER NOT_READY | NO: minute PM depth changes unavailable |
| 005i_depth_normalised_ofi_context | PAPER NOT_READY | PARTIAL inputs only; no exact output without depth/imbalance/OFI/raw quote count |
| 005f_renewal_state_context | PAPER context | YES when existing 005F exact state is ready |

These providers are automatically attached to the existing SHADOW bus whenever SHADOW itself is enabled. There is no LIVE model allowlist path for them.

## 005I recent five-minute context

The frozen research definition is reproduced only on completed Polymarket minute states:

    ret_1m = mid[t] - mid[t-1]
    ret_5m = mid[t] - mid[t-5]
    rv_5m  = sample std of the five ret_1m values ending at t
    relative_spread = (ask[t] - bid[t]) / max(mid[t], 0.001)

The state consumes the existing pre-coalescing Polymarket BBO observer. Missing minutes are forward-filled exactly as in the frozen minute panel when the prior BBO state is itself exact.

A minute is not accepted as parity-safe when its final observed BBO boundary is untrusted/ambiguous or is not from clob-market-ws-v1. Six consecutive exact completed minutes are required before ret_5m becomes ready.

The provider emits the observed ret_5m as a context score only. It does not translate the 67.26% HOLDOUT reversal result into a BUY/SELL direction.

## Why PRICE_DISCOVERY is NOT_READY

The frozen hierarchy is LIQUIDITY_STRESS, PRICE_DISCOVERY, POST_SHOCK, DIRECTIONAL_PRESSURE, REPLENISHMENT, STALE, ACTIVE, QUIET, OTHER.

PRICE_DISCOVERY is abs(ret_5m) >= 0.005 AND quote_events >= 19 after the prior LIQUIDITY_STRESS check.

The current SHADOW observer sees one grouped BBO boundary per token/message, not the raw research-row count used for quote_events. It also does not receive PM top-5 depth, so it cannot prove the preceding LIQUIDITY_STRESS condition is false. The provider therefore persists the exact available ret_5m component and returns NOT_READY; it never relabels a partial condition as PRICE_DISCOVERY.

## Why LIQUIDITY_STRESS / withdrawal / replenishment are NOT_READY

The frozen definitions depend on depth_total5:

    LIQUIDITY_STRESS:
    relative_spread >= 0.90 AND depth_total5 <= 605

    withdrawal_1m:
    max(-(depth_total5[t] - depth_total5[t-1]), 0)

    replenishment_1m:
    max(depth_total5[t] - depth_total5[t-1], 0)

    resilience:
    withdrawal_1m >= 200
    recovered iff next-minute replenishment_1m >= 200

The accepted SHADOW BBO observer does not carry the research-equivalent Polymarket top-five depth surface. These providers therefore fail closed with explicit depth-parity reasons.

## Frozen depth-normalised OFI

The exact frozen challenger remains identified by OFI_TRAIN_MODEL_FREEZE.json / blob c4c0370441a654e4670204c13157daf1f1ceff2b.

The live minute state can reproduce only mid, relative_spread, rv_5m, ret_1m and ret_5m.

It cannot exactly reproduce, from the current SHADOW BBO observer, depth_total5, raw quote_events, imbalance1 or ofi_depth_norm.

Therefore the frozen model output is deliberately **not computed**. The decision payload records the exact available subset, the missing inputs, and output_status=NOT_COMPUTED_WITH_PARTIAL_INPUTS.

## 005F renewal/freshness state

The 005F context provider reuses Live005FStateProvider directly. No parallel reconstruction is introduced. When ready it persists the exact frozen state features including genuine_age_s, genuine_15, genuine_60, abs_ret_15 and rv_60. If the existing provider is not ready, this provider is also NOT_READY.

## Measurement boundary

The purpose is to join these context decisions to future LIVE-LEARN outcomes: markouts, adverse selection, fill/economic evidence, and later replay diagnostics.

No context provider emits a direction, quote intent, fair value, order size or execution plan. Every provider has live_eligible=False.
