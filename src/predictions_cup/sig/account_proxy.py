"""Fail-closed local projection of SIG account exposure between REST snapshots."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from predictions_cup.execution.journal import ExecutionJournal, ExecutionJournalEvent
from predictions_cup.runtime.models import (
    AccountTrustGrade,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
)
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.realtime_models import (
    AccountBatchDto,
    AccountRefundDto,
    AccountSettlementDto,
)

logger = logging.getLogger(__name__)
_BLOCKING_JOURNAL_STATES = {"PENDING", "UNCERTAIN", "RECONCILING", "CANCEL_PENDING"}


@dataclass(frozen=True, slots=True)
class AccountProxyDiff:
    position_diffs: tuple[tuple[str, str, float], ...]
    open_order_missing: tuple[str, ...]
    open_order_extra: tuple[str, ...]
    position_diff_exceeded: bool
    cash_diff: float | None = None
    open_order_mismatch: tuple[str, ...] = ()

    @property
    def has_diff(self) -> bool:
        return bool(
            self.position_diffs
            or self.open_order_missing
            or self.open_order_extra
            or self.open_order_mismatch
            or (self.cash_diff is not None and abs(self.cash_diff) > 0.01)
        )


@dataclass(frozen=True, slots=True)
class _Intent:
    intent_id: str
    exchange_id: str
    market_id: str
    tournament_id: str
    signed_direction: int
    cash_direction: int
    quantity: Decimal
    strategy_id: str | None


@dataclass(slots=True)
class _ProxyOrder:
    order_id: str
    intent: _Intent
    filled_after_snapshot: Decimal
    open: bool
    baseline_order: bool
    terminal_fill_unknown: bool = False
    cumulative_fill_qty: Decimal = Decimal("0")
    cumulative_cash_delta: Decimal | None = None
    individual_fill_events: dict[str, tuple[Decimal, Decimal | None]] | None = None
    cash_applied: Decimal = Decimal("0")


class AccountProxyLedger:
    """Account/position estimate with conservative order and fill accounting.

    The ledger is seeded only from complete authoritative account snapshots. It
    applies identified local execution events and account batches afterward. Any
    unknown order/fill identity, unsupported economic event, age breach, or
    journal uncertainty denies PROXY trust until reconciliation resolves it.
    """

    def __init__(
        self,
        *,
        tournament_id: str,
        enabled: bool = False,
        max_age_ns: int = 600_000_000_000,
        position_diff_threshold: float = 1.0,
        exchange_market_ids: Mapping[str, str] | None = None,
    ) -> None:
        if not tournament_id.strip():
            raise ValueError("tournament_id must not be blank")
        if max_age_ns <= 0 or position_diff_threshold < 0.0:
            raise ValueError("invalid account proxy limits")
        self.tournament_id = tournament_id
        self.enabled = enabled
        self.max_age_ns = max_age_ns
        self.position_diff_threshold = position_diff_threshold
        self._exchange_market_ids = dict(exchange_market_ids or {})
        self.last_authoritative_monotonic_ns: int | None = None
        self.last_authoritative_snapshot: AccountAuthoritativeSnapshot | None = None
        self._cash_balance: Decimal | None = None
        self.reconciliation_blocked = False
        self.discrepancy_reason: str | None = None
        self.diff_count = 0
        self.last_diff: AccountProxyDiff | None = None
        self._positions: dict[str, tuple[str, Decimal]] = {}
        self._orders: dict[str, _ProxyOrder] = {}
        self._pending_intents: dict[tuple[str, str], _Intent] = {}
        self._journal_event_cursor = 0
        self._seen_fill_keys: set[str] = set()
        self._journal_blockers: frozenset[str] = frozenset()
        self._last_revision: int | None = None
        self._last_grade: AccountTrustGrade | None = None

    def initialize_journal_cursor(self, journal: ExecutionJournal) -> None:
        """Start consuming only execution events created after proxy attachment."""
        self._journal_event_cursor = journal.latest_event_id()
        self.refresh_journal_blockers(journal)

    def refresh_journal_blockers(self, journal: ExecutionJournal) -> None:
        self._journal_blockers = frozenset(
            envelope.lifecycle_state.value
            for envelope in journal.unresolved()
            if envelope.tournament_id == self.tournament_id
            and envelope.lifecycle_state.value in _BLOCKING_JOURNAL_STATES
        )

    def seed_authoritative(
        self,
        snapshot: AccountAuthoritativeSnapshot,
        *,
        observed_monotonic_ns: int,
    ) -> AccountProxyDiff:
        if snapshot.tournament_id != self.tournament_id:
            raise ValueError("authoritative snapshot tournament mismatch")
        if observed_monotonic_ns < 0:
            raise ValueError("authoritative monotonic timestamp must be non-negative")
        diff = self._diff_against(snapshot)
        self.last_diff = diff
        if diff.has_diff:
            self.diff_count += 1
            logger.info(
                "SIG account proxy reconciliation diff positions=%s open_order_missing=%s "
                "open_order_extra=%s open_order_mismatch=%s cash_diff=%s",
                diff.position_diffs,
                diff.open_order_missing,
                diff.open_order_extra,
                diff.open_order_mismatch,
                diff.cash_diff,
            )
        if diff.position_diff_exceeded:
            logger.warning(
                "SIG account proxy position discrepancy exceeds threshold=%s diffs=%s; "
                "or position identity mismatch; account remains UNTRUSTED until a clean "
                "reconciliation",
                self.position_diff_threshold,
                diff.position_diffs,
            )

        pending = dict(self._pending_intents)
        prior_cash = self._cash_balance
        self._positions = {
            row.exchange_id: (row.market_id, row.quantity)
            for row in snapshot.positions
            if row.quantity != 0
        }
        market_by_exchange = dict(self._exchange_market_ids)
        market_by_exchange.update({row.exchange_id: row.market_id for row in snapshot.positions})
        prior_orders = self._orders
        self._orders = {}
        for order in snapshot.open_orders:
            market_id = market_by_exchange.get(order.exchange_id)
            if market_id is None:
                self._mark_discrepancy("authoritative_open_order_market_unknown")
                continue
            intent = _Intent(
                intent_id=f"sig-order-{order.id}",
                exchange_id=order.exchange_id,
                market_id=market_id,
                tournament_id=self.tournament_id,
                signed_direction=_signed_direction(order.side, order.action),
                cash_direction=-1 if order.action == "buy" else 1,
                quantity=abs(order.quantity),
                strategy_id=None,
            )
            self._orders[str(order.id)] = _ProxyOrder(
                order_id=str(order.id),
                intent=intent,
                filled_after_snapshot=Decimal("0"),
                open=order.open,
                baseline_order=True,
                individual_fill_events={},
            )
        # The authoritative order list and position list are read separately.
        # During a busy account they can disagree about whether a known order
        # is still open even when the position and cash projections reconcile.
        # Keep proxy-only orders as uncertain reservations so their remaining
        # quantity stays in the portfolio's worst-case bounds. Orders found
        # only by REST are already included above as authoritative reservations.
        for order_id in diff.open_order_extra:
            prior_order = prior_orders.get(order_id)
            if prior_order is None or order_id in self._orders:
                continue
            prior_order.open = False
            prior_order.terminal_fill_unknown = True
            self._orders[order_id] = prior_order
        # In-flight local submissions remain risk-bearing if the account read
        # raced their response. The controller retries when the journal changes
        # during a read; this carry also covers an operation already in flight at
        # the read fence.
        self._pending_intents = pending
        self._last_revision = None
        self.last_authoritative_snapshot = snapshot
        self._cash_balance = snapshot.cash_balance
        if self.enabled and prior_cash is not None and snapshot.cash_balance is None:
            self._mark_discrepancy("authoritative_cash_unavailable")
        self.last_authoritative_monotonic_ns = observed_monotonic_ns
        self.reconciliation_blocked = (
            diff.position_diff_exceeded
            or bool(diff.open_order_mismatch)
            or (diff.cash_diff is not None and abs(diff.cash_diff) > 0.01)
            or (self.enabled and prior_cash is not None and snapshot.cash_balance is None)
        )
        if not self.reconciliation_blocked:
            self.discrepancy_reason = None
        return diff

    def apply_journal_operation(
        self,
        journal: ExecutionJournal,
        logical_operation_id: str,
    ) -> None:
        events = journal.events(logical_operation_id)
        for event in events:
            if event.event_id <= self._journal_event_cursor:
                continue
            self._journal_event_cursor = event.event_id
            self._apply_journal_event(event)
        self.refresh_journal_blockers(journal)

    def set_journal_blockers(self, states: Iterable[str]) -> None:
        self._journal_blockers = frozenset(
            state
            for state in states
            if state in _BLOCKING_JOURNAL_STATES
        )

    def mark_discrepancy(self, reason: str) -> None:
        self._mark_discrepancy(reason)

    def apply_realtime_batch(self, payload: object) -> None:
        try:
            batch = AccountBatchDto.model_validate(payload)
            self._validate_tournament(batch)
        except (ValidationError, ValueError):
            self._mark_discrepancy("malformed_or_wrong_tournament_batch")
            return
        revision = batch.delivery.revision
        if self._last_revision is not None:
            if revision == self._last_revision:
                return
            if batch.delivery.previous_revision != self._last_revision:
                self._last_revision = revision
                self._mark_discrepancy("realtime_revision_gap")
                return
        self._last_revision = revision
        for collateral in batch.collateral_changes:
            if self._cash_balance is None:
                self._mark_discrepancy("cash_baseline_unavailable")
            else:
                self._cash_balance += collateral.delta
        for fill in batch.fills:
            if fill.order_id is None:
                self._mark_discrepancy("fill_without_order_id")
                continue
            order = self._orders.get(str(fill.order_id))
            if order is None:
                self._mark_discrepancy("fill_for_unknown_order")
                continue
            if (
                order.intent.exchange_id != fill.exchange_id
                or order.intent.market_id != fill.market_id
                or (fill.tournament_id is not None and fill.tournament_id != self.tournament_id)
            ):
                self._mark_discrepancy("fill_identity_mismatch")
                continue
            key = _fill_key(
                order_id=str(fill.order_id),
                source_timestamp=fill.executed_at.isoformat(),
                quantity=fill.quantity,
                price=fill.price,
            )
            self._apply_individual_fill(order, key, fill.quantity, fill.price)
        for update in batch.order_updates:
            if update.order_id is None:
                self._mark_discrepancy("order_update_without_order_id")
                continue
            order = self._orders.get(str(update.order_id))
            if order is None:
                self._mark_discrepancy("order_update_for_unknown_order")
                continue
            if (
                order.intent.exchange_id != update.exchange_id
                or order.intent.market_id != update.market_id
                or (update.tournament_id is not None and update.tournament_id != self.tournament_id)
            ):
                self._mark_discrepancy("order_update_identity_mismatch")
                continue
            if update.quantity_traded < 0 or update.quantity_traded > order.intent.quantity:
                self._mark_discrepancy("order_update_fill_quantity_out_of_range")
                continue
            if not order.baseline_order:
                self._apply_cumulative_fill(
                    order,
                    update.quantity_traded,
                    None,
                    cumulative_total_cost=update.total_cost,
                )
            else:
                baseline_fill_matches = self._validate_baseline_order_update(
                    order=order,
                    quantity=update.quantity_traded,
                    total_cost=update.total_cost,
                )
            order.open = update.open
            if not update.open and order.baseline_order:
                order.terminal_fill_unknown = not baseline_fill_matches
        # Project fills before terminal settlement/refund events so a same-batch
        # fill cannot reappear after the market position has been removed.
        for settlement in batch.settlements:
            self._apply_settlement(settlement)
        for refund in batch.refunds:
            self._apply_refund(refund)

    def runtime_portfolio(
        self,
        *,
        account_state_trusted: bool,
        now_monotonic_ns: int,
    ) -> RuntimePortfolio:
        age_ns = self.proxy_age_ns(now_monotonic_ns)
        grade = self.trust_grade(
            account_state_trusted=account_state_trusted,
            proxy_age_ns=age_ns,
        )
        positions = tuple(
            RuntimePosition(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id=self.tournament_id,
                gross_exposure=float(abs(quantity)),
                signed_quantity=float(quantity),
            )
            for exchange_id, (market_id, quantity) in sorted(self._positions.items())
            if quantity != 0
        )
        orders = [self._runtime_order(order) for order in self._orders.values()]
        for (_operation_id, _), intent in self._pending_intents.items():
            orders.append(
                RuntimeOrderState(
                    logical_intent_id=intent.intent_id,
                    exchange_id=intent.exchange_id,
                    market_id=intent.market_id,
                    tournament_id=intent.tournament_id,
                    reserved_exposure=float(intent.quantity),
                    open=False,
                    uncertain=True,
                    strategy_id=intent.strategy_id,
                    signed_quantity=float(intent.signed_direction) * float(intent.quantity),
                )
            )
        uncertainty = sum(order.reserved_exposure for order in orders if order.uncertain)
        if grade is not self._last_grade:
            logger.info(
                "SIG account trust grade=%s proxy_age_ns=%s uncertainty=%.3f "
                "diff_count=%d reason=%s journal_blockers=%s",
                grade.value,
                age_ns,
                uncertainty if grade is AccountTrustGrade.PROXY else 0.0,
                self.diff_count,
                self.discrepancy_reason,
                sorted(self._journal_blockers),
            )
            self._last_grade = grade
        trusted = grade in {AccountTrustGrade.TRUSTED, AccountTrustGrade.PROXY}
        return RuntimePortfolio(
            positions=positions,
            orders=tuple(orders),
            account_trusted=trusted,
            account_trust_grade=grade,
            account_proxy_age_ns=age_ns,
            account_proxy_uncertainty=uncertainty if grade is AccountTrustGrade.PROXY else 0.0,
            account_proxy_cash_balance=self._cash_balance,
        )

    def proxy_age_ns(self, now_monotonic_ns: int) -> int | None:
        if self.last_authoritative_monotonic_ns is None:
            return None
        return max(0, now_monotonic_ns - self.last_authoritative_monotonic_ns)

    def trust_grade(
        self,
        *,
        account_state_trusted: bool,
        proxy_age_ns: int | None,
    ) -> AccountTrustGrade:
        if (
            account_state_trusted
            and not self.reconciliation_blocked
            and self.discrepancy_reason is None
            and not self._journal_blockers
            and (not self.enabled or self._cash_balance is not None)
        ):
            return AccountTrustGrade.TRUSTED
        if (
            self.enabled
            and self.last_authoritative_monotonic_ns is not None
            and proxy_age_ns is not None
            and proxy_age_ns <= self.max_age_ns
            and self._cash_balance is not None
            and not self.reconciliation_blocked
            and self.discrepancy_reason is None
            and not self._journal_blockers
        ):
            return AccountTrustGrade.PROXY
        return AccountTrustGrade.UNTRUSTED

    def _apply_journal_event(self, event: ExecutionJournalEvent) -> None:
        key = (event.logical_operation_id, event.logical_intent_id or "")
        if event.event_type == "SUBMISSION" and event.logical_intent_id is not None:
            intent = _intent_from_submission(event)
            if intent is None:
                self._mark_discrepancy("submission_intent_unavailable")
            else:
                self._pending_intents[key] = intent
            return
        if event.event_type == "ACK":
            intent = self._pending_intents.get(key)
            if intent is None:
                self._mark_discrepancy("ack_without_known_submission")
                return
            if event.exchange_order_id is None:
                self._mark_discrepancy("ack_without_order_id")
                return
            ack_order = _ProxyOrder(
                order_id=event.exchange_order_id,
                intent=intent,
                filled_after_snapshot=Decimal("0"),
                open=True,
                baseline_order=False,
                individual_fill_events={},
            )
            details = _json_object(event.detail_json)
            if isinstance(details.get("open"), bool):
                ack_order.open = bool(details["open"])
            self._orders[event.exchange_order_id] = ack_order
            self._pending_intents.pop(key, None)
            traded = _decimal(details.get("quantityTraded"))
            if traded is None and event.quantity is not None:
                traded = _decimal(event.quantity)
            if traded is not None:
                self._apply_cumulative_fill(
                    ack_order,
                    traded,
                    _decimal(details.get("fillPrice")),
                    cumulative_total_cost=_decimal(details.get("totalCost")),
                )
            if not ack_order.open and ack_order.filled_after_snapshot < intent.quantity:
                ack_order.terminal_fill_unknown = True
            return
        if event.event_type == "REJECTED" and event.logical_intent_id is not None:
            self._pending_intents.pop(key, None)
            return
        if event.event_type == "FILL_SUMMARY":
            fill_order = self._known_order(event.exchange_order_id)
            quantity = _decimal(event.quantity)
            if fill_order is None or quantity is None:
                self._mark_discrepancy("fill_summary_identity_unavailable")
                return
            self._apply_cumulative_fill(fill_order, quantity, _decimal(event.price))
            return
        if event.event_type in {"REALTIME_FILL", "AUTHORITATIVE_FILL"}:
            fill_order = self._known_order(event.exchange_order_id)
            quantity = _decimal(event.quantity)
            if fill_order is None or quantity is None:
                self._mark_discrepancy("journal_fill_identity_unavailable")
                return
            canonical_identity = _fill_key(
                order_id=event.exchange_order_id or "",
                source_timestamp=event.source_timestamp or "",
                quantity=quantity,
                price=_decimal(event.price),
            )
            self._apply_individual_fill(
                fill_order,
                event.fill_id or canonical_identity,
                quantity,
                _decimal(event.price),
                canonical_identity=canonical_identity,
            )
            return
        if event.event_type == "REALTIME_ORDER_UPDATE":
            update_order = self._known_order(event.exchange_order_id)
            quantity = _decimal(event.quantity)
            if update_order is None or quantity is None:
                self._mark_discrepancy("journal_order_update_identity_unavailable")
                return
            if not update_order.baseline_order:
                total_cost = _decimal(_json_object(event.detail_json).get("totalCost"))
                self._apply_cumulative_fill(
                    update_order,
                    quantity,
                    None,
                    cumulative_total_cost=total_cost,
                )
            else:
                total_cost = _decimal(_json_object(event.detail_json).get("totalCost"))
                baseline_fill_matches = self._validate_baseline_order_update(
                    order=update_order, quantity=quantity, total_cost=total_cost
                )
            update_order.open = event.terminal_status == "OPEN"
            if update_order.baseline_order and not update_order.open:
                update_order.terminal_fill_unknown = not baseline_fill_matches
            return
        if event.event_type == "CANCEL_ACK" and event.exchange_order_id is not None:
            cancelled_order = self._orders.get(event.exchange_order_id)
            if cancelled_order is not None:
                cancelled_order.open = False
                cancelled_order.terminal_fill_unknown = True

    def _apply_cumulative_fill(
        self,
        order: _ProxyOrder,
        cumulative: Decimal,
        average_price: Decimal | None,
        *,
        cumulative_total_cost: Decimal | None = None,
    ) -> None:
        if cumulative < 0 or cumulative > order.intent.quantity:
            self._mark_discrepancy("cumulative_fill_out_of_range")
            return
        prior_cumulative = order.cumulative_fill_qty
        order.cumulative_fill_qty = max(prior_cumulative, cumulative)
        if cumulative_total_cost is not None and (
            cumulative_total_cost < 0
            or cumulative_total_cost > order.cumulative_fill_qty
            or (order.cumulative_fill_qty > 0 and cumulative_total_cost == 0)
        ):
            self._mark_discrepancy("cumulative_fill_cost_out_of_range")
            return
        if cumulative_total_cost is not None:
            order.cumulative_cash_delta = (
                Decimal(order.intent.cash_direction) * cumulative_total_cost
            )
        elif average_price is not None:
            cash_sign = Decimal(order.intent.cash_direction)
            order.cumulative_cash_delta = cash_sign * order.cumulative_fill_qty * average_price
        elif order.cumulative_fill_qty > prior_cumulative:
            # Position accounting can use a cumulative order update even when
            # its cash value is absent. Individual fills in the same batch can
            # still provide the exact cash delta.
            order.cumulative_cash_delta = None
        self._recompute_fill_projection(order)

    def _apply_individual_fill(
        self,
        order: _ProxyOrder,
        identity: str,
        quantity: Decimal,
        price: Decimal | None,
        *,
        canonical_identity: str | None = None,
    ) -> None:
        if quantity <= 0:
            self._mark_discrepancy("individual_fill_out_of_range")
            return
        if identity in self._seen_fill_keys:
            return
        if canonical_identity is not None and canonical_identity in self._seen_fill_keys:
            if identity != canonical_identity:
                self._mark_discrepancy("ambiguous_duplicate_fill_identity")
            return
        self._seen_fill_keys.add(identity)
        if canonical_identity is not None:
            self._seen_fill_keys.add(canonical_identity)
        if order.individual_fill_events is None:
            order.individual_fill_events = {}
        order.individual_fill_events[identity] = (quantity, price)
        individual_qty = sum(
            (item[0] for item in order.individual_fill_events.values()),
            start=Decimal("0"),
        )
        if individual_qty > order.intent.quantity:
            self._mark_discrepancy("individual_fill_out_of_range")
            return
        self._recompute_fill_projection(order)

    def _validate_baseline_order_update(
        self,
        *,
        order: _ProxyOrder,
        quantity: Decimal,
        total_cost: Decimal | None,
    ) -> bool:
        individual_events = order.individual_fill_events or {}
        known_quantity = sum(
            (fill_quantity for fill_quantity, _ in individual_events.values()),
            start=Decimal("0"),
        )
        if known_quantity != quantity:
            self._mark_discrepancy("baseline_order_fill_delta_unknown")
            return False
        if total_cost is None:
            if quantity > 0:
                self._mark_discrepancy("baseline_order_fill_cost_unknown")
                return False
            return True
        if any(price is None for _, price in individual_events.values()):
            if quantity > 0:
                self._mark_discrepancy("baseline_order_fill_cost_unknown")
                return False
            return True
        known_cost = sum(
            (
                fill_quantity * price
                for fill_quantity, price in individual_events.values()
                if price is not None
            ),
            start=Decimal("0"),
        )
        if abs(known_cost - total_cost) > Decimal("0.01"):
            self._mark_discrepancy("baseline_order_fill_cost_mismatch")
            return False
        return True

    def _recompute_fill_projection(self, order: _ProxyOrder) -> None:
        individual_events = order.individual_fill_events or {}
        individual_qty = sum(
            (item[0] for item in individual_events.values()),
            start=Decimal("0"),
        )
        individual_cash = Decimal("0")
        individual_cash_known = True
        cash_sign = Decimal(order.intent.cash_direction)
        for quantity, price in individual_events.values():
            if price is None:
                individual_cash_known = False
                break
            individual_cash += cash_sign * quantity * price

        target = max(order.cumulative_fill_qty, individual_qty)
        delta = target - order.filled_after_snapshot
        if delta > 0:
            self._apply_position_delta(order.intent, delta)
            order.filled_after_snapshot = target

        cumulative_cash = order.cumulative_cash_delta
        if order.cumulative_fill_qty > individual_qty:
            cash_target = cumulative_cash
        elif individual_qty > order.cumulative_fill_qty:
            cash_target = individual_cash if individual_cash_known else None
        elif target == 0:
            cash_target = Decimal("0")
        elif cumulative_cash is not None and individual_cash_known:
            cash_target = cumulative_cash
            if abs(cumulative_cash - individual_cash) > Decimal("0.01"):
                self._mark_discrepancy("fill_cash_sources_disagree")
        elif cumulative_cash is not None:
            cash_target = cumulative_cash
        elif individual_cash_known:
            cash_target = individual_cash
        else:
            cash_target = None

        if cash_target is None:
            if target > 0:
                self._mark_discrepancy("fill_cash_value_unavailable")
            return
        if self._cash_balance is None:
            if cash_target != 0:
                self._mark_discrepancy("cash_baseline_unavailable")
            return
        self._cash_balance += cash_target - order.cash_applied
        order.cash_applied = cash_target

    def _apply_settlement(self, settlement: AccountSettlementDto) -> None:
        exchange_id = settlement.exchange_id
        shares = settlement.shares
        side = settlement.outcome_side.lower()
        payout = settlement.payout
        if side not in {"yes", "no"}:
            self._mark_discrepancy("settlement_side_unknown")
            return
        if shares < 0 or payout < 0 or payout > shares:
            self._mark_discrepancy("settlement_value_out_of_range")
            return
        known_market = self._exchange_market_ids.get(exchange_id)
        if known_market is None or known_market != settlement.market_id:
            self._mark_discrepancy("settlement_market_identity_mismatch")
            return
        position = self._positions.get(exchange_id)
        signed = Decimal("1") if side == "yes" else Decimal("-1")
        if position is not None:
            market_id, quantity = position
            if market_id != settlement.market_id or quantity * signed < 0:
                self._mark_discrepancy("settlement_position_side_mismatch")
                return
            if shares > abs(quantity):
                self._mark_discrepancy("settlement_shares_exceed_position")
                return
            remaining = max(Decimal("0"), abs(quantity) - shares)
            if remaining == 0:
                self._positions.pop(exchange_id, None)
            else:
                self._positions[exchange_id] = (market_id, signed * remaining)
        if self._cash_balance is None:
            self._mark_discrepancy("cash_baseline_unavailable")
        else:
            self._cash_balance += payout

    def _apply_refund(self, refund: AccountRefundDto) -> None:
        exchange_id = refund.exchange_id
        shares = refund.shares
        refund_amount = refund.refund_amount
        if shares < 0 or refund_amount < 0 or refund_amount > shares:
            self._mark_discrepancy("refund_value_out_of_range")
            return
        known_market = self._exchange_market_ids.get(exchange_id)
        if known_market is None or known_market != refund.market_id:
            self._mark_discrepancy("refund_market_identity_mismatch")
            return
        position = self._positions.get(exchange_id)
        if position is not None:
            market_id, quantity = position
            if market_id != refund.market_id or shares > abs(quantity):
                self._mark_discrepancy("refund_shares_exceed_position")
                return
            remaining = max(Decimal("0"), abs(quantity) - shares)
            if remaining == 0:
                self._positions.pop(exchange_id, None)
            else:
                self._positions[exchange_id] = (
                    market_id,
                    remaining if quantity > 0 else -remaining,
                )
        if self._cash_balance is None:
            self._mark_discrepancy("cash_baseline_unavailable")
        else:
            self._cash_balance += refund_amount

    def _apply_position_delta(self, intent: _Intent, quantity: Decimal) -> None:
        prior_market, prior = self._positions.get(
            intent.exchange_id,
            (intent.market_id, Decimal("0")),
        )
        if prior_market != intent.market_id:
            self._mark_discrepancy("position_market_identity_changed")
            return
        updated = prior + Decimal(intent.signed_direction) * quantity
        if updated == 0:
            self._positions.pop(intent.exchange_id, None)
        else:
            self._positions[intent.exchange_id] = (intent.market_id, updated)

    def _known_order(self, order_id: str | None) -> _ProxyOrder | None:
        if order_id is None:
            return None
        return self._orders.get(order_id)

    def _runtime_order(self, order: _ProxyOrder) -> RuntimeOrderState:
        unknown_remaining = order.terminal_fill_unknown
        remaining = max(Decimal("0"), order.intent.quantity - order.filled_after_snapshot)
        if not order.open and not unknown_remaining:
            remaining = Decimal("0")
        return RuntimeOrderState(
            logical_intent_id=order.intent.intent_id,
            exchange_id=order.intent.exchange_id,
            market_id=order.intent.market_id,
            tournament_id=order.intent.tournament_id,
            reserved_exposure=float(remaining),
            open=order.open,
            uncertain=unknown_remaining,
            strategy_id=order.intent.strategy_id,
            signed_quantity=float(order.intent.signed_direction) * float(remaining),
        )

    def _diff_against(self, snapshot: AccountAuthoritativeSnapshot) -> AccountProxyDiff:
        if self.last_authoritative_snapshot is None:
            return AccountProxyDiff((), (), (), False)
        projected = dict(self._positions)
        truth = {
            row.exchange_id: (row.market_id, row.quantity)
            for row in snapshot.positions
            if row.quantity != 0
        }
        diffs: list[tuple[str, str, float]] = []
        for exchange_id in sorted(set(projected).union(truth)):
            projected_row = projected.get(exchange_id)
            truth_row = truth.get(exchange_id)
            projected_market, projected_quantity = (
                ("UNKNOWN", Decimal("0")) if projected_row is None else projected_row
            )
            truth_market, truth_quantity = (
                (projected_market, Decimal("0")) if truth_row is None else truth_row
            )
            if projected_row is None:
                projected_market = truth_market
            if truth_row is None:
                truth_market = projected_market
            delta = float(projected_quantity - truth_quantity)
            if projected_market != truth_market or delta != 0.0:
                diffs.append((exchange_id, projected_market, delta))
        proxy_open = {order_id: order for order_id, order in self._orders.items() if order.open}
        truth_open = {str(order.id): order for order in snapshot.open_orders if order.open}
        market_by_exchange = dict(self._exchange_market_ids)
        market_by_exchange.update({row.exchange_id: row.market_id for row in snapshot.positions})
        mismatched_orders = tuple(
            sorted(
                order_id
                for order_id in set(proxy_open).intersection(truth_open)
                if (
                    proxy_open[order_id].intent.exchange_id != truth_open[order_id].exchange_id
                    or proxy_open[order_id].intent.market_id
                    != market_by_exchange.get(truth_open[order_id].exchange_id)
                    or proxy_open[order_id].intent.signed_direction
                    != _signed_direction(
                        truth_open[order_id].side,
                        truth_open[order_id].action,
                    )
                    or proxy_open[order_id].intent.quantity != abs(truth_open[order_id].quantity)
                )
            )
        )
        cash_diff = (
            None
            if self._cash_balance is None or snapshot.cash_balance is None
            else float(self._cash_balance - snapshot.cash_balance)
        )
        return AccountProxyDiff(
            position_diffs=tuple(diffs),
            open_order_missing=tuple(sorted(set(truth_open).difference(proxy_open))),
            open_order_extra=tuple(sorted(set(proxy_open).difference(truth_open))),
            position_diff_exceeded=any(
                abs(delta) > self.position_diff_threshold
                or (
                    exchange_id in projected
                    and exchange_id in truth
                    and projected[exchange_id][0] != truth[exchange_id][0]
                )
                for exchange_id, _, delta in diffs
            ),
            cash_diff=cash_diff,
            open_order_mismatch=mismatched_orders,
        )

    def _mark_discrepancy(self, reason: str) -> None:
        if self.discrepancy_reason is None:
            self.discrepancy_reason = reason
        logger.warning("SIG account proxy discrepancy reason=%s", reason)

    def _validate_tournament(self, batch: AccountBatchDto) -> None:
        tournament_ids = {
            item.tournament_id
            for collection in (
                batch.fills,
                batch.order_updates,
                batch.settlements,
                batch.refunds,
                batch.collateral_changes,
            )
            for item in collection
            if item.tournament_id is not None
        }
        if tournament_ids and tournament_ids != {self.tournament_id}:
            raise ValueError("account batch contains unexpected tournament scope")


def _signed_direction(side: str, action: str) -> int:
    return 1 if (side == "yes") == (action == "buy") else -1


def _intent_from_submission(event: ExecutionJournalEvent) -> _Intent | None:
    details = _json_object(event.detail_json)
    market_id = details.get("market_id")
    action = details.get("action")
    side = details.get("outcome_side")
    quantity = _decimal(event.quantity)
    if (
        event.exchange_id is None
        or not isinstance(market_id, str)
        or not isinstance(action, str)
        or action not in {"buy", "sell"}
        or not isinstance(side, str)
        or side not in {"yes", "no"}
        or quantity is None
        or quantity <= 0
    ):
        return None
    return _Intent(
        intent_id=event.logical_intent_id or "",
        exchange_id=event.exchange_id,
        market_id=market_id,
        tournament_id=event.tournament_id,
        signed_direction=_signed_direction(side, action),
        cash_direction=-1 if action == "buy" else 1,
        quantity=quantity,
        strategy_id=event.strategy_id,
    )


def _json_object(raw: str | None) -> dict[str, object]:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def _fill_key(
    *,
    order_id: str,
    source_timestamp: str,
    quantity: Decimal,
    price: Decimal | None,
) -> str:
    return "|".join(
        (
            order_id,
            source_timestamp,
            str(quantity.normalize()),
            "" if price is None else str(price.normalize()),
        )
    )
