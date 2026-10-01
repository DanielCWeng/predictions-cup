from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.models import PriceKind
from predictions_cup.sig import (
    RetryPolicy,
    SigApiError,
    SigAuthenticationError,
    SigAuthorizationError,
    SigClientRequestError,
    SigMalformedResponseError,
    SigNotFoundError,
    SigRateLimitError,
    SigRestClient,
    SigUnexpectedServerError,
)


def _settings() -> AppSettings:
    return AppSettings(
        sig_read_credential=SecretStr("read-secret"),
        sig_trade_credential=SecretStr("trade-secret"),
        tournament_id="configured-tournament-must-not-be-injected",
    )


def _account_payload() -> dict[str, object]:
    return {
        "id": "profile-1",
        "username": "reader",
        "email": None,
        "createdAt": "2026-09-24T23:00:00.000Z",
        "avatarUrl": None,
        "bio": None,
        "balance": "1000.25",
    }


def _public_market(market_id: str = "opaque-market") -> dict[str, object]:
    return {
        "id": market_id,
        "title": "Will the event occur?",
        "thumbnailUrl": None,
        "status": "open",
        "createdAt": "2026-09-24T20:00:00.000Z",
        "settlementDate": None,
        "settledWith": None,
        "settledOn": None,
        "categories": ["Election Outcome"],
        "isComposite": False,
        "isMultiOutcome": True,
        "exchanges": [
            {"id": "opaque-yes", "option": "YES", "latestPrice": "0.41", "initialPrice": 0.5},
            {"id": "opaque-no", "option": "NO", "latestPrice": "0.59", "initialPrice": 0.5},
        ],
        "contexts": [
            {
                "type": "public",
                "tournament": None,
                "status": "open",
                "settledWith": None,
                "settledOn": None,
                "exchanges": [
                    {"id": "opaque-yes", "latestPrice": "0.41"},
                    {"id": "opaque-no", "latestPrice": "0.59"},
                ],
            }
        ],
        "creator": None,
    }


def _market_node_leaf(*, position: int = 0) -> dict[str, object]:
    return {
        "nodeType": "contract",
        "position": position,
        "title": "Will the event occur?",
        "settlementDate": "2026-12-31T23:59:59.000Z",
        "contractType": "Freeform",
        "contractDetails": {
            "contractType": "Freeform",
            "description": "Resolves YES if the event occurs.",
            "providerSpecific": {"nested": True},
        },
        "settlementOptions": ["YES"],
        "settledWith": None,
        "settledOn": None,
    }


def _market_nodes_payload() -> dict[str, object]:
    return {
        "market_id": "26",
        "root": {
            "nodeType": "operator",
            "position": 0,
            "operator": "AND",
            "children": [
                _market_node_leaf(position=0),
                {
                    "nodeType": "operator",
                    "position": 1,
                    "operator": "OR",
                    "children": [_market_node_leaf(position=0)],
                },
            ],
        },
        "contexts": [],
    }


def _assert_market_nodes_rejected(payload: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SigMalformedResponseError):
                await client.get_market_nodes("26")

    asyncio.run(scenario())


def _price_payload(exchange_id: str = "36") -> dict[str, object]:
    return {
        "exchangeId": exchange_id,
        "marketId": "26",
        "option": "YES",
        "latestPrice": "0.42",
        "bestBid": "0.41",
        "bestAsk": "0.55",
        "spread": "0.14",
    }


def _error_payload(code: str, message: str = "safe error") -> dict[str, object]:
    return {"error": {"code": code, "message": message, "details": {"field": "safe"}}}


def test_read_credential_is_bearer_and_secrets_do_not_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("Authorization"))
        return httpx.Response(401, json=_error_payload("UNAUTHORIZED", "credential rejected"))

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with SigRestClient(_settings(), transport=transport) as client:
            with pytest.raises(SigAuthenticationError) as caught:
                await client.get_account()
        assert "read-secret" not in str(caught.value)
        assert "trade-secret" not in str(caught.value)

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())

    assert seen_authorization == ["Bearer read-secret"]
    assert "read-secret" not in caplog.text
    assert "trade-secret" not in caplog.text


