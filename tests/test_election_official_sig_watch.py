"""Synthetic official-SIG shadow watch, no live requests or orders."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from predictions_cup.analysis.election_official_sig_watch import capture_official_sig_pair
from predictions_cup.analysis.election_official_texas import TexasSenateObservation
from predictions_cup.sig.dto import OrderBookSnapshotDto


def official(status: str = "PRE_ELECTION") -> TexasSenateObservation:
    return TexasSenateObservation(
        race="Texas U.S. Senate",
        source_url="https://electionresults.sos.state.tx.us/results.html",
        observed_at=datetime(2026, 10, 28, tzinfo=UTC).isoformat(),
        body_sha256="b" * 64,
        status=status,  # type: ignore[arg-type]
        precincts_reporting_percent=0.0,
        votes_by_party={},
    )


def book(exchange: str, market: str, *, crossed: bool = False) -> OrderBookSnapshotDto:
    return OrderBookSnapshotDto.model_validate({
        "exchangeId": exchange, "marketId": market, "depth": 20,
        "bids": [{"price": "0.650" if crossed else "0.335", "quantity": "50"}],
        "asks": [{"price": "0.340", "quantity": "70"}],
        "bestBid": "0.650" if crossed else "0.335",
        "bestAsk": "0.340",
        "spread": "-0.310" if crossed else "0.005",
    })


class FakeBooks:
    def __init__(self, *, mismatched: bool = False, crossed: bool = False):
        self.calls: list[str] = []
        self.mismatched = mismatched
        self.crossed = crossed

    async def get_orderbook(
        self, exchange_id: str, *, depth: int = 20,
        tournament_id: str | None = None,
    ) -> OrderBookSnapshotDto:
        assert tournament_id == "test-tournament"
        assert depth == 20
        self.calls.append(exchange_id)
        mid = (
            "WRONG" if self.mismatched else
            ("293" if exchange_id == "982" else "294")
        )
        return book(exchange_id, mid, crossed=self.crossed)


async def no_pause(_: float) -> None:
    pass


def test_official_and_sig_quotes_are_both_observed_without_alpha() -> None:
    reader = FakeBooks()
    result = asyncio.run(capture_official_sig_pair(
        reader, official=official(),
        tournament_id="test-tournament", pause=no_pause,
    ))
    assert reader.calls == ["982", "983"]
    assert result["mode"] == "OFFICIAL_AND_SIG_READONLY_SHADOW"
    assert result["probability_generated"] is False
    assert result["execution_authority"] is False
    assert result["status"] == "NO_ELECTION_RESULT_SIGNAL"
    quotes = result["sig_quotes"]
    assert isinstance(quotes, list)
    assert len(quotes) == 2


def test_contract_mismatch_and_crossed_book_refuse_snapshot() -> None:
    for opts in ({"mismatched": True}, {"crossed": True}):
        with pytest.raises(ValueError, match="market|crossed"):
            asyncio.run(capture_official_sig_pair(
                FakeBooks(**opts), official=official(),
                tournament_id="test-tournament", pause=no_pause,
            ))


def test_missing_tournament_refused_before_any_read() -> None:
    reader = FakeBooks()
    with pytest.raises(ValueError, match="tournament"):
        asyncio.run(capture_official_sig_pair(
            reader, official=official(), tournament_id="",
            pause=no_pause,
        ))
    assert reader.calls == []
