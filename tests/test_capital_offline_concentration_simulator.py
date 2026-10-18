"""Invented fixture only; prices, exposures and outcomes are NOT market data."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from predictions_cup.research import offline_concentration_simulator as m


def test_threshold_tail_tradeoff_and_probability_conservation() -> None:
    p = m.report_demo()
    a, b = p["strategies"]
    assert p["measured_original_paired_executable_episodes"] == 0
    assert a["gross_shares"] == b["gross_shares"] == 100000
    assert a["max_paid_cash"] == b["max_paid_cash"] == "71500.000"
    assert D(a["P_terminal_at_least_125k_ASSUMED"]) > D(
        b["P_terminal_at_least_125k_ASSUMED"]
    )
    assert D(a["P_terminal_at_most_40k_ASSUMED"]) > D(
        b["P_terminal_at_most_40k_ASSUMED"]
    )
    assert a["P_actual_first"] is None and b["P_actual_first"] is None
    assert a["realised_net_susq"] is None
    assert p["source_admitted"] is False


def test_zero_fee_and_marked_exit_not_confused_with_settlement() -> None:
    c, assets, scenarios = m.demo()
    r = m.simulate(
        c, assets, scenarios, "A", {"INVENTED_A": D("1")},
        exceed_susq=D("125000"), bust_at_or_below_susq=D("40000"),
    )
    assert r.fees_susq_per_share == 0
    assert r.paid_spread_per_share == (("INVENTED_A", D("0.01")),)
    assert r.roundtrip_spread_per_share == (("INVENTED_A", D("0.02")),)
    assert r.realised_net_susq is None


def test_held_gross_cap_prevents_hypothetical_new_risk() -> None:
    c, a, s = m.demo()
    old = replace(c, settled_cash=D("34458.045"), existing_gross_shares=100000)
    with pytest.raises(ValueError, match="EXISTING_INVENTORY_SETTLED_VALUE_UNKNOWN"):
        m.simulate(old, a, s, "risk_denied", {"INVENTED_A": D("1")},
                   exceed_susq=D("125000"), bust_at_or_below_susq=D("40000"))


def test_zero_depth_and_cash_cap_fail_closed() -> None:
    c, a, s = m.demo()
    with pytest.raises(ValueError, match="NO_FEASIBLE_EXPOSURE"):
        m.simulate(c, (replace(a[0], arrived_depth_shares=0), a[1]), s,
                   "zero", {"INVENTED_A": D("1")},
                   exceed_susq=D("125000"), bust_at_or_below_susq=D("40000"))
    poor = replace(c, settled_cash=D("0"))
    with pytest.raises(ValueError, match="NO_FEASIBLE_EXPOSURE"):
        m.simulate(poor, a, s, "zero_cash", {"INVENTED_A": D("1")},
                   exceed_susq=D("125000"), bust_at_or_below_susq=D("40000"))


def test_invalid_probabilities_float_and_overallocations_denied() -> None:
    c, a, s = m.demo()
    kwargs = {"exceed_susq": D("125000"), "bust_at_or_below_susq": D("40000")}
    with pytest.raises(ValueError, match="SCENARIOS_DO_NOT_SUM_TO_ONE"):
        m.simulate(c, a, s[:-1], "bad", {"INVENTED_A": D("1")}, **kwargs)
    with pytest.raises(ValueError, match="OVERALLOCATED_GROSS_SLICES"):
        m.simulate(c, a, s, "bad",
                   {"INVENTED_A": D("1"), "INVENTED_B": D("0.1")}, **kwargs)
    with pytest.raises(ValueError, match="NONFINITE_OR_BINARY_FLOAT_INPUT"):
        m.simulate(c, (replace(a[0], fill_if_win=0.4), a[1]), s,
                   "bad", {"INVENTED_A": D("1")}, **kwargs)


def test_independent_opponent_evidence_mandatory_to_estimate_first() -> None:
    c, a, s = m.demo()
    kwargs = {"exceed_susq": D("125000"), "bust_at_or_below_susq": D("40000")}
    r = m.simulate(
        c, a, s, "A", {"INVENTED_A": D("1")},
        opponent_scores_independently_verified=True, **kwargs,
    )
    assert r.first_place_probability is None
    fake = tuple(replace(x, actual_opponent_top_settled=D("125000")) for x in s)
    r2 = m.simulate(
        c, a, fake, "A", {"INVENTED_A": D("1")},
        opponent_scores_independently_verified=False, **kwargs,
    )
    assert r2.first_place_probability is None


def test_joint_election_scenarios_change_basket_threshold_probability() -> None:
    c, a, s = m.demo()
    kwargs = {"exceed_susq": D("125000"), "bust_at_or_below_susq": D("40000")}
    joint = (
        m.Scenario("both_win", D("0.55"), frozenset({"INVENTED_A", "INVENTED_B"})),
        m.Scenario("both_lose", D("0.45"), frozenset()),
    )
    weights = {"INVENTED_A": D("0.5"), "INVENTED_B": D("0.5")}
    old = m.simulate(c, a, s, "basket", weights, **kwargs)
    new = m.simulate(c, a, joint, "basket", weights, **kwargs)
    assert new.prob_beat_target > old.prob_beat_target
