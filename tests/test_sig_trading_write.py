from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.account_state import (
    AccountRealtimeStateEngine,
    AccountTrustTransition,
)
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    MultiLegOrderRequestDto,
    OrderInputDto,
    PositionReadDto,
    SingleOrderRequestDto,
)


def _settings() -> AppSettings:
    return AppSettings(
        sig_trade_credential=SecretStr("trade-secret"),
        sig_read_credential=SecretStr("read-secret"),
    )


def _single_response(*, open_: bool = True) -> dict[str, object]:
    return {
        "orderId": 1001,
        "exchangeId": "36",
        "open": open_,
        "remainingQuantity": 1 if open_ else 0,
        "action": "buy",
        "side": "yes",
        "price": 0.42,
        "quantity": 1,
        "terminalReasonCode": None,
        "quantityTraded": 0 if open_ else 1,
        "totalCost": 0 if open_ else 0.42,
        "fillPrice": None if open_ else 0.42,
        "all": None,
    }


def _error(code: str, message: str = "error") -> dict[str, object]:
    return {"error": {"code": code, "message": message}}


def test_order_input_enforces_documented_tick_and_market_semantics() -> None:
    limit = OrderInputDto(
        exchangeId="36",
        side="yes",
        action="buy",
        quantity=1,
        price=Decimal("0.420"),
        tournamentId="tournament-1",
    )
    assert limit.price == Decimal("0.420")

    market = OrderInputDto(
        exchangeId="36",
        side="yes",
        action="buy",
        quantity=1,
        price=Decimal("1"),
    )
    assert market.price == Decimal("1")

    with pytest.raises(ValidationError, match="0.005 tick"):
        OrderInputDto(
            exchangeId="36",
            side="yes",
            action="buy",
            quantity=1,
            price=Decimal("0.421"),
        )

    with pytest.raises(ValidationError, match="market orders cannot have expirationDate"):
        OrderInputDto.model_validate(
            {
                "exchangeId": "36",
                "side": "yes",
                "action": "buy",
                "quantity": 1,
                "price": Decimal("1"),
                "expirationDate": "2026-10-01T00:00:00Z",
            }
        )


def test_multi_leg_rejects_duplicate_exchange_in_same_scope() -> None:
    leg = OrderInputDto(
        exchangeId="36",
        side="yes",
        action="buy",
        quantity=1,
        price=Decimal("0.42"),
        tournamentId="tournament-1",
    )
    with pytest.raises(ValidationError, match="duplicate exchange"):
        MultiLegOrderRequestDto(
            legs=(leg, leg),
            idempotencyKey="multi-1",
        )


def test_same_single_order_payload_and_key_are_reused_after_503() -> None:
    payloads: list[dict[str, object]] = []
    attempts = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        body = json.loads(request.content)
        assert isinstance(body, dict)
        payloads.append(body)
        if attempts == 1:
            return httpx.Response(503, json=_error("SERVICE_UNAVAILABLE"))
        return httpx.Response(200, json=_single_response())

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0.1,
                    max_delay_seconds=1.0,
                    jitter_ratio=0,
                ),
                sleep=fake_sleep,
            ) as client:
                response = await client.place_order(
                    SingleOrderRequestDto(
                        exchangeId="36",
                        side="yes",
                        action="buy",
                        quantity=1,
                        price=Decimal("0.42"),
                        tournamentId="tournament-1",
                        idempotencyKey="logical-operation-1",
                    )
                )
                assert response.order_id == 1001
        finally:
            await governor.aclose()

    asyncio.run(scenario())
    assert attempts == 2
    assert payloads[0] == payloads[1]
    assert payloads[0]["idempotencyKey"] == "logical-operation-1"
    assert sleeps == [0.1]


def test_order_status_unknown_stays_uncertain_after_bounded_retry() -> None:
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        del request
        return httpx.Response(502, json=_error("ORDER_STATUS_UNKNOWN"))

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
                retry_policy=RetryPolicy(max_attempts=2, jitter_ratio=0),
                sleep=fake_sleep,
            ) as client:
                with pytest.raises(SigExecutionUncertainError) as caught:
                    await client.place_order(
                        SingleOrderRequestDto(
                            exchangeId="36",
                            side="yes",
                            action="buy",
                            quantity=1,
                            price=Decimal("0.42"),
                            idempotencyKey="unknown-1",
                        )
                    )
                assert caught.value.code == "ORDER_STATUS_UNKNOWN"
        finally:
            await governor.aclose()

    asyncio.run(scenario())
    assert attempts == 2


