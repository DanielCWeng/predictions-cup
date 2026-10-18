"""INVENTED offline fixtures only. No venue/West access, trading or live data."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.research.native_paired_source_bridge import (
    PM_EXACT_SAME_YES,
    SIG_TOURNAMENT,
)
from predictions_cup.research.zero_fee_spread_latency import analyze, book, vwap


def ts(ms: int) -> datetime:
    return datetime(2026, 10, 18, 10, 0, tzinfo=UTC) + timedelta(milliseconds=ms)


def iso(ms: int) -> str:
    return ts(ms).isoformat().replace("+00:00", "Z")


def ladders(bid: str = "0.60", ask: str = "0.64") -> dict[str, object]:
    return {
        "bids": [{"price": bid, "quantity": "2.9"},
                 {"price": str(Decimal(bid) - Decimal("0.01")), "quantity": "20.9"}],
        "asks": [{"price": ask, "quantity": "2.9"},
                 {"price": str(Decimal(ask) + Decimal("0.01")), "quantity": "20.9"}],
    }


def sig_payload(rev: int, at: int) -> str:
    return json.dumps({
        "delivery": {"revision": rev, "previousRevision": rev - 1,
                     "sourceSequenceFrom": rev,
                     "sourceSequenceThrough": rev},
        "books": [{
            "exchangeId": 912, "asOf": {"at": iso(at), "sequence": rev + 90},
            **ladders("0.40", "0.45"),
        }],
    })


def write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def seed_event(ms: int) -> dict[str, object]:
    return {
        "schema": "alpha2-source-bridge-v1", "venue": "PM",
        "issuer_authenticated": False, "source_admitted": False,
        "paper_admitted": False,
        "kind": "REST_SEED_COMPLETE", "token_id": PM_EXACT_SAME_YES["223"][2],
        "connection_epoch": 1, "ws_local_ordinal": 0, "received_at": iso(ms),
        "full_depth": {
            "bids": [["0.61", "30"], ["0.60", "30"]],
            "asks": [["0.65", "30"], ["0.66", "30"]],
        },
    }


def ws_event(ms: int, ordinal: int, bid: str = "0.61") -> dict[str, object]:
    return {
        "schema": "alpha2-source-bridge-v1", "venue": "PM",
        "issuer_authenticated": False, "source_admitted": False,
        "paper_admitted": False,
        "kind": "WS_EVENT", "connection_epoch": 1, "ws_local_ordinal": ordinal,
        "received_at": iso(ms), "gap_or_unanchored": False,
        "uninitialized_tokens": [],
        "full_books": {
            PM_EXACT_SAME_YES["223"][2]: {
                "bids": [[bid, "30"], [str(Decimal(bid) - Decimal(".01")), "30"]],
                "asks": [["0.65", "30"], ["0.66", "30"]],
            },
        },
    }


def test_missing_inputs_are_data_gap_not_observed_zero_fill(tmp_path: Path) -> None:
    result = analyze(tmp_path / "absent", None, None)
    assert result["status"] == "DATA_GAP_NO_VERIFIED_PAIRS"
    assert result["n_sig_full_native_claims"] == 0
    assert result["n_pm_complete_seed"] == 0
    assert result["n_matched_pairs"] == 0
    assert result["top_five_conditional_upside"] == []
    assert result["n_fill_observed"] is None and result["n_attempted"] is None
    assert result["fees_sig"] == "0"


def test_real_shaped_raw_parquet_plus_pm_seeded_ws_and_size_aware_rank(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sig"
    token = PM_EXACT_SAME_YES["223"][2]
    write_parquet(root / "raw_events" / "part.parquet", [
        {"session_id": "invented", "connection_epoch": 1,
         "tournament_id": SIG_TOURNAMENT,
         "topic": f"tournament:{SIG_TOURNAMENT}:market:223",
         "local_receive_at": ts(1000), "raw_json": sig_payload(1, 900)},
        {"session_id": "invented", "connection_epoch": 1,
         "tournament_id": SIG_TOURNAMENT,
         "topic": f"tournament:{SIG_TOURNAMENT}:market:223",
         "local_receive_at": ts(2000), "raw_json": sig_payload(2, 1900)},
    ])
    pm_file = tmp_path / "pm-bridge.jsonl"
    pm_file.write_text(
        "\n".join(json.dumps(x) for x in [
            seed_event(100),
            ws_event(1100, 1),
            ws_event(1600, 2),
        ]) + "\n",
        encoding="utf-8",
    )
    result = analyze(root, None, pm_file)
    assert result["n_sig_full_native_claims"] == 2
    assert result["n_sig_valid_full"] == 2
    assert result["source_counts"]["sig_eligible_full"] == 1
    assert result["n_pm_complete_seed"] == 1
    assert result["n_matched_pairs"] == 1
    top = cast(list[dict[str, object]], result["top_five_conditional_upside"])
    assert top
    assert top[0]["market"] == "223"
    assert top[0]["direction"] == "BUY_SIG_SELL_PM_YES"
    assert top[0]["n"] == 20
    assert top[0]["effective_whole_size"] == 22
    assert Decimal(str(top[0]["conditional_upside_zero_fee"])) > 0
    assert top[0]["observed_tail_p05"] is None
    assert all(v is None for v in
               cast(dict[str, object], top[0]["markout"]).values())
    assert token == PM_EXACT_SAME_YES["223"][2]


def test_pm_periodic_depth_never_proves_seeding_or_pairing(tmp_path: Path) -> None:
    pm_root = tmp_path / "pm"
    write_parquet(pm_root / "depth_snapshots" / "part.parquet", [
        {"token_id": PM_EXACT_SAME_YES["223"][2], "recorded_at": ts(2000),
         "bids": [{"price": "0.61", "size": "3"}],
         "asks": [{"price": "0.65", "size": "3"}]},
    ])
    result = analyze(None, pm_root, None)
    assert result["source_counts"]["pm_periodic_two_sided_unproven"] == 1
    assert result["n_pm_complete_seed"] == 0
    assert result["n_matched_pairs"] == 0


def test_1000ms_1hz_is_not_proof_of_250ms_or_1s_markout() -> None:
    # A 1Hz observation series is too sparse to bracket any requested target
    # in <=100ms. Even a frame precisely at +1s has unknown interim state.
    from predictions_cup.research.zero_fee_spread_latency import horizon

    b = book(ladders())
    rows: list[dict[str, object]] = [
        {"at": ts(i * 1000), "book": b, "epoch": 1, "revision": i}
        for i in range(5)
    ]
    for ms in (250, 1000, 2000):
        assert horizon(rows, ts(1000), ms) is None


def test_integer_floor_at_each_level_no_fraction_pooling() -> None:
    b = book(ladders())
    assert vwap(b["asks"], 1) == Decimal("0.64")
    assert vwap(b["asks"], 5) == Decimal("0.646")
    assert vwap(b["asks"], 23) is None


def test_proxy_scalar_not_ranked(tmp_path: Path) -> None:
    sig_root, pm_root = tmp_path / "sig", tmp_path / "pm"
    write_parquet(sig_root / "normalized_events" / "part.parquet", [
        {"exchange_id": "912", "event_type": "BBO_SNAPSHOT",
         "observed_at": ts(2000), "best_bid": "0.42", "best_ask": "0.44"},
    ])
    write_parquet(pm_root / "observations" / "part.parquet", [
        {"token_id": PM_EXACT_SAME_YES["223"][2], "observed_at": ts(1900),
         "best_bid": "0.61", "best_ask": "0.65", "book_valid": True},
    ])
    result = analyze(sig_root, pm_root, None)
    assert result["n_matched_pairs"] == 0
    assert result["top_five_conditional_upside"] == []
    proxies = cast(list[dict[str, object]], result["scalar_proxy"])
    assert len(proxies) == 1
    assert proxies[0]["label"] == "NONEXECUTABLE_PROXY_NO_DEPTH_NO_FILL"
    assert proxies[0]["max_scalar_gap_per_share"] == "0.170000"
