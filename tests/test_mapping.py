from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from predictions_cup.external.polymarket.models import PolymarketMarket
from predictions_cup.mapping.crosswalk import document_csv, document_json, summary
from predictions_cup.mapping.generator import SigExchangeSnapshot, build_mapping_document
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingOverride,
    MappingOverrideDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
    PolymarketOverrideLeg,
)


def _poly(
    market_id: str,
    question: str,
    *,
    condition_id: str | None = None,
    yes_token: str | None = None,
    no_token: str | None = None,
) -> PolymarketMarket:
    return PolymarketMarket.from_gamma(
        {
            "id": market_id,
            "conditionId": condition_id or f"cid-{market_id}",
            "question": question,
            "slug": f"slug-{market_id}",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": (
                f'["{yes_token or f"{market_id}-yes"}", '
                f'"{no_token or f"{market_id}-no"}"]'
            ),
            "active": True,
            "closed": False,
            "events": [{"id": f"event-{market_id}", "title": question}],
        }
    )


def _sig(
    exchange_id: str = "sig-exchange",
    *,
    market_id: str = "sig-market",
    market_title: str = "Will the Republican Party win the Georgia Senate?",
    outcome_label: str = "Yes",
) -> SigExchangeSnapshot:
    return SigExchangeSnapshot(
        tournament_id="tournament-2026",
        market_id=market_id,
        market_title=market_title,
        exchange_id=exchange_id,
        outcome_label=outcome_label,
        semantic_text="Election Outcome Georgia Senate 2026",
    )


def _override(
    *,
    exchange_id: str = "sig-exchange",
    market_id: str = "sig-market",
    sig_outcome: str = "Yes",
    poly_market_id: str = "poly-1",
    poly_outcome: str = "Yes",
    mapping_class: MappingClass = MappingClass.EXACT,
    direction: MappingDirection = MappingDirection.SAME,
) -> MappingOverrideDocument:
    return MappingOverrideDocument(
        tournament_id="tournament-2026",
        records=(
            MappingOverride(
                sig_market_id=market_id,
                sig_exchange_id=exchange_id,
                sig_outcome_label=sig_outcome,
                mapping_class=mapping_class,
                mapping_direction=direction,
                mapping_confidence=Decimal("1"),
                status=MappingStatus.VERIFIED,
                polymarket_legs=(
                    PolymarketOverrideLeg(
                        market_id=poly_market_id,
                        outcome=poly_outcome,
                    ),
                ),
                semantic_notes="Reviewed semantic equivalence.",
                resolution_notes="Reviewed resolution semantics.",
            ),
        ),
    )


def test_outcome_token_alignment_is_preserved_exactly() -> None:
    market = _poly(
        "poly-1",
        "Will the Republican Party win the Georgia Senate election?",
        yes_token="token-a",
        no_token="token-b",
    )
    document = build_mapping_document(
        "tournament-2026",
        (_sig(),),
        (market,),
        _override(poly_outcome="No"),
    )

    direct = document.records[0].direct_polymarket
    assert direct is not None
    assert direct.outcomes == ("Yes", "No")
    assert direct.token_ids == ("token-a", "token-b")
    assert direct.mapped_outcome == "No"
    assert direct.mapped_token_id == "token-b"


def test_complement_mapping_selects_economically_correct_token() -> None:
    market = _poly(
        "poly-1",
        "Will the Democratic Party win the Georgia Senate election?",
        yes_token="dem-yes",
        no_token="dem-no",
    )
    document = build_mapping_document(
        "tournament-2026",
        (
            _sig(
                market_title="Will the Republican Party win the Georgia Senate?",
                outcome_label="Republican",
            ),
        ),
        (market,),
        _override(
            sig_outcome="Republican",
            poly_outcome="No",
            direction=MappingDirection.COMPLEMENT,
        ),
    )

    mapping = document.records[0]
    assert mapping.mapping_direction is MappingDirection.COMPLEMENT
    assert mapping.direct_polymarket is not None
    assert mapping.direct_polymarket.mapped_outcome == "No"
    assert mapping.direct_polymarket.mapped_token_id == "dem-no"


