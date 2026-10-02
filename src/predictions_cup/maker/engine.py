"""Pure low-latency market-maker calculation engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic_ns

from predictions_cup.maker.contracts import (
    DesiredQuote,
    EligibilityPolicy,
    FairValueProvider,
    GateDecision,
    GateMode,
    InventoryModel,
    MakerDecision,
    MakerMarketSnapshot,
    MakerTrace,
    PredictiveAdjuster,
    QuoteContext,
    SizePolicy,
    SpreadPolicy,
    ToxicityProvider,
)
from predictions_cup.maker.policies import with_quote_math
from predictions_cup.runtime.models import SIG_TICK

_TICK = float(SIG_TICK)
_HARD_MAX_PROJECTED_INVENTORY = 200.0


@dataclass(frozen=True, slots=True)
class MakerConfig:
    strategy_id: str = "make-direct-pm"
    strategy_version: str = "make-001-v1"
    max_abs_inventory: float = 10.0
    account_proxy_size_factor: float = 0.25

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("maker strategy identity/version must not be blank")
        if not math.isfinite(self.max_abs_inventory) or self.max_abs_inventory <= 0.0:
            raise ValueError("max_abs_inventory must be finite and positive")
        if not 0.0 < self.account_proxy_size_factor <= 1.0:
            raise ValueError("account proxy size factor must be within (0, 1]")


class MakerEngine:
    """Pure plugin composition. No I/O, await, logging, Pydantic or hidden state."""

    def __init__(
        self,
        *,
        fair_value: FairValueProvider,
        predictive: PredictiveAdjuster,
        toxicity: ToxicityProvider,
        inventory: InventoryModel,
        spread: SpreadPolicy,
        size: SizePolicy,
        eligibility: EligibilityPolicy,
        config: MakerConfig | None = None,
    ) -> None:
        self._fair_value = fair_value
        self._predictive = predictive
        self._toxicity = toxicity
        self._inventory = inventory
        self._spread = spread
        self._size = size
        self._eligibility = eligibility
        self._config = config or MakerConfig()

    @property
    def strategy_id(self) -> str:
        return self._config.strategy_id

    def quote(self, snapshot: MakerMarketSnapshot) -> MakerDecision:
        try:
            fair_value = self._fair_value.fair_value(snapshot)
        except Exception:
            return self._failed(snapshot, "fair_value_plugin_exception")

        if not fair_value.usable:
            return self._failed(
                snapshot,
                f"fair_value_unusable:{fair_value.reason}",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
            )

        try:
            prediction = self._predictive.adjust(snapshot, fair_value)
            toxicity = self._toxicity.estimate(snapshot, fair_value)
        except Exception:
            return self._failed(
                snapshot,
                "signal_plugin_exception",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
            )

        assert fair_value.value is not None
        adjusted = fair_value.value + prediction.probability_shift
        if not math.isfinite(adjusted) or not 0.0 < adjusted < 1.0:
            return self._failed(
                snapshot,
                "adjusted_fv_out_of_bounds",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
                predictive_shift=prediction.probability_shift,
            )

        signed_inventory = sum(
            position.signed_quantity
            for position in snapshot.runtime.portfolio.positions
            if (
                position.exchange_id == snapshot.exchange_id
                and position.tournament_id == snapshot.tournament_id
            )
        )
        max_abs_inventory = min(
            self._config.max_abs_inventory,
            _HARD_MAX_PROJECTED_INVENTORY,
        )
        context = QuoteContext(
            snapshot=snapshot,
            raw_fair_value=fair_value,
            adjusted_fair_value=adjusted,
            prediction=prediction,
            toxicity=toxicity,
            signed_inventory=signed_inventory,
            max_abs_inventory=max_abs_inventory,
        )

        try:
            gate = self._eligibility.gate(context)
            next_recheck_ns = self._eligibility.next_recheck_monotonic_ns(context)
            if next_recheck_ns is not None and next_recheck_ns < 0:
                raise ValueError("freshness deadline must be non-negative")
        except Exception:
            return self._failed(
                snapshot,
                "eligibility_plugin_exception",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
                predictive_shift=prediction.probability_shift,
            )

        if gate.mode in {GateMode.HOLD, GateMode.CANCEL, GateMode.SUSPEND, GateMode.NO_TRADE}:
            return MakerDecision(
                desired=None,
                gate=gate,
                trace=self._trace(
                    snapshot=snapshot,
                    fair_value=fair_value.value,
                    fv_source=fair_value.source_id,
                    fv_version=fair_value.source_version,
                    prediction_shift=prediction.probability_shift,
                    adjusted=adjusted,
                    uncertainty=fair_value.uncertainty,
                    confidence=fair_value.confidence,
                    update_hazard=toxicity.update_hazard,
                    adverse_selection=toxicity.adverse_selection,
                    signed_inventory=signed_inventory,
                    reservation_price=None,
                    half_spread=None,
                    bid_ticks=None,
                    ask_ticks=None,
                    bid_size=0,
                    ask_size=0,
                    gate=gate,
                ),
                next_recheck_monotonic_ns=None,
            )

        try:
            reservation = self._inventory.reservation_price(context)
            if not math.isfinite(reservation) or not 0.0 < reservation < 1.0:
                raise ValueError("reservation price outside probability support")
            provisional = with_quote_math(
                context,
                reservation_price=reservation,
                half_spread=0.0,
            )
            half_spread = self._spread.half_spread(provisional)
            if not math.isfinite(half_spread) or half_spread <= 0.0:
                raise ValueError("half spread must be finite and positive")
            half_spread *= gate.spread_multiplier
            priced = with_quote_math(
                context,
                reservation_price=reservation,
                half_spread=half_spread,
            )
            sizes = self._size.sizes(priced)
        except Exception:
            return self._failed(
                snapshot,
                "quote_policy_exception",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
                predictive_shift=prediction.probability_shift,
            )

        bid_ticks, ask_ticks = self._passive_ticks(
            snapshot,
            reservation=reservation,
            half_spread=half_spread,
        )
        if bid_ticks is None and ask_ticks is None:
            return self._failed(
                snapshot,
                "no_passive_quote_available",
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                raw_fv=fair_value.value,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
                predictive_shift=prediction.probability_shift,
            )

        bid_size = int(math.floor(sizes.bid * gate.size_multiplier))
        ask_size = int(math.floor(sizes.ask * gate.size_multiplier))
        portfolio = snapshot.runtime.portfolio
        if portfolio.proxy_active:
            low, high = portfolio.worst_case_inventory_bounds(
                snapshot.exchange_id,
                snapshot.tournament_id,
            )
            if high < 0.0:
                bid_size = min(bid_size, max(0, math.floor(-high)))
            elif bid_size > 0:
                bid_size = int(math.floor(bid_size * self._config.account_proxy_size_factor))
            if low > 0.0:
                ask_size = min(ask_size, max(0, math.floor(low)))
            elif ask_size > 0:
                ask_size = int(math.floor(ask_size * self._config.account_proxy_size_factor))
            bid_room = max(0, math.floor(max_abs_inventory - high))
            ask_room = max(0, math.floor(max_abs_inventory + low))
            bid_size = min(bid_size, bid_room)
            ask_size = min(ask_size, ask_room)
        elif gate.reason == "bbo_proxy_inventory_reducing":
            low, high = portfolio.worst_case_inventory_bounds(
                snapshot.exchange_id,
                snapshot.tournament_id,
            )
            if gate.mode is GateMode.ASK_ONLY:
                ask_size = min(ask_size, max(0, math.floor(low)))
            elif gate.mode is GateMode.BID_ONLY:
                bid_size = min(bid_size, max(0, math.floor(-high)))

        # The projected interval includes every open and unresolved order,
        # regardless of account trust grade. At or beyond a boundary, quote
        # only a side that reduces every represented position without crossing
        # through zero. This also lets an over-cap position unwind safely.
        low, high = portfolio.worst_case_inventory_bounds(
            snapshot.exchange_id,
            snapshot.tournament_id,
        )
        if high >= max_abs_inventory or low <= -max_abs_inventory:
            bid_size = min(bid_size, max(0, math.floor(-high))) if high < 0.0 else 0
            ask_size = min(ask_size, max(0, math.floor(low))) if low > 0.0 else 0
        else:
            bid_room = max(0, math.floor(max_abs_inventory - high))
            ask_room = max(0, math.floor(max_abs_inventory + low))
            bid_size = min(bid_size, bid_room)
            ask_size = min(ask_size, ask_room)
        if bid_ticks is None:
            bid_size = 0
        if ask_ticks is None:
            ask_size = 0
        if gate.mode is GateMode.BID_ONLY:
            ask_ticks = None
            ask_size = 0
        elif gate.mode is GateMode.ASK_ONLY:
            bid_ticks = None
            bid_size = 0

        if bid_size <= 0:
            bid_ticks = None
            bid_size = 0
        if ask_size <= 0:
            ask_ticks = None
            ask_size = 0
        if bid_ticks is None and ask_ticks is None:
            gate = GateDecision(GateMode.SUSPEND, "size_policy_zero")

        desired = None
        if gate.mode not in {GateMode.HOLD, GateMode.CANCEL, GateMode.SUSPEND, GateMode.NO_TRADE}:
            desired = DesiredQuote(
                exchange_id=snapshot.exchange_id,
                market_id=snapshot.market_id,
                tournament_id=snapshot.tournament_id,
                bid_ticks=bid_ticks,
                ask_ticks=ask_ticks,
                bid_size=bid_size,
                ask_size=ask_size,
            )

        return MakerDecision(
            desired=desired,
            gate=gate,
            trace=self._trace(
                snapshot=snapshot,
                fair_value=fair_value.value,
                fv_source=fair_value.source_id,
                fv_version=fair_value.source_version,
                prediction_shift=prediction.probability_shift,
                adjusted=adjusted,
                uncertainty=fair_value.uncertainty,
                confidence=fair_value.confidence,
                update_hazard=toxicity.update_hazard,
                adverse_selection=toxicity.adverse_selection,
                signed_inventory=signed_inventory,
                reservation_price=reservation,
                half_spread=half_spread,
                bid_ticks=bid_ticks,
                ask_ticks=ask_ticks,
                bid_size=bid_size,
                ask_size=ask_size,
                gate=gate,
            ),
            next_recheck_monotonic_ns=(next_recheck_ns if desired is not None else None),
        )

    @staticmethod
    def _passive_ticks(
        snapshot: MakerMarketSnapshot,
        *,
        reservation: float,
        half_spread: float,
    ) -> tuple[int | None, int | None]:
        book = snapshot.runtime.book(snapshot.exchange_id)
        if book is None:
            return None, None

        # The epsilon keeps an exact grid price (e.g. a mirrored PM touch at
        # 0.40) from flooring/ceiling one tick wider through float error.
        raw_bid = math.floor((reservation - half_spread) / _TICK + 1e-9)
        raw_ask = math.ceil((reservation + half_spread) / _TICK - 1e-9)
        bid_value = raw_bid if 1 <= raw_bid <= 199 else None
        ask_value = raw_ask if 1 <= raw_ask <= 199 else None

        if bid_value is not None and book.asks:
            best_ask = min(level.price_ticks for level in book.asks)
            bid_value = min(bid_value, best_ask - 1)
            if bid_value < 1:
                bid_value = None
        if ask_value is not None and book.bids:
            best_bid = max(level.price_ticks for level in book.bids)
            ask_value = max(ask_value, best_bid + 1)
            if ask_value > 199:
                ask_value = None
        if bid_value is not None and ask_value is not None and bid_value >= ask_value:
            return None, None
        return bid_value, ask_value

    def _failed(
        self,
        snapshot: MakerMarketSnapshot,
        reason: str,
        *,
        fv_source: str | None = None,
        fv_version: str | None = None,
        raw_fv: float | None = None,
        uncertainty: float = 0.0,
        confidence: float = 0.0,
        predictive_shift: float = 0.0,
    ) -> MakerDecision:
        gate = GateDecision(GateMode.SUSPEND, reason)
        return MakerDecision(
            desired=None,
            gate=gate,
            trace=MakerTrace(
                strategy_id=self._config.strategy_id,
                strategy_version=self._config.strategy_version,
                fv_source=fv_source or self._fair_value.provider_id,
                fv_version=fv_version or self._fair_value.version,
                raw_fv=raw_fv,
                predictive_shift=predictive_shift,
                adjusted_fv=None,
                uncertainty=uncertainty,
                confidence=confidence,
                update_hazard=0.0,
                adverse_selection=0.0,
                signed_inventory=0.0,
                reservation_price=None,
                half_spread=None,
                desired_bid_ticks=None,
                desired_ask_ticks=None,
                desired_bid_size=0,
                desired_ask_size=0,
                gate_mode=gate.mode,
                reason=reason,
                decision_monotonic_ns=snapshot.now_monotonic_ns,
            ),
        )

    def _trace(
        self,
        *,
        snapshot: MakerMarketSnapshot,
        fair_value: float | None,
        fv_source: str,
        fv_version: str,
        prediction_shift: float,
        adjusted: float | None,
        uncertainty: float,
        confidence: float,
        update_hazard: float,
        adverse_selection: float,
        signed_inventory: float,
        reservation_price: float | None,
        half_spread: float | None,
        bid_ticks: int | None,
        ask_ticks: int | None,
        bid_size: int,
        ask_size: int,
        gate: GateDecision,
    ) -> MakerTrace:
        return MakerTrace(
            strategy_id=self._config.strategy_id,
            strategy_version=self._config.strategy_version,
            fv_source=fv_source,
            fv_version=fv_version,
            raw_fv=fair_value,
            predictive_shift=prediction_shift,
            adjusted_fv=adjusted,
            uncertainty=uncertainty,
            confidence=confidence,
            update_hazard=update_hazard,
            adverse_selection=adverse_selection,
            signed_inventory=signed_inventory,
            reservation_price=reservation_price,
            half_spread=half_spread,
            desired_bid_ticks=bid_ticks,
            desired_ask_ticks=ask_ticks,
            desired_bid_size=bid_size,
            desired_ask_size=ask_size,
            gate_mode=gate.mode,
            reason=gate.reason,
            decision_monotonic_ns=snapshot.now_monotonic_ns,
        )


def maker_clock_ns() -> int:
    """Explicit injectable clock default for the asyncio shell."""
    return monotonic_ns()
