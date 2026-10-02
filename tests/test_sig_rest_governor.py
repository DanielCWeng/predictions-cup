from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.rest_governor import (
    RestGovernorQueueFullError,
    RestPriority,
    SigRestGovernor,
)


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


def _settings() -> AppSettings:
    return AppSettings(
        sig_read_credential=SecretStr("read-secret"),
        sig_rest_governor_rate_per_second=3.0,
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


def _error_payload(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "safe", "details": None}}


def test_global_governor_paces_concurrent_callers_deterministically() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=2.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )

        await asyncio.gather(
            governor.acquire(RestPriority.NORMAL),
            governor.acquire(RestPriority.NORMAL),
            governor.acquire(RestPriority.NORMAL),
        )

        snapshot = governor.snapshot()
        assert snapshot.requests_total == 3
        assert clock.now == 1.0
        assert clock.sleeps == [0.5, 0.5]
        await governor.aclose()

    asyncio.run(scenario())


def test_high_priority_can_use_reserved_token_immediately_after_read() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=2.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )
        await governor.acquire(RestPriority.NORMAL)
        before = clock.now
        await governor.acquire(RestPriority.HIGH)

        assert clock.now == before
        assert clock.sleeps == []
        assert governor.snapshot().requests_total == 2
        await governor.aclose()

    asyncio.run(scenario())


def test_high_priority_overtakes_background_waiting_for_capacity() -> None:
    async def scenario() -> None:
        now = 0.0
        first_wait_started = asyncio.Event()
        release_first_wait = asyncio.Event()
        sleep_calls = 0
        completion_order: list[str] = []

        def monotonic() -> float:
            return now

        async def sleep(delay: float) -> None:
            nonlocal now, sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                first_wait_started.set()
                await release_first_wait.wait()
            now += delay
            await asyncio.sleep(0)

        governor = SigRestGovernor(
            rate_per_second=1.0,
            sleep=sleep,
            monotonic=monotonic,
            random_fn=lambda: 0.0,
        )
        await governor.acquire(RestPriority.NORMAL)

        async def acquire(name: str, priority: RestPriority) -> None:
            await governor.acquire(priority)
            completion_order.append(name)

        background = asyncio.create_task(
            acquire("background", RestPriority.BACKGROUND)
        )
        await first_wait_started.wait()
        high = asyncio.create_task(acquire("high", RestPriority.HIGH))
        release_first_wait.set()
        await asyncio.gather(background, high)

        assert completion_order == ["high", "background"]
        await governor.aclose()

    asyncio.run(scenario())


def test_429_applies_shared_cooldown_to_next_caller() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=10.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )

        await governor.acquire(RestPriority.NORMAL)
        governor.record_response(429, retry_after_seconds=2.0)
        before = clock.now
        await governor.acquire(RestPriority.HIGH)

        snapshot = governor.snapshot()
        assert clock.now - before == 2.0
        assert snapshot.rate_limit_count == 1
        assert snapshot.shared_cooldown_count == 1
        assert snapshot.requests_total == 2
        await governor.aclose()

    asyncio.run(scenario())


def test_scoped_request_policy_shortens_get_timeout_and_retry(monkeypatch: Any) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=2.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )
        attempts = 0
        timeouts: list[float | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            del request
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("synthetic timeout")
            return httpx.Response(200, json=_account_payload())

        async with GovernedSigRestClient(
            AppSettings(
                sig_read_credential=SecretStr("read-secret"),
                sig_rest_governor_rate_per_second=2.0,
            ),
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            governor=governor,
        ) as client:
            original_get = client._client.get

            async def recording_get(url: str, **kwargs: Any) -> httpx.Response:
                timeout = kwargs.get("timeout")
                timeouts.append(timeout)
                return await original_get(url, **kwargs)

            monkeypatch.setattr(client._client, "get", recording_get)
            async with client.request_policy(
                timeout_seconds=4.0,
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0.01,
                    max_delay_seconds=0.02,
                    jitter_ratio=0,
                ),
            ):
                await client.get_account()

            # The context override is task-local and does not alter later reads.
            await client.get_account()
            snapshot = client.governor_snapshot()

        assert attempts == 3
        assert timeouts == [4.0, 4.0, None]
        assert 0.01 in clock.sleeps
        assert clock.now >= 1.0
        assert snapshot.rate_per_second == 2.0
        assert snapshot.requests_total == 3

    asyncio.run(scenario())


