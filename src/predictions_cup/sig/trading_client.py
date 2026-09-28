"""Persistent SIG write adapter implementing the api-1.json trading contract."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from predictions_cup.config import AppSettings
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.errors import (
    SigApiError,
    SigConflictError,
    SigExecutionUncertainError,
    SigMalformedResponseError,
    SigRateLimitError,
    SigTemporaryServiceError,
    error_from_payload,
)
from predictions_cup.sig.rest_governor import RestPriority, SigRestGovernor
from predictions_cup.sig.trading_dto import (
    BatchOrderRequestDto,
    BatchOrderResponseDto,
    CancelAllResponseDto,
    MultiLegOrderRequestDto,
    MultiLegResponseDto,
    SingleOrderRequestDto,
    SingleOrderResponseDto,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
SleepFn = Callable[[float], Awaitable[None]]



def _wire_json_value(value: object) -> object:
    """Convert exact domain values to JSON-native SIG wire scalars."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _wire_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_json_value(item) for item in value]
    return value


def _request_wire_payload(model: BaseModel) -> dict[str, object]:
    raw = model.model_dump(mode="python", by_alias=True, exclude_none=True)
    return {key: _wire_json_value(value) for key, value in raw.items()}


class SigTradingClient:
    """Trade-scoped client. All retries reuse the already-resolved payload."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        governor: SigRestGovernor,
        timeout_seconds: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        request_in_flight_wait_seconds: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        credential = settings.sig_trade_credential
        if credential is None:
            raise ValueError("SIG trade credential is required to construct SigTradingClient")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if request_in_flight_wait_seconds < 0:
            raise ValueError("request_in_flight_wait_seconds must be non-negative")
        self._governor = governor
        self._retry_policy = retry_policy or RetryPolicy()
        self._request_in_flight_wait_seconds = request_in_flight_wait_seconds
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=str(settings.sig_api_base_url).rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {credential.get_secret_value()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def __aenter__(self) -> SigTradingClient:
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

    async def place_order(self, request: SingleOrderRequestDto) -> SingleOrderResponseDto:
        status, payload = await self._request_json(
            "POST",
            "orders",
            route_template="/orders",
            resolved_payload=_request_wire_payload(request),
            accepted_statuses=frozenset({200}),
            execution_can_be_uncertain=True,
        )
        del status
        return self._validate(SingleOrderResponseDto, payload, "/orders")

    async def place_batch(self, request: BatchOrderRequestDto) -> BatchOrderResponseDto:
        _, payload = await self._request_json(
            "POST",
            "orders/batch",
            route_template="/orders/batch",
            resolved_payload=_request_wire_payload(request),
            accepted_statuses=frozenset({200, 207, 422}),
            execution_can_be_uncertain=True,
            resume_incomplete_batch=True,
        )
        return self._validate(BatchOrderResponseDto, payload, "/orders/batch")

    async def place_multi_leg(
        self, request: MultiLegOrderRequestDto
    ) -> MultiLegResponseDto:
        _, payload = await self._request_json(
            "POST",
            "orders/multi-leg",
            route_template="/orders/multi-leg",
            resolved_payload=_request_wire_payload(request),
            accepted_statuses=frozenset({200}),
            execution_can_be_uncertain=True,
        )
        return self._validate(MultiLegResponseDto, payload, "/orders/multi-leg")

    async def cancel_order(self, order_id: int) -> object:
        if order_id <= 0:
            raise ValueError("order_id must be positive")
        try:
            _, payload = await self._request_json(
                "DELETE",
                f"orders/{order_id}",
                route_template="/orders/{id}",
                resolved_payload=None,
                accepted_statuses=frozenset({200}),
                execution_can_be_uncertain=True,
            )
        except SigConflictError as exc:
            # api-1.json documents 409 here as "already closed because filled or
            # cancelled by a concurrent request". That is economic state, not a
            # rejected cancellation; the caller must reconcile order + fills.
            raise SigExecutionUncertainError(
                status_code=409,
                code=exc.code,
                safe_message=(
                    "SIG order is already closed; reconcile order/fills before "
                    "classifying the cancellation"
                ),
                details=exc.details,
            ) from exc
        return payload

    async def cancel_all(
        self,
        *,
        tournament_id: str | None = None,
        exchange_id: str | None = None,
        market_id: str | None = None,
    ) -> CancelAllResponseDto:
        if exchange_id is not None and market_id is not None:
            raise ValueError("exchange_id and market_id are mutually exclusive")
        payload: dict[str, object] = {}
        if exchange_id is not None:
            payload["exchangeId"] = exchange_id
        if market_id is not None:
            payload["marketId"] = market_id
        if tournament_id is not None:
            payload["tournamentId"] = tournament_id
        _, response_payload = await self._request_json(
            "POST",
            "orders/cancel-all",
            route_template="/orders/cancel-all",
            resolved_payload=payload or None,
            accepted_statuses=frozenset({200, 207, 422}),
            execution_can_be_uncertain=True,
        )
        return self._validate(
            CancelAllResponseDto,
            response_payload,
            "/orders/cancel-all",
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        route_template: str,
        resolved_payload: dict[str, object] | None,
        accepted_statuses: frozenset[int],
        execution_can_be_uncertain: bool,
        resume_incomplete_batch: bool = False,
    ) -> tuple[int, object]:
        policy = self._retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            await self._governor.acquire(RestPriority.HIGH)
            try:
                if resolved_payload is None:
                    response = await self._client.request(method, path)
                else:
                    response = await self._client.request(
                        method,
                        path,
                        json=resolved_payload,
                    )
            except httpx.TransportError as exc:
                if attempt < policy.max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                if execution_can_be_uncertain:
                    raise SigExecutionUncertainError(
                        status_code=None,
                        code="TRANSPORT_OUTCOME_UNKNOWN",
                        safe_message=(
                            "SIG execution transport failed after dispatch; authoritative "
                            "reconciliation is required"
                        ),
                    ) from exc
                raise

            self._governor.record_response(
                response.status_code,
                retry_after_seconds=self._retry_after_seconds(response),
            )
            payload = self._decode_json(response, route_template)

            if response.status_code in accepted_statuses:
                return response.status_code, payload

            if resume_incomplete_batch and response.status_code in {502, 503}:
                if attempt < policy.max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                raise SigExecutionUncertainError(
                    status_code=response.status_code,
                    code=self._error_code(payload) or "BATCH_INCOMPLETE",
                    safe_message=(
                        "SIG best-effort batch remains incomplete after bounded same-key "
                        "retries; reconcile and resume only with the original key"
                    ),
                )

            if response.status_code == 502:
                code = self._error_code(payload)
                if code == "ORDER_STATUS_UNKNOWN" and attempt < policy.max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                if code == "ORDER_STATUS_UNKNOWN":
                    raise SigExecutionUncertainError(
                        status_code=502,
                        code=code,
                        safe_message=(
                            "SIG could not confirm execution status; preserve the original "
                            "idempotency key and reconcile authoritatively"
                        ),
                    )

            try:
                error = error_from_payload(
                    status_code=response.status_code,
                    payload=payload,
                )
            except SigMalformedResponseError as exc:
                if execution_can_be_uncertain and response.status_code >= 500:
                    raise SigExecutionUncertainError(
                        status_code=response.status_code,
                        code=None,
                        safe_message=(
                            "SIG returned an incomplete execution envelope; authoritative "
                            "reconciliation is required"
                        ),
                    ) from exc
                raise

            if (
                response.status_code == 409
                and error.code == "REQUEST_IN_FLIGHT"
            ):
                if attempt < policy.max_attempts:
                    await self._sleep(self._request_in_flight_wait_seconds)
                    continue
                raise SigExecutionUncertainError(
                    status_code=409,
                    code=error.code,
                    safe_message=(
                        "SIG idempotent request remains in flight; do not create a new key"
                    ),
                    details=error.details,
                )

            if self._retryable(error) and attempt < policy.max_attempts:
                await self._sleep(self._retry_delay(attempt))
                continue

            if execution_can_be_uncertain and (
                error.code in {"TX_CONFLICT", "SERVICE_UNAVAILABLE"}
                and response.status_code >= 500
            ):
                raise SigExecutionUncertainError(
                    status_code=response.status_code,
                    code=error.code,
                    safe_message=(
                        "SIG execution is unresolved after bounded same-payload retries; "
                        "reconciliation is required"
                    ),
                    details=error.details,
                )
            raise error

        raise AssertionError("unreachable retry loop")

    @staticmethod
    def _validate(
        model_type: type[ModelT],
        payload: object,
        route_template: str,
    ) -> ModelT:
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise SigMalformedResponseError(
                status_code=200,
                code=None,
                safe_message=(
                    f"SIG success payload failed schema validation for {route_template}"
                ),
            ) from exc

    @staticmethod
    def _decode_json(response: httpx.Response, route_template: str) -> object:
        try:
            return json.loads(response.content.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SigMalformedResponseError(
                status_code=response.status_code,
                code=None,
                safe_message=f"SIG returned invalid JSON for {route_template}",
            ) from exc

    @staticmethod
    def _error_code(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        return code if isinstance(code, str) else None

    @staticmethod
    def _retryable(error: SigApiError) -> bool:
        if isinstance(error, SigRateLimitError):
            return error.code == "RATE_LIMITED"
        if isinstance(error, SigTemporaryServiceError):
            return error.code in {"TX_CONFLICT", "SERVICE_UNAVAILABLE"}
        return False

    def _retry_delay(self, failed_attempt: int) -> float:
        policy = self._retry_policy
        exponential = policy.base_delay_seconds * (2.0 ** (failed_attempt - 1))
        base = min(policy.max_delay_seconds, exponential)
        if base == 0.0 or policy.jitter_ratio == 0.0:
            return base
        jitter = base * policy.jitter_ratio * random.random()
        return min(policy.max_delay_seconds, base + jitter)

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None
