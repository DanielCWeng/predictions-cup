"""Independent combined #251 fake HTTP startup-replay admission tests.

No authenticated venue, LIVE traffic, external risk changes or production edits.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest

from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import LifecycleState
from predictions_cup.execution.recovery import (
    PlacementReplayAuthorization,
    RecoveryRest,
    recover_startup,
)
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient
from test_build009_recovery import (
    _79Governor,
    _fresh_replay_grant,
    _recovery_permit,
    _restart_replay_case,
)


@pytest.mark.parametrize(
    "state", (LifecycleState.PENDING, LifecycleState.UNCERTAIN, LifecycleState.RECONCILING)
)
def test_independent_default_deny_across_three_states_and_repeat_restart(
    tmp_path: Path, state: LifecycleState,
) -> None:
    journal, reservations, trading, rest, sink, envelope = _restart_replay_case(
        tmp_path, state
    )
    try:
        for _ in range(3):
            result = asyncio.run(
                recover_startup(
                    journal=journal,
                    rest=cast(RecoveryRest, rest),
                    live_sink=sink,
                    tournament_id="t1",
                    tournament_slug="cup",
                    reservations=reservations,
                    market_by_exchange={"36": "m1"},
                    exchange_ids_by_market={"m1": ("36",)},
                )
            )
            assert not result.safe_to_resume_live
            assert result.unresolved_operation_ids == (envelope.logical_operation_id,)
            assert journal.lifecycle_state(envelope.logical_operation_id) is state
            assert reservations.contains_operation(
                envelope.logical_operation_id, envelope.intent_ids
            )
            assert trading.payloads == []
    finally:
        journal.close()


@pytest.mark.parametrize("first_response", ("429", "503", "lost_ack"))
def test_revoked_grant_must_stop_second_in_client_retry_wire(
    tmp_path: Path, first_response: str,
) -> None:
    """P0: grant revoked after first possible wire must forbid an in-client retry.

    _retry_recovery_sig_operation checks callback only around dispatch_recovery,
    but SigTradingClient retries internally before returning to that wrapper.
    This is NOT a claim that a caller-chosen issuer string is authenticated.
    """
    journal, reservations, _trading, rest, _old_sink, envelope = _restart_replay_case(
        tmp_path, LifecycleState.UNCERTAIN
    )
    now = datetime.now(UTC)
    authorised = [True]
    calls: list[bytes] = []
    grant_checks: list[bool] = []

    def grant(item: object) -> PlacementReplayAuthorization | None:
        grant_checks.append(authorised[0])
        if not authorised[0]:
            return None
        # The same exact journal identity, issuer field and 15-second window.
        return _fresh_replay_grant(envelope, now)

    def respond(req: httpx.Request) -> httpx.Response:
        calls.append(req.content)
        authorised[0] = False  # revoked after FIRST possible wire
        if first_response == "lost_ack":
            raise httpx.ReadTimeout("fixture first attempt may have executed", request=req)
        if first_response == "503":
            return httpx.Response(
                503,
                json={"error": {"code": "SERVICE_UNAVAILABLE", "message": "retry"}},
            )
        return httpx.Response(
            429, json={"error": {"code": "RATE_LIMITED", "message": "retry"}},
            headers={"Retry-After": "0"},
        )

    client = object.__new__(SigTradingClient)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://sig.invalid/"
    )
    client._governor = cast(SigRestGovernor, _79Governor())
    client._retry_policy = RetryPolicy(
        max_attempts=2, base_delay_seconds=0, max_delay_seconds=0, jitter_ratio=0
    )
    client._sleep = asyncio.sleep
    client._timeout_seconds = 0.01
    client._request_in_flight_wait_seconds = 0.0
    # #248 introduces a per-wire cutoff clock absent on standalone #247.
    # This is a reviewer-ONLY compatibility fixture; assertions stay identical.
    client._wall_clock = lambda: now
    sink = SigLiveSink(
        client=client, journal=journal, permit=_recovery_permit(),
        reservations=reservations,
    )
    try:
        result = asyncio.run(
            recover_startup(
                journal=journal, rest=cast(RecoveryRest, rest), live_sink=sink,
                tournament_id="t1", tournament_slug="cup",
                placement_replay_allowed=grant,
                wall_clock=lambda: now,
                reservations=reservations,
                market_by_exchange={"36": "m1"},
                exchange_ids_by_market={"m1": ("36",)},
            )
        )
        assert all(payload == envelope.payload_json.encode() for payload in calls)
        # FAILS on original #247: the client sends the second HTTP attempt
        # while grant was already revoked after the first response/lost ACK.
        assert len(calls) == 1, "A revoked grant cannot admit a second HTTP placement wire"
        assert journal.lifecycle_state(envelope.logical_operation_id) is (
            LifecycleState.UNCERTAIN
        )
        assert reservations.contains_operation(
            envelope.logical_operation_id, envelope.intent_ids
        )
        assert not result.safe_to_resume_live
        assert any(grant_checks)
    finally:
        asyncio.run(client.aclose())
        journal.close()


@pytest.mark.xfail(
    strict=True,
    reason="Documented LIVE block: authority_id is only untrusted caller-controlled text",
)
def test_untrusted_callback_cannot_forge_a_valid_issuer_string(tmp_path: Path) -> None:
    """Distinct trust-boundary gap; do not confuse shape validation with auth."""
    from predictions_cup.execution.recovery import _placement_replay_authorised

    journal, _reservations, _trading, _rest, _sink, envelope = _restart_replay_case(
        tmp_path, LifecycleState.UNCERTAIN
    )
    now = datetime.now(UTC)
    grant = _fresh_replay_grant(envelope, now)
    fake_grant = PlacementReplayAuthorization(
        logical_operation_id=grant.logical_operation_id,
        tournament_id=grant.tournament_id,
        idempotency_key=grant.idempotency_key,
        payload_sha256=grant.payload_sha256,
        issued_at_utc=now - timedelta(seconds=1),
        expires_at_utc=now + timedelta(seconds=2),
        authority_id="spoofed-trusted-issuer",
    )
    try:
        assert not _placement_replay_authorised(
            envelope, permission=lambda _: fake_grant, wall_clock=lambda: now,
            journal=journal,
        )
    finally:
        journal.close()
