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
    return ExactPair("market-1", "pm-yes", "sig-yes", "sig-no", "ACCEPTED_EXACT")


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
                max_quote_age_ms=300, max_feature_delay_ms=5000,
                max_arrival_wait_ms=1000, max_exit_wait_ms=500,
                depth_haircut=D("0.5"))
    args.update(kw)
    return EconomicConfig(**args)


def tape(*, bad_arrival: bool = False, future_jump: bool = False,
         missing_exit: bool = False, depth: str = "100") -> tuple[ReplayEvent, ...]:
    before = T - timedelta(milliseconds=100)
    decision = T + timedelta(milliseconds=100)
    arrival = T + timedelta(milliseconds=200)
    exit_at = arrival + timedelta(minutes=5)
    return (
        trust(before),
        quote(ReplaySource.SIG, "sig-yes", before, 1, "0.50", "0.55", depth),
        quote(ReplaySource.SIG, "sig-no", before, 2, "0.40", "0.45", depth),
        quote(ReplaySource.POLYMARKET, "pm-yes", before, 1, "0.70", "0.72"),
        quote(ReplaySource.POLYMARKET, "pm-yes", decision, 2, "0.70", "0.72"),
        quote(ReplaySource.SIG, "sig-yes", arrival, 3,
              "0.80" if future_jump else "0.54",
              "0.83" if future_jump else "0.55", depth if not bad_arrival else "0"),
        quote(ReplaySource.SIG, "sig-no", arrival, 4, "0.40", "0.45", depth),
        *(() if missing_exit else (
            quote(ReplaySource.SIG, "sig-yes", exit_at, 5, "0.60", "0.62", depth),
            quote(ReplaySource.SIG, "sig-no", exit_at, 6, "0.37", "0.40", depth),
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
    with pytest.raises(ValueError, match="EXACT"):
        ExactPair("m", "pm", "yes", "no", "UNVERIFIED")
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


def test_ioc_limit_blocks_levels_above_limit_even_if_vwap_would_pass() -> None:
    asks = (
        BookLevel(D("0.50"), D("1")),
        BookLevel(D("0.60"), D("1")),
    )
    assert _walk(asks, D("2"), buy=True, haircut=D("1")) == D("1.10")
    assert _walk(
        asks, D("2"), buy=True, haircut=D("1"), max_buy_price=D("0.55"),
    ) is None
