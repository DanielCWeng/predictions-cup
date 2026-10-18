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

from predictions_cup.research.archived_depth_proxy import (
    analyze_archived_proxy,
    analyze_short_window_pilot,
)
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


# PILOT fixtures use the EXACT Parquet schema, quote parser, frozen tokens,
# source publisher timestamps and n-lot VWAP machinery of the full reader.
def _dated(root: Path, stream: str, timestamp: datetime,
           rows: list[dict[str, object]]) -> None:
    directory = root / stream / timestamp.strftime("%Y/%m/%d/%H")
    directory.mkdir(parents=True, exist_ok=True)
    name = timestamp.strftime("%Y%m%dT%H%M%SZ") + ".parquet"
    pq.write_table(pa.Table.from_pylist(rows), directory / name)


def _pilot_fixture(tmp_path: Path) -> tuple[Path, Path, datetime, datetime]:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    start, end = at(0), at(9 * 60000)
    # 5.000s inclusive at SIG receipt, PM recorded <=2s before SIG.
    # Native monotonic sequences and same-market orientation stay the same.
    sig_rows = [
        sig(4000, 21), sig(10000, 22), sig(16000, 23),
        sig(22000, 24), sig(28000, 25), sig(64000, 26),
    ]
    sig_rows.append(sig(66000, 26))  # publisher/sequence regression
    bad = sig(70000, 27)
    badframe = json.loads(str(bad["raw_json"]))
    badframe["books"][0]["asOf"]["at"] = at(59000).isoformat()
    bad["raw_json"] = json.dumps(badframe)
    sig_rows.append(bad)  # stale native publisher
    pm_rows = [
        pm(2500), pm(8500), pm(14500), pm(20500), pm(26500),
        pm(63000), pm(65000),
        pm(2500, token=PM_EXACT_SAME_YES["224"][2]),
        pm(8500, token=PM_EXACT_SAME_YES["224"][2]),
    ]
    pm_rows[0]["source_timestamp"] = at(-1000)  # effective 5.000s exact
    pm_rows[1]["source_timestamp"] = at(4999)  # effective 5.001s
    pm_rows[2]["source_timestamp"] = at(14000)  # fresh
    pm_rows[3]["source_timestamp"] = at(20501)  # future publisher
    pm_rows[4]["source_timestamp"] = None  # missing original publisher
    pm_rows[5]["source_timestamp"] = at(62600)  # fresh
    pm_rows[6]["source_timestamp"] = at(56000)  # newest is stale
    # Deliberately place an older fresh PM at 63s to confirm no fallback.
    # Wrong-market and time-shift placebos reuse identical freshness logic.
    _dated(sr, "raw_events", start, sig_rows)
    _dated(pr, "depth_snapshots", start, pm_rows)
    return sr, pr, start, end


def test_pilot_equals_full_parser_and_freshness_exactly(
    tmp_path: Path,
) -> None:
    sr, pr, start, end = _pilot_fixture(tmp_path)
    full = analyze_archived_proxy(sr, pr, start, end)
    pilot = analyze_short_window_pilot(sr, pr, start, end)
    assert pilot["classification"] == "WINDOW_LIMITED_ONLY_NONEXECUTABLE"
    assert pilot["n_publisher_fresh_matched_reference_pairs"] == (
        full["n_publisher_fresh_matched_reference_pairs"]
    )
    a = cast(list[dict[str, object]], full["per_market"])
    b = cast(list[dict[str, object]], pilot["per_market"])
    for i in range(4):
        for key in ("n_sig_unique_native", "n_sig_native_reversal",
                    "n_sig_clock_or_depth_rejected", "n_matched_total"):
            assert b[i][key] == a[i][key]
        # Includes EXACT 1/5/10/20 sizes, both venue orientations,
        # positive/negative n×gap, and rejected source-clock reasons.
        for key in ("fresh_reference_proxy", "all_age_nonexecutable_diagnostic"):
            assert b[i][key] == a[i][key]
    assert pilot["wrong_market_placebo"] == full["wrong_market_placebo"]
    assert pilot["time_shift_placebo"] == full["time_shift_placebo"]
    assert cast(list[dict[str, object]], b)[0]["fresh_reference_proxy"] == (
        a[0]["fresh_reference_proxy"]
    )
    assert pilot["n_admitted_pairs"] == 0
    assert pilot["real_after_spread_fill_pnl"] is None
    assert pilot["other_unseen_intervals"] == "UNKNOWN_NOT_SCANNED"
    assert pilot["top_five_publisher_fresh_reference_proxies"] is not None
    # stdout never includes exact raw identifiers or per-event timestamps
    serialized = json.dumps(pilot)
    assert PM_EXACT_SAME_YES["223"][2] not in serialized
    assert '"sample_receive_utc"' not in serialized


