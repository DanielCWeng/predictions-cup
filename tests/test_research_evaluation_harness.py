# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from predictions_cup.learning.evaluation_harness import (
    AblationVariant,
    ExecutionStress,
    NegativeControl,
    NegativeControlKind,
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
    BootstrapProtocol,
    DatasetVersion,
    EventBootstrapWeighting,
    EvidencePolicy,
    FDRProtocol,
    ResearchEvaluationSpec,
    STANDARD_HORIZONS,
    SplitMethod,
    StabilityProtocol,
    WalkForwardProtocol,
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
    WalkForwardConfig,
    chronological_group_holdout,
    chronological_group_holdout_result,
    chronological_split,
    leave_group_out_diagnostic,
    purge_development,
    purge_training,
    walk_forward_folds,
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
        "walk_forward": WalkForwardProtocol(
            training_window=timedelta(seconds=10),
            development_window=timedelta(seconds=5),
            holdout_window=timedelta(seconds=5),
            step=timedelta(seconds=5),
        ),
        "fdr": FDRProtocol(
            family_id="cross-venue",
            alpha=Decimal("0.05"),
            hypothesis_ids=("h1", "h2", "h3", "h4"),
        ),
        "bootstrap": BootstrapProtocol(
            method="moving_block",
            draws=100,
            block_size=3,
            event_weighting=EventBootstrapWeighting.OBSERVATION_WEIGHTED_CLUSTER,
        ),
        "stability": StabilityProtocol(tolerance=Decimal("0.10")),
        "disposition_policy": EvidencePolicy(),
        "negative_controls": (
            NegativeControl("zero", NegativeControlKind.ZERO_SIGNAL),
        ),
        "ablations": (AblationVariant("minus-flow", ("flow",)),),
        "execution_stresses": (
            ExecutionStress("fee", extra_cost_per_share=Decimal("0.01")),
        ),
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
    assert make_run_identity(spec, "def456").run_id != left.run_id

    changed_specs = (
        _spec(dataset=DatasetVersion("synthetic", "v1", "b" * 64)),
        _spec(feature_set_id="features-v2"),
        _spec(
            walk_forward=WalkForwardProtocol(
                training_window=timedelta(seconds=10),
                development_window=timedelta(seconds=5),
                holdout_window=timedelta(seconds=5),
                step=timedelta(seconds=10),
            )
        ),
        _spec(
            fdr=FDRProtocol(
                family_id="cross-venue",
                alpha=Decimal("0.10"),
                hypothesis_ids=("h1", "h2", "h3", "h4"),
            )
        ),
        _spec(stability=StabilityProtocol(tolerance=Decimal("0.20"))),
        _spec(
            negative_controls=(
                NegativeControl(
                    "delayed",
                    NegativeControlKind.DELAYED_PAST_ONLY,
                    delay=timedelta(seconds=2),
                ),
            )
        ),
        _spec(ablations=(AblationVariant("minus-book", ("book",)),)),
        _spec(
            execution_stresses=(
                ExecutionStress("fee", extra_cost_per_share=Decimal("0.02")),
            )
        ),
        _spec(
            disposition_policy=replace(
                EvidencePolicy(),
                min_fdr_rejections=2,
            )
        ),
    )
    for changed in changed_specs:
        assert make_run_identity(changed, "abc123").run_id != left.run_id


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


def test_chronological_group_holdout_purges_overlap_and_never_trains_on_future() -> None:
    rows = (
        _row(0, horizon=12, event="overlap", family="old-family"),
        _row(5, horizon=2, event="embargoed", family="old-family"),
        _row(10, event="held", family="held-family"),
        _row(20, event="future", family="future-family"),
    )
    result = chronological_group_holdout_result(
        rows,
        holdout_id="held",
        level="event",
        embargo=timedelta(seconds=6),
    )
    assert [(item.observation.event_id, item.role) for item in result.assignments] == [
        ("held", FoldRole.HOLDOUT),
    ]
    assert result.purge_evidence.rows_removed_by_purge == 1
    assert result.purge_evidence.rows_removed_by_embargo == 1

    family_fold = chronological_group_holdout(
        rows,
        holdout_id="held-family",
        level="family",
    )
    assert all(item.observation.event_family_id != "future-family" for item in family_fold)
    assert all(item.observation.event_id != "overlap" for item in family_fold)

    diagnostic = leave_group_out_diagnostic(rows, holdout_id="held", level="event")
    assert any(
        item.observation.event_id == "future" and item.role is FoldRole.TRAIN
        for item in diagnostic
    )


