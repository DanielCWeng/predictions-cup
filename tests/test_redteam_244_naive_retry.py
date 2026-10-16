"""Independent P0 #244 red-team regression: loss of clock timezone on retry.

An earlier SIG POST may have filled. If the following retry samples a timezone-
naive clock, the caller MUST receive SigExecutionUncertainError, never ordinary
SigApiError; otherwise LIVE sink marks its durable journal REJECTED and releases
the reservation. Only mocked HTTP is used; no venue credentials or API calls.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import cast

import httpx
import pytest

from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.competition_deadline import LAST_ORDER_SUBMISSION_UTC
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient


class _Governor:
    async def acquire(self, priority: object) -> None:
        del priority

    def record_response(
        self, code: int, *, retry_after_seconds: float | None
    ) -> None:
        del code, retry_after_seconds


@pytest.mark.parametrize("route", ("/orders", "/orders/batch", "/orders/multi-leg"))
@pytest.mark.parametrize("first_response", ("502", "429", "transport"))
def test_naive_clock_after_possible_placement_remains_uncertain(
    route: str, first_response: str,
) -> None:
    async def scenario() -> None:
        current = LAST_ORDER_SUBMISSION_UTC - timedelta(seconds=1)
        wires: list[bytes] = []

        def mock_http(request: httpx.Request) -> httpx.Response:
            nonlocal current
            wires.append(request.content)
            # Synthetic abrupt time-source timezone loss after first request.
            current = datetime(2026, 11, 4, 16, 59, 54)
            if first_response == "transport":
                raise httpx.ConnectError("fixture lost acknowledgement", request=request)
            if first_response == "429":
                return httpx.Response(
                    429,
                    json={"error": {"code": "RATE_LIMITED", "message": "retry"}},
                    headers={"Retry-After": "0"},
                )
            return httpx.Response(
                502, json={"error": {"code": "ORDER_STATUS_UNKNOWN", "message": "unknown"}},
            )

        client = object.__new__(SigTradingClient)
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(mock_http),
            base_url="https://invalid.sig.example/",
        )
        client._governor = cast(SigRestGovernor, _Governor())
        client._wall_clock = lambda: current
        client._retry_policy = RetryPolicy(
            max_attempts=3, base_delay_seconds=0, max_delay_seconds=0,
            jitter_ratio=0,
        )
        client._sleep = asyncio.sleep
        client._timeout_seconds = 0.01
        client._request_in_flight_wait_seconds = 0
        try:
            # This expectation fails on #244: source raises ordinary SigApiError
            # instead, and LIVE sink's generic handler releases possible fills.
            with pytest.raises(
                SigExecutionUncertainError, match="COMPETITION_CLOCK_UNTRUSTED",
            ):
                await client._request_json(
                    "POST",
                    route.lstrip("/"),
                    route_template=route,
                    resolved_payload={"idempotencyKey": "never-change"},
                    accepted_statuses=frozenset({200}),
                    execution_can_be_uncertain=True,
                    resume_incomplete_batch=route == "/orders/batch",
                )
            assert len(wires) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())
