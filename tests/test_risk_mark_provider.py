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
