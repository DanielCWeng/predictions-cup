"""Run the offline FV-LIVE-001 replay against the copied read-only extracts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from random import Random
from statistics import mean
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from predictions_cup.research.fv_live.model import (
    INVENTORY_SKEW_GRID_TICKS_PER_500,
    TICK,
    FVParameters,
    fair_value,
    frozen_ofi_probability,
)

LIVE_START = 1790870400.0  # 2026-10-01 16:00:00Z
LIVE_TRADE_START = 1790872200.0  # 2026-10-01 16:30:00Z
LIVE_END = 1790874598.380029  # last copied realtime trade, 2026-10-01 17:09:58Z
BOOTSTRAP_REPLICATES = 1000
QUOTE_HALF_WIDTH = TICK
STALE_PULL_SECONDS = 210.0
FORECAST_MODEL_NAMES = ("base_pm", "reversal", "ofi", "combined")


def _new_model_errors() -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for model in FORECAST_MODEL_NAMES:
        result[model] = []
    return result


def _timestamp(value: Any) -> float:
    return float(value.timestamp())


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_mapping(path: Path) -> dict[str, dict[str, str]]:
    """Load only verified one-token EXACT/NEAR mappings."""
    rows: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["mapping_class"] not in {"EXACT", "NEAR"} or row["status"] != "VERIFIED":
                continue
            if not row.get("sig_exchange_id") or not row.get("polymarket_token_id"):
                continue
            rows[row["sig_exchange_id"]] = row
    return rows


def _pm_files(root: Path, prelive: bool) -> list[Path]:
    source = root / "pm" / ("prelive_observations" if prelive else "observations")
    return sorted(source.rglob("*.parquet"))


def _book_files(root: Path, prelive: bool) -> list[Path]:
    source = root / "pm" / ("prelive_book_changes" if prelive else "book_changes")
    return sorted(source.rglob("*.parquet"))


def load_pm(
    root: Path, accepted_tokens: set[str], *, prelive: bool
) -> dict[str, list[dict[str, Any]]]:
    """Load valid mapped PM top-of-book observations, grouped by token."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_values = pa.array(tuple(accepted_tokens), type=pa.string())
    for path in _pm_files(root, prelive):
        table = pq.read_table(
            path,
            columns=[
                "token_id",
                "state_observed_at",
                "observed_at",
                "best_bid",
                "best_ask",
                "midpoint",
                "book_valid",
            ],
        )
        table = table.filter(pc.is_in(table["token_id"], value_set=token_values))
        table = table.filter(pc.equal(table["book_valid"], True))
        for row in table.to_pylist():
            token = row["token_id"]
            bid, ask, mid = (
                _float(row["best_bid"]),
                _float(row["best_ask"]),
                _float(row["midpoint"]),
            )
            if bid is None or ask is None or mid is None or bid > ask:
                continue
            observed = row["observed_at"]
            out[token].append(
                {
                    "t": _timestamp(row["state_observed_at"]),
                    "available_t": _timestamp(observed),
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "spread": ask - bid,
                }
            )
    for rows in out.values():
        rows.sort(key=lambda item: item["t"])
    return dict(out)


