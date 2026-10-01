from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.errors import (
    SigAuthorizationError,
    SigExecutionUncertainError,
)
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    MultiLegOrderRequestDto,
    SingleOrderRequestDto,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _settings() -> AppSettings:
    return AppSettings(
        sig_read_credential=SecretStr("read-secret"),
        sig_trade_credential=SecretStr("trade-secret"),
    )


def _error(code: str, message: str = "safe") -> dict[str, object]:
    return {"error": {"code": code, "message": message, "details": {}}}


def _single_success() -> dict[str, object]:
    return {
        "orderId": 91,
        "exchangeId": "36",
        "open": True,
        "remainingQuantity": "1",
        "action": "buy",
        "side": "yes",
        "price": "0.5",
        "quantity": 1,
        "terminalReasonCode": None,
        "quantityTraded": "0",
        "totalCost": "0",
        "fillPrice": None,
        "all": None,
    }


def _order_input(*, price: float | None = 0.5) -> dict[str, object]:
    payload: dict[str, object] = {
        "exchangeId": "36",
        "side": "yes",
        "action": "buy",
        "quantity": 1,
        "tournamentId": "t1",
    }
    if price is not None:
        payload["price"] = price
    return payload


async def _with_client(
    handler: Handler,
    scenario: Callable[[SigTradingClient], object],
    *,
    retry_policy: RetryPolicy | None = None,
    sleep: Callable[[float], object] | None = None,
    request_in_flight_wait_seconds: float = 0.0,
) -> object:
    governor = SigRestGovernor(rate_per_second=1_000_000_000.0)
    async def default_sleep(delay: float) -> None:
        del delay

    async def run_sleep(delay: float) -> None:
        if sleep is None:
            await default_sleep(delay)
        else:
            result = sleep(delay)
            if asyncio.iscoroutine(result):
                await result

    try:
        async with SigTradingClient(
            _settings(),
            governor=governor,
            transport=httpx.MockTransport(handler),
            retry_policy=retry_policy or RetryPolicy(max_attempts=1, jitter_ratio=0),
            sleep=run_sleep,
            request_in_flight_wait_seconds=request_in_flight_wait_seconds,
        ) as client:
            result = scenario(client)
            if asyncio.iscoroutine(result):
                return await result
            return result
    finally:
        await governor.aclose()


def test_single_order_uses_exact_wire_fields_and_trade_credential() -> None:
    seen: list[tuple[str, str, dict[str, object], str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content),
                request.headers.get("Authorization"),
            )
        )
        return httpx.Response(200, json=_single_success())

    async def scenario(client: SigTradingClient) -> None:
        response = await client.place_order(
            SingleOrderRequestDto.model_validate(
                {"idempotencyKey": "single-1", **_order_input()}
            )
        )
        assert response.order_id == 91

    asyncio.run(_with_client(handler, scenario))

    assert seen == [
        (
            "POST",
            "/api/v1/orders",
            {
                "exchangeId": "36",
                "side": "yes",
                "action": "buy",
                "quantity": 1,
                "price": 0.5,
                "tournamentId": "t1",
                "idempotencyKey": "single-1",
            },
            "Bearer trade-secret",
        )
    ]


def test_market_order_prefers_omitted_price_representation() -> None:
    request = SingleOrderRequestDto.model_validate(
        {"idempotencyKey": "market-1", **_order_input(price=None)}
    )
    dumped = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "price" not in dumped


def test_off_tick_and_duplicate_multileg_are_rejected_locally() -> None:
    with pytest.raises(ValidationError):
        SingleOrderRequestDto.model_validate(
            {"idempotencyKey": "bad", **_order_input(price=0.501)}
        )

    with pytest.raises(ValidationError):
        MultiLegOrderRequestDto.model_validate(
            {
                "idempotencyKey": "multi",
                "legs": [_order_input(), _order_input()],
            }
        )


