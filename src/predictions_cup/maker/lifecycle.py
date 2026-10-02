"""Quote lifecycle state machine for low-churn, fail-closed maker management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.execution.models import LifecycleState
from predictions_cup.maker.contracts import DesiredQuote, QuoteLevel, QuoteSide


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
    slot: str = ""

    def __post_init__(self) -> None:
        if not self.slot:
            object.__setattr__(self, "slot", f"{self.side.value}:{self.price_ticks}")

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
    additional_quotes: tuple[ActiveQuote, ...] = ()

    def all_quotes(self) -> tuple[ActiveQuote, ...]:
        return tuple(
            quote for quote in (self.bid, self.ask, *self.additional_quotes) if quote is not None
        )


@dataclass(frozen=True, slots=True)
class QuoteLifecycleAction:
    kind: QuoteLifecycleActionKind
    side: QuoteSide
    reason: str
    desired_ticks: int | None = None
    desired_size: int = 0
    active: ActiveQuote | None = None
    slot: str = ""


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

        actions: list[QuoteLifecycleAction] = []
        for side in (QuoteSide.BID, QuoteSide.ASK):
            active = [quote for quote in current.all_quotes() if quote.side is side]
            active.sort(
                key=lambda quote: quote.price_ticks,
                reverse=side is QuoteSide.BID,
            )
            levels = () if desired is None else desired.levels(side)
            remaining_active = list(active)
            remaining_levels = list(levels)

            # Preserve exact-price orders first. Remaining active and desired
            # levels pair by book priority for cancel-before-replace.
            exact: list[tuple[ActiveQuote, QuoteLevel]] = []
            for level in tuple(remaining_levels):
                slot = self._slot(side, level.price_ticks)
                quote = next((item for item in remaining_active if item.slot == slot), None)
                if quote is None:
                    continue
                exact.append((quote, level))
                remaining_active.remove(quote)
                remaining_levels.remove(level)

            side_actions: list[QuoteLifecycleAction] = []
            for quote, level in exact:
                side_actions.append(
                    self._side_action(
                        side=side,
                        slot=quote.slot,
                        desired_ticks=level.price_ticks,
                        desired_size=level.size,
                        active=quote,
                        current=current,
                        now_ns=now_monotonic_ns,
                        force_cancel=force_cancel,
                    )
                )

            replacement_blocker = False
            for quote, level in zip(remaining_active, remaining_levels, strict=False):
                # Terminal records have no live exposure; place into the desired
                # slot directly. Resting/unresolved records retain their old key
                # until cancel or reconciliation completes.
                slot = (
                    self._slot(side, level.price_ticks)
                    if not quote.resting and not quote.unresolved
                    else quote.slot
                )
                replacement = self._side_action(
                    side=side,
                    slot=slot,
                    desired_ticks=level.price_ticks,
                    desired_size=level.size,
                    active=quote,
                    current=current,
                    now_ns=now_monotonic_ns,
                    force_cancel=force_cancel,
                )
                side_actions.append(replacement)
                if (
                    quote.slot != self._slot(side, level.price_ticks)
                    and replacement.kind is not QuoteLifecycleActionKind.PLACE
                ):
                    replacement_blocker = True
            unmatched_active = remaining_active[len(remaining_levels) :]
            for quote in unmatched_active:
                side_actions.append(
                    self._side_action(
                        side=side,
                        slot=quote.slot,
                        desired_ticks=None,
                        desired_size=0,
                        active=quote,
                        current=current,
                        now_ns=now_monotonic_ns,
                        force_cancel=force_cancel,
                    )
                )

            blocked = (
                any(
                    item.kind
                    in {
                        QuoteLifecycleActionKind.CANCEL,
                        QuoteLifecycleActionKind.WAIT_RECONCILIATION,
                    }
                    for item in side_actions
                )
                or replacement_blocker
            )
            paired_count = min(len(remaining_active), len(remaining_levels))
            if not blocked:
                for level in remaining_levels[paired_count:]:
                    side_actions.append(
                        self._side_action(
                            side=side,
                            slot=self._slot(side, level.price_ticks),
                            desired_ticks=level.price_ticks,
                            desired_size=level.size,
                            active=None,
                            current=current,
                            now_ns=now_monotonic_ns,
                            force_cancel=force_cancel,
                        )
                    )
            if not side_actions:
                side_actions.append(
                    QuoteLifecycleAction(
                        QuoteLifecycleActionKind.KEEP,
                        side,
                        "no_quote_required",
                    )
                )
            actions.extend(side_actions)
        return tuple(actions)

    def _side_action(
        self,
        *,
        side: QuoteSide,
        slot: str,
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
                    slot=slot,
                )
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.PLACE,
                side,
                "initial_or_refill",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                slot=slot,
            )

        if active.unresolved or active.exchange_order_id is None:
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.WAIT_RECONCILIATION,
                side,
                "existing_quote_unresolved",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                active=active,
                slot=slot,
            )

        if not active.resting:
            if wants_quote:
                return QuoteLifecycleAction(
                    QuoteLifecycleActionKind.PLACE,
                    side,
                    "terminal_quote_refill",
                    desired_ticks=desired_ticks,
                    desired_size=desired_size,
                    slot=slot,
                )
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.KEEP,
                side,
                "terminal_no_quote",
                slot=slot,
            )

        if force_cancel or not wants_quote:
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.CANCEL,
                side,
                "forced_cancel" if force_cancel else "quote_no_longer_desired",
                active=active,
                slot=slot,
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
                slot=slot,
            )

        if (
            self._config.min_requote_interval_ns > 0
            and now_ns - current.last_action_monotonic_ns < self._config.min_requote_interval_ns
        ):
            return QuoteLifecycleAction(
                QuoteLifecycleActionKind.KEEP,
                side,
                "requote_interval_suppression",
                desired_ticks=desired_ticks,
                desired_size=desired_size,
                active=active,
                slot=slot,
            )

        return QuoteLifecycleAction(
            QuoteLifecycleActionKind.CANCEL,
            side,
            "cancel_for_material_replace",
            desired_ticks=desired_ticks,
            desired_size=desired_size,
            active=active,
            slot=slot,
        )

    @staticmethod
    def _slot(side: QuoteSide, price_ticks: int) -> str:
        return f"{side.value}:{price_ticks}"


class QuoteRegistry:
    """Small mutable I/O-shell registry; immutable records cross the hot path."""

    def __init__(self) -> None:
        self._states: dict[str, MakerQuoteState] = {}

    @property
    def exchange_ids(self) -> frozenset[str]:
        return frozenset(self._states)

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
        slot: str | None = None,
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
            slot=slot or f"{side.value}:{price_ticks}",
        )
        current = self.state(exchange_id)
        self._states[exchange_id] = self._replace_quote(
            current,
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
        slot: str | None = None,
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
            slot=slot or f"{side.value}:{price_ticks}",
        )
        current = self.state(exchange_id)
        self._states[exchange_id] = self._replace_quote(
            current,
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
        slot: str | None = None,
    ) -> None:
        current = self.state(exchange_id)
        active = next(
            (
                quote
                for quote in current.all_quotes()
                if quote.side is side and (slot is None or quote.slot == slot)
            ),
            None,
        )
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
            slot=active.slot,
        )
        self._states[exchange_id] = self._replace_quote(
            current,
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

    def clear_quote(
        self,
        *,
        exchange_id: str,
        slot: str,
        observed_monotonic_ns: int,
    ) -> None:
        current = self.state(exchange_id)
        retained = tuple(quote for quote in current.all_quotes() if quote.slot != slot)
        self._states[exchange_id] = self._make_state(
            exchange_id,
            retained,
            observed_monotonic_ns,
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
        retained = tuple(item for item in current.all_quotes() if item.side is not side)
        if quote is not None:
            retained = (*retained, quote)
        return QuoteRegistry._make_state(
            current.exchange_id,
            retained,
            last_action_ns,
        )

    @staticmethod
    def _replace_quote(
        current: MakerQuoteState,
        *,
        quote: ActiveQuote,
        last_action_ns: int,
    ) -> MakerQuoteState:
        retained = tuple(item for item in current.all_quotes() if item.slot != quote.slot)
        return QuoteRegistry._make_state(
            current.exchange_id,
            (*retained, quote),
            last_action_ns,
        )

    @staticmethod
    def _make_state(
        exchange_id: str,
        quotes: tuple[ActiveQuote, ...],
        last_action_ns: int,
    ) -> MakerQuoteState:
        bids = sorted(
            (quote for quote in quotes if quote.side is QuoteSide.BID),
            key=lambda quote: quote.price_ticks,
            reverse=True,
        )
        asks = sorted(
            (quote for quote in quotes if quote.side is QuoteSide.ASK),
            key=lambda quote: quote.price_ticks,
        )
        primary_bid = bids.pop(0) if bids else None
        primary_ask = asks.pop(0) if asks else None
        return MakerQuoteState(
            exchange_id=exchange_id,
            bid=primary_bid,
            ask=primary_ask,
            additional_quotes=tuple((*bids, *asks)),
            last_action_monotonic_ns=last_action_ns,
        )
