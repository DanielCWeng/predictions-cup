"""Translate Lane B (#220) PAPER rows into Lane C rejection-preserving episodes.

There is intentionally NO caller-facing switch to promote upstream trust booleans
into verified source provenance. Until independent #207 epoch/seed/receipt audit
and signed input attestations exist, ALL imported rows remain NO-TRADE-only.
This never reaches a venue or evaluates hypothetical positive PnL.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

D = Decimal
LANE_B_SCHEMA = "lane-b-paper-v1"


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _levels(raw: Any) -> list[list[str]]:
    """Do not reconstruct missing/invalid book depth or manufacture zeros."""
    if not isinstance(raw, list):
        return []
    levels: list[list[str]] = []
    for level in raw:
        if not isinstance(level, dict):
            return []
        try:
            p = D(str(level["price"]))
            q = D(str(level["size"]))
        except (InvalidOperation, KeyError, ValueError):
            return []
        if not p.is_finite() or not q.is_finite() or not D(0) < p < D(1) or q <= 0:
            return []
        levels.append([str(p), str(q)])
    return levels


def _book(row: dict[str, Any], stamp: str, name: str) -> dict[str, Any]:
    depth = row.get(name)
    if not isinstance(depth, dict):
        depth = {}
    return {
        "observed_at": stamp,
        "trusted": False,  # provenance cannot be established by the Lane B row alone
        "complete": False,
        "bids": _levels(depth.get("bids")),
        "asks": _levels(depth.get("asks")),
    }


def bridge_lane_b(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Never infer source trust or a real execution from an upstream PAPER label.

    Preserves every Lane B row, including all NO_TRADE observations, as one Lane C
    candidate episode plus an auditable source rejection record. No lookahead:
    received timestamps must not be later than decision; no synthetic forward
    arrivals or hypothetical future exits are created.
    """
    episodes: list[dict[str, Any]] = []
    source_ledger: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    seen: set[str] = set()
    for index, row in enumerate(rows):
        raw_digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode()
        ).hexdigest()
        lane_reason = str(row.get("reason") or "UNKNOWN")
        source_counts[lane_reason] = source_counts.get(lane_reason, 0) + 1
        at = _utc(row.get("decision_at"))
        sig_at = _utc(row.get("sig_received_at"))
        pm_at = _utc(row.get("pm_received_at"))
        sig_exch = _utc(row.get("sig_exchange_at"))
        pm_exch = _utc(row.get("pm_exchange_at"))
        schema_ok = row.get("schema_version") == LANE_B_SCHEMA
        identity_ok = all(
            isinstance(row.get(key), str) and bool(row[key])
            for key in ("sig_exchange_id", "pm_token_id", "mapping_direction")
        )
        mapping_ok = row.get("mapping_direction") in ("SAME", "COMPLEMENT")
        chronology_ok = (
            at is not None
            and all(t is not None and t <= at for t in (sig_at, pm_at, sig_exch, pm_exch))
            and sig_at is not None
            and pm_at is not None
            and sig_exch is not None
            and pm_exch is not None
            and sig_exch <= sig_at
            and pm_exch <= pm_at
        )
        fresh = bool(
            chronology_ok and at is not None and sig_at is not None and pm_at is not None
            and max((at - sig_at).total_seconds(), (at - pm_at).total_seconds()) <= 2
            and abs((sig_at - pm_at).total_seconds()) <= 2
        )
        sig_depth = row.get("sig_depth")
        pm_depth = row.get("pm_depth")
        ladders_present = (
            isinstance(sig_depth, dict) and isinstance(pm_depth, dict)
            and bool(_levels(sig_depth.get("bids")))
            and bool(_levels(sig_depth.get("asks")))
            and bool(_levels(pm_depth.get("bids")))
            and bool(_levels(pm_depth.get("asks")))
        )
        upstream_good = (
            schema_ok and identity_ok and mapping_ok and chronology_ok and fresh
            and ladders_present
            and row.get("sig_trust") is True and row.get("pm_trust") is True
            and row.get("decision") == "PAPER_COMPARABLE_NOT_EXECUTED"
            and lane_reason == "UNOBSERVED_FILL_AND_UNCALIBRATED_QUEUE"
            and row.get("observed_fill_qty") is None
            and row.get("observed_pnl") is None
        )
        if not schema_ok:
            gate_reason = "LANE_B_SCHEMA_UNVERIFIED"
        elif not identity_ok or not mapping_ok:
            gate_reason = "LANE_B_UNVERIFIED_MAPPING_IDENTITY"
        elif not chronology_ok:
            gate_reason = "LANE_B_CLOCK_CAUSALITY"
        elif not fresh:
            gate_reason = "LANE_B_STALE_OR_SKEWED_RECEIPTS"
        elif not ladders_present:
            gate_reason = "LANE_B_MISSING_EXECUTABLE_DEPTH"
        elif not upstream_good:
            gate_reason = "LANE_B_NO_TRADE_OR_UNTRUSTED"
        else:
            gate_reason = "SOURCE_EPOCH_SEED_AUDIT_NOT_YET_PROVEN"
        identity = (
            str(row.get("sequence")), str(row.get("sig_exchange_id")),
            str(row.get("side")), str(row.get("decision_at"))
        )
        dedupe = "|".join(identity)
        if dedupe in seen:
            gate_reason = "DUPLICATED_PAPER_EPISODE"
        seen.add(dedupe)
        source_ledger.append({
            "input_index": index,
            "lane_b_sequence": row.get("sequence"),
            "sig_exchange_id": row.get("sig_exchange_id"),
            "side": row.get("side"),
            "lane_b_decision": row.get("decision", "NO_TRADE"),
            "lane_b_reason": lane_reason,
            "source_gate": "NO_TRADE",
            "reason": gate_reason,
            "raw_row_sha256": raw_digest,
            "sig_trust_flag_not_independently_attested": row.get("sig_trust"),
            "pm_trust_flag_not_independently_attested": row.get("pm_trust"),
        })
        # Never elevate upstream "trusted=true" on a user-supplied row. Only an
        # external reviewed source/epoch attestation can permit actual candidate
        # economics; that workflow is intentionally absent from this adapter.
        if at is None or not identity_ok:
            continue
        stamp = at.isoformat()
        sig_stamp = sig_at.isoformat() if sig_at is not None else stamp
        episode = {
            "episode_id": "lane-b-" + raw_digest,
            "event_id": str(row.get("pm_event_id") or "UNVERIFIED_EVENT"),
            "sig_exchange_id": str(row["sig_exchange_id"]),
            "pm_token_id": str(row["pm_token_id"]),
            "mapping_direction": row["mapping_direction"],
            "decision_at": stamp,
            "feature_window_end": stamp,
            "features_available_at": stamp,
            "exact_mapping_verified": False,
            "paired_depth_trusted": False,
            "data_origin": "LANE_B_UNATTESTED_PAPER",
            "features": {},
            "decision_book": _book(row, sig_stamp, "sig_depth"),
            "arrivals": {},
            "exits": {},
            "lane_b_rejection_reason": gate_reason,
        }
        episodes.append(episode)
    return episodes, {
        "schema": "lane_c_lane_b_ingest_v1",
        "source_gate": "NO_TRADE_UNTIL_INDEPENDENT_SOURCE_ATTESTATION",
        "source_row_count": len(rows),
        "preserved_source_rejection_count": len(source_ledger),
        "converted_episode_count": len(episodes),
        "lane_b_reason_counts": source_counts,
        "source_ledger": source_ledger,
        "independently_qualified_depth_rows": None,
        "measured_real_executable_opportunities": None,
        "measured_net_pnl": None,
    }
