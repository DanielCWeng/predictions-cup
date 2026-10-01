"""MAKE quote-state recovery from BUILD-009 journal + authoritative account truth."""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.models import ExecutionEnvelope, LifecycleState, OperationKind
from predictions_cup.maker.contracts import QuoteSide
from predictions_cup.maker.lifecycle import QuoteRegistry
from predictions_cup.runtime.models import limit_price_to_ticks
from predictions_cup.sig.account_reconciliation import AccountAuthoritativeSnapshot


def maker_unresolved_envelopes(
    journal: ExecutionJournal,
    *,
    strategy_id: str = "make-direct-pm",
) -> tuple[ExecutionEnvelope, ...]:
    """Return unresolved operations proven by journal audit to belong to MAKE."""
    terminal = {
        LifecycleState.FILLED,
        LifecycleState.CANCELLED,
        LifecycleState.RECONCILED,
        LifecycleState.REJECTED,
    }
    return tuple(
        envelope
        for envelope in journal.envelopes_for_strategy(strategy_id)
        if envelope.lifecycle_state not in terminal
    )


def maker_placement_envelopes(
    journal: ExecutionJournal,
    *,
    strategy_id: str = "make-direct-pm",
) -> tuple[ExecutionEnvelope, ...]:
    """Return all durable MAKE placement envelopes, including reconciled history."""
    return tuple(
        envelope
        for envelope in journal.envelopes_for_strategy(strategy_id)
        if envelope.operation_kind
        in {
            OperationKind.SINGLE_PLACEMENT,
            OperationKind.BEST_EFFORT_BATCH,
            OperationKind.ATOMIC_MULTI_LEG,
        }
    )


def reconcile_maker_quote_registry(
    *,
    journal: ExecutionJournal,
    authoritative: AccountAuthoritativeSnapshot,
    quotes: QuoteRegistry,
    observed_monotonic_ns: int,
    envelopes: Iterable[ExecutionEnvelope] | None = None,
    strategy_id: str = "make-direct-pm",
) -> None:
    """Rebuild/clear maker quote state using authoritative open orders.

    Only operations with journal strategy attribution to MAKE are touched.
    Other strategies' tournament orders are deliberately ignored.
    """
    if observed_monotonic_ns < 0:
        raise ValueError("observed_monotonic_ns must be non-negative")
    if envelopes is None:
        maker_envelopes = maker_placement_envelopes(
            journal,
            strategy_id=strategy_id,
        )
    else:
        maker_envelopes = tuple(
            envelope
            for envelope in envelopes
            if _is_maker_operation(journal, envelope, strategy_id=strategy_id)
            and envelope.operation_kind
            in {
                OperationKind.SINGLE_PLACEMENT,
                OperationKind.BEST_EFFORT_BATCH,
                OperationKind.ATOMIC_MULTI_LEG,
            }
        )
    maker_operation_ids = {envelope.logical_operation_id for envelope in maker_envelopes}

    # Map durable per-intent ACK identity to its original payload leg.
    acked: dict[int, tuple[ExecutionEnvelope, dict[str, object]]] = {}
    for envelope in maker_envelopes:
        legs = _payload_legs(envelope)
        by_intent = dict(zip(envelope.intent_ids, legs, strict=True))
        for event in journal.events(envelope.logical_operation_id):
            if (
                event.event_type != "ACK"
                or event.logical_intent_id is None
                or event.exchange_order_id is None
            ):
                continue
            leg = by_intent.get(event.logical_intent_id)
            if leg is None:
                continue
            try:
                order_id = int(event.exchange_order_id)
            except ValueError:
                continue
            if order_id > 0:
                acked[order_id] = (envelope, leg)

    authoritative_open = {order.id: order for order in authoritative.open_orders if order.open}
    maker_open_ids = set(acked).intersection(authoritative_open)

    # Clear only locally known MAKE quotes proven absent from authoritative open
    # orders. Unrelated strategy quote state is never modified here.
    for exchange_id in quotes.exchange_ids:
        state = quotes.state(exchange_id)
        for side, active in (
            (QuoteSide.BID, state.bid),
            (QuoteSide.ASK, state.ask),
        ):
            if active is None:
                continue
            if active.logical_operation_id not in maker_operation_ids:
                continue
            if active.exchange_order_id is None or active.exchange_order_id not in maker_open_ids:
                quotes.clear_side(
                    exchange_id=exchange_id,
                    side=side,
                    observed_monotonic_ns=observed_monotonic_ns,
                )

    for order_id in sorted(maker_open_ids):
        envelope, leg = acked[order_id]
        order = authoritative_open[order_id]
        action = str(leg.get("action", ""))
        if action == "buy":
            side = QuoteSide.BID
        elif action == "sell":
            side = QuoteSide.ASK
        else:
            raise RuntimeError("MAKE journal contains unsupported quote action")
        if order.price_limit is None:
            raise RuntimeError("MAKE authoritative open order is not a limit order")
        # SIG canonicalises a flat/short YES sell into its complement
        # (`sell yes q@p` == `buy no q@(1-p)`), so a MAKE ask can rest as NO/BUY.
        if order.side == "yes" and order.action == action:
            yes_price = order.price_limit
        elif order.side == "no" and action == "sell" and order.action == "buy":
            yes_price = Decimal(1) - order.price_limit
        else:
            raise RuntimeError("MAKE authoritative open order does not match its journal quote leg")
        price_ticks = limit_price_to_ticks(yes_price)
        quantity = _whole_quantity(order.quantity)
        quotes.apply_authoritative(
            exchange_id=order.exchange_id,
            side=side,
            price_ticks=price_ticks,
            size=quantity,
            # OrderReadDto does not expose remaining quantity. Using original
            # quantity can under-replenish after a partial fill but never creates
            # extra economic exposure; account/inventory risk remains authoritative.
            remaining_size=quantity,
            logical_operation_id=envelope.logical_operation_id,
            exchange_order_id=order_id,
            lifecycle_state=LifecycleState.OPEN,
            observed_monotonic_ns=observed_monotonic_ns,
        )


def _is_maker_operation(
    journal: ExecutionJournal,
    envelope: ExecutionEnvelope,
    *,
    strategy_id: str,
) -> bool:
    return any(
        event.event_type == "SUBMISSION" and event.strategy_id == strategy_id
        for event in journal.events(envelope.logical_operation_id)
    )


def _payload_legs(envelope: ExecutionEnvelope) -> tuple[dict[str, object], ...]:
    raw = json.loads(envelope.payload_json)
    if not isinstance(raw, dict):
        raise RuntimeError("MAKE journal payload is not an object")
    collection: object
    if envelope.operation_kind is OperationKind.BEST_EFFORT_BATCH:
        collection = raw.get("orders")
    elif envelope.operation_kind is OperationKind.ATOMIC_MULTI_LEG:
        collection = raw.get("legs")
    else:
        collection = (raw,)

    if isinstance(collection, tuple):
        legs = collection
    elif isinstance(collection, list):
        legs = tuple(collection)
    else:
        raise RuntimeError("MAKE journal payload has no placement legs")
    if len(legs) != len(envelope.intent_ids):
        raise RuntimeError("MAKE journal payload/intent cardinality mismatch")
    if any(not isinstance(leg, dict) for leg in legs):
        raise RuntimeError("MAKE journal placement leg is not an object")
    return tuple(dict(leg) for leg in legs)


def _whole_quantity(value: Decimal) -> int:
    absolute = abs(value)
    integral = absolute.to_integral_value()
    if absolute != integral or integral <= 0:
        raise RuntimeError("MAKE authoritative order quantity is not a positive integer")
    return int(integral)
