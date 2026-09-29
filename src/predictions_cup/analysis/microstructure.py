"""Observable-time SIG microstructure and markout diagnostics for CAPTURE-001."""

from __future__ import annotations

import bisect
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds


@dataclass(frozen=True, slots=True)
class _Bbo:
    at: datetime
    bid: Decimal
    ask: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class _Trade:
    at: datetime
    price: Decimal
    quantity: Decimal | None


def _parquet_files(root: Path, stream: str) -> tuple[Path, ...]:
    path = root / stream
    if not path.exists():
        return ()
    return tuple(sorted(path.rglob("*.parquet")))


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p50": None, "p90": None, "p99": None}
    array = np.asarray(values, dtype=float)
    p10, p50, p90, p99 = np.percentile(array, [10, 50, 90, 99])
    return {
        "p10": float(p10),
        "p50": float(p50),
        "p90": float(p90),
        "p99": float(p99),
    }


def _mean(values: list[float]) -> float | None:
    return None if not values else float(np.mean(np.asarray(values, dtype=float)))


def _safe_rate(count: int, seconds: float) -> float | None:
    return None if seconds <= 0 else count / (seconds / 60.0)


def _bucket_15m(value: datetime) -> str:
    epoch = int(value.astimezone(UTC).timestamp())
    bucket = epoch - epoch % 900
    return datetime.fromtimestamp(bucket, tz=UTC).isoformat()


def _latest_at_or_before(
    points: list[_Bbo],
    times: list[datetime],
    at: datetime,
) -> _Bbo | None:
    index = bisect.bisect_right(times, at) - 1
    return None if index < 0 else points[index]


def _first_at_or_after(
    points: list[_Bbo],
    times: list[datetime],
    at: datetime,
) -> _Bbo | None:
    index = bisect.bisect_left(times, at)
    return None if index >= len(points) else points[index]


def _aggressor(
    trade: _Trade,
    prior: _Bbo | None,
    *,
    max_bbo_age: timedelta,
) -> str:
    if prior is None or trade.at - prior.at > max_bbo_age:
        return "UNKNOWN"
    if trade.price >= prior.ask:
        return "BUY"
    if trade.price <= prior.bid:
        return "SELL"
    return "UNKNOWN"


def _depth_metrics(payload_json: object) -> tuple[float, float, float | None, float | None] | None:
    if not isinstance(payload_json, str):
        return None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    def side(name: str) -> tuple[float, float | None]:
        raw = payload.get(name)
        if not isinstance(raw, list):
            return 0.0, None
        quantities: list[float] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            quantity = _decimal(item.get("quantity"))
            if quantity is not None and quantity >= 0:
                quantities.append(float(quantity))
        total = sum(quantities)
        share = None if total <= 0 or not quantities else quantities[0] / total
        return total, share

    bid_total, bid_share = side("bids")
    ask_total, ask_share = side("asks")
    return bid_total, ask_total, bid_share, ask_share


