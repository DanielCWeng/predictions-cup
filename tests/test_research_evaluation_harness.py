from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.learning.evaluation_harness import (
    ExecutionStress,
    ResearchEvaluationHarness,
    assert_variant_comparability,
)
from predictions_cup.learning.experiments import ExperimentObservation
from predictions_cup.learning.relationships import FeatureVector, Regime, RelationshipObservation
from predictions_cup.learning.reporting import (
    ResearchDisposition,
    ResearchLedgerEntry,
    ResearchReport,
)
from predictions_cup.learning.research_spec import (
    DatasetVersion,
    ResearchEvaluationSpec,
    SplitMethod,
    STANDARD_HORIZONS,
    make_run_identity,
)
from predictions_cup.learning.stability import ParameterCell, parameter_surface
from predictions_cup.learning.statistics import (
    HypothesisTest,
    benjamini_hochberg,
    event_bootstrap_mean,
    moving_block_bootstrap_mean,
)
from predictions_cup.learning.validation import (
    EvaluationObservation,
    FoldRole,
    adapt_experiment_observation,
    adapt_relationship_observation,
    chronological_group_holdout,
    chronological_split,
    leave_group_out_diagnostic,
    purge_training,
)
from predictions_cup.replay.markouts import Direction
from predictions_cup.replay.splits import ChronologicalBoundaries


BASE = datetime(2026, 1, 1, tzinfo=UTC)
DATA_HASH = "a" * 64


def _spec(**overrides: object) -> ResearchEvaluationSpec:
    values: dict[str, object] = {
        "experiment_id": "EXP-TEST",
        "hypothesis_family": "LEADLAG",
        "economic_mechanism": "external information arrival",
        "dataset": DatasetVersion("synthetic", "v1", DATA_HASH),
        "feature_set_id": "features-v1",
        "feature_availability_rule": "observable_at <= decision_time",
        "target": "future executable markout",
        "target_horizons": STANDARD_HORIZONS,
        "market_universe": ("m1",),
        "event_universe": ("e1", "e2"),
        "event_family_universe": ("f1",),
        "split_method": SplitMethod.WALK_FORWARD,
        "multiple_testing_family": "cross-venue",
    }
    values.update(overrides)
    return ResearchEvaluationSpec(**values)  # type: ignore[arg-type]


def _row(
    seconds: int,
    *,
    horizon: int = 5,
    event: str = "e1",
    family: str = "f1",
    run_id: str = "run",
    gross: str | None = "0.01",
    net: str | None = None,
    feature_offset: int = 0,
) -> EvaluationObservation:
    decision = BASE + timedelta(seconds=seconds)
    return EvaluationObservation(
        experiment_id="EXP-TEST",
        hypothesis_family="LEADLAG",
        run_id=run_id,
        decision_time=decision,
        feature_available_at=decision + timedelta(seconds=feature_offset),
        label_end_time=decision + timedelta(seconds=horizon),
        market_id="m1",
        instrument_id=f"i-{seconds}",
        event_id=event,
        event_family_id=family,
        horizon=timedelta(seconds=horizon),
        signal="BUY_YES",
        predictive_result=Decimal("0.02"),
        gross_executable_markout=None if gross is None else Decimal(gross),
        net_executable_markout=None if net is None else Decimal(net),
        valid=True,
        invalid_reason=None,
        underlying={"synthetic": True},
    )


def test_run_identity_is_deterministic_and_binds_material_inputs() -> None:
    spec = _spec()
    left = make_run_identity(spec, "abc123")
    right = make_run_identity(spec, "abc123")
    assert left == right
    assert left.run_id == right.run_id
    assert make_run_identity(spec, "def456").run_id != left.run_id
    changed = _spec(dataset=DatasetVersion("synthetic", "v1", "b" * 64))
    assert make_run_identity(changed, "abc123").run_id != left.run_id
    changed_cfg = _spec(feature_set_id="features-v2")
    assert make_run_identity(changed_cfg, "abc123").run_id != left.run_id


def test_asof_gate_rejects_future_feature() -> None:
    row = _row(0, feature_offset=1)
    with pytest.raises(ValueError, match="future feature rejected"):
        row.assert_asof_safe()


def test_chronological_split_and_purge_embargo_reconcile() -> None:
    rows = (_row(0, horizon=12), _row(5, horizon=2), _row(20), _row(40))
    boundaries = ChronologicalBoundaries(BASE + timedelta(seconds=10), BASE + timedelta(seconds=30))
    assignments = chronological_split(rows, boundaries)
    assert [item.role for item in assignments] == [
        FoldRole.TRAIN,
        FoldRole.TRAIN,
        FoldRole.DEVELOPMENT,
        FoldRole.HOLDOUT,
    ]
    kept, evidence = purge_training(
        assignments, BASE + timedelta(seconds=10), embargo=timedelta(seconds=6)
    )
    assert evidence.rows_before == 4
    assert evidence.rows_removed_by_purge == 1
    assert evidence.rows_removed_by_embargo == 1
    assert evidence.rows_remaining == 2
    assert all(item.role is not FoldRole.TRAIN for item in kept)


