from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow import (
    CanonicalShadowSnapshot,
    DecisionStatus,
    FeatureParity,
    FixedHazard005FRegimeProvider,
    Frozen005FEvaluator,
    FrozenPred006Evaluator,
    Hazard005FBboObservation,
    Hazard005FCandidate,
    IncrementalHazard005FState,
    IncrementalPred006FeatureState,
    Pred006ArtifactManifest,
    Pred006BlockObservation,
    Pred006Candidate,
    pred006_live_parity_matrix,
)
from predictions_cup.shadow.frozen_runtime import HAZARD005F_SCORER_HASHES

BASE_MONO = 10_000_000_000_000
NS = 1_000_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = PROJECT_ROOT / "tests/fixtures/candidate_runtime001_golden.json"


def _snapshot(
    timestamp_s: int,
    *,
    market_id: str = "m1",
    exchange_id: str = "e1",
) -> CanonicalShadowSnapshot:
    now = BASE_MONO + timestamp_s * 1_000_000_000
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="t1",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="t1",
                bids=(RuntimeLevel(price_ticks=90, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=110, quantity=10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="t1",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now,
        sig_depth_trusted=True,
        account_observed_ns=now,
        inventory_observed_ns=now,
        external_quotes={},
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=datetime.fromtimestamp(timestamp_s, tz=UTC),
        mapping_version="test-mapping",
        source_revision=f"test-{timestamp_s}",
    )


@dataclass(frozen=True)
class _FixedScorer:
    scorer_id: str
    value: float
    artifact_hash: str

    def score(self, values: tuple[float, ...]) -> float:
        assert values
        return self.value


def _pred_manifest() -> Pred006ArtifactManifest:
    return Pred006ArtifactManifest(
        manifest_version="pred006-artifact-manifest-v1",
        research_id=FrozenPred006Evaluator.research_id,
        frozen_spec_version=FrozenPred006Evaluator.frozen_spec_version,
        feature_schema_hash=FrozenPred006Evaluator.feature_schema_hash,
        artifacts=(
            ("PRED006-C01", "a" * 64),
            ("PRED006-C02", "b" * 64),
        ),
        provenance="synthetic-test-only:never-production-authorized",
    )


def _golden() -> dict[str, object]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    return hashlib.sha1(  # noqa: S324 - Git object identity, not security
        f"blob {len(payload)}\0".encode() + payload
    ).hexdigest()


def _pred_observation(
    timestamp_s: int,
    p_yes: float,
    *,
    scope_id: str = "m1",
    block_number: int | None = None,
) -> Pred006BlockObservation:
    block = timestamp_s + 1 if block_number is None else block_number
    return Pred006BlockObservation(
        scope_id=scope_id,
        window_id="w1",
        block_number=block,
        timestamp_s=timestamp_s,
        observed_monotonic_ns=BASE_MONO + timestamp_s * NS + block,
        p_yes=p_yes,
        size_shares=9.0,
        value_usd=4.0,
        fee_charged=1.0,
        fee_missing=0.0,
        fee_no_leg=0.0,
        active_fee_net=-2.0,
        active_fee_charged=3.0,
        active_fee_refunded=1.0,
        active_charge_legs=2.0,
        source_version="fixture-data003",
    )


def test_pred006_incremental_features_match_frozen_formula_fixture() -> None:
    state = IncrementalPred006FeatureState()
    state.observe(_pred_observation(0, 0.40))
    state.observe(_pred_observation(30, 0.50))
    state.observe(_pred_observation(120, 0.45))
    vector = state.observe(_pred_observation(1800, 0.55))
    values = vector.as_mapping()

    assert values["boundary_distance"] == pytest.approx(0.45)
    assert values["size_log"] == pytest.approx(math.log1p(9.0))
    assert values["value_log"] == pytest.approx(math.log1p(4.0))
    assert values["since_prev"] == 1680.0
    assert values["count_30"] == 1.0
    assert values["count_120"] == 1.0
    assert values["count_600"] == 1.0
    assert values["count_1800"] == 4.0
    assert values["vol_30"] == pytest.approx(0.10)
    assert values["vol_600"] == pytest.approx(0.10)
    assert values["vol_1800"] == pytest.approx(0.15)
    assert values["mom_30"] == 0.0
    assert values["mom_1800"] == pytest.approx(0.15)
    assert values["fee_net_log"] == pytest.approx(-math.log1p(2.0))
    assert values["fee_charge_log"] == pytest.approx(math.log1p(3.0))
    assert values["fee_refund_log"] == pytest.approx(math.log1p(1.0))
    assert values["charge_legs_log"] == pytest.approx(math.log1p(2.0))


def test_pred006_missing_history_preserves_nan_missingness() -> None:
    state = IncrementalPred006FeatureState()
    vector = state.observe(_pred_observation(100, 0.40))
    values = vector.as_mapping()
    assert math.isnan(values["since_prev"])
    assert math.isnan(values["count_30"])
    assert math.isnan(values["vol_1800"])
    assert math.isnan(values["mom_600"])


def test_pred006_current_live_parity_flags_exact_custody_gap() -> None:
    parity = {row.feature: row.parity for row in pred006_live_parity_matrix()}
    assert parity["p_yes"] is FeatureParity.EXACT_BUT_DELAYED
    assert parity["count_1800"] is FeatureParity.EXACT_BUT_DELAYED
    assert parity["fee_charged"] is FeatureParity.NOT_OBSERVABLE_LIVE
    assert parity["fee_net_log"] is FeatureParity.NOT_OBSERVABLE_LIVE


def test_pred006_model_absence_is_explicit_and_precedes_feature_guessing() -> None:
    candidate = Pred006Candidate(FrozenPred006Evaluator())
    output = candidate.evaluate(_snapshot(2000))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason == "model_artifact_missing"
    assert output.candidate_payload["artifact_hash"] is None
    assert (
        output.candidate_payload["expected_artifact_hash"]
        == "NO_AUTHORIZED_SERIALIZED_FITTED_MODEL_MANIFEST"
    )


def test_pred006_scores_both_survivors_without_aggregating_semantics() -> None:
    state = IncrementalPred006FeatureState(
        scope_resolver=lambda snapshot: snapshot.market_id
    )
    for timestamp_s, p_yes in ((0, 0.40), (30, 0.50), (120, 0.45), (1800, 0.55)):
        state.observe(_pred_observation(timestamp_s, p_yes))
    evaluator = FrozenPred006Evaluator(
        state,
        scorers={
            "PRED006-C01": _FixedScorer("c01", 0.70, "a" * 64),
            "PRED006-C02": _FixedScorer("c02", 0.30, "b" * 64),
        },
        artifact_manifest=_pred_manifest(),
    )
    output = Pred006Candidate(evaluator).evaluate(_snapshot(1800))
    assert output.status is DecisionStatus.OK
    assert output.score is None
    assert output.direction is None
    assert output.candidate_payload["hazard_probabilities"] == {
        "PRED006-C01": 0.70,
        "PRED006-C02": 0.30,
    }


def test_pred006_scorers_without_authorized_manifest_stay_not_ready() -> None:
    state = IncrementalPred006FeatureState(
        scope_resolver=lambda snapshot: snapshot.market_id
    )
    state.observe(_pred_observation(0, 0.40, block_number=1))
    evaluator = FrozenPred006Evaluator(
        state,
        scorers={
            "PRED006-C01": _FixedScorer("c01", 0.70, "a" * 64),
            "PRED006-C02": _FixedScorer("c02", 0.30, "b" * 64),
        },
    )
    output = Pred006Candidate(evaluator).evaluate(_snapshot(0))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason == "model_artifact_missing"


def test_pred006_manifest_hash_mismatch_fails_closed() -> None:
    state = IncrementalPred006FeatureState(
        scope_resolver=lambda snapshot: snapshot.market_id
    )
    state.observe(_pred_observation(0, 0.40, block_number=1))
    evaluator = FrozenPred006Evaluator(
        state,
        scorers={
            "PRED006-C01": _FixedScorer("c01", 0.70, "0" * 64),
            "PRED006-C02": _FixedScorer("c02", 0.30, "b" * 64),
        },
        artifact_manifest=_pred_manifest(),
    )
    output = Pred006Candidate(evaluator).evaluate(_snapshot(0))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason == "model_artifact_hash_mismatch:PRED006-C01"


def test_pred006_equal_second_blocks_are_legal_but_block_order_is_strict() -> None:
    state = IncrementalPred006FeatureState()
    state.observe(_pred_observation(100, 0.40, block_number=100))
    vector = state.observe(_pred_observation(100, 0.50, block_number=101))
    assert vector.block_number == 101
    assert vector.as_mapping()["since_prev"] == 0.0

    with pytest.raises(ValueError, match="block_number must strictly increase"):
        state.observe(_pred_observation(101, 0.51, block_number=101))
    with pytest.raises(ValueError, match="block_number must strictly increase"):
        state.observe(_pred_observation(102, 0.52, block_number=99))


def test_pred006_golden_vector_matches_original_research_output() -> None:
    golden = _golden()
    pred = golden["pred006"]
    assert isinstance(pred, dict)
    observations = pred["observations"]
    assert isinstance(observations, list)

    state = IncrementalPred006FeatureState()
    vectors = []
    for row in observations:
        assert isinstance(row, dict)
        vectors.append(
            state.observe(
                _pred_observation(
                    int(row["timestamp_s"]),
                    float(row["p_yes"]),
                    block_number=int(row["block_number"]),
                )
            )
        )

    equal_expected = pred["equal_second_expected"]
    assert isinstance(equal_expected, dict)
    assert vectors[1].block_number == int(equal_expected["block_number"])
    assert vectors[1].as_mapping()["since_prev"] == float(
        equal_expected["since_prev"]
    )

    final_expected = pred["final_expected"]
    assert isinstance(final_expected, dict)
    final = vectors[-1]
    assert final.block_number == int(final_expected["block_number"])
    assert final.timestamp_s == int(final_expected["timestamp_s"])
    expected_values = final_expected["values"]
    assert isinstance(expected_values, dict)
    actual = final.as_mapping()
    assert set(actual) == set(expected_values)
    for name, expected in expected_values.items():
        assert actual[name] == pytest.approx(float(expected), rel=1e-12, abs=1e-12)


def _observe_005f_fixture(state: IncrementalHazard005FState) -> None:
    rows = (
        (0, 0.40, 0.60),
        (10, 0.40, 0.60),
        (20, 0.42, 0.60),
        (30, 0.42, 0.60),
        (45, 0.44, 0.60),
    )
    for timestamp_s, bid, ask in rows:
        state.observe(
            Hazard005FBboObservation(
                scope_id="m1",
                timestamp_ns=timestamp_s * NS,
                observed_monotonic_ns=BASE_MONO + timestamp_s * NS,
                best_bid=bid,
                best_ask=ask,
                source_version="fixture",
            )
        )


def test_005f_genuine_age_and_capture_bin_boundaries_match_freeze() -> None:
    state = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    _observe_005f_fixture(state)
    vector = state.feature_vector(_snapshot(60))
    assert vector is not None
    assert vector.grid_time_ns == 60 * NS
    assert vector.values["genuine_age_s"] == 15.0
    assert vector.values["genuine_15"] == 0.0
    assert vector.values["genuine_60"] == 2.0
    assert vector.values["abs_ret_15"] == pytest.approx(0.0)
    assert math.isfinite(vector.values["rv_60"])
    assert vector.values["rv_60"] > 0.0


def test_005f_requires_explicit_grid_origin() -> None:
    state = IncrementalHazard005FState(
        scope_resolver=lambda snapshot: snapshot.market_id
    )
    _observe_005f_fixture(state)
    assert state.feature_vector(_snapshot(60)) is None


def test_005f_default_runtime_fails_closed_on_missing_orderbook_history() -> None:
    output = Hazard005FCandidate(Frozen005FEvaluator()).evaluate(_snapshot(60))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason == "required_orderbook_history_unavailable"


def test_005f_active_runtime_keeps_update_and_jump_coordinates_separate() -> None:
    state = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    _observe_005f_fixture(state)
    evaluator = Frozen005FEvaluator(
        state,
        FixedHazard005FRegimeProvider("ACTIVE_RESULTS"),
        scorers={
            "ACTIVE_RESULTS|clock|UPDATE_HAZARD": _FixedScorer(
                "active-update",
                0.70,
                HAZARD005F_SCORER_HASHES["ACTIVE_RESULTS|clock|UPDATE_HAZARD"]
            ),
            "ACTIVE_RESULTS|clock|JUMP_HAZARD": _FixedScorer(
                "active-jump",
                0.20,
                HAZARD005F_SCORER_HASHES["ACTIVE_RESULTS|clock|JUMP_HAZARD"]
            ),
        },
    )
    output = Hazard005FCandidate(evaluator).evaluate(_snapshot(60))
    assert output.status is DecisionStatus.OK
    assert output.score == 0.70
    assert output.direction is None
    assert output.candidate_payload["update_hazard"] == 0.70
    assert output.candidate_payload["jump_hazard"] == 0.20
    assert output.candidate_payload["output_type"] == "movement_hazard_not_directional"


def test_005f_pre_runtime_does_not_invent_unfrozen_jump_coordinate() -> None:
    state = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    _observe_005f_fixture(state)
    evaluator = Frozen005FEvaluator(
        state,
        FixedHazard005FRegimeProvider("PRE_ELECTION"),
        scorers={
            "PRE_ELECTION|clock|UPDATE_HAZARD": _FixedScorer(
                "pre-update",
                0.61,
                HAZARD005F_SCORER_HASHES["PRE_ELECTION|clock|UPDATE_HAZARD"]
            )
        },
    )
    output = Hazard005FCandidate(evaluator).evaluate(_snapshot(60))
    assert output.status is DecisionStatus.OK
    assert output.score == 0.61
    assert output.candidate_payload["jump_hazard"] is None


def test_005f_rejects_wrong_frozen_artifact_hash() -> None:
    state = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    _observe_005f_fixture(state)
    evaluator = Frozen005FEvaluator(
        state,
        FixedHazard005FRegimeProvider("PRE_ELECTION"),
        scorers={
            "PRE_ELECTION|clock|UPDATE_HAZARD": _FixedScorer(
                "wrong-hash", 0.50, "0" * 64
            )
        },
    )
    output = Hazard005FCandidate(evaluator).evaluate(_snapshot(60))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason is not None
    assert output.abstain_reason.startswith("model_artifact_hash_mismatch:")


def test_005f_boundary_and_ambiguous_bbo_do_not_establish_state() -> None:
    state = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    for observation in (
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=0,
            observed_monotonic_ns=BASE_MONO,
            best_bid=0.0,
            best_ask=0.60,
            source_version="fixture",
        ),
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=5 * NS,
            observed_monotonic_ns=BASE_MONO + 5 * NS,
            best_bid=0.40,
            best_ask=1.0,
            source_version="fixture",
        ),
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=10 * NS,
            observed_monotonic_ns=BASE_MONO + 10 * NS,
            best_bid=0.40,
            best_ask=0.60,
            source_version="fixture",
            ambiguous=True,
        ),
    ):
        state.observe(observation)
    assert state.feature_vector(_snapshot(15)) is None


