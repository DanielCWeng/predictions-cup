"""Genuine quote and depth response primitives for EXPERIMENT-005A A5."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np

from predictions_cup.learning.flow_response import (
    NS,
    BBOReconstruction,
    asof_index,
    count_events,
    observation_confirmation_ns,
)


@dataclass(frozen=True)
class QuoteResponse:
    available: bool
    confirmation_ns: int | None
    midpoint_move: float
    spread_change: float
    bid_move: float
    ask_move: float
    genuine_change_count: int
    raw_record_count: int
    repeated_unchanged_count: int
    genuine_renewal: bool
    motion: str


def _motion(bid_move: float, ask_move: float, spread_change: float) -> str:
    tolerance = 1e-15
    if abs(bid_move) <= tolerance and abs(ask_move) <= tolerance:
        return "NO_CHANGE"
    if spread_change > tolerance:
        return "WIDENING"
    if spread_change < -tolerance:
        return "TIGHTENING"
    if bid_move > tolerance and ask_move > tolerance:
        return "TRANSLATION_UP"
    if bid_move < -tolerance and ask_move < -tolerance:
        return "TRANSLATION_DOWN"
    return "MIXED"


def quote_response(
    series: BBOReconstruction,
    collector_times_ns: np.ndarray,
    *,
    decision_ns: int,
    horizon_seconds: int,
    max_collector_gap_seconds: int = 30,
    target_confirm_seconds: int = 300,
) -> QuoteResponse:
    """Measure actual BBO response only where the interval is continuously observable."""

    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    endpoint = int(decision_ns) + horizon_seconds * NS
    current = asof_index(series, int(decision_ns))
    future = asof_index(series, endpoint)
    confirmation = observation_confirmation_ns(
        series,
        int(decision_ns),
        endpoint,
        collector_times_ns,
        max_collector_gap_seconds=max_collector_gap_seconds,
        target_confirm_seconds=target_confirm_seconds,
    )
    if current is None or future is None or confirmation is None:
        return QuoteResponse(
            available=False,
            confirmation_ns=None,
            midpoint_move=np.nan,
            spread_change=np.nan,
            bid_move=np.nan,
            ask_move=np.nan,
            genuine_change_count=0,
            raw_record_count=0,
            repeated_unchanged_count=0,
            genuine_renewal=False,
            motion="UNAVAILABLE",
        )

    bid_move = float(series.bid[future] - series.bid[current])
    ask_move = float(series.ask[future] - series.ask[current])
    midpoint_move = float(series.mid[future] - series.mid[current])
    spread_change = float(
        (series.ask[future] - series.bid[future])
        - (series.ask[current] - series.bid[current])
    )
    genuine = count_events(
        series,
        int(decision_ns),
        endpoint,
        kind="genuine",
    )
    raw = count_events(series, int(decision_ns), endpoint, kind="raw")
    unchanged = count_events(
        series,
        int(decision_ns),
        endpoint,
        kind="unchanged",
    )
    return QuoteResponse(
        available=True,
        confirmation_ns=int(confirmation),
        midpoint_move=midpoint_move,
        spread_change=spread_change,
        bid_move=bid_move,
        ask_move=ask_move,
        genuine_change_count=genuine,
        raw_record_count=raw,
        repeated_unchanged_count=unchanged,
        genuine_renewal=genuine > 0,
        motion=_motion(bid_move, ask_move, spread_change),
    )


@dataclass(frozen=True)
class DepthSeries:
    times_ns: np.ndarray
    bid_depth_shares: np.ndarray
    ask_depth_shares: np.ndarray
    total_depth_shares: np.ndarray


def _levels_depth(levels: object) -> float | None:
    if levels is None:
        return None
    if not isinstance(levels, Iterable):
        return None
    total = 0.0
    iterator = list(levels)
    for level in iterator:
        if not isinstance(level, Mapping):
            return None
        value = level.get("size")
        try:
            size = float(str(value))
        except (TypeError, ValueError):
            return None
        if not np.isfinite(size) or size < 0:
            return None
        total += size
    return total


def reconstruct_depth_series(
    rows: Iterable[Mapping[str, object]],
) -> DepthSeries:
    """Build a deterministic full-depth series from valid snapshots only."""

    parsed: list[tuple[int, float, float]] = []
    for row in rows:
        observed = row.get("recorded_at")
        if observed is None:
            continue
        if isinstance(observed, np.datetime64):
            timestamp = int(observed.astype("datetime64[ns]").astype(np.int64))
        elif callable(timestamp_method := getattr(observed, "timestamp", None)):
            timestamp = int(round(float(timestamp_method()) * NS))
        elif isinstance(observed, (int, np.integer)):
            timestamp = int(observed)
        else:
            continue
        bid = _levels_depth(row.get("bids"))
        ask = _levels_depth(row.get("asks"))
        if bid is None or ask is None:
            continue
        parsed.append((timestamp, bid, ask))

    parsed.sort(key=lambda item: (item[0], item[1], item[2]))
    if not parsed:
        empty_i = np.zeros(0, np.int64)
        empty_f = np.zeros(0, np.float64)
        return DepthSeries(empty_i, empty_f, empty_f.copy(), empty_f.copy())

    # Multiple snapshots at the same timestamp must agree; otherwise drop the timestamp.
    times: list[int] = []
    bids: list[float] = []
    asks: list[float] = []
    index = 0
    while index < len(parsed):
        end = index + 1
        while end < len(parsed) and parsed[end][0] == parsed[index][0]:
            end += 1
        states = {(row[1], row[2]) for row in parsed[index:end]}
        if len(states) == 1:
            bid, ask = next(iter(states))
            times.append(parsed[index][0])
            bids.append(bid)
            asks.append(ask)
        index = end

    bid_array = np.asarray(bids, float)
    ask_array = np.asarray(asks, float)
    return DepthSeries(
        times_ns=np.asarray(times, np.int64),
        bid_depth_shares=bid_array,
        ask_depth_shares=ask_array,
        total_depth_shares=bid_array + ask_array,
    )


def asof_depth_index(
    series: DepthSeries,
    query_ns: int,
    *,
    freshness_seconds: int = 300,
) -> int | None:
    index = int(np.searchsorted(series.times_ns, int(query_ns), side="right") - 1)
    if index < 0:
        return None
    age = int(query_ns) - int(series.times_ns[index])
    if age < 0 or age > freshness_seconds * NS:
        return None
    return index


def depth_response(
    series: DepthSeries,
    *,
    decision_ns: int,
    horizon_seconds: int,
    freshness_seconds: int = 300,
) -> dict[str, float | int | bool]:
    """Depth change with explicit snapshot ages/cadence; never a queue or fill model."""

    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    endpoint = int(decision_ns) + horizon_seconds * NS
    current = asof_depth_index(series, int(decision_ns), freshness_seconds=freshness_seconds)
    future = asof_depth_index(series, endpoint, freshness_seconds=freshness_seconds)
    lo = int(np.searchsorted(series.times_ns, int(decision_ns), side="right"))
    hi = int(np.searchsorted(series.times_ns, endpoint, side="right"))
    snapshots = max(0, hi - lo)
    if current is None or future is None:
        return {
            "available": False,
            "total_depth_change": np.nan,
            "bid_depth_change": np.nan,
            "ask_depth_change": np.nan,
            "current_snapshot_age_seconds": np.nan,
            "future_snapshot_age_seconds": np.nan,
            "snapshots_in_interval": snapshots,
        }
    return {
        "available": True,
        "total_depth_change": float(
            series.total_depth_shares[future] - series.total_depth_shares[current]
        ),
        "bid_depth_change": float(
            series.bid_depth_shares[future] - series.bid_depth_shares[current]
        ),
        "ask_depth_change": float(
            series.ask_depth_shares[future] - series.ask_depth_shares[current]
        ),
        "current_snapshot_age_seconds": (
            int(decision_ns) - int(series.times_ns[current])
        )
        / NS,
        "future_snapshot_age_seconds": (endpoint - int(series.times_ns[future])) / NS,
        "snapshots_in_interval": snapshots,
    }