def test_batch_207_is_a_valid_terminal_envelope_not_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            207,
            json={
                "results": [
                    {"index": 0, "ok": True, "status": 200, "data": _single_response()},
                    {
                        "index": 1,
                        "ok": False,
                        "status": 400,
                        "data": _error("VALIDATION_ERROR"),
                    },
                ]
            },
        )

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
            ) as client:
                response = await client.place_batch(
                    BatchOrderRequestDto(
                        idempotencyKey="batch-1",
                        orders=(
                            OrderInputDto(
                                exchangeId="36",
                                side="yes",
                                action="buy",
                                quantity=1,
                                price=Decimal("0.42"),
                            ),
                            OrderInputDto(
                                exchangeId="37",
                                side="yes",
                                action="buy",
                                quantity=1,
                                price=Decimal("0.43"),
                            ),
                        ),
                    )
                )
                assert response.results[0].ok is True
                assert response.results[1].ok is False
        finally:
            await governor.aclose()

    asyncio.run(scenario())


def test_cancel_409_is_uncertain_until_order_and_fills_are_reconciled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(409, json=_error("CONFLICT", "already closed"))

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
                retry_policy=RetryPolicy(max_attempts=1),
            ) as client:
                with pytest.raises(SigExecutionUncertainError) as caught:
                    await client.cancel_order(1001)
                assert caught.value.status_code == 409
        finally:
            await governor.aclose()

    asyncio.run(scenario())


def _delivery(revision: int, previous: int) -> dict[str, object]:
    return {
        "revision": revision,
        "previousRevision": previous,
        "correlationId": f"batch-{revision}",
        "sourceSequenceFrom": revision,
        "sourceSequenceThrough": revision,
    }


def test_account_batch_revision_gap_fails_closed() -> None:
    engine = AccountRealtimeStateEngine(tournament_id="tournament-1")
    engine.trusted = True
    first = engine.handle_raw_batch(
        {
            "fills": [],
            "orderUpdates": [],
            "settlements": [],
            "refunds": [],
            "collateralChanges": [],
            "delivery": _delivery(1, 0),
        },
        observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    )
    assert first.accepted is True

    gap = engine.handle_raw_batch(
        {
            "fills": [],
            "orderUpdates": [],
            "settlements": [],
            "refunds": [],
            "collateralChanges": [],
            "delivery": _delivery(3, 2),
        },
        observed_at=datetime(2026, 9, 28, 20, 0, 1, tzinfo=UTC),
    )
    assert gap.requires_reconciliation is True
    assert engine.trusted is False
    assert engine.transition is AccountTrustTransition.UNTRUSTED_REVISION_GAP


def test_unknown_open_order_update_requires_authoritative_reconciliation() -> None:
    engine = AccountRealtimeStateEngine(tournament_id="tournament-1")
    engine.trusted = True
    result = engine.handle_raw_batch(
        {
            "fills": [],
            "orderUpdates": [
                {
                    "orderId": 1001,
                    "exchangeId": "36",
                    "marketId": "26",
                    "open": True,
                    "quantityTraded": 0,
                    "totalCost": 0,
                    "latestTradePrice": None,
                    "tournamentId": "tournament-1",
                    "at": "2026-09-28T20:00:00Z",
                }
            ],
            "settlements": [],
            "refunds": [],
            "collateralChanges": [],
            "delivery": _delivery(1, 0),
        },
        observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    )
    assert result.requires_reconciliation is True
    assert engine.transition is AccountTrustTransition.UNTRUSTED_UNKNOWN_OPEN_ORDER

@pytest.mark.parametrize(
    ("status_code", "operation"),
    (
        (200, "single"),
        (207, "batch"),
        (422, "cancel_all"),
    ),
)
def test_malformed_accepted_execution_response_is_uncertain(
    status_code: int,
    operation: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, json={"unexpected": True})

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
                retry_policy=RetryPolicy(max_attempts=1),
            ) as client:
                with pytest.raises(SigExecutionUncertainError) as caught:
                    if operation == "single":
                        await client.place_order(
                            SingleOrderRequestDto(
                                exchangeId="36",
                                side="yes",
                                action="buy",
                                quantity=1,
                                price=Decimal("0.42"),
                                idempotencyKey="malformed-single",
                            )
                        )
                    elif operation == "batch":
                        await client.place_batch(
                            BatchOrderRequestDto(
                                idempotencyKey="malformed-batch",
                                orders=(
                                    OrderInputDto(
                                        exchangeId="36",
                                        side="yes",
                                        action="buy",
                                        quantity=1,
                                        price=Decimal("0.42"),
                                    ),
                                ),
                            )
                        )
                    else:
                        await client.cancel_all(tournament_id="tournament-1")
                assert caught.value.status_code == status_code
        finally:
            await governor.aclose()

    asyncio.run(scenario())


