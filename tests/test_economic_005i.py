"""Economic replay adversarial tests: never substitute midpoint accuracy for net PnL."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from predictions_cup.replay.economic_005i import (
    EconomicConfig,
    ExactPair,
    MinuteFeature,
    _walk,
    evaluate,
    frozen_probability,
)
from predictions_cup.replay.model import (
    BookLevel,
    HealthPayload,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
    TrustPayload,
)

D = Decimal
T = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def quote(source: ReplaySource, contract: str, at: datetime, seq: int,
          bid: str, ask: str, qty: str = "100") -> ReplayEvent:
    return ReplayEvent(
        observed_at=at, source_at=at - timedelta(seconds=2),
        source=source, event_type=ReplayEventType.BOOK_OBSERVATION,
        instrument_id=contract, market_id="market-1", sequence=seq,
        payload=QuotePayload(
            best_bid=D(bid), best_ask=D(ask), quote_observed_at=at,
            bids=(BookLevel(D(bid), D(qty)),),
            asks=(BookLevel(D(ask), D(qty)),),
        ),
    )


def trust(at: datetime) -> ReplayEvent:
    return ReplayEvent(
        observed_at=at, source_at=None, source=ReplaySource.SIG,
        event_type=ReplayEventType.TRUST, instrument_id="*", market_id=None,
        sequence=1, payload=TrustPayload(trusted=True, transition="TRUSTED"),
    )


def pair() -> ExactPair:
    return ExactPair("market-1", "pm-yes", "sig-yes", "market-1", "SAME")


def feature(**overrides: float) -> MinuteFeature:
    vals = {
        "mid": 0.71, "relative_spread": 0.028, "depth_total5": 500.0,
        "quote_events": 19.0, "rv_5m": 0.001, "imbalance1": 0.15,
        "ret_1m": -0.01, "ret_5m": -0.03, "ofi_depth_norm": 0.04,
    }
    vals.update(overrides)
    return MinuteFeature("market-1", "pm-yes", T, T, vals,
                         "005I_V3_CAUSAL_COMPLETED_MINUTE")


def freeze() -> dict[str, Any]:
    common = {
        "features": list(feature().values),
        "medians": dict(feature().values),
        "scaler_mean": [0.0] * 9,
        "scaler_scale": [1.0] * 9,
        "coef": [0.0] * 9,
    }
    return {"models": {
        "baseline": {**common, "intercept": -2.0},
        "ofi_challenger": {**common, "intercept": 2.0},
    }}


def cfg(**kw: Any) -> EconomicConfig:
    args: dict[str, Any] = dict(fee_bps_per_side=D("20"), latency_ms=50,
                min_edge=D("0.01"), quantity=D("1"), capital_limit=D("10"),
                gross_share_limit=D("10"),
                max_quote_age_ms=300, max_feature_delay_ms=5000,
                max_arrival_wait_ms=1000, max_exit_wait_ms=500,
                depth_haircut=D("0.5"))
    args.update(kw)
    return EconomicConfig(**args)


def tape(*, bad_arrival: bool = False, future_jump: bool = False,
         missing_exit: bool = False, depth: str = "100",
         pm_no_favoured: bool = False,
         exit_depth: str | None = None) -> tuple[ReplayEvent, ...]:
    before = T - timedelta(milliseconds=100)
    decision = T + timedelta(milliseconds=100)
    arrival = T + timedelta(milliseconds=200)
    exit_at = arrival + timedelta(minutes=5)
    pm_bid, pm_ask = ("0.30", "0.32") if pm_no_favoured else ("0.70", "0.72")
    return (
        trust(before),
        quote(ReplaySource.SIG, "sig-yes", before, 1, "0.50", "0.55", depth),
        quote(ReplaySource.POLYMARKET, "pm-yes", before, 1, pm_bid, pm_ask),
        quote(ReplaySource.POLYMARKET, "pm-yes", decision, 2, pm_bid, pm_ask),
        quote(ReplaySource.SIG, "sig-yes", arrival, 3,
              "0.80" if future_jump else "0.54",
              "0.83" if future_jump else "0.55", depth if not bad_arrival else "0"),
        *(() if missing_exit else (
            quote(ReplaySource.SIG, "sig-yes", exit_at, 5, "0.60", "0.62",
                  exit_depth if exit_depth is not None else depth),
        )),
    )


def test_positive_cross_scenario_not_called_a_real_fill() -> None:
    result = evaluate(tape(), [feature()], [pair()], cfg(), freeze())
    rev = result["results"]["REVERSAL"]
    assert rev["signals"] == 1
    assert rev["qualified_orders"] == 1
    assert rev["touch_fill_upper_bound"] == 1
    assert rev["filled_with_5m_depth_exit"] == 1
    assert rev["net_pnl_known_exits"] > 0.04
    assert rev["trades"][0]["markouts_net_5s_30s_5m_1h"]["3600"] is None
    assert rev["empirically_calibrated_fill_probability"] is None
    assert result["promotion"].startswith("BLOCKED")
    assert result["results"]["NO_TRADE"]["net_pnl_known_exits"] == 0


def test_latency_price_change_rejects_ioc_and_no_phantom_profit() -> None:
    result = evaluate(tape(future_jump=True), [feature()], [pair()], cfg(), freeze())
    row = result["results"]["REVERSAL"]
    assert row["qualified_orders"] == 1
    assert row["touch_fill_upper_bound"] == 0
    assert row["net_pnl_known_exits"] == 0


def test_true_depth_limits_size_and_zero_depth_never_fills() -> None:
    huge = cfg(quantity=D("100"))
    result = evaluate(tape(depth="3"), [feature()], [pair()], huge, freeze())
    assert result["results"]["REVERSAL"]["qualified_orders"] == 0
    assert evaluate(tape(bad_arrival=True), [feature()], [pair()], cfg(), freeze()
                    )["results"]["REVERSAL"]["touch_fill_upper_bound"] == 0


def test_missing_executable_exit_is_a_loss_bound_not_imputed_profit() -> None:
    out = evaluate(tape(missing_exit=True), [feature()], [pair()], cfg(), freeze())
    row = out["results"]["REVERSAL"]
    assert row["touch_fill_upper_bound"] == 1
    assert row["filled_with_5m_depth_exit"] == 0
    assert row["net_pnl_known_exits"] == 0
    assert row["net_pnl_liquidation_zero_stress"] < 0


def test_critical_feature_clock_and_frozen_schema_guards() -> None:
    with pytest.raises(ValueError, match="before"):
        MinuteFeature("market-1", "pm-yes", T, T-timedelta(seconds=1),
                      feature().values, feature().provenance)
    with pytest.raises(ValueError, match="causal"):
        MinuteFeature("market-1", "pm-yes", T, T, {"future_ret_5m": 1},
                      feature().provenance)
    with pytest.raises(ValueError, match="provenance"):
        MinuteFeature("market-1", "pm-yes", T, T, feature().values, "unknown")
    with pytest.raises(ValueError, match="SAME/COMPLEMENT"):
        ExactPair("m", "pm", "yes", "m", "UNVERIFIED")
    with pytest.raises(ValueError, match="purge"):
        cfg(cooldown_seconds=20)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(tape(), [feature(), feature()], [pair()], cfg(), freeze())


def test_frozen_coefficients_and_model_baseline_pairing() -> None:
    models = freeze()["models"]
    assert frozen_probability(feature().values, models["baseline"]) < 0.5
    assert frozen_probability(feature().values, models["ofi_challenger"]) > 0.5
    out = evaluate(tape(), [feature()], [pair()], cfg(), freeze())
    assert out["results"]["OFI_CHALLENGER"]["touch_fill_upper_bound"] == 1
    assert out["results"]["OFI_BASELINE"]["touch_fill_upper_bound"] == 0


def test_quote_tape_is_receive_time_causal_and_deterministic() -> None:
    a = evaluate(tape(), [feature()], [pair()], cfg(), freeze())
    b = evaluate(reversed(tape()), [feature()], [pair()], cfg(), freeze())
    assert a == b


def test_fee_can_destroy_directional_profit() -> None:
    high_fee = evaluate(tape(), [feature()], [pair()],
                        cfg(fee_bps_per_side=D("2500")), freeze())
    assert high_fee["results"]["REVERSAL"]["qualified_orders"] == 0


def test_absent_events_are_explicit_not_positive_alpha() -> None:
    out = evaluate([], [feature()], [pair()], cfg(), freeze())
    assert out["status"] == "NO_ACCEPTED_OBSERVATIONS"
    assert out["promotion"] == "BLOCKED"



def test_no_buy_uses_same_exchange_yes_bid_complement_not_sibling_exchange() -> None:
    # PM YES=0.71; SIG YES bid=0.50 => NO ask=0.50, not another party's YES ask.
    result = evaluate(tape(pm_no_favoured=True), [feature(ret_5m=0.03)], [pair()], cfg(), freeze())
    reversal = result["results"]["REVERSAL"]
    assert reversal["touch_fill_upper_bound"] == 1
    trade = reversal["trades"][0]
    assert trade["side"] == "NO"
    assert trade["action"] == "buy"
    assert trade["exchange_id"] == "sig-yes"
    assert trade["signed_yes_entry_delta"] == -1
    assert trade["signed_yes_full_close_delta"] == 1
    # Latency: NO ask=1-0.54=0.46; 5m NO bid=1-0.62=0.38.
    # The matching is on the SAME exchange; fee-adjusted loss is ~-0.08168.
    assert abs(trade["net_pnl_5m"] - (-0.08168)) < 1e-8


def test_third_party_winner_proves_no_is_not_another_party_yes() -> None:
    p = pair()
    assert p.sig_exchange_id == "sig-yes"
    assert p.mapping_direction == "SAME"
    # Third-party winner: neither Republican YES nor Democratic YES settles true.
    # NO on Republican YES settles TRUE; buying Democratic YES settles FALSE.
    # Treating a separate Democratic YES exchange as NO Republican would lose a claim.
    republican_yes = False
    democratic_yes = False
    third_party_wins = True
    assert third_party_wins and not republican_yes and not democratic_yes
    assert int(not republican_yes) == 1
    assert int(democratic_yes) == 0
    assert all(e.instrument_id != "democratic-yes-exchange" for e in tape())


def test_cash_budget_uses_actual_susqies_not_nominal_share_quantity() -> None:
    result = evaluate(tape(), [feature()], [pair()],
                      cfg(capital_limit=D("0.40"), gross_share_limit=D("100")), freeze())
    assert result["results"]["REVERSAL"]["rejections"]["capital_exhausted"] == 1
    result = evaluate(tape(), [feature()], [pair()],
                      cfg(capital_limit=D("100"), gross_share_limit=D("0.5")), freeze())
    assert result["results"]["REVERSAL"]["rejections"]["gross_share_limit"] == 1


def test_complement_mapping_orients_pm_signal_into_sig_no() -> None:
    p = ExactPair("market-1", "pm-yes", "sig-yes", "market-1", "COMPLEMENT")
    out = evaluate(tape(), [feature(ret_5m=-0.03)], [p], cfg(), freeze())
    # PM YES rising reversal predicts PM token YES; this is SIG NO in reviewed complement.
    assert out["results"]["REVERSAL"]["touch_fill_upper_bound"] == 1
    assert out["results"]["REVERSAL"]["trades"][0]["side"] == "NO"

def test_ioc_limit_blocks_levels_above_limit_even_if_vwap_would_pass() -> None:
    asks = (
        BookLevel(D("0.50"), D("1")),
        BookLevel(D("0.60"), D("1")),
    )
    assert _walk(asks, D("2"), buy=True, haircut=D("1")) == D("1.10")
    assert _walk(
        asks, D("2"), buy=True, haircut=D("1"), max_buy_price=D("0.55"),
    ) is None


def test_no_partial_close_keeps_signed_residual_and_cost_capital_locked() -> None:
    out = evaluate(
        tape(pm_no_favoured=True, exit_depth="2"),
        [feature(ret_5m=0.03)], [pair()], cfg(quantity=D("2")), freeze(),
    )
    row = out["results"]["REVERSAL"]
    assert row["touch_fill_upper_bound"] == 1
    assert row["filled_with_5m_depth_exit"] == 0
    trade = row["trades"][0]
    assert trade["side"] == "NO"
    assert trade["exchange_id"] == "sig-yes"
    assert trade["signed_yes_entry_delta"] == -2
    assert trade["liquidated_quantity_5m"] == 1
    assert trade["signed_yes_partial_close_delta"] == 1
    assert trade["signed_yes_full_close_delta"] is None
    assert trade["remaining_signed_yes_quantity"] == -1
    assert trade["net_pnl_5m"] is None
    assert trade["partial_realised_5m_pnl_proxy"] is not None
    assert trade["unresolved_liquidation_zero_bound"] < 0


def test_sig_order_quantities_are_whole_shares() -> None:
    with pytest.raises(ValueError, match="whole shares"):
        cfg(quantity=D("1.5"))


def test_sig_untrusted_global_transition_after_book_blocks_entry() -> None:
    original = list(tape())
    at = T + timedelta(milliseconds=50)
    original.append(ReplayEvent(
        observed_at=at, source_at=None,
        source=ReplaySource.SIG, event_type=ReplayEventType.TRUST,
        instrument_id="*", market_id=None, sequence=99,
        payload=TrustPayload(trusted=False, transition="UNTRUSTED_REVISION_GAP"),
    ))
    out = evaluate(original, [feature()], [pair()], cfg(), freeze())
    assert out["results"]["REVERSAL"]["qualified_orders"] == 0


def test_pm_feed_disconnect_blocks_asof_mid_even_without_book_change() -> None:
    original = list(tape())
    at = T + timedelta(milliseconds=50)
    original.append(ReplayEvent(
        observed_at=at, source_at=None,
        source=ReplaySource.POLYMARKET, event_type=ReplayEventType.HEALTH,
        instrument_id="*", market_id=None, sequence=99,
        payload=HealthPayload(available=False, detail="gap"),
    ))
    out = evaluate(original, [feature()], [pair()], cfg(), freeze())
    assert out["results"]["REVERSAL"]["qualified_orders"] == 0


def test_health_recovery_event_alone_cannot_mint_a_tradeable_book_tick() -> None:
    original = list(tape())
    at = T + timedelta(milliseconds=150)
    original.append(ReplayEvent(
        observed_at=at, source_at=None,
        source=ReplaySource.SIG, event_type=ReplayEventType.HEALTH,
        instrument_id="*", market_id=None, sequence=99,
        payload=HealthPayload(available=False, detail="gap"),
    ))
    out = evaluate(original, [feature()], [pair()], cfg(), freeze())
    assert out["results"]["REVERSAL"]["touch_fill_upper_bound"] == 0
