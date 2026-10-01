"""Frozen launch-time research context providers.

These providers are PAPER/context only. They reproduce only definitions whose live
input surface has exact parity with the frozen research construction. Everything
else persists NOT_READY with the missing parity named explicitly.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from predictions_cup.mapping.models import MappingDocument
from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelProvider,
    ModelSpec,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.shadow.frozen_runtime import HAZARD005F_FROZEN_SPEC_VERSION
from predictions_cup.shadow.live_005f import (
    Accepted005FScopeResolver,
    Live005FStateProvider,
)
from predictions_cup.strategy.core import StrategyFamily

# Canonical pre-HOLDOUT frozen blob identities from EXPERIMENT-005I.
REVERSAL_FREEZE_BLOB = "3ec397904489aa12a983622192702a4618d4b76a"
REGIME_FREEZE_BLOB = "4b6d7e3aa2a1f686dc49af076d14012badd39e7d"
OFI_FREEZE_BLOB = "c4c0370441a654e4670204c13157daf1f1ceff2b"

DISCOVERY_ABS_RET_5M = 0.005
ACTIVE_QUOTE_EVENTS = 19
LIQUIDITY_STRESS_RELATIVE_SPREAD = 0.9
LIQUIDITY_STRESS_DEPTH_TOTAL5_MAX = 605.0
WITHDRAWAL_1M = 200.0
REPLENISHMENT_1M = 200.0

FROZEN_RESEARCH_MODEL_IDS = (
    "005i_recent_5m_reversal_context",
    "005i_price_discovery_context",
    "005i_liquidity_stress_context",
    "005i_withdrawal_replenishment_context",
    "005i_depth_normalised_ofi_context",
    "005f_renewal_state_context",
)


@dataclass(frozen=True, slots=True)
class Frozen005IMinuteFeatures:
    scope_id: str
    minute_epoch: int
    mid: float
    spread: float
    relative_spread: float
    ret_1m: float
    ret_5m: float
    rv_5m: float
    observed_quote_groups: int
    source_version: str

    def payload(self) -> dict[str, object]:
        return {
            "scope_id": self.scope_id,
            "minute_epoch": self.minute_epoch,
            "mid": self.mid,
            "spread": self.spread,
            "relative_spread": self.relative_spread,
            "ret_1m": self.ret_1m,
            "ret_5m": self.ret_5m,
            "abs_ret_5m": abs(self.ret_5m),
            "rv_5m": self.rv_5m,
            "observed_quote_groups": self.observed_quote_groups,
            "quote_events_exact_parity": False,
            "source_version": self.source_version,
        }


@dataclass(frozen=True, slots=True)
class Frozen005IContextResult:
    features: Frozen005IMinuteFeatures | None
    reason: str | None
    scope_id: str | None

    @property
    def ready(self) -> bool:
        return self.features is not None and self.reason is None


@dataclass(frozen=True, slots=True)
class _CompletedMinute:
    minute_epoch: int
    mid: float | None
    spread: float | None
    exact: bool
    quote_groups: int
    source_version: str


@dataclass(slots=True)
class _OpenMinute:
    minute_epoch: int
    mid: float | None = None
    spread: float | None = None
    last_state_exact: bool = False
    quote_groups: int = 0
    source_version: str = "unavailable"


@dataclass(slots=True)
class _ScopeState:
    current: _OpenMinute | None = None
    completed: deque[_CompletedMinute] = field(
        default_factory=lambda: deque(maxlen=16)
    )
    last_timestamp_ns: int | None = None


class Live005IMinuteState:
    """Exact completed-minute PM BBO state for the parity-safe subset of 005I.

    The existing SHADOW observer supplies grouped PM BBO boundaries. That is
    sufficient for completed-minute midpoint/spread returns, but it is *not*
    equivalent to the raw-row quote-event count used by 005I. Depth is also not
    present on this observer. Those boundaries are deliberately exposed to the
    providers rather than approximated.
    """

    provider_id = "005i-live-completed-minute-bbo"
    version = "005i-paper-context-v1"
    exact_ws_source_version = "clob-market-ws-v1"

    def __init__(self, mapping: MappingDocument) -> None:
        self._resolver = Accepted005FScopeResolver(mapping)
        self._scopes: dict[str, _ScopeState] = {}

    def scope_for_exchange(self, exchange_id: str) -> str | None:
        return self._resolver.resolve_exchange(exchange_id)

    def observe_bbo(
        self,
        *,
        scope_id: str,
        observed_at: datetime,
        best_bid: float | None,
        best_ask: float | None,
        source_version: str,
        trusted: bool,
    ) -> bool:
        if not self._resolver.accepts_scope(scope_id):
            return False
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("005I PM observed_at must be timezone-aware")
        if not source_version.strip():
            raise ValueError("005I source_version must not be blank")

        timestamp_ns = int(observed_at.timestamp() * 1_000_000_000)
        minute_epoch = int(observed_at.timestamp() // 60)
        state = self._scopes.setdefault(scope_id, _ScopeState())
        if state.last_timestamp_ns is not None and timestamp_ns < state.last_timestamp_ns:
            raise ValueError("005I BBO observations must be time ordered per scope")
        state.last_timestamp_ns = timestamp_ns

        if state.current is None:
            state.current = _OpenMinute(minute_epoch=minute_epoch)
        elif minute_epoch < state.current.minute_epoch:
            raise ValueError("005I minute chronology must be monotonic")
        elif minute_epoch > state.current.minute_epoch:
            self._advance_to(state, minute_epoch - 1)

        current = state.current
        assert current is not None
        valid = (
            trusted
            and source_version == self.exact_ws_source_version
            and best_bid is not None
            and best_ask is not None
            and math.isfinite(best_bid)
            and math.isfinite(best_ask)
            and 0.0 <= best_bid <= best_ask <= 1.0
        )
        current.quote_groups += 1
        current.source_version = source_version
        current.last_state_exact = valid
        if valid:
            assert best_bid is not None and best_ask is not None
            current.mid = (best_bid + best_ask) * 0.5
            current.spread = best_ask - best_bid
        return True

    def context(self, snapshot: CanonicalShadowSnapshot) -> Frozen005IContextResult:
        scope_id = self._resolver.resolve_snapshot(snapshot)
        if scope_id is None:
            return Frozen005IContextResult(None, "005i_scope_unmapped", None)
        quote = snapshot.maker.external_quotes.get(scope_id)
        if quote is None or not quote.trusted:
            return Frozen005IContextResult(
                None,
                "005i_pm_source_untrusted_or_missing",
                scope_id,
            )

        state = self._scopes.get(scope_id)
        if state is None or state.current is None:
            return Frozen005IContextResult(
                None,
                "005i_completed_minute_state_unavailable",
                scope_id,
            )
        target = int(snapshot.observed_at.timestamp() // 60) - 1
        if target >= state.current.minute_epoch:
            self._advance_to(state, target)

        rows = tuple(state.completed)
        if len(rows) < 6:
            return Frozen005IContextResult(
                None,
                "005i_five_minute_warmup_incomplete",
                scope_id,
            )
        window = rows[-6:]
        expected_start = window[-1].minute_epoch - 5
        if tuple(row.minute_epoch for row in window) != tuple(
            range(expected_start, expected_start + 6)
        ):
            return Frozen005IContextResult(
                None,
                "005i_minute_grid_discontinuous",
                scope_id,
            )
        if any(not row.exact or row.mid is None or row.spread is None for row in window):
            return Frozen005IContextResult(
                None,
                "005i_completed_minute_bbo_parity_unavailable",
                scope_id,
            )

        mids = [float(row.mid) for row in window if row.mid is not None]
        latest = window[-1]
        ret_1m_values = [mids[index] - mids[index - 1] for index in range(1, 6)]
        rv_5m = statistics.stdev(ret_1m_values)
        mid = mids[-1]
        assert latest.spread is not None
        spread = float(latest.spread)
        features = Frozen005IMinuteFeatures(
            scope_id=scope_id,
            minute_epoch=latest.minute_epoch,
            mid=mid,
            spread=spread,
            relative_spread=spread / max(mid, 0.001),
            ret_1m=ret_1m_values[-1],
            ret_5m=mids[-1] - mids[0],
            rv_5m=rv_5m,
            observed_quote_groups=latest.quote_groups,
            source_version=latest.source_version,
        )
        return Frozen005IContextResult(features, None, scope_id)

    def _advance_to(self, state: _ScopeState, target_minute: int) -> None:
        current = state.current
        if current is None:
            return
        while current.minute_epoch <= target_minute:
            state.completed.append(
                _CompletedMinute(
                    minute_epoch=current.minute_epoch,
                    mid=current.mid,
                    spread=current.spread,
                    exact=current.last_state_exact,
                    quote_groups=current.quote_groups,
                    source_version=current.source_version,
                )
            )
            current = _OpenMinute(
                minute_epoch=current.minute_epoch + 1,
                mid=current.mid,
                spread=current.spread,
                last_state_exact=current.last_state_exact,
                quote_groups=0,
                source_version=current.source_version,
            )
        state.current = current


def _spec(model_id: str, source_hash: str) -> ModelSpec:
    return ModelSpec(
        model_id=model_id,
        model_version="frozen-paper-v1",
        capability=ModelCapability.CONTEXT_ONLY,
        source_hash=source_hash,
        strategy_family=StrategyFamily.PRED,
        live_eligible=False,
        max_input_age_ns=60_000_000_000,
    )


def _not_ready(reason: str, context: dict[str, object]) -> ModelDecision:
    return ModelDecision(
        kind=ModelDecisionKind.NOT_READY,
        reason=reason,
        context=context,
    )


def _base_005i_payload(
    result: Frozen005IContextResult,
    *,
    frozen_object: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "research_id": "EXPERIMENT-005I",
        "frozen_object": frozen_object,
        "paper_only": True,
        "live_eligible": False,
        "scope_id": result.scope_id,
    }
    if result.features is not None:
        payload.update(result.features.payload())
    if result.reason is not None:
        payload["minute_state_readiness_reason"] = result.reason
    return payload


class Recent5mReversalContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[0], REVERSAL_FREEZE_BLOB)

    def __init__(self, state: Live005IMinuteState) -> None:
        self._state = state

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        result = self._state.context(snapshot)
        payload = _base_005i_payload(result, frozen_object="FIVE_MINUTE_REVERSAL")
        payload.update(
            {
                "frozen_definition": "ret_5m = minute_mid[t] - minute_mid[t-5]",
                "holdout_reversal_rate": 0.6726,
                "measurement_intent": "future_markout_and_economics_only",
            }
        )
        if not result.ready:
            return _not_ready(result.reason or "005i_recent_5m_not_ready", payload)
        assert result.features is not None
        return ModelDecision(
            kind=ModelDecisionKind.CONTEXT,
            signal_value=result.features.ret_5m,
            reason="005i_recent_5m_reversal_context",
            context=payload,
        )


class PriceDiscoveryContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[1], REGIME_FREEZE_BLOB)

    def __init__(self, state: Live005IMinuteState) -> None:
        self._state = state

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        result = self._state.context(snapshot)
        payload = _base_005i_payload(result, frozen_object="PRICE_DISCOVERY")
        payload.update(
            {
                "frozen_threshold_abs_ret_5m": DISCOVERY_ABS_RET_5M,
                "frozen_threshold_quote_events": ACTIVE_QUOTE_EVENTS,
                "required_hierarchy_predecessor": "LIQUIDITY_STRESS",
                "missing_exact_parity": (
                    "raw_quote_events",
                    "depth_total5_for_prior_LIQUIDITY_STRESS_check",
                ),
            }
        )
        if result.features is not None:
            payload["partial_abs_ret_condition"] = (
                abs(result.features.ret_5m) >= DISCOVERY_ABS_RET_5M
            )
        return _not_ready("005i_price_discovery_exact_parity_unavailable", payload)


class LiquidityStressContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[2], REGIME_FREEZE_BLOB)

    def __init__(self, state: Live005IMinuteState) -> None:
        self._state = state

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        result = self._state.context(snapshot)
        payload = _base_005i_payload(result, frozen_object="LIQUIDITY_STRESS")
        payload.update(
            {
                "frozen_threshold_relative_spread": LIQUIDITY_STRESS_RELATIVE_SPREAD,
                "frozen_threshold_depth_total5_max": LIQUIDITY_STRESS_DEPTH_TOTAL5_MAX,
                "missing_exact_parity": ("polymarket_depth_total5",),
            }
        )
        if result.features is not None:
            payload["partial_relative_spread_condition"] = (
                result.features.relative_spread
                >= LIQUIDITY_STRESS_RELATIVE_SPREAD
            )
        return _not_ready("005i_liquidity_stress_depth_parity_unavailable", payload)


class WithdrawalReplenishmentContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[3], REGIME_FREEZE_BLOB)

    def __init__(self, state: Live005IMinuteState) -> None:
        self._state = state

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        result = self._state.context(snapshot)
        payload = _base_005i_payload(
            result,
            frozen_object="RESILIENCE_AFTER_WITHDRAWAL",
        )
        payload.update(
            {
                "frozen_withdrawal_1m_threshold": WITHDRAWAL_1M,
                "frozen_replenishment_1m_threshold": REPLENISHMENT_1M,
                "frozen_definition": (
                    "withdrawal_1m=max(-(depth_total5[t]-depth_total5[t-1]),0); "
                    "recovered iff next-minute replenishment_1m>=200"
                ),
                "missing_exact_parity": (
                    "polymarket_depth_total5",
                    "next_minute_depth_total5",
                ),
            }
        )
        return _not_ready(
            "005i_withdrawal_replenishment_depth_parity_unavailable",
            payload,
        )


class DepthNormalisedOfiContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[4], OFI_FREEZE_BLOB)

    def __init__(self, state: Live005IMinuteState) -> None:
        self._state = state

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        result = self._state.context(snapshot)
        payload = _base_005i_payload(
            result,
            frozen_object="OFI_TRAIN_MODEL_FREEZE",
        )
        payload.update(
            {
                "frozen_model": "ofi_challenger",
                "output": None,
                "output_status": "NOT_COMPUTED_WITH_PARTIAL_INPUTS",
                "available_exact_inputs": (
                    "mid",
                    "relative_spread",
                    "rv_5m",
                    "ret_1m",
                    "ret_5m",
                ),
                "missing_exact_parity": (
                    "depth_total5",
                    "raw_quote_events",
                    "imbalance1",
                    "ofi_depth_norm",
                ),
            }
        )
        return _not_ready("005i_ofi_exact_feature_parity_unavailable", payload)


class Renewal005FContextProvider:
    spec = _spec(FROZEN_RESEARCH_MODEL_IDS[5], HAZARD005F_FROZEN_SPEC_VERSION)

    def __init__(self, provider: Live005FStateProvider) -> None:
        self._provider = provider

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> ModelDecision:
        vector = self._provider.feature_vector(snapshot)
        payload: dict[str, object] = {
            "research_id": "EXPERIMENT-005F",
            "frozen_spec_version": HAZARD005F_FROZEN_SPEC_VERSION,
            "paper_only": True,
            "live_eligible": False,
            "provider_id": self._provider.provider_id,
            "provider_version": self._provider.version,
            "measurement_intent": "future_markout_and_economics_only",
        }
        if vector is None:
            return _not_ready("005f_exact_renewal_state_not_ready", payload)
        payload.update(
            {
                "scope_id": vector.scope_id,
                "grid_time_ns": vector.grid_time_ns,
                "observed_monotonic_ns": vector.observed_monotonic_ns,
                "source_version": vector.source_version,
                "features": dict(vector.values),
            }
        )
        return ModelDecision(
            kind=ModelDecisionKind.CONTEXT,
            signal_value=float(vector.values.get("genuine_age_s", 0.0)),
            reason="005f_exact_renewal_state_context",
            context=payload,
        )


def frozen_research_paper_providers(
    *,
    mapping: MappingDocument,
    hazard_005f: Live005FStateProvider,
) -> tuple[Live005IMinuteState, tuple[ModelProvider, ...]]:
    state = Live005IMinuteState(mapping)
    providers: tuple[ModelProvider, ...] = (
        Recent5mReversalContextProvider(state),
        PriceDiscoveryContextProvider(state),
        LiquidityStressContextProvider(state),
        WithdrawalReplenishmentContextProvider(state),
        DepthNormalisedOfiContextProvider(state),
        Renewal005FContextProvider(hazard_005f),
    )
    return state, providers
