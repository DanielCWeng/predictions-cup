"""LANE-2 fail-closed, scenario-sensitivity and point-in-time falsification tests."""
from __future__ import annotations

from dataclasses import replace
from typing import Any
from datetime import UTC, datetime, timedelta

import pytest

from predictions_cup.research.election_evaluation import EvaluationRow, ForecastReport, evaluate_holdout
from predictions_cup.research.election_nowcast import (
    Abstain,
    CountObservation,
    FrozenPrior,
    MarketRule,
    OfficialSource,
    RemainingGroup,
    ShadowCandidate,
    choose_observation,
    make_shadow_candidate,
)

T = datetime(2026, 11, 4, 2, tzinfo=UTC)


def inputs() -> tuple[OfficialSource, MarketRule, FrozenPrior, CountObservation]:
    source = OfficialSource(
        "official", "results.example.gov", True,
        "https://results.example.gov/races", "NOT_VERIFIED_FOR_REAL_HOST",
    )
    rule = MarketRule(
        "844", "race-one", "2026-FAMILY", "candidate-a", "official",
        "b" * 64, True, True,
    )
    prior = FrozenPrior(
        "frozen-research", T - timedelta(days=3), ("2024-FAMILY",),
        "synthetic-fixture-only", 0.06, 0.05,
        {"west-mail": (15, 85), "east-day": (75, 25)},
    )
    obs = CountObservation(
        "race-one", "official", "fixture://2026/revision-one",
        "https://results.example.gov/races/1",
        T - timedelta(seconds=3), T - timedelta(seconds=2),
        "r1", "INITIAL", "UNOFFICIAL_RESULTS", 5100, 5000,
        (
            RemainingGroup("west-mail", "MAIL", 500, 2000),
            RemainingGroup("east-day", "ELECTION_DAY", 1000, 4000),
        ),
        True, "a" * 64,
    )
    return source, rule, prior, obs


def candidate(**updates: Any) -> ShadowCandidate:
    source, rule, prior, obs = inputs()
    return make_shadow_candidate(
        replace(obs, **updates), source, rule, prior, T, simulations=512
    )


def test_shadow_interface_and_deterministic_probability() -> None:
    a = candidate()
    assert a == candidate()
    assert a.mode == "SHADOW" and a.execution_authority is False
    assert a.target_exchange_id == "844"
    assert a.confidence == "MODEL_DEPENDENT_UNVALIDATED"
    assert a.publication_to_receipt_ms == 1000
    assert a.p_interval[0] <= a.probability_estimate <= a.p_interval[1]
    assert a.expiry > T
    assert {
        "source_time", "receive_time", "target_exchange_id", "p_interval",
        "confidence", "expiry", "reason", "source_refs"
    } <= set(a.as_dict())


def test_missing_remaining_inventory_and_frozen_prior_abstain() -> None:
    source, rule, prior, obs = inputs()
    with pytest.raises(Abstain, match="remaining"):
        make_shadow_candidate(
            replace(obs, remaining_inventory_verified=False),
            source, rule, prior, T, simulations=512,
        )
    with pytest.raises(Abstain, match="missing frozen"):
        make_shadow_candidate(
            obs, source, rule, replace(prior, beta_by_stratum={}),
            T, simulations=512,
        )


def test_stale_future_and_inverted_clocks_abstain() -> None:
    with pytest.raises(Abstain, match="future"):
        candidate(receive_time=T + timedelta(seconds=1))
    with pytest.raises(Abstain, match="TTL"):
        candidate(
            source_time=T - timedelta(seconds=51),
            receive_time=T - timedelta(seconds=50),
        )
    with pytest.raises(Abstain, match="transport"):
        candidate(source_time=T - timedelta(hours=1))


def test_official_hostname_and_exact_rules_are_required() -> None:
    source, rule, prior, obs = inputs()
    with pytest.raises(Abstain, match="host"):
        make_shadow_candidate(
            replace(obs, source_url="https://results.example.gov.evil.test/races"),
            source, rule, prior, T, simulations=512,
        )
    with pytest.raises(Abstain, match="not approved"):
        make_shadow_candidate(
            obs, replace(source, approved=False),
            rule, prior, T, simulations=512,
        )
    with pytest.raises(Abstain, match="not approved"):
        make_shadow_candidate(
            obs, source, replace(rule, rules_reviewed=False),
            prior, T, simulations=512,
        )
    with pytest.raises(Abstain, match="identity"):
        make_shadow_candidate(
            obs, source, replace(rule, rule_type="SENATE_CONTROL"),
            prior, T, simulations=512,
        )


