"""Fail-closed hold/close/rotate evaluation with no trading or price-feed authority.

A positive conditional score never authorises execution. Missing independent
fair value, settlement proof, contemporaneous depth or economic redeployment
evidence must produce NOT_RANKABLE, never fabricated positive alpha.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from predictions_cup.research.inventory_portfolio import close_slice, dec, timestamp

ZERO = Decimal("0")
ONE = Decimal("1")
LOCK = "2026-11-04T17:00:00Z"


def action_economics(
    position: dict[str, Any],
    book: dict[str, Any] | None,
    *,
    forecast: dict[str, Any] | None,
    settlement: dict[str, Any] | None,
    redeployment: dict[str, Any] | None,
    evaluated_at: str,
    quantity: Decimal | None = None,
    lock_at: str = LOCK,
) -> dict[str, Any]:
    """Compare the incremental final-value economics of a single held-side exit.

    Expected final-value improvement is measured relative to holding the same
    quantity through settlement. It is *conditional* on an immediate complete
    fill at the observed and conservatively stressed book prices.
    """
    q_signed = dec(position["quantity"])
    if q_signed == ZERO:
        raise ValueError("cannot sell a nonexistent position")
    qty = dec(quantity) if quantity is not None else abs(q_signed)
    if not ZERO < qty <= abs(q_signed):
        raise ValueError("invalid close slice")
    key = str(position["exchange_id"])
    out: dict[str, Any] = {
        "exchange_id": key,
        "quantity_closed": str(qty),
        "gross_shares_freed_if_filled": str(qty),
        "capital_seconds_released_if_filled": "0",
        "cash_recovered_if_filled": None,
        "mark_to_executable_slippage": None,
        "realised_pnl_if_filled": None,
        "hold_expected_payout_low": None,
        "hold_expected_payout_high": None,
        "incremental_final_ev_base": None,
        "incremental_final_ev_robust_low": None,
        "incremental_final_ev_optimistic_high": None,
        "worst_incremental_settlement_loss": None,
        "net_capacity_after_rotation": None,
        "status": "NOT_RANKABLE",
        "blockers": [],
        "execution_is_guaranteed": False,
        "execution_authority": False,
    }
    blockers: list[str] = []
    if timestamp(evaluated_at) >= timestamp(lock_at):
        blockers.append("AFTER_TOURNAMENT_LOCK")
    else:
        seconds = (timestamp(lock_at) - timestamp(evaluated_at)).total_seconds()
        out["capital_seconds_released_if_filled"] = str(qty * dec(seconds))

    exit_ = close_slice(
        q_signed, qty, book,
        exchange_id=key, evaluated_at=evaluated_at,
    )
    out["exit_depth_status"] = exit_["status"]
    if exit_["net_cash_if_filled"] is None:
        blockers.append("NO_FRESH_COMPLETE_EXECUTABLE_DEPTH")
    else:
        cash = dec(exit_["net_cash_if_filled"])
        out["cash_recovered_if_filled"] = str(cash)
        portion = qty / abs(q_signed)
        mark = dec(position["market_value"]) * portion
        cost = dec(position["cost_basis"]) * portion
        out["mark_to_executable_slippage"] = str(cash - mark)
        out["realised_pnl_if_filled"] = str(cash - cost)

    if settlement is None:
        blockers.append("MISSING_CANONICAL_SETTLEMENT_CERTIFICATE")
    elif (
        settlement.get("review_status") != "VERIFIED"
        or str(settlement.get("exchange_id")) != key
        or not settlement.get("rule_signature")
    ):
        blockers.append("INVALID_SETTLEMENT_CERTIFICATE")

    lo: Decimal | None = None
    hi: Decimal | None = None
    if forecast is None:
        blockers.append("NO_INDEPENDENT_FAIR_VALUE")
    else:
        try:
            issued = timestamp(str(forecast["published_at"]))
            expiry = timestamp(str(forecast["valid_until"]))
            lo, hi = dec(forecast["yes_lower"]), dec(forecast["yes_upper"])
            independent = forecast.get("independent_of_sig_prices") is True
            source = bool(forecast.get("source"))
            if (
                issued > timestamp(evaluated_at)
                or expiry < timestamp(evaluated_at)
                or not ZERO <= lo <= hi <= ONE
                or not independent or not source
            ):
                blockers.append("INVALID_OR_STALE_INDEPENDENT_FAIR_VALUE")
                lo, hi = None, None
        except (KeyError, TypeError, ValueError):
            blockers.append("INVALID_OR_STALE_INDEPENDENT_FAIR_VALUE")
            lo, hi = None, None
    if lo is not None and hi is not None:
        payout_lo = lo if q_signed > ZERO else ONE - hi
        payout_hi = hi if q_signed > ZERO else ONE - lo
        out["hold_expected_payout_low"] = str(qty * payout_lo)
        out["hold_expected_payout_high"] = str(qty * payout_hi)

    reinvest_base = ZERO
    reinvest_lower = ZERO
    reinvest_upper = ZERO
    reinvest_worst_loss = ZERO
    gross_occupied = ZERO
    if redeployment is not None:
        try:
            reinvest_base = dec(redeployment["net_expected_profit"])
            reinvest_lower = dec(redeployment["net_profit_lower"])
            reinvest_upper = dec(redeployment["net_profit_upper"])
            reinvest_worst_loss = dec(redeployment["worst_loss"])
            gross_occupied = dec(redeployment["gross_shares_used"])
            expires = timestamp(str(redeployment["valid_until"]))
            if (
                not redeployment.get("prospectively_validated")
                or not redeployment.get("net_of_all_costs")
                or not redeployment.get("evidence_id")
                or int(redeployment["heldout_opportunities"]) < 1
                or expires < timestamp(evaluated_at)
                or not reinvest_lower <= reinvest_base <= reinvest_upper
                or reinvest_worst_loss < ZERO
                or not ZERO <= gross_occupied <= qty
            ):
                blockers.append("UNVERIFIED_REDEPLOYMENT_ECONOMICS")
        except (KeyError, TypeError, ValueError):
            blockers.append("UNVERIFIED_REDEPLOYMENT_ECONOMICS")
    if not blockers:
        assert lo is not None and hi is not None
        cash = dec(out["cash_recovered_if_filled"])
        hold_lo = dec(out["hold_expected_payout_low"])
        hold_hi = dec(out["hold_expected_payout_high"])
        out["incremental_final_ev_base"] = str(
            cash + reinvest_base - (hold_lo + hold_hi) / 2
        )
        out["incremental_final_ev_robust_low"] = str(
            cash + reinvest_lower - hold_hi
        )
        out["incremental_final_ev_optimistic_high"] = str(
            cash + reinvest_upper - hold_lo
        )
        out["worst_incremental_settlement_loss"] = str(
            min(ZERO, cash - qty - reinvest_worst_loss)
        )
        out["net_capacity_after_rotation"] = str(qty - gross_occupied)
        out["status"] = "ECONOMIC_SENSITIVITY_ONLY_INDEPENDENT_REVIEW_REQUIRED"
    out["blockers"] = sorted(set(blockers))
    return out


def passive_exit_sensitivity(
    signed_quantity: Decimal,
    quantity: Decimal,
    *,
    target_yes_price: Decimal,
    independent_fill_probability: Decimal | None,
    conditional_yes_probability_if_filled: Decimal | None,
) -> dict[str, Any]:
    """Conditional EV, never treat a resting limit as currently executable."""
    p = dec(target_yes_price)
    q = dec(quantity)
    s = dec(signed_quantity)
    if not ZERO < q <= abs(s) or not ZERO <= p <= ONE:
        raise ValueError("invalid passive inventory/price")
    target_cash = q * (p if s > ZERO else ONE - p)
    result: dict[str, Any] = {
        "status": "UNFILLED_PASSIVE_SCENARIO_ONLY",
        "gross_release_if_filled": str(q),
        "gross_release_now": "0",
        "target_cash_if_filled_before_costs": str(target_cash),
        "conditional_expected_gain": None,
        "reason": "NO_INDEPENDENT_FILL_OR_SELECTION_MODEL",
    }
    if independent_fill_probability is None or conditional_yes_probability_if_filled is None:
        return result
    pf = dec(independent_fill_probability)
    py = dec(conditional_yes_probability_if_filled)
    if not ZERO <= pf <= ONE or not ZERO <= py <= ONE:
        raise ValueError("invalid prospective fill model")
    hold_cond = q * (py if s > ZERO else ONE - py)
    result["conditional_expected_gain"] = str(pf * (target_cash - hold_cond))
    result["reason"] = "MODEL_ONLY_EXCLUDES_FEES_QUEUE_DRIFT_AND_CAPITAL_FRICTION"
    return result


def rank_by_incremental_final_value(
    actions: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Never pad a top-ten EV table with unscoreable gross-ranked candidates."""
    rankable = [
        a for a in actions
        if a.get("status") == "ECONOMIC_SENSITIVITY_ONLY_INDEPENDENT_REVIEW_REQUIRED"
        and a.get("incremental_final_ev_base") is not None
    ]
    rankable.sort(key=lambda a: dec(a["incremental_final_ev_base"]), reverse=True)
    blocked = [a for a in actions if a not in rankable]
    return {"top_ten_ev_review_only": rankable[:10], "unrankable": blocked}
