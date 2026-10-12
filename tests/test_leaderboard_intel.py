"""Adversarial/offline tests for Lane D research only."""

from __future__ import annotations

import ast
import copy
import csv
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.analysis.leaderboard_intel import (
    compare_cumulative_snapshots,
    normalize_official_payload,
    required_volume,
    score_tournament_scenarios,
)


def sample() -> dict[str, Any]:
    return {
        "period": "all", "limit": 25, "offset": 0, "total": 1467,
        "leaderboard": [
            {"rank": 1, "profileId": "a", "username": "test-a", "pnl": "941965.08",
             "tradesCount": 3444, "volume": "10562773.8", "roi": "941.96508",
             "winRate": None},
            {"rank": 2, "profileId": "b", "username": None, "pnl": "845587.81",
             "tradesCount": 16322, "volume": "0", "roi": "845.58781",
             "winRate": "0.72"},
        ],
    }


def norm(
    payload: dict[str, Any] | None = None, when: str = "2026-10-12T20:20:00Z"
) -> list[dict[str, str]]:
    return normalize_official_payload(
        sample() if payload is None else payload,
        observed_at=when,
        source_url="https://sig.thesuper.market/api/v1/tournaments/example/leaderboard",
    )


def test_roundtrip_decimal_and_missing_volume() -> None:
    rows = norm()
    assert rows[0]["pnl_wire"] == "941965.08"
    assert rows[0]["start_bankroll_return_pct"] == "941.96508"
    assert rows[0]["pnl_per_volume"] != ""
    assert rows[1]["pnl_per_volume"] == ""
    assert rows[0]["settled_pnl"] == "NOT_OBSERVABLE"
    assert rows[1]["username"] == ""


@pytest.mark.parametrize("field,value", [
    ("pnl", "NaN"), ("pnl", "Infinity"), ("volume", "-1"),
    ("tradesCount", -1), ("rank", 0), ("profileId", ""),
    ("roi", None),
])
def test_fail_closed_malformed_rows(field: str, value: object) -> None:
    payload = sample()
    payload["leaderboard"][0][field] = value
    with pytest.raises(ValueError):
        norm(payload)


def test_duplicate_profile_rejected_but_tied_ranks_allowed() -> None:
    payload = sample()
    payload["leaderboard"][1]["rank"] = 1
    assert len(norm(payload)) == 2
    payload["leaderboard"][1]["profileId"] = "a"
    with pytest.raises(ValueError):
        norm(payload)


def test_wrong_page_and_naive_capture_rejected() -> None:
    for k, v in [("period", "weekly"), ("limit", 101), ("offset", -1), ("total", 1)]:
        bad = sample()
        bad[k] = v
        with pytest.raises(ValueError):
            norm(bad)
    with pytest.raises(ValueError):
        norm(when="2026-10-12T20:20:00")


def test_window_is_not_start_bankroll_return() -> None:
    payload = sample()
    payload["period"] = "1d"
    assert norm(payload)[0]["start_bankroll_return_pct"] == ""
    with pytest.raises(ValueError):
        compare_cumulative_snapshots(norm(payload), norm())


def test_change_requires_future_same_period_and_identity() -> None:
    before = norm()
    after_payload = copy.deepcopy(sample())
    after_payload["leaderboard"][0]["pnl"] = "941970.08"
    after_payload["leaderboard"][0]["tradesCount"] += 1
    after = norm(after_payload, when="2026-10-12T20:30:00Z")
    delta = compare_cumulative_snapshots(before, after)
    assert delta[0]["pnl_change"] == "5.00"
    assert delta[0]["trade_count_change"] == "1"
    with pytest.raises(ValueError):
        compare_cumulative_snapshots(after, before)


