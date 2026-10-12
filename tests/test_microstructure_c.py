"""Deterministic economic falsification of Lane C's research-only pathways."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from predictions_cup.replay.microstructure_c import CANDIDATES, Snapshot, evaluate

START = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def snap(seconds: float = 0, *, bid: str = "0.40", ask: str = "0.60",
         bid_qty: int = 20, ask_qty: int = 20,
         trusted: bool = True, complete: bool = True) -> dict[str, Any]:
    return {
        "observed_at": (START + timedelta(seconds=seconds)).isoformat(),
        "bids": [[bid, bid_qty]], "asks": [[ask, ask_qty]],
        "trusted": trusted, "complete": complete,
    }


def episode(*, side: str = "YES") -> dict[str, Any]:
    row: dict[str, Any] = {
        "episode_id": "e-1", "event_id": "evt-1", "sig_exchange_id": "101",
        "decision_at": START.isoformat(),
        "features_available_at": START.isoformat(),
        "feature_window_end": START.isoformat(),
        "exact_mapping_verified": True, "paired_depth_trusted": True,
        "mapping_direction": "SAME",
        "features": {"pm_minus_sig": 0.10 if side == "YES" else -0.10,
                     "verified_source_lag": True, "observable_external_update": True,
                     "reference_orientation": "SIG_YES",
                     "imbalance": 0.7,
                     "spread": 0.20, "aggressive_trade_sign": -1,
                     "replenishment": 0.9, "short_discovery": 0.1,
                     "inventory_yes": -10, "frozen_005i_signed": 1,
                     "frozen_005i_attested": True},
        "decision_book": snap(),
        "arrivals": {"1x": snap(0.2), "2x": snap(0.4), "5x": snap(0.8)},
        "exits": {
            "5": snap(5.2, bid="0.70", ask="0.80"),
            "30": snap(30.2, bid="0.70", ask="0.80"),
            "300": snap(300.2, bid="0.70", ask="0.80"),
            "3600": snap(3600.2, bid="0.70", ask="0.80"),
        },
    }
    return row


def result(row: dict[str, Any], strategy: str, **kw: Any) -> dict[str, Any]:
    out = evaluate([row], gross_free=Decimal(10), cash_free=Decimal(10), **kw)
    return next(x for x in out["ledger"] if x["strategy"] == strategy)


def test_only_upper_bound_from_unobserved_ioc() -> None:
    row = episode()
    output = evaluate([row], gross_free=Decimal(10), cash_free=Decimal(10))
    trade = next(x for x in output["ledger"] if x["strategy"] == "DIRECT_FV")
    assert trade["status"] == "IOC_TOUCH_UPPER_BOUND"
    assert trade["conditional_net_5m"] is not None
    assert trade["conditional_net_5m"] > 0
    assert trade["evidence_class"] == "HYPOTHETICAL_IOC_TOUCH"
    assert output["scorecard"]["DIRECT_FV"]["expected_net_pnl"] is None
    assert output["scorecard"]["DIRECT_FV"]["measured_realized_net_pnl"] is None
    assert output["scorecard"]["DIRECT_FV"]["classification"] == "INSUFFICIENT_DATA"
    assert output["actual_orders_sent"] == 0
    assert len(output["ledger"]) == len(CANDIDATES)


def test_no_side_is_one_exchange_complement_and_unprofitable() -> None:
    row = episode(side="NO")
    trade = result(row, "STALE_QUOTE")
    assert trade["side"] == "NO"
    assert trade["status"] == "IOC_TOUCH_UPPER_BOUND"
    # NO buys from YES bids @0.4 -> NO paid 0.6;
    # NO sells to YES asks @0.8 -> NO receives 0.2.
    assert trade["conditional_net_5m"] < -0.4


def test_after_latency_price_outside_decision_limit_never_fills() -> None:
    row = episode()
    row["arrivals"] = {"1x": snap(0.2, bid="0.61", ask="0.65")}
    trade = result(row, "DIRECT_FV")
    assert trade["status"] == "REJECTED"
    assert trade["conditional_net_5m"] is None


def test_future_or_stale_book_is_not_a_quote() -> None:
    row = episode()
    row["decision_book"] = snap(1.0)
    assert result(row, "DIRECT_FV")["reason"] == "untrusted_or_stale_decision_depth"
    row["decision_book"] = snap()
    row["arrivals"] = {"1x": snap(5.0)}
    assert result(row, "DIRECT_FV")["reason"] == "no_placeable_post_latency_arrival"


@pytest.mark.parametrize("key", ["exact_mapping_verified", "paired_depth_trusted"])
def test_mapping_and_depth_default_deny(key: str) -> None:
    row = episode()
    row[key] = False
    assert result(row, "DIRECT_FV")["status"] == "REJECTED"


def test_gross_and_cash_reservation_are_separate() -> None:
    row = episode()
    out = evaluate([row], gross_free=Decimal(0), cash_free=Decimal(100))
    assert next(x for x in out["ledger"] if x["strategy"] == "DIRECT_FV")[
        "reason"] == "gross_cap_full"
    out = evaluate([row], gross_free=Decimal(10), cash_free=Decimal("0.10"))
    assert next(x for x in out["ledger"] if x["strategy"] == "DIRECT_FV")[
        "reason"] == "insufficient_worst_case_cash"


def test_no_future_close_book_means_no_markout_not_free_money() -> None:
    row = episode()
    row["exits"] = {}
    trade = result(row, "DIRECT_FV")
    assert trade["conditional_net_5m"] is None
    assert trade["conditional_capital_seconds"] is None


def test_arrival_stress_absent_is_unknown_not_equal_to_primary() -> None:
    row = episode()
    row["arrivals"] = {"1x": snap(0.2)}
    item = result(row, "DIRECT_FV")
    assert item["arrival_stress_net_5m"]["1x"] is not None
    assert item["arrival_stress_net_5m"]["2x"] is None
    assert item["arrival_stress_net_5m"]["5x"] is None


def test_passive_never_infers_fill_from_book_or_trade_prints() -> None:
    row = episode()
    item = result(row, "PASSIVE_SPREAD")
    assert item["reason"] == "passive_queue_fill_unobserved"
    assert item["observed_passive_fill"] is None


def test_acknowledged_zero_fill_counts_in_denominator_not_pnl() -> None:
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "o-1", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "limit_price": "0.40",
        "side": "YES", "action": "buy", "ack_at": START.isoformat(),
        "filled_qty": "0", "fee_paid": "0",
        "terminal_state": "CANCELLED",
        "resolved_at": (START + timedelta(seconds=2)).isoformat(),
    }}
    out = evaluate([row], gross_free=Decimal(10), cash_free=Decimal(10))
    trade = next(x for x in out["ledger"] if x["strategy"] == "PASSIVE_SPREAD")
    assert trade["status"] == "NO_FILL"
    assert out["scorecard"]["PASSIVE_SPREAD"]["observed_passive_fill_probability"] == 0
    assert out["scorecard"]["PASSIVE_SPREAD"]["measured_realized_net_pnl"] is None


def test_observed_partial_fill_not_upscaled_to_order_quantity() -> None:
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "o-2", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "limit_price": "0.40",
        "side": "YES", "action": "buy", "ack_at": START.isoformat(),
        "fill_at": (START + timedelta(seconds=1)).isoformat(),
        "filled_qty": "0.5", "fill_price": "0.40", "fee_paid": "0.001",
        "terminal_state": "PARTIAL_CANCELLED",
        "resolved_at": (START + timedelta(seconds=2)).isoformat(),
    }}
    # We allow fractional PARTIAL fills in historical data, not in order size.
    row["exits"] = {"300": snap(301.2, bid="0.70", ask="0.80")}
    item = result(row, "PASSIVE_SPREAD")
    assert item["available_size"] == 0.5
    assert item["evidence_class"] == "OBSERVED_ENTRY_HYPOTHETICAL_EXIT"
    assert item["conditional_net_5m"] is not None
    assert item["conditional_net_5m"] < 0.5


def test_no_hypothetical_fills_after_confirmed_cancel() -> None:
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "o-3", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "limit_price": "0.40",
        "side": "YES", "action": "buy", "ack_at": START.isoformat(),
        "fill_at": (START + timedelta(seconds=5)).isoformat(),
        "cancel_ack_at": (START + timedelta(seconds=4)).isoformat(),
        "filled_qty": "1", "fill_price": "0.40", "fee_paid": "0",
        "terminal_state": "FILLED",
        "resolved_at": (START + timedelta(seconds=6)).isoformat(),
    }}
    with pytest.raises(ValueError, match="after confirmed cancellation"):
        result(row, "PASSIVE_SPREAD")


def test_duplicate_order_evidence_rejected_across_episodes() -> None:
    a = episode()
    b = deepcopy(a)
    b["episode_id"] = "e-2"
    b["decision_at"] = (START + timedelta(minutes=6)).isoformat()
    # Provide b's decision book to pass stale check at its later decision.
    b["decision_book"] = snap(360)
    proof = {"order_id": "same", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
             "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
             "submitted_at": (START + timedelta(minutes=6)).isoformat(),
             "limit_price": "0.40",
             "side": "YES", "action": "buy", "ack_at": (
                 START + timedelta(minutes=6)).isoformat(),
             "filled_qty": "0", "fee_paid": "0",
             "terminal_state": "CANCELLED",
             "resolved_at": (START + timedelta(minutes=6, seconds=2)).isoformat()}
    a["observed_orders"] = {"PASSIVE_SPREAD": {
        **proof, "ack_at": START.isoformat(), "submitted_at": START.isoformat()}}
    b["observed_orders"] = {"PASSIVE_SPREAD": proof}
    with pytest.raises(ValueError, match="reused passive journal"):
        evaluate([a, b], gross_free=Decimal(10), cash_free=Decimal(10))


def test_invalid_noncausal_fill_or_book_raises() -> None:
    with pytest.raises(ValueError, match="crossed"):
        Snapshot.from_json(snap(bid="0.65", ask="0.60"))
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "bad", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "limit_price": "0.40",
        "side": "YES", "action": "buy", "ack_at": START.isoformat(),
        "fill_at": (START - timedelta(seconds=1)).isoformat(),
        "filled_qty": "1", "fill_price": "0.40", "fee_paid": "0",
        "terminal_state": "FILLED",
        "resolved_at": (START + timedelta(seconds=6)).isoformat(),
    }}
    with pytest.raises(ValueError, match="before acknowledgement"):
        result(row, "PASSIVE_SPREAD")


def test_invalid_controls_cannot_inflate_capital() -> None:
    with pytest.raises(ValueError, match="economic controls"):
        evaluate([], quantity=Decimal("1.5"))
    with pytest.raises(ValueError, match="economic controls"):
        evaluate([], haircut=Decimal("1.1"))


def test_cluster_confidence_unavailable_for_single_event() -> None:
    out = evaluate([episode()], gross_free=Decimal(10), cash_free=Decimal(10))
    for candidate in out["scorecard"].values():
        assert candidate["day_event_clustered_markout_ci95"] is None
        assert candidate["capacity_ceiling"] is None


def test_complemented_frozen_pm_prediction_inverts_sig_side() -> None:
    row = episode(side="YES")
    row["mapping_direction"] = "COMPLEMENT"
    item = result(row, "FROZEN_005I")
    assert item["side"] == "NO"


def test_non_sig_reference_orientation_never_trades() -> None:
    row = episode()
    row["features"]["reference_orientation"] = "PM_TOKEN"
    assert result(row, "DIRECT_FV")["reason"] == "no_qualifying_signal"
    assert result(row, "CROSSVENUE_STALE")["reason"] == "no_qualifying_signal"


def test_wrong_journal_candidate_or_limit_fails_closed() -> None:
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "wrong-limit", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "ack_at": START.isoformat(),
        "limit_price": "0.41", "side": "YES", "action": "buy",
        "filled_qty": "0", "fee_paid": "0",
        "terminal_state": "CANCELLED",
        "resolved_at": (START + timedelta(seconds=2)).isoformat(),
    }}
    with pytest.raises(ValueError, match="passive limit"):
        result(row, "PASSIVE_SPREAD")
    row["observed_orders"]["PASSIVE_SPREAD"]["limit_price"] = "0.40"
    row["observed_orders"]["PASSIVE_SPREAD"]["strategy_id"] = "another_model"
    with pytest.raises(ValueError, match="not unambiguously matched"):
        result(row, "PASSIVE_SPREAD")


def test_depth_compression_and_high_volatility_are_not_assumed_robust() -> None:
    row = episode()
    # Less executable depth alone can eliminate the IOC at fixed quantity.
    assert result(row, "DIRECT_FV", haircut=Decimal("0.01"))[
        "reason"] == "insufficient_decision_depth"
    # A genuinely different high-volatility future exit yields an adverse markout.
    row["exits"]["300"] = snap(300.2, bid="0.10", ask="0.20")
    assert result(row, "DIRECT_FV")["conditional_net_5m"] < 0
    # Spread compression kills passive signal rather than imputing historic fills.
    row["features"]["spread"] = 0.02
    assert result(row, "PASSIVE_SPREAD")["reason"] == "no_qualifying_signal"


def test_future_feature_window_cannot_be_traded() -> None:
    row = episode()
    row["features_available_at"] = (START + timedelta(seconds=1)).isoformat()
    assert result(row, "DIRECT_FV")["reason"] == "feature_lookahead"
    row.pop("features_available_at")
    assert result(row, "DIRECT_FV")["reason"] == "feature_clock_missing_or_invalid"


def test_stale_signal_requires_observed_external_update() -> None:
    row = episode()
    row["features"]["observable_external_update"] = False
    assert result(row, "STALE_QUOTE")["reason"] == "no_qualifying_signal"


def test_unresolved_passive_order_locks_capital_not_counted_as_zero_fill() -> None:
    row = episode()
    row["observed_orders"] = {"PASSIVE_SPREAD": {
        "order_id": "unresolved", "provenance": "SIG_EXECUTION_JOURNAL_JOINED",
        "strategy_id": "PASSIVE_SPREAD", "sig_exchange_id": "101",
        "submitted_at": START.isoformat(), "ack_at": START.isoformat(),
        "limit_price": "0.40", "side": "YES", "action": "buy",
        "filled_qty": "0", "fee_paid": "0",
    }}
    out = evaluate([row], gross_free=Decimal(10), cash_free=Decimal(10))
    item = next(x for x in out["ledger"] if x["strategy"] == "PASSIVE_SPREAD")
    assert item["status"] == "UNRESOLVED"
    assert item["reason"] == "unresolved_order_capital_locked"
    assert out["scorecard"]["PASSIVE_SPREAD"]["observed_passive_fill_probability"] is None


def test_after_tournament_lock_never_enters_economic_scenarios() -> None:
    row = episode()
    row["decision_at"] = datetime(2026, 11, 4, 17, 0, tzinfo=UTC).isoformat()
    assert result(row, "DIRECT_FV")["reason"] == "after_tournament_lock"