def _test_result(hypothesis_id: str, family: str, p_value: str) -> HypothesisTest:
    return HypothesisTest(
        hypothesis_id=hypothesis_id,
        family_id=family,
        p_value=Decimal(p_value),
        test_name="block-bootstrap-sign-test",
        null_hypothesis="mean predictive result is zero",
        test_statistic="mean",
        dependence_assumption="event-cluster dependence",
    )


def test_bh_known_vector_binds_metadata_and_predeclared_search_space() -> None:
    protocol = FDRProtocol(
        family_id="f",
        alpha=Decimal("0.05"),
        hypothesis_ids=("h1", "h2", "h3", "h4"),
    )
    tests = (
        _test_result("h1", "f", "0.01"),
        _test_result("h2", "f", "0.04"),
        _test_result("h3", "f", "0.03"),
        _test_result("h4", "f", "0.002"),
    )
    results = benjamini_hochberg(tests, protocol)
    rejected = {item.hypothesis_id for item in results if item.rejected}
    assert rejected == {"h1", "h2", "h3", "h4"}

    tie_protocol = FDRProtocol("f", Decimal("0.05"), ("a", "b"))
    tied = benjamini_hochberg(
        (_test_result("b", "f", "0.01"), _test_result("a", "f", "0.01")),
        tie_protocol,
    )
    assert {item.hypothesis_id: item.rank for item in tied} == {"a": 1, "b": 2}

    with pytest.raises(ValueError, match="predeclared FDR search space"):
        benjamini_hochberg(tests[:-1], protocol)
    with pytest.raises(ValueError, match="p-values"):
        _test_result("bad", "f", "1.1")


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
    imbalanced = {
        "e1": (Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
        "e2": (Decimal("5"),),
    }
    observation_weighted = event_bootstrap_mean(
        imbalanced,
        draws=100,
        run_id="r",
        component_id="event-observation-weighted",
        weighting=EventBootstrapWeighting.OBSERVATION_WEIGHTED_CLUSTER,
    )
    equal_event = event_bootstrap_mean(
        imbalanced,
        draws=100,
        run_id="r",
        component_id="event-equal",
        weighting=EventBootstrapWeighting.EQUAL_EVENT,
    )
    assert observation_weighted.method == "event_cluster"
    assert observation_weighted.weighting == "OBSERVATION_WEIGHTED_CLUSTER"
    assert observation_weighted.point_estimate == Decimal("1.8")
    assert equal_event.method == "event_equal_weight"
    assert equal_event.weighting == "EQUAL_EVENT"
    assert equal_event.point_estimate == Decimal("3")
    assert equal_event.units == 2


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
    fee = ExecutionStress("fee", extra_cost_per_share=Decimal("0.01"))
    latency = ExecutionStress("latency", execution_delay=timedelta(milliseconds=100))
    spec = _spec(execution_stresses=(fee, latency))
    harness = ResearchEvaluationHarness(spec, "abc123")
    row = _row(0, gross="0.03", net=None, run_id=harness.identity.run_id)
    assert row.predictive_result == Decimal("0.02")
    assert row.gross_executable_markout == Decimal("0.03")
    assert row.net_executable_markout is None
    assert harness.apply_cost_stress((row,), fee) == (Decimal("0.02"),)
    with pytest.raises(ValueError, match="re-evaluation"):
        harness.apply_cost_stress((row,), latency)
    with pytest.raises(ValueError, match="not declared"):
        harness.apply_cost_stress(
            (row,),
            ExecutionStress("other", extra_cost_per_share=Decimal("0.02")),
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
        evidence_policy=spec.disposition_policy,
    )
    assert report.serialize() == report.serialize()
    assert b'"net_executable_metrics":null' in report.serialize()

    with pytest.raises(ValueError, match="duplicate evaluation observation"):
        harness.validate_rows((row, row))
    with pytest.raises(ValueError, match="hypothesis_family"):
        harness.validate_rows((replace(row, hypothesis_family="RV"),))
    with pytest.raises(ValueError, match="horizon"):
        harness.validate_rows(
            (
                replace(
                    row,
                    horizon=timedelta(seconds=2),
                    label_end_time=row.decision_time + timedelta(seconds=2),
                ),
            )
        )
    with pytest.raises(ValueError, match="market_id"):
        harness.validate_rows((replace(row, market_id="outside"),))
    with pytest.raises(ValueError, match="event_id"):
        harness.validate_rows((replace(row, event_id="outside"),))
    with pytest.raises(ValueError, match="event_family_id"):
        harness.validate_rows((replace(row, event_family_id="outside"),))
    with pytest.raises(ValueError, match="label_end_time"):
        replace(row, label_end_time=row.label_end_time + timedelta(seconds=1))


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


def test_walk_forward_builds_multiple_chronological_folds_with_auditable_roles() -> None:
    rows = tuple(_row(seconds) for seconds in (0, 5, 10, 15, 20, 25, 30, 35, 40))
    folds = walk_forward_folds(
        rows,
        WalkForwardConfig(
            training_window=timedelta(seconds=10),
            development_window=timedelta(seconds=5),
            holdout_window=timedelta(seconds=5),
            step=timedelta(seconds=10),
            expanding_training=True,
        ),
    )
    assert len(folds) >= 2
    for fold in folds:
        assert fold.train_start < fold.train_end < fold.development_end < fold.holdout_end
        assert any(item.role is FoldRole.TRAIN for item in fold.assignments)
        assert any(item.role is FoldRole.DEVELOPMENT for item in fold.assignments)
        assert (
            fold.training_purge_evidence.rows_before
            >= fold.training_purge_evidence.rows_remaining
        )
        assert (
            fold.development_purge_evidence.rows_before
            >= fold.development_purge_evidence.rows_remaining
        )


def test_negative_controls_and_ablations_are_explicit_and_past_safe() -> None:
    zero = NegativeControl("zero", NegativeControlKind.ZERO_SIGNAL)
    delayed = NegativeControl(
        "delay",
        NegativeControlKind.DELAYED_PAST_ONLY,
        delay=timedelta(seconds=1),
    )
    exclusion = NegativeControl(
        "exclude-book",
        NegativeControlKind.FEATURE_EXCLUSION,
        excluded_components=("book",),
    )
    ablation = AblationVariant("minus-flow", ("flow",))
    assert zero.delay is None
    assert delayed.delay == timedelta(seconds=1)
    assert exclusion.excluded_components == ("book",)
    assert ablation.removed_components == ("flow",)
    with pytest.raises(ValueError, match="positive delay"):
        NegativeControl("bad-delay", NegativeControlKind.DELAYED_PAST_ONLY)


def test_walk_forward_purges_development_labels_that_overlap_holdout() -> None:
    rows = (
        _row(0, horizon=5),
        _row(5, horizon=5),
        _row(10, horizon=2),
        _row(12, horizon=8),
        _row(15, horizon=5),
        _row(20, horizon=5),
    )
    folds = walk_forward_folds(
        rows,
        WalkForwardConfig(
            training_window=timedelta(seconds=10),
            development_window=timedelta(seconds=5),
            holdout_window=timedelta(seconds=5),
            step=timedelta(seconds=10),
        ),
    )
    first = folds[0]
    assert first.development_purge_evidence.rows_removed_by_purge == 1
    assert all(
        item.observation.decision_time != BASE + timedelta(seconds=12)
        for item in first.assignments
    )


def test_promoted_report_is_forced_inconclusive_when_required_evidence_is_missing() -> None:
    spec = _spec()
    harness = ResearchEvaluationHarness(spec, "abc123")
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
        net_executable_metrics={"mean": "0.02"},
        bootstrap_results=(),
        raw_statistical_tests=(),
        fdr_results=(),
        parameter_stability=None,
        negative_controls=(),
        ablations=(),
        execution_stresses=(),
        invalidity_counts={},
        known_limitations=(),
        disposition=ResearchDisposition.PROMOTED,
        evidence_policy=spec.disposition_policy,
    )
    assert report.requested_disposition is ResearchDisposition.PROMOTED
    assert report.disposition is ResearchDisposition.INCONCLUSIVE
    assert set(report.disposition_evidence_missing) == {
        "statistical_tests",
        "fdr",
        "bootstrap",
        "parameter_stability",
        "negative_controls",
        "ablations",
        "execution_stresses",
    }

    complete = replace(
        report,
        disposition=ResearchDisposition.PROMOTED,
        raw_statistical_tests=({"test_name": "declared-test"},),
        fdr_results=({"hypothesis_id": "h1", "rejected": True},),
        bootstrap_results=({"method": "moving_block"},),
        parameter_stability={"peak_is_isolated": False},
        negative_controls=({"name": "zero", "passed": True},),
        ablations=({"name": "minus-flow", "passed": True},),
        execution_stresses=({"name": "fee", "passed": True},),
    )
    assert complete.disposition is ResearchDisposition.PROMOTED
    assert complete.disposition_evidence_missing == ()


