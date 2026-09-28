"""Synchronous local execution reservations used to close exchange-reporting gaps."""

from __future__ import annotations

from dataclasses import dataclass

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


class ExecutionReservationBook:
    """Conservative in-memory reservations for approved but not yet reconciled intents.

    Reservations are created synchronously before dispatch. They remain risk-bearing
    until an authoritative account snapshot is applied. This deliberately
    over-reserves during the short interval where SIG Realtime may already know
    about an order but REST reconciliation has not yet restored a canonical state.
    """

    def __init__(self) -> None:
        self._by_intent: dict[str, ExecutionReservation] = {}

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

    def contains_operation(
        self,
        logical_operation_id: str,
        intent_ids: tuple[str, ...],
    ) -> bool:
        if not intent_ids:
            return False
        return all(
            (
                reservation := self._by_intent.get(intent_id)
            ) is not None
            and reservation.logical_operation_id == logical_operation_id
            for intent_id in intent_ids
        )

    def intent_ids(self) -> frozenset[str]:
        return frozenset(self._by_intent)

    def reserved_orders(self) -> tuple[RuntimeOrderState, ...]:
        return tuple(item.order for item in self._by_intent.values())

    def clear_after_authoritative_reconciliation(self) -> None:
        self._by_intent.clear()

    def overlay_snapshot(self, snapshot: RuntimeSnapshot) -> RuntimeSnapshot:
        existing_ids = {
            order.logical_intent_id
            for order in snapshot.portfolio.orders
        }
        reservations = tuple(
            order
            for order in self.reserved_orders()
            if order.logical_intent_id not in existing_ids
        )
        if not reservations:
            return snapshot
        portfolio = RuntimePortfolio(
            positions=snapshot.portfolio.positions,
            orders=snapshot.portfolio.orders + reservations,
            account_trusted=snapshot.portfolio.account_trusted,
        )
        return RuntimeSnapshot(
            markets=snapshot.markets,
            books=snapshot.books,
            portfolio=portfolio,
            observation_monotonic_ns=snapshot.observation_monotonic_ns,
        )