def test_duplicate_sig_exchange_mapping_is_rejected() -> None:
    record = MarketMapping(
        sig_tournament_id="tournament-2026",
        sig_market_id="sig-market",
        sig_market_title="Example",
        sig_exchange_id="sig-exchange",
        sig_outcome_label="Yes",
        mapping_class=MappingClass.NO_TRADE,
        mapping_confidence=Decimal("0"),
        status=MappingStatus.UNRESOLVED,
    )
    with pytest.raises(ValidationError, match="exactly once"):
        MappingDocument(
            tournament_id="tournament-2026",
            records=(record, record),
        )


def test_direct_identity_rejects_missing_cid_and_misaligned_mapped_token() -> None:
    with pytest.raises(ValidationError):
        PolymarketContractIdentity(
            market_id="poly",
            condition_id=" ",
            question="Question?",
            outcomes=("Yes", "No"),
            token_ids=("yes-token", "no-token"),
            mapped_outcome="Yes",
            mapped_token_id="yes-token",
        )

    with pytest.raises(ValidationError, match="not uniquely aligned"):
        PolymarketContractIdentity(
            market_id="poly",
            condition_id="cid",
            question="Question?",
            outcomes=("Yes", "No"),
            token_ids=("yes-token", "no-token"),
            mapped_outcome="Yes",
            mapped_token_id="no-token",
        )


def test_candidate_similarity_never_auto_promotes_ambiguous_market() -> None:
    markets = (
        _poly("poly-dem", "Will the Democratic Party win the Georgia Senate election?"),
        _poly("poly-rep", "Will the Republican Party win the Georgia Senate election?"),
    )
    document = build_mapping_document(
        "tournament-2026",
        (_sig(),),
        markets,
        MappingOverrideDocument(tournament_id="tournament-2026"),
    )

    mapping = document.records[0]
    assert mapping.mapping_class is MappingClass.NO_TRADE
    assert mapping.status is MappingStatus.UNRESOLVED
    assert mapping.direct_polymarket is None
    assert set(mapping.candidate_polymarket_market_ids) == {"poly-dem", "poly-rep"}


def test_generation_is_deterministic_for_equivalent_inputs() -> None:
    sig_rows = (
        _sig("sig-b", market_id="market-b"),
        _sig("sig-a", market_id="market-a"),
    )
    markets = (
        _poly("poly-z", "Will the Republican Party win the Georgia Senate election?"),
        _poly("poly-a", "Will the Democratic Party win the Georgia Senate election?"),
    )
    overrides = MappingOverrideDocument(tournament_id="tournament-2026")

    first = build_mapping_document(
        "tournament-2026",
        sig_rows,
        markets,
        overrides,
    )
    second = build_mapping_document(
        "tournament-2026",
        reversed(sig_rows),
        reversed(markets),
        overrides,
    )

    assert document_json(first) == document_json(second)
    assert document_csv(first) == document_csv(second)
    assert [record.sig_exchange_id for record in first.records] == ["sig-a", "sig-b"]


def test_summary_is_derived_from_document() -> None:
    market = _poly("poly-1", "Will the Republican Party win the Georgia Senate election?")
    document = build_mapping_document(
        "tournament-2026",
        (_sig(),),
        (market,),
        _override(),
    )

    result = summary(document)
    assert result["sig_markets"] == 1
    assert result["sig_exchanges"] == 1
    assert result["EXACT"] == 1
    assert result["direct_polymarket_markets"] == 1
    assert result["unique_cids"] == 1
    assert result["unique_clob_token_ids"] == 2
    assert result["duplicate_conflicting_mappings"] == 0


def test_stale_override_cannot_silently_attach_to_changed_sig_exchange() -> None:
    market = _poly("poly-1", "Will the Republican Party win the Georgia Senate election?")
    with pytest.raises(ValueError, match="outcome mismatch"):
        build_mapping_document(
            "tournament-2026",
            (_sig(outcome_label="No"),),
            (market,),
            _override(sig_outcome="Yes"),
        )