def test_new_429_extends_cooldown_for_already_waiting_caller() -> None:
    async def scenario() -> None:
        now = 0.0
        pacing_wait_started = asyncio.Event()
        release_pacing_wait = asyncio.Event()
        sleep_calls: list[float] = []

        def monotonic() -> float:
            return now

        async def sleep(delay: float) -> None:
            nonlocal now
            sleep_calls.append(delay)
            if len(sleep_calls) == 1:
                pacing_wait_started.set()
                await release_pacing_wait.wait()
            now += delay
            await asyncio.sleep(0)

        governor = SigRestGovernor(
            rate_per_second=2.0,
            sleep=sleep,
            monotonic=monotonic,
            random_fn=lambda: 0.0,
        )
        await governor.acquire(RestPriority.NORMAL)
        waiting = asyncio.create_task(governor.acquire(RestPriority.BACKGROUND))
        await pacing_wait_started.wait()

        governor.record_response(429, retry_after_seconds=2.0)
        release_pacing_wait.set()
        await waiting

        assert now == 2.0
        assert sleep_calls == [0.5, 1.5]
        await governor.aclose()

    asyncio.run(scenario())


def test_governed_client_429_retry_consumes_shared_cooldown() -> None:
    attempts = 0

    async def scenario() -> None:
        nonlocal attempts
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=10.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            del request
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    json=_error_payload("RATE_LIMITED"),
                    headers={"Retry-After": "1.5"},
                )
            return httpx.Response(200, json=_account_payload())

        async with GovernedSigRestClient(
            _settings(),
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=2, jitter_ratio=0),
            sleep=clock.sleep,
            governor=governor,
        ) as client:
            account = await client.get_account()
            snapshot = client.governor_snapshot()

        assert account.id == "profile-1"
        assert attempts == 2
        assert clock.now >= 1.5
        assert snapshot.rate_limit_count == 1
        assert snapshot.shared_cooldown_count == 1

    asyncio.run(scenario())


def test_governor_shutdown_cancels_waiters_without_deadlock() -> None:
    async def scenario() -> None:
        now = 0.0
        sleep_started = asyncio.Event()
        never_release = asyncio.Event()

        def monotonic() -> float:
            return now

        async def blocking_sleep(delay: float) -> None:
            del delay
            sleep_started.set()
            await never_release.wait()

        governor = SigRestGovernor(
            rate_per_second=1.0,
            sleep=blocking_sleep,
            monotonic=monotonic,
            random_fn=lambda: 0.0,
        )
        await governor.acquire(RestPriority.NORMAL)
        pending = asyncio.create_task(governor.acquire(RestPriority.BACKGROUND))
        await sleep_started.wait()

        await governor.aclose()
        outcome = await asyncio.gather(pending, return_exceptions=True)

        assert isinstance(outcome[0], asyncio.CancelledError)

    asyncio.run(scenario())


def test_governor_rejects_requests_when_bounded_queue_is_full() -> None:
    async def scenario() -> None:
        governor = SigRestGovernor(rate_per_second=1.0, queue_max=1)
        queued = asyncio.get_running_loop().create_future()
        governor._queue.put_nowait((int(RestPriority.NORMAL), 1, queued))

        with pytest.raises(RestGovernorQueueFullError, match="capacity=1"):
            await governor.acquire(RestPriority.NORMAL)

        assert governor.snapshot().pending_normal_reads == 0
        await governor.aclose()
        assert queued.cancelled()

    asyncio.run(scenario())


