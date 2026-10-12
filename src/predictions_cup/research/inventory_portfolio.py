"""Fail-closed offline SIG inventory economics; never sends, cancels or reserves orders.

Inputs are snapshots captured separately by the existing read-only SIG client.
Signed YES quantity < 0 means long NO; cost_basis is actual cash paid for NO.
Displayed book levels are hypothetical immediate matching capacity, not guaranteed fills.
"""
from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

D = Decimal
TICK = D("0.005")
RACE = re.compile(r"the (Republican|Democratic) Party win the ([A-Z]{2}-\d{2}) House race")


def dec(v: Any) -> Decimal:
    if v is None or isinstance(v, bool):
        raise ValueError("missing numeric input")
    try:
        x = D(str(v))
    except Exception as exc:
        raise ValueError("bad numeric input") from exc
    if not x.is_finite():
        raise ValueError("nonfinite input")
    return x


def timestamp(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("naive timestamp")
    return t.astimezone(UTC)


def party_race(title: str) -> tuple[str, str] | None:
    """Title-based *screening heuristic*, NOT a settlement certificate."""
    m = RACE.search(title)
    return (m.group(1), m.group(2)) if m else None


def close_slice(
    signed_quantity: Decimal,
    quantity: Decimal,
    book: dict[str, Any] | None,
    *,
    exchange_id: str,
    evaluated_at: str,
    max_age_seconds: int = 8,
    extra_slippage_ticks: int = 1,
    fee_bps: Decimal = D("10"),
) -> dict[str, Any]:
    """YES sells hit bids; NO cancellation pays YES asks; strict whole depth."""
    if signed_quantity == 0 or not 0 < quantity <= abs(signed_quantity):
        raise ValueError("invalid close quantity")
    if extra_slippage_ticks < 0 or fee_bps < 0 or max_age_seconds <= 0:
        raise ValueError("invalid stress")
    base = {
        "exchange_id": exchange_id, "requested": str(quantity), "fillable": "0",
        "gross_freed_if_filled": "0", "net_cash_if_filled": None,
        "status": "NOT_READY", "reason": "MISSING_BOOK",
    }
    if book is None:
        return base
    if str(book.get("exchange_id")) != exchange_id:
        base["reason"] = "WRONG_EXCHANGE"
        return base
    try:
        started = timestamp(book["request_started_at"])
        received = timestamp(book["received_at"])
        end = timestamp(evaluated_at)
    except (KeyError, TypeError, ValueError):
        base["reason"] = "MISSING_CLOCK"
        return base
    if started > received or received > end or (end - received).total_seconds() > max_age_seconds:
        base["reason"] = "STALE_NONCAUSAL_BOOK"
        return base
    if book.get("status") != "open":
        base["reason"] = "MARKET_NOT_VERIFIED_OPEN"
        return base
    levels = book.get("bids" if signed_quantity > 0 else "asks", [])
    remaining = quantity
    gross_cash = D("0")
    net_cash = D("0")
    last: Decimal | None = None
    for level in levels:
        price, capacity = dec(level["price"]), dec(level["quantity"])
        if not D("0") <= price <= D("1") or capacity < 0:
            base["reason"] = "INVALID_DEPTH"
            return base
        if last is not None and ((signed_quantity > 0 and price > last)
                                 or (signed_quantity < 0 and price < last)):
            base["reason"] = "UNSORTED_DEPTH"
            return base
        last = price
        amount = min(remaining, capacity)
        unit = price if signed_quantity > 0 else D("1") - price
        gross_cash += amount * unit
        net_cash += amount * max(D("0"), unit - TICK * extra_slippage_ticks)
        remaining -= amount
        if remaining == 0:
            break
    base["fillable"] = str(quantity - remaining)
    if remaining != 0:
        base["reason"] = "INSUFFICIENT_DEPTH"
        return base
    base.update({
        "gross_freed_if_filled": str(quantity),
        "gross_cash_at_book": str(gross_cash),
        "net_cash_if_filled": str(net_cash * (D("1") - fee_bps / D("10000"))),
        "status": "DEPTH_ONLY_NOT_RESERVED",
        "reason": "FILL_LATENCY_AND_ADVERSE_SELECTION_UNMEASURED",
    })
    return base


def build_ledger(snapshot: dict[str, Any], books: dict[str, Any], *,
                 evaluated_at: str, max_age_seconds: int = 8) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in snapshot["positions"]:
        eid = str(p["exchange_id"])
        if eid in seen:
            raise ValueError("duplicate position exchange")
        seen.add(eid)
        signed, cost, mark = dec(p["quantity"]), dec(p["cost_basis"]), dec(p["market_value"])
        if signed == 0 or cost < 0 or mark < 0:
            raise ValueError("invalid long position")
        group = party_race(str(p.get("market_title", "")))
        result = close_slice(
            signed, abs(signed), books.get(eid), exchange_id=eid,
            evaluated_at=evaluated_at, max_age_seconds=max_age_seconds
        )
        out.append({
            "exchange_id": eid, "market_id": str(p["market_id"]),
            "title": p.get("market_title"), "outcome_option": p.get("option"),
            "party_inferred_from_title": group[0] if group else None,
            "race_inferred_from_title": group[1] if group else None,
            "signed_yes_quantity": str(signed), "held_side": "YES" if signed > 0 else "NO",
            "held_side_cost_per_share": str(dec(p["avg_cost"])), "cost_basis": str(cost),
            "market_value": str(mark), "yes_mark": p.get("current_price"),
            "max_loss_from_entry": str(cost), "max_loss_from_current_mark": str(mark),
            "unrealized_pnl": str(dec(p["unrealized_pnl"])),
            "independent_fair_value": None, "settlement_certificate": None,
            "correlation_group": "race:" + group[1] if group else "UNKNOWN",
            "exit": result,
        })
    return out


def paired_review(ledger: list[dict[str, Any]], books: dict[str, Any], *,
                  evaluated_at: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for p in ledger:
        if p["race_inferred_from_title"] and p["party_inferred_from_title"]:
            groups[p["race_inferred_from_title"]][p["party_inferred_from_title"]] = p
    proposals: list[dict[str, Any]] = []
    for race, parties in groups.items():
        if not {"Democratic", "Republican"} <= parties.keys():
            continue
        dem, rep = parties["Democratic"], parties["Republican"]
        qd, qr = dec(dem["signed_yes_quantity"]), dec(rep["signed_yes_quantity"])
        if qd * qr > 0 and qd > 0:
            continue
        q = min(abs(qd), abs(qr))
        exits = [
            close_slice(x, q, books.get(p["exchange_id"]), exchange_id=p["exchange_id"],
                        evaluated_at=evaluated_at)
            for x, p in ((qd, dem), (qr, rep))
        ]
        ready = all(e["net_cash_if_filled"] is not None for e in exits)
        net = sum((dec(e["net_cash_if_filled"]) for e in exits), D("0")) if ready else None
        kind = "NO_NO" if qd < 0 and qr < 0 else "DOUBLED_DIRECTIONAL"
        proposals.append({
            "race_title_heuristic": race, "type": kind, "quantity_each": str(q),
            "gross_exposure_release_if_both_filled": str(q * 2),
            "net_proceeds_if_both_filled": str(net) if net is not None else None,
            "strict_two_party_hold_payout_if_NO_NO": str(q) if kind == "NO_NO" else None,
            "unwind_cost_vs_strict_two_party_hold": str(q - net)
                if kind == "NO_NO" and net is not None else None,
            "directional_backed_party_break_even_probability": str(net / (2 * q))
                if kind == "DOUBLED_DIRECTIONAL" and net is not None else None,
            "independent_probability_available": False,
            "settlement_semantics_verified": False,
            "legs_atomic": False, "executable": False,
            "incremental_expected_portfolio_benefit": None,
            "status": "NOT_RANKABLE_OR_PROMOTABLE",
        })
    proposals.sort(key=lambda p: dec(p["gross_exposure_release_if_both_filled"]), reverse=True)
    return proposals


def tournament_sensitivity(
    ledger: list[dict[str, Any]], *, cash: Decimal,
    race_inputs: dict[str, dict[str, Any]], competitor_inputs: list[dict[str, Any]],
    seed: int = 20261012, paths: int = 5000,
    national_logit_sd: float = .5, state_logit_sd: float = .3,
) -> dict[str, Any]:
    """Hypothetical correlated settlement/podium scenario, NOT calibrated forecast."""
    if not 1 <= paths <= 200_000 or len(competitor_inputs) < 3:
        raise ValueError("explicit scenario and at least three competitors required")
    if national_logit_sd < 0 or state_logit_sd < 0:
        raise ValueError("negative factor uncertainty")
    for item in ledger:
        race = item["race_inferred_from_title"]
        party = item["party_inferred_from_title"]
        if not race or not party or race not in race_inputs:
            raise ValueError("unmapped race/outcome")
        if race_inputs[race].get("settlement_review") != "VERIFIED":
            raise ValueError("unverified settlement rule")
        if not race_inputs[race].get("independent_source"):
            raise ValueError("missing independent probability source")
    rng = random.Random(seed)
    values: list[float] = []
    podium = 0
    for _ in range(paths):
        national = rng.gauss(0, national_logit_sd)
        by_state: dict[str, float] = {}
        wins: dict[str, bool] = {}
        value = float(cash)
        for p in ledger:
            race = p["race_inferred_from_title"]
            assumption = race_inputs[race]
            state = str(assumption["state"])
            if state not in by_state:
                by_state[state] = rng.gauss(0, state_logit_sd)
            if race not in wins:
                chance = float(dec(assumption["p_democratic"]))
                if not 0 < chance < 1:
                    raise ValueError("invalid independent probability")
                logits = math.log(chance / (1 - chance)) + national + by_state[state]
                wins[race] = rng.random() < 1 / (1 + math.exp(-logits))
            true_yes = wins[race] == (p["party_inferred_from_title"] == "Democratic")
            pays = true_yes == (p["held_side"] == "YES")
            if pays:
                value += float(abs(dec(p["signed_yes_quantity"])))
        values.append(value)
        rivals = [max(0., rng.gauss(float(dec(c["mean"])), float(dec(c["sd"]))))
                  for c in competitor_inputs]
        podium += int(sum(r > value for r in rivals) <= 2)
    values.sort()
    return {
        "model": "SYNTHETIC_FACTOR_SENSITIVITY_NOT_VALIDATED_PODIUM_FORECAST",
        "expected_final_value": sum(values) / paths,
        "p05_final_value": values[max(0, math.ceil(paths * .05) - 1)],
        "podium_probability_under_assumptions": podium / paths,
        "seed": seed, "paths": paths, "competitor_assumptions": competitor_inputs,
    }
