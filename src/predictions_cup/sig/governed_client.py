"""Governed SIG REST client used by live capture/runtime code."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import TracebackType

import httpx

from predictions_cup.config import AppSettings
from predictions_cup.sig.client import RetryPolicy, SigRestClient
from predictions_cup.sig.errors import (
    SigRateLimitError,
    SigTransportError,
    error_from_payload,
)
from predictions_cup.sig.rest_governor import (
    RestGovernorSnapshot,
    RestPriority,
    SigRestGovernor,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


class GovernedSigRestClient(SigRestClient):
    """SigRestClient whose every HTTP attempt consumes one shared governed slot."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        timeout_seconds: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
        governor: SigRestGovernor | None = None,
    ) -> None:
        super().__init__(
            settings,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy,
            transport=transport,
            sleep=sleep,
        )
        self._rest_governor = governor or SigRestGovernor(
            rate_per_second=settings.sig_rest_governor_rate_per_second,
            max_shared_cooldown_seconds=settings.sig_rest_shared_cooldown_max_seconds,
            sleep=sleep,
        )
        self._priority: ContextVar[RestPriority] = ContextVar(
            "sig_rest_priority", default=RestPriority.NORMAL
        )

    @asynccontextmanager
    async def priority(self, priority: RestPriority) -> AsyncIterator[None]:
        token = self._priority.set(priority)
        try:
            yield
        finally:
            self._priority.reset(token)

    def governor_snapshot(self) -> RestGovernorSnapshot:
        return self._rest_governor.snapshot()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def aclose(self) -> None:
        await self._rest_governor.aclose()
        await super().aclose()

    async def _get_json(
        self,
        path: str,
        *,
        route_template: str,
        params: dict[str, str | int] | None = None,
    ) -> object:
        policy = self._retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            await self._rest_governor.acquire(self._priority.get())
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
            self._rest_governor.record_response(
                response.status_code,
                retry_after_seconds=_retry_after_seconds(response),
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
                if not isinstance(error, SigRateLimitError):
                    await self._sleep(self._retry_delay(attempt))
                continue
            raise error

        raise AssertionError("unreachable retry loop")

    async def _post_json(self, path: str, *, route_template: str) -> object:
        await self._rest_governor.acquire(self._priority.get())
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
        self._rest_governor.record_response(
            response.status_code,
            retry_after_seconds=_retry_after_seconds(response),
        )
        payload = self._decode_json(response, route_template=route_template)
        if response.is_success:
            return payload
        raise error_from_payload(status_code=response.status_code, payload=payload)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return max(0.0, parsed)
