"""Auditable baseline maker mathematics for MAKE-001."""

from __future__ import annotations

import math
from dataclasses import replace

from predictions_cup.maker.contracts import (
    FairValueResult,
    GateDecision,
    GateMode,
    MakerMarketSnapshot,
    PredictiveAdjustment,
    QuoteContext,
    QuoteSizes,
    ToxicityEstimate,
)
from predictions_cup.runtime.models import SIG_TICK

_TICK = float(SIG_TICK)


class NullPredictiveAdjuster:
    model_id = "null-predictive"
    version = "make-001-v1"

    def adjust(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> PredictiveAdjustment:
        del fair_value
        return PredictiveAdjustment(
            probability_shift=0.0,
            confidence=1.0,
            observed_monotonic_ns=snapshot.now_monotonic_ns,
            trusted=True,
            model_id=self.model_id,
            version=self.version,
        )


class NullToxicityProvider:
    model_id = "null-toxicity"
    version = "make-001-v1"

    def estimate(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> ToxicityEstimate:
        del fair_value
        return ToxicityEstimate(
            update_hazard=0.0,
            adverse_selection=0.0,
            observed_monotonic_ns=snapshot.now_monotonic_ns,
            trusted=True,
            model_id=self.model_id,
            version=self.version,
        )


class AvellanedaStoikovInventoryModel:
    """A-S-inspired reservation price in probability space.

    Classical reservation price is s - q*gamma*sigma^2*(T-t). We use normalized
    inventory q/max_q because Cup contracts share a bounded probability payoff.
    The effective variance is the maximum of supplied short-horizon volatility,
    FV uncertainty, and a small configured floor; this avoids silently disabling
    inventory control when a volatility plugin is absent.
    """

    model_id = "as-normalized-inventory"
    version = "make-001-v1"

    def __init__(
        self,
        *,
        risk_aversion: float = 1.0,
        variance_horizon: float = 1.0,
        variance_floor: float = 0.000025,
    ) -> None:
        if risk_aversion < 0.0 or variance_horizon < 0.0 or variance_floor < 0.0:
            raise ValueError("inventory model parameters must be non-negative")
        self._risk_aversion = risk_aversion
        self._variance_horizon = variance_horizon
        self._variance_floor = variance_floor

    def reservation_price(self, context: QuoteContext) -> float:
        if context.max_abs_inventory <= 0.0:
            raise ValueError("max_abs_inventory must be positive")
        fraction = max(
            -1.5,
            min(1.5, context.signed_inventory / context.max_abs_inventory),
        )
        volatility = context.snapshot.volatility or 0.0
        variance = max(
            volatility * volatility,
            context.raw_fair_value.uncertainty * context.raw_fair_value.uncertainty,
            self._variance_floor,
        )
        shift = (
            fraction
            * self._risk_aversion
            * variance
            * self._variance_horizon
        )
        return min(0.995, max(0.005, context.adjusted_fair_value - shift))


class ConservativeSpreadPolicy:
    """Simple additive width model whose inputs have explicit probability units."""

    policy_id = "conservative-additive-spread"
    version = "make-001-v1"

    def __init__(
        self,
        *,
        base_half_spread_ticks: float = 1.0,
        uncertainty_multiplier: float = 1.0,
        volatility_multiplier: float = 0.5,
        toxicity_half_spread_ticks: float = 4.0,
    ) -> None:
        values = (
            base_half_spread_ticks,
            uncertainty_multiplier,
            volatility_multiplier,
            toxicity_half_spread_ticks,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("spread parameters must be finite and non-negative")
        self._base = base_half_spread_ticks * _TICK
        self._uncertainty_multiplier = uncertainty_multiplier
        self._volatility_multiplier = volatility_multiplier
        self._toxicity = toxicity_half_spread_ticks * _TICK

    def half_spread(self, context: QuoteContext) -> float:
        volatility = context.snapshot.volatility or 0.0
        toxic = max(
            context.toxicity.update_hazard,
            context.toxicity.adverse_selection,
        )
        return max(
            _TICK * 0.5,
            self._base
            + self._uncertainty_multiplier * context.raw_fair_value.uncertainty
            + self._volatility_multiplier * volatility
            + self._toxicity * toxic,
        )


class InventoryConfidenceSizePolicy:
    policy_id = "inventory-confidence-size"
    version = "make-001-v1"

    def __init__(
        self,
        *,
        base_size: int = 2,
        minimum_size: int = 1,
    ) -> None:
        if base_size <= 0 or minimum_size <= 0 or minimum_size > base_size:
            raise ValueError("invalid maker size configuration")
        self._base_size = base_size
        self._minimum_size = minimum_size

    def sizes(self, context: QuoteContext) -> QuoteSizes:
        if context.max_abs_inventory <= 0.0:
            raise ValueError("max_abs_inventory must be positive")
        confidence = min(
            context.raw_fair_value.confidence,
            context.prediction.confidence,
        )
        toxicity_scale = 1.0 - max(
            context.toxicity.update_hazard,
            context.toxicity.adverse_selection,
        )
        bid_headroom = max(
            0.0,
            min(
                1.0,
                (context.max_abs_inventory - context.signed_inventory)
                / context.max_abs_inventory,
            ),
        )
        ask_headroom = max(
            0.0,
            min(
                1.0,
                (context.max_abs_inventory + context.signed_inventory)
                / context.max_abs_inventory,
            ),
        )
        return QuoteSizes(
            bid=self._scaled(confidence * toxicity_scale * bid_headroom),
            ask=self._scaled(confidence * toxicity_scale * ask_headroom),
        )

    def _scaled(self, factor: float) -> int:
        if factor <= 0.0:
            return 0
        raw = int(math.floor(self._base_size * min(1.0, factor)))
        if raw <= 0:
            return self._minimum_size
        return max(self._minimum_size, min(self._base_size, raw))


class ConservativeEligibilityPolicy:
    policy_id = "conservative-maker-eligibility"
    version = "make-001-v1"

    def __init__(
        self,
        *,
        max_bbo_age_ns: int,
        max_fv_age_ns: int,
        max_account_age_ns: int,
        max_inventory_age_ns: int,
        max_optional_signal_age_ns: int,
        require_trusted_depth: bool = False,
        max_depth_age_ns: int | None = None,
        widen_toxicity_at: float = 0.35,
        suspend_toxicity_at: float = 0.80,
    ) -> None:
        ages = (
            max_bbo_age_ns,
            max_fv_age_ns,
            max_account_age_ns,
            max_inventory_age_ns,
            max_optional_signal_age_ns,
        )
        if min(ages) <= 0:
            raise ValueError("freshness limits must be positive")
        if require_trusted_depth and (
            max_depth_age_ns is None or max_depth_age_ns <= 0
        ):
            raise ValueError("trusted-depth policy requires positive max_depth_age_ns")
        if not 0.0 <= widen_toxicity_at <= suspend_toxicity_at <= 1.0:
            raise ValueError("invalid toxicity thresholds")
        self._max_bbo_age_ns = max_bbo_age_ns
        self._max_fv_age_ns = max_fv_age_ns
        self._max_account_age_ns = max_account_age_ns
        self._max_inventory_age_ns = max_inventory_age_ns
        self._max_signal_age_ns = max_optional_signal_age_ns
        self._require_depth = require_trusted_depth
        self._max_depth_age_ns = max_depth_age_ns
        self._widen_toxicity_at = widen_toxicity_at
        self._suspend_toxicity_at = suspend_toxicity_at

    def gate(self, context: QuoteContext) -> GateDecision:
        snapshot = context.snapshot
        market = snapshot.runtime.market(snapshot.market_id)
        if market is None:
            return GateDecision(GateMode.SUSPEND, "unknown_market")
        if market.status != "open":
            return GateDecision(GateMode.CANCEL, "market_not_open")
        if market.tournament_id != snapshot.tournament_id:
            return GateDecision(GateMode.SUSPEND, "tournament_mismatch")
        if snapshot.exchange_id not in market.exchange_ids:
            return GateDecision(GateMode.SUSPEND, "exchange_market_mismatch")
        if not market.mapping_accepted or not market.tradeable:
            return GateDecision(GateMode.NO_TRADE, "mapping_not_tradeable")
        if not snapshot.runtime.portfolio.account_trusted:
            return GateDecision(GateMode.CANCEL, "account_untrusted")
        if not snapshot.sig_bbo_trusted:
            return GateDecision(GateMode.CANCEL, "sig_bbo_untrusted")
        if self._stale(
            snapshot.now_monotonic_ns,
            snapshot.sig_bbo_observed_ns,
            self._max_bbo_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "sig_bbo_stale")
        if self._stale(
            snapshot.now_monotonic_ns,
            snapshot.account_observed_ns,
            self._max_account_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "account_stale")
        if self._stale(
            snapshot.now_monotonic_ns,
            snapshot.inventory_observed_ns,
            self._max_inventory_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "inventory_stale")
        if not context.raw_fair_value.usable:
            return GateDecision(GateMode.CANCEL, f"fv_{context.raw_fair_value.reason}")
        if self._stale(
            snapshot.now_monotonic_ns,
            context.raw_fair_value.observed_monotonic_ns,
            self._max_fv_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "fv_stale")
        if not context.prediction.trusted or self._stale(
            snapshot.now_monotonic_ns,
            context.prediction.observed_monotonic_ns,
            self._max_signal_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "predictive_signal_untrusted_or_stale")
        if not context.toxicity.trusted or self._stale(
            snapshot.now_monotonic_ns,
            context.toxicity.observed_monotonic_ns,
            self._max_signal_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "toxicity_untrusted_or_stale")
        if self._require_depth and (
            not snapshot.sig_depth_trusted
            or snapshot.sig_depth_observed_ns is None
            or self._max_depth_age_ns is None
            or self._stale(
                snapshot.now_monotonic_ns,
                snapshot.sig_depth_observed_ns,
                self._max_depth_age_ns,
            )
        ):
            return GateDecision(GateMode.CANCEL, "sig_depth_untrusted_or_stale")

        toxic = max(
            context.toxicity.update_hazard,
            context.toxicity.adverse_selection,
        )
        if toxic >= self._suspend_toxicity_at:
            return GateDecision(GateMode.SUSPEND, "toxicity_suspend")
        if context.signed_inventory >= context.max_abs_inventory:
            return GateDecision(GateMode.ASK_ONLY, "positive_inventory_boundary")
        if context.signed_inventory <= -context.max_abs_inventory:
            return GateDecision(GateMode.BID_ONLY, "negative_inventory_boundary")
        if toxic >= self._widen_toxicity_at:
            return GateDecision(
                GateMode.WIDER,
                "toxicity_widen",
                spread_multiplier=1.0 + toxic,
                size_multiplier=max(0.1, 1.0 - toxic),
            )
        return GateDecision(GateMode.NORMAL, "ok")

    @staticmethod
    def _stale(now_ns: int, observed_ns: int, max_age_ns: int) -> bool:
        age = now_ns - observed_ns
        return age < 0 or age > max_age_ns


def with_quote_math(
    context: QuoteContext,
    *,
    reservation_price: float,
    half_spread: float,
) -> QuoteContext:
    """Small helper to preserve immutable quote context between policies."""
    return replace(
        context,
        reservation_price=reservation_price,
        half_spread=half_spread,
    )
