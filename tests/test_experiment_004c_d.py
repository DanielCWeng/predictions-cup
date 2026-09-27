from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from predictions_cup.learning.conditional_response import (
    MULTIPLICITY_SLOTS,
    NS,
    PRIMARY_HORIZON_SECONDS,
    REAL_ORDER_PLACEMENT_ENABLED,
    asof_state,
    count_events,
    holm_adjust,
    observation_exposure_valid,
    reconstruct_genuine_bbo,
)

ROOT = Path(__file__).resolve().parents[1]
FREEZE_SHA = "ceecdcae3d6052d7501dadcf8d654c548cf7c5bf"


def row(second: int, bid: float | None, ask: float | None) -> dict[str, object]:
    return {"observed_at": second * NS, "best_bid": bid, "best_ask": ask}


def test_repeated_identical_bbo_records_are_not_genuine_changes() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(2, 0.40, 0.42),
        row(3, 0.40, 0.42),
    ])
    assert series.genuine_change.tolist() == [False, False, False]
    assert series.repeated_unchanged.tolist() == [False, True, True]
    assert count_events(series, 0, 3 * NS, kind="genuine") == 0


def test_conflicting_same_timestamp_states_fail_closed() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(2, 0.40, 0.42),
        row(2, 0.41, 0.43),
        row(3, 0.42, 0.44),
        row(4, 0.43, 0.45),
    ])
    assert not bool(series.valid[1])
    assert not bool(series.genuine_change[2])
    assert bool(series.genuine_change[3])
    assert asof_state(series, 2 * NS) is None


def test_long_gap_breaks_continuity_and_does_not_invent_renewal() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(302, 0.41, 0.43),
        row(303, 0.42, 0.44),
    ])
    assert series.genuine_change.tolist() == [False, False, True]


def test_genuine_change_age_differs_from_record_age() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(2, 0.41, 0.43),
        row(20, 0.41, 0.43),
    ])
    state = asof_state(series, 25 * NS)
    assert state is not None
    assert state["record_age_seconds"] == pytest.approx(5.0)
    assert state["genuine_change_age_seconds"] == pytest.approx(23.0)


def test_exposure_gap_never_becomes_zero_renewal() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(10, 0.40, 0.42),
        row(80, 0.40, 0.42),
    ])
    sparse_collector = np.asarray([0, 10, 80], dtype=np.int64) * NS
    assert not observation_exposure_valid(series, 10 * NS, 40 * NS, sparse_collector)


def test_exposure_valid_when_target_and_collector_bracket_horizon() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(10, 0.40, 0.42),
        row(20, 0.40, 0.42),
        row(40, 0.40, 0.42),
        row(41, 0.40, 0.42),
    ])
    collector = np.asarray([0, 10, 20, 30, 40, 41], dtype=np.int64) * NS
    assert observation_exposure_valid(series, 10 * NS, 40 * NS, collector)


def test_target_exclusion_is_frozen_in_preregistration() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    assert prereg["universe"]["target_excluded_from_own_aggregate"] is True
    assert "leave target out" in prereg["universe"]["family_graph"]


def test_state_variables_predate_source_interval_and_no_future_label_leakage() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    d1 = prereg["hypotheses"]["D1_C01"]
    assert d1["state_time"] == "t-30"
    assert d1["shock_interval"] == "(t-30,t]"
    assert d1["response_interval"] == "(t,t+30]"
    assert prereg["clock"]["asof_rule"].startswith("right edge minus 1ns")


def test_d1_and_d2_primary_horizon_is_30_seconds() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    assert PRIMARY_HORIZON_SECONDS == 30
    assert prereg["hypotheses"]["D1_C01"]["primary_target"].endswith("(t))")
    assert "(t,t+30]" in prereg["hypotheses"]["D2_C02"]["primary_target"]


def test_pre_and_active_are_separate_and_primary_pre() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    assert prereg["partition"]["primary_regime"] == "PRE_ELECTION"
    assert prereg["partition"]["secondary_regime"] == "ACTIVE_RESULTS"
    assert prereg["partition"]["pool_pre_and_active"] is False


def test_all_eight_multiplicity_slots_are_declared_and_dormant_cannot_substitute() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    assert tuple(prereg["multiplicity"]["slots"]) == MULTIPLICITY_SLOTS
    assert prereg["hypotheses"]["D3_C03"]["status"] == "DORMANT"
    assert prereg["hypotheses"]["D3_C03"]["reserved_slots_remain"] is True
    p = {slot: 1.0 for slot in MULTIPLICITY_SLOTS}
    result = holm_adjust(p)
    assert set(result) == set(MULTIPLICITY_SLOTS)
    assert not any(v["reject"] for v in result.values())


def test_challenge_access_is_declared_only_after_terminal_freeze() -> None:
    provenance = json.loads(
        (
            ROOT
            / "data/experiments/experiment_004c_d/provenance/astra_provenance.json"
        ).read_text()
    )
    assert provenance["challenge_empirical_access_before_freeze"] is False
    import subprocess

    freeze = subprocess.check_output(
        ["git", "rev-parse", FREEZE_SHA], cwd=ROOT, text=True
    ).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", freeze, head], cwd=ROOT
    )
    assert freeze == FREEZE_SHA
    assert ancestor.returncode == 0


def test_no_real_order_placement_can_occur() -> None:
    prereg = json.loads(
        (ROOT / "data/experiments/experiment_004c_d/preregistration.json").read_text()
    )
    assert REAL_ORDER_PLACEMENT_ENABLED is False
    assert prereg["scientific_restrictions"]["real_order_placement"] is False


def test_raw_record_placebo_is_distinct_from_genuine_change_count() -> None:
    series = reconstruct_genuine_bbo([
        row(1, 0.40, 0.42),
        row(2, 0.40, 0.42),
        row(3, 0.41, 0.43),
    ])
    assert count_events(series, 0, 3 * NS, kind="raw") == 3
    assert count_events(series, 0, 3 * NS, kind="genuine") == 1
    assert count_events(series, 0, 3 * NS, kind="unchanged") == 1
