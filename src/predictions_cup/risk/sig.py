"""SIG-to-RISK-002 normalization using the accepted api-1.json semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from predictions_cup.risk.capital import (
    AuthoritativeRiskPosition,
    AuthoritativeRiskSnapshot,
    CapitalRiskState,
    CostBasisPosition,
    ExposureAttribution,
    MarketExposureGroup,
    PnLReconstruction,
    RiskExposureSnapshot,
    RiskMark,
    RiskValuationPosition,
    build_exposure_snapshot,
)
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.realtime_state import SigRealtimeStateEngine
from predictions_cup.sig.trading_dto import (
    PortfolioPnlDto,
    TournamentTransactionPageDto,
)

SIG_API_1_SHA256 = "8825112d9413f3b7361773800400704078968c412c47526b251e8b57e982448a"




class SigRealtimeRiskMarkProvider:
    """Read-only adapter over the accepted SIG realtime/bulk-price state."""

    provider_id = "sig-realtime-risk-marks"
    version = SIG_API_1_SHA256

    def __init__(self, state: SigRealtimeStateEngine) -> None:
        self._state = state

    def marks_for(
        self,
        exchange_ids: frozenset[str],
        *,
        now_monotonic_ns: int,
    ) -> tuple[RiskMark, ...]:
        wall_now = datetime.now(UTC)
        marks: list[RiskMark] = []
        for exchange_id in sorted(exchange_ids):
            exchange = self._state.states.get(exchange_id)
            if exchange is None or exchange.latest_price is None:
                continue
            observed_at = (
                exchange.last_scalar_observed_at
                or exchange.last_realtime_observed_at
                or exchange.last_rest_observed_at
            )
            if observed_at is None:
                continue
            age_seconds = (
                wall_now - observed_at.astimezone(UTC)
            ).total_seconds()
            # A REST (bulk price / snapshot) mark is authoritative without the
            # Realtime socket, e.g. at the LIVE startup interlock; RISK applies
            # its own max mark age. A Realtime-sourced mark needs the socket.
            realtime_sourced = (
                exchange.last_scalar_observed_at is None
                and exchange.last_realtime_observed_at is not None
                and observed_at == exchange.last_realtime_observed_at
            )
            trusted = age_seconds >= -1.0 and (
                self._state.health.connected or not realtime_sourced
            )
            age_ns = max(0, int(max(age_seconds, 0.0) * 1_000_000_000))
            marks.append(
                RiskMark(
                    exchange_id=exchange.exchange_id,
                    market_id=exchange.market_id,
                    price=exchange.latest_price,
                    source="sig:realtime_or_bulk_price",
                    observed_monotonic_ns=max(0, now_monotonic_ns - age_ns),
                    trusted=trusted,
                    version=self.version,
                    method="yes_denominated_tournament_price",
                )
            )
        return tuple(marks)


@dataclass(frozen=True, slots=True)
class SigRiskInputs:
    authoritative: AuthoritativeRiskSnapshot
    reconstruction: PnLReconstruction
    exposure: RiskExposureSnapshot
    marks: tuple[RiskMark, ...]
    valuation_positions: tuple[RiskValuationPosition, ...]


def reconstruct_sig_cost_basis(
    account: AccountAuthoritativeSnapshot,
    *,
    tolerance: Decimal = Decimal("0.000001"),
) -> PnLReconstruction:
    """Rebuild surviving cash cost basis from SIG's authoritative FIFO lots."""
    positions: list[CostBasisPosition] = []
    for position in account.positions:
        if position.quantity == 0:
            continue
        if not position.lots:
            raise ValueError(
                f"SIG position {position.exchange_id} has quantity but no FIFO lots"
            )
        expected_side = "YES" if position.quantity > 0 else "NO"
        lot_quantity = Decimal("0")
        cash_cost_basis = Decimal("0")
        yes_basis_numerator = Decimal("0")
        for lot in position.lots:
            side = lot.side.upper()
            if side != expected_side:
                raise ValueError(
                    f"SIG position {position.exchange_id} has mixed/opposite FIFO lot side"
                )
            quantity = abs(lot.quantity)
            if quantity <= 0:
                raise ValueError("SIG FIFO lot quantity must be positive")
            lot_quantity += quantity
            cash_cost_basis += quantity * lot.entry_price
            entry_yes = (
                lot.entry_price
                if side == "YES"
                else Decimal("1") - lot.entry_price
            )
            yes_basis_numerator += quantity * entry_yes

        if abs(lot_quantity - abs(position.quantity)) > tolerance:
            raise ValueError(
                f"SIG position {position.exchange_id} lot quantity disagrees with position"
            )
        if abs(cash_cost_basis - position.cost_basis) > tolerance:
            raise ValueError(
                f"SIG position {position.exchange_id} lot cost basis disagrees with position"
            )
        positions.append(
            CostBasisPosition(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                signed_quantity=position.quantity,
                avg_entry_yes=yes_basis_numerator / lot_quantity,
                canonical_cost_basis=cash_cost_basis,
            )
        )

    return PnLReconstruction(
        positions=tuple(sorted(positions, key=lambda item: item.exchange_id)),
        # RISK-002 takes realised P&L from authoritative tournament equity; the
        # local reconstruction is deliberately only the surviving FIFO cost basis.
        realised_pnl=Decimal("0"),
        processed_fill_ids=(),
    )


