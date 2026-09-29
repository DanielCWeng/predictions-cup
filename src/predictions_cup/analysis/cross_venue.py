"""Direct mapped SIG/Polymarket observable-time lag diagnostics for CAPTURE-001."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDirection


@dataclass(frozen=True, slots=True)
class _Point:
    at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class _Change:
    at: datetime
    delta: float


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p90": None, "p99": None}
    array = np.asarray(values, dtype=float)
    p50, p90, p99 = np.percentile(array, [50, 90, 99])
    return {"p50": float(p50), "p90": float(p90), "p99": float(p99)}


def _economic(points: list[_Point]) -> list[_Point]:
    if not points:
        return []
    result = [points[0]]
    for point in points[1:]:
        if point.value != result[-1].value:
            result.append(point)
    return result


def _changes(points: list[_Point]) -> list[_Change]:
    economic = _economic(points)
    return [
        _Change(right.at, right.value - left.value)
        for left, right in zip(economic, economic[1:], strict=False)
    ]


def _mid(row: dict[str, object]) -> float | None:
    bid = row.get("best_bid")
    ask = row.get("best_ask")
    if bid is None or ask is None:
        return None
    try:
        bid_value = float(str(bid))
        ask_value = float(str(ask))
    except ValueError:
        return None
    if ask_value < bid_value:
        return None
    return (bid_value + ask_value) / 2.0


def _load_sig_series(
    root: Path,
    exchange_ids: set[str],
) -> dict[str, list[_Point]]:
    files = sorted((root / "normalized_events").rglob("*.parquet"))
    if not files or not exchange_ids:
        return {}
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    required = {"event_type", "exchange_id", "observed_at", "best_bid", "best_ask"}
    if not required <= set(dataset.schema.names):
        return {}
    scanner = dataset.scanner(
        columns=["exchange_id", "observed_at", "best_bid", "best_ask"],
        filter=(
            (ds.field("event_type") == "BBO_SNAPSHOT")
            & ds.field("exchange_id").isin(sorted(exchange_ids))
        ),
        batch_size=65_536,
    )
    result: dict[str, list[_Point]] = defaultdict(list)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            exchange_id = row.get("exchange_id")
            observed = row.get("observed_at")
            value = _mid(row)
            if (
                isinstance(exchange_id, str)
                and isinstance(observed, datetime)
                and value is not None
            ):
                result[exchange_id].append(_Point(observed.astimezone(UTC), value))
    for values in result.values():
        values.sort(key=lambda item: item.at)
    return dict(result)


def _load_pm_series(
    root: Path,
    token_ids: set[str],
) -> dict[str, list[_Point]]:
    files = sorted((root / "observations").rglob("*.parquet"))
    if not files or not token_ids:
        return {}
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    required = {"token_id", "observed_at", "best_bid", "best_ask"}
    if not required <= set(dataset.schema.names):
        return {}
    scanner = dataset.scanner(
        columns=["token_id", "observed_at", "best_bid", "best_ask", "book_valid"],
        filter=ds.field("token_id").isin(sorted(token_ids)),
        batch_size=65_536,
    )
    result: dict[str, list[_Point]] = defaultdict(list)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            if row.get("book_valid") is False:
                continue
            token_id = row.get("token_id")
            observed = row.get("observed_at")
            value = _mid(row)
            if (
                isinstance(token_id, str)
                and isinstance(observed, datetime)
                and value is not None
            ):
                result[token_id].append(_Point(observed.astimezone(UTC), value))
    for values in result.values():
        values.sort(key=lambda item: item.at)
    return dict(result)


def _align_pm(points: list[_Point], direction: MappingDirection) -> list[_Point]:
    if direction is MappingDirection.SAME:
        return points
    if direction is MappingDirection.COMPLEMENT:
        return [_Point(point.at, 1.0 - point.value) for point in points]
    raise ValueError("direct cross-venue diagnostics require SAME or COMPLEMENT")


def _subsequent_lags(
    source_changes: list[_Change],
    target_changes: list[_Change],
    *,
    max_lag_seconds: float,
) -> tuple[list[float], int]:
    if not source_changes or not target_changes:
        return [], 0
    target_times = [item.at for item in target_changes]
    lags: list[float] = []
    same_direction = 0
    for source in source_changes:
        index = bisect.bisect_left(target_times, source.at)
        if index >= len(target_changes):
            continue
        target = target_changes[index]
        lag = (target.at - source.at).total_seconds()
        if lag < 0 or lag > max_lag_seconds:
            continue
        lags.append(lag)
        if source.delta * target.delta > 0:
            same_direction += 1
    return lags, same_direction


def analyze_direct_cross_venue(
    *,
    sig_root: Path,
    polymarket_root: Path | None,
    mapping_path: Path | None,
    max_lag_seconds: float = 60.0,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """Describe nearest subsequent economic changes without claiming causal lead/lag."""
    if (
        polymarket_root is None
        or mapping_path is None
        or not polymarket_root.exists()
        or not mapping_path.exists()
    ):
        return {"available": False}, []

    document = load_document(mapping_path)
    direct_records = [
        record
        for record in document.records
        if record.direct_polymarket is not None
        and record.mapping_direction
        in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
    ]
    exchange_ids = {record.sig_exchange_id for record in direct_records}
    token_ids = {
        record.direct_polymarket.mapped_token_id
        for record in direct_records
        if record.direct_polymarket is not None
    }
    sig_series = _load_sig_series(sig_root, exchange_ids)
    pm_series = _load_pm_series(polymarket_root, token_ids)

    rows: list[dict[str, object]] = []
    aggregate_pm_sig: list[float] = []
    aggregate_sig_pm: list[float] = []
    total_pm_sig_same = 0
    total_sig_pm_same = 0
    total_pm_sig_matches = 0
    total_sig_pm_matches = 0

    for record in direct_records:
        direct = record.direct_polymarket
        assert direct is not None
        direction = record.mapping_direction
        assert direction is not None
        sig_points = sig_series.get(record.sig_exchange_id, [])
        pm_raw = pm_series.get(direct.mapped_token_id, [])
        pm_points = _align_pm(pm_raw, direction)
        sig_changes = _changes(sig_points)
        pm_changes = _changes(pm_points)

        pm_to_sig, pm_to_sig_same = _subsequent_lags(
            pm_changes,
            sig_changes,
            max_lag_seconds=max_lag_seconds,
        )
        sig_to_pm, sig_to_pm_same = _subsequent_lags(
            sig_changes,
            pm_changes,
            max_lag_seconds=max_lag_seconds,
        )
        aggregate_pm_sig.extend(pm_to_sig)
        aggregate_sig_pm.extend(sig_to_pm)
        total_pm_sig_same += pm_to_sig_same
        total_sig_pm_same += sig_to_pm_same
        total_pm_sig_matches += len(pm_to_sig)
        total_sig_pm_matches += len(sig_to_pm)

        latest_sig = sig_points[-1] if sig_points else None
        latest_pm = pm_points[-1] if pm_points else None
        rows.append(
            {
                "sig_exchange_id": record.sig_exchange_id,
                "polymarket_token_id": direct.mapped_token_id,
                "mapping_class": record.mapping_class.value,
                "mapping_direction": direction.value,
                "sig_samples": len(sig_points),
                "pm_samples": len(pm_points),
                "sig_economic_changes": len(sig_changes),
                "pm_economic_changes": len(pm_changes),
                "pm_to_sig_matches": len(pm_to_sig),
                "pm_to_sig_lag_seconds_p50": _percentiles(pm_to_sig)["p50"],
                "pm_to_sig_lag_seconds_p90": _percentiles(pm_to_sig)["p90"],
                "pm_to_sig_same_direction_rate": (
                    None
                    if not pm_to_sig
                    else pm_to_sig_same / len(pm_to_sig)
                ),
                "sig_to_pm_matches": len(sig_to_pm),
                "sig_to_pm_lag_seconds_p50": _percentiles(sig_to_pm)["p50"],
                "sig_to_pm_lag_seconds_p90": _percentiles(sig_to_pm)["p90"],
                "sig_to_pm_same_direction_rate": (
                    None
                    if not sig_to_pm
                    else sig_to_pm_same / len(sig_to_pm)
                ),
                "latest_sig_mid": None if latest_sig is None else latest_sig.value,
                "latest_pm_aligned_mid": None if latest_pm is None else latest_pm.value,
                "latest_discrepancy": (
                    None
                    if latest_sig is None or latest_pm is None
                    else latest_sig.value - latest_pm.value
                ),
                "latest_sig_observed_at": (
                    None if latest_sig is None else latest_sig.at.isoformat()
                ),
                "latest_pm_observed_at": (
                    None if latest_pm is None else latest_pm.at.isoformat()
                ),
            }
        )

    summary: dict[str, Any] = {
        "available": True,
        "direct_mapping_count": len(direct_records),
        "max_lag_seconds": max_lag_seconds,
        "pm_to_sig_lag_seconds": _percentiles(aggregate_pm_sig),
        "sig_to_pm_lag_seconds": _percentiles(aggregate_sig_pm),
        "pm_to_sig_matches": total_pm_sig_matches,
        "sig_to_pm_matches": total_sig_pm_matches,
        "pm_to_sig_same_direction_rate": (
            None
            if total_pm_sig_matches == 0
            else total_pm_sig_same / total_pm_sig_matches
        ),
        "sig_to_pm_same_direction_rate": (
            None
            if total_sig_pm_matches == 0
            else total_sig_pm_same / total_sig_pm_matches
        ),
        "interpretation": (
            "Nearest subsequent economic BBO change within the declared window. "
            "This is an observable-time response-lag diagnostic, not causal evidence."
        ),
    }
    return summary, rows