def analyze_sig_microstructure(
    root: Path,
    *,
    markout_horizons_seconds: tuple[int, ...] = (1, 5, 30, 300),
    aggressor_bbo_max_age_seconds: float = 30.0,
    markout_max_sampling_delay_seconds: float = 30.0,
) -> tuple[
    dict[str, Any],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Build descriptive diagnostics using only locally observable-time capture."""
    files = _parquet_files(root, "normalized_events")
    if not files:
        return (
            {"available": False},
            [],
            [],
            [],
            [],
        )

    quotes: dict[str, list[_Bbo]] = defaultdict(list)
    trades: dict[str, list[_Trade]] = defaultdict(list)
    depths: dict[str, list[tuple[datetime, float, float, float | None, float | None]]] = (
        defaultdict(list)
    )
    bucket_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    dataset = ds.dataset([str(path) for path in files], format="parquet")
    requested = (
        "event_type",
        "exchange_id",
        "observed_at",
        "price",
        "quantity",
        "best_bid",
        "best_ask",
        "payload_json",
    )
    columns = [column for column in requested if column in dataset.schema.names]
    scanner = dataset.scanner(columns=columns, batch_size=65_536)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            exchange_id = row.get("exchange_id")
            observed = row.get("observed_at")
            event_type = row.get("event_type")
            if (
                not isinstance(exchange_id, str)
                or not isinstance(observed, datetime)
                or not isinstance(event_type, str)
            ):
                continue
            observed = observed.astimezone(UTC)
            bucket_counts[(exchange_id, _bucket_15m(observed))][event_type] += 1
            if event_type == "BBO_SNAPSHOT":
                bid = _decimal(row.get("best_bid"))
                ask = _decimal(row.get("best_ask"))
                if bid is not None and ask is not None and ask >= bid:
                    quotes[exchange_id].append(_Bbo(observed, bid, ask))
            elif event_type == "TRADE":
                price = _decimal(row.get("price"))
                if price is not None:
                    trades[exchange_id].append(
                        _Trade(observed, price, _decimal(row.get("quantity")))
                    )
            elif event_type == "DEPTH_SNAPSHOT":
                metrics = _depth_metrics(row.get("payload_json"))
                if metrics is not None:
                    depths[exchange_id].append((observed, *metrics))

    for quote_values in quotes.values():
        quote_values.sort(key=lambda item: item.at)
    for trade_values in trades.values():
        trade_values.sort(key=lambda item: item.at)
    for depth_values in depths.values():
        depth_values.sort(key=lambda item: item[0])

    market_rows: list[dict[str, object]] = []
    all_lifetimes: list[float] = []
    all_mid_moves: list[float] = []
    all_trade_intervals: list[float] = []
    aggressive_counts: Counter[str] = Counter()
    jump_005 = 0
    jump_010 = 0

    exchange_ids = sorted(set(quotes) | set(trades) | set(depths))
    for exchange_id in exchange_ids:
        q = quotes.get(exchange_id, [])
        t = trades.get(exchange_id, [])
        economic_times: list[datetime] = []
        spreads: list[float] = []
        mid_moves: list[float] = []
        prior_key: tuple[Decimal, Decimal] | None = None
        prior_mid: Decimal | None = None
        for point in q:
            spreads.append(float(point.spread))
            key = (point.bid, point.ask)
            if prior_key == key:
                continue
            if economic_times:
                lifetime = (point.at - economic_times[-1]).total_seconds()
                if lifetime >= 0:
                    all_lifetimes.append(lifetime)
            economic_times.append(point.at)
            if prior_mid is not None:
                move = abs(float(point.mid - prior_mid))
                mid_moves.append(move)
                all_mid_moves.append(move)
                if move >= 0.05:
                    jump_005 += 1
                if move >= 0.10:
                    jump_010 += 1
            prior_key = key
            prior_mid = point.mid

        trade_intervals = [
            (right.at - left.at).total_seconds()
            for left, right in zip(t, t[1:], strict=False)
            if right.at >= left.at
        ]
        all_trade_intervals.extend(trade_intervals)

        times = [point.at for point in q]
        local_aggressors: Counter[str] = Counter()
        max_age = timedelta(seconds=aggressor_bbo_max_age_seconds)
        for trade in t:
            side = _aggressor(
                trade,
                _latest_at_or_before(q, times, trade.at),
                max_bbo_age=max_age,
            )
            local_aggressors[side] += 1
            aggressive_counts[side] += 1

        observations = [point.at for point in q] + [trade.at for trade in t]
        span_seconds = (
            0.0
            if len(observations) < 2
            else (max(observations) - min(observations)).total_seconds()
        )
        market_rows.append(
            {
                "exchange_id": exchange_id,
                "bbo_samples": len(q),
                "economic_bbo_updates": max(0, len(economic_times) - 1),
                "economic_updates_per_minute": _safe_rate(
                    max(0, len(economic_times) - 1),
                    span_seconds,
                ),
                "trades": len(t),
                "trades_per_minute": _safe_rate(len(t), span_seconds),
                "aggressive_buy": local_aggressors["BUY"],
                "aggressive_sell": local_aggressors["SELL"],
                "aggressor_unknown": local_aggressors["UNKNOWN"],
                "spread_p50": _percentiles(spreads)["p50"],
                "spread_p90": _percentiles(spreads)["p90"],
                "bbo_lifetime_seconds_p50": _percentiles(
                    [
                        (right - left).total_seconds()
                        for left, right in zip(economic_times, economic_times[1:], strict=False)
                    ]
                )["p50"],
                "bbo_lifetime_seconds_p90": _percentiles(
                    [
                        (right - left).total_seconds()
                        for left, right in zip(economic_times, economic_times[1:], strict=False)
                    ]
                )["p90"],
                "absolute_mid_move_p90": _percentiles(mid_moves)["p90"],
            }
        )

    markout_rows: list[dict[str, object]] = []
    for horizon in markout_horizons_seconds:
        maker_markouts: list[float] = []
        absolute_moves: list[float] = []
        sampling_delays: list[float] = []
        candidates = 0
        adverse = 0
        for exchange_id, exchange_trades in trades.items():
            q = quotes.get(exchange_id, [])
            if not q:
                continue
            times = [point.at for point in q]
            max_age = timedelta(seconds=aggressor_bbo_max_age_seconds)
            for trade in exchange_trades:
                side = _aggressor(
                    trade,
                    _latest_at_or_before(q, times, trade.at),
                    max_bbo_age=max_age,
                )
                if side == "UNKNOWN":
                    continue
                target = trade.at + timedelta(seconds=horizon)
                future = _first_at_or_after(q, times, target)
                if future is None:
                    continue
                delay = (future.at - target).total_seconds()
                if (
                    delay < 0
                    or delay > markout_max_sampling_delay_seconds
                ):
                    continue
                candidates += 1
                future_mid = future.mid
                maker = (
                    trade.price - future_mid
                    if side == "BUY"
                    else future_mid - trade.price
                )
                maker_value = float(maker)
                maker_markouts.append(maker_value)
                absolute_moves.append(abs(float(future_mid - trade.price)))
                sampling_delays.append(delay)
                if maker_value < 0:
                    adverse += 1
        markout_rows.append(
            {
                "horizon_seconds": horizon,
                "classified_samples": candidates,
                "maker_markout_mean": _mean(maker_markouts),
                "maker_markout_p10": _percentiles(maker_markouts)["p10"],
                "maker_markout_p50": _percentiles(maker_markouts)["p50"],
                "maker_markout_p90": _percentiles(maker_markouts)["p90"],
                "adverse_selection_rate": (
                    None if candidates == 0 else adverse / candidates
                ),
                "absolute_post_trade_move_p50": _percentiles(absolute_moves)["p50"],
                "sampling_delay_seconds_p50": _percentiles(sampling_delays)["p50"],
                "sampling_delay_seconds_p90": _percentiles(sampling_delays)["p90"],
            }
        )

    depth_rows: list[dict[str, object]] = []
    all_bid_depth: list[float] = []
    all_ask_depth: list[float] = []
    for exchange_id, depth_values in sorted(depths.items()):
        bid_depth = [item[1] for item in depth_values]
        ask_depth = [item[2] for item in depth_values]
        bid_share = [item[3] for item in depth_values if item[3] is not None]
        ask_share = [item[4] for item in depth_values if item[4] is not None]
        all_bid_depth.extend(bid_depth)
        all_ask_depth.extend(ask_depth)
        depth_rows.append(
            {
                "exchange_id": exchange_id,
                "depth_snapshots": len(depth_values),
                "bid_depth_p50": _percentiles(bid_depth)["p50"],
                "ask_depth_p50": _percentiles(ask_depth)["p50"],
                "bid_top_level_share_p50": _percentiles(
                    [float(value) for value in bid_share]
                )["p50"],
                "ask_top_level_share_p50": _percentiles(
                    [float(value) for value in ask_share]
                )["p50"],
            }
        )

    bucket_rows = [
        {
            "exchange_id": exchange_id,
            "bucket_start": bucket,
            "bbo_snapshots": counts["BBO_SNAPSHOT"],
            "depth_snapshots": counts["DEPTH_SNAPSHOT"],
            "trades": counts["TRADE"],
            "book_dirty": counts["BOOK_DIRTY"],
            "trust_transitions": counts["TRUST_TRANSITION"],
        }
        for (exchange_id, bucket), counts in sorted(bucket_counts.items())
    ]

    summary: dict[str, Any] = {
        "available": True,
        "exchange_count": len(exchange_ids),
        "economic_bbo_lifetime_seconds": _percentiles(all_lifetimes),
        "trade_arrival_interval_seconds": _percentiles(all_trade_intervals),
        "absolute_midpoint_move": _percentiles(all_mid_moves),
        "jump_count_abs_0_05": jump_005,
        "jump_count_abs_0_10": jump_010,
        "aggressor_classification": {
            "BUY": aggressive_counts["BUY"],
            "SELL": aggressive_counts["SELL"],
            "UNKNOWN": aggressive_counts["UNKNOWN"],
            "rule": (
                "BUY if trade >= latest captured ask; SELL if trade <= latest "
                "captured bid; otherwise UNKNOWN; prior BBO age <= "
                f"{aggressor_bbo_max_age_seconds:g}s"
            ),
        },
        "tracked_bid_depth": _percentiles(all_bid_depth),
        "tracked_ask_depth": _percentiles(all_ask_depth),
        "markout_rule": (
            "maker-perspective sign from defensibly classified aggressor; first captured "
            "BBO at/after each horizon; rows report sampling delay and reject delay > "
            f"{markout_max_sampling_delay_seconds:g}s; no passive queue inference"
        ),
    }
    return summary, market_rows, markout_rows, depth_rows, bucket_rows
