"""Synchronous local execution reservations used to close exchange-reporting gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from predictions_cup.execution.models import RuntimeOrderIntent
from predictions_cup.runtime.models import (
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimeSnapshot,
)


@dataclass(frozen=True, slots=True)
class ExecutionReservation:
    logical_operation_id: str
    order: RuntimeOrderState
    exchange_order_id: str | None = None
    acknowledged_at: datetime | None = None


class ExecutionReservationBook:
    """Conservative in-memory reservations for approved but not yet reconciled intents.

    Reservations are created synchronously before dispatch. They remain risk-bearing
    until an authoritative account snapshot is applied. This deliberately
    over-reserves during the short interval where SIG Realtime may already know
    about an order but REST reconciliation has not yet restored a canonical state.
    """

    def __init__(self) -> None:
        self._by_intent: dict[str, ExecutionReservation] = {}
        self._intent_by_exchange_order: dict[str, str] = {}

    def reserve(
        self,
        logical_operation_id: str,
        intents: tuple[RuntimeOrderIntent, ...],
    ) -> None:
        if not logical_operation_id.strip():
            raise ValueError("logical_operation_id must not be blank")
        pending: list[tuple[str, ExecutionReservation]] = []
        for intent in intents:
            existing = self._by_intent.get(intent.intent_id)
            candidate = ExecutionReservation(
                logical_operation_id=logical_operation_id,
                order=RuntimeOrderState(
                    logical_intent_id=intent.intent_id,
                    exchange_id=intent.exchange_id,
                    market_id=intent.market_id,
                    tournament_id=intent.tournament_id,
                    reserved_exposure=float(intent.quantity),
                    open=False,
                    uncertain=True,
                    strategy_id=intent.strategy_id,
                    signed_quantity=(
                        (1.0 if intent.outcome_side.value == "yes" else -1.0)
                        * (1.0 if intent.action.value == "buy" else -1.0)
                        * float(intent.quantity)
                    ),
                ),
            )
            if existing is not None:
                if existing != candidate:
                    raise ValueError(
                        f"conflicting reservation for logical intent {intent.intent_id}"
                    )
                continue
            pending.append((intent.intent_id, candidate))

        for intent_id, reservation in pending:
            self._by_intent[intent_id] = reservation

    def bind_exchange_order(
        self,
        intent_id: str,
        exchange_order_id: str,
        *,
        acknowledged_at: datetime,
    ) -> None:
        if acknowledged_at.tzinfo is None or acknowledged_at.utcoffset() is None:
            raise ValueError("acknowledged_at must be timezone-aware")
        if not exchange_order_id.strip():
            raise ValueError("exchange_order_id must not be blank")
        reservation = self._by_intent.get(intent_id)
        if reservation is None:
            raise KeyError(f"unknown logical intent reservation: {intent_id}")
        prior_intent = self._intent_by_exchange_order.get(exchange_order_id)
        if prior_intent is not None and prior_intent != intent_id:
            raise ValueError("exchange order identity cannot bind to multiple intents")
        if (
            reservation.exchange_order_id is not None
            and reservation.exchange_order_id != exchange_order_id
        ):
            raise ValueError("logical intent cannot bind to multiple exchange orders")
        self._by_intent[intent_id] = ExecutionReservation(
            logical_operation_id=reservation.logical_operation_id,
            order=reservation.order,
            exchange_order_id=exchange_order_id,
            acknowledged_at=acknowledged_at,
        )
        self._intent_by_exchange_order[exchange_order_id] = intent_id

    def reservation_for_exchange_order_id(
        self,
        exchange_order_id: str,
    ) -> ExecutionReservation | None:
        intent_id = self._intent_by_exchange_order.get(exchange_order_id)
        if intent_id is None:
            return None
        return self._by_intent.get(intent_id)

    def reconcile_authoritative(self, *, observed_at: datetime) -> int:
        """Drop only reservations definitely covered by a non-stale snapshot."""
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("authoritative observed_at must be timezone-aware")
        releasable = tuple(
            intent_id
            for intent_id, reservation in self._by_intent.items()
            if reservation.exchange_order_id is not None
            and reservation.acknowledged_at is not None
            and reservation.acknowledged_at <= observed_at
        )
        for intent_id in releasable:
            reservation = self._by_intent.pop(intent_id)
            assert reservation.exchange_order_id is not None
            self._intent_by_exchange_order.pop(reservation.exchange_order_id, None)
        return len(releasable)

    def contains_operation(
        self,
        logical_operation_id: str,
        intent_ids: tuple[str, ...],
    ) -> bool:
        if not intent_ids:
            return False
        return all(
            (reservation := self._by_intent.get(intent_id)) is not None
            and reservation.logical_operation_id == logical_operation_id
            for intent_id in intent_ids
        )

    def release_operation(self, logical_operation_id: str) -> int:
        """Release only reservations owned by one conclusively dead operation."""
        if not logical_operation_id.strip():
            raise ValueError("logical_operation_id must not be blank")
        intent_ids = tuple(
            intent_id
            for intent_id, reservation in self._by_intent.items()
            if reservation.logical_operation_id == logical_operation_id
        )
        for intent_id in intent_ids:
            reservation = self._by_intent.pop(intent_id)
            if reservation.exchange_order_id is not None:
                self._intent_by_exchange_order.pop(
                    reservation.exchange_order_id,
                    None,
                )
        return len(intent_ids)

    def intent_ids(self) -> frozenset[str]:
        return frozenset(self._by_intent)

    def reserved_orders(self) -> tuple[RuntimeOrderState, ...]:
        return tuple(item.order for item in self._by_intent.values())

    def clear_after_authoritative_reconciliation(self) -> None:
        self._by_intent.clear()
        self._intent_by_exchange_order.clear()

    def overlay_snapshot(self, snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
        existing_ids = {order.logical_intent_id for order in snapshot.portfolio.orders}
        reservations = tuple(
            order for order in self.reserved_orders() if order.logical_intent_id not in existing_ids
        )
        if not reservations:
            return snapshot
        portfolio = RuntimePortfolio(
            positions=snapshot.portfolio.positions,
            orders=snapshot.portfolio.orders + reservations,
            account_trusted=snapshot.portfolio.account_trusted,
            account_trust_grade=snapshot.portfolio.account_trust_grade,
            account_proxy_age_ns=snapshot.portfolio.account_proxy_age_ns,
            account_proxy_uncertainty=snapshot.portfolio.account_proxy_uncertainty,
            account_proxy_cash_balance=snapshot.portfolio.account_proxy_cash_balance,
        )
        return RuntimeSnapshot(
            markets=snapshot.markets,
            books=snapshot.books,
            portfolio=portfolio,
            observation_monotonic_ns=snapshot.observation_monotonic_ns,
        )
