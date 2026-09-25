"""Configurable 2026 U.S. election-market universe selection."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.external.polymarket.models import PolymarketMarket

_ELECTION_TERMS = (
    "senate",
    "house of representatives",
    "u.s. house",
    "us house",
    "congress",
    "governor",
    "gubernatorial",
    "midterm",
    "balance of power",
    "chamber control",
    "house control",
    "senate control",
    "congressional district",
    "seat count",
    "senate seats",
    "house seats",
)


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    markets: tuple[PolymarketMarket, ...]

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(sorted({token_id for market in self.markets for token_id in market.token_ids}))


class ElectionUniverseSelector:
    """Select a narrow U.S.-election universe, with explicit manual overrides."""

    def __init__(
        self,
        *,
        include_ids: frozenset[str] = frozenset(),
        exclude_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.include_ids = include_ids
        self.exclude_ids = exclude_ids

    def select(self, markets: tuple[PolymarketMarket, ...]) -> UniverseSelection:
        selected = [market for market in markets if self._selected(market)]
        selected.sort(key=lambda market: (market.condition_id, market.market_id))
        return UniverseSelection(tuple(selected))

    def _selected(self, market: PolymarketMarket) -> bool:
        identities = {market.market_id, market.condition_id, *market.token_ids}
        if identities & self.exclude_ids:
            return False
        if identities & self.include_ids:
            return True
        if not market.active or market.closed:
            return False

        searchable = " ".join(
            part
            for part in (
                market.question,
                market.slug,
                market.event_title,
                market.event_slug,
                market.group_item_title,
                market.parent_event_slug,
            )
            if part
        ).lower()
        election_like = any(term in searchable for term in _ELECTION_TERMS)
        year_like = "2026" in searchable or (
            market.end_at is not None and market.end_at.year in {2026, 2027}
        )
        return election_like and year_like


def parse_id_csv(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())
