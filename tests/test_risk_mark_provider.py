from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from predictions_cup.risk.sig import SigRealtimeRiskMarkProvider
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine


def _provider(*, connected: bool, rest: bool, realtime: bool) -> SigRealtimeRiskMarkProvider:
    now = datetime.now(UTC) - timedelta(seconds=1)
    exchange = SimpleNamespace(
        exchange_id="960",
        market_id="271",
        latest_price=Decimal("0.43"),
        last_scalar_observed_at=now if rest else None,
        last_rest_observed_at=None,
        last_realtime_observed_at=now if realtime else None,
    )
    state = SimpleNamespace(
        states={"960": exchange}, health=SimpleNamespace(connected=connected)
    )
    return SigRealtimeRiskMarkProvider(cast(SigRealtimeStateEngine, state))


def _trusted(provider: SigRealtimeRiskMarkProvider) -> bool:
    (mark,) = provider.marks_for(frozenset({"960"}), now_monotonic_ns=10**12)
    return mark.trusted


def test_rest_seeded_mark_is_trusted_before_realtime_connects() -> None:
    # LIVE startup interlock runs before the Realtime socket is connected.
    assert _trusted(_provider(connected=False, rest=True, realtime=False))


def test_realtime_only_mark_requires_connected_socket() -> None:
    assert not _trusted(_provider(connected=False, rest=False, realtime=True))
    assert _trusted(_provider(connected=True, rest=False, realtime=True))


def test_new_realtime_trade_supersedes_older_scalar_mark() -> None:
    provider = _provider(connected=True, rest=True, realtime=True)
    (exchange,) = provider._state.states.values()
    exchange.last_scalar_observed_at = datetime.now(UTC) - timedelta(minutes=5)
    exchange.last_trade_observed_at = datetime.now(UTC) - timedelta(seconds=1)

    (mark,) = provider.marks_for(frozenset({"960"}), now_monotonic_ns=10**12)

    assert mark.trusted
    assert 0 <= 10**12 - mark.observed_monotonic_ns < 2 * 10**9


def test_realtime_trade_mark_requires_connected_socket_even_with_old_scalar() -> None:
    provider = _provider(connected=False, rest=True, realtime=True)
    exchange = cast(SimpleNamespace, provider._state.states["960"])
    exchange.last_scalar_observed_at = datetime.now(UTC) - timedelta(minutes=5)
    exchange.last_trade_observed_at = datetime.now(UTC) - timedelta(seconds=1)

    (mark,) = provider.marks_for(frozenset({"960"}), now_monotonic_ns=10**12)

    assert not mark.trusted


def test_trusted_book_uses_healthy_batch_time_for_unchanged_mark() -> None:
    provider = _provider(connected=True, rest=True, realtime=False)
    exchange = cast(SimpleNamespace, provider._state.states["960"])
    exchange.trusted = True
    exchange.last_scalar_observed_at = datetime.now(UTC) - timedelta(minutes=5)
    provider._state.health.last_valid_batch = datetime.now(UTC) - timedelta(seconds=1)

    (mark,) = provider.marks_for(frozenset({"960"}), now_monotonic_ns=10**12)

    assert mark.trusted
    assert 0 <= 10**12 - mark.observed_monotonic_ns < 2 * 10**9


def test_untrusted_book_does_not_borrow_healthy_batch_freshness() -> None:
    provider = _provider(connected=True, rest=True, realtime=False)
    exchange = cast(SimpleNamespace, provider._state.states["960"])
    exchange.trusted = False
    exchange.last_scalar_observed_at = datetime.now(UTC) - timedelta(minutes=5)
    provider._state.health.last_valid_batch = datetime.now(UTC) - timedelta(seconds=1)

    (mark,) = provider.marks_for(frozenset({"960"}), now_monotonic_ns=10**12)

    assert 10**12 - mark.observed_monotonic_ns > 180 * 10**9
