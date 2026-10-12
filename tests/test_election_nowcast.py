"""Adversarial LANE-2 tests; all invented counts/sources are synthetic fixtures."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from predictions_cup.research.election_evaluation import (
    EvaluationRow,
    ForecastReport,
    evaluate_holdout,
)
from predictions_cup.research.election_nowcast import (
    Abstain,
    CountObservation,
    FrozenPrior,
    MarketRule,
    OfficialSource,
    RemainingGroup,
    ReportedChoice,
    RevisionEvidence,
    ShadowCandidate,
    choose_observation,
    make_shadow_candidate,
)

T = datetime(2026, 11, 4, 2, tzinfo=UTC)
A = ReportedChoice("candidate-a", "party-a", "ballot-a", 5100)
B = ReportedChoice("candidate-b", "party-b", "ballot-b", 5000)


def raw_payload(obs: CountObservation) -> bytes:
    return json.dumps({
        "publisher_id": "state-authority",
        "contest_id": obs.contest_id,
        "revision_id": obs.revision_id,
        "source_time": obs.source_time.isoformat(),
        "source_url": obs.source_url,
        "choices": [
            {
                "candidate_id": c.candidate_id,
                "party_id": c.party_id,
                "ballot_choice_id": c.ballot_choice_id,
                "votes": c.votes,
            }
            for c in obs.reported_choices
        ],
    }, sort_keys=True).encode()


def bind_payload(obs: CountObservation) -> CountObservation:
    return replace(obs, source_document_sha256=hashlib.sha256(raw_payload(obs)).hexdigest())


def evidence_for(obs: CountObservation) -> RevisionEvidence:
    return RevisionEvidence(
        publisher_id="state-authority",
        source_id=obs.source_id,
        contest_id=obs.contest_id,
        revision_id=obs.revision_id,
        source_url=obs.source_url,
        source_time=obs.source_time,
        receive_time=obs.receive_time,
        raw_source_body=raw_payload(obs),
        extracted_choices=obs.reported_choices,
        capture_ref="fixture://immutable/revision/" + obs.revision_id,
        capture_manifest_sha256="c" * 64,
        publisher_timestamp_field="PUBLISHER_REPORTED",
    )


def inputs() -> tuple[OfficialSource, MarketRule, FrozenPrior, CountObservation]:
    source = OfficialSource(
        "official", "results.example.gov", True,
        "https://results.example.gov/races", "NOT_VERIFIED_FOR_REAL_HOST",
        "state-authority", "d" * 64,
    )
    rule = MarketRule(
        "844", "race-one", "2026-FAMILY", "candidate-a", "official",
        "b" * 64, True, True,
        "candidate-a", "party-a", "ballot-a",
        "candidate-b", "party-b", "ballot-b",
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
        "r1", "INITIAL", "UNOFFICIAL_RESULTS", (A, B),
        (
            RemainingGroup("west-mail", "MAIL", 500, 2000),
            RemainingGroup("east-day", "ELECTION_DAY", 1000, 4000),
        ),
        True, "",
    )
    return source, rule, prior, bind_payload(obs)


def calculate(
    obs: CountObservation | None = None,
    *,
    evidence: RevisionEvidence | None = None,
    source: OfficialSource | None = None,
    rule: MarketRule | None = None,
    prior: FrozenPrior | None = None,
    decision_at: datetime = T,
    simulations: int = 512,
) -> ShadowCandidate:
    s, r, p, original = inputs()
    current = obs if obs is not None else original
    return make_shadow_candidate(
        current, source or s, rule or r, prior or p,
        evidence if evidence is not None else evidence_for(current),
        decision_at, simulations=simulations,
    )


def candidate(**updates: Any) -> ShadowCandidate:
    _, _, _, obs = inputs()
    return calculate(bind_payload(replace(obs, **updates)))


def test_shadow_interface_and_deterministic_probability() -> None:
    a = candidate()
    assert a == candidate()
    assert a.mode == "SHADOW" and a.execution_authority is False
    assert a.target_exchange_id == "844"
    assert a.confidence == "MODEL_DEPENDENT_UNVALIDATED"
    assert a.publication_to_receipt_ms == 1000
    assert a.p_interval[0] <= a.probability_estimate <= a.p_interval[1]
    assert a.expiry > T
    assert a.reported_margin == 100 and a.revision_id == "r1"
    assert {
        "source_time", "receive_time", "target_exchange_id", "p_interval",
        "confidence", "expiry", "reason", "source_refs", "revision_id",
        "source_document_sha256", "reported_margin",
    } <= set(a.as_dict())


def test_missing_remaining_inventory_and_frozen_prior_abstain() -> None:
    source, rule, prior, obs = inputs()
    with pytest.raises(Abstain, match="remaining"):
        calculate(replace(obs, remaining_inventory_verified=False))
    with pytest.raises(Abstain, match="missing frozen"):
        calculate(obs, prior=replace(prior, beta_by_stratum={}))


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
    source, rule, _, obs = inputs()
    with pytest.raises(Abstain, match="identity"):
        candidate(source_url="https://results.example.gov.evil.test/races")
    with pytest.raises(Abstain, match="not approved"):
        calculate(obs, source=replace(source, approved=False))
    with pytest.raises(Abstain, match="not approved"):
        calculate(obs, rule=replace(rule, rules_reviewed=False))
    with pytest.raises(Abstain, match="identity"):
        calculate(obs, rule=replace(rule, rule_type="SENATE_CONTROL"))


def test_training_overlap_and_postpublication_freeze_abstain() -> None:
    _, _, prior, obs = inputs()
    with pytest.raises(Abstain, match="leakage"):
        calculate(obs, prior=replace(prior, training_event_families=("2026-FAMILY",)))
    with pytest.raises(Abstain, match="frozen"):
        calculate(obs, prior=replace(prior, frozen_at=T))


def test_mail_day_composition_changes_probabilities_not_precinct_share() -> None:
    _, _, _, obs = inputs()
    mail = replace(obs, remaining=(
        RemainingGroup("west-mail", "MAIL", 3000, 3000),
        RemainingGroup("east-day", "ELECTION_DAY", 0, 0),
    ))
    day = replace(obs, remaining=(
        RemainingGroup("west-mail", "MAIL", 0, 0),
        RemainingGroup("east-day", "ELECTION_DAY", 3000, 3000),
    ))
    assert calculate(mail, simulations=1024).probability_estimate < 0.1
    assert calculate(day, simulations=1024).probability_estimate > 0.9


def test_remaining_ballot_uncertainty_expands_envelope() -> None:
    _, _, _, obs = inputs()
    wide = calculate(obs, simulations=1024)
    narrow = calculate(
        replace(obs, remaining=tuple(
            replace(g, min_count=g.max_count) for g in obs.remaining
        )), simulations=1024,
    )
    assert wide.p_interval[0] <= narrow.p_interval[0] + 0.05
    assert wide.p_interval[1] >= narrow.p_interval[1] - 0.05


def test_revision_not_visible_before_receipt_or_without_correction_label() -> None:
    _, _, _, original = inputs()
    corrected = bind_payload(replace(
        original, source_time=T + timedelta(seconds=1),
        receive_time=T + timedelta(seconds=2),
        revision_id="r2", revision_kind="CORRECTION",
        reported_choices=(replace(A, votes=4000), B),
    ))
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


def test_candidate_party_and_ballot_ids_are_required() -> None:
    _, rule, _, obs = inputs()
    for wrong in (
        replace(A, candidate_id="wrong-candidate"),
        replace(A, party_id="wrong-party"),
        replace(A, ballot_choice_id="wrong-ballot-choice"),
    ):
        with pytest.raises(Abstain, match="candidate, party or ballot choice mismatch"):
            calculate(bind_payload(replace(obs, reported_choices=(wrong, B))))
    with pytest.raises(Abstain, match="orientation"):
        calculate(obs, rule=replace(rule, outcome_a="candidate-b"))


def test_column_order_is_not_probability_orientation() -> None:
    _, _, _, obs = inputs()
    reordered = bind_payload(replace(obs, reported_choices=(B, A)))
    p = calculate(obs).probability_estimate
    q = calculate(reordered).probability_estimate
    assert abs(p - q) < 0.1


def test_swapped_counts_with_original_receipt_must_abstain() -> None:
    _, _, _, obs = inputs()
    original_evidence = evidence_for(obs)
    swapped = replace(obs, reported_choices=(replace(A, votes=5000), replace(B, votes=5100)))
    with pytest.raises(Abstain, match="receipt"):
        calculate(swapped, evidence=original_evidence)
    # Even if an adapter changes the reported digest, old archived bytes disagree.
    with pytest.raises(Abstain, match="receipt"):
        calculate(bind_payload(swapped), evidence=original_evidence)
    with pytest.raises(Abstain, match="receipt"):
        calculate(obs, evidence=replace(
            original_evidence, extracted_choices=swapped.reported_choices
        ))


def test_third_party_votes_never_ignored_silently() -> None:
    _, _, _, obs = inputs()
    third = ReportedChoice("candidate-c", "party-independent", "ballot-c", 550)
    with pytest.raises(Abstain, match="third-party"):
        calculate(bind_payload(replace(obs, reported_choices=(A, B, third))))


def test_unverifiable_raw_body_and_revision_receipt_abstain() -> None:
    _, _, _, obs = inputs()
    receipt = evidence_for(obs)
    with pytest.raises(Abstain, match="receipt"):
        calculate(obs, evidence=replace(receipt, raw_source_body=b"altered"))
    with pytest.raises(Abstain, match="receipt"):
        calculate(obs, evidence=replace(receipt, publisher_timestamp_field="HTTP_DATE"))
    with pytest.raises(Abstain, match="receipt"):
        calculate(obs, evidence=replace(receipt, receive_time=T))
    with pytest.raises(Abstain, match="unparseable"):
        calculate(
            replace(obs, source_document_sha256=hashlib.sha256(b"{}").hexdigest()),
            evidence=replace(receipt, raw_source_body=b"{}"),
        )


def make_row(**updates: Any) -> EvaluationRow:
    fields: dict[str, Any] = {
        "event_family": "2026-FAMILY", "contest_id": "race-one",
        "quote_exchange_id": "844",
        "candidate": candidate(),
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
        make_row(event_family="WRONG-FAMILY"),
        make_row(quote_exchange_id="WRONG-EXCHANGE"),
    ])
    assert report.rows == 0 and report.failed_rows == 7


def test_duplicate_decision_and_conflicting_outcome_not_double_counted() -> None:
    original = make_row()
    report = evaluate([original, original, replace(original, outcome_a_won=False)])
    assert report.rows == 1
    assert report.failed_rows == 2
    assert report.independent_event_families == 1


def test_many_observations_one_event_do_not_create_forecast_claim() -> None:
    rows = [
        make_row(candidate=replace(candidate(), decision_time=T + timedelta(milliseconds=100*i)))
        for i in range(60)
    ]
    report = evaluate(rows)
    assert report.rows == 60
    assert report.independent_event_families == 1
    assert len(report.per_event) == 1
    assert report.per_event[0].decision_rows == 60
    assert report.calibration_gap_cluster_ci95 is None
    assert report.status == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    assert report.economic_advantage == "NOT_MEASURED_NO_EXECUTABLE_REPLAY"
    assert report.historical_negative_comparison == "R3_PRED007_NOT_COMPARABLE_TARGETS"


def test_official_count_reversal_unchanged_sig_quote_one_episode_no_fills() -> None:
    source, rule, prior, obs = inputs()
    first = bind_payload(replace(
        obs, source_time=T - timedelta(seconds=8),
        receive_time=T - timedelta(seconds=7),
    ))
    revised = bind_payload(replace(
        obs, revision_id="r2", revision_kind="CORRECTION",
        source_time=T - timedelta(seconds=3), receive_time=T - timedelta(seconds=2),
        reported_choices=(replace(A, votes=4000), replace(B, votes=5200)),
    ))
    assert choose_observation([first, revised], T - timedelta(seconds=5)) == first
    assert choose_observation([first, revised], T) == revised
    first_p = calculate(
        first, source=source, rule=rule, prior=prior,
        decision_at=T - timedelta(seconds=5),
    )
    revised_p = calculate(revised, source=source, rule=rule, prior=prior)
    quote = {
        "quote_asof": T - timedelta(seconds=9),
        "quote_received": T - timedelta(seconds=8),
        "sig_bid_yes": 0.45, "sig_ask_yes": 0.47,
        "outcome_a_won": False,
    }
    report = evaluate([
        make_row(candidate=first_p, **quote),
        make_row(candidate=revised_p, **quote),
    ])
    assert first_p.reported_margin > 0 and revised_p.reported_margin < 0
    assert report.rows == 2 and report.independent_event_families == 1
    assert report.status == "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    event = report.per_event[0]
    assert event.revisions == 2 and event.decision_rows == 2
    assert event.unchanged_sig_mid and event.count_lead_reversed
    assert event.hypothetical_fills == 0
    assert event.brier_improvement_vs_asof_mid == report.brier_improvement_vs_asof_mid
    assert report.calibration_gap_cluster_ci95 is None
    assert report.economic_advantage == "NOT_MEASURED_NO_EXECUTABLE_REPLAY"
