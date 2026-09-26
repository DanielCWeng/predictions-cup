"""BUILD-008 orchestration helpers above accepted experiment implementations."""
# ruff: noqa: I001

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from predictions_cup.learning.research_spec import (
    AblationSpec,
    ExecutionStressSpec,
    NegativeControlKind as _NegativeControlKind,
    NegativeControlSpec,
    ResearchEvaluationSpec,
    RunIdentity,
    make_run_identity,
)
from predictions_cup.learning.validation import EvaluationObservation

NegativeControlKind = _NegativeControlKind
NegativeControl = NegativeControlSpec
AblationVariant = AblationSpec
ExecutionStress = ExecutionStressSpec


class ResearchEvaluationHarness:
    def __init__(self, spec: ResearchEvaluationSpec, code_revision: str) -> None:
        self.spec = spec
        self.identity: RunIdentity = make_run_identity(spec, code_revision)

    def validate_rows(
        self, rows: Iterable[EvaluationObservation]
    ) -> tuple[EvaluationObservation, ...]:
        materialized = tuple(rows)
        seen: set[str] = set()
        allowed_horizons = set(self.spec.target_horizons)
        market_universe = set(self.spec.market_universe)
        event_universe = set(self.spec.event_universe)
        family_universe = set(self.spec.event_family_universe)

        for row in materialized:
            if row.run_id != self.identity.run_id:
                raise ValueError("observation run_id does not match harness identity")
            if row.experiment_id != self.spec.experiment_id:
                raise ValueError("observation experiment_id does not match research spec")
            if row.hypothesis_family != self.spec.hypothesis_family:
                raise ValueError("observation hypothesis_family does not match research spec")
            if row.horizon not in allowed_horizons:
                raise ValueError("observation horizon is not declared by the research spec")
            if market_universe and row.market_id not in market_universe:
                raise ValueError("observation market_id is outside the declared market universe")
            if event_universe and row.event_id not in event_universe:
                raise ValueError("observation event_id is outside the declared event universe")
            if family_universe and row.event_family_id not in family_universe:
                raise ValueError(
                    "observation event_family_id is outside the declared family universe"
                )
            row.assert_asof_safe()
            observation_id = row.observation_id
            if observation_id in seen:
                raise ValueError(f"duplicate evaluation observation: {observation_id}")
            seen.add(observation_id)

        return tuple(
            sorted(
                materialized,
                key=lambda row: (
                    row.decision_time,
                    row.instrument_id,
                    row.horizon,
                    row.event_id or "",
                    row.observation_id,
                ),
            )
        )

    @staticmethod
    def apply_cost_stress(
        rows: Iterable[EvaluationObservation], stress: ExecutionStressSpec
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
