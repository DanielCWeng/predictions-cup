from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import ExecutionEnvelope, LifecycleState


class _Journal:
    def __init__(self, current: LifecycleState) -> None:
        self.current = current

    def mark_state(self, _op: str, state: LifecycleState, _ns: int) -> None:
        if self.current is LifecycleState.CANCELLED:
            raise ValueError(
                f"invalid lifecycle transition {self.current.value} -> {state.value}"
            )
        self.current = state


def _call(journal: _Journal) -> None:
    sink = cast(Any, object.__new__(SigLiveSink))
    sink._journal = journal
    envelope = cast(ExecutionEnvelope, SimpleNamespace(logical_operation_id="op-1"))
    SigLiveSink._mark_uncertain_unless_resolved(sink, envelope, 1)


def test_timed_out_cancel_keeps_concurrently_resolved_cancelled_state() -> None:
    # LIVE 2026-10-01 19:37Z crashed MAKE: CANCELLED -> UNCERTAIN.
    journal = _Journal(LifecycleState.CANCELLED)
    _call(journal)
    assert journal.current is LifecycleState.CANCELLED


def test_unresolved_operation_still_becomes_uncertain() -> None:
    journal = _Journal(LifecycleState.CANCEL_PENDING)
    _call(journal)
    assert journal.current is LifecycleState.UNCERTAIN