def test_005f_golden_nanosecond_vector_matches_frozen_research_output() -> None:
    golden = _golden()
    hazard = golden["hazard005f"]
    assert isinstance(hazard, dict)
    state = IncrementalHazard005FState(
        grid_origin_ns=int(hazard["grid_origin_ns"]),
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    observations = hazard["observations"]
    assert isinstance(observations, list)
    for row in observations:
        assert isinstance(row, dict)
        timestamp_ns = int(row["timestamp_ns"])
        state.observe(
            Hazard005FBboObservation(
                scope_id="m1",
                timestamp_ns=timestamp_ns,
                observed_monotonic_ns=BASE_MONO + timestamp_ns,
                best_bid=float(row["best_bid"]),
                best_ask=float(row["best_ask"]),
                source_version="golden-005f",
            )
        )

    query_ns = int(hazard["query_ns"])
    vector = state.feature_vector(_snapshot(query_ns // NS))
    assert vector is not None
    expected = hazard["expected"]
    assert isinstance(expected, dict)
    assert vector.grid_time_ns == int(expected["grid_time_ns"])
    for name in ("genuine_15", "genuine_60", "genuine_age_s", "abs_ret_15", "rv_60"):
        assert vector.values[name] == pytest.approx(
            float(expected[name]), rel=1e-12, abs=1e-12
        )


def test_005f_gap_boundary_is_nanosecond_exact() -> None:
    golden = _golden()
    hazard = golden["hazard005f"]
    assert isinstance(hazard, dict)
    boundary = hazard["gap_boundary"]
    assert isinstance(boundary, dict)

    below = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    below.observe(
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=0,
            observed_monotonic_ns=BASE_MONO,
            best_bid=0.40,
            best_ask=0.60,
        )
    )
    below_ns = int(boundary["below_300s_ns"])
    below.observe(
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=below_ns,
            observed_monotonic_ns=BASE_MONO + below_ns,
            best_bid=0.41,
            best_ask=0.60,
        )
    )
    vector = below.feature_vector(_snapshot(300))
    assert vector is not None
    assert vector.values["genuine_age_s"] == pytest.approx(1e-9, abs=1e-15)

    above = IncrementalHazard005FState(
        grid_origin_ns=0,
        scope_resolver=lambda snapshot: snapshot.market_id,
    )
    above.observe(
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=0,
            observed_monotonic_ns=BASE_MONO,
            best_bid=0.40,
            best_ask=0.60,
        )
    )
    above_ns = int(boundary["above_300s_ns"])
    above.observe(
        Hazard005FBboObservation(
            scope_id="m1",
            timestamp_ns=above_ns,
            observed_monotonic_ns=BASE_MONO + above_ns,
            best_bid=0.41,
            best_ask=0.60,
        )
    )
    assert above.feature_vector(_snapshot(315)) is None


def test_golden_fixture_is_pinned_to_original_research_source_blobs() -> None:
    golden = _golden()
    provenance = golden["provenance"]
    assert isinstance(provenance, dict)
    pred_path = PROJECT_ROOT / str(provenance["pred006_source"])
    hazard_path = PROJECT_ROOT / str(provenance["hazard005f_source"])
    assert _git_blob_sha(pred_path) == provenance["pred006_source_blob_sha"]
    assert _git_blob_sha(hazard_path) == provenance["hazard005f_source_blob_sha"]


def test_incremental_runtime_requires_explicit_scope_resolution() -> None:
    pred = IncrementalPred006FeatureState()
    pred.observe(_pred_observation(0, 0.40))
    assert pred.feature_vector(_snapshot(0)) is None

    hazard = IncrementalHazard005FState(grid_origin_ns=0)
    _observe_005f_fixture(hazard)
    assert hazard.feature_vector(_snapshot(60)) is None
