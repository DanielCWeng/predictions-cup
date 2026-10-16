"""Red Team: frozen #263 recovery-entrypoint + #264 scope-API adaptation.

Source is frozen #256. Uses local SQLite and httpx.MockTransport ONLY.
Never authenticates issuer, places a live order, or alters implementation.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest

from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import LifecycleState
from predictions_cup.execution.recovery import reserve_unresolved_placements
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.errors import SigExecutionUncertainError
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import (
    SigTradingClient,
    enforce_startup_replay_wire_authorization,
)
from test_build009_recovery import (
    _79Governor,
    _recovery_permit,
    _restart_replay_case,
)


def _fake_client(responder: httpx.MockTransport, now: datetime) -> SigTradingClient:
    client = object.__new__(SigTradingClient)
    client._client = httpx.AsyncClient(transport=responder, base_url="https://sig.invalid/")
    client._governor = cast(SigRestGovernor, _79Governor())
    client._retry_policy = RetryPolicy(
        max_attempts=1, base_delay_seconds=0, max_delay_seconds=0, jitter_ratio=0
    )
    client._sleep = asyncio.sleep
    client._timeout_seconds = 0.01
    client._request_in_flight_wait_seconds = 0.0
    client._wall_clock = lambda: now
    return client


@pytest.mark.parametrize("outcome", ("429", "503", "lost_ack"))
def test_direct_unscoped_recovery_never_sends_economic_placement(
    tmp_path: Path, outcome: str,
) -> None:
    """P0: durable same-key identity is not authority for an unscoped replay."""
    journal, reservations, _dummy, _rest, _old_sink, envelope = _restart_replay_case(
        tmp_path, LifecycleState.UNCERTAIN
    )
    reserve_unresolved_placements(
        journal, (envelope,), reservations=reservations,
        market_by_exchange={"36": "m1"},
    )
    now = datetime.now(UTC)
    attempts: list[bytes] = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempts.append(request.content)
        if outcome == "lost_ack":
            raise httpx.ReadTimeout("lost ACK after possible execution", request=request)
        status = int(outcome)
        code = "RATE_LIMITED" if status == 429 else "SERVICE_UNAVAILABLE"
        return httpx.Response(
            status, json={"error": {"code": code, "message": "fake only"}},
            headers={"Retry-After": "0"},
        )

    client = _fake_client(httpx.MockTransport(respond), now)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_recovery_permit(),
        reservations=reservations, wall_clock=lambda: now,
    )
    try:
        with pytest.raises(SigExecutionUncertainError):
            asyncio.run(sink.dispatch_recovery(envelope))  # NO recover_startup/grant
        assert journal.lifecycle_state(envelope.logical_operation_id) is (
            LifecycleState.UNCERTAIN
        )
        assert reservations.contains_operation(
            envelope.logical_operation_id, envelope.intent_ids
        )
        # Source #256 fails this: a direct sink entrypoint bypasses the grant
        # scope and makes one possible economic POST. This is a mocked wire.
        assert not attempts, "Unscoped direct recovery emitted an unauthorised placement POST"
    finally:
        asyncio.run(client.aclose())
        journal.close()


def test_child_task_must_not_inherit_other_operation_grant_after_scope_exit() -> None:
    """Task spawned within one recovery's context cannot reuse it for another ID."""
    now = datetime.now(UTC)
    attempts: list[dict[str, object]] = []
    grant_calls: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempts.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    client = _fake_client(httpx.MockTransport(respond), now)

    async def send_other_operation() -> None:
        await asyncio.sleep(0)  # the parent's authorisation scope has ended
        await client._request_json(
            "POST", "orders", route_template="/orders",
            resolved_payload=None,
            resolved_content=b'{"idempotencyKey":"DIFFERENT-OPERATION"}',
            accepted_statuses=frozenset({200}),
            execution_can_be_uncertain=True,
        )

    async def scenario() -> None:
        try:
            def approved_for_original_operation() -> bool:
                grant_calls.append(1)
                return True

            # asyncio.create_task snapshots the task context. Passing a
            # blanket boolean callback is a test of identity/source binding.
            with enforce_startup_replay_wire_authorization(
                approved_for_original_operation,
                logical_operation_id="ORIGINAL-OPERATION",
                tournament_id="t1",
                idempotency_key="ORIGINAL-KEY",
                payload_sha256="a" * 64,
            ):
                other = asyncio.create_task(send_other_operation())
            with pytest.raises(SigExecutionUncertainError):
                await other
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert not grant_calls  # mismatched task denied before consulting parent's grant
    assert not attempts, "Child task reused another operation's replay grant"


def test_replay_scope_restores_context_after_success_exception_and_cancel() -> None:
    """Positive control: new task-bound scope is cleared at all parent exits."""
    from predictions_cup.sig.trading_client import _startup_replay_wire_authorizer

    def scoped() -> AbstractContextManager[None]:
        return enforce_startup_replay_wire_authorization(
            lambda: True,
            logical_operation_id="original",
            tournament_id="t1",
            idempotency_key="original-key",
            payload_sha256="a" * 64,
        )

    async def scenario() -> None:
        assert _startup_replay_wire_authorizer.get() is None
        with scoped():
            assert _startup_replay_wire_authorizer.get() is not None
        assert _startup_replay_wire_authorizer.get() is None

        with (pytest.raises(RuntimeError, match="fixture"), scoped()):
            raise RuntimeError("fixture")
        assert _startup_replay_wire_authorizer.get() is None

        with pytest.raises(asyncio.CancelledError), scoped():
            raise asyncio.CancelledError
        assert _startup_replay_wire_authorizer.get() is None

    asyncio.run(scenario())
    assert _startup_replay_wire_authorizer.get() is None