def test_invalid_json_after_accepted_execution_is_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"{not-json")

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
                retry_policy=RetryPolicy(max_attempts=1),
            ) as client:
                with pytest.raises(SigExecutionUncertainError) as caught:
                    await client.place_order(
                        SingleOrderRequestDto(
                            exchangeId="36",
                            side="yes",
                            action="buy",
                            quantity=1,
                            price=Decimal("0.42"),
                            idempotencyKey="invalid-json",
                        )
                    )
                assert caught.value.status_code == 200
        finally:
            await governor.aclose()

    asyncio.run(scenario())


def test_pre_resolved_payload_bytes_are_sent_exactly() -> None:
    expected = (
        b'{"idempotencyKey":"wire-1","exchangeId":"36","side":"yes",'
        b'"action":"buy","quantity":1,"price":0.42,"tournamentId":"t1"}'
    )
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=_single_response())

    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1_000_000_000)
        try:
            async with SigTradingClient(
                _settings(),
                governor=governor,
                transport=httpx.MockTransport(handler),
            ) as client:
                result = await client.place_order_payload(expected.decode("utf-8"))
                assert result.order_id == 1001
        finally:
            await governor.aclose()

    asyncio.run(scenario())
    assert seen == [expected]


def test_realtime_fill_never_mutates_position_without_direction() -> None:
    engine = AccountRealtimeStateEngine(tournament_id="tournament-1")
    engine.trusted = True
    result = engine.handle_raw_batch(
        {
            "fills": [
                {
                    "orderId": 1001,
                    "exchangeId": "36",
                    "marketId": "26",
                    "price": "0.42",
                    "quantity": "5",
                    "executedAt": "2026-09-28T20:00:00Z",
                    "tournamentId": "tournament-1",
                }
            ],
            "orderUpdates": [],
            "settlements": [],
            "refunds": [],
            "collateralChanges": [],
            "delivery": _delivery(1, 0),
        },
        observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    )
    assert result.requires_reconciliation is True
    assert result.accepted is False
    assert engine.trusted is False
    assert (
        engine.transition
        is AccountTrustTransition.UNTRUSTED_FILL_REQUIRES_RECONCILIATION
    )
    assert engine.runtime_portfolio().positions == ()

def test_delayed_fill_after_rest_snapshot_does_not_double_apply_or_assume_direction() -> None:
    state = AccountRealtimeStateEngine(tournament_id="tournament-1")
    position = PositionReadDto.model_validate(
        {
            "exchangeId": "36",
            "marketId": "26",
            "marketTitle": "fixture",
            "option": "yes",
            "settled": False,
            "quantity": "5",
            "avgCost": "0.42",
            "currentPrice": "0.42",
            "marketValue": "2.10",
            "costBasis": "2.10",
            "unrealizedPnl": "0",
            "unrealizedPnlPct": "0",
            "moneyEarned": "0",
            "lots": [],
        }
    )
    state.apply_authoritative(
        AccountAuthoritativeSnapshot(
            tournament_id="tournament-1",
            tournament_slug="cup",
            open_orders=(),
            positions=(position,),
            observed_at=datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
        )
    )
    before = state.runtime_portfolio()
    assert before.positions[0].gross_exposure == 5.0

    # Realtime does not tell us whether this quantity is a buy or sell, and the
    # fill may already be reflected in the REST position above.
    result = state.handle_raw_batch(
        {
            "fills": [
                {
                    "orderId": 1001,
                    "exchangeId": "36",
                    "marketId": "26",
                    "price": "0.42",
                    "quantity": "2",
                    "executedAt": "2026-09-28T20:00:01Z",
                    "tournamentId": "tournament-1",
                }
            ],
            "orderUpdates": [],
            "settlements": [],
            "refunds": [],
            "collateralChanges": [],
            "delivery": _delivery(1, 0),
        },
        observed_at=datetime(2026, 9, 28, 20, 0, 1, tzinfo=UTC),
    )

    assert result.requires_reconciliation is True
    assert state.trusted is False
    after = state.runtime_portfolio()
    assert after.positions[0].gross_exposure == 5.0

