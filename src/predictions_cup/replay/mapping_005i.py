"""Load 005I research pairs only from the hash-bound MAPPING-001 acceptance ledger.

No arbitrary pair JSON or manually asserted ACCEPTED_EXACT flag can enable a
prospective PAPER run. The crosswalk is a reviewed semantic input, not a
new mapping generator.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
)
from predictions_cup.replay.economic_005i import ExactPair


def load_accepted_005i_pairs(
    root: Path, *, sig_exchange_ids: tuple[str, ...] | None = None
) -> tuple[ExactPair, ...]:
    base = root / "data" / "mappings"
    canonical = base / "sig_polymarket_2026.json"
    evidence = base / "sig_polymarket_2026_acceptance.json"
    if not canonical.is_file() or not evidence.is_file():
        raise ValueError("canonical accepted MAPPING-001 registry/evidence is missing")
    raw = canonical.read_bytes()
    attestation = json.loads(evidence.read_text())
    if not isinstance(attestation, dict):
        raise ValueError("malformed MAPPING-001 acceptance evidence")
    if attestation.get("all_records_verified") is not True:
        raise ValueError("MAPPING-001 registry not fully verified")
    if attestation.get("artifact_sha256", {}).get("crosswalk_json") != hashlib.sha256(
        raw
    ).hexdigest():
        raise ValueError("MAPPING-001 crosswalk digest mismatch")
    smoke = attestation.get("clob_smoke")
    if not isinstance(smoke, dict) or smoke.get("passed") is not True:
        raise ValueError("MAPPING-001 CLOB smoke has not passed")
    ids = smoke.get("mapped_token_ids")
    if not isinstance(ids, list) or (
        smoke.get("mapped_token_count") != len(ids)
        or smoke.get("books_returned") != len(ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("MAPPING-001 mapped-token smoke evidence is inconsistent")
    tokens = set(ids)
    document = MappingDocument.model_validate_json(raw)
    records = [record for record in document.records if record.mapping_class is MappingClass.EXACT]
    selected = set(sig_exchange_ids) if sig_exchange_ids is not None else None
    if selected is not None and len(selected) != len(sig_exchange_ids):
        raise ValueError("duplicate requested exchange ID")
    result: list[ExactPair] = []
    for record in records:
        if selected is not None and record.sig_exchange_id not in selected:
            continue
        direct = record.direct_polymarket
        if record.status is not MappingStatus.VERIFIED or direct is None:
            raise ValueError("EXACT pair lacks VERIFIED direct identity")
        if record.mapping_direction not in (
            MappingDirection.SAME, MappingDirection.COMPLEMENT
        ):
            raise ValueError("EXACT pair missing reviewed SAME/COMPLEMENT orientation")
        if direct.mapped_token_id not in tokens:
            raise ValueError("EXACT pair absent from mapped-token smoke evidence")
        result.append(
            ExactPair(
                market_id=direct.market_id,
                pm_yes=direct.mapped_token_id,
                sig_exchange_id=record.sig_exchange_id,
                sig_market_id=record.sig_market_id,
                mapping_direction=record.mapping_direction.value,
            )
        )
    if selected is not None and {p.sig_exchange_id for p in result} != selected:
        raise ValueError("requested exchange has no VERIFIED accepted EXACT map")
    if not result:
        raise ValueError("no accepted EXACT pairs passed the registry gate")
    if len({p.sig_exchange_id for p in result}) != len(result):
        raise ValueError("duplicate SIG exchange in accepted registry")
    return tuple(result)
