"""BUILD-008 orchestration helpers above accepted experiment implementations."""
# ruff: noqa: I001

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
from decimal import Decimal

from predictions_cup.learning.reporting import ResearchReport
from predictions_cup.learning.research_spec import (
    AblationSpec,
    EventBootstrapWeighting,
    ExecutionStressSpec,
    FDRProtocol,
    NegativeControlKind as _NegativeControlKind,
    NegativeControlSpec,
    ResearchEvaluationSpec,
    RunIdentity,
    make_run_identity,
)
from predictions_cup.learning.stability import (
    ParameterCell,
    ParameterSurfaceReport,
    parameter_surface,
)
from predictions_cup.learning.statistics import (
    BootstrapResult,
    FDRResult,
    HypothesisTest,
    benjamini_hochberg,
    event_bootstrap_mean,
    moving_block_bootstrap_mean,
)
from predictions_cup.learning.validation import (
    EvaluationObservation,
    WalkForwardConfig,
    WalkForwardFold,
    walk_forward_folds,
)

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

    def walk_forward(
        self,
        rows: Iterable[EvaluationObservation],
        *,
        config: WalkForwardConfig | None = None,
    ) -> tuple[WalkForwardFold, ...]:
        protocol = self.spec.walk_forward
        if protocol is None:
            raise ValueError("research spec does not declare a walk-forward protocol")
        expected = WalkForwardConfig(
            training_window=protocol.training_window,
            development_window=protocol.development_window,
            holdout_window=protocol.holdout_window,
            step=protocol.step,
            expanding_training=protocol.expanding_training,
            embargo=self.spec.embargo,
        )
        if config is not None and config != expected:
            raise ValueError("runtime walk-forward config does not match canonical research spec")
        return walk_forward_folds(self.validate_rows(rows), expected)

    def apply_fdr(
        self,
        tests: Iterable[HypothesisTest],
        *,
        protocol: FDRProtocol | None = None,
    ) -> tuple[FDRResult, ...]:
        expected = self.spec.fdr
        if protocol is not None and protocol != expected:
            raise ValueError("runtime FDR protocol does not match canonical research spec")
        return benjamini_hochberg(tests, expected)

    def moving_block_bootstrap(
        self,
        values: Sequence[Decimal],
        *,
        component_id: str,
        draws: int | None = None,
        block_size: int | None = None,
        seed: int | None = None,
    ) -> BootstrapResult:
        expected = self.spec.bootstrap
        if draws is not None and draws != expected.draws:
            raise ValueError("runtime bootstrap draws do not match canonical research spec")
        if block_size is not None and block_size != expected.block_size:
            raise ValueError("runtime bootstrap block size does not match canonical research spec")
        return moving_block_bootstrap_mean(
            values,
            block_size=expected.block_size,
            draws=expected.draws,
            run_id=self.identity.run_id,
            component_id=component_id,
            seed=seed,
        )

    def event_bootstrap(
        self,
        event_values: Mapping[str, Sequence[Decimal]],
        *,
        component_id: str,
        draws: int | None = None,
        weighting: EventBootstrapWeighting | None = None,
        seed: int | None = None,
    ) -> BootstrapResult:
        expected = self.spec.bootstrap
        if draws is not None and draws != expected.draws:
            raise ValueError("runtime bootstrap draws do not match canonical research spec")
        if weighting is not None and weighting is not expected.event_weighting:
            raise ValueError("runtime event weighting does not match canonical research spec")
        return event_bootstrap_mean(
            event_values,
            draws=expected.draws,
            run_id=self.identity.run_id,
            component_id=component_id,
            weighting=expected.event_weighting,
            seed=seed,
        )

    def parameter_surface(
        self,
        cells: tuple[ParameterCell, ...],
        *,
        tolerance: Decimal | None = None,
    ) -> ParameterSurfaceReport:
        expected = self.spec.stability.tolerance
        if tolerance is not None and tolerance != expected:
            raise ValueError("runtime stability tolerance does not match canonical research spec")
        return parameter_surface(cells, tolerance=expected)

    def validate_negative_control(self, control: NegativeControlSpec) -> None:
        if control not in self.spec.negative_controls:
            raise ValueError("runtime negative control is not declared by the research spec")

    def validate_ablation(self, ablation: AblationSpec) -> None:
        if ablation not in self.spec.ablations:
            raise ValueError("runtime ablation is not declared by the research spec")

    def apply_cost_stress(
        self,
        rows: Iterable[EvaluationObservation],
        stress: ExecutionStressSpec,
    ) -> tuple[Decimal | None, ...]:
        if stress not in self.spec.execution_stresses:
            raise ValueError("runtime execution stress is not declared by the research spec")
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

    def validate_report(self, report: ResearchReport) -> ResearchReport:
        if report.run_id != self.identity.run_id:
            raise ValueError("report run_id does not match harness identity")
        if report.config_hash != self.identity.config_hash:
            raise ValueError("report config_hash does not match harness identity")
        if report.code_revision != self.identity.code_revision:
            raise ValueError("report code_revision does not match harness identity")
        if report.dataset_hash != self.identity.dataset_hash:
            raise ValueError("report dataset_hash does not match harness identity")
        if report.experiment_id != self.spec.experiment_id:
            raise ValueError("report experiment_id does not match research spec")
        if report.hypothesis_family != self.spec.hypothesis_family:
            raise ValueError("report hypothesis_family does not match research spec")
        if report.evidence_policy != self.spec.disposition_policy:
            raise ValueError("report evidence policy does not match canonical research spec")
        return report


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
