"""Fail-closed LIVE execution interlocks."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.config import AppSettings


class LiveInterlockError(RuntimeError):
    pass


_PERMIT_MARKER = object()


@dataclass(frozen=True, slots=True, init=False)
class LiveExecutionPermit:
    """Capability issued only after the relevant LIVE interlocks pass.

    Recovery-only permits can reconcile/cancel durable existing risk and can
    redispatch only through SigLiveSink.dispatch_recovery(). They cannot admit a
    fresh economic placement.
    """

    tournament_id: str
    fresh_admission_allowed: bool

    def __init__(
        self,
        tournament_id: str,
        marker: object,
        *,
        fresh_admission_allowed: bool,
    ) -> None:
        if marker is not _PERMIT_MARKER:
            raise LiveInterlockError("LIVE execution permit cannot be constructed directly")
        object.__setattr__(self, "tournament_id", tournament_id)
        object.__setattr__(self, "fresh_admission_allowed", fresh_admission_allowed)


def _live_failures(
    settings: AppSettings,
    *,
    explicit_live_invocation: bool,
    account_trusted: bool,
) -> list[str]:
    failures: list[str] = []
    if not explicit_live_invocation:
        failures.append("explicit_live_invocation")
    if settings.execution_mode != "LIVE":
        failures.append("execution_mode")
    if not settings.trading_enabled:
        failures.append("trading_enabled")
    if settings.sig_trade_credential is None:
        failures.append("trade_credential")
    if settings.tournament_id is None or settings.tournament_slug is None:
        failures.append("tournament_context")
    if settings.global_kill_switch:
        failures.append("global_kill_switch")
    if not account_trusted:
        failures.append("account_state")
    if not settings.risk_capital_control_enabled:
        failures.append("risk_capital_control")
    if settings.risk_session_loss_limit is None:
        failures.append("session_loss_limit")
    if settings.risk_drawdown_limit is None:
        failures.append("drawdown_limit")
    limits = (
        settings.risk_max_order_size,
        settings.risk_max_gross_exposure,
        settings.risk_max_per_market_exposure,
        settings.risk_max_open_order_exposure,
        settings.risk_max_concurrent_open_orders,
    )
    if any(value is None for value in limits):
        failures.append("risk_limits")
    return failures


def assert_live_recovery_interlocks(
    settings: AppSettings,
    *,
    explicit_live_invocation: bool,
    account_trusted: bool,
) -> LiveExecutionPermit:
    """Issue a recovery-only capability after configuration/account gates pass.

    This intentionally does not require reconciled RISK-002 capital state:
    startup must be able to reconcile/cancel existing PENDING, OPEN and
    UNCERTAIN risk while a durable capital halt remains latched.
    """
    failures = _live_failures(
        settings,
        explicit_live_invocation=explicit_live_invocation,
        account_trusted=account_trusted,
    )
    if failures:
        raise LiveInterlockError(
            "LIVE recovery interlocks failed: " + ",".join(failures)
        )

    assert settings.tournament_id is not None
    return LiveExecutionPermit(
        settings.tournament_id,
        _PERMIT_MARKER,
        fresh_admission_allowed=False,
    )


def assert_live_interlocks(
    settings: AppSettings,
    *,
    explicit_live_invocation: bool,
    account_trusted: bool,
    capital_state_ready: bool,
) -> LiveExecutionPermit:
    """Require every independent fresh-LIVE gate and return the network permit."""
    failures = _live_failures(
        settings,
        explicit_live_invocation=explicit_live_invocation,
        account_trusted=account_trusted,
    )
    if not capital_state_ready:
        failures.append("capital_control_state")

    if failures:
        raise LiveInterlockError(
            "LIVE execution interlocks failed: " + ",".join(failures)
        )

    assert settings.tournament_id is not None
    return LiveExecutionPermit(
        settings.tournament_id,
        _PERMIT_MARKER,
        fresh_admission_allowed=True,
    )
