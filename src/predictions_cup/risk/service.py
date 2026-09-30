"""I/O-shell orchestration for restart-safe RISK-002 capital state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from time import monotonic_ns

from predictions_cup.risk.capital import (
    AuthoritativeRiskSnapshot,
    CapitalRiskState,
    PnLReconstruction,
    RiskExposureSnapshot,
    RiskMark,
    RiskMarkProvider,
    RiskStateStore,
    RiskValuationPosition,
    reconcile_capital_state,
    revalue_capital_state,
    trip_global_halt,
)
from predictions_cup.risk.core import RiskContext, RiskLimits


def validate_restart_preflight(
    state: CapitalRiskState | None,
    *,
    session_id: str,
    profile_version: str,
    live_recovery: bool,
) -> None:
    """Validate durable identity before restart recovery.

    A durable capital halt intentionally does not block authoritative
    reconciliation or non-economic cancellation. The halt remains latched and
    central Risk continues to reject fresh exposure until an explicit reset.
    """
    del live_recovery
    if state is None:
        return
    if state.session_id != session_id:
        raise RuntimeError("durable risk state belongs to a different session")
    if state.limit_profile_version != profile_version:
        raise RuntimeError("durable risk profile version mismatch")


class CapitalControlService:
    """Small state coordinator; economic calculations remain pure in capital.py."""

    def __init__(
        self,
        *,
        store: RiskStateStore,
        max_account_age_ns: int,
        max_mark_age_ns: int,
        session_loss_limit: Decimal | None,
        drawdown_limit: Decimal | None,
    ) -> None:
        if max_account_age_ns <= 0 or max_mark_age_ns <= 0:
            raise ValueError("risk freshness limits must be positive")
        self._store = store
        self._max_account_age_ns = max_account_age_ns
        self._max_mark_age_ns = max_mark_age_ns
        self._session_loss_limit = session_loss_limit
        self._drawdown_limit = drawdown_limit

    def load_or_initialize(
        self,
        *,
        session_id: str,
        start_equity: Decimal,
        start_unrealised_pnl: Decimal,
        profile_version: str,
        observed_monotonic_ns: int,
        realised_pnl_cursor: str | None = None,
    ) -> CapitalRiskState:
        existing = self._store.load()
        if existing is not None:
            if existing.session_id != session_id:
                raise RuntimeError("durable risk state belongs to a different session")
            if existing.limit_profile_version != profile_version:
                raise RuntimeError("durable risk profile version mismatch")
            return replace(
                existing,
                account_trusted=False,
                marks_trusted=False,
                reconciliation_complete=False,
            )

        state = CapitalRiskState(
            session_id=session_id,
            session_start_equity=start_equity,
            session_start_unrealised_pnl=start_unrealised_pnl,
            realised_pnl=Decimal("0"),
            unrealised_pnl=start_unrealised_pnl,
            current_equity=start_equity,
            peak_session_equity=start_equity,
            drawdown=Decimal("0"),
            net_external_cash_flow=Decimal("0"),
            exposure=RiskExposureSnapshot(
                gross_exposure=0.0,
                net_directional_exposure=0.0,
                open_order_exposure=0.0,
                uncertain_order_exposure=0.0,
                trusted=False,
            ),
            account_trusted=False,
            account_observed_monotonic_ns=observed_monotonic_ns,
            marks_trusted=False,
            oldest_mark_observed_monotonic_ns=None,
            reconciliation_complete=False,
            global_halt=None,
            strategy_halts=(),
            limit_profile_version=profile_version,
            realised_pnl_cursor=realised_pnl_cursor,
        )
        self._store.save(state, event_type="SESSION_INITIALIZED", detail=session_id)
        return state


    def checkpoint(
        self,
        state: CapitalRiskState,
        *,
        event_type: str,
        detail: str,
    ) -> None:
        self._store.save(state, event_type=event_type, detail=detail)

    def reconcile(
        self,
        *,
        state: CapitalRiskState,
        authoritative: AuthoritativeRiskSnapshot,
        reconstruction: PnLReconstruction | None,
        exposure: RiskExposureSnapshot,
        marks: tuple[RiskMark, ...],
        now_monotonic_ns: int,
    ) -> CapitalRiskState:
        reconciled = reconcile_capital_state(
            previous=state,
            authoritative=authoritative,
            reconstruction=reconstruction,
            exposure=exposure,
            marks=marks,
            now_monotonic_ns=now_monotonic_ns,
            max_account_age_ns=self._max_account_age_ns,
            max_mark_age_ns=self._max_mark_age_ns,
            session_loss_limit=self._session_loss_limit,
            drawdown_limit=self._drawdown_limit,
        )
        detail = (
            "global_halt=" + reconciled.global_halt.reason
            if reconciled.global_halt is not None and reconciled.global_halt.active
            else "reconciled"
        )
        self._store.save(
            reconciled,
            event_type="AUTHORITATIVE_RECONCILIATION",
            detail=detail,
        )
        return reconciled


class RiskContextSource:
    """Publish reconciled state and revalue it from in-memory marks on demand.

    The normal call path performs no filesystem/network I/O. The optional
    halt_checkpoint is invoked only on the first hard loss/drawdown breach so
    the fail-closed latch is durable before any later decision can recover.
    """

    def __init__(
        self,
        base_context: RiskContext,
        *,
        state: CapitalRiskState | None = None,
        valuation_positions: tuple[RiskValuationPosition, ...] = (),
        mark_provider: RiskMarkProvider | None = None,
        halt_checkpoint: Callable[[CapitalRiskState, str], None] | None = None,
    ) -> None:
        self._base_context = base_context
        self._authoritative_state = state
        self._state = state
        self._valuation_positions = valuation_positions
        self._mark_provider = mark_provider
        self._halt_checkpoint = halt_checkpoint

    def __call__(self) -> RiskContext:
        return replace(
            self._base_context,
            capital_state=self.refresh(),
        )

    def refresh(self, *, now_ns: int | None = None) -> CapitalRiskState | None:
        authoritative = self._authoritative_state
        current = self._state
        if (
            authoritative is None
            or current is None
            or not authoritative.reconciliation_complete
            or self._mark_provider is None
            or not self._valuation_positions
            or self._base_context.max_mark_age_ns is None
        ):
            return current

        observed_ns = monotonic_ns() if now_ns is None else now_ns
        marks = self._mark_provider.marks_for(
            frozenset(item.exchange_id for item in self._valuation_positions),
            now_monotonic_ns=observed_ns,
        )
        revalued = revalue_capital_state(
            authoritative,
            positions=self._valuation_positions,
            marks=marks,
            now_monotonic_ns=observed_ns,
            max_mark_age_ns=self._base_context.max_mark_age_ns,
            peak_equity_floor=current.peak_session_equity,
        )
        revalued = replace(
            revalued,
            global_halt=current.global_halt,
            strategy_halts=current.strategy_halts,
            peak_session_equity=max(
                revalued.peak_session_equity,
                current.peak_session_equity,
            ),
        )
        revalued = replace(
            revalued,
            drawdown=revalued.peak_session_equity - revalued.current_equity,
        )
        current = self._latch_hard_loss_if_needed(
            revalued,
            now_ns=observed_ns,
        )
        self._state = current
        return current

    def _latch_hard_loss_if_needed(
        self,
        state: CapitalRiskState,
        *,
        now_ns: int,
    ) -> CapitalRiskState:
        if state.global_halt is not None and state.global_halt.active:
            return state
        for limits in self._limit_sets():
            reason: str | None = None
            if (
                limits.session_loss_limit is not None
                and float(state.session_pnl) <= -limits.session_loss_limit
            ):
                reason = "session_loss_limit"
            if (
                limits.drawdown_limit is not None
                and float(state.drawdown) >= limits.drawdown_limit
            ):
                reason = "peak_drawdown_limit"
            if reason is None:
                continue
            halted = trip_global_halt(
                state,
                reason=reason,
                now_monotonic_ns=now_ns,
            )
            if self._halt_checkpoint is not None:
                self._halt_checkpoint(halted, reason)
            return halted
        return state

    def _limit_sets(self) -> tuple[RiskLimits, ...]:
        profile = self._base_context.profile
        if profile is not None:
            if profile.hard_limits is None:
                return (profile.limits,)
            return (profile.limits, profile.hard_limits)
        if self._base_context.limits is None:
            return ()
        return (self._base_context.limits,)

    def publish(
        self,
        state: CapitalRiskState,
        *,
        valuation_positions: tuple[RiskValuationPosition, ...] | None = None,
    ) -> None:
        if (
            self._state is not None
            and self._state.session_id != state.session_id
        ):
            raise ValueError("cannot publish capital state from another session")
        self._authoritative_state = state
        self._state = state
        if valuation_positions is not None:
            self._valuation_positions = valuation_positions

    @property
    def state(self) -> CapitalRiskState | None:
        return self._state