def test_chronological_group_holdout_never_trains_on_later_events() -> None:
    rows = (
        _row(0, event="old", family="old-family"),
        _row(10, event="held", family="held-family"),
        _row(20, event="future", family="future-family"),
    )
    event_fold = chronological_group_holdout(rows, holdout_id="held", level="event")
    assert [(item.observation.event_id, item.role) for item in event_fold] == [
        ("old", FoldRole.TRAIN),
        ("held", FoldRole.HOLDOUT),
    ]
    family_fold = chronological_group_holdout(rows, holdout_id="held-family", level="family")
    assert all(item.observation.event_family_id != "future-family" for item in family_fold)
    diagnostic = leave_group_out_diagnostic(rows, holdout_id="held", level="event")
    assert any(
        item.observation.event_id == "future" and item.role is FoldRole.TRAIN
        for item in diagnostic
    )


def test_bh_known_vector_ties_and_invalid_pvalues() -> None:
    tests = (
        HypothesisTest("h1", "f", Decimal("0.01")),
        HypothesisTest("h2", "f", Decimal("0.04")),
        HypothesisTest("h3", "f", Decimal("0.03")),
        HypothesisTest("h4", "f", Decimal("0.002")),
    )
    results = benjamini_hochberg(tests, Decimal("0.05"))
    rejected = {item.hypothesis_id for item in results if item.rejected}
    assert rejected == {"h1", "h3", "h4"}
    tied = benjamini_hochberg(
        (HypothesisTest("b", "f", Decimal("0.01")), HypothesisTest("a", "f", Decimal("0.01"))),
        Decimal("0.05"),
    )
    ranks = {item.hypothesis_id: item.rank for item in tied}
    assert ranks == {"a": 1, "b": 2}
    with pytest.raises(ValueError, match="p-values"):
        benjamini_hochberg((HypothesisTest("bad", "f", Decimal("1.1")),), Decimal("0.05"))


def test_bootstrap_is_deterministic_and_uses_declared_units() -> None:
    values = tuple(Decimal(i) for i in range(1, 11))
    left = moving_block_bootstrap_mean(
        values, block_size=3, draws=100, run_id="r", component_id="c"
    )
    right = moving_block_bootstrap_mean(
        values, block_size=3, draws=100, run_id="r", component_id="c"
    )
    assert left == right
    assert left.method == "moving_block"
    assert left.units == 8
    event = event_bootstrap_mean(
        {"e1": (Decimal("1"), Decimal("2")), "e2": (Decimal("4"),)},
        draws=100,
        run_id="r",
        component_id="event",
    )
    assert event.method == "event"
    assert event.units == 2


def test_parameter_surface_distinguishes_plateau_and_knife_edge() -> None:
    plateau = parameter_surface(
        (
            ParameterCell((0,), (("x", "0"),), Decimal("0.95")),
            ParameterCell((1,), (("x", "1"),), Decimal("1.00")),
            ParameterCell((2,), (("x", "2"),), Decimal("0.96")),
        ),
        tolerance=Decimal("0.10"),
    )
    assert not plateau.peak_is_isolated
    sharp = parameter_surface(
        (
            ParameterCell((0,), (("x", "0"),), Decimal("0.10")),
            ParameterCell((1,), (("x", "1"),), Decimal("1.00")),
            ParameterCell((2,), (("x", "2"),), Decimal("-0.10")),
        ),
        tolerance=Decimal("0.10"),
    )
    assert sharp.peak_is_isolated


def test_economics_remain_separate_and_latency_is_not_faked() -> None:
    row = _row(0, gross="0.03", net=None)
    assert row.predictive_result == Decimal("0.02")
    assert row.gross_executable_markout == Decimal("0.03")
    assert row.net_executable_markout is None
    stressed = ResearchEvaluationHarness.apply_cost_stress(
        (row,), ExecutionStress("fee+slippage", extra_cost_per_share=Decimal("0.01"))
    )
    assert stressed == (Decimal("0.02"),)
    with pytest.raises(ValueError, match="re-evaluation"):
        ResearchEvaluationHarness.apply_cost_stress(
            (row,), ExecutionStress("latency", execution_delay=timedelta(milliseconds=100))
        )


def test_controls_and_ablations_cannot_change_dataset_or_folds() -> None:
    assert_variant_comparability(DATA_HASH, ("f1", "f2"), DATA_HASH, ("f1", "f2"))
    with pytest.raises(ValueError, match="dataset"):
        assert_variant_comparability(DATA_HASH, ("f1",), "b" * 64, ("f1",))
    with pytest.raises(ValueError, match="folds"):
        assert_variant_comparability(DATA_HASH, ("f1",), DATA_HASH, ("f2",))


def test_standard_horizons_are_canonical_five() -> None:
    expected = (
        timedelta(seconds=1),
        timedelta(seconds=5),
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=5),
    )
    assert expected == STANDARD_HORIZONS