def load_topbook_ofi(
    root: Path, accepted_tokens: set[str], *, prelive: bool
) -> dict[str, list[tuple[float, float, float, float, int]]]:
    """Derive top-of-book-only OFI increments from observed book changes.

    Event availability uses `observed_at`. Only current best bid/ask levels are
    tracked; deeper book levels are never reconstructed. Each event tuple is
    (availability time, signed OFI increment, current top-depth sum, imbalance,
    top-book-event flag).
    """
    raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_values = pa.array(tuple(accepted_tokens), type=pa.string())
    for path in _book_files(root, prelive):
        table = pq.read_table(
            path,
            columns=[
                "token_id",
                "side",
                "price",
                "size",
                "observed_at",
                "best_bid",
                "best_ask",
            ],
        )
        table = table.filter(pc.is_in(table["token_id"], value_set=token_values))
        for row in table.to_pylist():
            raw[row["token_id"]].append(row)
    result: dict[str, list[tuple[float, float, float, float, int]]] = {}
    for token, rows in raw.items():
        rows.sort(key=lambda row: row["observed_at"])
        prior_bid: float | None = None
        prior_ask: float | None = None
        prior_bid_size: float | None = None
        prior_ask_size: float | None = None
        events: list[tuple[float, float, float, float, int]] = []
        for row in rows:
            bid, ask = _float(row["best_bid"]), _float(row["best_ask"])
            price, size = _float(row["price"]), _float(row["size"])
            if bid is None or ask is None or price is None or size is None or bid > ask:
                continue
            side = str(row["side"]).upper()
            bid_size = prior_bid_size if bid == prior_bid else None
            ask_size = prior_ask_size if ask == prior_ask else None
            if side in {"BUY", "BID"} and price == bid:
                bid_size = size
            if side in {"SELL", "ASK"} and price == ask:
                ask_size = size
            top_event = (
                prior_bid is None
                or prior_ask is None
                or bid != prior_bid
                or ask != prior_ask
                or (side in {"BUY", "BID"} and price == bid)
                or (side in {"SELL", "ASK"} and price == ask)
            )
            increment = 0.0
            if top_event and prior_bid is not None and prior_ask is not None:
                bid_term = 0.0
                ask_term = 0.0
                if bid_size is not None and prior_bid_size is not None:
                    if bid > prior_bid:
                        bid_term = bid_size
                    elif bid == prior_bid:
                        bid_term = bid_size - prior_bid_size
                    else:
                        bid_term = -prior_bid_size
                if ask_size is not None and prior_ask_size is not None:
                    if ask < prior_ask:
                        ask_term = -ask_size
                    elif ask == prior_ask:
                        ask_term = prior_ask_size - ask_size
                    else:
                        ask_term = prior_ask_size
                increment = bid_term + ask_term
            depth = (bid_size or 0.0) + (ask_size or 0.0)
            imbalance = (
                ((bid_size or 0.0) - (ask_size or 0.0)) / depth if depth > 0 else 0.0050633144068354
            )
            events.append(
                (_timestamp(row["observed_at"]), increment, depth, imbalance, int(top_event))
            )
            prior_bid, prior_ask = bid, ask
            prior_bid_size, prior_ask_size = bid_size, ask_size
        result[token] = events
    return result


def _at_or_before(
    rows: list[dict[str, Any]], times: list[float], t: float
) -> dict[str, Any] | None:
    index = bisect_right(times, t) - 1
    return rows[index] if index >= 0 else None


def _at_or_after(rows: list[dict[str, Any]], times: list[float], t: float) -> dict[str, Any] | None:
    index = bisect_left(times, t)
    return rows[index] if index < len(rows) else None


