"""Cash-aware maker quoting: prevent unfundable bids and unfunded short sells."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from predictions_cup.maker import MakerMarketSnapshot

from test_make001 import _engine, _maker_snapshot


def _cash_snapshot(*, cash: str, inventory: float = 0.0) -> MakerMarketSnapshot:
    snapshot = _maker_snapshot(signed_inventory=inventory)
    return replace(
        snapshot,
        runtime=replace(
            snapshot.runtime,
            portfolio=replace(
                snapshot.runtime.portfolio,
                account_proxy_cash_balance=Decimal(cash),
            ),
        ),
    )


def test_zero_cash_flat_account_sends_no_new_collateral_consuming_quotes() -> None:
    normal = _engine().quote(_maker_snapshot())
    assert normal.desired is not None
    assert normal.desired.bid_size > 0
    assert normal.desired.ask_size > 0
    restricted = _engine().quote(_cash_snapshot(cash="0.075"))
    assert restricted.desired is None or (
        restricted.desired.bid_size == 0 and restricted.desired.ask_size == 0
    )


def test_zero_cash_keeps_only_proven_inventory_reducing_ask() -> None:
    decision = _engine(max_inventory=200.0, base_size=100).quote(
        _cash_snapshot(cash="0.075", inventory=80.0)
    )
    assert decision.desired is not None
    assert decision.desired.bid_size == 0
    assert decision.desired.ask_size > 0
    assert decision.desired.ask_size <= 80


def test_enough_cash_preserves_normal_both_sided_maker() -> None:
    quote = _engine().quote(_cash_snapshot(cash="1000"))
    assert quote.desired is not None
    assert quote.desired.bid_size > 0
    assert quote.desired.ask_size > 0


def test_deep_ladder_cash_shortage_preserves_total_reducing_inventory() -> None:
    decision = _engine(max_inventory=200.0, deep_ladder_enabled=True).quote(
        _cash_snapshot(cash="0.075", inventory=125.0)
    )
    assert decision.desired is not None
    assert not decision.desired.bid_levels
    assert decision.desired.bid_size == 0
    assert sum(level.size for level in decision.desired.ask_levels) <= 125
    assert decision.desired.ask_size > 0