def normalize_sig_risk_inputs(
    *,
    session_id: str,
    session_start_equity: Decimal,
    session_start_unrealised_pnl: Decimal,
    account: AccountAuthoritativeSnapshot,
    pnl: PortfolioPnlDto,
    observed_monotonic_ns: int,
    net_external_cash_flow: Decimal,
    unresolved_operation_ids: tuple[str, ...] = (),
    realised_pnl_cursor: str | None = None,
    attributions: tuple[ExposureAttribution, ...] = (),
    memberships: tuple[MarketExposureGroup, ...] = (),
    strategy_attribution_complete: bool | None = None,
) -> SigRiskInputs:
    """Normalize authoritative SIG reads without guessing missing economics.

    api-1.json defines tournament position quantity as signed YES/NO inventory
    and currentPrice as the tournament valuation price. totalAccountValue is the
    authoritative account-equity input. Any caller-visible external cash flow
    since the RISK-002 session start must be supplied explicitly so it is not
    mistaken for trading P&L.
    """
    if account.tournament_id == "":
        raise ValueError("account tournament identity must not be blank")
    if not session_id.strip():
        raise ValueError("session identity must not be blank")

    reconstruction = reconstruct_sig_cost_basis(account)
    positions: list[AuthoritativeRiskPosition] = []
    marks: list[RiskMark] = []
    valuation_positions: list[RiskValuationPosition] = []
    for position in account.positions:
        if position.quantity == 0:
            continue
        if position.current_price is None:
            raise ValueError(
                f"SIG position {position.exchange_id} has no tournament valuation price"
            )
        positions.append(
            AuthoritativeRiskPosition(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                signed_quantity=position.quantity,
                canonical_cost_basis=position.cost_basis,
                unrealised_pnl=position.unrealized_pnl,
            )
        )
        valuation_positions.append(
            RiskValuationPosition(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                signed_quantity=position.quantity,
                baseline_mark=position.current_price,
                baseline_unrealised_pnl=position.unrealized_pnl,
            )
        )
        marks.append(
            RiskMark(
                exchange_id=position.exchange_id,
                market_id=position.market_id,
                price=position.current_price,
                source="sig:tournament_positions",
                observed_monotonic_ns=observed_monotonic_ns,
                trusted=True,
                version=SIG_API_1_SHA256,
                method="tournament_valuation_price",
            )
        )

    session_pnl = pnl.total_account_value - session_start_equity - net_external_cash_flow
    unrealised_change = pnl.unrealized_pnl - session_start_unrealised_pnl
    realised = session_pnl - unrealised_change
    authoritative = AuthoritativeRiskSnapshot(
        session_id=session_id,
        equity=pnl.total_account_value - net_external_cash_flow,
        account_trusted=True,
        observed_monotonic_ns=observed_monotonic_ns,
        positions=tuple(positions),
        unresolved_operation_ids=unresolved_operation_ids,
        realised_pnl=realised,
        unrealised_pnl=pnl.unrealized_pnl,
        realised_pnl_cursor=realised_pnl_cursor,
    )
    exposure = build_exposure_snapshot(
        account.to_runtime_portfolio(),
        attributions=attributions,
        memberships=memberships,
        strategy_attribution_complete=strategy_attribution_complete,
    )
    return SigRiskInputs(
        authoritative=authoritative,
        reconstruction=reconstruction,
        exposure=exposure,
        marks=tuple(marks),
        valuation_positions=tuple(valuation_positions),
    )