@pytest.mark.parametrize(
    ("status", "code", "expected_type"),
    [
        (401, "UNAUTHORIZED", SigAuthenticationError),
        (403, "FORBIDDEN", SigAuthorizationError),
        (404, "NOT_FOUND", SigNotFoundError),
        (429, "RATE_LIMITED", SigRateLimitError),
        (400, "VALIDATION_ERROR", SigClientRequestError),
        (500, "INTERNAL_ERROR", SigUnexpectedServerError),
    ],
)
def test_error_statuses_map_to_typed_errors(
    status: int, code: str, expected_type: type[SigApiError]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, json=_error_payload(code))

    async def scenario() -> None:
        async with SigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        ) as client:
            with pytest.raises(expected_type) as caught:
                await client.get_account()
        assert caught.value.status_code == status
        assert caught.value.code == code

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["TX_CONFLICT", "SERVICE_UNAVAILABLE"])
def test_documented_503_codes_retry_then_succeed(code: str) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json=_error_payload(code))
        return httpx.Response(200, json=_account_payload())

    async def scenario() -> None:
        async with SigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(
                max_attempts=3,
                base_delay_seconds=0.1,
                max_delay_seconds=1.0,
                jitter_ratio=0,
            ),
            sleep=fake_sleep,
        ) as client:
            account = await client.get_account()
        assert account.id == "profile-1"

    asyncio.run(scenario())
    assert attempts == 3
    assert sleeps == [0.1, 0.2]


def test_rate_limit_retries_are_bounded() -> None:
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(429, json=_error_payload("RATE_LIMITED"))

    async def scenario() -> None:
        async with SigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=3, jitter_ratio=0),
            sleep=fake_sleep,
        ) as client:
            with pytest.raises(SigRateLimitError):
                await client.get_account()

    asyncio.run(scenario())
    assert attempts == 3


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "VALIDATION_ERROR"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
    ],
)
def test_permanent_client_errors_do_not_retry(status: int, code: str) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx.Response(status, json=_error_payload(code))

    async def scenario() -> None:
        async with SigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=3),
        ) as client:
            with pytest.raises(SigApiError):
                await client.get_account()

    asyncio.run(scenario())
    assert attempts == 1


def test_transport_timeout_retries_are_bounded() -> None:
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    async def scenario() -> None:
        async with SigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=2, jitter_ratio=0),
            sleep=fake_sleep,
        ) as client:
            with pytest.raises(SigApiError):
                await client.get_account()

    asyncio.run(scenario())
    assert attempts == 2


def test_explicit_tournament_is_sent_and_none_does_not_inject_settings_context() -> None:
    queries: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params)
        return httpx.Response(200, json=_price_payload())

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            await client.get_exchange_price("36", tournament_id="caller-context")
            await client.get_exchange_price("36", tournament_id=None)

    asyncio.run(scenario())
    assert queries[0].get("tournamentId") == "caller-context"
    assert "tournamentId" not in queries[1]
    assert "configured-tournament-must-not-be-injected" not in str(queries)


def test_market_container_converts_with_multiple_opaque_exchanges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_public_market())

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            market_dto = await client.get_market("opaque-market")
        market = market_dto.to_canonical()
        assert market.market_id == "opaque-market"
        assert [exchange.exchange_id for exchange in market.exchanges] == [
            "opaque-yes",
            "opaque-no",
        ]
        assert [exchange.outcome_label for exchange in market.exchanges] == ["YES", "NO"]
        assert market.tournament is None

    asyncio.run(scenario())


