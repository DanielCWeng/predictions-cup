"""Fail-closed LIVE execution interlocks."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.config import AppSettings


class LiveInterlockError(RuntimeError):
    pass


_PERMIT_MARKER = object()


@dataclass(frozen=True, slots=True, init=False)
class LiveExecutionPermit:
    """Capability issued only after every configured LIVE interlock passes."""

    tournament_id: str

    def __init__(self, tournament_id: str, marker: object) -> None:
        if marker is not _PERMIT_MARKER:
            raise LiveInterlockError("LIVE execution permit cannot be constructed directly")
        object.__setattr__(self, "tournament_id", tournament_id)


def assert_live_interlocks(
    settings: AppSettings,
    *,
    explicit_live_invocation: bool,
    account_trusted: bool,
) -> LiveExecutionPermit:
    """Require every independent LIVE gate and return the network-sink permit."""
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
    limits = (
        settings.risk_max_order_size,
        settings.risk_max_gross_exposure,
        settings.risk_max_per_market_exposure,
        settings.risk_max_open_order_exposure,
        settings.risk_max_concurrent_open_orders,
    )
    if any(value is None for value in limits):
        failures.append("risk_limits")

    if failures:
        raise LiveInterlockError(
            "LIVE execution interlocks failed: " + ",".join(failures)
        )

    assert settings.tournament_id is not None
    return LiveExecutionPermit(settings.tournament_id, _PERMIT_MARKER)
