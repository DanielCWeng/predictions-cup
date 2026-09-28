"""Fail-closed LIVE execution interlocks."""

from __future__ import annotations

from predictions_cup.config import AppSettings


class LiveInterlockError(RuntimeError):
    pass


def assert_live_interlocks(
    settings: AppSettings,
    *,
    explicit_live_invocation: bool,
    account_trusted: bool,
) -> None:
    """Require every independent LIVE gate before a network-capable sink is used."""
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
