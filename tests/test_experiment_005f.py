from __future__ import annotations

import numpy as np
import pytest

from predictions_cup.learning.microstructure_atlas import (
    NS,
    L2Book,
    benjamini_hochberg,
    canonical_yes_bbo,
    cont_ofi,
    depth_metrics,
    ewma,
    future_event_index,
    primitive_microprice,
    split_bounds,
    split_for_time,
)


def test_no_token_is_oriented_to_yes() -> None:
    out = canonical_yes_bbo("No", 0.30, 0.40)
    assert out.bid == pytest.approx(0.60)
    assert out.ask == pytest.approx(0.70)


def test_same_time_absolute_changes_are_order_free_when_nonconflicting() -> None:
    book = L2Book.empty()
    book.snapshot([(0.40, 10), (0.39, 5)], [(0.60, 8), (0.61, 7)], 1 * NS)
    current, flags = book.apply_change_group(
        [
            {"side": "BUY", "price": 0.40, "size": 0, "best_bid": 0.39, "best_ask": 0.60},
            {"side": "SELL", "price": 0.60, "size": 11, "best_bid": 0.39, "best_ask": 0.60},
        ],
        2 * NS,
    )
    assert current is not None
    assert current.bid == pytest.approx(0.39)
    assert current.ask == pytest.approx(0.60)
    assert flags["genuine_bid_change"] is True
    assert flags["same_timestamp_ambiguity"] is False


def test_conflicting_same_level_update_invalidates_reconstructed_depth() -> None:
    book = L2Book.empty()
    book.snapshot([(0.40, 10)], [(0.60, 8)], 1 * NS)
    _, flags = book.apply_change_group(
        [
            {"side": "BUY", "price": 0.40, "size": 7, "best_bid": 0.40, "best_ask": 0.60},
            {"side": "BUY", "price": 0.40, "size": 9, "best_bid": 0.40, "best_ask": 0.60},
        ],
        2 * NS,
    )
    assert flags["same_timestamp_ambiguity"] is True
    assert flags["depth_valid"] is False


def test_conflicting_post_change_bbos_are_ambiguous() -> None:
    book = L2Book.empty()
    book.snapshot([(0.40, 10)], [(0.60, 8)], 1 * NS)
    _, flags = book.apply_change_group(
        [
            {"side": "BUY", "price": 0.40, "size": 9, "best_bid": 0.40, "best_ask": 0.60},
            {"side": "SELL", "price": 0.60, "size": 7, "best_bid": 0.40, "best_ask": 0.61},
        ],
        2 * NS,
    )
    assert flags["same_timestamp_ambiguity"] is True


def test_cont_ofi_handles_price_level_changes() -> None:
    same = cont_ofi(0.40, 0.60, 10, 8, 0.40, 0.60, 12, 5)
    assert same == pytest.approx((12 - 10) - (5 - 8))
    bid_up = cont_ofi(0.40, 0.60, 10, 8, 0.41, 0.60, 4, 5)
    assert bid_up == pytest.approx(4 - (5 - 8))


def test_microprice_is_algebraically_consistent() -> None:
    value = primitive_microprice(0.40, 0.60, 15, 5)
    assert value == pytest.approx(0.55)


def test_depth_metrics_and_impact_use_observable_levels() -> None:
    book = L2Book.empty()
    book.snapshot(
        [(0.49, 20), (0.48, 30), (0.45, 100)],
        [(0.51, 10), (0.52, 30), (0.55, 100)],
        NS,
    )
    metrics = depth_metrics(book)
    assert metrics["depth_1c"] == pytest.approx(30)
    assert metrics["depth_5c"] == pytest.approx(290)
    assert metrics["buy_impact_q10"] == pytest.approx(0.01)


def test_chronological_split_has_purge_gaps() -> None:
    bounds = split_bounds(0, 1000 * NS)
    assert split_for_time(100 * NS, bounds) == "TRAIN"
    assert split_for_time(599 * NS, bounds) is None
    assert split_for_time(700 * NS, bounds) is None
    assert split_for_time(750 * NS, bounds) is None
    assert split_for_time(900 * NS, bounds) == "HOLDOUT"


def test_future_event_index_counts_only_genuine_events() -> None:
    mask = np.asarray([False, True, False, True, True, False])
    assert future_event_index(mask, 0, 1) == 1
    assert future_event_index(mask, 0, 3) == 4
    assert future_event_index(mask, 4, 1) is None


def test_ewma_respects_irregular_event_time() -> None:
    out = ewma([0.0, 1.0, 1.0], [0, 5 * NS, 10 * NS], 5)
    assert out[1] == pytest.approx(0.5)
    assert out[2] == pytest.approx(0.75)


def test_bh_adjustment_is_monotone() -> None:
    result = benjamini_hochberg({"a": 0.001, "b": 0.02, "c": 0.5}, q=0.10)
    assert result["a"]["reject"] is True
    assert result["b"]["p_bh"] >= result["a"]["p_bh"]
