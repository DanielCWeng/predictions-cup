"""INVENTED two-sided archived snapshots: no original issuer/WS/fill proof."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.research.archived_depth_proxy import analyze_archived_proxy
from predictions_cup.research.native_paired_source_bridge import (
    PM_EXACT_SAME_YES,
    SIG_TOURNAMENT,
)
from predictions_cup.research.zero_fee_spread_latency import main

BASE = datetime(2026, 10, 18, 16, 50, tzinfo=UTC)


def at(ms: int) -> datetime:
    return BASE + timedelta(milliseconds=ms)


def sig(ms: int, seq: int, bid: str = "0.4", ask: str = "0.45") -> dict[str, object]:
    frame = {
        "delivery": {"revision": seq, "previousRevision": seq - 1,
                     "sourceSequenceFrom": seq, "sourceSequenceThrough": seq},
        "books": [{"exchangeId": 912,
                   "asOf": {"sequence": seq, "at": at(ms - 100).isoformat()},
                   "bids": [{"price": bid, "quantity": "2.9"},
                            {"price": "0.39", "quantity": "20.5"}],
                   "asks": [{"price": ask, "quantity": "2.9"},
                            {"price": "0.46", "quantity": "20.5"}]}],
    }
    return {"raw_json": json.dumps(frame), "local_receive_at": at(ms),
            "topic": f"tournament:{SIG_TOURNAMENT}:market:223",
            "connection_epoch": 1, "session_id": "fixture-1"}


def pm(ms: int, bid: str = "0.61", ask: str = "0.65",
       token: str | None = None) -> dict[str, object]:
    next_bid = str(Decimal(bid) - Decimal("0.01"))
    next_ask = str(Decimal(ask) + Decimal("0.01"))
    return {"token_id": token or PM_EXACT_SAME_YES["223"][2],
            "recorded_at": at(ms),
            "source_timestamp": at(ms - 100),
            "state_observed_at": at(ms - 100),
            "bids": [{"price": bid, "size": "3.5"},
                     {"price": next_bid, "size": "20.2"}],
            "asks": [{"price": ask, "size": "3.5"},
                     {"price": next_ask, "size": "20.2"}]}


def write(root: Path, stream: str, rows: list[dict[str, object]]) -> None:
    folder = root / stream
    folder.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), folder / "part.parquet")


def test_real_shaped_periodic_proxy_bins_whole_share_vwap_and_no_admission(
    tmp_path: Path,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(2000, 91), sig(4000, 92)])
    write(pr, "depth_snapshots", [pm(500), pm(1000)])
    out = analyze_archived_proxy(sr, pr, BASE, at(6000))
    rows = cast(list[dict[str, object]], out["per_market"])
    first = rows[0]
    assert first["n_raw_native_two_sided"] == 2
    assert first["n_pm_two_sided_periodic"] == 2
    assert first["n_matched_0_2s"] == 1
    assert first["n_matched_2_5s"] == 1
    feasible = cast(dict[str, dict[str, int]], first["n_feasible"])
    assert feasible["BUY_SIG_SELL_PM_YES"]["1"] == 2
    assert feasible["BUY_SIG_SELL_PM_YES"]["5"] == 2
    assert feasible["BUY_SIG_SELL_PM_YES"]["20"] == 2
    assert first["n_admitted_pairs"] == out["n_admitted_pairs"] == 0
    assert out["n_actual_executable_fills"] is None
    assert first["250ms_markout"] is None
    top = cast(list[dict[str, object]], out["top_five_measured_positive_depth_proxies"])
    assert top[0]["label"] == "PUBLISHER_FRESH_NONEXECUTABLE_PROXY_NOT_ADMITTED"
    assert top[0]["market"] == "223"
    assert Decimal(str(top[0]["maximum_positive_proxy_total"])) > 0
    placebo = cast(dict[str, object], out["wrong_market_placebo"])
    assert placebo["wrong_market_swapped_yes_pairs"] == 0


def test_duplicate_asof_and_reversed_native_dont_double_count(tmp_path: Path) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    duplicated = sig(1200, 91)
    duplicate_body = json.loads(str(duplicated["raw_json"]))
    duplicate_body["books"][0]["asOf"]["at"] = at(900).isoformat()
    duplicated["raw_json"] = json.dumps(duplicate_body)
    write(sr, "raw_events", [
        sig(1000, 91), duplicated,
        sig(1500, 90), sig(2500, 92),
    ])
    write(pr, "depth_snapshots", [pm(500)])
    out = analyze_archived_proxy(sr, pr, BASE, at(3000))
    first = cast(list[dict[str, object]], out["per_market"])[0]
    assert first["n_raw_native_two_sided"] == 4
    assert first["n_sig_duplicate_asof"] == 1
    assert first["n_sig_native_reversal"] == 1
    assert first["n_sig_unique_native"] == 2
    assert first["n_matched_total"] == 2


def test_only_measured_prices_rank_not_market_event_count(tmp_path: Path) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(1100, 91), sig(2200, 92)])
    write(pr, "depth_snapshots", [pm(900, bid="0.42", ask="0.70")])
    out = analyze_archived_proxy(sr, pr, BASE, at(3000))
    assert out["top_five_measured_positive_depth_proxies"] is None
    first = cast(list[dict[str, object]], out["per_market"])[0]
    ext = cast(dict[str, dict[str, dict[str, str | None]]],
               first["gap_extremes_per_share"])
    assert Decimal(str(ext["BUY_SIG_SELL_PM_YES"]["1"]["min"])) < 0


def test_unsupported_price_size_never_implicitly_partially_filled(tmp_path: Path) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    tiny = sig(1200, 91)
    body = json.loads(str(tiny["raw_json"]))
    for side in ("bids", "asks"):
        body["books"][0][side] = [{"price": "0.40" if side == "bids" else "0.45",
                                   "quantity": "1.9"}]
    tiny["raw_json"] = json.dumps(body)
    write(sr, "raw_events", [tiny])
    write(pr, "depth_snapshots", [pm(500)])
    out = analyze_archived_proxy(sr, pr, BASE, at(2000))
    first = cast(list[dict[str, object]], out["per_market"])[0]
    feasible = cast(dict[str, dict[str, int]], first["n_feasible"])
    assert feasible["BUY_SIG_SELL_PM_YES"]["1"] == 1
    assert feasible["BUY_SIG_SELL_PM_YES"]["5"] == 0
    assert feasible["BUY_SIG_SELL_PM_YES"]["10"] == 0


def test_wrong_market_placebo_is_separate_and_never_ranked(tmp_path: Path) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(1200, 91)])
    write(pr, "depth_snapshots", [
        pm(500, token=PM_EXACT_SAME_YES["224"][2]),
    ])
    out = analyze_archived_proxy(sr, pr, BASE, at(2000))
    assert out["top_five_measured_positive_depth_proxies"] is None
    placebo = cast(dict[str, object], out["wrong_market_placebo"])
    assert placebo["wrong_market_swapped_yes_pairs"] == 1
    assert out["n_admitted_pairs"] == 0


def test_proxy_cli_uses_existing_archive_without_seed_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(1200, 91)])
    write(pr, "depth_snapshots", [pm(500)])
    monkeypatch.setattr(sys, "argv", [
        "zero_fee_spread_latency", "--sig-root", str(sr),
        "--pm-root", str(pr), "--periodic-depth-proxy",
        "--start", BASE.isoformat(), "--end", at(2000).isoformat(),
    ])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["n_admitted_pairs"] == 0
    assert result["top_five_measured_positive_depth_proxies"][0]["label"] == (
        "PUBLISHER_FRESH_NONEXECUTABLE_PROXY_NOT_ADMITTED"
    )


def _proxy_for_single_pair(tmp_path: Path, *,
                           publisher_age_ms: int | None,
                           recorded_ms: int = 1000,
                           sig_ms: int = 2200,
                           bid: str = "0.61",
                           ask: str = "0.65",
                           source_override_ms: int | None = None
                           ) -> dict[str, object]:
    sig_root, pm_root = tmp_path / "sig", tmp_path / "pm"
    write(sig_root, "raw_events", [sig(sig_ms, 91)])
    periodic = pm(recorded_ms, bid=bid, ask=ask)
    if source_override_ms is not None:
        periodic["source_timestamp"] = at(source_override_ms)
    elif publisher_age_ms is None:
        periodic["source_timestamp"] = None
    else:
        periodic["source_timestamp"] = at(recorded_ms - publisher_age_ms)
    write(pm_root, "depth_snapshots", [periodic])
    return analyze_archived_proxy(sig_root, pm_root, BASE, at(sig_ms + 100))


@pytest.mark.parametrize("stale_at_pm_record_ms", [9218, 13593])
def test_real_reported_pm_source_ages_poison_positive_rank_despite_1200ms_receipt(
    tmp_path: Path, stale_at_pm_record_ms: int,
) -> None:
    # Ages are West's real measured numbers, books here are invented test data.
    result = _proxy_for_single_pair(
        tmp_path, publisher_age_ms=stale_at_pm_record_ms,
    )
    first = cast(list[dict[str, object]], result["per_market"])[0]
    assert first["n_matched_0_2s"] == 1
    assert result["top_five_all_age_nonexecutable_diagnostic"] is not None
    assert result["top_five_measured_positive_depth_proxies"] is None
    assert result["top_five_publisher_fresh_reference_proxies"] is None
    fresh = cast(dict[str, object], first["fresh_reference_proxy"])
    assert fresh["n_matched"] == 0
    rejected = cast(dict[str, int], fresh["rejected_matched_receipts"])
    assert rejected["stale_effective_pm_publisher_age"] == 1
    assert result["n_admitted_pairs"] == 0
    assert result["n_actual_executable_fills"] is None


@pytest.mark.parametrize(
    ("effective_age_ms", "should_pass"), [(5000, True), (5001, False)],
)
def test_apparent_pm_effective_publisher_age_exact_5s_boundary(
    tmp_path: Path, effective_age_ms: int, should_pass: bool,
) -> None:
    result = _proxy_for_single_pair(
        tmp_path, publisher_age_ms=effective_age_ms - 1200,
    )
    fresh_top = result["top_five_measured_positive_depth_proxies"]
    assert (fresh_top is not None) is should_pass
    first = cast(list[dict[str, object]], result["per_market"])[0]
    fresh = cast(dict[str, object], first["fresh_reference_proxy"])
    assert fresh["n_matched"] == (1 if should_pass else 0)
    assert result["n_publisher_fresh_matched_reference_pairs"] == (
        1 if should_pass else 0
    )


@pytest.mark.parametrize(
    ("publisher_ms", "expected_reason"),
    [(None, "missing_pm_publisher"), (1500, "future_pm_publisher")],
)
def test_missing_or_future_pm_publisher_cannot_enter_fresh_rank(
    tmp_path: Path, publisher_ms: int | None, expected_reason: str,
) -> None:
    result = _proxy_for_single_pair(
        tmp_path, publisher_age_ms=None,
        source_override_ms=publisher_ms,
    )
    first = cast(list[dict[str, object]], result["per_market"])[0]
    assert first["n_matched_total"] == 1
    assert result["top_five_all_age_nonexecutable_diagnostic"] is not None
    assert result["top_five_measured_positive_depth_proxies"] is None
    fresh = cast(dict[str, object], first["fresh_reference_proxy"])
    rejected = cast(dict[str, int], fresh["rejected_matched_receipts"])
    assert rejected[expected_reason] == 1
    assert fresh["n_matched"] == 0


def test_fresh_positive_and_negative_gap_size_price_extremes_stay_distinct(
    tmp_path: Path,
) -> None:
    # Positive correct-direction synthetic fixture: result is STILL only a
    # fresh *reference*, never an admitted live/source/fill economics result.
    positive = _proxy_for_single_pair(tmp_path / "positive",
                                      publisher_age_ms=1000)
    first = cast(list[dict[str, object]], positive["per_market"])[0]
    fresh = cast(dict[str, object], first["fresh_reference_proxy"])
    n = cast(dict[str, dict[str, int]], fresh["n_feasible"])
    assert n["BUY_SIG_SELL_PM_YES"]["20"] == 1
    assert n["BUY_PM_SELL_SIG_YES"]["20"] == 1
    positive_top = cast(list[dict[str, object]],
                        positive["top_five_measured_positive_depth_proxies"])
    assert positive_top[0]["label"] == (
        "PUBLISHER_FRESH_NONEXECUTABLE_PROXY_NOT_ADMITTED"
    )
    assert positive_top[0]["size_whole_shares"] == 20
    assert positive["source_admitted"] is False
    assert positive["n_admitted_pairs"] == 0

    negative = _proxy_for_single_pair(
        tmp_path / "negative", publisher_age_ms=500,
        bid="0.42", ask="0.70",
    )
    assert negative["top_five_measured_positive_depth_proxies"] is None
    neg_row = cast(list[dict[str, object]], negative["per_market"])[0]
    n_ref = cast(dict[str, object], neg_row["fresh_reference_proxy"])
    limits = cast(dict[str, dict[str, dict[str, str | None]]],
                  n_ref["gap_extremes_per_share"])
    totals = cast(dict[str, dict[str, dict[str, str | None]]],
                  n_ref["gap_extremes_total"])
    assert Decimal(str(limits["BUY_SIG_SELL_PM_YES"]["1"]["min"])) < 0
    assert Decimal(str(totals["BUY_SIG_SELL_PM_YES"]["20"]["min"])) < 0


def test_depth_infeasible_fresh_rows_do_not_rank_size_5_and_20(
    tmp_path: Path,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    sparse = sig(2000, 91)
    source = json.loads(str(sparse["raw_json"]))
    for side, price in (("bids", "0.40"), ("asks", "0.45")):
        source["books"][0][side] = [{"price": price, "quantity": "1.9"}]
    sparse["raw_json"] = json.dumps(source)
    write(sr, "raw_events", [sparse])
    write(pr, "depth_snapshots", [pm(1000)])
    result = analyze_archived_proxy(sr, pr, BASE, at(2500))
    row = cast(list[dict[str, object]], result["per_market"])[0]
    fresh = cast(dict[str, object], row["fresh_reference_proxy"])
    n = cast(dict[str, dict[str, int]], fresh["n_feasible"])
    infeasible = cast(dict[str, dict[str, int]], fresh["n_infeasible"])
    assert n["BUY_SIG_SELL_PM_YES"]["1"] == 1
    assert n["BUY_SIG_SELL_PM_YES"]["5"] == 0
    assert n["BUY_SIG_SELL_PM_YES"]["20"] == 0
    assert infeasible["BUY_SIG_SELL_PM_YES"]["20"] == 1


def test_both_placebos_apply_effective_publisher_freshness(
    tmp_path: Path,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(62000, 91)])
    other = pm(61000, token=PM_EXACT_SAME_YES["224"][2])
    shifted = pm(1000)
    for row in (other, shifted):
        row["source_timestamp"] = at(-10000)
    write(pr, "depth_snapshots", [other, shifted])
    result = analyze_archived_proxy(sr, pr, BASE, at(63000))
    wrong = cast(dict[str, object], result["wrong_market_placebo"])
    shifted_control = cast(dict[str, object], result["time_shift_placebo"])
    assert wrong["wrong_market_swapped_yes_pairs"] == 1
    assert shifted_control["shifted_60s_same_market_pairs"] == 1
    assert wrong["publisher_fresh_pairs"] == 0
    assert shifted_control["publisher_fresh_pairs"] == 0
    assert result["top_five_measured_positive_depth_proxies"] is None
