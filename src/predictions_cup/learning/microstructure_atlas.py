"""Observable-state primitives for EXPERIMENT-005F."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log
from typing import Iterable, Mapping, Sequence

import numpy as np

NS = 1_000_000_000
CONTINUITY_GAP_SECONDS = 300
CLOCK_HORIZONS = (1, 5, 15, 30, 60, 120, 300)
EVENT_HORIZONS = (1, 2, 5, 10)
DEPTH_BANDS = (0.01, 0.02, 0.05)
IMPACT_SIZES = (10.0, 50.0, 100.0, 500.0)


@dataclass(frozen=True, slots=True)
class CanonicalBBO:
    bid: float
    ask: float

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def valid(self) -> bool:
        return 0.0 < self.bid <= self.ask < 1.0


@dataclass(slots=True)
class L2Book:
    bids: dict[float, float]
    asks: dict[float, float]
    depth_valid: bool = False
    last_observed_ns: int | None = None
    last_snapshot_ns: int | None = None
    last_genuine_ns: int | None = None
    last_bid_change_ns: int | None = None
    last_ask_change_ns: int | None = None
    previous_bbo: CanonicalBBO | None = None

    @classmethod
    def empty(cls) -> "L2Book":
        return cls(bids={}, asks={})

    def invalidate_depth(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.depth_valid = False

    def snapshot(
        self,
        bids: Iterable[tuple[float, float]],
        asks: Iterable[tuple[float, float]],
        observed_ns: int,
    ) -> CanonicalBBO | None:
        self.bids = _clean_side(bids)
        self.asks = _clean_side(asks)
        self.depth_valid = bool(self.bids and self.asks)
        self.last_snapshot_ns = int(observed_ns)
        self.last_observed_ns = int(observed_ns)
        current = self.bbo()
        self._update_change_ages(current, int(observed_ns))
        self.previous_bbo = current
        return current

    def apply_change_group(
        self,
        rows: Sequence[Mapping[str, object]],
        observed_ns: int,
    ) -> tuple[CanonicalBBO | None, dict[str, bool]]:
        observed_ns = int(observed_ns)
        gap = (
            self.last_observed_ns is not None
            and observed_ns - self.last_observed_ns > CONTINUITY_GAP_SECONDS * NS
        )
        if gap:
            self.invalidate_depth()

        assignments: dict[tuple[str, float], set[float]] = {}
        source_bbos: set[tuple[float, float]] = set()
        source_invalid = False
        for row in rows:
            side = str(row.get("side", "")).upper()
            price = _finite_float(row.get("price"))
            size = _finite_float(row.get("size"))
            if side not in {"BUY", "SELL"} or price is None or size is None:
                source_invalid = True
                continue
            if not (0.0 <= price <= 1.0) or size < 0.0:
                source_invalid = True
                continue
            assignments.setdefault((side, price), set()).add(size)
            bb = _finite_float(row.get("best_bid"))
            ba = _finite_float(row.get("best_ask"))
            if bb is not None and ba is not None:
                if 0.0 < bb <= ba < 1.0:
                    source_bbos.add((bb, ba))
                else:
                    source_invalid = True

        conflicting_level = any(len(values) != 1 for values in assignments.values())
        if conflicting_level:
            self.invalidate_depth()
        elif self.depth_valid:
            for (side, price), values in assignments.items():
                size = next(iter(values))
                levels = self.bids if side == "BUY" else self.asks
                if size == 0.0:
                    levels.pop(price, None)
                else:
                    levels[price] = size

        source_ambiguous = len(source_bbos) > 1
        source_bbo = CanonicalBBO(*next(iter(source_bbos))) if len(source_bbos) == 1 else None
        reconstructed = self.bbo() if self.depth_valid else None
        current = reconstructed if reconstructed is not None else source_bbo
        prev = self.previous_bbo

        genuine = bool(current is not None and prev is not None and current != prev and not source_ambiguous)
        bid_changed = bool(genuine and current is not None and prev is not None and current.bid != prev.bid)
        ask_changed = bool(genuine and current is not None and prev is not None and current.ask != prev.ask)
        spread_changed = bool(genuine and current is not None and prev is not None and current.spread != prev.spread)
        midpoint_changed = bool(
            genuine and current is not None and prev is not None and current.midpoint != prev.midpoint
        )

        if not source_ambiguous and current is not None:
            self._update_change_ages(current, observed_ns)
            self.previous_bbo = current
        elif source_ambiguous:
            self.previous_bbo = None
        self.last_observed_ns = observed_ns

        return current, {
            "record_arrived": True,
            "continuity_gap": bool(gap),
            "same_timestamp_ambiguity": bool(source_ambiguous or conflicting_level),
            "invalid_or_crossed": bool(source_invalid),
            "genuine_bbo_change": genuine,
            "genuine_bid_change": bid_changed,
            "genuine_ask_change": ask_changed,
            "spread_change": spread_changed,
            "midpoint_change": midpoint_changed,
            "depth_valid": self.depth_valid,
        }

    def _update_change_ages(self, current: CanonicalBBO | None, observed_ns: int) -> None:
        prev = self.previous_bbo
        if current is None or prev is None:
            return
        if current != prev:
            self.last_genuine_ns = observed_ns
        if current.bid != prev.bid:
            self.last_bid_change_ns = observed_ns
        if current.ask != prev.ask:
            self.last_ask_change_ns = observed_ns

    def bbo(self) -> CanonicalBBO | None:
        if not self.depth_valid or not self.bids or not self.asks:
            return None
        bbo = CanonicalBBO(max(self.bids), min(self.asks))
        return bbo if bbo.valid else None


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _clean_side(levels: Iterable[tuple[float, float]]) -> dict[float, float]:
    out: dict[float, float] = {}
    for price_raw, size_raw in levels:
        price = float(price_raw)
        size = float(size_raw)
        if np.isfinite(price) and np.isfinite(size) and 0.0 <= price <= 1.0 and size > 0.0:
            out[price] = size
    return out


def canonical_yes_bbo(outcome: str, bid: float, ask: float) -> CanonicalBBO:
    raw = CanonicalBBO(float(bid), float(ask))
    if not raw.valid:
        raise ValueError("invalid token BBO")
    if outcome.strip().lower() == "yes":
        return raw
    if outcome.strip().lower() == "no":
        yes = CanonicalBBO(1.0 - raw.ask, 1.0 - raw.bid)
        if not yes.valid:
            raise ValueError("invalid implied YES BBO")
        return yes
    raise ValueError(f"unsupported binary outcome: {outcome!r}")


def clipped_logit(probability: float, *, eps: float = 1e-6) -> float:
    p = min(1.0 - eps, max(eps, float(probability)))
    return log(p / (1.0 - p))


def depth_within(levels: Mapping[float, float], midpoint: float, band: float) -> float:
    return float(sum(size for price, size in levels.items() if abs(price - midpoint) <= band))


def top_sizes(book: L2Book) -> tuple[float, float] | None:
    bbo = book.bbo()
    if bbo is None:
        return None
    return float(book.bids[bbo.bid]), float(book.asks[bbo.ask])


def primitive_microprice(bid: float, ask: float, bid_size: float, ask_size: float) -> float:
    denom = float(bid_size) + float(ask_size)
    if denom <= 0.0:
        return float("nan")
    return (float(ask) * float(bid_size) + float(bid) * float(ask_size)) / denom


def cont_ofi(
    prev_bid: float,
    prev_ask: float,
    prev_bid_size: float,
    prev_ask_size: float,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
) -> float:
    e = 0.0
    if bid >= prev_bid:
        e += bid_size
    if bid <= prev_bid:
        e -= prev_bid_size
    if ask <= prev_ask:
        e -= ask_size
    if ask >= prev_ask:
        e += prev_ask_size
    return float(e)


def vwap_impact(
    levels: Mapping[float, float],
    quantity: float,
    midpoint: float,
    *,
    buy: bool,
) -> float:
    if quantity <= 0.0:
        raise ValueError("quantity must be positive")
    ordered = sorted(levels.items(), reverse=not buy)
    remaining = float(quantity)
    notional = 0.0
    filled = 0.0
    for price, size in ordered:
        take = min(remaining, size)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if remaining > 1e-12 or filled <= 0.0:
        return float("nan")
    vwap = notional / filled
    return float(vwap - midpoint if buy else midpoint - vwap)


def depth_metrics(book: L2Book) -> dict[str, float]:
    bbo = book.bbo()
    if bbo is None:
        return {}
    sizes = top_sizes(book)
    assert sizes is not None
    qb, qa = sizes
    total_top = qb + qa
    out = {
        "bid_size_top": qb,
        "ask_size_top": qa,
        "depth_top": total_top,
        "imbalance_top": 0.0 if total_top == 0 else (qb - qa) / total_top,
        "primitive_microprice": primitive_microprice(bbo.bid, bbo.ask, qb, qa),
    }
    for band in DEPTH_BANDS:
        key = int(round(band * 100))
        bd = depth_within(book.bids, bbo.midpoint, band)
        ad = depth_within(book.asks, bbo.midpoint, band)
        total = bd + ad
        out[f"bid_depth_{key}c"] = bd
        out[f"ask_depth_{key}c"] = ad
        out[f"depth_{key}c"] = total
        out[f"depth_imbalance_{key}c"] = 0.0 if total == 0 else (bd - ad) / total
    d1 = out["depth_1c"]
    d5 = out["depth_5c"]
    out["depth_concentration_1c_5c"] = np.nan if d5 <= 0 else d1 / d5
    out["depth_slope_1c_5c"] = (d5 - d1) / 0.04
    for quantity in IMPACT_SIZES:
        label = str(int(quantity))
        out[f"buy_impact_q{label}"] = vwap_impact(book.asks, quantity, bbo.midpoint, buy=True)
        out[f"sell_impact_q{label}"] = vwap_impact(book.bids, quantity, bbo.midpoint, buy=False)
    return out


def split_bounds(start_ns: int, end_ns: int) -> dict[str, tuple[int, int]]:
    if end_ns <= start_ns:
        raise ValueError("end must exceed start")
    span = end_ns - start_ns
    train_end = start_ns + int(span * 0.60)
    dev_end = start_ns + int(span * 0.80)
    return {
        "TRAIN": (start_ns, train_end),
        "DEV": (train_end, dev_end),
        "HOLDOUT": (dev_end, end_ns),
    }


def split_for_time(
    timestamp_ns: int,
    bounds: Mapping[str, tuple[int, int]],
    *,
    purge_seconds: int = 300,
) -> str | None:
    purge = purge_seconds * NS
    train_start, train_end = bounds["TRAIN"]
    dev_start, dev_end = bounds["DEV"]
    hold_start, hold_end = bounds["HOLDOUT"]
    t = int(timestamp_ns)
    if train_start <= t < train_end - purge:
        return "TRAIN"
    if dev_start + purge <= t < dev_end - purge:
        return "DEV"
    if hold_start + purge <= t < hold_end:
        return "HOLDOUT"
    return None


def future_clock_index(times_ns: np.ndarray, index: int, horizon_seconds: int) -> int | None:
    target = int(times_ns[index]) + int(horizon_seconds) * NS
    j = int(np.searchsorted(times_ns, target, side="right") - 1)
    return j if j > index else None


def future_event_index(genuine_mask: np.ndarray, index: int, k: int) -> int | None:
    if k <= 0:
        raise ValueError("k must be positive")
    subsequent = np.flatnonzero(genuine_mask[index + 1 :])
    if len(subsequent) < k:
        return None
    return int(index + 1 + subsequent[k - 1])


def ewma(values: Sequence[float], times_ns: Sequence[int], half_life_seconds: float) -> np.ndarray:
    if half_life_seconds <= 0:
        raise ValueError("half life must be positive")
    if len(values) != len(times_ns):
        raise ValueError("values/times mismatch")
    out = np.full(len(values), np.nan, dtype=float)
    if not values:
        return out
    rate = log(2.0) / half_life_seconds
    state = float(values[0])
    out[0] = state
    for i in range(1, len(values)):
        dt_s = max(0.0, (int(times_ns[i]) - int(times_ns[i - 1])) / NS)
        weight = 1.0 - exp(-rate * dt_s)
        state = state + weight * (float(values[i]) - state)
        out[i] = state
    return out


def benjamini_hochberg(
    pvalues: Mapping[str, float], *, q: float = 0.10
) -> dict[str, dict[str, float | bool]]:
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0,1)")
    items = sorted(((name, float(p)) for name, p in pvalues.items()), key=lambda item: item[1])
    m = len(items)
    if m == 0:
        return {}
    adjusted = [1.0] * m
    running = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        running = min(running, items[i][1] * m / rank)
        adjusted[i] = min(1.0, running)
    max_reject_rank = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            max_reject_rank = i
    out: dict[str, dict[str, float | bool]] = {}
    for i, ((name, p), p_adj) in enumerate(zip(items, adjusted, strict=True), start=1):
        out[name] = {"p_raw": p, "p_bh": p_adj, "reject": bool(i <= max_reject_rank)}
    return out


def moving_block_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    if n <= 0 or block <= 0:
        raise ValueError("n and block must be positive")
    draws: list[int] = []
    while len(draws) < n:
        start = int(rng.integers(0, max(1, n - block + 1)))
        draws.extend(range(start, min(n, start + block)))
    return np.asarray(draws[:n], dtype=np.int64)
