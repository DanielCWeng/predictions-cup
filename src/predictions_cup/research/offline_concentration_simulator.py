"""Offline, scenario-conditioned whole-share first-place-threshold stress.

RESEARCH ONLY. Does not claim an empirical win rate, executable edge, or score
rank. Only invented fixtures without any venue, account, or file reads. Source
admission and current signed risk/headroom are external hard gates.
"""
from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

D = Decimal
Z = D("0")
ONE = D("1")


@dataclass(frozen=True)
class Capital:
    settled_cash: Decimal
    reserved_cash: Decimal
    existing_gross_shares: int
    max_gross_shares: int
    verified_current: bool = False
    other_holdings_valued_at_settlement: bool = False


@dataclass(frozen=True)
class Candidate:
    key: str
    # ALL economics below are invented in demonstration, never measurements.
    ask_vwap: Decimal
    bid_exit_vwap: Decimal
    pretrade_mid: Decimal
    latency_slippage: Decimal
    arrived_depth_shares: int
    fill_if_win: Decimal
    fill_if_lose: Decimal
    observed_native_book: bool = False
    original_fill_denominator: bool = False


@dataclass(frozen=True)
class Scenario:
    key: str
    weight: Decimal
    winners: frozenset[str]
    # Only an independently observed, causal opponent score with joint scenario
    # and qualified provenance would support a first-place probability.
    actual_opponent_top_settled: Decimal | None = None


@dataclass(frozen=True)
class Result:
    strategy: str
    allocations: tuple[tuple[str, int], ...]
    cash_reserved_max: Decimal
    gross_increment: int
    paid_spread_per_share: tuple[tuple[str, Decimal], ...]
    roundtrip_spread_per_share: tuple[tuple[str, Decimal], ...]
    expected_net_susq: Decimal
    prob_beat_target: Decimal
    prob_bust: Decimal
    worst_terminal_susq: Decimal
    p05_terminal_susq: Decimal
    first_place_probability: Decimal | None
    realised_net_susq: None = None
    fees_susq_per_share: Decimal = Z


def _dec(v: object) -> Decimal:
    if type(v) is not Decimal or not v.is_finite():
        raise ValueError("NONFINITE_OR_BINARY_FLOAT_INPUT")
    return v


def _valid(c: Capital, candidates: tuple[Candidate, ...],
           scenarios: tuple[Scenario, ...]) -> None:
    if not candidates or len(candidates) > 8 or not scenarios:
        raise ValueError("NO_CANDIDATES_OR_SCENARIOS_OR_TOO_MANY")
    if (c.settled_cash < Z or c.reserved_cash < Z
            or c.reserved_cash > c.settled_cash
            or c.existing_gross_shares < 0
            or c.max_gross_shares < c.existing_gross_shares):
        raise ValueError("CAPITAL_OR_GROSS_INVALID")
    for v in (c.settled_cash, c.reserved_cash):
        _dec(v)
    keys = [x.key for x in candidates]
    if len(keys) != len(set(keys)) or not all(keys):
        raise ValueError("DUPLICATE_CANDIDATE")
    for x in candidates:
        for v in (x.ask_vwap, x.bid_exit_vwap, x.pretrade_mid,
                  x.latency_slippage, x.fill_if_win, x.fill_if_lose):
            _dec(v)
        if (not Z < x.ask_vwap <= ONE or not Z <= x.bid_exit_vwap <= ONE
                or not Z <= x.pretrade_mid <= ONE or x.latency_slippage < Z
                or x.ask_vwap + x.latency_slippage > ONE
                or x.arrived_depth_shares < 0
                or not Z <= x.fill_if_win <= ONE
                or not Z <= x.fill_if_lose <= ONE):
            raise ValueError("INVALID_PRICE_DEPTH_OR_FILL")
    if sum((x.weight for x in scenarios), Z) != ONE:
        raise ValueError("SCENARIOS_DO_NOT_SUM_TO_ONE")
    if len({x.key for x in scenarios}) != len(scenarios):
        raise ValueError("DUPLICATE_SCENARIO")
    for s in scenarios:
        _dec(s.weight)
        if (s.weight < Z or not s.winners <= set(keys)
                or s.actual_opponent_top_settled is not None and
                _dec(s.actual_opponent_top_settled) < Z):
            raise ValueError("INVALID_JOINT_SCENARIO")


