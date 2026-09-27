"""Scientific primitives for EXPERIMENT-004C-D.

This module is deliberately outcome-agnostic. It reconstructs observable BBO state,
genuine quote renewal, censoring/exposure, support gates and multiplicity mechanics
that were frozen before challenge-event access.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

NS = 1_000_000_000
PRIMARY_HORIZON_SECONDS = 30
FRESHNESS_SECONDS = 300
CONTINUITY_GAP_SECONDS = 300
MULTIPLICITY_SLOTS = (
    "C01-PRE", "C01-ACTIVE",
    "C02-PRE", "C02-ACTIVE",
    "C03-PRE", "C03-ACTIVE",
    "C04-PRE", "C04-ACTIVE",
)
REAL_ORDER_PLACEMENT_ENABLED = False


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
    last_genuine_ns: np.ndarray
    last_midpoint_change_ns: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.times_ns)
        for name in (
            "bid", "ask", "mid", "valid", "genuine_change", "midpoint_change",
            "repeated_unchanged", "raw_rows", "last_genuine_ns",
            "last_midpoint_change_ns",
        ):
            if len(getattr(self, name)) != n:
                raise ValueError(f"{name} length mismatch")


def _as_ns(value: object) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, np.datetime64):
        return int(value.astype("datetime64[ns]").astype(np.int64))
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        return int(round(float(timestamp()) * NS))
    raise TypeError(f"unsupported timestamp: {type(value)!r}")


def _valid_state(bid: object, ask: object) -> tuple[float, float] | None:
    try:
        b = float(bid)
        a = float(ask)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(b) or not np.isfinite(a) or not (0.0 < b <= a < 1.0):
        return None
    return b, a


def reconstruct_genuine_bbo(
    rows: Iterable[Mapping[str, object]],
    *,
    gap_seconds: int = CONTINUITY_GAP_SECONDS,
) -> BBOReconstruction:
    """Collapse same-time observations and reconstruct genuine observable BBO changes."""
    normalized: list[tuple[int, object, object]] = []
    for row in rows:
        normalized.append(
            (_as_ns(row["observed_at"]), row.get("best_bid"), row.get("best_ask"))
        )
    normalized.sort(key=lambda x: (x[0], repr(x[1]), repr(x[2])))

    times: list[int] = []
    bids: list[float] = []
    asks: list[float] = []
    valids: list[bool] = []
    genuine: list[bool] = []
    midpoint_changes: list[bool] = []
    unchanged: list[bool] = []
    raw_rows: list[int] = []
    last_genuine: list[int] = []
    last_mid_change: list[int] = []

    prev_state: tuple[float, float] | None = None
    prev_time: int | None = None
    last_genuine_time: int | None = None
    last_mid_time: int | None = None
    gap_ns = gap_seconds * NS

    i = 0
    while i < len(normalized):
        j = i + 1
        while j < len(normalized) and normalized[j][0] == normalized[i][0]:
            j += 1
        t = normalized[i][0]
        parsed = [_valid_state(bid, ask) for _, bid, ask in normalized[i:j]]
        states = {state for state in parsed if state is not None}
        raw_rows.append(j - i)
        times.append(t)

        if len(states) != 1 or any(state is None for state in parsed):
            bids.append(np.nan)
            asks.append(np.nan)
            valids.append(False)
            genuine.append(False)
            midpoint_changes.append(False)
            unchanged.append(False)
            last_genuine.append(-1)
            last_mid_change.append(-1)
            prev_state = None
            prev_time = None
            last_genuine_time = None
            last_mid_time = None
            i = j
            continue

        state = next(iter(states))
        bid, ask = state
        current_mid = (bid + ask) / 2.0
        contiguous = (
            prev_state is not None
            and prev_time is not None
            and 0 <= t - prev_time <= gap_ns
        )
        if not contiguous:
            prev_state = None
            last_genuine_time = None
            last_mid_time = None

        is_change = bool(contiguous and state != prev_state)
        is_mid_change = bool(
            contiguous
            and prev_state is not None
            and current_mid != (prev_state[0] + prev_state[1]) / 2.0
        )
        is_unchanged = bool(contiguous and state == prev_state)
        if is_change:
            last_genuine_time = t
        if is_mid_change:
            last_mid_time = t

        bids.append(bid)
        asks.append(ask)
        valids.append(True)
        genuine.append(is_change)
        midpoint_changes.append(is_mid_change)
        unchanged.append(is_unchanged)
        last_genuine.append(-1 if last_genuine_time is None else last_genuine_time)
        last_mid_change.append(-1 if last_mid_time is None else last_mid_time)
        prev_state = state
        prev_time = t
        i = j

    bid_arr = np.asarray(bids, float)
    ask_arr = np.asarray(asks, float)
    return BBOReconstruction(
        times_ns=np.asarray(times, np.int64),
        bid=bid_arr,
        ask=ask_arr,
        mid=(bid_arr + ask_arr) / 2.0,
        valid=np.asarray(valids, bool),
        genuine_change=np.asarray(genuine, bool),
        midpoint_change=np.asarray(midpoint_changes, bool),
        repeated_unchanged=np.asarray(unchanged, bool),
        raw_rows=np.asarray(raw_rows, np.int64),
        last_genuine_ns=np.asarray(last_genuine, np.int64),
        last_midpoint_change_ns=np.asarray(last_mid_change, np.int64),
    )


def asof_index(
    series: BBOReconstruction,
    query_ns: int,
    *,
    freshness_seconds: int = FRESHNESS_SECONDS,
) -> int | None:
    idx = int(np.searchsorted(series.times_ns, int(query_ns), side="right") - 1)
    if idx < 0 or not bool(series.valid[idx]):
        return None
    age = int(query_ns) - int(series.times_ns[idx])
    if age < 0 or age > freshness_seconds * NS:
        return None
    return idx


def asof_state(
    series: BBOReconstruction,
    query_ns: int,
) -> dict[str, float | int] | None:
    idx = asof_index(series, query_ns)
    if idx is None:
        return None
    last_genuine = int(series.last_genuine_ns[idx])
    last_mid = int(series.last_midpoint_change_ns[idx])
    return {
        "bid": float(series.bid[idx]),
        "ask": float(series.ask[idx]),
        "mid": float(series.mid[idx]),
        "spread": float(series.ask[idx] - series.bid[idx]),
        "record_age_seconds": (int(query_ns) - int(series.times_ns[idx])) / NS,
        "genuine_change_age_seconds": (
            np.nan if last_genuine < 0 else (int(query_ns) - last_genuine) / NS
        ),
        "midpoint_change_age_seconds": (
            np.nan if last_mid < 0 else (int(query_ns) - last_mid) / NS
        ),
        "record_time_ns": int(series.times_ns[idx]),
    }


def count_events(
    series: BBOReconstruction,
    start_ns: int,
    end_ns: int,
    *,
    kind: str = "genuine",
    exclude_times: set[int] | None = None,
) -> int:
    if end_ns <= start_ns:
        return 0
    lo = int(np.searchsorted(series.times_ns, start_ns, side="right"))
    hi = int(np.searchsorted(series.times_ns, end_ns, side="right"))
    if kind == "genuine":
        mask = series.genuine_change[lo:hi]
        times = series.times_ns[lo:hi]
        if exclude_times:
            mask = mask & ~np.isin(times, list(exclude_times))
        return int(mask.sum())
    if kind == "midpoint":
        return int(series.midpoint_change[lo:hi].sum())
    if kind == "unchanged":
        return int(series.repeated_unchanged[lo:hi].sum())
    if kind == "raw":
        return int(series.raw_rows[lo:hi].sum())
    raise ValueError(f"unknown event kind: {kind}")


def any_genuine_change(series: BBOReconstruction, start_ns: int, end_ns: int) -> bool:
    return count_events(series, start_ns, end_ns, kind="genuine") > 0


def observation_exposure_valid(
    series: BBOReconstruction,
    start_ns: int,
    end_ns: int,
    collector_times_ns: np.ndarray,
    *,
    max_collector_gap_seconds: int = 30,
    target_confirm_seconds: int = 300,
) -> bool:
    """Fail closed unless unchanged target state is demonstrably observable."""
    if end_ns <= start_ns or asof_index(series, start_ns) is None:
        return False

    lo = int(np.searchsorted(series.times_ns, start_ns, side="right"))
    hi = int(np.searchsorted(series.times_ns, end_ns, side="right"))
    if hi > lo and np.any(~series.valid[lo:hi]):
        return False

    next_idx = int(np.searchsorted(series.times_ns, end_ns, side="left"))
    if next_idx >= len(series.times_ns) or not bool(series.valid[next_idx]):
        return False
    if int(series.times_ns[next_idx]) - int(end_ns) > target_confirm_seconds * NS:
        return False

    ct = np.asarray(collector_times_ns, dtype=np.int64)
    if len(ct) == 0:
        return False
    left = int(np.searchsorted(ct, start_ns, side="right") - 1)
    right = int(np.searchsorted(ct, end_ns, side="left"))
    if left < 0 or right >= len(ct):
        return False
    segment = ct[left : right + 1]
    if len(segment) < 2:
        return False
    return bool(np.max(np.diff(segment)) <= max_collector_gap_seconds * NS)


def equal_hierarchical_weights(
    events: Sequence[object],
    blocks: Sequence[object],
    targets: Sequence[object],
) -> np.ndarray:
    """Equal event -> block -> target -> row weights, normalized to mean one."""
    n = len(events)
    if not (len(blocks) == n and len(targets) == n):
        raise ValueError("weighting arrays must align")
    if n == 0:
        return np.zeros(0, float)
    events_a = np.asarray(events, object)
    blocks_a = np.asarray(blocks, object)
    targets_a = np.asarray(targets, object)
    out = np.zeros(n, float)
    unique_events = sorted({str(x) for x in events_a})
    for event in unique_events:
        em = np.asarray([str(x) == event for x in events_a])
        block_vals = sorted({str(x) for x in blocks_a[em]})
        for block in block_vals:
            bm = em & np.asarray([str(x) == block for x in blocks_a])
            target_vals = sorted({str(x) for x in targets_a[bm]})
            for target in target_vals:
                tm = bm & np.asarray([str(x) == target for x in targets_a])
                out[tm] = (
                    1.0 / len(unique_events)
                    / len(block_vals)
                    / len(target_vals)
                    / int(tm.sum())
                )
    return out * n / out.sum()


def residualize_added_feature(x: np.ndarray, z: np.ndarray) -> np.ndarray:
    if x.ndim != 2 or len(x) != len(z):
        raise ValueError("invalid residualization inputs")
    design = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(design, z, rcond=1e-10)
    return z - design @ beta


def support_gate(
    x: np.ndarray,
    z: np.ndarray,
    times_ns: np.ndarray,
    *,
    y: np.ndarray | None = None,
    training: bool = False,
) -> tuple[bool, list[str], dict[str, float | int]]:
    reasons: list[str] = []
    finite = np.all(np.isfinite(x), axis=1) & np.isfinite(z)
    if y is not None:
        finite &= np.isfinite(y)
    xx = x[finite]
    zz = z[finite]
    tt = np.asarray(times_ns, np.int64)[finite]
    ncoef = x.shape[1] + 2
    if training and len(xx) < 20 * ncoef:
        reasons.append("TRAIN_OBS_PER_COEFFICIENT_LT_20")
    if len(xx) == 0:
        reasons.append("NO_VALID_ROWS")
        return False, reasons, {"rows": 0, "blocks": 0, "varying_blocks": 0}

    zr = residualize_added_feature(xx, zz)
    if float(np.dot(zr, zr)) <= 1e-12:
        reasons.append("ADDED_FEATURE_NOT_FULL_RANK")

    blocks = tt // (300 * NS)
    unique_blocks = np.unique(blocks)
    if len(unique_blocks) < 40:
        reasons.append("OCCUPIED_300S_BLOCKS_LT_40")
    block_ss: list[float] = []
    varying = 0
    for block in unique_blocks:
        vals = zr[blocks == block]
        ss = float(np.dot(vals, vals))
        block_ss.append(ss)
        if len(vals) > 1 and np.nanmax(vals) - np.nanmin(vals) > 1e-12:
            varying += 1
    if varying < 20:
        reasons.append("ADDED_FEATURE_VARIATION_BLOCKS_LT_20")
    total_ss = float(sum(block_ss))
    max_frac = 1.0 if total_ss <= 0 else max(block_ss, default=0.0) / total_ss
    if max_frac > 0.50:
        reasons.append("SINGLE_BLOCK_SS_GT_50PCT")
    if y is not None and len(np.unique(np.asarray(y)[finite])) < 2:
        reasons.append("HAZARD_REQUIRES_BOTH_CLASSES")
    details = {
        "rows": int(len(xx)),
        "blocks": int(len(unique_blocks)),
        "varying_blocks": int(varying),
        "max_block_ss_fraction": float(max_frac),
        "residualized_ss": float(total_ss),
        "coefficients_in_challenger": int(ncoef),
    }
    return not reasons, reasons, details


def holm_adjust(
    p_values: Mapping[str, float],
    alpha: float = 0.05,
) -> dict[str, dict[str, float | bool]]:
    if tuple(p_values.keys()) != MULTIPLICITY_SLOTS:
        raise ValueError("all eight declared slots must be supplied in frozen order")
    ordered = sorted(p_values.items(), key=lambda kv: (float(kv[1]), kv[0]))
    m = len(ordered)
    adjusted_raw: dict[str, float] = {}
    running = 0.0
    for rank, (name, p) in enumerate(ordered):
        candidate = min(1.0, (m - rank) * float(p))
        running = max(running, candidate)
        adjusted_raw[name] = running
    return {
        name: {
            "p_raw": float(p_values[name]),
            "p_holm": float(adjusted_raw[name]),
            "reject": bool(adjusted_raw[name] <= alpha),
        }
        for name in MULTIPLICITY_SLOTS
    }


def assert_execution_safety() -> None:
    if REAL_ORDER_PLACEMENT_ENABLED:
        raise RuntimeError("real order placement must remain disabled")
