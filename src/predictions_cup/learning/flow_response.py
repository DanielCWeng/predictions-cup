"""Observable-time quote-state utilities and dependence-preserving null primitives for 005A."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

import numpy as np

NS = 1_000_000_000
CONTINUITY_GAP_SECONDS = 300


@dataclass(frozen=True)
class BBOReconstruction:
    times_ns: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    mid: np.ndarray
    valid: np.ndarray
    genuine_change: np.ndarray
    midpoint_change: np.ndarray
    repeated_unchanged: np.ndarray
    raw_rows: np.ndarray


def _as_ns(value: object) -> int:
    if isinstance(value, np.datetime64):
        return int(value.astype("datetime64[ns]").astype(np.int64))
    if isinstance(value, datetime):
        return int(round(value.timestamp() * NS))
    if isinstance(value, (int, np.integer)):
        return int(value)
    raise TypeError(f"unsupported timestamp value {type(value)!r}")


def _valid_state(bid: object, ask: object) -> tuple[float, float] | None:
    try:
        b = float(str(bid))
        a = float(str(ask))
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(b) and np.isfinite(a) and 0 < b <= a < 1):
        return None
    return b, a


def reconstruct_genuine_bbo(
    rows: Iterable[Mapping[str, object]],
    *,
    gap_seconds: int = CONTINUITY_GAP_SECONDS,
) -> BBOReconstruction:
    """Collapse same-time observations; identical repeated states are not renewal."""

    normalized = [
        (_as_ns(row["observed_at"]), row.get("best_bid"), row.get("best_ask"))
        for row in rows
    ]
    normalized.sort(key=lambda item: (item[0], repr(item[1]), repr(item[2])))

    times: list[int] = []
    bids: list[float] = []
    asks: list[float] = []
    valids: list[bool] = []
    genuine: list[bool] = []
    midpoint_changes: list[bool] = []
    unchanged: list[bool] = []
    raw_rows: list[int] = []

    previous: tuple[float, float] | None = None
    previous_time: int | None = None
    gap_ns = gap_seconds * NS

    index = 0
    while index < len(normalized):
        end = index + 1
        while end < len(normalized) and normalized[end][0] == normalized[index][0]:
            end += 1
        timestamp = normalized[index][0]
        parsed = [_valid_state(bid, ask) for _, bid, ask in normalized[index:end]]
        states = {state for state in parsed if state is not None}

        times.append(timestamp)
        raw_rows.append(end - index)
        if len(states) != 1 or any(state is None for state in parsed):
            bids.append(np.nan)
            asks.append(np.nan)
            valids.append(False)
            genuine.append(False)
            midpoint_changes.append(False)
            unchanged.append(False)
            previous = None
            previous_time = None
            index = end
            continue

        state = next(iter(states))
        contiguous = (
            previous is not None
            and previous_time is not None
            and 0 <= timestamp - previous_time <= gap_ns
        )
        is_change = bool(contiguous and state != previous)
        old_mid = np.nan if previous is None else (previous[0] + previous[1]) / 2.0
        new_mid = (state[0] + state[1]) / 2.0
        is_mid_change = bool(contiguous and new_mid != old_mid)

        bids.append(state[0])
        asks.append(state[1])
        valids.append(True)
        genuine.append(is_change)
        midpoint_changes.append(is_mid_change)
        unchanged.append(bool(contiguous and state == previous))
        previous = state
        previous_time = timestamp
        index = end

    bid = np.asarray(bids, float)
    ask = np.asarray(asks, float)
    return BBOReconstruction(
        times_ns=np.asarray(times, np.int64),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2.0,
        valid=np.asarray(valids, bool),
        genuine_change=np.asarray(genuine, bool),
        midpoint_change=np.asarray(midpoint_changes, bool),
        repeated_unchanged=np.asarray(unchanged, bool),
        raw_rows=np.asarray(raw_rows, np.int64),
    )


def asof_index(
    series: BBOReconstruction,
    query_ns: int,
    *,
    freshness_seconds: int = 300,
) -> int | None:
    index = int(np.searchsorted(series.times_ns, int(query_ns), side="right") - 1)
    if index < 0 or not bool(series.valid[index]):
        return None
    age = int(query_ns) - int(series.times_ns[index])
    if age < 0 or age > freshness_seconds * NS:
        return None
    return index


def midpoint_at(
    series: BBOReconstruction,
    query_ns: int,
    *,
    freshness_seconds: int = 300,
) -> float | None:
    index = asof_index(series, query_ns, freshness_seconds=freshness_seconds)
    if index is None:
        return None
    return float(series.mid[index])


def future_mid_move(
    series: BBOReconstruction,
    decision_ns: int,
    horizon_seconds: int,
    *,
    freshness_seconds: int = 300,
) -> float | None:
    before = midpoint_at(series, decision_ns, freshness_seconds=freshness_seconds)
    after = midpoint_at(
        series,
        decision_ns + horizon_seconds * NS,
        freshness_seconds=freshness_seconds,
    )
    if before is None or after is None:
        return None
    return after - before


def count_events(
    series: BBOReconstruction,
    start_ns: int,
    end_ns: int,
    *,
    kind: str = "genuine",
) -> int:
    lo = int(np.searchsorted(series.times_ns, start_ns, side="right"))
    hi = int(np.searchsorted(series.times_ns, end_ns, side="right"))
    if hi <= lo:
        return 0
    if kind == "genuine":
        mask = series.genuine_change
    elif kind == "midpoint":
        mask = series.midpoint_change
    elif kind == "unchanged":
        mask = series.repeated_unchanged
    elif kind == "raw":
        return int(series.raw_rows[lo:hi].sum())
    else:
        raise ValueError(f"unsupported event kind {kind!r}")
    return int(mask[lo:hi].sum())


def deterministic_seed(master: int, component: str) -> int:
    digest = hashlib.sha256(f"{master}|{component}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def circular_shift_nonmissing(
    values: np.ndarray,
    *,
    shift: int,
) -> np.ndarray:
    """Circular-shift observed values while preserving the original missingness mask."""

    arr = np.asarray(values, float)
    result = np.full_like(arr, np.nan)
    observed = np.flatnonzero(np.isfinite(arr))
    if len(observed) == 0:
        return result
    shifted = np.roll(arr[observed], int(shift) % len(observed))
    result[observed] = shifted
    return result


def block_permutation(
    times_ns: np.ndarray,
    *,
    block_seconds: int,
    seed: int,
) -> np.ndarray:
    """Return row indices with contiguous time blocks permuted as whole units."""

    times = np.asarray(times_ns, np.int64)
    if len(times) == 0:
        return np.zeros(0, np.int64)
    origin = int(times.min())
    block = ((times - origin) // (block_seconds * NS)).astype(np.int64)
    labels = np.unique(block)
    rng = np.random.default_rng(seed)
    order = labels.copy()
    rng.shuffle(order)
    pieces = [np.flatnonzero(block == label) for label in order]
    return np.concatenate(pieces).astype(np.int64)
