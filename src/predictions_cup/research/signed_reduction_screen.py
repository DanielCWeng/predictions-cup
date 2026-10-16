"""Research-only signed-inventory reduction certificate.

This module DOES NOT authorise a trade, place an order, amend LIVE risk
limits, or alter the actual RISK-002 evaluator. It computes a mathematical
precondition for independently reviewed reduction-only execution design.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SignedInventoryEvidence:
    """Synthetic or independently authenticated full-exchange inventory.

    All evidence and provenance checks default to fail-closed. A persisted
    absolute by-market risk bucket alone cannot attest signed quantities.
    """

    signed_by_exchange: Mapping[str, int]
    account_gross: int
    account_net: int
    open_order_exposure: int = 0
    uncertain_order_exposure: int = 0
    reservation_exposure: int = 0
    account_trusted: bool = False
    reconciliation_complete: bool = False
    signed_positions_verified: bool = False
    venue_netting_verified: bool = False
    market_open_verified: bool = False
    global_halt_clear: bool = False
    account_age_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ConditionalReduction:
    """No execution permission is conveyed, regardless of the result."""

    mathematically_reducing: bool
    reason: str
    signed_delta: int | None = None
    projected_gross: int | None = None

    @property
    def execution_authorised(self) -> bool:
        """Always false: independent economic, venue and safety gates remain."""
        return False


def _reject(reason: str) -> ConditionalReduction:
    return ConditionalReduction(mathematically_reducing=False, reason=reason)


def screen_signed_reduction(
    evidence: SignedInventoryEvidence,
    *,
    exchange_id: str,
    outcome_side: str,
    action: str,
    quantity: int,
    gross_limit: int,
    max_account_age_seconds: float,
) -> ConditionalReduction:
    """Calculate only whether ONE intent strictly reduces verified gross.

    This does not model order arrival, partial/duplicate fills, cancellation,
    changing portfolios or settlement. All those require independent review.
    It must NEVER be called by LIVE execution to replace evaluate_risk.
    """
    if not isinstance(gross_limit, int) or gross_limit <= 0:
        return _reject("invalid_gross_limit")
    if (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or quantity <= 0
    ):
        return _reject("invalid_whole_share_quantity")
    if not isinstance(exchange_id, str) or not exchange_id:
        return _reject("unknown_exchange")
    if action not in {"BUY", "SELL"} or outcome_side not in {"YES", "NO"}:
        return _reject("unknown_venue_action_or_outcome")

    if not evidence.account_trusted or not evidence.reconciliation_complete:
        return _reject("account_not_authoritatively_reconciled")
    if not evidence.signed_positions_verified:
        return _reject("signed_position_provenance_missing")
    if not evidence.venue_netting_verified:
        return _reject("venue_netting_semantics_unverified")
    if not evidence.market_open_verified or not evidence.global_halt_clear:
        return _reject("market_or_global_halt_not_cleared")
    if (
        evidence.account_age_seconds is None
        or not math.isfinite(evidence.account_age_seconds)
        or evidence.account_age_seconds < 0
        or not math.isfinite(max_account_age_seconds)
        or max_account_age_seconds <= 0
        or evidence.account_age_seconds > max_account_age_seconds
    ):
        return _reject("account_observation_missing_or_stale")

    amounts = (
        evidence.open_order_exposure,
        evidence.uncertain_order_exposure,
        evidence.reservation_exposure,
    )
    if any(not isinstance(a, int) or isinstance(a, bool) or a < 0 for a in amounts):
        return _reject("untrusted_order_reservation_accounting")
    if any(amounts):
        return _reject("unresolved_order_or_reservation")

    signed = evidence.signed_by_exchange
    if not signed or any(
        not isinstance(v, int) or isinstance(v, bool) or v == 0
        for v in signed.values()
    ):
        return _reject("invalid_signed_position_ledger")
    if (
        not isinstance(evidence.account_gross, int)
        or not isinstance(evidence.account_net, int)
        or isinstance(evidence.account_gross, bool)
        or isinstance(evidence.account_net, bool)
        or sum(abs(v) for v in signed.values()) != evidence.account_gross
        or sum(signed.values()) != evidence.account_net
    ):
        return _reject("signed_ledger_not_reconciled_to_gross_and_net")
    if evidence.account_gross > gross_limit:
        return _reject("portfolio_already_above_cap")

    position = signed.get(exchange_id)
    if position is None:
        return _reject("no_authoritative_existing_position")

    # Canonical signed YES: YES BUY / NO SELL add YES; YES SELL / NO BUY
    # subtract YES. No interpretation based on the venue's 'option' label.
    increase_yes = (outcome_side == "YES" and action == "BUY") or (
        outcome_side == "NO" and action == "SELL"
    )
    delta = quantity if increase_yes else -quantity
    if position * delta >= 0:
        return _reject("intent_not_opposite_to_existing_inventory")
    if abs(delta) > abs(position):
        return _reject("possible_overshoot_and_side_flip")

    projected = evidence.account_gross - abs(position) + abs(position + delta)
    if projected >= evidence.account_gross or projected > gross_limit:
        return _reject("no_strict_gross_reduction")

    return ConditionalReduction(
        mathematically_reducing=True,
        reason="CONDITIONAL_MATH_ONLY_NOT_EXECUTION_AUTHORITY",
        signed_delta=delta,
        projected_gross=projected,
    )
