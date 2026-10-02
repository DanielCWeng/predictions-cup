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


class BinaryCaraInventoryModel:
    """Exact binary-CARA reservation probability from MATHS_LEDGER M-041.

    For a Bernoulli settlement payoff and CARA utility, the marginal reservation
    probability for another YES share is:

        r(q) = sigmoid(logit(p) - gamma * q)

    where p is the adjusted fair probability, q is signed YES inventory and gamma
    is the configured per-share risk-aversion coefficient. Unlike literal
    Avellaneda-Stoikov, this does not assume a Brownian mid-price or stationary
    Poisson fills.
    """

    model_id = "binary-cara-inventory"
    version = "make-001-v1"

    def __init__(self, *, risk_aversion: float = 0.02) -> None:
        if not math.isfinite(risk_aversion) or risk_aversion < 0.0:
            raise ValueError("risk_aversion must be finite and non-negative")
        self._risk_aversion = risk_aversion

    def reservation_price(self, context: QuoteContext) -> float:
        p = context.adjusted_fair_value
        if not math.isfinite(p) or not 0.0 < p < 1.0:
            raise ValueError("binary CARA requires fair probability strictly within (0,1)")
        q = context.signed_inventory
        if not math.isfinite(q):
            raise ValueError("inventory must be finite")
        logit = math.log(p / (1.0 - p))
        shifted = logit - self._risk_aversion * q
        if shifted >= 0.0:
            exp_neg = math.exp(-shifted)
            reservation = 1.0 / (1.0 + exp_neg)
        else:
            exp_pos = math.exp(shifted)
            reservation = exp_pos / (1.0 + exp_pos)
        return min(0.995, max(0.005, reservation))


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
                (context.max_abs_inventory - context.signed_inventory) / context.max_abs_inventory,
            ),
        )
        ask_headroom = max(
            0.0,
            min(
                1.0,
                (context.max_abs_inventory + context.signed_inventory) / context.max_abs_inventory,
            ),
        )
        bid_capacity = max(
            0,
            math.floor(context.max_abs_inventory - context.signed_inventory),
        )
        ask_capacity = max(
            0,
            math.floor(context.max_abs_inventory + context.signed_inventory),
        )
        return QuoteSizes(
            bid=min(
                bid_capacity,
                self._scaled(confidence * toxicity_scale * bid_headroom),
            ),
            ask=min(
                ask_capacity,
                self._scaled(confidence * toxicity_scale * ask_headroom),
            ),
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
        min_fair_value: float = 0.0,
        max_fair_value: float = 1.0,
        account_proxy_enabled: bool = False,
        max_account_proxy_age_ns: int = 600_000_000_000,
        proxy_soft_unwind_limit: float = 150.0,
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
        if require_trusted_depth and (max_depth_age_ns is None or max_depth_age_ns <= 0):
            raise ValueError("trusted-depth policy requires positive max_depth_age_ns")
        if not 0.0 <= widen_toxicity_at <= suspend_toxicity_at <= 1.0:
            raise ValueError("invalid toxicity thresholds")
        if not 0.0 <= min_fair_value < max_fair_value <= 1.0:
            raise ValueError("invalid fair-value band")
        if max_account_proxy_age_ns <= 0 or proxy_soft_unwind_limit <= 0.0:
            raise ValueError("invalid account-proxy policy limits")
        self._min_fair_value = min_fair_value
        self._max_fair_value = max_fair_value
        self._account_proxy_enabled = account_proxy_enabled
        self._max_account_proxy_age_ns = max_account_proxy_age_ns
        self._proxy_soft_unwind_limit = proxy_soft_unwind_limit
        self._max_bbo_age_ns = max_bbo_age_ns
        self._max_fv_age_ns = max_fv_age_ns
        self._max_account_age_ns = max_account_age_ns
        self._max_inventory_age_ns = max_inventory_age_ns
        self._max_signal_age_ns = max_optional_signal_age_ns
        self._require_depth = require_trusted_depth
        self._max_depth_age_ns = max_depth_age_ns
        self._widen_toxicity_at = widen_toxicity_at
        self._suspend_toxicity_at = suspend_toxicity_at

    def next_recheck_monotonic_ns(
        self,
        context: QuoteContext,
    ) -> int | None:
        snapshot = context.snapshot
        deadlines = [
            snapshot.sig_bbo_observed_ns + self._max_bbo_age_ns,
            self._fair_value_observed_ns(context) + self._max_fv_age_ns,
            context.prediction.observed_monotonic_ns + self._max_signal_age_ns,
            context.toxicity.observed_monotonic_ns + self._max_signal_age_ns,
        ]
        portfolio = snapshot.runtime.portfolio
        if portfolio.proxy_active and portfolio.account_proxy_age_ns is not None:
            deadlines.append(
                snapshot.now_monotonic_ns
                + max(
                    0,
                    self._max_account_proxy_age_ns - portfolio.account_proxy_age_ns,
                )
            )
        else:
            deadlines.extend(
                (
                    snapshot.account_observed_ns + self._max_account_age_ns,
                    snapshot.inventory_observed_ns + self._max_inventory_age_ns,
                )
            )
        if self._require_depth:
            if snapshot.sig_depth_observed_ns is None or self._max_depth_age_ns is None:
                return snapshot.now_monotonic_ns
            deadlines.append(snapshot.sig_depth_observed_ns + self._max_depth_age_ns)
        return min(deadlines)

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
        # Account reconciliation is a bounded HOLD, not permission to retain
        # resting exposure indefinitely. Once the last accepted/authoritative
        # account observation ages out, stale capital truth forces withdrawal.
        portfolio = snapshot.runtime.portfolio
        if portfolio.proxy_active:
            if (
                portfolio.account_proxy_age_ns is None
                or portfolio.account_proxy_age_ns > self._max_account_proxy_age_ns
            ):
                return GateDecision(GateMode.CANCEL, "account_proxy_stale")
        elif self._stale(
            snapshot.now_monotonic_ns,
            snapshot.account_observed_ns,
            self._max_account_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "account_stale")
        if not snapshot.runtime.portfolio.account_trusted:
            return GateDecision(GateMode.HOLD, "account_untrusted")
        sig_bbo_stale = self._stale(
            snapshot.now_monotonic_ns,
            snapshot.sig_bbo_observed_ns,
            self._max_bbo_age_ns,
        )
        bbo_proxy = not snapshot.sig_bbo_trusted or sig_bbo_stale
        if not portfolio.proxy_active and self._stale(
            snapshot.now_monotonic_ns,
            snapshot.inventory_observed_ns,
            self._max_inventory_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "inventory_stale")
        if not context.raw_fair_value.usable:
            return GateDecision(GateMode.CANCEL, f"fv_{context.raw_fair_value.reason}")
        if self._stale(
            snapshot.now_monotonic_ns,
            self._fair_value_observed_ns(context),
            self._max_fv_age_ns,
        ):
            return GateDecision(GateMode.CANCEL, "fv_stale")
        fair = context.raw_fair_value.value
        if fair is not None and not (self._min_fair_value <= fair <= self._max_fair_value):
            # Quote only mid-range markets; held inventory is kept, not dumped.
            return GateDecision(GateMode.CANCEL, "fv_outside_band")
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

        if bbo_proxy:
            book = snapshot.runtime.book(snapshot.exchange_id)
            if (
                not self._account_proxy_enabled
                or book is None
                or book.market_id != snapshot.market_id
                or book.tournament_id != snapshot.tournament_id
                or book.observed_monotonic_ns != snapshot.sig_bbo_observed_ns
                or not book.bids
                or not book.asks
                or context.raw_fair_value.source_id != "direct-polymarket"
                or self._stale(
                    snapshot.now_monotonic_ns,
                    context.raw_fair_value.observed_monotonic_ns,
                    self._max_fv_age_ns,
                )
            ):
                return GateDecision(
                    GateMode.CANCEL,
                    "sig_bbo_untrusted" if not snapshot.sig_bbo_trusted else "sig_bbo_stale",
                )
            reducing_mode = self._inventory_reducing_mode(context)
            if reducing_mode is None:
                return GateDecision(GateMode.CANCEL, "bbo_proxy_no_reducing_side")
            widened = toxic >= self._widen_toxicity_at
            return GateDecision(
                reducing_mode,
                "bbo_proxy_inventory_reducing",
                spread_multiplier=1.0 + toxic if widened else 1.0,
                size_multiplier=max(0.1, 1.0 - toxic) if widened else 1.0,
            )

        low, high = self._inventory_bounds(context)
        if high >= context.max_abs_inventory:
            return GateDecision(GateMode.ASK_ONLY, "positive_inventory_boundary")
        if low <= -context.max_abs_inventory:
            return GateDecision(GateMode.BID_ONLY, "negative_inventory_boundary")
        if portfolio.proxy_active and max(abs(low), abs(high)) > self._proxy_soft_unwind_limit:
            reducing_mode = self._inventory_reducing_mode(context)
            if reducing_mode is None:
                return GateDecision(GateMode.CANCEL, "proxy_unwind_side_unknown")
            return GateDecision(reducing_mode, "proxy_soft_unwind")
        if toxic >= self._widen_toxicity_at:
            return GateDecision(
                GateMode.WIDER,
                "toxicity_widen",
                spread_multiplier=1.0 + toxic,
                size_multiplier=max(0.1, 1.0 - toxic),
            )
        return GateDecision(GateMode.NORMAL, "ok")

    @staticmethod
    def _inventory_bounds(
        context: QuoteContext,
        *,
        include_orders: bool = False,
    ) -> tuple[float, float]:
        portfolio = context.snapshot.runtime.portfolio
        if not portfolio.proxy_active and not include_orders:
            return context.signed_inventory, context.signed_inventory
        return portfolio.worst_case_inventory_bounds(
            context.snapshot.exchange_id,
            context.snapshot.tournament_id,
        )

    def _inventory_reducing_mode(self, context: QuoteContext) -> GateMode | None:
        low, high = self._inventory_bounds(context, include_orders=True)
        if low > 0.0:
            return GateMode.ASK_ONLY
        if high < 0.0:
            return GateMode.BID_ONLY
        return None

    @staticmethod
    def _fair_value_observed_ns(context: QuoteContext) -> int:
        observed = context.raw_fair_value.observed_monotonic_ns
        feed_observed = context.snapshot.external_feed_observed_ns
        if context.raw_fair_value.source_id == "direct-polymarket" and feed_observed is not None:
            return max(observed, feed_observed)
        return observed

    @staticmethod
    def _stale(now_ns: int, observed_ns: int, max_age_ns: int) -> bool:
        age = now_ns - observed_ns
        return age < 0 or age >= max_age_ns


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
