"""Live SIG/Polymarket crosswalk generation for MAPPING-001.

Candidate generation is deliberately non-authoritative. Only explicit reviewer-owned overrides can
create direct or derived Polymarket token mappings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.config import load_settings
from predictions_cup.external.polymarket.client import ClobMarketDataClient
from predictions_cup.external.polymarket.gamma import GammaClient
from predictions_cup.external.polymarket.models import PolymarketMarket
from predictions_cup.external.polymarket.universe import ElectionUniverseSelector
from predictions_cup.mapping.acceptance import write_acceptance_evidence
from predictions_cup.mapping.crosswalk import (
    summary_json,
    write_csv,
    write_document,
    write_summary,
)
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDocument,
    MappingOverride,
    MappingOverrideDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)
from predictions_cup.sig.client import SigRestClient
from predictions_cup.sig.dto import MarketNodeDto

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TERMS = frozenset(
    {
        "a",
        "after",
        "and",
        "election",
        "in",
        "of",
        "party",
        "race",
        "the",
        "us",
        "u",
        "s",
        "will",
        "win",
        "yes",
        "no",
    }
)


@dataclass(frozen=True, slots=True)
class SigExchangeSnapshot:
    tournament_id: str
    market_id: str
    market_title: str
    exchange_id: str
    outcome_label: str
    semantic_text: str


async def collect_sig_universe(
    client: SigRestClient,
    tournament_id: str,
) -> tuple[SigExchangeSnapshot, ...]:
    """Enumerate the explicit live tournament universe and inspect every market-node tree."""
    markets = {
        market.id: market
        async for market in client.iter_markets(
            tournament_id=tournament_id,
            status="any",
        )
    }
    if not markets:
        raise ValueError("SIG returned no markets for the requested tournament")

    node_text: dict[str, str] = {}
    for market_id in sorted(markets):
        nodes = await client.get_market_nodes(market_id, tournament_id=tournament_id)
        node_text[market_id] = _node_semantic_text(nodes.root)

    snapshots: list[SigExchangeSnapshot] = []
    seen_exchange_ids: set[str] = set()
    async for exchange in client.iter_exchanges(tournament_id=tournament_id):
        if exchange.id in seen_exchange_ids:
            raise ValueError(f"SIG returned duplicate exchange_id {exchange.id!r}")
        seen_exchange_ids.add(exchange.id)
        market = markets.get(exchange.market_id)
        if market is None:
            raise ValueError(
                f"SIG exchange {exchange.id!r} references undiscovered market "
                f"{exchange.market_id!r}"
            )
        if exchange.option is None or not exchange.option.strip():
            raise ValueError(f"SIG exchange {exchange.id!r} has no usable outcome label")
        snapshots.append(
            SigExchangeSnapshot(
                tournament_id=tournament_id,
                market_id=exchange.market_id,
                market_title=market.title,
                exchange_id=exchange.id,
                outcome_label=exchange.option.strip(),
                semantic_text=node_text[exchange.market_id],
            )
        )

    if not snapshots:
        raise ValueError("SIG returned no exchanges for the requested tournament")
    return tuple(sorted(snapshots, key=lambda item: (item.market_id, item.exchange_id)))


def candidate_market_ids(
    sig_exchange: SigExchangeSnapshot,
    polymarket_markets: Iterable[PolymarketMarket],
    *,
    limit: int = 8,
) -> tuple[str, ...]:
    """Return deterministic review candidates; never infer a mapping class from this score."""
    if limit < 1:
        raise ValueError("candidate limit must be positive")
    sig_terms = _terms(
        f"{sig_exchange.market_title} {sig_exchange.outcome_label} {sig_exchange.semantic_text}"
    )
    scored: list[tuple[Decimal, str, str]] = []
    for market in polymarket_markets:
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
        )
        poly_terms = _terms(searchable)
        if not sig_terms or not poly_terms:
            continue
        overlap = sig_terms & poly_terms
        if len(overlap) < 2:
            continue
        score = Decimal(len(overlap)) / Decimal(len(sig_terms | poly_terms))
        scored.append((score, market.condition_id, market.market_id))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(market_id for _, _, market_id in scored[:limit])


def build_mapping_document(
    tournament_id: str,
    sig_exchanges: Iterable[SigExchangeSnapshot],
    polymarket_markets: Iterable[PolymarketMarket],
    overrides: MappingOverrideDocument,
) -> MappingDocument:
    """Build a fail-closed document from live metadata plus explicit semantic overrides."""
    if overrides.tournament_id != tournament_id:
        raise ValueError("override tournament_id does not match live SIG tournament_id")

    sig_rows = tuple(sig_exchanges)
    exchange_ids = tuple(row.exchange_id for row in sig_rows)
    if len(exchange_ids) != len(set(exchange_ids)):
        raise ValueError("live SIG universe contains duplicate exchange IDs")

    poly_rows = tuple(polymarket_markets)
    poly_by_id = _unique_polymarket_index(poly_rows)
    override_by_exchange = {record.sig_exchange_id: record for record in overrides.records}

    records: list[MarketMapping] = []
    used_overrides: set[str] = set()
    for sig_exchange in sig_rows:
        candidate_ids = candidate_market_ids(sig_exchange, poly_rows)
        override = override_by_exchange.get(sig_exchange.exchange_id)
        if override is None:
            records.append(
                MarketMapping(
                    sig_tournament_id=tournament_id,
                    sig_market_id=sig_exchange.market_id,
                    sig_market_title=sig_exchange.market_title,
                    sig_exchange_id=sig_exchange.exchange_id,
                    sig_outcome_label=sig_exchange.outcome_label,
                    mapping_class=MappingClass.NO_TRADE,
                    mapping_direction=None,
                    mapping_confidence=Decimal("0"),
                    status=MappingStatus.UNRESOLVED,
                    direct_polymarket=None,
                    polymarket_components=(),
                    candidate_polymarket_market_ids=candidate_ids,
                    semantic_notes=(
                        "No explicit reviewed mapping override; candidate similarity is not "
                        "semantic proof."
                    ),
                    resolution_notes=None,
                )
            )
            continue

        used_overrides.add(override.sig_exchange_id)
        _validate_override_against_sig(override, sig_exchange)
        identities = tuple(
            _identity_from_market(poly_by_id[leg.market_id], leg.outcome)
            for leg in override.polymarket_legs
        )
        direct = (
            identities[0]
            if override.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}
            else None
        )
        components = identities if override.mapping_class is MappingClass.DERIVED else ()
        records.append(
            MarketMapping(
                sig_tournament_id=tournament_id,
                sig_market_id=sig_exchange.market_id,
                sig_market_title=sig_exchange.market_title,
                sig_exchange_id=sig_exchange.exchange_id,
                sig_outcome_label=sig_exchange.outcome_label,
                mapping_class=override.mapping_class,
                mapping_direction=override.mapping_direction,
                mapping_confidence=override.mapping_confidence,
                status=override.status,
                direct_polymarket=direct,
                polymarket_components=components,
                candidate_polymarket_market_ids=candidate_ids,
                semantic_notes=override.semantic_notes,
                resolution_notes=override.resolution_notes,
            )
        )

    unused = set(override_by_exchange) - used_overrides
    if unused:
        raise ValueError(f"mapping overrides reference absent SIG exchanges: {sorted(unused)!r}")

    return MappingDocument(tournament_id=tournament_id, records=tuple(records)).normalized()


def load_overrides(path: Path | None, tournament_id: str) -> MappingOverrideDocument:
    if path is None:
        return MappingOverrideDocument(tournament_id=tournament_id)
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    return MappingOverrideDocument.model_validate(raw)


async def generate_live_crosswalk(
    *,
    tournament_id: str,
    overrides_path: Path | None,
    json_path: Path,
    csv_path: Path,
    summary_path: Path,
    smoke_clob: bool,
    acceptance_evidence_path: Path | None = None,
) -> MappingDocument:
    settings = load_settings()
    if settings.sig_read_credential is None:
        raise ValueError("PREDICTIONS_CUP_SIG_READ_CREDENTIAL is required for live mapping")

    async with SigRestClient(settings) as sig_client:
        sig_exchanges = await collect_sig_universe(sig_client, tournament_id)

    discovery = await GammaClient(
        str(settings.polymarket_gamma_base_url),
        page_limit=settings.polymarket_gamma_page_limit,
    ).discover_active_markets()
    if discovery.parse_failures:
        raise ValueError(
            f"Gamma discovery had {discovery.parse_failures} parse failures; "
            "refusing incomplete identity mapping"
        )

    overrides = load_overrides(overrides_path, tournament_id)
    override_market_ids = frozenset(
        leg.market_id for record in overrides.records for leg in record.polymarket_legs
    )
    configured_include_ids = frozenset(
        part.strip() for part in settings.polymarket_include_ids.split(",") if part.strip()
    )
    polymarket_markets = ElectionUniverseSelector(
        include_ids=configured_include_ids | override_market_ids,
        exclude_ids=frozenset(
            part.strip() for part in settings.polymarket_exclude_ids.split(",") if part.strip()
        ),
    ).select(discovery.markets).markets

    document = build_mapping_document(
        tournament_id,
        sig_exchanges,
        polymarket_markets,
        overrides,
    )
    write_document(json_path, document)
    write_csv(csv_path, document)
    write_summary(summary_path, document)

    token_ids: tuple[str, ...] = ()
    books_returned = 0
    if smoke_clob:
        token_ids = _verified_mapped_token_ids(document)
        if token_ids:
            books = await ClobMarketDataClient(
                str(settings.polymarket_clob_base_url)
            ).fetch_books(token_ids)
            books_returned = len(books)
            if books_returned != len(token_ids):
                raise ValueError(
                    "Polymarket CLOB smoke test returned a different number of books than tokens"
                )

    if acceptance_evidence_path is not None:
        write_acceptance_evidence(
            acceptance_evidence_path,
            document=document,
            overrides=overrides,
            overrides_path=overrides_path,
            json_path=json_path,
            csv_path=csv_path,
            summary_path=summary_path,
            smoke_requested=smoke_clob,
            mapped_token_ids=token_ids,
            books_returned=books_returned,
        )
    return document


def _unique_polymarket_index(
    markets: tuple[PolymarketMarket, ...],
) -> dict[str, PolymarketMarket]:
    result: dict[str, PolymarketMarket] = {}
    for market in markets:
        if market.market_id in result:
            raise ValueError(f"duplicate Polymarket market_id {market.market_id!r}")
        result[market.market_id] = market
    return result


def _identity_from_market(
    market: PolymarketMarket,
    outcome: str,
) -> PolymarketContractIdentity:
    matches = tuple(token for token in market.tokens() if token.outcome == outcome)
    if len(matches) != 1:
        raise ValueError(
            f"Polymarket market {market.market_id!r} does not contain exactly one "
            f"outcome {outcome!r}"
        )
    token = matches[0]
    return PolymarketContractIdentity(
        market_id=market.market_id,
        condition_id=market.condition_id,
        event_id=market.event_id,
        slug=market.slug,
        question=market.question,
        outcomes=market.outcomes,
        token_ids=market.token_ids,
        mapped_outcome=token.outcome,
        mapped_token_id=token.token_id,
    )


def _validate_override_against_sig(
    override: MappingOverride,
    sig_exchange: SigExchangeSnapshot,
) -> None:
    if override.sig_market_id != sig_exchange.market_id:
        raise ValueError(
            f"override market mismatch for SIG exchange {sig_exchange.exchange_id!r}"
        )
    if override.sig_outcome_label != sig_exchange.outcome_label:
        raise ValueError(
            f"override outcome mismatch for SIG exchange {sig_exchange.exchange_id!r}"
        )


def _verified_mapped_token_ids(document: MappingDocument) -> tuple[str, ...]:
    token_ids: set[str] = set()
    for record in document.records:
        if record.status is not MappingStatus.VERIFIED:
            continue
        identities = (
            (record.direct_polymarket,)
            if record.direct_polymarket is not None
            else record.polymarket_components
        )
        for identity in identities:
            if identity is not None:
                token_ids.add(identity.mapped_token_id)
    return tuple(sorted(token_ids))


def _node_semantic_text(node: MarketNodeDto) -> str:
    parts: list[str] = []
    if node.title:
        parts.append(node.title)
    if node.contract_type:
        parts.append(node.contract_type)
    if node.contract_details:
        parts.append(json.dumps(node.contract_details, sort_keys=True, default=str))
    if node.settlement_options:
        parts.extend(node.settlement_options)
    if node.children:
        parts.extend(_node_semantic_text(child) for child in node.children)
    return " ".join(parts)


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        term
        for term in _TOKEN_RE.findall(value.lower())
        if term not in _STOP_TERMS and len(term) > 1
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the live SIG ↔ Polymarket crosswalk")
    parser.add_argument("--tournament-id", help="Explicit SIG tournament ID")
    parser.add_argument(
        "--overrides",
        type=Path,
        help="Reviewer-owned semantic mapping overrides JSON",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("data/mappings/sig_polymarket_2026.json"),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/mappings/sig_polymarket_2026.csv"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/mappings/sig_polymarket_2026_summary.json"),
    )
    parser.add_argument(
        "--smoke-clob",
        action="store_true",
        help="Fetch mapped verified token books through the existing CLOB REST client",
    )
    parser.add_argument(
        "--acceptance-evidence",
        type=Path,
        help=(
            "Write deterministic live acceptance evidence after reviewer overrides, "
            "verification, and CLOB smoke all pass"
        ),
    )
    parser.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit non-zero if any mapping remains non-VERIFIED",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    settings = load_settings()
    tournament_id = args.tournament_id or settings.tournament_id
    if tournament_id is None:
        raise ValueError(
            "explicit tournament context is required via --tournament-id or "
            "PREDICTIONS_CUP_TOURNAMENT_ID"
        )

    document = await generate_live_crosswalk(
        tournament_id=tournament_id,
        overrides_path=args.overrides,
        json_path=args.json,
        csv_path=args.csv,
        summary_path=args.summary,
        smoke_clob=args.smoke_clob,
        acceptance_evidence_path=args.acceptance_evidence,
    )
    print(summary_json(document), end="")
    if args.require_verified and any(
        record.status is not MappingStatus.VERIFIED for record in document.records
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
