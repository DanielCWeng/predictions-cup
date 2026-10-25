from dataclasses import replace

from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.election_catalyst_bridge import market_view_from_snapshot

from test_residual_taker_live import _snapshot, _with_inventory, NOW


def test_book_view_reuses_live_snapshot_and_fails_closed_on_unknown_cash():
    snap = _snapshot()
    view = market_view_from_snapshot(snap, ExecutionReservationBook(), "BUY")
    assert view.market_open and view.bbo_trusted and view.depth_trusted
    assert view.exchange_id == snap.exchange_id
    assert view.available_cash is None


def test_reducible_shares_derive_from_worst_case_signed_inventory():
    snap = _with_inventory(_snapshot(), -37)
    view = market_view_from_snapshot(snap, ExecutionReservationBook(), "BUY")
    assert view.reducible_shares == 37
    assert market_view_from_snapshot(snap, ExecutionReservationBook(), "SELL").reducible_shares == 0


def test_bbo_staleness_does_not_become_trusted():
    snap = _snapshot()
    old = replace(snap, sig_bbo_observed_ns=NOW-16_000_000_000)
    view = market_view_from_snapshot(old, ExecutionReservationBook(), "BUY")
    assert not view.bbo_trusted


def test_closed_or_untrusted_depth_is_not_actionable():
    snap = _snapshot()
    runtime = replace(
        snap.runtime,
        markets=(replace(snap.runtime.markets[0], status="settled"),),
    )
    snap = replace(snap, runtime=runtime, sig_depth_trusted=False)
    view = market_view_from_snapshot(snap, ExecutionReservationBook(), "SELL")
    assert not view.market_open and not view.depth_trusted
