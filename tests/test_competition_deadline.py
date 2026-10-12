"""SIG 2026 Cup placement cutoff: pure boundaries and actual HTTP entrypoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.competition_deadline import (
    COMPETITION_END_UTC,
    LAST_ORDER_SUBMISSION_UTC,
    assert_competition_placement_open,
)
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.trading_client import SigTradingClient


def test_official_deadline_and_buffer() -> None:
    assert COMPETITION_END_UTC == datetime(2026, 11, 4, 17, tzinfo=UTC)
    assert LAST_ORDER_SUBMISSION_UTC == COMPETITION_END_UTC - timedelta(seconds=5)
    assert_competition_placement_open(LAST_ORDER_SUBMISSION_UTC - timedelta(microseconds=1))
    for now in (LAST_ORDER_SUBMISSION_UTC, COMPETITION_END_UTC, COMPETITION_END_UTC + timedelta(days=1)):
        with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
            assert_competition_placement_open(now)


def test_timezone_and_clock_anomalies_fail_closed() -> None:
    plus_one = UTC + timedelta(hours=1)
    assert_competition_placement_open(
        (LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)).astimezone(plus_one)
    )
    with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
        assert_competition_placement_open(LAST_ORDER_SUBMISSION_UTC.astimezone(plus_one))
    with pytest.raises(SigApiError, match="COMPETITION_CLOCK_UNTRUSTED"):
        assert_competition_placement_open(datetime(2026, 11, 4, 16, 59))


def test_blocked_retry_is_uncertain_not_rejected() -> None:
    with pytest.raises(SigExecutionUncertainError, match="COMPETITION_CUTOFF_AFTER_DISPATCH"):
        assert_competition_placement_open(
            LAST_ORDER_SUBMISSION_UTC, attempted_network=True
        )


class _Governor:
    def __init__(self, after_acquire: Any = None) -> None:
        self.after_acquire = after_acquire
        self.acquires = 0

    async def acquire(self, priority: object) -> None:
        self.acquires += 1
        if self.after_acquire is not None:
            self.after_acquire()

    def record_response(self, code: int, *, retry_after_seconds: float | None) -> None:
        del code, retry_after_seconds


def _client(
    transport: httpx.MockTransport,
    governor: _Governor,
    clock: Any,
) -> SigTradingClient:
    # Exercising the real low-level dispatch loop without constructing credentials.
    obj = object.__new__(SigTradingClient)
    obj._client = httpx.AsyncClient(
        transport=transport, base_url="https://sig.invalid/"
    )
    obj._governor = governor
    obj._wall_clock = clock
    obj._retry_policy = RetryPolicy(
        max_attempts=2, base_delay_seconds=0, max_delay_seconds=0, jitter_ratio=0
    )
    obj._timeout_seconds = 1.0
    obj._request_in_flight_wait_seconds = 0.0
    return obj


@pytest.mark.asyncio
async def test_governor_wait_crossing_cutoff_sends_zero_placements() -> None:
    requests = 0
    now = LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)

    def move_past_cutoff() -> None:
        nonlocal now
        now = LAST_ORDER_SUBMISSION_UTC

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(respond), _Governor(move_past_cutoff), lambda: now)
    try:
        with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
            await client._request_json(
                "POST", "orders", route_template="/orders",
                resolved_payload={}, accepted_statuses=frozenset({200}),
                execution_can_be_uncertain=True,
            )
        assert requests == 0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_retry_after_prior_send_cannot_cross_cutoff() -> None:
    requests = 0
    clock_samples = iter(
        [LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1), LAST_ORDER_SUBMISSION_UTC]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(502, json={
            "error": {"code": "ORDER_STATUS_UNKNOWN", "message": "retry"}
        })

    client = _client(httpx.MockTransport(respond), _Governor(), lambda: next(clock_samples))
    try:
        with pytest.raises(SigExecutionUncertainError, match="COMPETITION_CUTOFF_AFTER_DISPATCH"):
            await client._request_json(
                "POST", "orders", route_template="/orders",
                resolved_payload={}, accepted_statuses=frozenset({200}),
                execution_can_be_uncertain=True,
            )
        assert requests == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancellation_remains_permitted_after_cutoff() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    client = _client(
        httpx.MockTransport(respond), _Governor(),
        lambda: COMPETITION_END_UTC + timedelta(minutes=1),
    )
    try:
        status, body = await client._request_json(
            "POST", "orders/cancel-all", route_template="/orders/cancel-all",
            resolved_payload={}, accepted_statuses=frozenset({200}),
            execution_can_be_uncertain=True,
        )
        assert status == 200 and body == {"ok": True}
        assert calls == 1
    finally:
        await client.aclose()
