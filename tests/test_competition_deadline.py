"""SIG 2026 Cup placement cutoff: pure boundaries and actual HTTP entrypoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import httpx
import pytest

from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.competition_deadline import (
    COMPETITION_END_UTC,
    LAST_ORDER_SUBMISSION_UTC,
    assert_competition_placement_open,
)
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient


def test_official_deadline_and_buffer() -> None:
    assert datetime(2026, 11, 4, 17, tzinfo=UTC) == COMPETITION_END_UTC
    assert COMPETITION_END_UTC - timedelta(seconds=5) == LAST_ORDER_SUBMISSION_UTC
    assert_competition_placement_open(LAST_ORDER_SUBMISSION_UTC - timedelta(microseconds=1))
    for now in (
        LAST_ORDER_SUBMISSION_UTC,
        COMPETITION_END_UTC,
        COMPETITION_END_UTC + timedelta(days=1),
    ):
        with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
            assert_competition_placement_open(now)


def test_timezone_and_clock_anomalies_fail_closed() -> None:
    plus_one = timezone(timedelta(hours=1))
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
    obj._governor = cast(SigRestGovernor, governor)
    obj._wall_clock = clock
    obj._retry_policy = RetryPolicy(
        max_attempts=2, base_delay_seconds=0, max_delay_seconds=0, jitter_ratio=0
    )
    obj._sleep = asyncio.sleep
    obj._timeout_seconds = 1.0
    obj._request_in_flight_wait_seconds = 0.0
    return obj


@pytest.mark.parametrize(
    "route", ("/orders", "/orders/batch", "/orders/multi-leg")
)
def test_governor_wait_crossing_cutoff_sends_zero_placements(route: str) -> None:
    async def _run() -> None:
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
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={}, accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert requests == 0
        finally:
            await client.aclose()

    asyncio.run(_run())

@pytest.mark.parametrize(
    "route", ("/orders", "/orders/batch", "/orders/multi-leg")
)
def test_retry_after_prior_send_cannot_cross_cutoff(route: str) -> None:
    async def _run() -> None:
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
            with pytest.raises(
                SigExecutionUncertainError, match="COMPETITION_CUTOFF_AFTER_DISPATCH"
            ):
                await client._request_json(
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={}, accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert requests == 1
        finally:
            await client.aclose()

    asyncio.run(_run())

def test_cancellation_remains_permitted_after_cutoff() -> None:
    async def _run() -> None:
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

    asyncio.run(_run())



@pytest.mark.parametrize("route", ("/orders", "/orders/batch", "/orders/multi-leg"))
def test_exhausted_429_preserves_unknown_placement(route: str) -> None:
    async def _run() -> None:
        wires: list[bytes] = []

        def respond(request: httpx.Request) -> httpx.Response:
            wires.append(request.content)
            return httpx.Response(
                429, json={"error": {"code": "RATE_LIMITED", "message": "slow down"}},
                headers={"Retry-After": "0"},
            )

        client = _client(
            httpx.MockTransport(respond), _Governor(),
            lambda: LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=10),
        )
        try:
            with pytest.raises(SigExecutionUncertainError, match="RATE_LIMITED"):
                await client._request_json(
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={"idempotencyKey": "fixed"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert len(wires) == 2
            assert wires[0] == wires[1]
        finally:
            await client.aclose()

    asyncio.run(_run())


@pytest.mark.parametrize("route", ("/orders", "/orders/batch", "/orders/multi-leg"))
def test_retry_429_then_cutoff_retains_uncertain_single_wire(route: str) -> None:
    async def _run() -> None:
        now = LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)
        wires = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal now, wires
            wires += 1
            now = LAST_ORDER_SUBMISSION_UTC
            return httpx.Response(
                429, json={"error": {"code": "RATE_LIMITED", "message": "retry"}},
                headers={"Retry-After": "0"},
            )

        client = _client(httpx.MockTransport(respond), _Governor(), lambda: now)
        try:
            with pytest.raises(
                SigExecutionUncertainError, match="COMPETITION_CUTOFF_AFTER_DISPATCH"
            ):
                await client._request_json(
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={"idempotencyKey": "same"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert wires == 1
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_clock_rollback_within_client_blocks_retry_after_prior_wire() -> None:
    async def _run() -> None:
        now = LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)
        wires = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal now, wires
            wires += 1
            now -= timedelta(seconds=15)
            return httpx.Response(
                502, json={"error": {"code": "ORDER_STATUS_UNKNOWN", "message": "unknown"}},
            )

        client = _client(httpx.MockTransport(respond), _Governor(), lambda: now)
        try:
            with pytest.raises(SigExecutionUncertainError, match="COMPETITION_CLOCK_ROLLBACK"):
                await client._request_json(
                    "POST", "orders", route_template="/orders",
                    resolved_payload={"idempotencyKey": "same"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert wires == 1
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_clock_reopens_after_cutoff_same_client_is_denied() -> None:
    async def _run() -> None:
        now = COMPETITION_END_UTC
        wires = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal wires
            wires += 1
            return httpx.Response(200, json={"ok": True})

        client = _client(httpx.MockTransport(respond), _Governor(), lambda: now)
        try:
            with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
                await client._request_json(
                    "POST", "orders", route_template="/orders",
                    resolved_payload={}, accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            now -= timedelta(hours=1)
            with pytest.raises(SigApiError, match="COMPETITION_CLOCK_ROLLBACK"):
                await client._request_json(
                    "POST", "orders", route_template="/orders",
                    resolved_payload={}, accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert wires == 0
        finally:
            await client.aclose()

    asyncio.run(_run())


@pytest.mark.parametrize("route", ("/orders", "/orders/batch", "/orders/multi-leg"))
@pytest.mark.parametrize("outcome", ("unknown_502", "rate_429", "transport"))
def test_earlier_possible_wire_then_naive_retry_is_uncertain(
    route: str, outcome: str,
) -> None:
    """Original #207/#246 red-team P0 falsifier: no second wire, never REJECTED."""
    async def run() -> None:
        now: datetime = LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)
        wires: list[bytes] = []

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal now
            wires.append(request.content)
            now = datetime(2026, 11, 4, 16, 59, 54)  # same nominal time, no tz
            if outcome == "transport":
                raise httpx.ConnectError("possible lost ACK", request=request)
            if outcome == "rate_429":
                return httpx.Response(429, json={
                    "error": {"code": "RATE_LIMITED", "message": "fixture"}
                }, headers={"Retry-After": "0"})
            return httpx.Response(502, json={
                "error": {"code": "ORDER_STATUS_UNKNOWN", "message": "fixture"}
            })

        client = _client(httpx.MockTransport(respond), _Governor(), lambda: now)
        try:
            with pytest.raises(SigExecutionUncertainError) as caught:
                await client._request_json(
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={"idempotencyKey": "original-unmodified"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert caught.value.code == "COMPETITION_CLOCK_UNTRUSTED"
            assert len(wires) == 1
            assert b"original-unmodified" in wires[0]
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("route", ("/orders", "/orders/batch", "/orders/multi-leg"))
def test_first_attempt_naive_clock_is_rejected_with_zero_wire(route: str) -> None:
    async def run() -> None:
        wires = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal wires
            wires += 1
            return httpx.Response(200, json={"ok": True})

        client = _client(
            httpx.MockTransport(respond), _Governor(),
            lambda: datetime(2026, 11, 4, 16, 59, 54),
        )
        try:
            with pytest.raises(SigApiError) as caught:
                await client._request_json(
                    "POST", route.lstrip("/"), route_template=route,
                    resolved_payload={"idempotencyKey": "never-sent"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
            assert caught.value.code == "COMPETITION_CLOCK_UNTRUSTED"
            assert not isinstance(caught.value, SigExecutionUncertainError)
            assert wires == 0
        finally:
            await client.aclose()

    asyncio.run(run())