def test_parse_retry_after_supports_seconds_http_date_and_bounds() -> None:
    from datetime import UTC, datetime

    from predictions_cup.sig.rest_governor import MAX_RETRY_AFTER_SECONDS, parse_retry_after

    now = datetime(2026, 10, 1, 21, 0, 0, tzinfo=UTC)
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after("Thu, 01 Oct 2026 21:00:07 GMT", now=now) == 7.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("99999") == MAX_RETRY_AFTER_SECONDS
    assert parse_retry_after("nan") is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_503_retry_after_sets_process_wide_cooldown() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=10.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )
        await governor.acquire(RestPriority.HIGH)
        governor.record_response(503, retry_after_seconds=4.0)
        assert governor.snapshot().cooldown_remaining_seconds == 4.0
        await governor.acquire(RestPriority.HIGH)
        assert clock.now >= 4.0
        # 503 without Retry-After does not invent a shared cooldown.
        governor.record_response(503)
        assert governor.snapshot().cooldown_remaining_seconds == 0.0
        await governor.aclose()

    asyncio.run(scenario())


def test_governed_get_honours_503_retry_after_before_retrying() -> None:
    responses = [
        httpx.Response(
            503, json=_error_payload("SERVICE_UNAVAILABLE"), headers={"Retry-After": "3"}
        ),
        httpx.Response(200, json=_account_payload()),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return responses.pop(0)

    async def scenario() -> None:
        clock = FakeClock()
        governor = SigRestGovernor(
            rate_per_second=10.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
        )
        async with GovernedSigRestClient(
            _settings(),
            governor=governor,
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.1, jitter_ratio=0),
            sleep=clock.sleep,
        ) as client:
            await client.get_account()
        assert clock.now >= 3.0

    asyncio.run(scenario())


def test_host_shared_budget_is_account_wide_and_reserves_high_priority(tmp_path: Path) -> None:
    from predictions_cup.sig.shared_budget import HostSharedRestBudget

    now = [1000.0]
    path = tmp_path / "sig_account_budget.json"
    capture = HostSharedRestBudget(path, rate_per_second=2.0, wall=lambda: now[0])
    make = HostSharedRestBudget(path, rate_per_second=2.0, wall=lambda: now[0])

    assert capture.try_take(RestPriority.HIGH) == pytest.approx(0.5)  # starts empty
    now[0] += 1.0  # 1 s refills to the burst of 2
    assert capture.try_take(RestPriority.NORMAL) == 0.0
    # One token left: NORMAL must leave it for execution in ANY process.
    assert make.try_take(RestPriority.NORMAL) > 0.0
    assert make.try_take(RestPriority.HIGH) == 0.0
    assert capture.try_take(RestPriority.HIGH) == pytest.approx(0.5)

    now[0] += 10.0
    make.extend_cooldown(5.0)
    assert capture.try_take(RestPriority.HIGH) == pytest.approx(5.0)
    now[0] += 5.0
    assert capture.try_take(RestPriority.HIGH) == 0.0

    path.write_text("garbage")
    assert capture.try_take(RestPriority.HIGH) > 0.0  # unreadable state fails slow


def test_governor_waits_on_shared_budget(tmp_path: Path) -> None:
    from predictions_cup.sig.shared_budget import HostSharedRestBudget

    async def scenario() -> None:
        clock = FakeClock(now=100.0)
        shared = HostSharedRestBudget(
            tmp_path / "b.json", rate_per_second=1.0, wall=clock.monotonic
        )
        governor = SigRestGovernor(
            rate_per_second=100.0,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            random_fn=lambda: 0.0,
            shared_budget=shared,
        )
        await governor.acquire(RestPriority.HIGH)
        await governor.acquire(RestPriority.HIGH)
        # Local rate allows 100/s; the shared account budget limits to 1/s.
        assert clock.now >= 101.9
        await governor.aclose()

    asyncio.run(scenario())
