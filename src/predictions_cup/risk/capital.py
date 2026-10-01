"""Pure RISK-002 capital-control contracts and accounting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from predictions_cup.runtime.models import (
    OrderAction,
    OutcomeSide,
    RuntimePortfolio,
)

ZERO = Decimal("0")


class HaltScope(StrEnum):
    GLOBAL = "GLOBAL"
    STRATEGY_ID = "STRATEGY_ID"
    STRATEGY_FAMILY = "STRATEGY_FAMILY"


@dataclass(frozen=True, slots=True)
class RiskMark:
    exchange_id: str
    market_id: str
    price: Decimal
    source: str
    observed_monotonic_ns: int
    trusted: bool
    version: str
    method: str

    def __post_init__(self) -> None:
        if (
            not self.exchange_id.strip()
            or not self.market_id.strip()
            or not self.source.strip()
        ):
            raise ValueError("mark identity/source must not be blank")
        if not self.version.strip() or not self.method.strip():
            raise ValueError("mark version/method must not be blank")
        if not ZERO <= self.price <= Decimal("1"):
            raise ValueError("risk mark must lie on probability support")
        if self.observed_monotonic_ns < 0:
            raise ValueError("mark observation time must be non-negative")


class RiskMarkProvider(Protocol):
    def marks_for(
        self,
        exchange_ids: frozenset[str],
        *,
        now_monotonic_ns: int,
    ) -> tuple[RiskMark, ...]: ...


@dataclass(frozen=True, slots=True)
class MarketExposureGroup:
    market_id: str
    tournament_id: str
    group_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.market_id.strip() or not self.tournament_id.strip():
            raise ValueError("market/tournament must not be blank")
        if any(not group.strip() for group in self.group_ids):
            raise ValueError("group ids must not be blank")


class ExposureGroupProvider(Protocol):
    def groups_for(self, market_id: str, tournament_id: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ExposureBucket:
    key: str
    exposure: float

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("exposure bucket key must not be blank")
        if self.exposure < 0.0:
            raise ValueError("exposure must be non-negative")


@dataclass(frozen=True, slots=True)
class ExposureAttribution:
    market_id: str
    tournament_id: str
    strategy_id: str
    strategy_family: str
    exposure: float
    source: str

    def __post_init__(self) -> None:
        if min(
            len(self.market_id.strip()),
            len(self.tournament_id.strip()),
            len(self.strategy_id.strip()),
            len(self.strategy_family.strip()),
            len(self.source.strip()),
        ) == 0:
            raise ValueError("attribution identity must not be blank")
        if self.exposure < 0.0:
            raise ValueError("attributed exposure must be non-negative")


@dataclass(frozen=True, slots=True)
class RiskExposureSnapshot:
    gross_exposure: float
    net_directional_exposure: float
    open_order_exposure: float
    uncertain_order_exposure: float
    by_market: tuple[ExposureBucket, ...] = ()
    by_strategy: tuple[ExposureBucket, ...] = ()
    by_tournament: tuple[ExposureBucket, ...] = ()
    by_group: tuple[ExposureBucket, ...] = ()
    trusted: bool = False
    strategy_attribution_complete: bool = False
    group_classification_complete: bool = False

    def __post_init__(self) -> None:
        if min(
            self.gross_exposure,
            self.open_order_exposure,
            self.uncertain_order_exposure,
        ) < 0.0:
            raise ValueError("gross/open/uncertain exposure must be non-negative")

    def market(self, market_id: str) -> float:
        return _bucket_value(self.by_market, market_id)

    def strategy(self, strategy_id: str) -> float:
        return _bucket_value(self.by_strategy, strategy_id)

    def tournament(self, tournament_id: str) -> float:
        return _bucket_value(self.by_tournament, tournament_id)

    def group(self, group_id: str) -> float:
        return _bucket_value(self.by_group, group_id)


def _bucket_value(buckets: tuple[ExposureBucket, ...], key: str) -> float:
    for bucket in buckets:
        if bucket.key == key:
            return bucket.exposure
    return 0.0


def build_exposure_snapshot(
    portfolio: RuntimePortfolio,
    *,
    attributions: tuple[ExposureAttribution, ...] = (),
    memberships: tuple[MarketExposureGroup, ...] = (),
    strategy_attribution_complete: bool | None = None,
) -> RiskExposureSnapshot:
    """Build a conservative one-unit-per-share exposure view.

    Position and open/UNCERTAIN order exposure are taken from BUILD-009 runtime
    state. Strategy attribution is deliberately supplied by the execution
    journal/account attribution layer rather than guessed here.
    """
    by_market: dict[str, float] = {}
    by_tournament: dict[str, float] = {}
    net_directional = 0.0
    for position in portfolio.positions:
        exposure = abs(position.gross_exposure)
        by_market[position.market_id] = by_market.get(position.market_id, 0.0) + exposure
        by_tournament[position.tournament_id] = (
            by_tournament.get(position.tournament_id, 0.0) + exposure
        )
        net_directional += position.signed_quantity

    open_exposure = 0.0
    uncertain_exposure = 0.0
    for order in portfolio.orders:
        if not (order.open or order.uncertain):
            continue
        by_market[order.market_id] = by_market.get(order.market_id, 0.0) + order.reserved_exposure
        by_tournament[order.tournament_id] = (
            by_tournament.get(order.tournament_id, 0.0) + order.reserved_exposure
        )
        open_exposure += order.reserved_exposure
        if order.uncertain:
            uncertain_exposure += order.reserved_exposure

    by_strategy: dict[str, float] = {}
    for item in attributions:
        by_strategy[item.strategy_id] = (
            by_strategy.get(item.strategy_id, 0.0) + item.exposure
        )

    group_lookup: dict[tuple[str, str], tuple[str, ...]] = {
        (item.market_id, item.tournament_id): item.group_ids for item in memberships
    }
    by_group: dict[str, float] = {}
    for (market_id, _tournament_id), groups in group_lookup.items():
        amount = by_market.get(market_id, 0.0)
        if amount == 0.0:
            continue
        for group_id in groups:
            by_group[group_id] = by_group.get(group_id, 0.0) + amount

    gross = sum(abs(position.gross_exposure) for position in portfolio.positions) + open_exposure
    strategy_complete = (
        gross == 0.0
        if strategy_attribution_complete is None
        else strategy_attribution_complete
    )
    exposed_identities = {
        (position.market_id, position.tournament_id)
        for position in portfolio.positions
        if abs(position.gross_exposure) > 0.0
    }
    exposed_identities.update(
        (order.market_id, order.tournament_id)
        for order in portfolio.orders
        if (order.open or order.uncertain) and order.reserved_exposure > 0.0
    )
    classified_identities = {
        (item.market_id, item.tournament_id) for item in memberships
    }
    group_complete = exposed_identities.issubset(classified_identities)
    return RiskExposureSnapshot(
        gross_exposure=gross,
        net_directional_exposure=net_directional,
        open_order_exposure=open_exposure,
        uncertain_order_exposure=uncertain_exposure,
        by_market=_buckets(by_market),
        by_strategy=_buckets(by_strategy),
        by_tournament=_buckets(by_tournament),
        by_group=_buckets(by_group),
        trusted=portfolio.account_trusted,
        strategy_attribution_complete=strategy_complete,
        group_classification_complete=group_complete,
    )


def _buckets(values: dict[str, float]) -> tuple[ExposureBucket, ...]:
    return tuple(
        ExposureBucket(key=key, exposure=value)
        for key, value in sorted(values.items())
    )


@dataclass(frozen=True, slots=True)
class HaltState:
    scope: HaltScope
    scope_value: str
    reason: str
    tripped_monotonic_ns: int
    active: bool = True
    reset_by: str | None = None
    reset_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.scope_value.strip() or not self.reason.strip():
            raise ValueError("halt scope/reason must not be blank")
        if self.tripped_monotonic_ns < 0:
            raise ValueError("halt time must be non-negative")


@dataclass(frozen=True, slots=True)
class CapitalRiskState:
    session_id: str
    session_start_equity: Decimal
    session_start_unrealised_pnl: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal
    current_equity: Decimal
    peak_session_equity: Decimal
    drawdown: Decimal
    net_external_cash_flow: Decimal
    exposure: RiskExposureSnapshot
    account_trusted: bool
    account_observed_monotonic_ns: int
    marks_trusted: bool
    oldest_mark_observed_monotonic_ns: int | None
    reconciliation_complete: bool
    global_halt: HaltState | None
    strategy_halts: tuple[HaltState, ...]
    limit_profile_version: str
    realised_pnl_cursor: str | None = None
    # Newest SIG tournament transaction already folded into net_external_cash_flow.
    # Kept separate from realised_pnl_cursor (fill reconstruction), which the
    # authoritative refresh overwrites; sharing them re-counted deposits.
    external_cash_flow_cursor: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.limit_profile_version.strip():
            raise ValueError("session/profile identity must not be blank")
        if self.account_observed_monotonic_ns < 0:
            raise ValueError("account observation time must be non-negative")
        if self.drawdown < ZERO:
            raise ValueError("drawdown must be non-negative")

    @property
    def session_pnl(self) -> Decimal:
        return self.current_equity - self.session_start_equity

    def strategy_halted(self, strategy_id: str, family: str) -> bool:
        for halt in self.strategy_halts:
            if not halt.active:
                continue
            if halt.scope is HaltScope.STRATEGY_ID and halt.scope_value == strategy_id:
                return True
            if halt.scope is HaltScope.STRATEGY_FAMILY and halt.scope_value == family:
                return True
        return False




@dataclass(frozen=True, slots=True)
class RiskValuationPosition:
    exchange_id: str
    market_id: str
    signed_quantity: Decimal
    baseline_mark: Decimal
    baseline_unrealised_pnl: Decimal

    def __post_init__(self) -> None:
        if not self.exchange_id.strip() or not self.market_id.strip():
            raise ValueError("valuation position identity must not be blank")
        if not ZERO <= self.baseline_mark <= Decimal("1"):
            raise ValueError("baseline mark must lie on probability support")


def revalue_capital_state(
    state: CapitalRiskState,
    *,
    positions: tuple[RiskValuationPosition, ...],
    marks: tuple[RiskMark, ...],
    now_monotonic_ns: int,
    max_mark_age_ns: int,
    peak_equity_floor: Decimal | None = None,
) -> CapitalRiskState:
    """Revalue open inventory from trusted YES-denominated in-memory marks."""
    if max_mark_age_ns <= 0:
        raise ValueError("max_mark_age_ns must be positive")
    mark_by_exchange = {mark.exchange_id: mark for mark in marks}
    oldest: int | None = None
    unrealised = ZERO
    for position in positions:
        mark = mark_by_exchange.get(position.exchange_id)
        if (
            mark is None
            or mark.market_id != position.market_id
            or not mark.trusted
            or now_monotonic_ns - mark.observed_monotonic_ns > max_mark_age_ns
        ):
            return replace(
                state,
                marks_trusted=False,
                oldest_mark_observed_monotonic_ns=None,
            )
        oldest = (
            mark.observed_monotonic_ns
            if oldest is None
            else min(oldest, mark.observed_monotonic_ns)
        )
        unrealised += position.baseline_unrealised_pnl + position.signed_quantity * (
            mark.price - position.baseline_mark
        )

    baseline_unrealised = sum(
        (item.baseline_unrealised_pnl for item in positions),
        ZERO,
    )
    equity = state.current_equity + unrealised - baseline_unrealised
    peak = max(
        state.peak_session_equity,
        equity,
        state.peak_session_equity if peak_equity_floor is None else peak_equity_floor,
    )
    return replace(
        state,
        unrealised_pnl=unrealised,
        current_equity=equity,
        peak_session_equity=peak,
        drawdown=peak - equity,
        marks_trusted=True,
        oldest_mark_observed_monotonic_ns=oldest,
    )


@dataclass(frozen=True, slots=True)
class RiskFill:
    fill_id: str
    exchange_id: str
    market_id: str
    outcome_side: OutcomeSide
    action: OrderAction
    quantity: Decimal
    price: Decimal
    filled_monotonic_ns: int
    fee: Decimal = ZERO

    def __post_init__(self) -> None:
        if not self.fill_id.strip() or not self.exchange_id.strip() or not self.market_id.strip():
            raise ValueError("fill identity must not be blank")
        if self.quantity <= ZERO:
            raise ValueError("fill quantity must be positive")
        if not ZERO <= self.price <= Decimal("1"):
            raise ValueError("fill price must lie on probability support")
        if self.fee < ZERO:
            raise ValueError("fill fee must be non-negative")
        if self.filled_monotonic_ns < 0:
            raise ValueError("fill time must be non-negative")


@dataclass(frozen=True, slots=True)
class CostBasisPosition:
    exchange_id: str
    market_id: str
    signed_quantity: Decimal
    avg_entry_yes: Decimal
    canonical_cost_basis: Decimal

    def __post_init__(self) -> None:
        if not self.exchange_id.strip() or not self.market_id.strip():
            raise ValueError("position identity must not be blank")
        if not ZERO <= self.avg_entry_yes <= Decimal("1"):
            raise ValueError("entry price must lie on probability support")
        if self.canonical_cost_basis < ZERO:
            raise ValueError("cost basis must be non-negative")


@dataclass(frozen=True, slots=True)
class PnLReconstruction:
    positions: tuple[CostBasisPosition, ...]
    realised_pnl: Decimal
    processed_fill_ids: tuple[str, ...]
    cursor: str | None = None


def replay_fills(
    fills: Iterable[RiskFill],
    *,
    cursor: str | None = None,
) -> PnLReconstruction:
    """Replay normalized fills using deterministic average-cost accounting.

    NO-side trades are converted to their economically equivalent YES price and
    direction. The caller must supply action metadata from authoritative order or
    journal evidence; this function never infers missing action from a fill row.
    """
    unique: dict[str, RiskFill] = {}
    for fill in fills:
        existing_fill = unique.get(fill.fill_id)
        if existing_fill is not None and existing_fill != fill:
            raise ValueError(f"conflicting replay for fill {fill.fill_id}")
        unique[fill.fill_id] = fill

    positions: dict[str, CostBasisPosition] = {}
    realised = ZERO
    ordered = sorted(
        unique.values(),
        key=lambda item: (item.filled_monotonic_ns, item.fill_id),
    )
    for fill in ordered:
        delta, price_yes = _canonical_trade(fill)
        current_position = positions.get(fill.exchange_id)
        if current_position is None:
            positions[fill.exchange_id] = _open_position(fill, delta, price_yes)
            realised -= fill.fee
            continue

        q0 = current_position.signed_quantity
        if q0 == ZERO or (q0 > ZERO) == (delta > ZERO):
            q1 = q0 + delta
            weighted = (
                abs(q0) * current_position.avg_entry_yes + abs(delta) * price_yes
            ) / abs(q1)
            positions[fill.exchange_id] = CostBasisPosition(
                exchange_id=fill.exchange_id,
                market_id=fill.market_id,
                signed_quantity=q1,
                avg_entry_yes=weighted,
                canonical_cost_basis=_cash_cost_basis(q1, weighted),
            )
            realised -= fill.fee
            continue

        close_quantity = min(abs(q0), abs(delta))
        if q0 > ZERO:
            realised += close_quantity * (price_yes - current_position.avg_entry_yes)
        else:
            realised += close_quantity * (current_position.avg_entry_yes - price_yes)
        realised -= fill.fee

        q1 = q0 + delta
        if q1 == ZERO:
            positions.pop(fill.exchange_id, None)
        elif (q1 > ZERO) == (q0 > ZERO):
            positions[fill.exchange_id] = CostBasisPosition(
                exchange_id=fill.exchange_id,
                market_id=fill.market_id,
                signed_quantity=q1,
                avg_entry_yes=current_position.avg_entry_yes,
                canonical_cost_basis=_cash_cost_basis(q1, current_position.avg_entry_yes),
            )
        else:
            positions[fill.exchange_id] = CostBasisPosition(
                exchange_id=fill.exchange_id,
                market_id=fill.market_id,
                signed_quantity=q1,
                avg_entry_yes=price_yes,
                canonical_cost_basis=_cash_cost_basis(q1, price_yes),
            )

    return PnLReconstruction(
        positions=tuple(sorted(positions.values(), key=lambda item: item.exchange_id)),
        realised_pnl=realised,
        processed_fill_ids=tuple(fill.fill_id for fill in ordered),
        cursor=cursor,
    )


def _canonical_trade(fill: RiskFill) -> tuple[Decimal, Decimal]:
    price_yes = fill.price if fill.outcome_side is OutcomeSide.YES else Decimal("1") - fill.price
    direction = Decimal("1")
    if (
        (fill.outcome_side is OutcomeSide.YES and fill.action is OrderAction.SELL)
        or (fill.outcome_side is OutcomeSide.NO and fill.action is OrderAction.BUY)
    ):
        direction = Decimal("-1")
    return direction * fill.quantity, price_yes


def _open_position(
    fill: RiskFill,
    delta: Decimal,
    price_yes: Decimal,
) -> CostBasisPosition:
    return CostBasisPosition(
        exchange_id=fill.exchange_id,
        market_id=fill.market_id,
        signed_quantity=delta,
        avg_entry_yes=price_yes,
        canonical_cost_basis=_cash_cost_basis(delta, price_yes),
    )


def _cash_cost_basis(
    signed_quantity: Decimal,
    avg_entry_yes: Decimal,
) -> Decimal:
    """Cash paid for the surviving side, while avg_entry_yes stays YES-denominated."""
    if signed_quantity >= ZERO:
        return abs(signed_quantity) * avg_entry_yes
    return abs(signed_quantity) * (Decimal("1") - avg_entry_yes)


@dataclass(frozen=True, slots=True)
class AuthoritativeRiskPosition:
    exchange_id: str
    market_id: str
    signed_quantity: Decimal
    canonical_cost_basis: Decimal | None
    unrealised_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AuthoritativeRiskSnapshot:
    session_id: str
    equity: Decimal
    account_trusted: bool
    observed_monotonic_ns: int
    positions: tuple[AuthoritativeRiskPosition, ...]
    unresolved_operation_ids: tuple[str, ...] = ()
    realised_pnl: Decimal | None = None
    unrealised_pnl: Decimal | None = None
    realised_pnl_cursor: str | None = None


class ReconciliationError(RuntimeError):
    pass


def reconcile_capital_state(
    *,
    previous: CapitalRiskState,
    authoritative: AuthoritativeRiskSnapshot,
    reconstruction: PnLReconstruction | None,
    exposure: RiskExposureSnapshot,
    marks: tuple[RiskMark, ...],
    now_monotonic_ns: int,
    max_account_age_ns: int,
    max_mark_age_ns: int,
    session_loss_limit: Decimal | None,
    drawdown_limit: Decimal | None,
    cost_basis_tolerance: Decimal = Decimal("0.01"),
    # SIG reports per-position and account PnL/value rounded to cents.
    pnl_tolerance: Decimal = Decimal("0.01"),
) -> CapitalRiskState:
    """Reconcile durable state against authoritative account and explicit marks."""
    if authoritative.session_id != previous.session_id:
        raise ReconciliationError("session_identity_mismatch")
    if not authoritative.account_trusted:
        raise ReconciliationError("account_state_untrusted")
    if now_monotonic_ns - authoritative.observed_monotonic_ns > max_account_age_ns:
        raise ReconciliationError("account_state_stale")
    if authoritative.unresolved_operation_ids:
        raise ReconciliationError("execution_state_unresolved")
    if not exposure.trusted:
        raise ReconciliationError("exposure_state_untrusted")

    authoritative_positions = {
        item.exchange_id: item for item in authoritative.positions
    }
    reconstructed = (
        {}
        if reconstruction is None
        else {item.exchange_id: item for item in reconstruction.positions}
    )
    if reconstruction is not None and set(reconstructed) != set(authoritative_positions):
        raise ReconciliationError("journal_account_position_disagreement")

    mark_by_exchange = {mark.exchange_id: mark for mark in marks}
    oldest_mark: int | None = None
    locally_computed_unrealised = ZERO
    authoritative_position_unrealised = ZERO
    for exchange_id, account_position in authoritative_positions.items():
        local = reconstructed.get(exchange_id)
        if reconstruction is not None:
            if local is None:
                raise ReconciliationError("journal_account_position_disagreement")
            if local.market_id != account_position.market_id:
                raise ReconciliationError("journal_account_market_disagreement")
            if local.signed_quantity != account_position.signed_quantity:
                raise ReconciliationError("journal_account_quantity_disagreement")
            if (
                account_position.canonical_cost_basis is not None
                and abs(
                    local.canonical_cost_basis - account_position.canonical_cost_basis
                )
                > cost_basis_tolerance
            ):
                raise ReconciliationError("journal_account_cost_basis_disagreement")

        mark = mark_by_exchange.get(exchange_id)
        if mark is None or mark.market_id != account_position.market_id:
            raise ReconciliationError("required_mark_missing")
        if not mark.trusted:
            raise ReconciliationError("required_mark_untrusted")
        if now_monotonic_ns - mark.observed_monotonic_ns > max_mark_age_ns:
            raise ReconciliationError("required_mark_stale")
        oldest_mark = (
            mark.observed_monotonic_ns
            if oldest_mark is None
            else min(oldest_mark, mark.observed_monotonic_ns)
        )
        if local is not None:
            position_pnl = account_position.signed_quantity * (
                mark.price - local.avg_entry_yes
            )
            locally_computed_unrealised += position_pnl
            if (
                account_position.unrealised_pnl is not None
                and abs(position_pnl - account_position.unrealised_pnl) > pnl_tolerance
            ):
                raise ReconciliationError("account_mark_pnl_disagreement")
        if account_position.unrealised_pnl is not None:
            authoritative_position_unrealised += account_position.unrealised_pnl

    if authoritative.realised_pnl is None or authoritative.unrealised_pnl is None:
        raise ReconciliationError("authoritative_pnl_incomplete")
    realised = authoritative.realised_pnl
    unrealised = authoritative.unrealised_pnl
    if authoritative_positions and any(
        item.unrealised_pnl is None for item in authoritative_positions.values()
    ):
        raise ReconciliationError("authoritative_position_pnl_incomplete")
    # Each rounded SIG figure contributes up to one cent of error to sums.
    aggregate_tolerance = pnl_tolerance * (len(authoritative_positions) + 1)
    if abs(authoritative_position_unrealised - unrealised) > aggregate_tolerance:
        raise ReconciliationError("authoritative_unrealised_pnl_disagreement")
    if (
        reconstruction is not None
        and abs(locally_computed_unrealised - unrealised) > aggregate_tolerance
    ):
        raise ReconciliationError("local_mark_pnl_disagreement")

    peak = max(previous.peak_session_equity, authoritative.equity)
    drawdown = peak - authoritative.equity
    global_halt = previous.global_halt
    session_pnl = authoritative.equity - previous.session_start_equity
    unrealised_change = unrealised - previous.session_start_unrealised_pnl
    if abs(realised + unrealised_change - session_pnl) > aggregate_tolerance:
        raise ReconciliationError("authoritative_session_pnl_disagreement")
    hard_reason: str | None = None
    if session_loss_limit is not None and session_pnl <= -session_loss_limit:
        hard_reason = "session_loss_limit"
    if drawdown_limit is not None and drawdown >= drawdown_limit:
        hard_reason = "peak_drawdown_limit"
    if hard_reason is not None and (global_halt is None or not global_halt.active):
        global_halt = HaltState(
            scope=HaltScope.GLOBAL,
            scope_value="capital",
            reason=hard_reason,
            tripped_monotonic_ns=now_monotonic_ns,
        )

    return CapitalRiskState(
        session_id=previous.session_id,
        session_start_equity=previous.session_start_equity,
        session_start_unrealised_pnl=previous.session_start_unrealised_pnl,
        realised_pnl=realised,
        unrealised_pnl=unrealised,
        current_equity=authoritative.equity,
        peak_session_equity=peak,
        drawdown=drawdown,
        net_external_cash_flow=previous.net_external_cash_flow,
        exposure=exposure,
        account_trusted=True,
        account_observed_monotonic_ns=authoritative.observed_monotonic_ns,
        marks_trusted=True,
        oldest_mark_observed_monotonic_ns=oldest_mark,
        reconciliation_complete=True,
        global_halt=global_halt,
        strategy_halts=previous.strategy_halts,
        limit_profile_version=previous.limit_profile_version,
        realised_pnl_cursor=(
            reconstruction.cursor
            if reconstruction is not None
            else authoritative.realised_pnl_cursor
        ),
        external_cash_flow_cursor=previous.external_cash_flow_cursor,
    )


def trip_global_halt(
    state: CapitalRiskState,
    *,
    reason: str,
    now_monotonic_ns: int,
) -> CapitalRiskState:
    if state.global_halt is not None and state.global_halt.active:
        return state
    return replace(
        state,
        global_halt=HaltState(
            scope=HaltScope.GLOBAL,
            scope_value="capital",
            reason=reason,
            tripped_monotonic_ns=now_monotonic_ns,
        ),
    )


def reset_global_halt(
    state: CapitalRiskState,
    *,
    expected_reason: str,
    operator: str,
    now_monotonic_ns: int,
) -> CapitalRiskState:
    halt = state.global_halt
    if halt is None or not halt.active:
        raise ValueError("global halt is not active")
    if halt.reason != expected_reason:
        raise ValueError("global halt reason changed; refusing stale reset")
    if not operator.strip():
        raise ValueError("operator must not be blank")
    return replace(
        state,
        global_halt=replace(
            halt,
            active=False,
            reset_by=operator,
            reset_monotonic_ns=now_monotonic_ns,
        ),
    )


def trip_strategy_halt(
    state: CapitalRiskState,
    *,
    scope: HaltScope,
    scope_value: str,
    reason: str,
    now_monotonic_ns: int,
) -> CapitalRiskState:
    if scope is HaltScope.GLOBAL:
        raise ValueError("use trip_global_halt for global scope")
    halt = HaltState(
        scope=scope,
        scope_value=scope_value,
        reason=reason,
        tripped_monotonic_ns=now_monotonic_ns,
    )
    retained = tuple(
        item
        for item in state.strategy_halts
        if not (item.scope is scope and item.scope_value == scope_value and item.active)
    )
    return replace(state, strategy_halts=retained + (halt,))


def clear_strategy_halt(
    state: CapitalRiskState,
    *,
    scope: HaltScope,
    scope_value: str,
    operator: str,
    now_monotonic_ns: int,
) -> CapitalRiskState:
    if not operator.strip():
        raise ValueError("operator must not be blank")
    found = False
    updated: list[HaltState] = []
    for halt in state.strategy_halts:
        if halt.active and halt.scope is scope and halt.scope_value == scope_value:
            found = True
            updated.append(
                replace(
                    halt,
                    active=False,
                    reset_by=operator,
                    reset_monotonic_ns=now_monotonic_ns,
                )
            )
        else:
            updated.append(halt)
    if not found:
        raise ValueError("strategy halt is not active")
    return replace(state, strategy_halts=tuple(updated))


class SessionPolicy(Protocol):
    def session_id(self) -> str: ...

    def session_start_equity(self) -> Decimal: ...


class RiskStateStore(Protocol):
    def load(self) -> CapitalRiskState | None: ...

    def save(self, state: CapitalRiskState, *, event_type: str, detail: str) -> None: ...
