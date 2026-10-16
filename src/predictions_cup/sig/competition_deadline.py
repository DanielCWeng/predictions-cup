"""Fail-closed SIG Predictions Cup 2026 order-submission cutoff.

The official competition closes on 4 Nov 2026 at noon EST (17:00 UTC).
All order *placements*, including same-key retries and recovery re-dispatch,
must stop before that time. Cancels and read-only reconciliation are exempt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from predictions_cup.sig.errors import SigApiError, SigExecutionUncertainError

COMPETITION_END_UTC = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)
ORDER_SUBMISSION_BUFFER = timedelta(seconds=5)
LAST_ORDER_SUBMISSION_UTC = COMPETITION_END_UTC - ORDER_SUBMISSION_BUFFER


def assert_competition_placement_open(
    now: datetime,
    *,
    attempted_network: bool = False,
) -> None:
    """Reject placement before any network write once the safety buffer starts.

    An attempted placement might have reached SIG even if no response was read;
    a blocked retry is therefore UNCERTAIN, not a conclusively rejected order.
    This guard must be sampled again after REST-governor waits and before *each*
    HTTP placement attempt. A naive check only at strategy decision time is unsafe.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        # Untrusted clock BEFORE any possible wire attempt is conclusively
        # unsent; after a prior attempt it cannot negate earlier economic risk.
        # The latter must be journalled UNCERTAIN and keep its reservation.
        error_type = SigExecutionUncertainError if attempted_network else SigApiError
        raise error_type(
            status_code=None,
            code="COMPETITION_CLOCK_UNTRUSTED",
            safe_message=(
                "Timezone-aware clock required; preserve original placement "
                "identity and reconcile if an earlier HTTP attempt was made"
            ),
        )
    if now.astimezone(UTC) < LAST_ORDER_SUBMISSION_UTC:
        return
    if attempted_network:
        raise SigExecutionUncertainError(
            status_code=None,
            code="COMPETITION_CUTOFF_AFTER_DISPATCH",
            safe_message=(
                "SIG placement retry blocked by competition cutoff; earlier "
                "attempt may have executed; reconcile existing idempotency key"
            ),
        )
    raise SigApiError(
        status_code=None,
        code="COMPETITION_ORDER_CLOSED",
        safe_message="SIG 2026 competition order-placement safety cutoff has passed",
    )
