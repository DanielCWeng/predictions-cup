from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDirection, MappingStatus
from predictions_cup.models.contracts import ModelCapability
from predictions_cup.models.frozen_research import (
    FROZEN_RESEARCH_MODEL_IDS,
    Live005IMinuteState,
    frozen_research_paper_providers,
)
from predictions_cup.models.runtime import PaperModelCandidate
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot, DecisionStatus
from predictions_cup.shadow.live_005f import Live005FStateProvider

BASE = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
BASE_MONO = 30_000_000_000_000
NS = 1_000_000_000
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = PROJECT_ROOT / "data/mappings/sig_polymarket_2026.json"


def _identity() -> tuple[str, str, str]:
    mapping = load_document(MAPPING_PATH)
    for record in mapping.records:
        if (
            record.status is MappingStatus.VERIFIED
            and record.direct_polymarket is not None
            and record.mapping_direction
            in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
        ):
            return (
                record.sig_market_id,
                record.sig_exchange_id,
                record.direct_polymarket.mapped_token_id,
            )
    raise AssertionError("fixture mapping has no verified direct PM relation")


def _snapshot(minutes: float, bid: float, ask: float) -> CanonicalShadowSnapshot:
    market_id, exchange_id, token_id = _identity()
    now = BASE_MONO + int(minutes * 60 * NS)
    observed_at = BASE + timedelta(minutes=minutes)
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="SIG-2026",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="SIG-2026",
                bids=(RuntimeLevel(90, 10.0),),
                asks=(RuntimeLevel(110, 10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="SIG-2026",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now,
        sig_depth_trusted=True,
        account_observed_ns=now,
        inventory_observed_ns=now,
        external_quotes={
            token_id: ExternalQuoteState(
                token_id=token_id,
                best_bid=bid,
                best_ask=ask,
                observed_monotonic_ns=now,
                trusted=True,
                source_version="clob-market-ws-v1",
                observed_at=observed_at,
            )
        },
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=observed_at,
        mapping_version="test-mapping",
        source_revision=f"minute-{minutes}",
    )


def _feed_005i(state: Live005IMinuteState) -> tuple[str, float, float]:
    _, _, token_id = _identity()
    last_bid = 0.39
    last_ask = 0.41
    for minute in range(7):
        mid = 0.40 + minute * 0.01
        last_bid = mid - 0.01
        last_ask = mid + 0.01
        assert state.observe_bbo(
            scope_id=token_id,
            observed_at=BASE + timedelta(minutes=minute, seconds=10),
            best_bid=last_bid,
            best_ask=last_ask,
            source_version="clob-market-ws-v1",
            trusted=True,
        )
    return token_id, last_bid, last_ask


def test_005i_recent_5m_context_matches_frozen_completed_minute_definition() -> None:
    mapping = load_document(MAPPING_PATH)
    hazard = Live005FStateProvider(mapping=mapping, grid_origin=BASE)
    state, providers = frozen_research_paper_providers(
        mapping=mapping,
        hazard_005f=hazard,
    )
    _, bid, ask = _feed_005i(state)
    snapshot = _snapshot(7.0, bid, ask)

    recent = providers[0]
    output = PaperModelCandidate(recent).evaluate(snapshot)
    assert output.status is DecisionStatus.OK
    assert output.direction is None
    assert output.action_intent is None
    assert output.score is not None
    assert math.isclose(output.score, 0.05, abs_tol=1e-12)
    context = output.candidate_payload["model_context"]
    assert isinstance(context, dict)
    assert math.isclose(float(context["ret_5m"]), 0.05, abs_tol=1e-12)
    assert context["quote_events_exact_parity"] is False
    assert output.candidate_payload["hypothetical_size"] == 0


def test_005i_non_parity_objects_are_explicit_not_ready_not_approximated() -> None:
    mapping = load_document(MAPPING_PATH)
    hazard = Live005FStateProvider(mapping=mapping, grid_origin=BASE)
    state, providers = frozen_research_paper_providers(
        mapping=mapping,
        hazard_005f=hazard,
    )
    _, bid, ask = _feed_005i(state)
    snapshot = _snapshot(7.0, bid, ask)

    expected = {
        "005i_price_discovery_context": "raw_quote_events",
        "005i_liquidity_stress_context": "polymarket_depth_total5",
        "005i_withdrawal_replenishment_context": "polymarket_depth_total5",
        "005i_depth_normalised_ofi_context": "ofi_depth_norm",
    }
    for provider in providers[1:5]:
        output = PaperModelCandidate(provider).evaluate(snapshot)
        assert output.status is DecisionStatus.NOT_READY
        assert output.direction is None
        context = output.candidate_payload["model_context"]
        assert isinstance(context, dict)
        missing = context["missing_exact_parity"]
        assert expected[provider.spec.model_id] in missing


def test_frozen_research_providers_are_context_only_and_code_live_off() -> None:
    mapping = load_document(MAPPING_PATH)
    hazard = Live005FStateProvider(mapping=mapping, grid_origin=BASE)
    _, providers = frozen_research_paper_providers(
        mapping=mapping,
        hazard_005f=hazard,
    )
    assert tuple(provider.spec.model_id for provider in providers) == FROZEN_RESEARCH_MODEL_IDS
    assert all(provider.spec.capability is ModelCapability.CONTEXT_ONLY for provider in providers)
    assert all(provider.spec.live_eligible is False for provider in providers)


def test_005i_non_ws_or_ambiguous_minute_fails_closed() -> None:
    mapping = load_document(MAPPING_PATH)
    state = Live005IMinuteState(mapping)
    _, _, token_id = _identity()
    for minute in range(7):
        assert state.observe_bbo(
            scope_id=token_id,
            observed_at=BASE + timedelta(minutes=minute, seconds=10),
            best_bid=0.39 + minute * 0.01,
            best_ask=0.41 + minute * 0.01,
            source_version=(
                "clob-rest-seed-v1" if minute == 3 else "clob-market-ws-v1"
            ),
            trusted=True,
        )
    result = state.context(_snapshot(7.0, 0.45, 0.47))
    assert not result.ready
    assert result.reason == "005i_completed_minute_bbo_parity_unavailable"


def test_005f_renewal_context_reuses_exact_existing_live_state() -> None:
    mapping = load_document(MAPPING_PATH)
    hazard = Live005FStateProvider(mapping=mapping, grid_origin=BASE)
    _, providers = frozen_research_paper_providers(
        mapping=mapping,
        hazard_005f=hazard,
    )
    renewal = providers[-1]
    _, _, token_id = _identity()

    rows = (
        (0.0, 0.40, 0.60),
        (10.0, 0.40, 0.60),
        (20.0, 0.42, 0.60),
        (30.0, 0.42, 0.60),
        (45.0, 0.44, 0.60),
    )
    for seconds, bid, ask in rows:
        assert hazard.observe_bbo(
            scope_id=token_id,
            observed_at=BASE + timedelta(seconds=seconds),
            observed_monotonic_ns=BASE_MONO + int(seconds * NS),
            best_bid=bid,
            best_ask=ask,
            source_version="clob-market-ws-v1",
            trusted=True,
        )

    snapshot = _snapshot(1.0, 0.44, 0.60)
    output = PaperModelCandidate(renewal).evaluate(snapshot)
    assert output.status is DecisionStatus.OK
    assert output.direction is None
    context = output.candidate_payload["model_context"]
    assert isinstance(context, dict)
    features = context["features"]
    assert isinstance(features, dict)
    assert features["genuine_age_s"] == 15.0
    assert features["genuine_60"] == 2.0
