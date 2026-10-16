"""LANE-5 independent red-team: deadline, burst, partial response and outages.

Pure fake clock + mocked HTTP only. This file cannot submit a SIG order.
Run against PR #202's safety head; green tests are not LIVE signoff.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import httpx
import pytest

from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.competition_deadline import (
    COMPETITION_END_UTC,
    LAST_ORDER_SUBMISSION_UTC,
)
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


class Governor:
    def __init__(self, on_acquire: Any = None) -> None:
        self.on_acquire = on_acquire
        self.acquires = 0
        self.responses: list[int] = []

    async def acquire(self, priority: object) -> None:
        self.acquires += 1
        if self.on_acquire is not None:
            self.on_acquire()

    def record_response(self, code: int, *, retry_after_seconds: float | None) -> None:
        self.responses.append(code)


def client_with_mock(
    mock: httpx.MockTransport, governor: Governor, clock: Clock,
    *, attempts: int = 3,
) -> SigTradingClient:
    # The network client has a fake transport; no credentials.
    client = object.__new__(SigTradingClient)
    client._client = httpx.AsyncClient(
        transport=mock, base_url="https://invalid.sig.example/"
    )
    client._governor = cast(SigRestGovernor, governor)
    client._wall_clock = clock
    client._retry_policy = RetryPolicy(
        max_attempts=attempts,
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter_ratio=0,
    )
    client._sleep = asyncio.sleep
    client._timeout_seconds = 0.01
    client._request_in_flight_wait_seconds = 0.0
    return client


async def place_raw(
    client: SigTradingClient, route: str,
    *, accepted: frozenset[int] = frozenset({200}),
) -> tuple[int, object]:
    return await client._request_json(
        "POST", route.lstrip("/"), route_template=route,
        resolved_payload={"idempotencyKey": "unchanged-key", "orders": []},
        accepted_statuses=accepted,
        execution_can_be_uncertain=True,
        resume_incomplete_batch=(route == "/orders/batch"),
    )


@pytest.mark.parametrize("route", ["/orders", "/orders/batch", "/orders/multi-leg"])
@pytest.mark.parametrize("burst_scale", [1, 10])
def test_queued_election_burst_crosses_cutoff_without_any_wire_send(
    route: str, burst_scale: int,
) -> None:
    """10x queued bursts must be rejected after governor admission, not before."""
    async def run() -> None:
        clock = Clock(LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1))
        wires: list[httpx.Request] = []

        def on_acquire() -> None:
            clock.now = LAST_ORDER_SUBMISSION_UTC

        def on_wire(request: httpx.Request) -> httpx.Response:
            wires.append(request)
            return httpx.Response(200, json={"ok": True})

        gov = Governor(on_acquire)
        client = client_with_mock(httpx.MockTransport(on_wire), gov, clock)
        try:
            outcomes = await asyncio.gather(
                *(place_raw(client, route) for _ in range(12 * burst_scale)),
                return_exceptions=True,
            )
            assert len(outcomes) == 12 * burst_scale
            assert all(
                isinstance(outcome, SigApiError)
                and outcome.code == "COMPETITION_ORDER_CLOSED"
                for outcome in outcomes
            )
            assert not wires, "No order may cross HTTP boundary after cutoff"
            assert gov.acquires == 12 * burst_scale
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("route", ["/orders", "/orders/batch", "/orders/multi-leg"])
@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("transport", None, None),
        ("unknown_502", 502, "ORDER_STATUS_UNKNOWN"),
        ("inflight_409", 409, "REQUEST_IN_FLIGHT"),
        ("incomplete_503", 503, "SERVICE_UNAVAILABLE"),
    ],
)
def test_outage_before_cutoff_then_retry_after_cutoff_is_uncertain_and_not_sent(
    route: str, outcome: str, status: int | None, code: str | None,
) -> None:
    async def run() -> None:
        clock = Clock(LAST_ORDER_SUBMISSION_UTC - timedelta(microseconds=1))
        wires: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            wires.append(request)
            clock.now = LAST_ORDER_SUBMISSION_UTC
            if outcome == "transport":
                raise httpx.ConnectError("fixture disconnected", request=request)
            return httpx.Response(
                cast(int, status), json={"error": {"code": code, "message": "fixture"}}
            )

        client = client_with_mock(httpx.MockTransport(respond), Governor(), clock)
        try:
            with pytest.raises(
                SigExecutionUncertainError, match="COMPETITION_CUTOFF_AFTER_DISPATCH"
            ):
                await place_raw(client, route)
            assert len(wires) == 1, "Never send a second retry beyond cutoff"
        finally:
            await client.aclose()
    asyncio.run(run())


def test_partial_batch_result_is_not_turned_into_an_imaginary_retry_fill() -> None:
    """A 207 partially accepted batch must not be replayed as if rejected."""
    async def run() -> None:
        clock = Clock(LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=10))
        observed: list[bytes] = []

        def respond(request: httpx.Request) -> httpx.Response:
            observed.append(request.content)
            clock.now = LAST_ORDER_SUBMISSION_UTC
            return httpx.Response(
                207, json={"results": [
                    {"index": 0, "ok": True, "status": 200, "data": {"orderId": 21}},
                    {"index": 1, "ok": False, "status": 422, "data": {}},
                ]}
            )

        client = client_with_mock(httpx.MockTransport(respond), Governor(), clock)
        try:
            status, result = await place_raw(
                client, "/orders/batch", accepted=frozenset({200, 207, 422})
            )
            assert status == 207
            assert isinstance(result, dict)
            assert len(cast(list[Any], result["results"])) == 2
            assert len(observed) == 1
        finally:
            await client.aclose()
    asyncio.run(run())


def test_same_key_stays_byte_identical_across_retries_before_cutoff() -> None:
    async def run() -> None:
        clock = Clock(LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=30))
        payloads: list[bytes] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payloads.append(request.content)
            if len(payloads) == 1:
                return httpx.Response(502, json={
                    "error": {"code": "ORDER_STATUS_UNKNOWN", "message": "unknown"}
                })
            return httpx.Response(200, json={"ok": True})

        client = client_with_mock(httpx.MockTransport(respond), Governor(), clock)
        try:
            status, _ = await place_raw(client, "/orders")
            assert status == 200
            assert len(payloads) == 2
            assert payloads[0] == payloads[1]
            assert b"unchanged-key" in payloads[0]
        finally:
            await client.aclose()
    asyncio.run(run())


def test_naive_clock_blocks_all_three_placement_routes_before_wire() -> None:
    async def run() -> None:
        clock = Clock(datetime(2026, 11, 4, 16, 30))
        wires: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            wires.append(request)
            return httpx.Response(200, json={"ok": True})

        client = client_with_mock(httpx.MockTransport(respond), Governor(), clock)
        try:
            for route in ("/orders", "/orders/batch", "/orders/multi-leg"):
                with pytest.raises(SigApiError, match="COMPETITION_CLOCK_UNTRUSTED"):
                    await place_raw(client, route)
            assert not wires
        finally:
            await client.aclose()
    asyncio.run(run())


def test_post_lock_cancellation_and_read_only_remain_usable() -> None:
    async def run() -> None:
        clock = Clock(COMPETITION_END_UTC + timedelta(days=1))
        routes: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            routes.append(request.url.path)
            return httpx.Response(200, json={"ok": True})

        client = client_with_mock(httpx.MockTransport(respond), Governor(), clock)
        try:
            for method, path in (
                ("POST", "/orders/cancel-all"),
                ("DELETE", "/orders/123"),
                ("GET", "/portfolio/fills"),
            ):
                status, _ = await client._request_json(
                    method, path.lstrip("/"), route_template=path,
                    resolved_payload={} if method == "POST" else None,
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                )
                assert status == 200
            assert routes == ["/orders/cancel-all", "/orders/123", "/portfolio/fills"]
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known FAIL: clock rollback across restart has no monotonic persisted "
        "watermark or remote trusted time; after-cutoff placement can reopen"
    ),
)
def test_rollback_clock_after_restart_must_not_reopen_submission() -> None:
    """Expected FAIL; keep gate red until independent trusted-time proof."""
    async def run() -> None:
        wires: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            wires.append(request)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(respond)
        late_clock = Clock(COMPETITION_END_UTC + timedelta(seconds=1))
        first = client_with_mock(transport, Governor(), late_clock)
        try:
            with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
                await place_raw(first, "/orders")
        finally:
            await first.aclose()
        assert wires == []

        # A restart into a mis-set early system clock must not resurrect LIVE.
        rebooted_clock = Clock(LAST_ORDER_SUBMISSION_UTC - timedelta(minutes=30))
        second = client_with_mock(transport, Governor(), rebooted_clock)
        try:
            with pytest.raises(SigApiError, match="COMPETITION_ORDER_CLOSED"):
                await place_raw(second, "/orders")
        finally:
            await second.aclose()
        assert not wires
    asyncio.run(run())
