#!/usr/bin/env python3
"""Quantify EXPERIMENT-005B exposure to same-second pseudo-ordering.

Diagnostic only. The parent reconstruction is timestamp-sorted, so this uses a
single streaming pass and never modifies PR #45 evidence.
"""
# ruff: noqa: E731

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")


def empty_scope() -> dict[str, int]:
    return {
        "rows_same_second": 0,
        "rows_multi_tx_second": 0,
        "collision_groups": 0,
        "unique_tx_multi_second": 0,
    }


Row = tuple[str | None, str | None, str, int]


def rank_changed(rows: list[Row], scope: str) -> int:
    if scope == "family":
        groups = {None: rows}
        old_key = lambda row: (row[2], row[3], row[1] or "")
        new_key = lambda row: (row[3], row[2], row[1] or "")
    elif scope == "event":
        groups = defaultdict(list)
        for row in rows:
            groups[row[0]].append(row)
        old_key = lambda row: (row[2], row[3], row[1] or "")
        new_key = lambda row: (row[3], row[2], row[1] or "")
    elif scope == "market":
        groups = defaultdict(list)
        for row in rows:
            groups[row[1]].append(row)
        old_key = lambda row: (row[2], row[3])
        new_key = lambda row: (row[3], row[2])
    else:
        raise ValueError(scope)

    changed = 0
    for group in groups.values():
        old = sorted(group, key=old_key)
        new = sorted(group, key=new_key)
        changed += sum(a != b for a, b in zip(old, new, strict=True))
    return changed


def add_subgroups(
    rows: list[Row],
    index: int,
    stats: dict[str, int],
) -> None:
    groups: dict[str | None, list[str]] = defaultdict(list)
    for row in rows:
        groups[row[index]].append(row[2])
    for tx_rows in groups.values():
        n = len(tx_rows)
        if n > 1:
            stats["rows_same_second"] += n
        ntx = len(set(tx_rows))
        if ntx > 1:
            stats["rows_multi_tx_second"] += n
            stats["collision_groups"] += 1
            stats["unique_tx_multi_second"] += ntx


def flush_timestamp_group(
    rows: list[Row],
    family_stats: dict[str, int],
    event_stats: dict[str, int],
    market_stats: dict[str, int],
    rank_stats: dict[str, int],
) -> None:
    if not rows:
        return
    n = len(rows)
    if n > 1:
        family_stats["rows_same_second"] += n
    ntx = len({row[2] for row in rows})
    if ntx > 1:
        family_stats["rows_multi_tx_second"] += n
        family_stats["collision_groups"] += 1
        family_stats["unique_tx_multi_second"] += ntx
    log_indexes = [row[3] for row in rows]
    if len(log_indexes) != len(set(log_indexes)):
        raise RuntimeError(
            "duplicate log_index within one block timestamp; "
            "cannot establish block/log total order"
        )
    add_subgroups(rows, 0, event_stats)
    add_subgroups(rows, 1, market_stats)
    rank_stats["family"] += rank_changed(rows, "family")
    rank_stats["event"] += rank_changed(rows, "event")
    rank_stats["market"] += rank_changed(rows, "market")


def quantify_family(path: Path, family: str) -> dict[str, Any]:
    pf = pq.ParquetFile(path)
    total_rows = int(pf.metadata.num_rows)
    family_stats = empty_scope()
    event_stats = empty_scope()
    market_stats = empty_scope()

    current_ts: int | None = None
    current_rows: list[Row] = []
    rank_stats = {"family": 0, "event": 0, "market": 0}
    observed_rows = 0
    previous_ts: int | None = None

    for batch in pf.iter_batches(
        batch_size=131_072,
        columns=[
            "timestamp",
            "event_id",
            "condition_id",
            "tx_hash",
            "log_index",
        ],
    ):
        data = batch.to_pydict()
        for timestamp, event_id, condition_id, tx_hash, log_index in zip(
            data["timestamp"],
            data["event_id"],
            data["condition_id"],
            data["tx_hash"],
            data["log_index"],
            strict=True,
        ):
            if timestamp is None or tx_hash is None or log_index is None:
                raise RuntimeError(
                    f"{family}: null timestamp/tx_hash/log_index"
                )
            ts = int(timestamp)
            if previous_ts is not None and ts < previous_ts:
                raise RuntimeError(
                    f"{family}: parent reconstruction is not timestamp-sorted"
                )
            previous_ts = ts
            if current_ts is None:
                current_ts = ts
            if ts != current_ts:
                flush_timestamp_group(
                    current_rows,
                    family_stats,
                    event_stats,
                    market_stats,
                    rank_stats,
                )
                current_rows = []
                current_ts = ts
            current_rows.append(
                (
                    None if event_id is None else str(event_id),
                    None if condition_id is None else str(condition_id),
                    str(tx_hash),
                    int(log_index),
                )
            )
            observed_rows += 1
    flush_timestamp_group(
        current_rows,
        family_stats,
        event_stats,
        market_stats,
        rank_stats,
    )
    if observed_rows != total_rows:
        raise RuntimeError(
            f"{family}: streamed rows {observed_rows} != metadata {total_rows}"
        )

    row: dict[str, Any] = {"family": family, "total_rows": total_rows}
    for label, stats in (
        ("family", family_stats),
        ("event", event_stats),
        ("market", market_stats),
    ):
        row[f"rows_same_{label}_second"] = stats["rows_same_second"]
        row[f"rows_multi_tx_{label}_second"] = stats["rows_multi_tx_second"]
        row[f"collision_groups_{label}"] = stats["collision_groups"]
        row[f"unique_tx_multi_{label}_second"] = stats[
            "unique_tx_multi_second"
        ]
        row[f"rows_shared_{label}_block"] = stats["rows_same_second"]
        row[f"{label}_order_rank_changed_rows"] = rank_stats[label]
    return row


def quantify(root: Path) -> dict[str, object]:
    families: list[dict[str, Any]] = []
    for family in FAMILIES:
        row = quantify_family(
            root / f"canonical_trades_{family}.parquet",
            family,
        )
        families.append(row)
        print("FAMILY " + json.dumps(row, sort_keys=True), flush=True)

    totals: dict[str, int] = {
        "total_rows": sum(int(row["total_rows"]) for row in families)
    }
    for key in families[0]:
        if key not in {"family", "total_rows"}:
            totals[key] = sum(int(row[key]) for row in families)
    return {
        "schema_version": 1,
        "experiment_id": "EXPERIMENT-005B-ORDERING-FALSIFICATION",
        "diagnostic": "same_second_collision_exposure",
        "families": families,
        "totals": totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = quantify(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("TOTAL " + json.dumps(result["totals"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
