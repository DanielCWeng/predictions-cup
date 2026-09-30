from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.analysis.structural_shadow import (
    QuotePoint,
    _seat_shocks,
    run,
)


def _write(root: Path, stream: str, rows: list[dict[str, object]]) -> None:
    directory = root / stream
    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), directory / "part.parquet")


def test_seat_distribution_shock_requires_coherent_direction_agreement() -> None:
    tokens = [str(index) for index in range(11)]
    before = [1.0 / 11.0] * 11
    after = before.copy()
    after[0] -= 0.05
    after[-1] += 0.05

    quotes: dict[str, list[QuotePoint]] = {}
    for token, p0, p1 in zip(tokens, before, after, strict=True):
        quotes[token] = [
            QuotePoint(time_ns=1_000_000_000, bid=p0, ask=p0),
            QuotePoint(time_ns=2_000_000_000, bid=p1, ask=p1),
        ]

    candidate = {
        "id": "COUNT",
        "source_tokens": [{"token_id": token} for token in tokens],
        "thresholds": {
            "scalar_t50_qp_abs_change": 0.01,
            "scalar_t50_kl_abs_change": 0.01,
            "clr_qp_l2": 0.01,
            "clr_kl_l2": 0.01,
        },
    }
    shocks = _seat_shocks(candidate, quotes, freshness_seconds=5)

    assert len(shocks) == 1
    assert shocks[0].direction == 1
    assert shocks[0].details["qp_kl_direction_agree"] is True


def test_structural_shadow_binary_markout_is_executable_side_and_shadow_only(
    tmp_path: Path,
) -> None:
    pm_root = tmp_path / "pm"
    sig_root = tmp_path / "sig"
    out = tmp_path / "out"
    at = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)

    _write(
        pm_root,
        "observations",
        [
            {
                "token_id": "pm-core4",
                "observed_at": at,
                "best_bid": 0.39,
                "best_ask": 0.41,
                "book_valid": True,
            },
            {
                "token_id": "pm-core4",
                "observed_at": at + timedelta(seconds=10),
                "best_bid": 0.49,
                "best_ask": 0.51,
                "book_valid": True,
            },
        ],
    )
    _write(
        sig_root,
        "normalized_events",
        [
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "842",
                "observed_at": at,
                "best_bid": 0.48,
                "best_ask": 0.50,
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "842",
                "observed_at": at + timedelta(seconds=9),
                "best_bid": 0.49,
                "best_ask": 0.51,
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "842",
                "observed_at": at + timedelta(seconds=15),
                "best_bid": 0.54,
                "best_ask": 0.56,
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "842",
                "observed_at": at + timedelta(seconds=40),
                "best_bid": 0.55,
                "best_ask": 0.57,
            },
            {
                "event_type": "BBO_SNAPSHOT",
                "exchange_id": "842",
                "observed_at": at + timedelta(seconds=310),
                "best_bid": 0.56,
                "best_ask": 0.58,
            },
        ],
    )
    config = {
        "schema_version": 1,
        "experiment_id": "TEST",
        "mode": "SHADOW_ONLY",
        "data001_used": False,
        "source_freshness_seconds": 5,
        "target_pre_shock_freshness_seconds": 30,
        "max_horizon_sampling_delay_seconds": 30,
        "independent_shock_separation_seconds": 30,
        "horizons_seconds": [5, 30, 300],
        "candidates": [
            {
                "id": "CORE4",
                "type": "binary_probability",
                "gate_eligible": True,
                "source_tokens": [
                    {
                        "market_id": "1178882",
                        "label": "CORE4",
                        "token_id": "pm-core4",
                    }
                ],
                "target_exchange_ids": [
                    {
                        "exchange_id": "842",
                        "target_sig_market_id": "153",
                        "direction_multiplier": 1,
                    }
                ],
                "thresholds": {"binary_clr_l2": 0.01},
            }
        ],
        "live_gate": {
            "minimum_independent_shocks": 20,
            "minimum_distinct_days_or_event_windows": 2,
            "require_positive_increment_vs_target_only_reversal": True,
            "require_positive_side_aware_markout_after_fees_slippage": True,
            "require_no_single_shock_dominance": True,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = run(
        sig_root=sig_root,
        polymarket_root=pm_root,
        config_path=config_path,
        output_root=out,
    )

    assert result["mode"] == "SHADOW_ONLY"
    assert result["promotion_allowed"] is False
    assert result["independent_shocks"] == 1
    assert result["markout_rows"] == 3
    five = next(
        row
        for row in result["summaries"]
        if row["horizon_seconds"] == 5
    )
    assert five["gate_status"] == "AWAIT_LIVE_SUPPORT"
    assert abs(float(five["mean_gross_top_of_book_markout"]) - 0.03) < 1e-12
    assert abs(float(five["mean_increment_vs_target_reversal"]) - 0.10) < 1e-12
    assert (out / "structural_shocks.csv").exists()
    assert (out / "structural_shadow_markouts.csv").exists()
    assert (out / "structural_shadow_result.json").exists()