def test_build005_adapter_preserves_underlying_and_asof_semantics() -> None:
    row = ExperimentObservation(
        experiment_id="EXP-TEST",
        decision_at=BASE,
        instrument="sig-1",
        market_id="m1",
        features={"x": Decimal("1")},
        signal="BUY_YES",
        direction=Direction.BUY_YES,
        entry_executable_price=Decimal("0.5"),
        target_horizon=timedelta(seconds=5),
        future_executable_price=Decimal("0.55"),
        gross_markout=Decimal("0.05"),
        midpoint_markout=Decimal("0.06"),
        estimated_cost=None,
        net_markout=None,
        valid=True,
        invalid_reason=None,
    )
    adapted = adapt_experiment_observation(row, hypothesis_family="GENERIC", run_id="r")
    assert adapted.underlying is row
    assert adapted.gross_executable_markout == Decimal("0.05")
    assert adapted.net_executable_markout is None
    with pytest.raises(ValueError, match="future feature"):
        adapt_experiment_observation(
            row,
            hypothesis_family="GENERIC",
            run_id="r",
            feature_available_at=BASE + timedelta(microseconds=1),
        )


def test_experiment002_adapter_preserves_relationship_observation() -> None:
    features = FeatureVector(
        price_movement=Decimal("0.1"),
        logit_movement=None,
        spread=Decimal("0.02"),
        depth=None,
        quote_age_seconds=Decimal("0"),
        reference_residual_probability=None,
        reference_residual_logit=None,
        number_active_references=1,
        logit_clamp_epsilon=Decimal("0.000001"),
    )
    row = RelationshipObservation(
        experiment_id="EXP-TEST",
        dataset_id="synthetic",
        target="SIG:sig-1",
        reference_set=("POLYMARKET:poly-1",),
        excluded_instruments=(),
        decision_time=BASE,
        regime=Regime.NORMAL,
        signal_type="LEADLAG",
        signal_direction=Direction.BUY_YES,
        signal_value=Decimal("0.1"),
        threshold=Decimal("0.05"),
        features=features,
        target_bid=Decimal("0.49"),
        target_ask=Decimal("0.51"),
        entry_price=Decimal("0.51"),
        horizon=timedelta(seconds=5),
        future_bid=Decimal("0.55"),
        future_ask=Decimal("0.57"),
        gross_markout=Decimal("0.04"),
        net_markout=None,
        target_mid_change=Decimal("0.06"),
        target_logit_change=None,
        valid=True,
        invalid_reason=None,
    )
    adapted = adapt_relationship_observation(
        row, hypothesis_family="LEADLAG", run_id="r", event_id="e1", event_family_id="f1"
    )
    assert adapted.underlying is row
    assert adapted.signal == "BUY_YES"
    assert adapted.net_executable_markout is None


def test_harness_validates_identity_and_report_is_byte_deterministic() -> None:
    spec = _spec()
    harness = ResearchEvaluationHarness(spec, "abc123")
    row = _row(0, run_id=harness.identity.run_id)
    assert harness.validate_rows((row,)) == (row,)
    report = ResearchReport(
        schema_version="1",
        run_id=harness.identity.run_id,
        experiment_id=spec.experiment_id,
        hypothesis_family=spec.hypothesis_family,
        economic_mechanism=spec.economic_mechanism,
        code_revision="abc123",
        dataset_id=spec.dataset.dataset_id,
        dataset_version=spec.dataset.schema_version,
        dataset_hash=spec.dataset.manifest_sha256,
        config_hash=harness.identity.config_hash,
        universe={"markets": ("m1",), "events": ("e1",), "families": ("f1",)},
        folds=({"id": "fold-1", "role": "HOLDOUT"},),
        purge_embargo={"purged": 0, "embargoed": 0},
        sample_counts={"observations": 1, "markets": 1, "events": 1, "families": 1},
        horizon_results=({"horizon": "5", "valid": 1},),
        predictive_metrics={"mean": "0.02"},
        gross_executable_metrics={"mean": "0.03"},
        net_executable_metrics=None,
        bootstrap_results=(),
        raw_statistical_tests=(),
        fdr_results=(),
        parameter_stability=None,
        negative_controls=(),
        ablations=(),
        execution_stresses=(),
        invalidity_counts={},
        known_limitations=("synthetic fixture only",),
        disposition=ResearchDisposition.INCONCLUSIVE,
    )
    assert report.serialize() == report.serialize()
    assert b'"net_executable_metrics":null' in report.serialize()


@pytest.mark.parametrize(
    "disposition",
    [
        ResearchDisposition.PROMOTED,
        ResearchDisposition.REJECTED,
        ResearchDisposition.INCONCLUSIVE,
    ],
)
def test_all_research_dispositions_are_preserved(disposition: ResearchDisposition) -> None:
    entry = ResearchLedgerEntry(
        experiment_id="EXP",
        hypothesis_family="FAM",
        run_id="run",
        data_version="synthetic-v1",
        code_revision="abc",
        disposition=disposition,
        headline_evidence="fixture",
        report_path="reports/fixture.json",
    )
    assert disposition.value in entry.markdown_row()
