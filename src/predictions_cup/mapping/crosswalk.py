"""Deterministic serialization, lookup, and summary helpers for MAPPING-001."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from predictions_cup.mapping.models import (
    MappingClass,
    MappingDocument,
    MappingStatus,
    MarketMapping,
    PolymarketContractIdentity,
)

CSV_FIELDS = (
    "sig_tournament_id",
    "sig_market_id",
    "sig_market_title",
    "sig_exchange_id",
    "sig_outcome_label",
    "mapping_class",
    "mapping_direction",
    "mapping_confidence",
    "status",
    "polymarket_market_id",
    "polymarket_condition_id",
    "polymarket_event_id",
    "polymarket_slug",
    "polymarket_question",
    "polymarket_outcomes",
    "polymarket_token_ids",
    "polymarket_outcome",
    "polymarket_token_id",
    "polymarket_components",
    "candidate_polymarket_market_ids",
    "semantic_notes",
    "resolution_notes",
)


def mapping_for_sig_exchange(exchange_id: str, document: MappingDocument) -> MarketMapping:
    """Return the single canonical mapping for a SIG exchange."""
    return document.mapping_for_sig_exchange(exchange_id)


def document_json(document: MappingDocument) -> str:
    payload = document.normalized().model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_document(path: Path) -> MappingDocument:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    return MappingDocument.model_validate(raw)


def write_document(path: Path, document: MappingDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document_json(document), encoding="utf-8")


def document_csv(document: MappingDocument) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for record in document.normalized().records:
        writer.writerow(_csv_row(record))
    return buffer.getvalue()


def write_csv(path: Path, document: MappingDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document_csv(document), encoding="utf-8")


def summary(document: MappingDocument) -> dict[str, int]:
    normalized = document.normalized()
    class_counts = Counter(record.mapping_class for record in normalized.records)
    direct = tuple(
        record.direct_polymarket
        for record in normalized.records
        if record.direct_polymarket is not None
    )
    all_contracts = tuple(
        identity
        for record in normalized.records
        for identity in _polymarket_identities(record)
    )
    return {
        "sig_markets": len({record.sig_market_id for record in normalized.records}),
        "sig_exchanges": len(normalized.records),
        "EXACT": class_counts[MappingClass.EXACT],
        "NEAR": class_counts[MappingClass.NEAR],
        "DERIVED": class_counts[MappingClass.DERIVED],
        "MODEL_ONLY": class_counts[MappingClass.MODEL_ONLY],
        "NO_TRADE": class_counts[MappingClass.NO_TRADE],
        "direct_polymarket_markets": len({identity.market_id for identity in direct}),
        "unique_cids": len({identity.condition_id for identity in all_contracts}),
        "unique_clob_token_ids": len(
            {token_id for identity in all_contracts for token_id in identity.token_ids}
        ),
        "unmapped_ambiguous_records": sum(
            record.status is not MappingStatus.VERIFIED
            or record.mapping_class in {MappingClass.MODEL_ONLY, MappingClass.NO_TRADE}
            for record in normalized.records
        ),
        "duplicate_conflicting_mappings": 0,
    }


def summary_json(document: MappingDocument) -> str:
    return json.dumps(summary(document), indent=2, sort_keys=True) + "\n"


def write_summary(path: Path, document: MappingDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary_json(document), encoding="utf-8")


def _polymarket_identities(record: MarketMapping) -> tuple[PolymarketContractIdentity, ...]:
    if record.direct_polymarket is not None:
        return (record.direct_polymarket,)
    return record.polymarket_components


def _compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _csv_row(record: MarketMapping) -> dict[str, str]:
    direct = record.direct_polymarket
    components = [
        component.model_dump(mode="json")
        for component in record.polymarket_components
    ]
    return {
        "sig_tournament_id": record.sig_tournament_id,
        "sig_market_id": record.sig_market_id,
        "sig_market_title": record.sig_market_title,
        "sig_exchange_id": record.sig_exchange_id,
        "sig_outcome_label": record.sig_outcome_label,
        "mapping_class": record.mapping_class.value,
        "mapping_direction": record.mapping_direction.value if record.mapping_direction else "",
        "mapping_confidence": str(record.mapping_confidence),
        "status": record.status.value,
        "polymarket_market_id": direct.market_id if direct else "",
        "polymarket_condition_id": direct.condition_id if direct else "",
        "polymarket_event_id": direct.event_id or "" if direct else "",
        "polymarket_slug": direct.slug or "" if direct else "",
        "polymarket_question": direct.question if direct else "",
        "polymarket_outcomes": _compact_json(list(direct.outcomes)) if direct else "",
        "polymarket_token_ids": _compact_json(list(direct.token_ids)) if direct else "",
        "polymarket_outcome": direct.mapped_outcome if direct else "",
        "polymarket_token_id": direct.mapped_token_id if direct else "",
        "polymarket_components": _compact_json(components) if components else "",
        "candidate_polymarket_market_ids": _compact_json(
            list(record.candidate_polymarket_market_ids)
        ),
        "semantic_notes": record.semantic_notes or "",
        "resolution_notes": record.resolution_notes or "",
    }