def _sample_features(
    rows: list[dict[str, Any]],
    book_events: list[tuple[float, float, float, float, int]] | None = None,
) -> list[dict[str, Any]]:
    """Build point-in-time 005I features; OFI is BBO-price-only, never full depth."""
    times = [row["t"] for row in rows]
    event_rows = book_events or []
    event_times = [event[0] for event in event_rows]
    prefix_ofi = [0.0]
    prefix_count = [0]
    for event in event_rows:
        prefix_ofi.append(prefix_ofi[-1] + event[1])
        prefix_count.append(prefix_count[-1] + event[4])
    out: list[dict[str, Any]] = []
    last_state_change_t = rows[0]["t"] if rows else 0.0
    for i, row in enumerate(rows):
        if i == 0 or row["bid"] != rows[i - 1]["bid"] or row["ask"] != rows[i - 1]["ask"]:
            last_state_change_t = row["t"]
        prev5 = _at_or_before(rows, times, row["t"] - 300.0)
        prev1 = _at_or_before(rows, times, row["t"] - 60.0)
        if (
            prev5 is None
            or prev1 is None
            or row["t"] - prev5["t"] - 300.0 > 75
            or row["t"] - prev1["t"] - 60.0 > 75
        ):
            continue
        fwd5 = _at_or_after(rows, times, row["t"] + 300.0)
        if fwd5 is None or fwd5["t"] - row["t"] - 300.0 > 75:
            fwd5 = None
        mid, ret5 = row["mid"], row["mid"] - prev5["mid"]
        one_returns: list[float] = []
        for j in range(max(1, i - 4), i + 1):
            delta = rows[j]["t"] - rows[j - 1]["t"]
            if 45 <= delta <= 75:
                one_returns.append(rows[j]["mid"] - rows[j - 1]["mid"])
        rv5 = 0.0
        if len(one_returns) >= 2:
            m = mean(one_returns)
            rv5 = math.sqrt(sum((x - m) ** 2 for x in one_returns) / (len(one_returns) - 1))
        left = bisect_left(event_times, row["available_t"] - 60.0)
        right = bisect_right(event_times, row["available_t"])
        event_state = event_rows[right - 1] if right else None
        depth_top = event_state[2] if event_state is not None else 0.0
        imbalance1 = event_state[3] if event_state is not None else 0.0050633144068354
        quote_events = prefix_count[right] - prefix_count[left]
        ofi = (prefix_ofi[right] - prefix_ofi[left]) / max(depth_top, 1.0)
        features = (
            mid,
            row["spread"] / max(mid, 0.001),
            depth_top if depth_top > 0 else 1255.7557112181355,
            float(quote_events) if quote_events > 0 else 46.28957943495199,
            rv5,
            imbalance1,
            row["mid"] - prev1["mid"],
            ret5,
            ofi,
        )
        p_ofi = frozen_ofi_probability(features)
        age = row["t"] - last_state_change_t
        out.append(
            {
                "t": row["t"],
                "available_t": row["available_t"],
                "mid": mid,
                "ret1": features[6],
                "ret5": ret5,
                "rv5": rv5,
                "ofi": ofi,
                "p_ofi": p_ofi,
                "fwd5": None if fwd5 is None else fwd5["mid"] - mid,
                "fwd5_available_t": None if fwd5 is None else fwd5["available_t"],
                "age": age,
                "row": row,
            }
        )
    return out


def _fit_fv_coefficients(samples: list[dict[str, Any]]) -> tuple[float, float]:
    """Two-factor least squares, fitted only to pre-16:00 observations."""
    good = [r for r in samples if r["fwd5"] is not None]
    if len(good) < 10:
        raise RuntimeError(f"only {len(good)} pre-live calibration samples")
    x1 = [r["ret5"] for r in good]
    x2 = [r["p_ofi"] - 0.5 for r in good]
    y = [r["fwd5"] for r in good]
    m1, m2, my = mean(x1), mean(x2), mean(y)
    a = sum((x - m1) ** 2 for x in x1)
    b = sum((x - m1) * (z - m2) for x, z in zip(x1, x2, strict=True))
    c = sum((z - m2) ** 2 for z in x2)
    d = sum((x - m1) * (v - my) for x, v in zip(x1, y, strict=True))
    e = sum((z - m2) * (v - my) for z, v in zip(x2, y, strict=True))
    determinant = a * c - b * b
    if determinant <= 1e-18:
        raise RuntimeError("pre-live fair-value calibration is singular")
    return (d * c - e * b) / determinant, (e * a - d * b) / determinant