def test_exact_boundary_labels_are_purged_at_every_evaluation_boundary() -> None:
    train_row = _row(0, horizon=10)
    dev_row = _row(10, horizon=5)
    holdout_row = _row(15, horizon=5)
    assignments = chronological_split(
        (train_row, dev_row, holdout_row),
        ChronologicalBoundaries(
            BASE + timedelta(seconds=10),
            BASE + timedelta(seconds=15),
        ),
    )
    after_train, train_evidence = purge_training(
        assignments,
        BASE + timedelta(seconds=10),
    )
    assert train_evidence.rows_removed_by_purge == 1
    assert all(item.observation is not train_row for item in after_train)

    after_dev, dev_evidence = purge_development(
        after_train,
        BASE + timedelta(seconds=15),
    )
    assert dev_evidence.rows_removed_by_purge == 1
    assert all(item.observation is not dev_row for item in after_dev)

    group = chronological_group_holdout_result(
        (
            _row(0, horizon=10, event="prior", family="f1"),
            _row(10, horizon=5, event="held", family="f1"),
        ),
        holdout_id="held",
        level="event",
    )
    assert group.purge_evidence.rows_removed_by_purge == 1
    assert [item.observation.event_id for item in group.assignments] == ["held"]


def test_harness_rejects_runtime_protocol_mismatches() -> None:
    spec = _spec()
    harness = ResearchEvaluationHarness(spec, "abc123")
    rows = tuple(
        _row(seconds, run_id=harness.identity.run_id)
        for seconds in (0, 5, 10, 15, 20, 25, 30)
    )

    bad_walk = WalkForwardConfig(
        training_window=timedelta(seconds=10),
        development_window=timedelta(seconds=5),
        holdout_window=timedelta(seconds=5),
        step=timedelta(seconds=10),
    )
    with pytest.raises(ValueError, match="walk-forward config"):
        harness.walk_forward(rows, config=bad_walk)

    bad_fdr = replace(spec.fdr, alpha=Decimal("0.10"))
    with pytest.raises(ValueError, match="FDR protocol"):
        tests = tuple(
            _test_result(hypothesis_id, "cross-venue", "0.01")
            for hypothesis_id in spec.fdr.hypothesis_ids
        )
        harness.apply_fdr(tests, protocol=bad_fdr)

    with pytest.raises(ValueError, match="bootstrap draws"):
        harness.moving_block_bootstrap(
            (Decimal("1"), Decimal("2"), Decimal("3")),
            component_id="block",
            draws=999,
        )

    with pytest.raises(ValueError, match="event weighting"):
        harness.event_bootstrap(
            {"e1": (Decimal("1"),), "e2": (Decimal("2"),)},
            component_id="event",
            weighting=EventBootstrapWeighting.EQUAL_EVENT,
        )

    with pytest.raises(ValueError, match="stability tolerance"):
        harness.parameter_surface(
            (
                ParameterCell((0,), (("x", "0"),), Decimal("1")),
                ParameterCell((1,), (("x", "1"),), Decimal("0.9")),
            ),
            tolerance=Decimal("0.20"),
        )

    with pytest.raises(ValueError, match="negative control"):
        harness.validate_negative_control(
            NegativeControl(
                "delayed",
                NegativeControlKind.DELAYED_PAST_ONLY,
                delay=timedelta(seconds=1),
            )
        )
    with pytest.raises(ValueError, match="ablation"):
        harness.validate_ablation(AblationVariant("minus-book", ("book",)))


