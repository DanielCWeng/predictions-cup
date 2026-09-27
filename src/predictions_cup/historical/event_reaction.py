"""Descriptive market-reaction diagnostics for EXPERIMENT-004A.

The event clock is an input, never an output. These diagnostics may describe what
DATA-001 did around frozen factual anchors, but they cannot move those anchors or
select a trading rule.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import pyarrow.parquet as pq

SCHEMA_VERSION = 1
FIXED_HORIZON_HOURS = (0.25, 0.5, 1.0, 3.0, 6.0, 12.0, 24.0)


@dataclass(frozen=True, slots=True)
class SnapshotPoint:
    at: datetime
    midpoint: float
    spread: float | None
    top_bid_depth: float | None
    top_ask_depth: float | None


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive timestamp: {value}")
    return parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _top_depth(levels: Any) -> float | None:
    if not levels:
        return None
    first = levels[0]
    if not isinstance(first, dict):
        return None
    return _float(first.get("size"))


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    return math.log(clipped / (1 - clipped))


def _hours_after(start: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return round((value - start).total_seconds() / 3600.0, 6)


def _threshold_time(
    points: list[SnapshotPoint],
    *,
    start: datetime,
    side: str,
    probability: float,
) -> datetime | None:
    opposite = 1.0 - probability
    for point in points:
        if point.at < start:
            continue
        if side == "YES" and point.midpoint >= probability:
            return point.at
        if side == "NO" and point.midpoint <= opposite:
            return point.at
    return None


def _sustained_move_time(
    points: list[SnapshotPoint],
    *,
    start: datetime,
    side: str,
) -> datetime | None:
    """First 75/25 crossing whose later observed snapshots stay on the final side of 0.5."""
    eligible = [point for point in points if point.at >= start]
    if not eligible:
        return None

    values = [point.midpoint for point in eligible]
    suffix_min = [0.0] * len(values)
    suffix_max = [0.0] * len(values)
    running_min = math.inf
    running_max = -math.inf
    for idx in range(len(values) - 1, -1, -1):
        running_min = min(running_min, values[idx])
        running_max = max(running_max, values[idx])
        suffix_min[idx] = running_min
        suffix_max[idx] = running_max

    for idx, point in enumerate(eligible):
        if side == "YES" and point.midpoint >= 0.75 and suffix_min[idx] >= 0.5:
            return point.at
        if side == "NO" and point.midpoint <= 0.25 and suffix_max[idx] <= 0.5:
            return point.at
    return None


def _baseline(points: list[SnapshotPoint], start: datetime) -> tuple[SnapshotPoint | None, str]:
    before = [point for point in points if point.at <= start]
    if before:
        return before[-1], "AT_OR_BEFORE_RESULT_START"
    after = [point for point in points if point.at > start]
    if after:
        return after[0], "FIRST_AFTER_RESULT_START"
    return None, "NO_SNAPSHOT"


def _fixed_window_diagnostics(
    points: list[SnapshotPoint],
    *,
    start: datetime,
    corpus_end: datetime,
    baseline: SnapshotPoint | None,
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for hours in FIXED_HORIZON_HOURS:
        label = f"{int(hours * 60)}m" if hours < 1 else f"{int(hours)}h"
        end = min(start + timedelta(hours=hours), corpus_end)
        sample = [point for point in points if start <= point.at <= end]
        if baseline is None or not sample:
            result[f"max_abs_midpoint_change_{label}"] = None
            result[f"max_abs_logit_change_{label}"] = None
            continue
        result[f"max_abs_midpoint_change_{label}"] = round(
            max(abs(point.midpoint - baseline.midpoint) for point in sample),
            8,
        )
        base_logit = _logit(baseline.midpoint)
        result[f"max_abs_logit_change_{label}"] = round(
            max(abs(_logit(point.midpoint) - base_logit) for point in sample),
            8,
        )
    return result


def _summarize(
    points: list[SnapshotPoint],
    *,
    active_start: datetime,
    active_end: datetime,
    corpus_end: datetime,
) -> dict[str, Any]:
    points = sorted(points, key=lambda point: point.at)
    baseline, baseline_relation = _baseline(points, active_start)
    active_limit = min(active_end, corpus_end)
    active = [point for point in points if active_start <= point.at < active_limit]
    final = points[-1] if points else None

    side: str | None = None
    if final is not None:
        side = "YES" if final.midpoint >= 0.5 else "NO"

    first_90 = (
        None
        if side is None
        else _threshold_time(points, start=active_start, side=side, probability=0.90)
    )
    first_95 = (
        None
        if side is None
        else _threshold_time(points, start=active_start, side=side, probability=0.95)
    )
    first_99 = (
        None
        if side is None
        else _threshold_time(points, start=active_start, side=side, probability=0.99)
    )
    sustained = (
        None
        if side is None
        else _sustained_move_time(points, start=active_start, side=side)
    )

    spreads = [point.spread for point in active if point.spread is not None]
    bid_depth = [point.top_bid_depth for point in active if point.top_bid_depth is not None]
    ask_depth = [point.top_ask_depth for point in active if point.top_ask_depth is not None]

    result: dict[str, Any] = {
        "snapshot_count": len(points),
        "first_snapshot": _iso(points[0].at) if points else None,
        "last_snapshot": _iso(points[-1].at) if points else None,
        "first_midpoint": None if not points else round(points[0].midpoint, 8),
        "last_midpoint": None if final is None else round(final.midpoint, 8),
        "last_midpoint_side": side,
        "baseline_at_result_start": None if baseline is None else round(baseline.midpoint, 8),
        "baseline_timestamp": None if baseline is None else _iso(baseline.at),
        "baseline_relation": baseline_relation,
        "active_snapshot_count": len(active),
        "active_first_midpoint": None if not active else round(active[0].midpoint, 8),
        "active_last_midpoint": None if not active else round(active[-1].midpoint, 8),
        "active_min_midpoint": None if not active else round(min(p.midpoint for p in active), 8),
        "active_max_midpoint": None if not active else round(max(p.midpoint for p in active), 8),
        "active_first_logit": None if not active else round(_logit(active[0].midpoint), 8),
        "active_last_logit": None if not active else round(_logit(active[-1].midpoint), 8),
        "median_active_spread": None if not spreads else round(float(median(spreads)), 8),
        "median_active_top_bid_depth": (
            None if not bid_depth else round(float(median(bid_depth)), 8)
        ),
        "median_active_top_ask_depth": (
            None if not ask_depth else round(float(median(ask_depth)), 8)
        ),
        "first_90_toward_last_side": _iso(first_90),
        "hours_to_90_from_result_start": _hours_after(active_start, first_90),
        "first_95_toward_last_side": _iso(first_95),
        "hours_to_95_from_result_start": _hours_after(active_start, first_95),
        "first_99_toward_last_side": _iso(first_99),
        "hours_to_99_from_result_start": _hours_after(active_start, first_99),
        "first_sustained_75_toward_last_side": _iso(sustained),
        "hours_to_sustained_75_from_result_start": _hours_after(active_start, sustained),
        "effectively_one_sided_99_observed": first_99 is not None,
        "diagnostic_only": True,
        "used_to_choose_regime_boundary": False,
    }
    result.update(
        _fixed_window_diagnostics(
            points,
            start=active_start,
            corpus_end=corpus_end,
            baseline=baseline,
        )
    )
    return result


def scan_snapshot_reactions(
    corpus_parent: Path,
    timeline: dict[str, Any],
    *,
    batch_size: int = 65_536,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Scan exact DATA-001 depth snapshots and return per-token descriptive diagnostics."""
    schema_root = corpus_parent / f"schema_version={SCHEMA_VERSION}"
    points: dict[tuple[str, str], list[SnapshotPoint]] = defaultdict(list)
    event_index = {event["regime_id"]: event for event in timeline["events"]}

    for regime_id in sorted(event_index):
        base = schema_root / regime_id / "books" / "depth_snapshots"
        for path in sorted(base.glob("date=*/part-*.parquet")):
            parquet = pq.ParquetFile(path)
            columns = ["token_id", "recorded_at", "midpoint", "spread", "bids", "asks"]
            for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
                for row in batch.to_pylist():
                    midpoint = _float(row["midpoint"])
                    if midpoint is None:
                        continue
                    recorded_at = row["recorded_at"]
                    if not isinstance(recorded_at, datetime):
                        continue
                    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
                        raise ValueError(f"naive DATA-001 recorded_at in {path}")
                    points[(regime_id, row["token_id"])].append(
                        SnapshotPoint(
                            at=recorded_at.astimezone(UTC),
                            midpoint=midpoint,
                            spread=_float(row["spread"]),
                            top_bid_depth=_top_depth(row["bids"]),
                            top_ask_depth=_top_depth(row["asks"]),
                        )
                    )

    result: dict[tuple[str, str], dict[str, Any]] = {}
    for (regime_id, token_id), token_points in sorted(points.items()):
        event = event_index[regime_id]
        active_start = _dt(event["anchors"]["active_results_start"]["utc_timestamp"])
        active_end = _dt(event["anchors"]["active_results_end"]["utc_timestamp"])
        corpus_end = _dt(event["corpus_window_end_utc"])
        result[(regime_id, token_id)] = _summarize(
            token_points,
            active_start=active_start,
            active_end=active_end,
            corpus_end=corpus_end,
        )
    return result