def test_organization_market_conversion_requires_explicit_context() -> None:
    payload = _public_market()
    payload["contexts"] = [
        {
            "type": "tournament",
            "tournament": {
                "id": "tournament-a",
                "slug": "a",
                "name": "A",
                "currencyName": "Coins",
                "isOngoingPlay": False,
            },
            "exchanges": [{"id": "opaque-yes", "latestPrice": "0.42"}],
        },
        {
            "type": "tournament",
            "tournament": {
                "id": "tournament-b",
                "slug": "b",
                "name": "B",
                "currencyName": "Coins",
                "isOngoingPlay": False,
            },
            "exchanges": [{"id": "opaque-yes", "latestPrice": "0.44"}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            market_dto = await client.get_market("opaque-market")
        with pytest.raises(ValueError, match="explicit tournament_id"):
            market_dto.to_canonical()
        canonical = market_dto.to_canonical(tournament_id="tournament-b")
        assert canonical.tournament is not None
        assert canonical.tournament.tournament_id == "tournament-b"

    asyncio.run(scenario())


def test_null_exchange_option_is_preserved_in_transport_but_not_invented_canonically() -> None:
    payload = _public_market()
    exchanges = payload["exchanges"]
    assert isinstance(exchanges, list)
    first = exchanges[0]
    assert isinstance(first, dict)
    first["option"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            market_dto = await client.get_market("opaque-market")
        assert market_dto.exchanges[0].option is None
        with pytest.raises(ValueError, match="outcome_label"):
            market_dto.to_canonical()

    asyncio.run(scenario())


def test_market_nodes_parse_leaf_and_recursive_operator_structure() -> None:
    payload = _market_nodes_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            nodes = await client.get_market_nodes("26")

        assert nodes.root.node_type == "operator"
        assert nodes.root.operator == "AND"
        assert nodes.root.children is not None
        leaf = nodes.root.children[0]
        assert leaf.node_type == "contract"
        assert leaf.position == 0
        assert leaf.contract_details == {
            "contractType": "Freeform",
            "description": "Resolves YES if the event occurs.",
            "providerSpecific": {"nested": True},
        }

        nested_operator = nodes.root.children[1]
        assert nested_operator.children is not None
        assert nested_operator.children[0].node_type == "contract"

    asyncio.run(scenario())


def test_market_nodes_accept_valid_leaf_root() -> None:
    payload = {"market_id": "26", "root": _market_node_leaf(), "contexts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            nodes = await client.get_market_nodes("26")
        assert nodes.root.node_type == "contract"
        assert nodes.root.position == 0

    asyncio.run(scenario())


def test_market_nodes_reject_missing_node_type() -> None:
    payload = _market_nodes_payload()
    root = payload["root"]
    assert isinstance(root, dict)
    root.pop("nodeType")
    _assert_market_nodes_rejected(payload)


def test_market_nodes_accept_live_contract_identity_without_position() -> None:
    payload = _market_nodes_payload()
    root = payload["root"]
    assert isinstance(root, dict)
    root.pop("position")
    root["node_id"] = "152"
    root["contract_id"] = "152"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            nodes = await client.get_market_nodes("26")
        assert nodes.root.position is None
        assert nodes.root.node_id == "152"
        assert nodes.root.contract_id == "152"

    asyncio.run(scenario())


def test_market_nodes_reject_invalid_operator() -> None:
    payload = _market_nodes_payload()
    root = payload["root"]
    assert isinstance(root, dict)
    root["operator"] = "XOR"
    _assert_market_nodes_rejected(payload)


def test_market_nodes_reject_malformed_child() -> None:
    payload = _market_nodes_payload()
    root = payload["root"]
    assert isinstance(root, dict)
    root["children"] = [{"nodeType": "contract", "position": "not-an-integer"}]
    _assert_market_nodes_rejected(payload)


def test_market_nodes_reject_structurally_invalid_nested_operator() -> None:
    payload = _market_nodes_payload()
    root = payload["root"]
    assert isinstance(root, dict)
    root["children"] = [{"nodeType": "operator", "position": 1, "operator": "AND"}]
    _assert_market_nodes_rejected(payload)


def test_price_snapshot_keeps_latest_bid_ask_and_transport_spread() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_price_payload())

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            snapshot = await client.get_exchange_price("36")
        assert snapshot.spread == Decimal("0.14")
        observed_at = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
        prices = snapshot.to_canonical_prices(observed_at=observed_at)
        assert [(price.kind, price.value) for price in prices] == [
            (PriceKind.LAST_TRADE, Decimal("0.42")),
            (PriceKind.BEST_BID, Decimal("0.41")),
            (PriceKind.BEST_ASK, Decimal("0.55")),
        ]
        assert all(price.timestamp == observed_at for price in prices)

    asyncio.run(scenario())


def test_bulk_prices_preserve_request_order_and_missing_ids() -> None:
    seen_ids: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_ids.append(request.url.params.get("ids"))
        return httpx.Response(
            200,
            json={
                "data": [_price_payload("a"), _price_payload("b")],
                "missingIds": ["missing"],
            },
        )

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            response = await client.get_bulk_prices(["a", "b", "missing"])
        assert [item.exchange_id for item in response.data] == ["a", "b"]
        assert response.missing_ids == ("missing",)

    asyncio.run(scenario())
    assert seen_ids == ["a,b,missing"]


def test_bulk_prices_reject_more_than_documented_maximum() -> None:
    async def scenario() -> None:
        async with SigRestClient(
            _settings(), transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            with pytest.raises(ValueError, match="at most 100"):
                await client.get_bulk_prices([str(index) for index in range(101)])

    asyncio.run(scenario())


def test_orderbook_decimal_precision_ordering_and_explicit_observation_time() -> None:
    body = b"""{
        "exchangeId":"36","marketId":"26","depth":2,
        "bids":[{"price":0.4000000000000000001,"quantity":"10.25"},{"price":"0.39","quantity":20}],
        "asks":[{"price":"0.41","quantity":"5.5"},{"price":"0.42","quantity":7}],
        "bestBid":0.4000000000000000001,"bestAsk":"0.41","spread":"0.0099999999999999999"
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            snapshot = await client.get_orderbook("36", depth=2)
        assert snapshot.bids[0].price == Decimal("0.4000000000000000001")
        assert snapshot.bids[0].quantity == Decimal("10.25")
        observed_at = datetime(2026, 9, 25, 0, 5, tzinfo=UTC)
        canonical = snapshot.to_canonical(observed_at=observed_at)
        assert [level.price for level in canonical.bids] == [
            Decimal("0.4000000000000000001"),
            Decimal("0.39"),
        ]
        assert [level.price for level in canonical.asks] == [Decimal("0.41"), Decimal("0.42")]
        assert canonical.timestamp == observed_at
        assert canonical.revision is None

    asyncio.run(scenario())


def test_orderbook_rejects_upstream_ordering_drift() -> None:
    payload = {
        "exchangeId": "36",
        "marketId": "26",
        "depth": 2,
        "bids": [{"price": "0.39", "quantity": 1}, {"price": "0.40", "quantity": 1}],
        "asks": [],
        "bestBid": "0.40",
        "bestAsk": None,
        "spread": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SigMalformedResponseError):
                await client.get_orderbook("36")

    asyncio.run(scenario())


def test_trade_wire_timestamp_becomes_aware_utc_and_yes_no_is_not_aggressor_side() -> None:
    payload = {
        "exchangeId": "36",
        "marketId": "26",
        "from": "2026-09-24T20:00:00.000Z",
        "to": "2026-09-25T01:00:00.000Z",
        "data": [
            {
                "id": "trade-1",
                "createdAt": "2026-09-24T20:30:00-04:00",
                "price": "0.425",
                "size": 100,
                "side": "YES",
                "volume": "42.5",
            }
        ],
        "pagination": {"limit": 50, "hasMore": False, "nextCursor": None},
        "coverage": {"complete": True, "projectedThroughSequence": 184233},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            page = await client.get_trades_page("36")
        trade = page.data[0].to_canonical(exchange_id=page.exchange_id)
        assert trade.timestamp == datetime(2026, 9, 25, 0, 30, tzinfo=UTC)
        assert trade.price == Decimal("0.425")
        assert trade.quantity == Decimal("100")
        assert trade.side is None

    asyncio.run(scenario())


def test_market_pagination_preserves_opaque_cursor_across_iteration() -> None:
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        if cursor is None:
            return httpx.Response(
                200,
                json={
                    "data": [_public_market("m1")],
                    "pagination": {
                        "total": 2,
                        "limit": 1,
                        "hasMore": True,
                        "nextCursor": "opaque::cursor::A==",
                    },
                },
            )
        assert cursor == "opaque::cursor::A=="
        return httpx.Response(
            200,
            json={
                "data": [_public_market("m2")],
                "pagination": {"total": 2, "limit": 1, "hasMore": False, "nextCursor": None},
            },
        )

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            received = [market.id async for market in client.iter_markets(limit=1)]
        assert received == ["m1", "m2"]

    asyncio.run(scenario())
    assert cursors == [None, "opaque::cursor::A=="]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"id": "profile-only"}),
        httpx.Response(200, json={**_account_payload(), "unexpectedField": True}),
    ],
)
def test_malformed_success_responses_fail_visibly(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SigMalformedResponseError):
                await client.get_account()

    asyncio.run(scenario())


def test_malformed_error_body_fails_without_exposing_raw_payload() -> None:
    secretish_body = "upstream-body-that-must-not-be-in-error"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"unexpected": secretish_body})

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SigMalformedResponseError) as caught:
                await client.get_account()
        assert secretish_body not in str(caught.value)

    asyncio.run(scenario())


def test_market_context_accepts_current_settlement_fields() -> None:
    payload = _public_market()
    context = payload["contexts"][0]
    assert isinstance(context, dict)
    context["status"] = "settled"
    context["settledWith"] = "YES"
    context["settledOn"] = "2026-10-01T12:00:00.000Z"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        async with SigRestClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            market = await client.get_market("opaque-market")
        parsed = market.contexts[0]
        assert parsed.status == "settled"
        assert parsed.settled_with == "YES"
        assert parsed.settled_on == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    asyncio.run(scenario())