def test_harness_validates_report_identity_and_policy() -> None:
    spec = _spec()
    harness = ResearchEvaluationHarness(spec, "abc123")
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
        folds=(),
        purge_embargo={},
        sample_counts={},
        horizon_results=(),
        predictive_metrics={},
        gross_executable_metrics={},
        net_executable_metrics=None,
        bootstrap_results=(),
        raw_statistical_tests=(),
        fdr_results=(),
        parameter_stability=None,
        negative_controls=(),
        ablations=(),
        execution_stresses=(),
        invalidity_counts={},
        known_limitations=(),
        disposition=ResearchDisposition.INCONCLUSIVE,
        evidence_policy=spec.disposition_policy,
    )
    assert harness.validate_report(report) is report
    with pytest.raises(ValueError, match="evidence policy"):
        harness.validate_report(
            replace(
                report,
                evidence_policy=replace(spec.disposition_policy, min_fdr_rejections=2),
            )
        )


def test_promotion_requires_declared_evidence_to_pass_not_just_exist() -> None:
    spec = _spec()
    harness = ResearchEvaluationHarness(spec, "abc123")
    failing = ResearchReport(
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
        folds=(),
        purge_embargo={},
        sample_counts={},
        horizon_results=(),
        predictive_metrics={},
        gross_executable_metrics={},
        net_executable_metrics=None,
        bootstrap_results=({"method": "moving_block", "lower": "0.01"},),
        raw_statistical_tests=({"test_name": "declared"},),
        fdr_results=({"hypothesis_id": "h1", "rejected": False},),
        parameter_stability={"peak_is_isolated": True},
        negative_controls=({"name": "zero", "passed": False},),
        ablations=({"name": "minus-flow", "passed": False},),
        execution_stresses=({"name": "fee", "passed": False},),
        invalidity_counts={},
        known_limitations=(),
        disposition=ResearchDisposition.PROMOTED,
        evidence_policy=spec.disposition_policy,
    )
    assert failing.disposition is ResearchDisposition.INCONCLUSIVE
    assert set(failing.disposition_evidence_missing) >= {
        "fdr_threshold",
        "stability_threshold",
        "negative_controls_threshold",
        "ablations_threshold",
        "execution_stresses_threshold",
    }

    passing = replace(
        failing,
        disposition=ResearchDisposition.PROMOTED,
        fdr_results=({"hypothesis_id": "h1", "rejected": True},),
        parameter_stability={"peak_is_isolated": False},
        negative_controls=({"name": "zero", "passed": True},),
        ablations=({"name": "minus-flow", "passed": True},),
        execution_stresses=({"name": "fee", "passed": True},),
    )
    assert passing.disposition is ResearchDisposition.PROMOTED
    assert passing.disposition_evidence_missing == ()
