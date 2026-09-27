"""Diagnostic V1/V2 overlap audit for the PMXT 2026-04-13T19 hour (reviewer evidence only).

Normalizes the PMXT V1 and PMXT V2 extracts of the same archive hour with the corpus's own
``pmxt.normalize_extract`` over the window where both versions recorded (the raw V2 file's
first received row to the end of the hour), then compares coverage, counts, timestamp density
and as-of BBO states per token. Nothing here feeds the corpus: no stitching, no choosing rows.

Usage:
    python scripts/data_001_overlap_audit.py --v1-extracts <pmxt_extracts dir> \
        --v2-extracts <moved-aside V2 dir holding <FAMILY>/events.parquet> \
        --fills <polyleviathan dir> --output <json>
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.historical import pmxt
from predictions_cup.historical.corpus import load_candidates

HOUR = datetime(2026, 4, 13, 19, tzinfo=UTC)
# First received row of the raw V2 2026-04-13T19 archive file.
OVERLAP_START = datetime(2026, 4, 13, 19, 42, 26, 600_000, tzinfo=UTC)
OVERLAP_END = HOUR + timedelta(hours=1)
FAMILIES = ("PER_2026", "HUN_2026")


def _bbo_states(tables: dict[str, pa.Table]) -> dict[str, list[tuple[datetime, str, str]]]:
    """token -> [(observed_at, best_bid, best_ask)] from snapshots and BBO changes."""
    states: dict[str, list[tuple[datetime, str, str]]] = {}
    for stream, time_field in (("depth_snapshots", "recorded_at"), ("book_changes", "observed_at")):
        table = tables[stream]
        for token, at, bid, ask in zip(
            table["token_id"].to_pylist(),
            table[time_field].to_pylist(),
            table["best_bid"].to_pylist(),
            table["best_ask"].to_pylist(),
            strict=True,
        ):
            states.setdefault(token, []).append((at, bid or "", ask or ""))
    for rows in states.values():
        rows.sort(key=lambda row: row[0])
    return states


def _as_of(rows: list[tuple[datetime, str, str]], mark: datetime) -> tuple[str, Any]:
    """Latest BBO at or before ``mark`` (backward only). Same-instant disagreement = ambiguous."""
    times = [row[0] for row in rows]
    index = bisect.bisect_right(times, mark)
    if index == 0:
        return "none", None
    instant = rows[index - 1][0]
    first = bisect.bisect_left(times, instant)
    same = {(bid, ask) for _, bid, ask in rows[first:index]}
    if len(same) > 1:
        return "ambiguous", None
    return "state", same.pop()


def _tokens(tables: dict[str, pa.Table]) -> set[str]:
    return {
        token
        for stream in ("depth_snapshots", "book_changes", "trades")
        for token in tables[stream]["token_id"].to_pylist()
    }


def _conditions(tables: dict[str, pa.Table]) -> set[str]:
    return {
        cid
        for stream in ("depth_snapshots", "book_changes", "trades")
        for cid in tables[stream]["market_id"].to_pylist()
    }


def _density(tables: dict[str, pa.Table], tokens: set[str]) -> dict[str, Any]:
    minutes = (OVERLAP_END - OVERLAP_START).total_seconds() / 60
    instants: dict[str, set[datetime]] = {token: set() for token in tokens}
    for stream, time_field in (("depth_snapshots", "recorded_at"), ("book_changes", "observed_at")):
        table = tables[stream]
        for token, at in zip(
            table["token_id"].to_pylist(), table[time_field].to_pylist(), strict=True
        ):
            if token in instants:
                instants[token].add(at)
    per_minute = sorted(len(v) / minutes for v in instants.values())
    if not per_minute:
        return {"tokens": 0}
    return {
        "tokens": len(per_minute),
        "median_distinct_instants_per_token_minute": round(statistics.median(per_minute), 3),
        "mean_distinct_instants_per_token_minute": round(statistics.fmean(per_minute), 3),
        "max_distinct_instants_per_token_minute": round(per_minute[-1], 3),
        "total_distinct_token_instants": sum(len(v) for v in instants.values()),
    }


def audit_family(v1_path: Path, v2_path: Path, fills_root: Path, family: str) -> dict[str, Any]:
    candidates, _ = load_candidates(fills_root, family)
    token_ids = frozenset(c.token_id for c in candidates)
    out: dict[str, dict[str, pa.Table]] = {}
    raw_rows: dict[str, int] = {}
    for version, path in (("PMXT_V1", v1_path), ("PMXT_V2", v2_path)):
        table = pq.read_table(path)
        raw_rows[version] = table.num_rows
        normalized = pmxt.normalize_extract(
            table,
            source_version=version,
            token_ids=token_ids,
            window_start=OVERLAP_START,
            window_end=OVERLAP_END,
        )
        out[version] = {
            stream: pmxt.sort_and_dedupe(stream, normalized.tables[stream])[0]
            for stream in ("depth_snapshots", "book_changes", "trades")
        }
    v1, v2 = out["PMXT_V1"], out["PMXT_V2"]

    tokens1, tokens2 = _tokens(v1), _tokens(v2)
    cond1, cond2 = _conditions(v1), _conditions(v2)
    bbo1, bbo2 = _bbo_states(v1), _bbo_states(v2)
    both_bbo = sorted(set(bbo1) & set(bbo2))

    marks = []
    mark = OVERLAP_START.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while mark < OVERLAP_END:
        marks.append(mark)
        mark += timedelta(minutes=1)
    marks.append(OVERLAP_END - timedelta(milliseconds=1))

    alignment = {
        "minute_marks": len(marks),
        "token_marks_compared": 0,
        "agree": 0,
        "disagree": 0,
        "disagree_bid_only": 0,
        "disagree_ask_only": 0,
        "disagree_both_sides": 0,
        "v1_state_only": 0,
        "v2_state_only": 0,
        "neither_state": 0,
        "ambiguous_same_instant": 0,
        "tokens_with_any_disagreement": 0,
    }
    for token in both_bbo:
        disagreed = False
        for mark in marks:
            kind1, state1 = _as_of(bbo1[token], mark)
            kind2, state2 = _as_of(bbo2[token], mark)
            if "ambiguous" in (kind1, kind2):
                alignment["ambiguous_same_instant"] += 1
            elif kind1 == "none" and kind2 == "none":
                alignment["neither_state"] += 1
            elif kind2 == "none":
                alignment["v1_state_only"] += 1
            elif kind1 == "none":
                alignment["v2_state_only"] += 1
            else:
                alignment["token_marks_compared"] += 1
                if state1 == state2:
                    alignment["agree"] += 1
                else:
                    disagreed = True
                    alignment["disagree"] += 1
                    bid_differs = state1[0] != state2[0]
                    ask_differs = state1[1] != state2[1]
                    if bid_differs and ask_differs:
                        alignment["disagree_both_sides"] += 1
                    elif bid_differs:
                        alignment["disagree_bid_only"] += 1
                    else:
                        alignment["disagree_ask_only"] += 1
        alignment["tokens_with_any_disagreement"] += int(disagreed)

    def counts(tables: dict[str, pa.Table]) -> dict[str, int]:
        return {stream: tables[stream].num_rows for stream in tables}

    def first_last(tables: dict[str, pa.Table]) -> dict[str, str | None]:
        times = [
            t
            for stream, field in (
                ("depth_snapshots", "recorded_at"),
                ("book_changes", "observed_at"),
                ("trades", "observed_at"),
            )
            for t in tables[stream][field].to_pylist()
        ]
        if not times:
            return {"first": None, "last": None}
        return {"first": min(times).isoformat(), "last": max(times).isoformat()}

    snap1 = set(v1["depth_snapshots"]["token_id"].to_pylist())
    snap2 = set(v2["depth_snapshots"]["token_id"].to_pylist())
    return {
        "raw_extract_rows_whole_hour": raw_rows,
        "rows_in_overlap": {"PMXT_V1": counts(v1), "PMXT_V2": counts(v2)},
        "observed_span_in_overlap": {"PMXT_V1": first_last(v1), "PMXT_V2": first_last(v2)},
        "tokens": {
            "v1_only": len(tokens1 - tokens2),
            "v2_only": len(tokens2 - tokens1),
            "both": len(tokens1 & tokens2),
        },
        "conditions": {
            "v1_only": len(cond1 - cond2),
            "v2_only": len(cond2 - cond1),
            "both": len(cond1 & cond2),
        },
        "v2_only_conditions": sorted(cond2 - cond1),
        "v1_only_conditions": sorted(cond1 - cond2),
        "tokens_with_bbo_observation": {
            "PMXT_V1": len(bbo1),
            "PMXT_V2": len(bbo2),
            "both": len(both_bbo),
        },
        "tokens_with_depth_snapshot": {
            "PMXT_V1": len(snap1),
            "PMXT_V2": len(snap2),
            "both": len(snap1 & snap2),
        },
        "timestamp_density_all_tokens": {
            "PMXT_V1": _density(v1, set(bbo1)),
            "PMXT_V2": _density(v2, set(bbo2)),
        },
        "timestamp_density_shared_tokens": {
            "PMXT_V1": _density(v1, set(both_bbo)),
            "PMXT_V2": _density(v2, set(both_bbo)),
        },
        "asof_bbo_alignment_shared_tokens": alignment,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-extracts", type=Path, required=True)
    parser.add_argument("--v2-extracts", type=Path, required=True)
    parser.add_argument("--fills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pa.set_memory_pool(pa.system_memory_pool())
    report: dict[str, Any] = {
        "purpose": "diagnostic only; not used by the corpus build",
        "hour": f"{HOUR:%Y-%m-%dT%H}",
        "overlap_window": [OVERLAP_START.isoformat(), OVERLAP_END.isoformat()],
        "alignment_rule": "latest BBO at or before each minute mark (backward as-of), using only "
        "rows inside the overlap window; marks are whole minutes plus the last ms of the hour",
        "families": {},
    }
    for family in FAMILIES:
        v1_path = (
            args.v1_extracts / family / f"date={HOUR:%Y-%m-%d}" / f"hour={HOUR:%H}"
            / "events.parquet"
        )
        v2_path = args.v2_extracts / family / "events.parquet"
        report["families"][family] = audit_family(v1_path, v2_path, args.fills, family)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
