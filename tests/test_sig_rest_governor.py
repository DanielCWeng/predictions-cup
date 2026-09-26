from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from predictions_cup.config import AppSettings
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.rest_governor import RestPriority, SigRestGovernor


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