def load_sig(
    db_path: Path, exchange_ids: set[str], start: float, stop: float
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Read mapped SIG BBOs and realtime trades from the local SQLite backup."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    placeholders = ",".join("?" for _ in exchange_ids)
    ids = tuple(exchange_ids)
    prices: dict[str, list[dict[str, Any]]] = defaultdict(list)
    query = (
        "SELECT exchange_id,best_bid,best_ask,rest_observed_at FROM price_observations "
        f"WHERE exchange_id IN ({placeholders}) AND rest_observed_at>=? AND rest_observed_at<=? "
        "ORDER BY rest_observed_at"
    )
    for exch, bid_raw, ask_raw, observed in conn.execute(
        query, (*ids, "2026-10-01T15:55:00", "2026-10-01T18:00:00")
    ):
        bid, ask = _float(bid_raw), _float(ask_raw)
        if bid is None or ask is None or bid > ask:
            continue
        t = __import__("datetime").datetime.fromisoformat(observed).timestamp()
        prices[exch].append({"t": t, "bid": bid, "ask": ask, "mid": (bid + ask) / 2})
    trades: list[dict[str, Any]] = []
    for exch, market, price_raw, qty_raw, executed, observed in conn.execute(
        "SELECT exchange_id,market_id,price,quantity,executed_at,observed_at FROM realtime_trades "
        f"WHERE exchange_id IN ({placeholders}) ORDER BY observed_at",
        ids,
    ):
        price, qty = _float(price_raw), _float(qty_raw)
        if price is None or qty is None or qty <= 0:
            continue
        t = __import__("datetime").datetime.fromisoformat(observed).timestamp()
        if LIVE_TRADE_START <= t <= stop:
            trades.append(
                {
                    "exchange": exch,
                    "market": market,
                    "price": price,
                    "qty": qty,
                    "t": t,
                    "executed": executed,
                }
            )
    conn.close()
    return dict(prices), trades


def _bootstrap_mean_ci(
    values_by_market: dict[str, list[float]], seed: int = 20261001
) -> tuple[float, float, float]:
    means = [mean(values) for values in values_by_market.values() if values]
    if not means:
        return math.nan, math.nan, math.nan
    rng = Random(seed)
    draws = []
    n = len(means)
    for _ in range(BOOTSTRAP_REPLICATES):
        draws.append(mean(means[rng.randrange(n)] for _ in range(n)))
    draws.sort()
    return mean(means), draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def _bootstrap_total_ci(
    values_by_market: dict[str, list[float]], seed: int = 20261001
) -> tuple[float, float]:
    """Market-cluster bootstrap interval for a summed per-market outcome."""
    values = [sum(value) for value in values_by_market.values()]
    if not values:
        return math.nan, math.nan
    rng = Random(seed)
    n = len(values)
    draws = [sum(values[rng.randrange(n)] for _ in range(n)) for _ in range(BOOTSTRAP_REPLICATES)]
    draws.sort()
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def _bootstrap_relative_ci(
    paired_mse: dict[str, tuple[float, float]], seed: int = 20261002
) -> tuple[float, float]:
    markets = list(paired_mse.values())
    if not markets:
        return math.nan, math.nan
    rng = Random(seed)
    n = len(markets)
    draws = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [markets[rng.randrange(n)] for _ in range(n)]
        base_mse = mean(row[0] for row in sample)
        model_mse = mean(row[1] for row in sample)
        draws.append((base_mse - model_mse) / max(base_mse, 1e-15))
    draws.sort()
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def _forecast_report(
    pm: dict[str, list[dict[str, Any]]],
    sig: dict[str, list[dict[str, Any]]],
    mapping_by_token: dict[str, str],
    parameters: FVParameters,
    all_samples: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for market_type in ("pm", "sig"):
        for horizon in (60, 300):
            squared: dict[str, dict[str, dict[str, list[float]]]] = {
                name: defaultdict(_new_model_errors) for name in (market_type,)
            }
            for token, samples in all_samples.items():
                exch = mapping_by_token[token]
                target_rows = pm[token] if market_type == "pm" else sig.get(exch, [])
                target_times = [r["t"] for r in target_rows]
                for sample in samples:
                    if sample["available_t"] < LIVE_START:
                        continue
                    target_time = sample["available_t"] + horizon
                    target = _at_or_after(target_rows, target_times, target_time)
                    if target is None or target["t"] - target_time > (
                        75 if market_type == "pm" else 20
                    ):
                        continue
                    if target.get("available_t", target["t"]) > LIVE_END:
                        continue
                    y = target["mid"]
                    base = sample["mid"]
                    values = {
                        "base_pm": base,
                        "reversal": fair_value(
                            base, sample["ret5"], sample["p_ofi"], parameters, use_ofi=False
                        ),
                        "ofi": fair_value(
                            base, sample["ret5"], sample["p_ofi"], parameters, use_reversal=False
                        ),
                        "combined": fair_value(base, sample["ret5"], sample["p_ofi"], parameters),
                    }
                    market_id = exch
                    for model, pred in values.items():
                        squared[market_type][market_id][model].append((y - pred) ** 2)
            report: dict[str, Any] = {}
            for model in FORECAST_MODEL_NAMES:
                base_by_market = squared[market_type]
                paired = {
                    market: [mean(rows["base_pm"]), mean(rows[model])]
                    for market, rows in base_by_market.items()
                    if rows["base_pm"] and rows[model]
                }
                if not paired:
                    report[model] = {
                        "markets": 0,
                        "n": 0,
                        "mse": None,
                        "relative_improvement_vs_pm": None,
                        "ci95_relative": None,
                    }
                    continue
                base_mse = mean(pair[0] for pair in paired.values())
                model_mse = mean(pair[1] for pair in paired.values())
                lo, hi = _bootstrap_relative_ci(
                    {market: (pair[0], pair[1]) for market, pair in paired.items()}
                )
                total_n = sum(len(v[model]) for v in base_by_market.values())
                report[model] = {
                    "markets": len(paired),
                    "n": total_n,
                    "mse": model_mse,
                    "relative_improvement_vs_pm": (base_mse - model_mse) / max(base_mse, 1e-15),
                    "ci95_relative": [lo, hi],
                }
            result[f"{market_type}_h{horizon}"] = report
    return result


def _simulate_policy(
    trades: list[dict[str, Any]],
    sig: dict[str, list[dict[str, Any]]],
    pm: dict[str, list[dict[str, Any]]],
    samples: dict[str, list[dict[str, Any]]],
    exch_to_token: dict[str, str],
    params: FVParameters,
    *,
    use_fv: bool,
    use_reversal: bool = True,
    use_ofi: bool = True,
    use_pull: bool,
    k: float,
) -> dict[str, Any]:
    # Each accepted market maps one SIG contract to its Polymarket Yes token.
    inv: dict[str, float] = defaultdict(float)
    cash: dict[str, float] = defaultdict(float)
    fills: list[dict[str, Any]] = []
    for trade in trades:
        exch = trade["exchange"]
        token = exch_to_token.get(exch)
        if token is None or trade["t"] < LIVE_TRADE_START:
            continue
        si = bisect_right([r["t"] for r in sig.get(exch, [])], trade["t"]) - 1
        if si < 0:
            continue
        sample_rows = samples.get(token, [])
        stimes = [r["available_t"] for r in sample_rows]
        sidx = bisect_right(stimes, trade["t"]) - 1
        if sidx < 0:
            continue
        sample = sample_rows[sidx]
        pm_mid = sample["mid"]
        pm_age = sample["age"] + max(0.0, trade["t"] - sample["available_t"])
        if use_pull and pm_age >= params.hazard_pull_threshold:
            continue
        fv = (
            fair_value(
                pm_mid,
                sample["ret5"],
                sample["p_ofi"],
                params,
                use_reversal=use_reversal,
                use_ofi=use_ofi,
            )
            if use_fv
            else pm_mid
        )
        center = min(1.0, max(0.0, fv - k * TICK * inv[exch] / 500.0))
        bid = round(max(0.0, center - QUOTE_HALF_WIDTH) / TICK) * TICK
        ask = round(min(1.0, center + QUOTE_HALF_WIDTH) / TICK) * TICK
        sig_row = sig[exch][si]
        buy_passive = bid < sig_row["ask"]
        sell_passive = ask > sig_row["bid"]
        side = (
            "buy"
            if buy_passive and trade["price"] <= bid
            else "sell"
            if sell_passive and trade["price"] >= ask
            else ""
        )
        if not side:
            continue
        fill_price = bid if side == "buy" else ask
        signed_qty = trade["qty"] if side == "buy" else -trade["qty"]
        inv[exch] += signed_qty
        cash[exch] -= signed_qty * fill_price
        fills.append(
            {
                "exchange": exch,
                "t": trade["t"],
                "price": fill_price,
                "qty": trade["qty"],
                "signed_qty": signed_qty,
                "pm_mid": pm_mid,
            }
        )
    # Side-aware PM markouts at 60/300 seconds. Use first available valid snapshot after target.
    markouts: dict[int, dict[str, list[float]]] = {60: defaultdict(list), 300: defaultdict(list)}
    for fill in fills:
        token = exch_to_token[fill["exchange"]]
        rows = pm.get(token, [])
        times = [r["t"] for r in rows]
        for horizon in (60, 300):
            future = _at_or_after(rows, times, fill["t"] + horizon)
            if (
                future is None
                or future["available_t"] > LIVE_END
                or future["t"] - (fill["t"] + horizon) > 75
            ):
                continue
            mark = (future["mid"] - fill["price"]) * (1 if fill["signed_qty"] > 0 else -1)
            markouts[horizon][fill["exchange"]].append(mark)
    end_pm: dict[str, float] = {}
    for exch, token in exch_to_token.items():
        rows = [r for r in pm.get(token, []) if LIVE_TRADE_START <= r["available_t"] <= LIVE_END]
        if rows:
            end_pm[exch] = rows[-1]["mid"]
    per_market_pnl = {
        exch: cash[exch] + inv[exch] * end_pm.get(exch, 0.0) for exch in exch_to_token
    }
    gross_inv = sum(abs(value) for value in inv.values())
    return {
        "fills": len(fills),
        "markets_filled": len({f["exchange"] for f in fills}),
        "end_inventory_net_lots": sum(inv.values()),
        "end_inventory_gross_lots": gross_inv,
        "gross_pnl_marked_to_last_pm": sum(per_market_pnl.values()),
        "gross_pnl_market_bootstrap_ci95": list(
            _bootstrap_total_ci({k0: [v] for k0, v in per_market_pnl.items()})
        ),
        "markout_cents_per_lot": {
            str(h): {
                "mean": _bootstrap_mean_ci(data)[0] * 100 if data else None,
                "ci95": [x * 100 for x in _bootstrap_mean_ci(data)[1:]] if data else None,
                "markets": len(data),
                "fills": sum(len(v) for v in data.values()),
            }
            for h, data in markouts.items()
        },
    }


def _build_samples(
    pm: dict[str, list[dict[str, Any]]],
    cutoff: float,
    book_events: dict[str, list[tuple[float, float, float, float, int]]] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    book_events = book_events or {}
    samples = {
        token: _sample_features(rows, book_events.get(token, [])) for token, rows in pm.items()
    }
    prelive = [
        sample
        for token_rows in samples.values()
        for sample in token_rows
        if (
            sample["available_t"] < cutoff
            and sample["t"] + 300 < cutoff
            and sample["fwd5_available_t"] is not None
            and sample["fwd5_available_t"] < cutoff
            and sample["fwd5"] is not None
        )
    ]
    return samples, prelive


def _movement_summary(
    grouped_rows: dict[str, list[dict[str, Any]]], *, time_key: str
) -> dict[str, Any]:
    abs_steps: list[float] = []
    ranges: list[float] = []
    start_end: list[float] = []
    moved = 0
    observations = 0
    for rows in grouped_rows.values():
        selected = [row for row in rows if LIVE_START <= row[time_key] <= LIVE_END]
        if len(selected) < 2:
            continue
        mids = [row["mid"] for row in selected]
        changes = [abs(mids[i] - mids[i - 1]) for i in range(1, len(mids))]
        observations += len(selected)
        moved += int(any(change > 0 for change in changes))
        abs_steps.extend(changes)
        ranges.append(max(mids) - min(mids))
        start_end.append(abs(mids[-1] - mids[0]))
    abs_steps.sort()
    ranges.sort()
    start_end.sort()

    def median(values: list[float]) -> float | None:
        if not values:
            return None
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2

    median_abs_step = median(abs_steps)
    median_range = median(ranges)
    median_start_end = median(start_end)

    return {
        "markets_with_2plus_observations": len(ranges),
        "markets_with_any_mid_change": moved,
        "observations": observations,
        "median_abs_step_cents": None if median_abs_step is None else median_abs_step * 100,
        "median_market_range_cents": None if median_range is None else median_range * 100,
        "median_abs_start_end_cents": None if median_start_end is None else median_start_end * 100,
        "max_market_range_cents": max(ranges, default=0.0) * 100,
    }


def run(data_root: Path, mapping_path: Path, output: Path) -> dict[str, Any]:
    mapping_rows = load_mapping(mapping_path)
    exch_to_token = {exch: row["polymarket_token_id"] for exch, row in mapping_rows.items()}
    token_to_exch = {token: exch for exch, token in exch_to_token.items()}
    accepted_tokens = set(token_to_exch)
    pm_pre = load_pm(data_root, accepted_tokens, prelive=True)
    pm_live = load_pm(data_root, accepted_tokens, prelive=False)
    book_pre = load_topbook_ofi(data_root, accepted_tokens, prelive=True)
    book_live = load_topbook_ofi(data_root, accepted_tokens, prelive=False)
    book_all = {
        token: sorted(book_pre.get(token, []) + book_live.get(token, []), key=lambda row: row[0])
        for token in accepted_tokens
    }
    pm_all = {
        token: sorted(pm_pre.get(token, []) + pm_live.get(token, []), key=lambda r: r["t"])
        for token in accepted_tokens
    }
    # The analysis split is fixed before fitting: all accepted PM observations before 16:00Z.
    all_samples = {
        token: _sample_features(rows, book_all.get(token, [])) for token, rows in pm_all.items()
    }
    # Calibration targets are computed from the pre-live extract alone so no
    # forward endpoint can cross 16:00Z into the untouched TEST set.
    _, prelive_samples = _build_samples(pm_pre, LIVE_START, book_pre)
    beta_reversal, beta_ofi = _fit_fv_coefficients(prelive_samples)
    params = FVParameters(beta_reversal, beta_ofi, STALE_PULL_SECONDS)
    sig, trades = load_sig(
        data_root / "sig_realtime.sqlite3", set(mapping_rows), LIVE_START - 300, LIVE_END
    )
    # TEST input rows are immutable and separated here before any metric calculations.
    test_samples = {
        token: [r for r in rows if LIVE_START <= r["available_t"] <= LIVE_END]
        for token, rows in all_samples.items()
    }
    forecasts = _forecast_report(pm_all, sig, token_to_exch, params, test_samples)

    # The inventory grid was declared in model.py before any TEST replay results were read.
    economics: dict[str, Any] = {}
    for fv_name, use_fv, use_reversal, use_ofi in (
        ("PM_mid", False, False, False),
        ("FV_reversal_only", True, True, False),
        ("FV_combined", True, True, True),
    ):
        # Fair-value components are ablated above in forecasts; economic replay compares the
        # PM baseline with the two signed candidates. OFI-only is added as its own ablation.
        for gate in (False, True):
            for k in INVENTORY_SKEW_GRID_TICKS_PER_500:
                key = f"{fv_name}|pull={int(gate)}|k={k:g}"
                economics[key] = _simulate_policy(
                    trades,
                    sig,
                    pm_all,
                    test_samples,
                    exch_to_token,
                    params,
                    use_fv=use_fv,
                    use_reversal=use_reversal,
                    use_ofi=use_ofi,
                    use_pull=gate,
                    k=k,
                )
    for gate in (False, True):
        for k in INVENTORY_SKEW_GRID_TICKS_PER_500:
            key = f"FV_ofi_only|pull={int(gate)}|k={k:g}"
            ofi_only_params = FVParameters(0.0, beta_ofi, STALE_PULL_SECONDS)
            economics[key] = _simulate_policy(
                trades,
                sig,
                pm_all,
                test_samples,
                exch_to_token,
                ofi_only_params,
                use_fv=True,
                use_reversal=False,
                use_ofi=True,
                use_pull=gate,
                k=k,
            )
    report = {
        "task": "FV-LIVE-001",
        "test_window_utc": ["2026-10-01T16:00:00Z", "2026-10-01T17:09:58.380029Z"],
        "live_trade_window_utc": ["2026-10-01T16:30:00Z", "2026-10-01T17:09:58.380029Z"],
        "mapping": {
            "verified_exact_or_near": len(mapping_rows),
            "markets_with_pm_test_samples": sum(bool(v) for v in test_samples.values()),
        },
        "fit": {
            "prelive_cutoff_utc": "2026-10-01T16:00:00Z",
            "prelive_calibration_samples": len(prelive_samples),
            "reversal_beta_5m": beta_reversal,
            "ofi_score_to_price_beta_5m": beta_ofi,
            "ofi_coefficient_status": (
                "frozen logistic coefficients; score-to-price calibration fit pre-live only"
            ),
            "hazard_gate": (
                "pull when PM BBO unchanged for >=210s (005I stale threshold; "
                "005F/PRED-006 coefficients are not serialized)"
            ),
        },
        "frozen_inventory_skew_grid_ticks_per_500": list(INVENTORY_SKEW_GRID_TICKS_PER_500),
        "forecast": forecasts,
        "economics": economics,
        "price_movement": {
            "polymarket": _movement_summary(pm_all, time_key="available_t"),
            "sig": _movement_summary(sig, time_key="t"),
        },
        "input_counts": {
            "sig_price_observations_test": sum(
                sum(LIVE_START <= r["t"] <= LIVE_END for r in rows) for rows in sig.values()
            ),
            "sig_realtime_trades_test": len(trades),
            "pm_observation_markets_test": sum(bool(v) for v in test_samples.values()),
            "pm_topbook_change_events_prelive": sum(len(v) for v in book_pre.values()),
            "pm_topbook_change_events_test": sum(len(v) for v in book_live.values()),
        },
        "limitations": [
            (
                "OFI uses observed top-of-book size/price changes, normalized by top-depth sum; "
                "no full depth is reconstructed."
            ),
            (
                "The frozen model expects depth5, while the replay supplies top-depth sum; "
                "quote_events count top-book events and imbalance1 uses top-level sizes."
            ),
            (
                "PM pull gate tests unchanged-BBO age only; exact 005F/PRED-006 fitted hazard "
                "artifacts and 005G state-dwell model are unavailable."
            ),
            (
                "Queue priority is assumed and every SIG realtime trade at or through quote is "
                "fully filled; optimistic."
            ),
            (
                "Gross mark-to-market PnL excludes fees, rebates, latency, cancellations, "
                "capital costs and settlement."
            ),
            (
                "PM panel is one-minute partitioned; five-minute endpoints are sparse and "
                "overlapping observations are dependent."
            ),
        ],
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.data_root, args.mapping, args.output)
    print(
        json.dumps(
            {"output": str(args.output), "inputs": report["input_counts"], "fit": report["fit"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