def test_scenarios_strict_podium_and_mean_tail() -> None:
    spec: dict[str, Any] = {
        "third_place_thresholds": [
            {"value": 300000, "probability": "0.5"},
            {"value": 1000000, "probability": "0.5"},
        ],
        "strategies": [{"name": "binary",
            "final_bankroll_outcomes": [
                {"value": 1000000, "probability": "0.25"},
                {"value": 0, "probability": "0.75"},
            ]}],
    }
    result = score_tournament_scenarios(spec)[0]
    assert result["illustrative_expected_terminal_bankroll"] == "250000.00"
    assert result["illustrative_p05_terminal_bankroll"] == "0"
    assert result["illustrative_podium_probability"] == "0.125"
    spec["third_place_thresholds"][1]["probability"] = "0.7"
    with pytest.raises(ValueError):
        score_tournament_scenarios(spec)


def test_required_turnover_and_sanity_checks() -> None:
    assert required_volume(profit_gap="765000", net_profit_per_volume="0.02") == 38250000
    for bad in ["0", "-0.1", "NaN", "1.1"]:
        with pytest.raises(ValueError):
            required_volume(profit_gap="765000", net_profit_per_volume=bad)


def test_declared_official_api_cannot_be_laundered_from_public_web_or_other_hosts() -> None:
    invalid = [
        "https://sig.thesuper.market/leaderboards",
        "https://sig.thesuper.market/user/abc",
        "https://fake.sig.thesuper.market/api/v1/tournaments/cup/leaderboard",
        "http://sig.thesuper.market/api/v1/tournaments/cup/leaderboard",
        "https://sig.thesuper.market/api/v1/tournaments/cup/leaderboard#cached",
        "https://sig.thesuper.market/api/v1/tournaments/cup/portfolio/positions",
    ]
    for source_url in invalid:
        with pytest.raises(ValueError):
            normalize_official_payload(
                sample(), observed_at="2026-10-12T20:20:00Z", source_url=source_url
            )
    rows = normalize_official_payload(
        sample(),
        observed_at="2026-10-12T20:20:00Z",
        source_url="https://sig.thesuper.market/api/v1/tournaments/cup/leaderboard?period=all",
        raw_payload_sha256="a" * 64,
    )
    assert rows[0]["source_kind"] == "official_participant_api_declared_offline"
    assert rows[0]["raw_payload_sha256"] == "a" * 64
    with pytest.raises(ValueError):
        normalize_official_payload(
            sample(),
            observed_at="2026-10-12T20:20:00Z",
            source_url="https://sig.thesuper.market/api/v1/tournaments/cup/leaderboard",
            raw_payload_sha256="not-a-digest",
        )


def test_web_display_and_issue_transcription_provenance_are_never_combined() -> None:
    root = Path(__file__).resolve().parents[1]
    data = root / "data/research/leaderboard_intel_20261012"
    with (data / "public_web_leaderboard_top25_approx.csv").open(
        encoding="utf-8", newline=""
    ) as input_file:
        public_rows = list(csv.DictReader(input_file))
    with (data / "issue221_official_aggregate_20261012T2020Z.csv").open(
        encoding="utf-8", newline=""
    ) as input_file:
        issue_rows = list(csv.DictReader(input_file))
    assert len(public_rows) == 25
    assert len(issue_rows) == 3
    assert all(r["volume"] == "NOT_OBSERVABLE" for r in public_rows)
    assert all(r["settled_profit"] == "NOT_OBSERVABLE" for r in public_rows)
    assert all(r["precision"] == "issue_transcription_of_official_API" for r in issue_rows)
    assert all(r["public_profile_fills"] == "NOT_OBSERVABLE" for r in issue_rows)
    assert all("T" not in r["retrieval_date_utc"] for r in public_rows)
    assert all("T" in r["observed_at_utc_approx"] for r in issue_rows)


def test_offline_module_has_no_trading_or_network_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "src/predictions_cup/analysis/leaderboard_intel.py"
    syntax = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    prohibited = (
        "httpx", "requests", "aiohttp", "urllib.request",
        "predictions_cup.execution", "predictions_cup.maker", "predictions_cup.risk",
    )
    assert not any(
        module == banned or module.startswith(banned + ".")
        for module in imports
        for banned in prohibited
    )
