#!/usr/bin/env python3
"""Outcome-independent A1 role-quality audit for EXPERIMENT-005A."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from predictions_cup.learning.fee_role_data import (
    ROLE_EVIDENCE_COLUMNS,
    append_role_classification,
    enrich_fills_with_roles,
    load_event_fills,
    load_fee_family,
    load_infrastructure_addresses,
    role_counts,
)
from predictions_cup.learning.fee_role_structure import V2_START_UTC

ROOT = Path(__file__).resolve().parents[2]
EVENT_FAMILY = {
    "hungary_election": "HUN_2026",
    "peru_first_round": "PER_2026",
    "peru_runoff": "PER_2026",
    "colombia_first_round": "COL_2026",
    "colombia_runoff": "COL_2026",
}
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
FILL_COLUMNS = (
    "fill_id",
    "token_id",
    "market_id",
    "event_id",
    "outcome",
    "observed_at",
    "price",
    "size",
    "value_usd",
    "source_side",
    "maker_address",
    "taker_address",
    "transaction_hash",
    "log_index",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def windows() -> dict[str, dict[str, tuple[datetime, datetime]]]:
    payload = json.loads(
        (ROOT / "data/experiments/experiment_004a/regime_definitions.json").read_text()
    )
    result: dict[str, dict[str, tuple[datetime, datetime]]] = {}
    for event in payload["events"]:
        event_id = str(event["regime_id"])
        result[event_id] = {}
        for regime in event["regimes"]:
            name = str(regime["name"])
            if name not in REGIMES:
                continue
            result[event_id][name] = (dt(regime["start_utc"]), dt(regime["end_utc"]))
    return result


def _counts(values: pa.ChunkedArray) -> dict[str, int]:
    rows = pc.value_counts(values).to_pylist()
    return {str(row["values"]): int(row["counts"]) for row in rows}


def _fee_present(table: pa.Table) -> list[bool]:
    evidence = table["role_fee_evidence"].to_pylist()
    net = table["role_fee_net_usd_equiv"].to_pylist()
    return [
        bool(ev and str(ev).startswith("fee_charged") and value is not None and float(value) > 0)
        for ev, value in zip(evidence, net, strict=True)
    ]


def v2_cells(table: pa.Table) -> dict[str, int]:
    timestamps = table["role_timestamp"].to_pylist()
    order_role = table["role_order_is_match_taker_order"].to_pylist()
    fee = _fee_present(table)
    boundary = int(V2_START_UTC.timestamp())
    cells: Counter[str] = Counter()
    for timestamp, taker, has_fee in zip(timestamps, order_role, fee, strict=True):
        if timestamp is None or int(timestamp) < boundary or taker is None:
            continue
        label = ("TAKER" if bool(taker) else "NONTAKER") + ("_FEE" if has_fee else "_NO_FEE")
        cells[label] += 1
    return dict(cells)


def transaction_condition_audit(table: pa.Table) -> dict[str, int]:
    columns = [
        "transaction_hash",
        "role_condition_id",
        "maker_address",
        "role_order_is_match_taker_order",
    ]
    groups: dict[tuple[str, str], dict[str, set[str] | int]] = {}
    for row in table.select(columns).to_pylist():
        condition = row["role_condition_id"]
        participant = row["maker_address"]
        if condition is None or participant is None:
            continue
        key = (str(row["transaction_hash"]), str(condition))
        state = groups.setdefault(key, {"active": set(), "passive": set()})
        bucket = "active" if row["role_order_is_match_taker_order"] is True else "passive"
        cast = state[bucket]
        assert isinstance(cast, set)
        cast.add(str(participant).lower())

    result: Counter[str] = Counter()
    for state in groups.values():
        active = state["active"]
        passive = state["passive"]
        assert isinstance(active, set) and isinstance(passive, set)
        if len(active) == 1:
            result["groups_exactly_one_active_owner"] += 1
        elif len(active) == 0:
            result["groups_zero_active_owner"] += 1
        else:
            result["groups_multiple_active_owners"] += 1
        if len(active) == 1 and len(passive) >= 1:
            result["groups_one_active_plus_passive"] += 1
        if len(passive) > 1:
            result["groups_multiple_passive_owners"] += 1
    result["transaction_condition_groups"] = len(groups)
    return dict(result)


def paired_attribution_conflicts(table: pa.Table) -> dict[str, int]:
    columns = [
        "transaction_hash",
        "role_condition_id",
        "maker_address",
        "role_counterparty_address",
        "role_order_is_match_taker_order",
        "role_outcome_side",
        "role_price",
        "role_size_shares",
        "role_fee_net_usd_equiv",
    ]
    rows = table.select(columns).to_pylist()
    by_tx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_tx[str(row["transaction_hash"])].append(row)

    passive_fee = [
        row
        for row in rows
        if row["role_order_is_match_taker_order"] is False
        and float(row["role_fee_net_usd_equiv"] or 0) > 0
    ]
    matched = 0
    unique = 0
    paired_taker_no_fee = 0
    for passive in passive_fee:
        candidates = []
        for active in by_tx[str(passive["transaction_hash"])]:
            if active["role_order_is_match_taker_order"] is not True:
                continue
            if active["role_condition_id"] != passive["role_condition_id"]:
                continue
            if str(active["maker_address"]).lower() != str(
                passive["role_counterparty_address"]
            ).lower():
                continue
            if active["role_outcome_side"] == passive["role_outcome_side"]:
                continue
            if abs(float(active["role_price"]) + float(passive["role_price"]) - 1.0) > 1e-9:
                continue
            passive_size = float(passive["role_size_shares"])
            active_size = float(active["role_size_shares"])
            if abs(active_size - passive_size) > max(1e-6, abs(passive_size) * 1e-6):
                continue
            candidates.append(active)
        if candidates:
            matched += 1
        if len(candidates) == 1:
            unique += 1
            if float(candidates[0]["role_fee_net_usd_equiv"] or 0) <= 0:
                paired_taker_no_fee += 1
    return {
        "passive_rows_with_positive_fee": len(passive_fee),
        "complement_pair_match": matched,
        "unique_complement_pair_match": unique,
        "unique_pair_active_has_no_fee": paired_taker_no_fee,
    }


def unmatched_fee_legs_for_window(
    path: Path,
    *,
    start: datetime,
    end: datetime,
    token_ids: set[str],
) -> int:
    if not token_ids:
        return 0
    start_s = int(start.astimezone(UTC).timestamp())
    end_s = int(end.astimezone(UTC).timestamp())
    table = pq.read_table(
        path,
        columns=["event_timestamp", "token_id"],
        filters=[
            ("event_timestamp", ">=", start_s),
            ("event_timestamp", "<", end_s),
        ],
    )
    if table.num_rows == 0:
        return 0
    keep = pc.is_in(table["token_id"], value_set=pa.array(sorted(token_ids), pa.string()))
    return int(pc.sum(keep).as_py() or 0)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for name in row:
            if name not in fields:
                fields.append(name)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--fee-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    infra = load_infrastructure_addresses(
        ROOT / "data/reference/polymarket_infrastructure/infra_registry.json"
    )
    event_windows = windows()
    summary_rows: list[dict[str, Any]] = []
    class_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    v2_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    unmatched_path = args.fee_root / "unmatched" / "unattributed_fee_legs.parquet"

    for event, family in EVENT_FAMILY.items():
        for regime in REGIMES:
            start, end = event_windows[event][regime]
            fills = load_event_fills(
                args.corpus_root,
                event,
                start=start,
                end=end,
                columns=FILL_COLUMNS,
            )
            fee_rows = load_fee_family(
                args.fee_root,
                family,
                start=start,
                end=end,
                columns=ROLE_EVIDENCE_COLUMNS,
            )
            joined, audit = enrich_fills_with_roles(
                fills,
                fee_rows,
                infrastructure_addresses=infra,
            )
            classified = append_role_classification(joined)
            tokens = {str(value) for value in fills["token_id"].to_pylist() if value is not None}
            unmatched = unmatched_fee_legs_for_window(
                unmatched_path,
                start=start,
                end=end,
                token_ids=tokens,
            )
            common = {
                "event": event,
                "event_family": family,
                "regime": regime,
                "start_utc": start.isoformat(),
                "end_utc": end.isoformat(),
            }
            summary_rows.append(
                {
                    **common,
                    **audit.as_dict(),
                    "unattributed_fee_legs": unmatched,
                    "unique_participants": len(
                        {
                            str(value).lower()
                            for value in classified["maker_address"].to_pylist()
                            if value is not None
                        }
                    ),
                }
            )
            for label, count in sorted(role_counts(classified).items()):
                class_rows.append({**common, "role_class": label, "rows": count})
            for label, count in sorted(_counts(classified["role_fee_evidence"]).items()):
                evidence_rows.append({**common, "fee_evidence": label, "rows": count})
            for label, count in sorted(v2_cells(classified).items()):
                v2_rows.append({**common, "v2_cell": label, "rows": count})
            group_rows.append({**common, **transaction_condition_audit(classified)})
            pair_rows.append({**common, **paired_attribution_conflicts(classified)})

    write_csv(args.output / "join_audit.csv", summary_rows)
    write_csv(args.output / "role_class_counts.csv", class_rows)
    write_csv(args.output / "fee_evidence_counts.csv", evidence_rows)
    write_csv(args.output / "v2_agreement_cells.csv", v2_rows)
    write_csv(args.output / "transaction_condition_audit.csv", group_rows)
    write_csv(args.output / "paired_attribution_conflicts.csv", pair_rows)

    manifest: dict[str, Any] = {
        "experiment_id": "EXPERIMENT-005A-A1",
        "scope": "ROLE_QUALITY_ONLY_NO_PRICE_RESPONSE",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "data001_manifest_sha256": sha256(
                ROOT / "data/manifests/historical/data_001_corpus_manifest.json"
            ),
            "data002_manifest_sha256": sha256(
                ROOT / "data/manifests/fees/data_002_manifest.json"
            ),
            "infra_registry_sha256": sha256(
                ROOT / "data/reference/polymarket_infrastructure/infra_registry.json"
            ),
        },
        "outputs": {},
    }
    for path in sorted(args.output.glob("*.csv")):
        manifest["outputs"][path.name] = {
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