class TournamentTransactionRest(Protocol):
    async def list_tournament_transactions(
        self,
        tournament_slug: str,
        *,
        limit: int = 200,
        cursor: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ) -> TournamentTransactionPageDto: ...


@dataclass(frozen=True, slots=True)
class ExternalCashFlowScan:
    delta: Decimal
    newest_event_id: str | None
    prior_cursor_found: bool
    events_scanned: int


async def latest_tournament_transaction_id(
    rest: TournamentTransactionRest,
    *,
    tournament_slug: str,
) -> str | None:
    page = await rest.list_tournament_transactions(
        tournament_slug,
        limit=1,
    )
    if not page.data:
        return None
    return page.data[0].event_id


async def scan_external_cash_flows(
    rest: TournamentTransactionRest,
    *,
    tournament_slug: str,
    prior_event_id: str | None,
) -> ExternalCashFlowScan:
    """Walk newest-first transaction history until the durable cursor is found."""
    cursor: str | None = None
    newest_event_id: str | None = None
    delta = Decimal("0")
    scanned = 0
    prior_found = prior_event_id is None

    while True:
        page = await rest.list_tournament_transactions(
            tournament_slug,
            limit=200,
            cursor=cursor,
        )
        if not page.coverage.complete:
            raise RuntimeError("SIG tournament transaction coverage is incomplete")
        for event in page.data:
            if newest_event_id is None:
                newest_event_id = event.event_id
            if prior_event_id is not None and event.event_id == prior_event_id:
                prior_found = True
                return ExternalCashFlowScan(
                    delta=delta,
                    newest_event_id=newest_event_id,
                    prior_cursor_found=True,
                    events_scanned=scanned,
                )
            scanned += 1
            if event.event_type == "deposit":
                if event.amount is None:
                    raise RuntimeError("SIG deposit transaction is missing amount")
                delta += event.amount

        if not page.pagination.has_more:
            break
        cursor = page.pagination.next_cursor
        if cursor is None or not cursor.strip():
            raise RuntimeError("SIG transaction pagination lost next cursor")

    if prior_event_id is not None and not prior_found:
        raise RuntimeError("durable SIG transaction cursor was not found")
    return ExternalCashFlowScan(
        delta=delta,
        newest_event_id=newest_event_id,
        prior_cursor_found=prior_found,
        events_scanned=scanned,
    )


def apply_external_cash_flow_scan(
    state: CapitalRiskState,
    scan: ExternalCashFlowScan,
) -> CapitalRiskState:
    if not scan.prior_cursor_found:
        raise ValueError("cannot apply an unreconciled transaction scan")
    return replace(
        state,
        net_external_cash_flow=state.net_external_cash_flow + scan.delta,
        external_cash_flow_cursor=(
            scan.newest_event_id
            if scan.newest_event_id is not None
            else state.external_cash_flow_cursor
        ),
    )
