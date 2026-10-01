from types import SimpleNamespace
from typing import cast

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import LifecycleState
from predictions_cup.maker.service import _risk_uncertain_operation_ids


def test_cancel_in_flight_does_not_block_capital_reconciliation() -> None:
    envelopes = tuple(
        SimpleNamespace(logical_operation_id=state.value, lifecycle_state=state)
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
