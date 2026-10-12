# ruff: noqa: E501
"""Read-only coverage inventory for existing SIG/Polymarket Parquet research shards.

This is a *data availability* audit, not proof of trustworthy executable books.
The PM Parquet rows omit reconnect/initialisation provenance; those cannot be
inferred from book_valid or timestamps. Never edit source capture or its WALs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from predictions_cup.research.paired_depth import InvalidInput, load_specs


def _iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    return None


def _scan(directory: Path, *, columns: tuple[str, ...], id_col: str,
          time_col: str, selected: set[str], sig: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts: dict[str, Any] = defaultdict(lambda: {
        "snapshot_rows": 0, "first_receipt": None, "last_receipt": None,
        "max_gap_s": None, "crossed_or_zero_count": 0,
        "untrusted_or_incomplete_count": 0, "source_age_max_s": None,
        "revisions_missing": 0, "provenance_unknown": 0,
    })
    files: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*.parquet")):
        try:
            parquet = pq.ParquetFile(path)
            available = set(parquet.schema_arrow.names)
            if not set(columns).issubset(available):
                files.append({"path": str(path), "error": "MISSING_REQUIRED_COLUMNS"})
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append({"path": str(path), "sha256": digest, "rows": parquet.metadata.num_rows})
            for batch in parquet.iter_batches(columns=list(columns), batch_size=2048):
                for row in batch.to_pylist():
                    if sig and row["event_type"] != "DEPTH_SNAPSHOT":
                        continue
                    ident = str(row[id_col])
                    if ident not in selected:
                        continue
                    at = _iso(row[time_col])
                    if at is None:
                        counts[ident]["untrusted_or_incomplete_count"] += 1
                        continue
                    rec = counts[ident]
                    rec["snapshot_rows"] += 1
                    prior = rec["last_receipt"]
                    if prior is not None and at > prior:
                        gap = (at - prior).total_seconds()
                        rec["max_gap_s"] = max(gap, rec["max_gap_s"] or 0)
                    if rec["first_receipt"] is None or at < rec["first_receipt"]:
                        rec["first_receipt"] = at
                    if prior is None or at > prior:
                        rec["last_receipt"] = at
                    if sig:
                        if row["evidence_label"] != "AUTHORITATIVE_DEPTH":
                            rec["untrusted_or_incomplete_count"] += 1
                        if row.get("revision") is None:
                            rec["revisions_missing"] += 1
                        try:
                            payload = json.loads(row["payload_json"])
                            bids = payload["bids"]
                            asks = payload["asks"]
                            bid = max(float(x["price"]) for x in bids)
                            ask = min(float(x["price"]) for x in asks)
                            if bid >= ask:
                                rec["crossed_or_zero_count"] += 1
                        except (ValueError, TypeError, KeyError):
                            rec["crossed_or_zero_count"] += 1
                    else:
                        # Neither PM book_valid nor snapshot writing attests
                        # uninterrupted WS events since the last HTTP seed.
                        rec["provenance_unknown"] += 1
                        bids = row["bids"] or []
                        asks = row["asks"] or []
                        if not bids or not asks:
                            rec["crossed_or_zero_count"] += 1
                        else:
                            try:
                                if max(float(x["price"]) for x in bids) >= min(
                                    float(x["price"]) for x in asks
                                ):
                                    rec["crossed_or_zero_count"] += 1
                            except (TypeError, KeyError, ValueError):
                                rec["crossed_or_zero_count"] += 1
                        state_at = _iso(row["state_observed_at"])
                        if state_at is None or state_at > at:
                            rec["untrusted_or_incomplete_count"] += 1
                        else:
                            age = (at - state_at).total_seconds()
                            rec["source_age_max_s"] = max(
                                age, rec["source_age_max_s"] or 0
                            )
        except (OSError, ValueError, RuntimeError) as exc:
            files.append({"path": str(path), "error": type(exc).__name__})
    for c in counts.values():
        for k in ("first_receipt", "last_receipt"):
            if c[k] is not None:
                c[k] = c[k].isoformat()
    return dict(counts), files


def audit(sig_root: Path, pm_root: Path, specs: dict) -> dict:
    sig, sig_files = _scan(
        sig_root / "normalized_events",
        columns=("exchange_id", "event_type", "observed_at", "evidence_label",
                 "revision", "payload_json"),
        id_col="exchange_id", time_col="observed_at",
        selected=set(specs), sig=True,
    )
    pm, pm_files = _scan(
        pm_root / "depth_snapshots",
        columns=("token_id", "recorded_at", "state_observed_at", "bids", "asks"),
        id_col="token_id", time_col="recorded_at",
        selected={v.token for v in specs.values()}, sig=False,
    )
    rows = []
    for s in specs.values():
        a = sig.get(s.exchange, {})
        b = pm.get(s.token, {})
        rows.append({
            "sig_exchange_id": s.exchange, "pm_token_id": s.token,
            "sig": a, "pm": b,
            "raw_depth_presence": bool(a.get("snapshot_rows") and b.get("snapshot_rows")),
            "trusted_paired_coverage": None,
            "prospective_hours": None,
            "empirical_net_pnl": None,
            "status": "BLOCKED_NEEDS_HEALTH_ATTESTATION_AND_DECISION_TIME_JOIN"
            if a.get("snapshot_rows") and b.get("snapshot_rows") else "BLOCKED_MISSING_DEPTH",
        })
    return {
        "schema_version": 1,
        "status": "PAPER_DATA_COVERAGE_ONLY",
        "not_validated": [
            "PM reconnect/initialisation coverage",
            "account-wide rate usage", "actual arrival-time fills",
            "cross-host clock synchronisation", "causal 24h paired capture"
        ],
        "files": {"sig": sig_files, "pm": pm_files},
        "markets": rows,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sig-research-root", required=True, type=Path)
    p.add_argument("--pm-research-root", required=True, type=Path)
    p.add_argument("--mapping", required=True, type=Path)
    p.add_argument("--acceptance", required=True, type=Path)
    p.add_argument("--exchange-ids", required=True)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    if a.output.exists() or a.output.resolve().is_relative_to(a.sig_research_root.resolve()) or (
        a.output.resolve().is_relative_to(a.pm_research_root.resolve())
    ):
        raise InvalidInput("write audit only to a NEW path outside both capture roots")
    specs = load_specs(a.mapping, a.acceptance, a.exchange_ids.split(","))
    result = audit(a.sig_research_root, a.pm_research_root, specs)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"markets": len(specs), "status": result["status"], "output": str(a.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