def test_batch_503_incomplete_resumes_with_same_key_and_ordered_payload() -> None:
    bodies: list[dict[str, object]] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        bodies.append(json.loads(request.content))
        if attempts == 1:
            return httpx.Response(
                503,
                json={
                    "results": [
                        {
                            "index": 0,
                            "ok": False,
                            "status": 502,
                            "data": {
                                "code": "ORDER_STATUS_UNKNOWN",
                                "error": "unknown",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "ok": True, "status": 200, "data": {"orderId": 9}}
                ]
            },
        )

    async def scenario(client: SigTradingClient) -> None:
        result = await client.place_batch(
            BatchOrderRequestDto.model_validate(
                {
                    "idempotencyKey": "batch-1",
                    "orders": [_order_input(), {**_order_input(), "exchangeId": "37"}],
                }
            )
        )
        assert result.results[0].ok is True

    asyncio.run(
        _with_client(
            handler,
            scenario,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
            ),
        )
    )
    assert attempts == 2
    assert bodies[0] == bodies[1]
    assert bodies[0]["idempotencyKey"] == "batch-1"
    orders = bodies[0]["orders"]
    assert isinstance(orders, list)
    assert [order["exchangeId"] for order in orders if isinstance(order, dict)] == [
        "36",
        "37",
    ]


def test_exhausted_incomplete_batch_becomes_explicitly_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            502,
            json={
                "results": [
                    {
                        "index": 0,
                        "ok": False,
                        "status": 502,
                        "data": {"code": "ORDER_STATUS_UNKNOWN", "error": "unknown"},
                    }
                ]
            },
        )

    async def scenario(client: SigTradingClient) -> None:
        with pytest.raises(SigExecutionUncertainError) as caught:
            await client.place_batch(
                BatchOrderRequestDto.model_validate(
                    {"idempotencyKey": "batch-unknown", "orders": [_order_input()]}
                )
            )
        assert caught.value.status_code == 502
        assert caught.value.code == "BATCH_INCOMPLETE"

    asyncio.run(_with_client(handler, scenario))


def test_request_in_flight_retries_identical_single_order_key() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(409, json=_error("REQUEST_IN_FLIGHT", "busy"))
        return httpx.Response(200, json=_single_success())

    async def scenario(client: SigTradingClient) -> None:
        await client.place_order(
            SingleOrderRequestDto.model_validate(
                {"idempotencyKey": "lease-1", **_order_input()}
            )
        )

    asyncio.run(
        _with_client(
            handler,
            scenario,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
                jitter_ratio=0,
            ),
            request_in_flight_wait_seconds=0.0,
        )
    )
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


def test_batch_request_timeout_covers_live_sig_batch_duration() -> None:
    read_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions["timeout"]
        assert isinstance(timeout, dict)
        read_timeouts.append(float(timeout["read"]))
        return httpx.Response(200, json={"results": []})

    async def scenario(client: SigTradingClient) -> None:
        await client.place_batch(
            BatchOrderRequestDto.model_validate(
                {"idempotencyKey": "slow-batch", "orders": [_order_input()]}
            )
        )

    asyncio.run(_with_client(handler, scenario))
    assert read_timeouts == [120.0]


def test_terms_not_acknowledged_is_operator_condition_not_auto_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "TERMS_NOT_ACKNOWLEDGED",
                    "message": "documents required",
                    "details": {"missingDocuments": [{"documentId": "doc-1"}]},
                }
            },
        )

    async def scenario(client: SigTradingClient) -> None:
        with pytest.raises(SigAuthorizationError) as caught:
            await client.place_order(
                SingleOrderRequestDto.model_validate(
                    {"idempotencyKey": "terms-1", **_order_input()}
                )
            )
        assert caught.value.code == "TERMS_NOT_ACKNOWLEDGED"

    asyncio.run(
        _with_client(
            handler,
            scenario,
            retry_policy=RetryPolicy(max_attempts=3, jitter_ratio=0),
        )
    )
    assert attempts == 1


def test_multileg_relationship_constraint_is_transported_exactly() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "ok": True, "data": {"orderId": 1}},
                    {"index": 1, "ok": True, "data": {"orderId": 2}},
                ]
            },
        )

    relationship = "22222222-2222-2222-2222-222222222222"

    async def scenario(client: SigTradingClient) -> None:
        await client.place_multi_leg(
            MultiLegOrderRequestDto.model_validate(
                {
                    "idempotencyKey": "multi-1",
                    "relationshipConstraint": relationship,
                    "legs": [
                        _order_input(),
                        {**_order_input(), "exchangeId": "37", "side": "no"},
                    ],
                }
            )
        )

    asyncio.run(_with_client(handler, scenario))
    assert seen[0]["relationshipConstraint"] == relationship
    assert seen[0]["idempotencyKey"] == "multi-1"
