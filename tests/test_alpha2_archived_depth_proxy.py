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
    return {"token_id": token or PM_EXACT_SAME_YES["223"][2],
            "recorded_at": at(ms),
            "source_timestamp": at(ms - 100),
            "state_observed_at": at(ms - 100),
            "bids": [{"price": bid, "size": "3.5"},
                     {"price": "0.60", "size": "20.2"}],
            "asks": [{"price": ask, "size": "3.5"},
                     {"price": "0.66", "size": "20.2"}]}


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
    assert top[0]["label"] == "NONEXECUTABLE_PROXY_NOT_ADMITTED"
    assert top[0]["market"] == "223"
    assert Decimal(str(top[0]["maximum_positive_proxy_total"])) > 0
    placebo = cast(dict[str, object], out["wrong_market_placebo"])
    assert placebo["wrong_market_swapped_yes_pairs"] == 0


def test_duplicate_asof_and_reversed_native_dont_double_count(tmp_path: Path) -> None:
    sr, pr = tmp_path / "sig", tmp_path / "pm"
    write(sr, "raw_events", [
        sig(1000, 91), sig(1200, 91),
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
    assert result["top_five_measured_positive_depth_proxies"][0]["label"].startswith(
        "NONEXECUTABLE"
    )
