"""Quote lifecycle state machine for low-churn, fail-closed maker management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.execution.models import LifecycleState
from predictions_cup.maker.contracts import DesiredQuote, QuoteSide


class QuoteLifecycleActionKind(StrEnum):
    KEEP = "KEEP"
    PLACE = "PLACE"
    CANCEL = "CANCEL"
    WAIT_RECONCILIATION = "WAIT_RECONCILIATION"


@dataclass(frozen=True, slots=True)
class ActiveQuote:
    side: QuoteSide
    price_ticks: int
    size: int
    remaining_size: int
    logical_operation_id: str
    exchange_order_id: int | None
    lifecycle_state: LifecycleState
    observed_monotonic_ns: int

    @property
    def unresolved(self) -> bool:
        return self.lifecycle_state in {
            LifecycleState.PENDING,
            LifecycleState.ACKED,
            LifecycleState.CANCEL_PENDING,
            LifecycleState.UNCERTAIN,
            LifecycleState.RECONCILING,
        }

    @property
    def resting(self) -> bool:
        return self.lifecycle_state in {
            LifecycleState.OPEN,
            LifecycleState.PARTIALLY_FILLED,
        }


@dataclass(frozen=True, slots=True)
class MakerQuoteState:
    exchange_id: str
    bid: ActiveQuote | None = None
    ask: ActiveQuote | None = None
    last_action_monotonic_ns: int = 0


@dataclass(frozen=True, slots=True)
class QuoteLifecycleAction:
    kind: QuoteLifecycleActionKind
    side: QuoteSide
    reason: str
    desired_ticks: int | None = None
    desired_size: int = 0
    active: ActiveQuote | None = None


@dataclass(frozen=True, slots=True)
class QuoteLifecycleConfig:
    min_replace_ticks: int = 1
    min_replace_size: int = 1
    min_requote_interval_ns: int = 0

    def __post_init__(self) -> None:
        if self.min_replace_ticks <= 0 or self.min_replace_size <= 0:
            raise ValueError("replace materiality thresholds must be positive")
        if self.min_requote_interval_ns < 0:
            raise ValueError("min_requote_interval_ns must be non-negative")


class QuoteLifecycleManager:
    """Pure desired-vs-active quote state machine.

    Cancel-for-replace is deliberately two phase: the manager emits CANCEL first
    and will not emit PLACE until authoritative/local state no longer contains the
    previous economic exposure.
    """

    def __init__(self, config: QuoteLifecycleConfig | None = None) -> None:
        self._config = config or QuoteLifecycleConfig()

    def decide(
        self,
        *,
        desired: DesiredQuote | None,
        current: MakerQuoteState,
        now_monotonic_ns: int,
        force_cancel: bool = False,
    ) -> tuple[QuoteLifecycleAction, ...]:
        if now_monotonic_ns < 0:
            raise ValueError("now_monotonic_ns must be non-negative")
        if desired is not None and desired.exchange_id != current.exchange_id:
            raise ValueError("desired/current exchange mismatch")

        bid_ticks = None if desired is None else desired.bid_ticks
        ask_ticks = None if desired is None else desired.ask_ticks
        bid_size = 0 if desired is None else desired.bid_size
        ask_size = 0 if desired is None else desired.ask_size
        return (
            self._side_action(
                side=QuoteSide.BID,
                desired_ticks=bid_ticks,
                desired_size=bid_size,
                active=current.bid,
                current=current,
                now_ns=now_monotonic_ns,
                force_cancel=force_cancel,
            ),
            self._side_action(
                side=QuoteSide.ASK,
                desired_ticks=ask_ticks,
                desired_size=ask_size,
                active=current.ask,
                current=current,
                now_ns=now_monotonic_ns,
                force_cancel=force_cancel,
            ),
        )

    def _side_action(
        self,
        *,
        side: QuoteSide,
        desired_ticks: int | None,
        desired_size: int,
        active: ActiveQuote | None,
        current: MakerQuoteState,
        now_ns: int,
        force_cancel: bool,
    ) -> QuoteLifecycleAction:
        wants_quote = desired_ticks is not None and desired_size > 0

        if active is None:
            if not wants_quote:
                return QuoteLifecycleAction(
                    QuoteLifecycleActionKind.KEEP,
                    side,
                    "no_quote_required",
                )
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.PLACE,
                side,
                "initial_or_refill",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
            )

        if active.unresolved or active.exchange_order_id is None:
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.WAIT_RECONCILIATION,
                side,
                "existing_quote_unresolved",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                active=active,
            )

        if not active.resting:
            if wants_quote:
                return QuoteLifecycleAction(
                    QuoteLifecycleActionKind.PLACE,
                    side,
                    "terminal_quote_refill",
                    desired_ticks=desired_ticks,
                    desired_size=desired_size,
                )
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.KEEP,
                side,
                "terminal_no_quote",
            )

        if force_cancel or not wants_quote:
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.CANCEL,
                side,
                "forced_cancel" if force_cancel else "quote_no_longer_desired",
                active=active,
            )

        assert desired_ticks is not None
        price_move = abs(desired_ticks - active.price_ticks)
        size_move = abs(desired_size - active.remaining_size)
        material = (
            price_move >= self._config.min_replace_ticks
            or size_move >= self._config.min_replace_size
        )
        if not material:
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.KEEP,
                side,
                "below_materiality_threshold",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                active=active,
            )

        if (
            self._config.min_requote_interval_ns > 0
            and now_ns - current.last_action_monotonic_ns
            < self._config.min_requote_interval_ns
        ):
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.KEEP,
                side,
                "requote_interval_suppression",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                active=active,
            )

        return QuoteLifecycleAction(
            QuoteLifecycleActionKind.CANCEL,
            side,
            "cancel_for_material_replace",
            desired_ticks=desired_ticks,
            desired_size=desired_size,
            active=active,
        )


class QuoteRegistry:
    """Small mutable I/O-shell registry; immutable records cross the hot path."""

    def __init__(self) -> None:
        self._states: dict[str, MakerQuoteState] = {}

    def state(self, exchange_id: str) -> MakerQuoteState:
        return self._states.get(exchange_id, MakerQuoteState(exchange_id=exchange_id))

    def apply_authoritative(
        self,
        *,
        exchange_id: str,
        side: QuoteSide,
        price_ticks: int,
        size: int,
        remaining_size: int,
        logical_operation_id: str,
        exchange_order_id: int,
        lifecycle_state: LifecycleState,
        observed_monotonic_ns: int,
    ) -> None:
        quote = ActiveQuote(
            side=side,
            price_ticks=price_ticks,
            size=size,
            remaining_size=remaining_size,
            logical_operation_id=logical_operation_id,
            exchange_order_id=exchange_order_id,
            lifecycle_state=lifecycle_state,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        current = self.state(exchange_id)
        self._states[exchange_id] = self._replace_side(
            current,
            side=side,
            quote=quote,
            last_action_ns=observed_monotonic_ns,
        )

    def mark_submitted(
        self,
        *,
        exchange_id: str,
        side: QuoteSide,
        price_ticks: int,
        size: int,
        logical_operation_id: str,
        observed_monotonic_ns: int,
    ) -> None:
        """Reserve lifecycle locally until account/order identity is authoritative."""
        quote = ActiveQuote(
            side=side,
            price_ticks=price_ticks,
            size=size,
            remaining_size=size,
            logical_operation_id=logical_operation_id,
            exchange_order_id=None,
            lifecycle_state=LifecycleState.UNCERTAIN,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        current = self.state(exchange_id)
        self._states[exchange_id] = self._replace_side(
            current,
            side=side,
            quote=quote,
            last_action_ns=observed_monotonic_ns,
        )

    def mark_lifecycle(
        self,
        *,
        exchange_id: str,
        side: QuoteSide,
        lifecycle_state: LifecycleState,
        observed_monotonic_ns: int,
    ) -> None:
        current = self.state(exchange_id)
        active = current.bid if side is QuoteSide.BID else current.ask
        if active is None:
            return
        quote = ActiveQuote(
            side=active.side,
            price_ticks=active.price_ticks,
            size=active.size,
            remaining_size=active.remaining_size,
            logical_operation_id=active.logical_operation_id,
            exchange_order_id=active.exchange_order_id,
            lifecycle_state=lifecycle_state,
            observed_monotonic_ns=observed_monotonic_ns,
        )
        self._states[exchange_id] = self._replace_side(
            current,
            side=side,
            quote=quote,
            last_action_ns=observed_monotonic_ns,
        )

    def clear_side(
        self,
        *,
        exchange_id: str,
        side: QuoteSide,
        observed_monotonic_ns: int,
    ) -> None:
        current = self.state(exchange_id)
        self._states[exchange_id] = self._replace_side(
            current,
            side=side,
            quote=None,
            last_action_ns=observed_monotonic_ns,
        )

    def clear_exchange(self, exchange_id: str, *, observed_monotonic_ns: int) -> None:
        self._states[exchange_id] = MakerQuoteState(
            exchange_id=exchange_id,
            last_action_monotonic_ns=observed_monotonic_ns,
        )

    @staticmethod
    def _replace_side(
        current: MakerQuoteState,
        *,
        side: QuoteSide,
        quote: ActiveQuote | None,
        last_action_ns: int,
    ) -> MakerQuoteState:
        if side is QuoteSide.BID:
            return MakerQuoteState(
                exchange_id=current.exchange_id,
                bid=quote,
                ask=current.ask,
                last_action_monotonic_ns=last_action_ns,
            )
        return MakerQuoteState(
            exchange_id=current.exchange_id,
            bid=current.bid,
            ask=quote,
            last_action_monotonic_ns=last_action_ns,
        )
