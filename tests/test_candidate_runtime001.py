from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

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
    Pred006BlockObservation,
    Pred006Candidate,
    pred006_live_parity_matrix,
)
from predictions_cup.shadow.frozen_runtime import HAZARD005F_SCORER_HASHES

BASE_MONO = 10_000_000_000_000


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


def _pred_observation(
    timestamp_s: int,
    p_yes: float,
    *,
    scope_id: str = "m1",
) -> Pred006BlockObservation:
    return Pred006BlockObservation(
        scope_id=scope_id,
        window_id="w1",
        timestamp_s=timestamp_s,
        observed_monotonic_ns=BASE_MONO + timestamp_s * 1_000_000_000,
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
        == "NO_SERIALIZED_FITTED_MODEL_IN_FROZEN_PRED006_OUTPUT"
    )


def test_pred006_scores_both_survivors_without_aggregating_semantics() -> None:
    state = IncrementalPred006FeatureState()
    for timestamp_s, p_yes in ((0, 0.40), (30, 0.50), (120, 0.45), (1800, 0.55)):
        state.observe(_pred_observation(timestamp_s, p_yes))
    evaluator = FrozenPred006Evaluator(
        state,
        scorers={
            "PRED006-C01": _FixedScorer("c01", 0.70, "a" * 64),
            "PRED006-C02": _FixedScorer("c02", 0.30, "b" * 64),
        },
    )
    output = Pred006Candidate(evaluator).evaluate(_snapshot(1800))
    assert output.status is DecisionStatus.OK
    assert output.score is None
    assert output.direction is None
    assert output.candidate_payload["hazard_probabilities"] == {
        "PRED006-C01": 0.70,
        "PRED006-C02": 0.30,
    }


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
                timestamp_s=timestamp_s,
                observed_monotonic_ns=BASE_MONO + timestamp_s * 1_000_000_000,
                best_bid=bid,
                best_ask=ask,
                source_version="fixture",
            )
        )


def test_005f_genuine_age_and_capture_bin_boundaries_match_freeze() -> None:
    state = IncrementalHazard005FState(grid_origin_s=0)
    _observe_005f_fixture(state)
    vector = state.feature_vector(_snapshot(60))
    assert vector is not None
    assert vector.grid_time_s == 60
    assert vector.values["genuine_age_s"] == 15.0
    assert vector.values["genuine_15"] == 0.0
    assert vector.values["genuine_60"] == 2.0
    assert vector.values["abs_ret_15"] == pytest.approx(0.0)
    assert math.isfinite(vector.values["rv_60"])
    assert vector.values["rv_60"] > 0.0


def test_005f_requires_explicit_grid_origin() -> None:
    state = IncrementalHazard005FState()
    _observe_005f_fixture(state)
    assert state.feature_vector(_snapshot(60)) is None


def test_005f_default_runtime_fails_closed_on_missing_orderbook_history() -> None:
    output = Hazard005FCandidate(Frozen005FEvaluator()).evaluate(_snapshot(60))
    assert output.status is DecisionStatus.NOT_READY
    assert output.abstain_reason == "required_orderbook_history_unavailable"


def test_005f_active_runtime_keeps_update_and_jump_coordinates_separate() -> None:
    state = IncrementalHazard005FState(grid_origin_s=0)
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
    state = IncrementalHazard005FState(grid_origin_s=0)
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
    state = IncrementalHazard005FState(grid_origin_s=0)
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
