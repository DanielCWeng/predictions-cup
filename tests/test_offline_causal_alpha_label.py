"""Offline-only negative admission tests for the causal label consumer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from predictions_cup.research.offline_causal_alpha_label import (
    Book,
    CompletedMinute,
    DecisionCandidate,
    FeeRecord,
    FrozenDecision,
    Level,
    label_offline_candidates,
)
from test_offline_paired_tape_gate import _sample

D = Decimal
T = datetime(2026, 10, 16, 12, 0, 0, 500000, tzinfo=timezone.utc)


def _book(
    ms: int, seq: int, *, bid: str = "0.40", ask: str = "0.70",
    shares: str = "2",
) -> Book:
    at = T + timedelta(milliseconds=ms)
    return Book(
        "879", "m-879", f"book-{seq}", seq, 1,
        at - timedelta(milliseconds=10), at, 10,
        (Level(D(bid), D(shares)),), (Level(D(ask), D(shares)),),
    )


def _candidate(side: str = "YES") -> DecisionCandidate:
    minutes = tuple(
        CompletedMinute(
            datetime(2026, 10, 16, 11, 55, tzinfo=timezone.utc)
            + timedelta(minutes=i),
            datetime(2026, 10, 16, 11, 55, tzinfo=timezone.utc)
            + timedelta(minutes=i, milliseconds=50),
            True,
        )
        for i in range(6)
    )
    return DecisionCandidate(
        candidate_id="candidate-1", paired_interval_id="interval-1",
        event_id="event-1", episode_id="episode-1",
        frozen=FrozenDecision("1" * 64, "2" * 64, "DEV", 5, D("0.03")),
        sig_exchange_id="879", sig_market_id="m-879",
        pm_condition_id="condition-a", pm_token_id="pm-yes",
        sig_outcome="YES", pm_outcome="YES", mapping_direction="SAME",
        buy_side=side, quantity=1, depth_haircut=D("0.5"),
        decision_at=T, latency_ms=200, max_age_ms=300, max_uncertainty_ms=50,
        pm_minutes=minutes, decision_book=_book(-50, 51),
        arrival_book=_book(70, 52, bid="0.42", ask="0.69"),
        exit_at=T + timedelta(seconds=5),
        exit_book=_book(4950, 53, bid="0.44", ask="0.68"),
        settlement=None,
        fee_record=FeeRecord("HYPOTHETICAL", "fixture-only", D("0.002"), "TAKER"),
    )


def _run(tmp_path: Path, *candidates: DecisionCandidate, bad_gate: bool = False):
    def corrupt(_mapping: object, sig: dict[str, object],
                _pm: object, _manifest: object) -> None:
        sig["publisher_clock_attested"] = False

    paths, expected = _sample(tmp_path, corrupt if bad_gate else None)
    return label_offline_candidates(
        *paths, expected_manifest_sha256=expected, candidates=tuple(candidates),
    )


def _reject(tmp_path: Path, case: DecisionCandidate, code: str) -> None:
    result = _run(tmp_path, case)
    label = result.candidates[0]
    assert label.status == "REJECTED/PAPER_NOT_ADMITTED"
    assert label.rejection == code
    assert result.rejection_counts == ((code, 1),)
    assert label.real_after_cost_susqies is None
    assert result.admitted_real_net_susqies is None
    assert result.no_trade_control_count == 1


def test_good_shape_remains_paper_and_real_net_denied(tmp_path: Path) -> None:
    result = _run(tmp_path, _candidate())
    label = result.candidates[0]
    assert result.tape_gate.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.tape_gate.schema_valid_intervals == 1
    assert label.status == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert label.rejection is None
    assert label.hypothetical_entry_limit == D("0.70")
    assert label.hypothetical_exit_proceeds == D("0.44")
    assert "SOURCE_NOT_EXTERNALLY_AUTHENTICATED" in label.economic_rejections
    assert "FEE_PROOF_NOT_EXTERNALLY_ADMITTED" in label.economic_rejections
    assert label.real_after_cost_susqies is None
    assert result.admitted_real_net_susqies is None
    assert not result.paper_admitted and not result.source_admitted
    assert not label.paper_admitted and not label.source_admitted
    assert result.network_requests == 0
    assert result.no_trade_control_count == 1


def test_no_complements_same_exchange_yes_bid_and_ask(tmp_path: Path) -> None:
    label = _run(tmp_path, _candidate("NO")).candidates[0]
    assert label.status == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert label.hypothetical_entry_limit == D("0.60")  # 1 - YES bid .40
    assert label.hypothetical_exit_proceeds == D("0.32")  # 1 - YES ask .68
    assert label.real_after_cost_susqies is None


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("future_decision", "FUTURE_DECISION_BOOK"),
        ("stale_decision", "STALE_DECISION_BOOK"),
        ("future_arrival", "FUTURE_ARRIVAL_BOOK"),
        ("crossed", "CROSSED_BOOK"),
        ("no_exit", "EXIT_NOT_AVAILABLE"),
        ("bad_mapping", "MAPPING_ORIENTATION_INVALID"),
        ("future_pm", "PM_MINUTE_FUTURE_LEAK"),
        ("partial_pm", "PM_MINUTE_FUTURE_LEAK"),
        ("gap_pm", "PM_MINUTE_SEQUENCE_GAP"),
        ("naive_clock", "NAIVE_OR_NONUTC_PUBLISHER"),
        ("fractional", "WHOLE_LOT_DEPTH_UNAVAILABLE"),
        ("latency_2x", "STALE_ARRIVAL_BOOK"),
        ("latency_5x", "STALE_ARRIVAL_BOOK"),
        ("exit_sequence", "EXIT_NOT_NEW_CAUSAL_BOOK"),
    ],
)
def test_adversarial_causal_rejections(
    tmp_path: Path, case: str, reason: str,
) -> None:
    c = _candidate()
    if case == "future_decision":
        c = replace(c, decision_book=_book(40, 51))
    elif case == "stale_decision":
        c = replace(c, decision_book=_book(-1000, 51))
    elif case == "future_arrival":
        c = replace(c, arrival_book=_book(300, 52))
    elif case == "crossed":
        c = replace(c, decision_book=_book(-50, 51, bid="0.75", ask="0.70"))
    elif case == "no_exit":
        c = replace(c, exit_book=None)
    elif case == "bad_mapping":
        c = replace(c, mapping_direction="COMPLEMENT")
    elif case in ("future_pm", "partial_pm", "gap_pm"):
        mins = list(c.pm_minutes)
        if case == "future_pm":
            mins[-1] = replace(mins[-1], received_at=T + timedelta(milliseconds=100))
        elif case == "partial_pm":
            mins[1] = replace(mins[1], complete=False)
        else:
            mins[3] = replace(mins[3], ended_at=mins[3].ended_at
                              + timedelta(seconds=1))
        c = replace(c, pm_minutes=tuple(mins))
    elif case == "naive_clock":
        c = replace(c, decision_book=replace(
            c.decision_book, publisher_at=c.decision_book.publisher_at.replace(
                tzinfo=None,
            ),
        ))
    elif case == "fractional":
        c = replace(c, decision_book=replace(
            c.decision_book,
            asks=(Level(D("0.60"), D("1")), Level(D("0.70"), D("1"))),
        ))
    elif case == "latency_2x":
        c = replace(c, latency_ms=400)
    elif case == "latency_5x":
        c = replace(c, latency_ms=1000)
    elif case == "exit_sequence":
        assert c.exit_book is not None
        c = replace(c, exit_book=replace(c.exit_book, sequence=52))
    _reject(tmp_path, c, reason)


def test_missing_native_fees_not_backfilled_with_zero(tmp_path: Path) -> None:
    label = _run(tmp_path, replace(_candidate(), fee_record=None)).candidates[0]
    assert label.rejection is None
    assert "NATIVE_FEE_MISSING" in label.economic_rejections
    assert label.real_after_cost_susqies is None


def test_second_overlapping_episode_refused(tmp_path: Path) -> None:
    first = _candidate()
    second = replace(first, candidate_id="candidate-2", episode_id="episode-2",
                     decision_at=T + timedelta(seconds=1))
    result = _run(tmp_path, first, second)
    assert result.candidates[0].status == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.candidates[1].rejection == "OVERLAPPING_EVENT_EPISODE"
    assert result.no_trade_control_count == 2
    assert result.admitted_real_net_susqies is None


def test_real_upstream_gate_failure_preempts_scenario(tmp_path: Path) -> None:
    result = _run(tmp_path, _candidate(), bad_gate=True)
    assert result.tape_gate.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.candidates[0].rejection == (
        "UPSTREAM_PAIRED_TAPE:PUBLISHER_CLOCK_UNATTESTED"
    )
    assert result.admitted_real_net_susqies is None


@pytest.mark.parametrize("quantity", [1, 5, 10, 20])
def test_whole_sizes_floor_each_level_not_pool_half_shares(
    tmp_path: Path, quantity: int,
) -> None:
    c = _candidate()
    c = replace(c, quantity=quantity, decision_book=replace(
        c.decision_book, asks=(Level(D("0.60"), D(quantity)),),
    ))
    _reject(tmp_path, c, "WHOLE_LOT_DEPTH_UNAVAILABLE")
