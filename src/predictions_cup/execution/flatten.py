"""Tournament-scoped emergency flatten with authoritative verification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import monotonic_ns
from uuid import uuid4

from predictions_cup.config import AppSettings
from predictions_cup.execution.interlocks import assert_live_recovery_interlocks
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import (
    ExecutionEnvelope,
    ExecutionMode,
    LifecycleState,
    OperationKind,
)
from predictions_cup.execution.recovery import RecoveryRest
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.sig.account_reconciliation import reconcile_account
from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient


@dataclass(frozen=True, slots=True)
class FlattenResult:
    status: str
    verified_flat: bool
    attempts: int
    orders_before: int
    orders_after: int
    cancel_state: str
    last_error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def flatten_with_sink(
    *,
    rest: RecoveryRest,
    live_sink: SigLiveSink,
    tournament_id: str,
    tournament_slug: str,
    max_attempts: int = 3,
    logical_operation_id: str | None = None,
) -> FlattenResult:
    """Cancel all tournament orders and prove zero authoritative open orders."""
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    before = await reconcile_account(
        rest,
        tournament_id=tournament_id,
        tournament_slug=tournament_slug,
    )
    if not before.open_orders:
        return FlattenResult(
            status="VERIFIED_FLAT",
            verified_flat=True,
            attempts=0,
            orders_before=0,
            orders_after=0,
            cancel_state="NOT_NEEDED",
        )

    envelope = ExecutionEnvelope.cancellation(
        logical_operation_id=(
            logical_operation_id
            or f"operator-flatten:{tournament_id}:{uuid4().hex}"
        ),
        operation_kind=OperationKind.CANCEL_ALL,
        sink_mode=ExecutionMode.LIVE,
        created_monotonic_ns=monotonic_ns(),
        tournament_id=tournament_id,
    )
    last_state = LifecycleState.CANCEL_PENDING
    last_error: str | None = None
    after = before

    for attempt in range(1, max_attempts + 1):
        try:
            event = await live_sink.cancel(envelope)
            last_state = event.state
            last_error = None
        except SigExecutionUncertainError as exc:
            last_state = LifecycleState.UNCERTAIN
            last_error = f"{type(exc).__name__}:{exc}"
        except SigApiError as exc:
            return FlattenResult(
                status="FAILED",
                verified_flat=False,
                attempts=attempt,
                orders_before=len(before.open_orders),
                orders_after=len(after.open_orders),
                cancel_state=last_state.value,
                last_error=f"{type(exc).__name__}:{exc}",
            )

        after = await reconcile_account(
            rest,
            tournament_id=tournament_id,
            tournament_slug=tournament_slug,
        )
        if not after.open_orders:
            return FlattenResult(
                status="VERIFIED_FLAT",
                verified_flat=True,
                attempts=attempt,
                orders_before=len(before.open_orders),
                orders_after=0,
                cancel_state=last_state.value,
                last_error=last_error,
            )

    return FlattenResult(
        status="UNVERIFIED",
        verified_flat=False,
        attempts=max_attempts,
        orders_before=len(before.open_orders),
        orders_after=len(after.open_orders),
        cancel_state=last_state.value,
        last_error=last_error,
    )


async def flatten_tournament(
    settings: AppSettings,
    *,
    max_attempts: int = 3,
) -> FlattenResult:
    """Independent operator/startup entry point with its own bounded clients."""
    tournament_id = settings.tournament_id
    tournament_slug = settings.tournament_slug
    if tournament_id is None or tournament_slug is None:
        raise ValueError("flatten requires explicit tournament_id and tournament_slug")

    governor = SigRestGovernor(
        rate_per_second=settings.sig_rest_governor_rate_per_second,
        max_shared_cooldown_seconds=settings.sig_rest_shared_cooldown_max_seconds,
    )
    rest = GovernedSigRestClient(settings, governor=governor)
    trading = SigTradingClient(settings, governor=governor)
    journal = ExecutionJournal(settings.execution_journal_path)
    try:
        permit = assert_live_recovery_interlocks(
            settings,
            explicit_live_invocation=True,
            account_trusted=True,
        )
        sink = SigLiveSink(
            client=trading,
            journal=journal,
            permit=permit,
            reservations=ExecutionReservationBook(),
        )
        return await flatten_with_sink(
            rest=rest,
            live_sink=sink,
            tournament_id=tournament_id,
            tournament_slug=tournament_slug,
            max_attempts=max_attempts,
        )
    finally:
        journal.close()
        await trading.aclose()
        await rest.aclose()