def test_pilot_never_rescues_stale_latest_pm_or_trades_on_missing_proof(
    tmp_path: Path,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(6200, 11)])
    earlier = pm(1000)
    stale_nearest = pm(5500)
    stale_nearest["source_timestamp"] = at(-10000)
    write(pr, "depth_snapshots", [earlier, stale_nearest])
    pilot = analyze_short_window_pilot(sr, pr, at(0), at(9000))
    row = cast(list[dict[str, object]], pilot["per_market"])[0]
    fresh = cast(dict[str, object], row["fresh_reference_proxy"])
    rejected = cast(dict[str, int], fresh["rejected_matched_receipts"])
    assert row["n_matched_total"] == 1
    assert rejected["stale_effective_pm_publisher_age"] == 1
    assert fresh["n_matched"] == 0
    assert pilot["top_five_publisher_fresh_reference_proxies"] is None


def test_pilot_single_window_cap_and_unknown_prestart_pm_context(
    tmp_path: Path,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [sig(1000, 1)])
    write(pr, "depth_snapshots", [pm(6000)])
    observed = analyze_short_window_pilot(sr, pr, at(0), at(9000))
    assert observed["n_boundary_prior_pm_context_unknown"] == 1
    assert observed["status"] == "WINDOW_COMPLETE_WITH_UNKNOWN_PRESTART_PM_CONTEXT"
    assert observed["other_unseen_intervals"] == "UNKNOWN_NOT_SCANNED"
    with pytest.raises(ValueError, match="MAX_10MIN"):
        analyze_short_window_pilot(sr, pr, at(0), at(600001))


def test_pilot_prunes_dated_shards_before_parquet_open_and_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    start, end = at(300000), at(360000)
    _dated(sr, "raw_events", at(300000), [sig(305000, 1)])
    _dated(pr, "depth_snapshots", at(300000), [pm(301000)])
    for i in (0, 600000, 1200000):
        _dated(sr, "raw_events", at(i), [sig(i + 2000, 2)])
        _dated(pr, "depth_snapshots", at(i), [pm(i + 1000)])
    real = pq.ParquetFile
    paths: list[str] = []
    def only_selected(path: str | Path, *args: object,
                      **kwargs: object) -> pq.ParquetFile:
        paths.append(str(path))
        if "20261018T" in str(path) and "T1655" not in str(path):
            raise AssertionError("pilot opened a nonselected minute Parquet")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(pq, "ParquetFile", only_selected)
    result = analyze_short_window_pilot(sr, pr, start, end)
    assert result["input_counts"]["raw_events_shards"] == 1
    assert result["input_counts"]["depth_snapshots_shards"] == 1
    assert len(paths) == 2


