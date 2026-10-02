from types import SimpleNamespace
from typing import cast

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import LifecycleState, OperationKind
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.service import _risk_uncertain_operation_ids


def test_cancel_in_flight_does_not_block_capital_reconciliation() -> None:
    envelopes = tuple(
        SimpleNamespace(
            logical_operation_id=state.value,
            lifecycle_state=state,
            operation_kind=OperationKind.SINGLE_PLACEMENT,
        )
        for state in (
            LifecycleState.PENDING,
            LifecycleState.CANCEL_PENDING,
            LifecycleState.UNCERTAIN,
            LifecycleState.RECONCILING,
            LifecycleState.ACKED,
        )
    )
    journal = cast(ExecutionJournal, SimpleNamespace(unresolved=lambda: envelopes))

    assert set(_risk_uncertain_operation_ids(journal)) == {
        LifecycleState.PENDING.value,
        LifecycleState.UNCERTAIN.value,
        LifecycleState.RECONCILING.value,
    }
    assert _risk_uncertain_operation_ids(None) == ()


def test_reserved_in_flight_placement_does_not_block_but_orphan_does() -> None:
    in_flight = SimpleNamespace(
        logical_operation_id="in-flight",
        lifecycle_state=LifecycleState.PENDING,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intent_ids=("i1",),
    )
    orphan = SimpleNamespace(
        logical_operation_id="orphan",
        lifecycle_state=LifecycleState.PENDING,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intent_ids=("i2",),
    )
    timed_out = SimpleNamespace(
        logical_operation_id="timed-out",
        lifecycle_state=LifecycleState.UNCERTAIN,
        operation_kind=OperationKind.SINGLE_PLACEMENT,
        intent_ids=("i3",),
    )
    journal = cast(
        ExecutionJournal,
        SimpleNamespace(
            unresolved=lambda: (in_flight, orphan, timed_out),
            confirmed_cancelled_order_ids=lambda: frozenset(),
            events=lambda _operation_id: (),
        ),
    )
    reserved = {("in-flight", ("i1",)), ("timed-out", ("i3",))}
    reservations = cast(
        ExecutionReservationBook,
        SimpleNamespace(
            contains_operation=lambda op, intents: (op, tuple(intents)) in reserved,
            contains_operation_intents=lambda op, intents: (
                op,
                tuple(intents),
            )
            in reserved
        ),
    )

    assert set(_risk_uncertain_operation_ids(journal, reservations)) == {
        "orphan",
    }
    assert set(_risk_uncertain_operation_ids(journal)) == {
        "in-flight",
        "orphan",
        "timed-out",
    }
