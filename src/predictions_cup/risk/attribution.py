"""Strategy-exposure attribution from authoritative SIG state plus BUILD-009 journal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import OperationKind
from predictions_cup.risk.capital import ExposureAttribution
from predictions_cup.runtime.models import OrderAction, OutcomeSide
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot
from predictions_cup.sig.trading_dto import FillReadDto, PortfolioFillPageDto


class TournamentFillRest(Protocol):
    async def list_portfolio_fills(
        self,
        *,
        exchange_id: str | None = None,
        market_id: str | None = None,
        tournament_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PortfolioFillPageDto: ...


@dataclass(frozen=True, slots=True)
class StrategyAttributionResult:
    attributions: tuple[ExposureAttribution, ...]
    complete: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _OrderAttribution:
    strategy_id: str
    exchange_id: str
    outcome_side: OutcomeSide
    action: OrderAction


async def fetch_tournament_fills(
    rest: TournamentFillRest,
    *,
    tournament_id: str,
) -> tuple[FillReadDto, ...]:
    cursor: str | None = None
    fills: list[FillReadDto] = []
    seen_ids: set[int] = set()
    while True:
        page = await rest.list_portfolio_fills(
            tournament_id=tournament_id,
            limit=200,
            cursor=cursor,
        )
        if page.coverage is None or not page.coverage.complete:
            raise RuntimeError("SIG tournament fill coverage is incomplete")
        for fill in page.data:
            if fill.id in seen_ids:
                continue
            seen_ids.add(fill.id)
            fills.append(fill)
        if not page.pagination.has_more:
            return tuple(fills)
        cursor = page.pagination.next_cursor
        if cursor is None or not cursor.strip():
            raise RuntimeError("SIG fill pagination lost next cursor")


def attribute_strategy_exposure(
    *,
    journal: ExecutionJournal,
    account: AccountAuthoritativeSnapshot,
    fills: tuple[FillReadDto, ...],
    market_by_exchange: dict[str, str],
) -> StrategyAttributionResult:
    order_map = _journal_order_map(journal)
    signed: dict[tuple[str, str], Decimal] = {}

    for fill in fills:
        if fill.order_id is None:
            continue
        order_attribution = order_map.get(str(fill.order_id))
        if order_attribution is None:
            continue
        if (
            order_attribution.exchange_id != fill.exchange_id
            or order_attribution.outcome_side.value != fill.side
        ):
            return StrategyAttributionResult(
                attributions=(),
                complete=False,
                reason="journal_fill_identity_disagreement",
            )
        delta = _signed_yes_delta(
            side=order_attribution.outcome_side,
            action=order_attribution.action,
            quantity=abs(fill.quantity),
        )
        signed_key = (order_attribution.strategy_id, fill.exchange_id)
        signed[signed_key] = signed.get(signed_key, Decimal("0")) + delta

    complete = True
    reason = "complete"
    items: dict[tuple[str, str, str], Decimal] = {}
    strategies = {strategy for strategy, _ in signed}
    for position in account.positions:
        if position.quantity == 0:
            continue
        attributed = sum(
            (
                signed.get((strategy, position.exchange_id), Decimal("0"))
                for strategy in strategies
            ),
            Decimal("0"),
        )
        if attributed != position.quantity:
            complete = False
            reason = "position_strategy_attribution_incomplete"
        for strategy in strategies:
            quantity = signed.get((strategy, position.exchange_id), Decimal("0"))
            if quantity == 0:
                continue
            item_key = (strategy, position.market_id, "position")
            items[item_key] = items.get(item_key, Decimal("0")) + abs(quantity)

    for open_order in account.open_orders:
        attribution = order_map.get(str(open_order.id))
        if attribution is None:
            complete = False
            reason = "open_order_strategy_attribution_incomplete"
            continue
        market_id = market_by_exchange.get(open_order.exchange_id)
        if market_id is None:
            complete = False
            reason = "open_order_market_attribution_incomplete"
            continue
        item_key = (attribution.strategy_id, market_id, "open_order")
        items[item_key] = (
            items.get(item_key, Decimal("0")) + abs(open_order.quantity)
        )

    attributions = tuple(
        ExposureAttribution(
            market_id=market_id,
            tournament_id=account.tournament_id,
            strategy_id=strategy_id,
            strategy_family="journal",
            exposure=float(amount),
            source=f"build009:{source}",
        )
        for (strategy_id, market_id, source), amount in sorted(items.items())
    )
    return StrategyAttributionResult(
        attributions=attributions,
        complete=complete,
        reason=reason,
    )


def _journal_order_map(journal: ExecutionJournal) -> dict[str, _OrderAttribution]:
    result: dict[str, _OrderAttribution] = {}
    for envelope in journal.envelopes():
        if envelope.operation_kind not in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }:
            continue
        specs = _placement_specs(envelope.payload_json)
        if len(specs) != len(envelope.intent_ids):
            continue
        by_intent = {
            intent_id: spec
            for intent_id, spec in zip(envelope.intent_ids, specs, strict=True)
        }
        strategy_by_intent = {
            event.logical_intent_id: event.strategy_id
            for event in journal.events(envelope.logical_operation_id)
            if event.event_type == "SUBMISSION"
            and event.logical_intent_id is not None
            and event.strategy_id is not None
        }
        for event in journal.events(envelope.logical_operation_id):
            if (
                event.event_type != "ACK"
                or event.exchange_order_id is None
                or event.logical_intent_id is None
            ):
                continue
            spec = by_intent.get(event.logical_intent_id)
            strategy_id = strategy_by_intent.get(event.logical_intent_id)
            if spec is None or strategy_id is None:
                continue
            result[event.exchange_order_id] = _OrderAttribution(
                strategy_id=strategy_id,
                exchange_id=str(spec["exchangeId"]),
                outcome_side=OutcomeSide(str(spec["side"])),
                action=OrderAction(str(spec["action"])),
            )
    return result


def _placement_specs(payload_json: str) -> tuple[dict[str, object], ...]:
    raw = json.loads(payload_json)
    if not isinstance(raw, dict):
        return ()
    if "orders" in raw:
        values = raw["orders"]
    elif "legs" in raw:
        values = raw["legs"]
    else:
        values = [raw]
    if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
        return ()
    return tuple({str(key): value for key, value in item.items()} for item in values)


def _signed_yes_delta(
    *,
    side: OutcomeSide,
    action: OrderAction,
    quantity: Decimal,
) -> Decimal:
    long_yes = (
        (side is OutcomeSide.YES and action is OrderAction.BUY)
        or (side is OutcomeSide.NO and action is OrderAction.SELL)
    )
    return quantity if long_yes else -quantity
