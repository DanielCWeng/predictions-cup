"""BUILD-008 orchestration helpers above accepted experiment implementations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from predictions_cup.learning.research_spec import ResearchEvaluationSpec, RunIdentity, make_run_identity
from predictions_cup.learning.validation import EvaluationObservation


@dataclass(frozen=True, slots=True)
class NamedVariant:
    name: str
    removed_components: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionStress:
    name: str
    extra_cost_per_share: Decimal | None = None
    execution_delay: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("stress name must be non-blank")
        if self.extra_cost_per_share is not None and self.extra_cost_per_share < 0:
            raise ValueError("extra execution cost must not be negative")
        if self.execution_delay is not None and self.execution_delay < timedelta(0):
            raise ValueError("execution delay must not be negative")


class ResearchEvaluationHarness:
    def __init__(self, spec: ResearchEvaluationSpec, code_revision: str) -> None:
        self.spec = spec
        self.identity: RunIdentity = make_run_identity(spec, code_revision)

    def validate_rows(
        self, rows: Iterable[EvaluationObservation]
    ) -> tuple[EvaluationObservation, ...]:
        materialized = tuple(rows)
        for row in materialized:
            if row.run_id != self.identity.run_id:
                raise ValueError("observation run_id does not match harness identity")
            if row.experiment_id != self.spec.experiment_id:
                raise ValueError("observation experiment_id does not match research spec")
            row.assert_asof_safe()
        return tuple(
            sorted(
                materialized,
                key=lambda row: (
                    row.decision_time,
                    row.instrument_id,
                    row.horizon,
                    row.event_id or "",
                ),
            )
        )

    @staticmethod
    def apply_cost_stress(
        rows: Iterable[EvaluationObservation], stress: ExecutionStress
    ) -> tuple[Decimal | None, ...]:
        if stress.execution_delay not in (None, timedelta(0)):
            raise ValueError(
                "latency stress requires replay re-evaluation at the delayed observable time"
            )
        results: list[Decimal | None] = []
        for row in rows:
            if row.gross_executable_markout is None or stress.extra_cost_per_share is None:
                results.append(None)
            else:
                results.append(row.gross_executable_markout - stress.extra_cost_per_share)
        return tuple(results)


def assert_variant_comparability(
    base_dataset_hash: str,
    base_fold_ids: tuple[str, ...],
    candidate_dataset_hash: str,
    candidate_fold_ids: tuple[str, ...],
) -> None:
    if base_dataset_hash != candidate_dataset_hash:
        raise ValueError("ablation/control cannot silently change dataset")
    if base_fold_ids != candidate_fold_ids:
        raise ValueError("ablation/control cannot silently change folds")