def test_pilot_cli_stdout_is_aggregate_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sr, pr, start, end = _pilot_fixture(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "zero_fee_spread_latency", "--sig-root", str(sr), "--pm-root", str(pr),
        "--short-window-pilot", "--start", start.isoformat(),
        "--end", end.isoformat(),
    ])
    main()
    out = capsys.readouterr().out
    assert "WINDOW_LIMITED_ONLY" in out
    assert "UNKNOWN_NOT_SCANNED" in out
    assert PM_EXACT_SAME_YES["223"][2] not in out
    assert "sample_receive_utc" not in out
    assert str(sr) not in out


def test_off_west_synthetic_full_versus_10min_cpu_wall_rss_shards(
    tmp_path: Path,
) -> None:
    """CI synthetic-only benchmark, separate child peak RSS/CPU per mode.

    Emits measured Linux CI counters as a pytest warning; does NOT predict
    West CPU PSI or grant any host action permission.
    """
    import subprocess
    import warnings

    if sys.platform != "linux":
        pytest.skip("Linux process CPU and peak RSS benchmark")
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    # Pre-price rule: take [17:20, 17:30) 30min after BASE 16:50Z.
    # 60 one-minute shards over original 2h horizon, 48 unrelated SIG
    # events per shard; ensures both readers scan genuine file metadata.
    for minute in range(60):
        offset = minute * 60000
        instant = at(offset)
        active = sig(offset + 3000, minute + 1)
        junk = [{
            "raw_json": json.dumps(
                {"books": [{"exchangeId": 123456, "bids": [], "asks": []}]}
            ),
            "local_receive_at": at(offset + 4000 + j),
            "connection_epoch": 1,
            "session_id": "irrelevant-native-topic",
            "topic": "irrelevant:other",
        } for j in range(48)]
        _dated(sr, "raw_events", instant, [active, *junk])
        _dated(pr, "depth_snapshots", instant, [pm(offset + 1000)])
    worker = """
import json, resource, sys, time
from pathlib import Path
from predictions_cup.research.archived_depth_proxy import (
    analyze_archived_proxy, analyze_short_window_pilot,
)
from predictions_cup.research.zero_fee_spread_latency import utc
sig, pm, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
begin = time.perf_counter()
cpu = time.process_time()
if mode == 'full':
    result = analyze_archived_proxy(
        sig, pm, utc('2026-10-18T16:50:00Z'), utc('2026-10-18T18:50:00Z'),
    )
else:
    result = analyze_short_window_pilot(
        sig, pm, utc('2026-10-18T17:20:00Z'), utc('2026-10-18T17:30:00Z'),
    )
c = result['input_counts']
print(json.dumps({
    'mode': mode,
    'cpu_s': round(time.process_time() - cpu, 5),
    'wall_s': round(time.perf_counter() - begin, 5),
    'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    'sig_shards': c['raw_events_shards'],
    'pm_shards': c['depth_snapshots_shards'],
    'sig_rows_scanned': c['raw_events_scanned'],
    'pm_rows_scanned': c['depth_snapshots_scanned'],
    'sig_target_rows': c.get('raw_events_retained_exact_rows'),
    'fresh_matched': result['n_publisher_fresh_matched_reference_pairs'],
}))
"""
    measurements = []
    for mode in ("full", "pilot"):
        completed = subprocess.run(
            [sys.executable, "-c", worker, str(sr), str(pr), mode],
            check=True, capture_output=True, text=True, timeout=30,
        )
        measurements.append(json.loads(completed.stdout))
    full, pilot = measurements
    assert pilot["sig_shards"] < full["sig_shards"]
    assert pilot["pm_shards"] < full["pm_shards"]
    assert pilot["sig_rows_scanned"] < full["sig_rows_scanned"]
    assert pilot["pm_rows_scanned"] < full["pm_rows_scanned"]
    assert pilot["sig_target_rows"] is not None
    # Emit actual runner measurements only; no threshold/ratio as if West.
    warnings.warn(
        "ALPHA2_OFF_WEST_SYNTHETIC_RESOURCE_REPORT "
        + json.dumps(measurements, sort_keys=True),
        RuntimeWarning, stacklevel=2,
    )