def _quantile(distribution: list[tuple[Decimal, Decimal]], q: Decimal) -> Decimal:
    acc = Z
    for wealth, prob in sorted(distribution, key=lambda x: x[0]):
        acc += prob
        if acc >= q:
            return wealth
    raise AssertionError("BAD_DISTRIBUTION")


def simulate(
    capital: Capital,
    candidates: tuple[Candidate, ...],
    scenarios: tuple[Scenario, ...],
    strategy: str,
    share_weights: Mapping[str, Decimal],
    *,
    exceed_susq: Decimal,
    bust_at_or_below_susq: Decimal,
    opponent_scores_independently_verified: bool = False,
) -> Result:
    """Exact scenario/fill enumeration; NOTHING sampled or executed.

    The strategy allocates fractions of available gross shares. For multi-name
    baskets, each candidate's size is independently bounded by its slice,
    available depth and cash; caps are reserved for worst case ALL fill.
    Conditional fills can be more likely on losing than winning outcomes.
    """
    _valid(capital, candidates, scenarios)
    _dec(exceed_susq)
    _dec(bust_at_or_below_susq)
    if not share_weights or any(k not in {x.key for x in candidates}
                               for k in share_weights):
        raise ValueError("UNKNOWN_ALLOCATION")
    if any(_dec(w) < Z for w in share_weights.values()) or sum(
        share_weights.values(), Z
    ) > ONE:
        raise ValueError("OVERALLOCATED_GROSS_SLICES")
    if not capital.other_holdings_valued_at_settlement and capital.existing_gross_shares:
        raise ValueError("EXISTING_INVENTORY_SETTLED_VALUE_UNKNOWN")

    remaining_cash = capital.settled_cash - capital.reserved_cash
    free_gross = capital.max_gross_shares - capital.existing_gross_shares
    chosen: list[tuple[Candidate, int]] = []
    reserved = Z
    for x in candidates:
        portion = share_weights.get(x.key, Z)
        desired = int((D(free_gross) * portion).to_integral_value(rounding=ROUND_FLOOR))
        px = x.ask_vwap + x.latency_slippage  # fees EXACT zero for this competition.
        affordable = int((remaining_cash / px).to_integral_value(rounding=ROUND_FLOOR))
        n = min(desired, x.arrived_depth_shares, affordable)
        if n > 0:
            chosen.append((x, n))
            value = D(n) * px
            reserved += value
            remaining_cash -= value
    if not chosen or sum(n for _, n in chosen) > free_gross:
        raise ValueError("NO_FEASIBLE_EXPOSURE")
    if capital.existing_gross_shares != 0:
        raise ValueError("PENDING_PORTFOLIO_SETTLEMENT_NOT_MODELLED")

    distribution: list[tuple[Decimal, Decimal]] = []
    expected_net = Z
    prob_target = Z
    prob_bust = Z
    first = Z
    has_opponents = (opponent_scores_independently_verified
                     and all(s.actual_opponent_top_settled is not None for s in scenarios))
    for s in scenarios:
        conditional_fills = [x.fill_if_win if x.key in s.winners else x.fill_if_lose
                             for x, _ in chosen]
        # Independent fill Bernoullis CONDITIONAL on joint outcome; no implicit
        # independence of contract results (the scenario probabilities govern it).
        for mask in itertools.product((False, True), repeat=len(chosen)):
            probability = s.weight
            net = Z
            for (x, n), filled, p in zip(chosen, mask, conditional_fills, strict=True):
                probability *= p if filled else ONE - p
                if filled:
                    settle = ONE if x.key in s.winners else Z
                    net += D(n) * (settle - x.ask_vwap - x.latency_slippage)
            terminal = capital.settled_cash + net
            distribution.append((terminal, probability))
            expected_net += probability * net
            if terminal >= exceed_susq:
                prob_target += probability
            if terminal <= bust_at_or_below_susq:
                prob_bust += probability
            if (has_opponents and s.actual_opponent_top_settled is not None
                    and terminal > s.actual_opponent_top_settled):
                first += probability
    assert sum((p for _, p in distribution), Z) == ONE
    return Result(
        strategy,
        tuple((x.key, n) for x, n in chosen),
        reserved,
        sum(n for _, n in chosen),
        tuple((x.key, x.ask_vwap - x.pretrade_mid) for x, _ in chosen),
        tuple((x.key, x.ask_vwap - x.bid_exit_vwap) for x, _ in chosen),
        expected_net,
        prob_target,
        prob_bust,
        min(w for w, _ in distribution),
        _quantile(distribution, D("0.05")),
        first if has_opponents else None,
    )


