"""Authenticated, read-only asynchronous client for The Super Market REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.sig.dto import (
    AccountDto,
    BulkPricesDto,
    ExchangeListItemDto,
    ExchangePageDto,
    ExchangeTradeDto,
    MarketDto,
    MarketNodesDto,
    MarketPageDto,
    OrderBookSnapshotDto,
    PriceHistoryDto,
    PriceSnapshotDto,
    Resolution,
    TradePageDto,
)
from predictions_cup.sig.errors import (
    SigApiError,
    SigMalformedResponseError,
    SigRateLimitError,
    SigTemporaryServiceError,
    SigTransportError,
    error_from_payload,
)
from predictions_cup.sig.realtime_models import (
    RealtimeTokenDto,
    TournamentLeaderboardDto,
    TournamentListStatus,
    TournamentPageDto,
)
from predictions_cup.sig.trading_dto import (
    OrderFillsResponseDto,
    OrderPageDto,
    OrderReadDto,
    OrderStatusFilter,
    PortfolioFillPageDto,
    PositionsResponseDto,
)

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)
SleepFn = Callable[[float], Awaitable[None]]
MarketSort = Literal["recent", "trending", "closing", "oldest"]
MarketStatusFilter = Literal["open", "closed", "settled", "resolved", "any"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 1.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.jitter_ratio < 0:
            raise ValueError("jitter_ratio must be non-negative")


class SigRestClient:
    """Reusable authenticated read-only SIG REST client with a shared connection pool."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        timeout_seconds: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        credential = settings.sig_read_credential
        if credential is None:
            raise ValueError("SIG read credential is required to construct SigRestClient")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=str(settings.sig_api_base_url).rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {credential.get_secret_value()}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def __aenter__(self) -> SigRestClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_account(self) -> AccountDto:
        payload = await self._get_json("account", route_template="/account")
        return self._validate(AccountDto, payload, route_template="/account")

    async def list_tournaments(
        self,
        *,
        status: TournamentListStatus = "any",
        limit: int = 50,
        offset: int = 0,
    ) -> TournamentPageDto:
        self._require_range("limit", limit, 1, 100)
        if offset < 0:
            raise ValueError("offset must be non-negative")
        payload = await self._get_json(
            "tournaments",
            params={"status": status, "limit": limit, "offset": offset},
            route_template="/tournaments",
        )
        return self._validate(TournamentPageDto, payload, route_template="/tournaments")

    async def mint_realtime_token(self) -> RealtimeTokenDto:
        payload = await self._post_json(
            "realtime/token", route_template="/realtime/token"
        )
        return self._validate(
            RealtimeTokenDto, payload, route_template="/realtime/token"
        )

    async def get_tournament_leaderboard(
        self,
        tournament_slug: str,
        *,
        period: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> TournamentLeaderboardDto:
        self._require_identifier("tournament_slug", tournament_slug)
        if period not in {"1d", "7d", "30d", "all"}:
            raise ValueError("period must be one of 1d, 7d, 30d, all")
        self._require_range("limit", limit, 1, 100)
        if offset < 0:
            raise ValueError("offset must be non-negative")
        payload = await self._get_json(
            f"tournaments/{tournament_slug}/leaderboard",
            params={"period": period, "limit": limit, "offset": offset},
            route_template="/tournaments/{slug}/leaderboard",
        )
        return self._validate(
            TournamentLeaderboardDto,
            payload,
            route_template="/tournaments/{slug}/leaderboard",
        )

    async def list_markets(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        tournament_id: str | None = None,
        search: str | None = None,
        sort: MarketSort = "recent",
        status: MarketStatusFilter = "any",
        category: str | None = None,
        ids: Sequence[str] | None = None,
        is_composite: bool | None = None,
        is_multi_outcome: bool | None = None,
    ) -> MarketPageDto:
        self._require_range("limit", limit, 1, 100)
        params: dict[str, str | int] = {"limit": limit, "sort": sort, "status": status}
        self._put_optional(params, "cursor", cursor)
        self._put_optional(params, "tournamentId", tournament_id)
        self._put_optional(params, "search", search)
        self._put_optional(params, "category", category)
        if ids is not None:
            params["ids"] = self._join_ids(ids)
        if is_composite is not None:
            params["is_composite"] = self._bool_query(is_composite)
        if is_multi_outcome is not None:
            params["is_multi_outcome"] = self._bool_query(is_multi_outcome)
        payload = await self._get_json("markets", params=params, route_template="/markets")
        return self._validate(MarketPageDto, payload, route_template="/markets")

    async def iter_markets(
        self,
        *,
        limit: int = 100,
        tournament_id: str | None = None,
        search: str | None = None,
        sort: MarketSort = "recent",
        status: MarketStatusFilter = "any",
    ) -> AsyncIterator[MarketDto]:
        cursor: str | None = None
        while True:
            page = await self.list_markets(
                limit=limit,
                cursor=cursor,
                tournament_id=tournament_id,
                search=search,
                sort=sort,
                status=status,
            )
            for market in page.data:
                yield market
            if not page.pagination.has_more:
                return
            cursor = self._require_next_cursor(page.pagination.next_cursor, "/markets")

    async def get_market(self, market_id: str, *, tournament_id: str | None = None) -> MarketDto:
        self._require_identifier("market_id", market_id)
        params = self._tournament_params(tournament_id)
        payload = await self._get_json(
            f"markets/{market_id}", params=params, route_template="/markets/{id}"
        )
        return self._validate(MarketDto, payload, route_template="/markets/{id}")

    async def get_market_nodes(
        self, market_id: str, *, tournament_id: str | None = None
    ) -> MarketNodesDto:
        self._require_identifier("market_id", market_id)
        params = self._tournament_params(tournament_id)
        payload = await self._get_json(
            f"markets/{market_id}/nodes",
            params=params,
            route_template="/markets/{id}/nodes",
        )
        return self._validate(MarketNodesDto, payload, route_template="/markets/{id}/nodes")

    async def list_exchanges(
        self,
        *,
        market_id: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        tournament_id: str | None = None,
    ) -> ExchangePageDto:
        self._require_range("limit", limit, 1, 200)
        if market_id is not None and ids is not None:
            raise ValueError("market_id and ids are mutually exclusive")
        params: dict[str, str | int] = {"limit": limit}
        self._put_optional(params, "marketId", market_id)
        if ids is not None:
            params["ids"] = self._join_ids(ids, maximum=100)
        self._put_optional(params, "cursor", cursor)
        self._put_optional(params, "tournamentId", tournament_id)
        payload = await self._get_json("exchanges", params=params, route_template="/exchanges")
        return self._validate(ExchangePageDto, payload, route_template="/exchanges")

    async def iter_exchanges(
        self,
        *,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[ExchangeListItemDto]:
        cursor: str | None = None
        while True:
            page = await self.list_exchanges(
                market_id=market_id,
                limit=limit,
                cursor=cursor,
                tournament_id=tournament_id,
            )
            for exchange in page.data:
                yield exchange
            if not page.pagination.has_more:
                return
            cursor = self._require_next_cursor(page.pagination.next_cursor, "/exchanges")

    async def get_exchange_price(
        self, exchange_id: str, *, tournament_id: str | None = None
    ) -> PriceSnapshotDto:
        self._require_identifier("exchange_id", exchange_id)
        payload = await self._get_json(
            f"exchanges/{exchange_id}/price",
            params=self._tournament_params(tournament_id),
            route_template="/exchanges/{id}/price",
        )
        return self._validate(PriceSnapshotDto, payload, route_template="/exchanges/{id}/price")

    async def get_bulk_prices(
        self, exchange_ids: Sequence[str], *, tournament_id: str | None = None
    ) -> BulkPricesDto:
        params: dict[str, str | int] = {"ids": self._join_ids(exchange_ids, maximum=100)}
        self._put_optional(params, "tournamentId", tournament_id)
        payload = await self._get_json(
            "exchanges/prices", params=params, route_template="/exchanges/prices"
        )
        return self._validate(BulkPricesDto, payload, route_template="/exchanges/prices")

    async def get_orderbook(
        self,
        exchange_id: str,
        *,
        depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto:
        self._require_identifier("exchange_id", exchange_id)
        self._require_range("depth", depth, 1, 200)
        params: dict[str, str | int] = {"depth": depth}
        self._put_optional(params, "tournamentId", tournament_id)
        payload = await self._get_json(
            f"exchanges/{exchange_id}/orderbook",
            params=params,
            route_template="/exchanges/{id}/orderbook",
        )
        return self._validate(
            OrderBookSnapshotDto, payload, route_template="/exchanges/{id}/orderbook"
        )

    async def get_price_history(
        self,
        exchange_id: str,
        *,
        tournament_id: str | None = None,
        resolution: Resolution = "1h",
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 200,
    ) -> PriceHistoryDto:
        self._require_identifier("exchange_id", exchange_id)
        self._require_range("limit", limit, 1, 1000)
        params: dict[str, str | int] = {"resolution": resolution, "limit": limit}
        self._put_optional(params, "tournamentId", tournament_id)
        if from_ is not None:
            params["from"] = self._format_datetime(from_)
        if to is not None:
            params["to"] = self._format_datetime(to)
        payload = await self._get_json(
            f"exchanges/{exchange_id}/price-history",
            params=params,
            route_template="/exchanges/{id}/price-history",
        )
        return self._validate(
            PriceHistoryDto, payload, route_template="/exchanges/{id}/price-history"
        )

    async def get_trades_page(
        self,
        exchange_id: str,
        *,
        tournament_id: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TradePageDto:
        self._require_identifier("exchange_id", exchange_id)
        self._require_range("limit", limit, 1, 200)
        params: dict[str, str | int] = {"limit": limit}
        self._put_optional(params, "tournamentId", tournament_id)
        self._put_optional(params, "cursor", cursor)
        if from_ is not None:
            params["from"] = self._format_datetime(from_)
        if to is not None:
            params["to"] = self._format_datetime(to)
        payload = await self._get_json(
            f"exchanges/{exchange_id}/trades",
            params=params,
            route_template="/exchanges/{id}/trades",
        )
        return self._validate(TradePageDto, payload, route_template="/exchanges/{id}/trades")

    async def iter_trades(
        self,
        exchange_id: str,
        *,
        tournament_id: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 200,
    ) -> AsyncIterator[ExchangeTradeDto]:
        cursor: str | None = None
        while True:
            page = await self.get_trades_page(
                exchange_id,
                tournament_id=tournament_id,
                from_=from_,
                to=to,
                limit=limit,
                cursor=cursor,
            )
            for trade in page.data:
                yield trade
            if not page.pagination.has_more:
                return
            cursor = self._require_next_cursor(
                page.pagination.next_cursor, "/exchanges/{id}/trades"
            )

    async def list_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderPageDto:
        self._require_range("limit", limit, 1, 200)
        if exchange_id is not None and market_id is not None:
            raise ValueError("exchange_id and market_id are mutually exclusive")
        params: dict[str, str | int] = {"status": status, "limit": limit}
        self._put_optional(params, "exchangeId", exchange_id)
        self._put_optional(params, "marketId", market_id)
        self._put_optional(params, "tournamentId", tournament_id)
        self._put_optional(params, "cursor", cursor)
        payload = await self._get_json(
            "orders",
            params=params,
            route_template="/orders",
        )
        return self._validate(OrderPageDto, payload, route_template="/orders")

    async def iter_orders(
        self,
        *,
        status: OrderStatusFilter = "open",
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 200,
    ) -> AsyncIterator[OrderReadDto]:
        cursor: str | None = None
        while True:
            page = await self.list_orders(
                status=status,
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id=tournament_id,
                limit=limit,
                cursor=cursor,
            )
            for order in page.data:
                yield order
            if not page.pagination.has_more:
                return
            cursor = self._require_next_cursor(page.pagination.next_cursor, "/orders")

    async def get_order(self, order_id: int) -> OrderReadDto:
        if order_id <= 0:
            raise ValueError("order_id must be positive")
        payload = await self._get_json(
            f"orders/{order_id}",
            route_template="/orders/{id}",
        )
        return self._validate(OrderReadDto, payload, route_template="/orders/{id}")

    async def get_order_fills(
        self,
        order_id: int,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> OrderFillsResponseDto:
        if order_id <= 0:
            raise ValueError("order_id must be positive")
        self._require_range("limit", limit, 1, 200)
        params: dict[str, str | int] = {"limit": limit}
        self._put_optional(params, "cursor", cursor)
        payload = await self._get_json(
            f"orders/{order_id}/fills",
            params=params,
            route_template="/orders/{id}/fills",
        )
        return self._validate(
            OrderFillsResponseDto,
            payload,
            route_template="/orders/{id}/fills",
        )

    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto:
        self._require_range("limit", limit, 1, 200)
        if exchange_id is not None and market_id is not None:
            raise ValueError("exchange_id and market_id are mutually exclusive")
        params: dict[str, str | int] = {"limit": limit}
        self._put_optional(params, "exchangeId", exchange_id)
        self._put_optional(params, "marketId", market_id)
        self._put_optional(params, "tournamentId", tournament_id)
        self._put_optional(params, "cursor", cursor)
        payload = await self._get_json(
            "portfolio/fills",
            params=params,
            route_template="/portfolio/fills",
        )
        return self._validate(
            PortfolioFillPageDto,
            payload,
            route_template="/portfolio/fills",
        )

    async def get_default_positions(self) -> PositionsResponseDto:
        """Read the API key's default context only; never use for an explicit tournament."""
        payload = await self._get_json(
            "portfolio/positions",
            route_template="/portfolio/positions",
        )
        return self._validate(
            PositionsResponseDto,
            payload,
            route_template="/portfolio/positions",
        )

    async def get_tournament_positions(self, tournament_slug: str) -> PositionsResponseDto:
        self._require_identifier("tournament_slug", tournament_slug)
        payload = await self._get_json(
            f"tournaments/{tournament_slug}/portfolio/positions",
            route_template="/tournaments/{slug}/portfolio/positions",
        )
        return self._validate(
            PositionsResponseDto,
            payload,
            route_template="/tournaments/{slug}/portfolio/positions",
        )

    async def _get_json(
        self,
        path: str,
        *,
        route_template: str,
        params: dict[str, str | int] | None = None,
    ) -> object:
        policy = self._retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            started = time.monotonic()
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                self._log_transport_failure(route_template, attempt, started, exc)
                if attempt >= policy.max_attempts:
                    raise SigTransportError(
                        status_code=None,
                        code=None,
                        safe_message="SIG REST transport failed after bounded retries",
                    ) from exc
                await self._sleep(self._retry_delay(attempt))
                continue

            latency_ms = round((time.monotonic() - started) * 1000, 3)
            logger.info(
                "SIG REST response",
                extra={
                    "sig_method": "GET",
                    "sig_endpoint": route_template,
                    "sig_status": response.status_code,
                    "sig_attempt": attempt,
                    "sig_latency_ms": latency_ms,
                },
            )
            payload = self._decode_json(response, route_template=route_template)
            if response.is_success:
                return payload

            error = error_from_payload(status_code=response.status_code, payload=payload)
            if self._is_retryable(error) and attempt < policy.max_attempts:
                logger.warning(
                    "SIG REST retry",
                    extra={
                        "sig_method": "GET",
                        "sig_endpoint": route_template,
                        "sig_status": response.status_code,
                        "sig_attempt": attempt,
                        "sig_retry_reason": error.code or type(error).__name__,
                    },
                )
                await self._sleep(self._retry_delay(attempt))
                continue
            raise error

        raise AssertionError("unreachable retry loop")

    async def _post_json(self, path: str, *, route_template: str) -> object:
        started = time.monotonic()
        try:
            response = await self._client.post(path)
        except httpx.TransportError as exc:
            logger.warning(
                "SIG REST transport failure",
                extra={
                    "sig_method": "POST",
                    "sig_endpoint": route_template,
                    "sig_status": None,
                    "sig_attempt": 1,
                    "sig_latency_ms": round((time.monotonic() - started) * 1000, 3),
                    "sig_retry_reason": type(exc).__name__,
                },
            )
            raise SigTransportError(
                status_code=None,
                code=None,
                safe_message="SIG REST transport failed",
            ) from exc

        logger.info(
            "SIG REST response",
            extra={
                "sig_method": "POST",
                "sig_endpoint": route_template,
                "sig_status": response.status_code,
                "sig_attempt": 1,
                "sig_latency_ms": round((time.monotonic() - started) * 1000, 3),
            },
        )
        payload = self._decode_json(response, route_template=route_template)
        if response.is_success:
            return payload
        raise error_from_payload(status_code=response.status_code, payload=payload)

    def _decode_json(self, response: httpx.Response, *, route_template: str) -> object:
        try:
            text = response.content.decode("utf-8")
            payload: object = json.loads(text, parse_float=Decimal)
            return payload
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SigMalformedResponseError(
                status_code=response.status_code,
                code=None,
                safe_message=f"SIG returned invalid JSON for {route_template}",
            ) from exc

    @staticmethod
    def _validate(model_type: type[ModelT], payload: object, *, route_template: str) -> ModelT:
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise SigMalformedResponseError(
                status_code=200,
                code=None,
                safe_message=f"SIG success payload failed schema validation for {route_template}",
            ) from exc

    def _retry_delay(self, failed_attempt: int) -> float:
        policy = self._retry_policy
        exponential: float = policy.base_delay_seconds * (2.0 ** (failed_attempt - 1))
        base: float = min(policy.max_delay_seconds, exponential)
        if base == 0 or policy.jitter_ratio == 0:
            return base
        jitter: float = base * policy.jitter_ratio * random.random()
        return min(policy.max_delay_seconds, base + jitter)

    @staticmethod
    def _is_retryable(error: SigApiError) -> bool:
        if isinstance(error, SigRateLimitError):
            return error.code == "RATE_LIMITED"
        if isinstance(error, SigTemporaryServiceError):
            return error.code in {"TX_CONFLICT", "SERVICE_UNAVAILABLE"}
        return False

    @staticmethod
    def _log_transport_failure(
        route_template: str,
        attempt: int,
        started: float,
        error: httpx.TransportError,
    ) -> None:
        logger.warning(
            "SIG REST transport failure",
            extra={
                "sig_method": "GET",
                "sig_endpoint": route_template,
                "sig_status": None,
                "sig_attempt": attempt,
                "sig_latency_ms": round((time.monotonic() - started) * 1000, 3),
                "sig_retry_reason": type(error).__name__,
            },
        )

    @staticmethod
    def _tournament_params(tournament_id: str | None) -> dict[str, str | int]:
        params: dict[str, str | int] = {}
        SigRestClient._put_optional(params, "tournamentId", tournament_id)
        return params

    @staticmethod
    def _put_optional(params: dict[str, str | int], key: str, value: str | None) -> None:
        if value is not None:
            SigRestClient._require_identifier(key, value)
            params[key] = value

    @staticmethod
    def _require_identifier(name: str, value: str) -> None:
        if not value.strip():
            raise ValueError(f"{name} must not be blank")

    @staticmethod
    def _join_ids(ids: Sequence[str], *, maximum: int | None = None) -> str:
        values = tuple(ids)
        if not values:
            raise ValueError("ids must contain at least one identifier")
        if maximum is not None and len(values) > maximum:
            raise ValueError(f"ids may contain at most {maximum} identifiers")
        for value in values:
            SigRestClient._require_identifier("id", value)
        return ",".join(values)

    @staticmethod
    def _require_range(name: str, value: int, minimum: int, maximum: int) -> None:
        if value < minimum or value > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum} inclusive")

    @staticmethod
    def _bool_query(value: bool) -> str:
        return "true" if value else "false"

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("query timestamp must be timezone aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _require_next_cursor(cursor: str | None, route_template: str) -> str:
        if cursor is None:
            raise SigMalformedResponseError(
                status_code=200,
                code=None,
                safe_message=(
                    f"SIG pagination for {route_template} reports hasMore without nextCursor"
                ),
            )
        return cursor
