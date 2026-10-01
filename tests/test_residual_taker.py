import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.maker.residual_taker import ResidualInput, ResidualTakerSignal
from predictions_cup.runtime.models import OrderAction, OutcomeSide

FIXTURES = Path(__file__).parent / "fixtures"


def _optional(row: dict[str, str], key: str) -> float | None:
    return None if not row[key] else float(row[key])


def _state(second: int, **updates: object) -> ResidualInput:
    fields: dict[str, object] = {
        "exchange_id": "market-a",
        "sig_bid": 0.70,
        "sig_ask": 0.705,
        "pm_mid": 0.75,
        "pm_spread": 0.01,
        "pm_bid_size": 100.0,
        "pm_ask_size": 100.0,
        "observed_monotonic_ns": second * 1_000_000_000,
    }
    fields.update(updates)
    return ResidualInput(**fields)  # type: ignore[arg-type]


def test_frozen_validation_replay_matches_battery_trade_list() -> None:
    golden = json.loads((FIXTURES / "residual_taker_validation_expected.json").read_text())
    engine = ResidualTakerSignal(size=50)
    matched = []
    with (FIXTURES / "residual_taker_validation.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            at = datetime.fromisoformat(row["at"])
            signal = engine.on_state(
                ResidualInput(
                    exchange_id=row["exchange_id"],
                    sig_bid=float(row["sig_bid"]),
                    sig_ask=float(row["sig_ask"]),
                    pm_mid=float(row["pm_mid"]),
                    pm_spread=float(row["pm_spread"]),
                    pm_bid_size=_optional(row, "pm_bid1_size"),
                    pm_ask_size=_optional(row, "pm_ask1_size"),
                    observed_monotonic_ns=int(at.replace(tzinfo=UTC).timestamp())
                    * 1_000_000_000,
                )
            )
            if signal is not None:
                matched.append(
                    {
                        "at": row["at"],
                        "split": row["split"],
                        "exchange_id": row["exchange_id"],
                        "direction": signal.direction,
                        "entry_price": signal.entry_price,
                    }
                )

    expected = golden["trades"]
    assert len(matched) == len(expected) == 90
    exact = 0
    for actual, reference in zip(matched, expected, strict=True):
        assert (actual["at"], actual["split"], actual["exchange_id"], actual["direction"]) == (
            reference["at"], reference["split"], reference["exchange_id"], reference["direction"]
        )
        assert abs(actual["entry_price"] - reference["entry_price"]) <= 1e-12
        exact += 1
    validation = [row for row in expected if row["split"] == "VAL"]
    assert len(validation) == 33
    assert len({row["exchange_id"] for row in validation}) == 7
    assert exact / len(expected) == 1.0


def test_cooldown_is_per_market_and_direction() -> None:
    engine = ResidualTakerSignal()
    assert engine.on_state(_state(0)) is not None
    assert engine.on_state(_state(59)) is None
    assert engine.on_state(_state(60)) is not None
    # Opposite direction has an independent cooldown key.
    opposite = _state(61, sig_bid=0.80, sig_ask=0.805)
    signal = engine.on_state(opposite)
    assert signal is not None
    assert signal.direction == "SELL"


def test_touch_depth_caps_size_and_flat_yes_sell_canonicalizes_to_no_buy() -> None:
    engine = ResidualTakerSignal(size=50)
    buy = engine.on_state(_state(0, pm_bid_size=40.0, pm_ask_size=75.0))
    assert buy is None  # Battery's depth=50 is a minimum gate.
    sell = engine.on_state(
        _state(1, sig_bid=0.80, sig_ask=0.805, pm_bid_size=55.0, pm_ask_size=80.0)
    )
    assert sell is not None
    assert sell.quantity == 50
    assert sell.outcome_side is OutcomeSide.NO
    assert sell.action is OrderAction.BUY


def test_universe_is_fail_closed_when_configured() -> None:
    engine = ResidualTakerSignal(tracked_exchange_ids=frozenset({"market-b"}))
    assert engine.on_state(_state(0)) is None
