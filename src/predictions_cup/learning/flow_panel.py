"""Causal flow and quote-panel construction primitives for EXPERIMENT-005A."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from predictions_cup.learning.flow_response import NS, BBOReconstruction, asof_index


@dataclass(frozen=True)
class FlowSeries:
    times_ns: np.ndarray
    signed_value: np.ndarray
    signed_shares: np.ndarray
    unsigned_value: np.ndarray


@dataclass(frozen=True)
class FlowGrid:
    signed_value: np.ndarray
    signed_shares: np.ndarray
    unsigned_value: np.ndarray
    fill_count: np.ndarray


@dataclass(frozen=True)
class QuoteGrid:
    midpoint: np.ndarray
    spread: np.ndarray
    quote_age_seconds: np.ndarray
    recent_move_30s: np.ndarray
    abs_move_300s: np.ndarray
    genuine_changes_30s: np.ndarray
    raw_records_30s: np.ndarray
    repeated_unchanged_30s: np.ndarray


def build_role_flow_series(
    table: pa.Table,
    *,
    condition_id: str,
    role_class: str = "TAKER_HIGH_CONFIDENCE",
) -> FlowSeries:
    required = (
        "role_condition_id",
        "role_timestamp",
        "role_class",
        "role_outcome_side",
        "role_participant_side",
        "role_value_usd",
        "role_size_shares",
    )
    missing = [name for name in required if name not in table.column_names]
    if missing:
        raise ValueError(f"flow-series columns missing: {missing}")

    mask = pc.and_(
        pc.equal(table["role_condition_id"], condition_id),
        pc.equal(table["role_class"], role_class),
    )
    rows = table.filter(mask).select(required).to_pylist()
    values: list[tuple[int, float, float, float]] = []
    for row in rows:
        outcome = str(row["role_outcome_side"]).upper()
        side = str(row["role_participant_side"]).lower()
        if outcome not in {"YES", "NO"} or side not in {"buy", "sell"}:
            continue
        sign = 1.0 if (outcome, side) in {("YES", "buy"), ("NO", "sell")} else -1.0
        timestamp_ns = int(row["role_timestamp"]) * NS
        value = float(row["role_value_usd"])
        shares = float(row["role_size_shares"])
        values.append((timestamp_ns, sign * value, sign * shares, abs(value)))

    values.sort(key=lambda row: row[0])
    if not values:
        empty_i = np.zeros(0, np.int64)
        empty_f = np.zeros(0, np.float64)
        return FlowSeries(empty_i, empty_f, empty_f.copy(), empty_f.copy())
    array = np.asarray(values, dtype=np.float64)
    return FlowSeries(
        times_ns=array[:, 0].astype(np.int64),
        signed_value=array[:, 1],
        signed_shares=array[:, 2],
        unsigned_value=array[:, 3],
    )


def aggregate_flow_grid(
    series: FlowSeries,
    grid_ns: np.ndarray,
    *,
    lookback_seconds: int = 30,
) -> FlowGrid:
    """Aggregate completed second-granular fills strictly before each decision instant."""

    grid = np.asarray(grid_ns, np.int64)
    signed_value = np.zeros(len(grid), np.float64)
    signed_shares = np.zeros(len(grid), np.float64)
    unsigned_value = np.zeros(len(grid), np.float64)
    count = np.zeros(len(grid), np.float64)
    lookback_ns = lookback_seconds * NS
    for index, decision in enumerate(grid):
        lo = int(np.searchsorted(series.times_ns, decision - lookback_ns, side="right"))
        hi = int(np.searchsorted(series.times_ns, decision, side="left"))
        if hi <= lo:
            continue
        signed_value[index] = float(series.signed_value[lo:hi].sum())
        signed_shares[index] = float(series.signed_shares[lo:hi].sum())
        unsigned_value[index] = float(series.unsigned_value[lo:hi].sum())
        count[index] = hi - lo
    return FlowGrid(signed_value, signed_shares, unsigned_value, count)


def _count_quote_events(
    series: BBOReconstruction,
    start_ns: int,
    end_ns: int,
    mask: np.ndarray,
) -> int:
    lo = int(np.searchsorted(series.times_ns, start_ns, side="right"))
    hi = int(np.searchsorted(series.times_ns, end_ns, side="right"))
    return int(mask[lo:hi].sum()) if hi > lo else 0


def build_quote_grid(
    series: BBOReconstruction,
    grid_ns: np.ndarray,
    *,
    freshness_seconds: int = 300,
) -> QuoteGrid:
    grid = np.asarray(grid_ns, np.int64)
    midpoint = np.full(len(grid), np.nan, np.float64)
    spread = np.full(len(grid), np.nan, np.float64)
    age = np.full(len(grid), np.nan, np.float64)
    move30 = np.full(len(grid), np.nan, np.float64)
    abs300 = np.full(len(grid), np.nan, np.float64)
    change30 = np.zeros(len(grid), np.float64)
    raw30 = np.zeros(len(grid), np.float64)
    unchanged30 = np.zeros(len(grid), np.float64)

    for index, decision in enumerate(grid):
        current = asof_index(series, int(decision), freshness_seconds=freshness_seconds)
        if current is None:
            continue
        midpoint[index] = float(series.mid[current])
        spread[index] = float(series.ask[current] - series.bid[current])
        age[index] = (int(decision) - int(series.times_ns[current])) / NS

        prior30 = asof_index(
            series,
            int(decision - 30 * NS),
            freshness_seconds=freshness_seconds,
        )
        if prior30 is not None:
            move30[index] = midpoint[index] - float(series.mid[prior30])

        prior300 = asof_index(
            series,
            int(decision - 300 * NS),
            freshness_seconds=freshness_seconds,
        )
        if prior300 is not None:
            abs300[index] = abs(midpoint[index] - float(series.mid[prior300]))

        start = int(decision - 30 * NS)
        change30[index] = _count_quote_events(
            series, start, int(decision), series.genuine_change
        )
        raw30[index] = float(
            series.raw_rows[
                np.searchsorted(series.times_ns, start, side="right"):
                np.searchsorted(series.times_ns, int(decision), side="right")
            ].sum()
        )
        unchanged30[index] = _count_quote_events(
            series, start, int(decision), series.repeated_unchanged
        )

    return QuoteGrid(
        midpoint=midpoint,
        spread=spread,
        quote_age_seconds=age,
        recent_move_30s=move30,
        abs_move_300s=abs300,
        genuine_changes_30s=change30,
        raw_records_30s=raw30,
        repeated_unchanged_30s=unchanged30,
    )


def future_moves(
    series: BBOReconstruction,
    grid_ns: np.ndarray,
    *,
    horizon_seconds: int,
    freshness_seconds: int = 300,
) -> np.ndarray:
    grid = np.asarray(grid_ns, np.int64)
    result = np.full(len(grid), np.nan, np.float64)
    for index, decision in enumerate(grid):
        current = asof_index(series, int(decision), freshness_seconds=freshness_seconds)
        future = asof_index(
            series,
            int(decision + horizon_seconds * NS),
            freshness_seconds=freshness_seconds,
        )
        if current is None or future is None:
            continue
        result[index] = float(series.mid[future] - series.mid[current])
    return result


def common_event_move(
    quote_grids: dict[str, QuoteGrid],
    *,
    exclude_tokens: frozenset[str] = frozenset(),
) -> np.ndarray:
    included = [
        grid.recent_move_30s
        for token, grid in sorted(quote_grids.items())
        if token not in exclude_tokens
    ]
    if not included:
        length = len(next(iter(quote_grids.values())).midpoint) if quote_grids else 0
        return np.full(length, np.nan, np.float64)
    matrix = np.vstack(included)
    valid = np.isfinite(matrix)
    count = valid.sum(axis=0)
    total = np.where(valid, matrix, 0.0).sum(axis=0)
    result = np.divide(
        total,
        count,
        out=np.full(matrix.shape[1], np.nan, np.float64),
        where=count > 0,
    )
    return np.asarray(result, dtype=np.float64)