def test_training_overlap_and_postpublication_freeze_abstain() -> None:
    source, rule, prior, obs = inputs()
    with pytest.raises(Abstain, match="leakage"):
        make_shadow_candidate(
            obs, source, rule,
            replace(prior, training_event_families=("2026-FAMILY",)),
            T, simulations=512,
        )
    with pytest.raises(Abstain, match="frozen"):
        make_shadow_candidate(
            obs, source, rule, replace(prior, frozen_at=T),
            T, simulations=512,
        )


def test_mail_day_composition_changes_probabilities_not_just_precinct_share() -> None:
    source, rule, prior, obs = inputs()
    mail = replace(obs, remaining=(
        RemainingGroup("west-mail", "MAIL", 3000, 3000),
        RemainingGroup("east-day", "ELECTION_DAY", 0, 0),
    ))
    day = replace(obs, remaining=(
        RemainingGroup("west-mail", "MAIL", 0, 0),
        RemainingGroup("east-day", "ELECTION_DAY", 3000, 3000),
    ))
    p_mail = make_shadow_candidate(mail, source, rule, prior, T, simulations=1024)
    p_day = make_shadow_candidate(day, source, rule, prior, T, simulations=1024)
    assert p_mail.probability_estimate < 0.1
    assert p_day.probability_estimate > 0.9


def test_remaining_ballot_uncertainty_expands_envelope() -> None:
    source, rule, prior, obs = inputs()
    wide = make_shadow_candidate(obs, source, rule, prior, T, simulations=1024)
    narrow = make_shadow_candidate(
        replace(obs, remaining=tuple(
            replace(g, min_count=g.max_count) for g in obs.remaining
        )),
        source, rule, prior, T, simulations=1024,
    )
    assert wide.p_interval[0] <= narrow.p_interval[0] + 0.05
    assert wide.p_interval[1] >= narrow.p_interval[1] - 0.05


def test_revision_not_visible_before_receipt_or_without_correction_label() -> None:
    _, _, _, original = inputs()
    corrected = replace(
        original, source_time=T + timedelta(seconds=1),
        receive_time=T + timedelta(seconds=2),
        revision_id="r2", revision_kind="CORRECTION", a_votes=4000,
    )
    assert choose_observation([original, corrected], T) == original
    assert choose_observation([original, corrected], T + timedelta(seconds=3)) == corrected
    with pytest.raises(Abstain, match="unlabeled"):
        choose_observation(
            [original, replace(corrected, revision_kind="UPDATE")],
            T + timedelta(seconds=3),
        )
    with pytest.raises(Abstain, match="duplicate"):
        choose_observation(
            [original, replace(corrected, revision_id="r1")],
            T + timedelta(seconds=3),
        )


def test_media_call_not_equivalent_to_official_result() -> None:
    with pytest.raises(Abstain, match="media projection"):
        candidate(status="MEDIA_CALL")


def make_row(**updates: Any) -> EvaluationRow:
    c = candidate()
    fields: dict[str, Any] = {
        "event_family": "2026-FAMILY", "contest_id": "race-one", "candidate": c,
        "quote_asof": T - timedelta(seconds=3),
        "quote_received": T - timedelta(seconds=2),
        "sig_bid_yes": 0.2, "sig_ask_yes": 0.25,
        "outcome_a_won": True, "outcome_known_at": T + timedelta(days=2),
        "split": "HOLDOUT",
    }
    fields.update(updates)
    return EvaluationRow(**fields)


def evaluate(
    rows: list[EvaluationRow], training: frozenset[str] = frozenset({"2024-FAMILY"})
) -> ForecastReport:
    return evaluate_holdout(
        rows, sealed_event_families=frozenset({"2026-FAMILY"}),
        training_event_families=training, resamples=200,
    )


def test_no_real_study_means_no_positive_alpha_claim() -> None:
    report = evaluate([])
    assert report.status == "NO_VALID_DECISION_TIME_OBSERVATIONS"
    assert report.economic_advantage == "NOT_MEASURED_NO_EXECUTABLE_REPLAY"


def test_split_mismatch_and_future_quote_are_hard_rejections() -> None:
    with pytest.raises(Abstain, match="overlaps"):
        evaluate([make_row()], training=frozenset({"2026-FAMILY"}))
    report = evaluate([
        make_row(quote_received=T + timedelta(seconds=1)),
        make_row(quote_asof=T - timedelta(minutes=3)),
        make_row(outcome_known_at=T - timedelta(seconds=1)),
        make_row(sig_bid_yes=0.9, sig_ask_yes=0.1),
        make_row(split="DEV"),
    ])
    assert report.rows == 0 and report.failed_rows == 5


def test_large_tick_count_from_one_event_cannot_pass_support() -> None:
    report = evaluate([make_row() for _ in range(100)])
    assert report.rows == 100
    assert report.independent_event_families == 1
    assert report.status == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert report.economic_advantage == "NOT_MEASURED_NO_EXECUTABLE_REPLAY"
