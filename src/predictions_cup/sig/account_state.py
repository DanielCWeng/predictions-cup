"""Best-effort account Realtime state with fail-closed REST recovery boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import ValidationError

from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.runtime.models import RuntimeOrderState, RuntimePortfolio, RuntimePosition
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.realtime_models import AccountBatchDto


class AccountTrustTransition(StrEnum):
    INITIAL = "INITIAL"
    TRUSTED_AFTER_RECONCILIATION = "TRUSTED_AFTER_RECONCILIATION"
    UNTRUSTED_REVISION_GAP = "UNTRUSTED_REVISION_GAP"
    UNTRUSTED_RECONNECT = "UNTRUSTED_RECONNECT"
    UNTRUSTED_TOKEN_REFRESH = "UNTRUSTED_TOKEN_REFRESH"
    UNTRUSTED_SOCKET_ERROR = "UNTRUSTED_SOCKET_ERROR"
    UNTRUSTED_MALFORMED_PAYLOAD = "UNTRUSTED_MALFORMED_PAYLOAD"
    UNTRUSTED_UNKNOWN_OPEN_ORDER = "UNTRUSTED_UNKNOWN_OPEN_ORDER"
    UNTRUSTED_FILL_REQUIRES_RECONCILIATION = "UNTRUSTED_FILL_REQUIRES_RECONCILIATION"
    UNTRUSTED_RESYNC_ACTIVITY = "UNTRUSTED_RESYNC_ACTIVITY"


@dataclass(frozen=True, slots=True)
class AccountBatchApplyResult:
    accepted: bool
    duplicate: bool
    requires_reconciliation: bool
    transition: AccountTrustTransition


class AccountRealtimeStateEngine:
    """Realtime accelerates state; authoritative reconciliation restores trust."""

    def __init__(
        self,
        *,
        tournament_id: str,
        reservations: ExecutionReservationBook | None = None,
    ) -> None:
        if not tournament_id.strip():
            raise ValueError("tournament_id must not be blank")
        self.tournament_id = tournament_id
        self._reservations = reservations
        self.trusted = False
        self.transition = AccountTrustTransition.INITIAL
        self.last_accepted_revision: int | None = None
        self.last_realtime_observed_at: datetime | None = None
        self._positions: dict[str, tuple[str, Decimal]] = {}
        self._orders: dict[int, RuntimeOrderState] = {}
        self._outstanding_advance: dict[str, Decimal] = {}

    def mark_untrusted(self, transition: AccountTrustTransition) -> None:
        self.trusted = False
        self.transition = transition
        self.last_accepted_revision = None

    def apply_authoritative(
        self,
        snapshot: AccountAuthoritativeSnapshot,
        *,
        mark_trusted: bool = True,
    ) -> None:
        if snapshot.tournament_id != self.tournament_id:
            raise ValueError("authoritative snapshot tournament mismatch")
        self._positions = {
            position.exchange_id: (position.market_id, position.quantity)
            for position in snapshot.positions
            if position.quantity != 0
        }
        market_by_exchange = {
            position.exchange_id: position.market_id for position in snapshot.positions
        }
        self._orders = {
            order.id: RuntimeOrderState(
                logical_intent_id=f"sig-order-{order.id}",
                exchange_id=order.exchange_id,
                market_id=market_by_exchange.get(order.exchange_id, "UNKNOWN"),
                tournament_id=self.tournament_id,
                reserved_exposure=float(abs(order.quantity)),
                open=order.open,
                uncertain=False,
            )
            for order in snapshot.open_orders
        }
        # The authoritative snapshot now contains every position/open order that
        # can economically overlap with local in-flight reservations. Clear the
        # overlay before trust is restored so Risk never sees a gap between them.
        if self._reservations is not None:
            self._reservations.clear_after_authoritative_reconciliation()
        self.trusted = False
        self.last_accepted_revision = None
        if mark_trusted:
            self.mark_trusted_after_reconciliation()

    def mark_trusted_after_reconciliation(self) -> None:
        self.trusted = True
        self.transition = AccountTrustTransition.TRUSTED_AFTER_RECONCILIATION
        self.last_accepted_revision = None

    def handle_raw_batch(
        self,
        payload: object,
        *,
        observed_at: datetime,
    ) -> AccountBatchApplyResult:
        self.last_realtime_observed_at = observed_at
        try:
            batch = AccountBatchDto.model_validate(payload)
            self._validate_tournament(batch)
        except (ValidationError, ValueError):
            self.mark_untrusted(AccountTrustTransition.UNTRUSTED_MALFORMED_PAYLOAD)
            return AccountBatchApplyResult(
                accepted=False,
                duplicate=False,
                requires_reconciliation=True,
                transition=self.transition,
            )

        delivery = batch.delivery
        if self.last_accepted_revision == delivery.revision:
            return AccountBatchApplyResult(
                accepted=False,
                duplicate=True,
                requires_reconciliation=False,
                transition=self.transition,
            )

        if (
            self.last_accepted_revision is not None
            and delivery.previous_revision != self.last_accepted_revision
        ):
            self.mark_untrusted(AccountTrustTransition.UNTRUSTED_REVISION_GAP)
            return AccountBatchApplyResult(
                accepted=False,
                duplicate=False,
                requires_reconciliation=True,
                transition=self.transition,
            )

        # account_batch fill records do not contain action/direction. Applying
        # quantity as a signed delta can invert sell economics, and delayed batches
        # can double-apply fills already present in the last REST snapshot. A valid
        # fill therefore invalidates local exposure and forces authoritative truth.
        if batch.fills:
            self.mark_untrusted(
                AccountTrustTransition.UNTRUSTED_FILL_REQUIRES_RECONCILIATION
            )
            return AccountBatchApplyResult(
                accepted=False,
                duplicate=False,
                requires_reconciliation=True,
                transition=self.transition,
            )

        for update in batch.order_updates:
            existing = self._orders.get(update.order_id)
            if update.open:
                if existing is None:
                    self.mark_untrusted(
                        AccountTrustTransition.UNTRUSTED_UNKNOWN_OPEN_ORDER
                    )
                    return AccountBatchApplyResult(
                        accepted=False,
                        duplicate=False,
                        requires_reconciliation=True,
                        transition=self.transition,
                    )
            else:
                self._orders.pop(update.order_id, None)

        for settlement in batch.settlements:
            self._positions.pop(settlement.exchange_id, None)
        for refund in batch.refunds:
            self._positions.pop(refund.exchange_id, None)
        for collateral in batch.collateral_changes:
            self._outstanding_advance[collateral.component_id] = (
                collateral.outstanding_advance_after
            )

        self.last_accepted_revision = delivery.revision
        return AccountBatchApplyResult(
            accepted=True,
            duplicate=False,
            requires_reconciliation=False,
            transition=self.transition,
        )

    def runtime_portfolio(self) -> RuntimePortfolio:
        return RuntimePortfolio(
            positions=tuple(
                RuntimePosition(
                    exchange_id=exchange_id,
                    market_id=market_id,
                    tournament_id=self.tournament_id,
                    gross_exposure=float(abs(quantity)),
                    signed_quantity=float(quantity),
                )
                for exchange_id, (market_id, quantity) in self._positions.items()
                if quantity != 0
            ),
            orders=tuple(self._orders.values()),
            account_trusted=self.trusted,
        )

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
