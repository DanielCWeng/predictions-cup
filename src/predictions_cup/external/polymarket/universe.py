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


class UniverseSelectionError(ValueError):
    """The requested explicit universe could not be resolved exactly."""


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    markets: tuple[PolymarketMarket, ...]
    explicit_token_ids: tuple[str, ...] | None = None

    @property
    def token_ids(self) -> tuple[str, ...]:
        if self.explicit_token_ids is not None:
            return self.explicit_token_ids
        return tuple(
            sorted({token_id for market in self.markets for token_id in market.token_ids})
        )


class ElectionUniverseSelector:
    """Select election markets, or an exact externally supplied supervised universe."""

    def __init__(
        self,
        *,
        include_ids: frozenset[str] = frozenset(),
        exclude_ids: frozenset[str] = frozenset(),
        strict_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.include_ids = include_ids
        self.exclude_ids = exclude_ids
        self.strict_ids = strict_ids

    def select(self, markets: tuple[PolymarketMarket, ...]) -> UniverseSelection:
        if self.strict_ids:
            return self._select_strict(markets)
        selected = [market for market in markets if self._selected(market)]
        selected.sort(key=lambda market: (market.condition_id, market.market_id))
        return UniverseSelection(tuple(selected))

    def _select_strict(self, markets: tuple[PolymarketMarket, ...]) -> UniverseSelection:
        selected_markets: list[PolymarketMarket] = []
        selected_tokens: set[str] = set()
        matched_ids: set[str] = set()

        for market in markets:
            if not market.active or market.closed:
                continue
            market_level = {market.market_id, market.condition_id}
            market_matches = market_level & self.strict_ids
            token_matches = set(market.token_ids) & self.strict_ids
            if not market_matches and not token_matches:
                continue

            selected_markets.append(market)
            matched_ids.update(market_matches)
            matched_ids.update(token_matches)
            if market_matches:
                selected_tokens.update(market.token_ids)
            else:
                selected_tokens.update(token_matches)

        unresolved = sorted(self.strict_ids - matched_ids)
        if unresolved:
            preview = ", ".join(unresolved[:5])
            suffix = "" if len(unresolved) <= 5 else f" (+{len(unresolved) - 5} more)"
            raise UniverseSelectionError(
                "strict supervised Polymarket IDs were not resolved from active Gamma markets: "
                f"{preview}{suffix}"
            )
        if not selected_markets or not selected_tokens:
            raise UniverseSelectionError("strict supervised Polymarket universe resolved empty")

        selected_markets.sort(key=lambda market: (market.condition_id, market.market_id))
        return UniverseSelection(
            tuple(selected_markets),
            tuple(sorted(selected_tokens)),
        )

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