def demo() -> tuple[Capital, tuple[Candidate, ...], tuple[Scenario, ...]]:
    """Invented stress fixture only; names never correspond to SIG contracts."""
    capital = Capital(D("100000"), Z, 0, 100000)
    candidates = (
        Candidate("INVENTED_A", D("0.70"), D("0.68"), D("0.69"),
                  D("0.015"), 100000, D("0.40"), D("0.90")),
        Candidate("INVENTED_B", D("0.70"), D("0.68"), D("0.69"),
                  D("0.015"), 100000, D("0.40"), D("0.90")),
    )
    # Symmetric 55% marginal settlement prob, correlated through a joint table.
    scenarios = (
        Scenario("both_win", D("0.30"), frozenset({"INVENTED_A", "INVENTED_B"})),
        Scenario("only_A", D("0.25"), frozenset({"INVENTED_A"})),
        Scenario("only_B", D("0.25"), frozenset({"INVENTED_B"})),
        Scenario("both_lose", D("0.20"), frozenset()),
    )
    return capital, candidates, scenarios


def report_demo() -> dict[str, object]:
    c, assets, s = demo()
    params = dict(exceed_susq=D("125000"), bust_at_or_below_susq=D("40000"))
    res = [
        simulate(c, assets, s, "CONCENTRATED_A", {"INVENTED_A": ONE}, **params),
        simulate(c, assets, s, "DIVERSIFIED_50_50",
                 {"INVENTED_A": D("0.5"), "INVENTED_B": D("0.5")}, **params),
    ]
    return {
        "status": "SYNTHETIC_SENSITIVITY_ONLY_REAL_EXECUTABLE_CANDIDATES_NOT_AVAILABLE",
        "measured_original_paired_executable_episodes": 0,
        "measured_context": {
            "oct12_cash_susq": "34458.045", "oct12_absolute_gross_shares": 100000,
            "oct12_old_gross_cap_shares": 100000,
            "current_cash_and_headroom": None,
            "original_native_whole_l2_and_fills": None,
            "frozen_005i_reversal_accuracy_is_not_pnl": "67.26057102%",
        },
        "assumed_fixture": {
            "initial_cash": "100000", "gross_limit_shares": 100000,
            "price_ask": "0.70", "exit_bid": "0.68", "mid": "0.69",
            "latency_slip_per_share": "0.015", "SIG_fee": "0",
            "fill_probability_on_winner": "0.40",
            "fill_probability_on_loser": "0.90",
            "settle_probability_each": "0.55", "threshold_target_cash": "125000",
            "bust_threshold_cash": "40000",
        },
        "strategies": [{
            "name": r.strategy, "allocations": dict(r.allocations),
            "max_paid_cash": str(r.cash_reserved_max), "gross_shares": r.gross_increment,
            "paid_spread_vs_mid_each": {k: str(v) for k, v in r.paid_spread_per_share},
            "quoted_entry_to_exit_spread_each": {
                k: str(v) for k, v in r.roundtrip_spread_per_share
            },
            "expected_net_susq_ASSUMED": str(r.expected_net_susq),
            "P_terminal_at_least_125k_ASSUMED": str(r.prob_beat_target),
            "P_terminal_at_most_40k_ASSUMED": str(r.prob_bust),
            "worst_terminal_susq_ASSUMED": str(r.worst_terminal_susq),
            "p05_terminal_susq_ASSUMED": str(r.p05_terminal_susq),
            "P_actual_first": (
                None if r.first_place_probability is None
                else str(r.first_place_probability)
            ),
            "realised_net_susq": None,
        } for r in res],
        "source_admitted": False, "paper_admitted": False, "live_authorised": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="invented scenarios only")
    args = ap.parse_args()
    if not args.demo:
        ap.error("No admitted market source inputs; --demo runs invented scenarios only")
    print(json.dumps(report_demo(), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
