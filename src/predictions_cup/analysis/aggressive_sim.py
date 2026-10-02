"""Offline replay grid for the SIG/Polymarket maker and residual-taker policies.

This is a bounded, observable-time simulator. Maker evaluation times come from the
execution journal for the configured live universe. Added markets are sampled at
the median observed live quote cadence. Fill fractions are calibrated from actual
MAKE and FV-TAKE journal intents; simulated fills are expected quantities rather
than claims about queue position.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow.dataset as pads

from predictions_cup.maker.contracts import (
    ExternalQuoteState,
    MakerMarketSnapshot,
    QuoteContext,
)
from predictions_cup.maker.direct_pm import DirectPolymarketFairValueProvider
from predictions_cup.maker.engine import MakerConfig, MakerEngine
from predictions_cup.maker.policies import (
    BinaryCaraInventoryModel,
    ConservativeEligibilityPolicy,
    ConservativeSpreadPolicy,
    InventoryConfidenceSizePolicy,
    NullPredictiveAdjuster,
    NullToxicityProvider,
)
from predictions_cup.maker.residual_taker import (
    ResidualInput,
    ResidualTakerSignal,
)
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)
from predictions_cup.runtime.models import (
    SIG_TICK,
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)

SIZE_MULTIPLIERS = (1, 2, 4)
EDGE_FACTORS = (1.0, 0.75, 0.5)
INVENTORY_CAPS = (200, 400)
TAKER_THRESHOLDS = (("current", 0.02), ("lower", 0.01))
MARKET_SETS = ("current76", "all_mapped")
_SIG_TICK = float(SIG_TICK)
_NS_SECOND = 1_000_000_000
_NS_MINUTE = 60 * _NS_SECOND
_MAX_SIG_AGE_NS = 15_000 * 1_000_000
_MAX_FV_AGE_NS = 1_000 * 1_000_000
_MAX_TAKER_PM_AGE_NS = 35 * 1_000_000_000
_MAX_MARK_AGE_NS = 120 * _NS_SECOND
_MARKOUT_HORIZONS = (60, 300, 1_800, 7_200)


@dataclass(frozen=True, slots=True)
class QuotePoint:
    event_ns: int
    observed_ns: int
    bid: float | None
    ask: float | None
    trusted: bool


@dataclass(frozen=True, slots=True)
class DepthPoint:
    event_ns: int
    observed_ns: int
    bid_size: float | None
    ask_size: float | None


@dataclass(frozen=True, slots=True)
class QuoteView:
    event_ns: int
    observed_ns: int
    bid: float
    ask: float
    trusted: bool

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(slots=True)
class TimeSeries:
    points: list[QuotePoint]
    times: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.points.sort(key=lambda point: (point.event_ns, point.observed_ns))
        self.times = [point.event_ns for point in self.points]

    def at(self, at_ns: int, *, max_age_ns: int | None = None) -> QuoteView | None:
        index = bisect_right(self.times, at_ns) - 1
        if index < 0:
            return None
        point = self.points[index]
        age = at_ns - point.observed_ns
        if age < 0 or (max_age_ns is not None and age > max_age_ns):
            return None
        if not point.trusted or point.bid is None or point.ask is None:
            return None
        if not 0.0 <= point.bid <= point.ask <= 1.0:
            return None
        return QuoteView(point.event_ns, point.observed_ns, point.bid, point.ask, True)

    def forward(
        self,
        at_ns: int,
        *,
        max_delay_ns: int = _MAX_MARK_AGE_NS,
    ) -> QuoteView | None:
        index = bisect_left(self.times, at_ns)
        while index < len(self.points):
            point = self.points[index]
            if point.event_ns - at_ns > max_delay_ns:
                return None
            if (
                point.trusted
                and point.bid is not None
                and point.ask is not None
                and 0.0 <= point.bid <= point.ask <= 1.0
                and 0 <= point.event_ns - point.observed_ns <= _MAX_MARK_AGE_NS
            ):
                return QuoteView(
                    point.event_ns,
                    point.observed_ns,
                    point.bid,
                    point.ask,
                    True,
                )
            index += 1
        return None


@dataclass(slots=True)
class DepthSeries:
    points: list[DepthPoint]
    times: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.points.sort(key=lambda point: (point.event_ns, point.observed_ns))
        self.times = [point.event_ns for point in self.points]

    def at(self, at_ns: int, *, max_age_ns: int) -> DepthPoint | None:
        index = bisect_right(self.times, at_ns) - 1
        if index < 0:
            return None
        point = self.points[index]
        age = at_ns - point.observed_ns
        if age < 0 or age > max_age_ns:
            return None
        return point


@dataclass(slots=True)
class CaptureHistory:
    sig: dict[str, TimeSeries]
    polymarket: dict[str, TimeSeries]
    depth: dict[str, DepthSeries]
    sig_rows: int
    polymarket_rows: dict[str, int]

    def mark(
        self,
        record: MarketMapping,
        at_ns: int,
        *,
        max_age_ns: int,
        forward: bool = False,
    ) -> tuple[float, float] | None:
        quotes: list[QuoteView] = []
        identities = _polymarket_identities(record)
        if not identities:
            return None
        for identity in identities:
            series = self.polymarket.get(identity.mapped_token_id)
            if series is None:
                return None
            quote = series.forward(at_ns) if forward else series.at(at_ns, max_age_ns=max_age_ns)
            if quote is None:
                return None
            quotes.append(quote)

        if record.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}:
            quote = quotes[0]
            if record.mapping_direction is MappingDirection.COMPLEMENT:
                return 1.0 - quote.ask, 1.0 - quote.bid
            if record.mapping_direction is MappingDirection.SAME:
                return quote.bid, quote.ask
            return None

        if record.mapping_class is MappingClass.DERIVED:
            bid = sum(quote.bid for quote in quotes)
            ask = sum(quote.ask for quote in quotes)
            if 0.0 <= bid <= ask <= 1.0:
                return bid, ask
        return None

    def markout(
        self, record: MarketMapping, at_ns: int, horizon_seconds: int
    ) -> tuple[float, float] | None:
        return self.mark(
            record,
            at_ns + horizon_seconds * _NS_SECOND,
            max_age_ns=_MAX_MARK_AGE_NS,
            forward=True,
        )


@dataclass(frozen=True, slots=True)
class ActualOrder:
    operation_id: str
    intent_id: str
    exchange_id: str
    family: str
    at_ns: int
    sign: int
    price: float
    quantity: float
    fair_value: float | None
    distance_bucket: int
    filled_quantity: float
    fill_price: float
    fill_at_ns: int


@dataclass(frozen=True, slots=True)
class MakerOpportunity:
    at_ns: int
    exchange_id: str


@dataclass(frozen=True, slots=True)
class TakerOpportunity:
    at_ns: int
    exchange_id: str
    sign: int
    price: float
    quantity: float


@dataclass(frozen=True, slots=True)
class SimFill:
    at_ns: int
    exchange_id: str
    sign: int
    price: float
    quantity: float
    family: str


@dataclass(frozen=True, slots=True)
class Calibration:
    overall_fraction: float
    by_distance: dict[int, float]

    def fraction(self, distance_bucket: int) -> float:
        return min(1.0, max(0.0, self.by_distance.get(distance_bucket, self.overall_fraction)))


@dataclass(frozen=True, slots=True)
class CalibrationCheck:
    training_orders: int
    validation_orders: int
    predicted_validation_shares: float
    actual_validation_shares: float
    error_percent: float | None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    size_multiplier: int
    edge_factor: float
    cap: int
    taker_label: str
    taker_threshold: float
    market_set: str
    maker_opportunities: int
    maker_quotes: int
    taker_signals: int
    expected_fill_shares: float
    max_inventory: float
    max_inventory_exchange_id: str | None
    pnl_pm_exit: float | None
    pnl_marked_markets: int
    pnl_unmarked_positions: int
    pnl_unmarked_exchange_ids: tuple[str, ...]
    maker_markouts: dict[int, tuple[float | None, float]]
    taker_markouts: dict[int, tuple[float | None, float]]


class ScaledSpreadPolicy:
    """Scale the accepted MAKE half-spread while retaining its one-half-tick floor."""

    policy_id = "aggressive-sim-scaled-conservative-spread"
    version = "aggressive-sim-v1"

    def __init__(self, inner: ConservativeSpreadPolicy, factor: float) -> None:
        if not math.isfinite(factor) or not 0.0 < factor <= 1.0:
            raise ValueError("edge factor must lie in (0, 1]")
        self.inner = inner
        self.factor = factor

    def half_spread(self, context: QuoteContext) -> float:
        return max(_SIG_TICK * 0.5, self.inner.half_spread(context) * self.factor)


def _parse_time(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _time_ns(value: str | datetime) -> int:
    return int(_parse_time(value).timestamp() * _NS_SECOND)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_time(value: object) -> int:
    if not isinstance(value, datetime):
        raise TypeError("Parquet timestamp did not decode as datetime")
    return _time_ns(value)


def _mapping_tokens(record: MarketMapping) -> tuple[str, ...]:
    if record.direct_polymarket is not None:
        return (record.direct_polymarket.mapped_token_id,)
    if record.mapping_class is MappingClass.DERIVED:
        return tuple(component.mapped_token_id for component in record.polymarket_components)
    return ()


def _polymarket_identities(record: MarketMapping) -> tuple[Any, ...]:
    if record.direct_polymarket is not None:
        return (record.direct_polymarket,)
    if record.mapping_class is MappingClass.DERIVED:
        return record.polymarket_components
    return ()


def _supported_derived(record: MarketMapping) -> bool:
    notes = " ".join(
        value.lower()
        for value in (record.semantic_notes, record.resolution_notes)
        if value is not None
    )
    return ("union/sum" in notes or "sum of" in notes) and (
        "mutually exclusive" in notes or "partition" in notes
    )


def _sim_tradeable(record: MarketMapping) -> bool:
    if record.status is not MappingStatus.VERIFIED:
        return False
    if record.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}:
        return True
    return record.mapping_class is MappingClass.DERIVED and _supported_derived(record)


def load_sig_history(
    path: Path,
    exchange_ids: set[str],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, TimeSeries], int]:
    if not exchange_ids:
        return {}, 0
    placeholders = ",".join("?" for _ in exchange_ids)
    query = (
        "SELECT exchange_id, best_bid, best_ask, rest_observed_at "
        "FROM price_observations "
        f"WHERE rest_observed_at >= ? AND rest_observed_at < ? AND exchange_id IN ({placeholders}) "
        "ORDER BY exchange_id, rest_observed_at, id"
    )
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    grouped: dict[str, list[QuotePoint]] = defaultdict(list)
    rows = 0
    try:
        for exchange_id, raw_bid, raw_ask, observed in db.execute(
            query,
            (start.isoformat(), end.isoformat(), *sorted(exchange_ids)),
        ):
            timestamp = _time_ns(str(observed))
            bid = _as_float(raw_bid)
            ask = _as_float(raw_ask)
            grouped[str(exchange_id)].append(
                QuotePoint(timestamp, timestamp, bid, ask, bid is not None and ask is not None)
            )
            rows += 1
    finally:
        db.close()
    return {key: TimeSeries(value) for key, value in grouped.items()}, rows


def _parquet_dataset(root: Path, stream: str) -> Any | None:
    stream_root = root / stream
    files = sorted(stream_root.rglob("*.parquet")) if stream_root.is_dir() else []
    if not files:
        return None
    return pads.dataset([str(path) for path in files], format="parquet")


def _filter_expression(
    *,
    time_field: str,
    start: datetime,
    end: datetime,
    token_ids: tuple[str, ...],
) -> Any:
    return (
        (pads.field(time_field) >= start)
        & (pads.field(time_field) < end)
        & pads.field("token_id").isin(list(token_ids))
    )


def _top_size(levels: object, *, bid: bool) -> float | None:
    if not isinstance(levels, list):
        return None
    valid: list[tuple[float, float]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _as_float(level.get("price"))
        size = _as_float(level.get("size"))
        if price is not None and size is not None and size >= 0.0:
            valid.append((price, size))
    if not valid:
        return None
    price, size = max(valid) if bid else min(valid)
    del price
    return size


def load_polymarket_history(
    root: Path,
    token_ids: set[str],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, TimeSeries], dict[str, DepthSeries], dict[str, int]]:
    tokens = tuple(sorted(token_ids))
    bbo_buckets: dict[str, dict[int, tuple[QuotePoint, QuotePoint]]] = defaultdict(dict)
    depth_points: dict[str, list[DepthPoint]] = defaultdict(list)
    counts = {"observations": 0, "book_changes": 0, "depth_snapshots": 0, "trades": 0}

    for stream, time_field in (("observations", "observed_at"), ("book_changes", "observed_at")):
        dataset = _parquet_dataset(root, stream)
        if dataset is None:
            continue
        columns = ["token_id", time_field, "best_bid", "best_ask"]
        if stream == "observations":
            columns.extend(("state_observed_at", "book_valid"))
        expression = _filter_expression(
            time_field=time_field,
            start=start,
            end=end,
            token_ids=tokens,
        )
        scanner = dataset.scanner(columns=columns, filter=expression, batch_size=65_536)
        for batch in scanner.to_batches():
            token_values = batch.column("token_id").to_pylist()
            event_values = batch.column(time_field).to_pylist()
            bid_values = batch.column("best_bid").to_pylist()
            ask_values = batch.column("best_ask").to_pylist()
            if stream == "observations":
                observed_values = batch.column("state_observed_at").to_pylist()
                valid_values = batch.column("book_valid").to_pylist()
            else:
                observed_values = event_values
                valid_values = [True] * batch.num_rows
            for token, event_at, quote_at, raw_bid, raw_ask, valid in zip(
                token_values,
                event_values,
                observed_values,
                bid_values,
                ask_values,
                valid_values,
                strict=True,
            ):
                token_id = str(token)
                event_ns = _as_time(event_at)
                observed_ns = _as_time(quote_at)
                bid = _as_float(raw_bid)
                ask = _as_float(raw_ask)
                is_valid = bool(valid) and bid is not None and ask is not None
                point = QuotePoint(event_ns, observed_ns, bid, ask, is_valid)
                buckets = bbo_buckets[token_id]
                bucket = event_ns // _NS_SECOND
                previous = buckets.get(bucket)
                if previous is None:
                    buckets[bucket] = (point, point)
                else:
                    first, last = previous
                    point_key = (event_ns, observed_ns)
                    if point_key < (first.event_ns, first.observed_ns):
                        first = point
                    if point_key > (last.event_ns, last.observed_ns):
                        last = point
                    buckets[bucket] = (first, last)
                counts[stream] += 1

    depth_dataset = _parquet_dataset(root, "depth_snapshots")
    if depth_dataset is not None:
        columns = ["token_id", "recorded_at", "state_observed_at", "bids", "asks"]
        expression = _filter_expression(
            time_field="recorded_at",
            start=start,
            end=end,
            token_ids=tokens,
        )
        scanner = depth_dataset.scanner(columns=columns, filter=expression, batch_size=16_384)
        for batch in scanner.to_batches():
            token_values = batch.column("token_id").to_pylist()
            event_values = batch.column("recorded_at").to_pylist()
            observed_values = batch.column("state_observed_at").to_pylist()
            bid_levels = batch.column("bids").to_pylist()
            ask_levels = batch.column("asks").to_pylist()
            for token, event_at, quote_at, bids, asks in zip(
                token_values,
                event_values,
                observed_values,
                bid_levels,
                ask_levels,
                strict=True,
            ):
                token_id = str(token)
                depth_points[token_id].append(
                    DepthPoint(
                        _as_time(event_at),
                        _as_time(quote_at),
                        _top_size(bids, bid=True),
                        _top_size(asks, bid=False),
                    )
                )
                counts["depth_snapshots"] += 1

    trade_dataset = _parquet_dataset(root, "trades")
    if trade_dataset is not None:
        counts["trades"] = trade_dataset.count_rows(
            filter=_filter_expression(
                time_field="observed_at",
                start=start,
                end=end,
                token_ids=tokens,
            )
        )
    return (
        {
            key: TimeSeries(
                [
                    point
                    for first, last in buckets.values()
                    for point in ((first, last) if first != last else (first,))
                ]
            )
            for key, buckets in bbo_buckets.items()
        },
        {key: DepthSeries(value) for key, value in depth_points.items()},
        counts,
    )


def _unique_fill_rows(rows: list[tuple[Any, ...]], event_type: str) -> list[tuple[Any, ...]]:
    unique: dict[tuple[object, ...], tuple[Any, ...]] = {}
    for row in rows:
        event_id, fill_id, quantity, price, source_time = row
        key: tuple[object, ...]
        if event_type == "AUTHORITATIVE_FILL" and fill_id is not None:
            key = (str(fill_id),)
        elif event_type == "REALTIME_FILL":
            key = (source_time, str(quantity), str(price))
        else:
            key = (int(event_id),)
        unique[key] = row
    return list(unique.values())


def _actual_fill_summary(
    rows: list[tuple[Any, ...]], quantity_limit: float
) -> tuple[float, float | None, int | None]:
    by_type: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        by_type[str(row[0])].append(row[1:])
    unique_by_type = {
        event_type: _unique_fill_rows(values, event_type) for event_type, values in by_type.items()
    }
    totals = {
        event_type: sum(abs(_as_float(row[2]) or 0.0) for row in values)
        for event_type, values in unique_by_type.items()
    }
    if not totals:
        return 0.0, None, None
    chosen_type = max(
        totals,
        key=lambda event_type: (
            totals[event_type],
            event_type == "FILL_SUMMARY",
            event_type == "AUTHORITATIVE_FILL",
        ),
    )
    chosen = unique_by_type[chosen_type]
    total = min(quantity_limit, totals[chosen_type])
    price_weight = 0.0
    quantity_weight = 0.0
    time_weight = 0.0
    timed_quantity = 0.0
    for row in chosen:
        quantity = abs(_as_float(row[2]) or 0.0)
        price = _as_float(row[3])
        source_time = row[4]
        if quantity <= 0.0:
            continue
        capped = min(quantity, max(0.0, total - quantity_weight))
        if capped <= 0.0:
            continue
        if price is not None:
            price_weight += capped * price
        quantity_weight += capped
        if source_time is not None:
            time_weight += capped * _time_ns(str(source_time))
            timed_quantity += capped
    average_price = (
        None if quantity_weight <= 0.0 or price_weight == 0.0 else price_weight / quantity_weight
    )
    fill_time = None if timed_quantity <= 0.0 else int(time_weight / timed_quantity)
    return total, average_price, fill_time


def _distance_bucket(sign: int, price: float, sig_quote: QuoteView | None) -> int:
    if sig_quote is None:
        return 99
    touch = sig_quote.bid if sign > 0 else sig_quote.ask
    distance_ticks = (price - touch) / _SIG_TICK if sign > 0 else (touch - price) / _SIG_TICK
    if distance_ticks < -0.5:
        return -1
    if distance_ticks <= 0.5:
        return 0
    return 1


def load_actual_orders(
    path: Path,
    sig: dict[str, TimeSeries],
    *,
    start: datetime,
    end: datetime,
) -> tuple[list[ActualOrder], dict[str, int]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    start_text = start.isoformat()
    end_text = end.isoformat()
    fill_rows: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in db.execute(
        "SELECT logical_intent_id,event_type,event_id,fill_id,quantity,price,source_timestamp "
        "FROM execution_events WHERE event_type IN "
        "('AUTHORITATIVE_FILL','REALTIME_FILL','FILL_SUMMARY') "
        "AND logical_intent_id IS NOT NULL"
    ):
        intent_id, event_type, event_id, fill_id, quantity, price, source_time = row
        fill_rows[str(intent_id)].append(
            (str(event_type), int(event_id), fill_id, quantity, price, source_time)
        )

    query = (
        "SELECT event.logical_operation_id,event.logical_intent_id,event.exchange_id,"
        "event.strategy_family,event.fair_value,event.quantity,event.detail_json,"
        "envelope.created_at_utc,envelope.payload_json "
        "FROM execution_events AS event "
        "JOIN execution_envelopes AS envelope USING(logical_operation_id) "
        "WHERE event.event_type='SUBMISSION' AND event.strategy_family IN ('MAKE','FV-TAKE') "
        "AND envelope.created_at_utc >= ? AND envelope.created_at_utc < ? "
        "ORDER BY envelope.created_at_utc,event.event_id"
    )
    orders: list[ActualOrder] = []
    for row in db.execute(query, (start_text, end_text)):
        (
            operation_id,
            intent_id,
            exchange_id,
            family,
            fair,
            submitted_quantity,
            detail_json,
            created_at,
            payload_json,
        ) = row
        if intent_id is None or exchange_id is None or created_at is None:
            continue
        detail = json.loads(str(detail_json or "{}"))
        payload = json.loads(str(payload_json or "{}"))
        action = str(detail.get("action", "")).lower()
        side = str(detail.get("outcome_side", "")).upper()
        candidates = payload.get("orders", [])
        if not candidates and "exchangeId" in payload:
            candidates = [payload]
        order = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and str(candidate.get("exchangeId")) == str(exchange_id)
                and str(candidate.get("action", "")).lower() == action
                and str(candidate.get("side", "")).upper() == side
            ),
            None,
        )
        if order is None:
            continue
        raw_price = _as_float(order.get("price"))
        quantity = _as_float(order.get("quantity")) or _as_float(submitted_quantity)
        if raw_price is None or quantity is None or quantity <= 0.0:
            continue
        sign = (
            1 if (side == "YES" and action == "buy") or (side == "NO" and action == "sell") else -1
        )
        yes_price = raw_price if side == "YES" else 1.0 - raw_price
        at_ns = _time_ns(str(created_at))
        sig_quote = sig.get(str(exchange_id))
        quote_at_submit = (
            None if sig_quote is None else sig_quote.at(at_ns, max_age_ns=_MAX_SIG_AGE_NS)
        )
        distance_bucket = _distance_bucket(sign, yes_price, quote_at_submit)
        fill_qty, fill_price_raw, fill_time = _actual_fill_summary(
            fill_rows.get(str(intent_id), []),
            quantity,
        )
        fill_price = (
            yes_price
            if fill_price_raw is None
            else fill_price_raw
            if side == "YES"
            else 1.0 - fill_price_raw
        )
        orders.append(
            ActualOrder(
                str(operation_id),
                str(intent_id),
                str(exchange_id),
                str(family),
                at_ns,
                sign,
                yes_price,
                quantity,
                _as_float(fair),
                distance_bucket,
                fill_qty,
                fill_price,
                at_ns if fill_time is None else fill_time,
            )
        )
    db.close()
    counts = {
        "submissions": len(orders),
        "maker_orders": sum(order.family == "MAKE" for order in orders),
        "taker_orders": sum(order.family == "FV-TAKE" for order in orders),
        "maker_filled_orders": sum(
            order.family == "MAKE" and order.filled_quantity > 0 for order in orders
        ),
        "taker_filled_orders": sum(
            order.family == "FV-TAKE" and order.filled_quantity > 0 for order in orders
        ),
    }
    return orders, counts


def fit_fill_calibration(orders: list[ActualOrder], *, prior_weight: float = 250.0) -> Calibration:
    quantity = sum(order.quantity for order in orders)
    filled = sum(min(order.quantity, order.filled_quantity) for order in orders)
    overall = 0.0 if quantity <= 0.0 else filled / quantity
    bin_totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for order in orders:
        stats = bin_totals[order.distance_bucket]
        stats[0] += min(order.quantity, order.filled_quantity)
        stats[1] += order.quantity
    by_distance = {
        bucket: (filled_quantity + prior_weight * overall) / (submitted_quantity + prior_weight)
        for bucket, (filled_quantity, submitted_quantity) in bin_totals.items()
    }
    return Calibration(overall, by_distance)


def check_fill_calibration(orders: list[ActualOrder]) -> CalibrationCheck:
    maker_orders = sorted(
        (order for order in orders if order.family == "MAKE"),
        key=lambda order: (order.at_ns, order.intent_id),
    )
    split = max(1, int(len(maker_orders) * 0.70))
    training = maker_orders[:split]
    validation = maker_orders[split:]
    model = fit_fill_calibration(training)
    predicted = sum(order.quantity * model.fraction(order.distance_bucket) for order in validation)
    actual = sum(min(order.quantity, order.filled_quantity) for order in validation)
    error = None if actual <= 0.0 else 100.0 * abs(predicted - actual) / actual
    return CalibrationCheck(len(training), len(validation), predicted, actual, error)


def _fill_lag_ns(orders: list[ActualOrder]) -> int:
    lags = sorted(
        order.fill_at_ns - order.at_ns
        for order in orders
        if order.family == "MAKE"
        and order.filled_quantity > 0.0
        and order.fill_at_ns >= order.at_ns
    )
    return 0 if not lags else int(statistics.median(lags))


def _maker_opportunities(
    orders: list[ActualOrder],
    current_ids: set[str],
    all_records: list[MarketMapping],
    sig: dict[str, TimeSeries],
    *,
    start_ns: int,
    end_ns: int,
) -> tuple[dict[str, list[MakerOpportunity]], int]:
    operations: dict[str, dict[str, int]] = defaultdict(dict)
    for order in orders:
        if order.family == "MAKE" and order.exchange_id in current_ids:
            operations[order.exchange_id][order.operation_id] = order.at_ns
    per_market_counts = [len(values) for values in operations.values() if values]
    cadence = max(1, int(statistics.median(per_market_counts))) if per_market_counts else 1
    current: list[MakerOpportunity] = [
        MakerOpportunity(at_ns, exchange_id)
        for exchange_id, values in operations.items()
        for at_ns in values.values()
    ]
    expanded: list[MakerOpportunity] = []
    for record in all_records:
        exchange_id = record.sig_exchange_id
        if exchange_id in current_ids or not _sim_tradeable(record):
            continue
        if exchange_id not in sig or not sig[exchange_id].points:
            continue
        for index in range(cadence):
            at_ns = start_ns + ((index + 1) * (end_ns - start_ns)) // (cadence + 1)
            expanded.append(MakerOpportunity(at_ns, exchange_id))
    current.sort(key=lambda item: (item.at_ns, item.exchange_id))
    expanded.sort(key=lambda item: (item.at_ns, item.exchange_id))
    return {"current76": current, "all_mapped": current + expanded}, cadence


def _eligible_taker_ids(records: list[MarketMapping], requested: set[str]) -> set[str]:
    return {
        record.sig_exchange_id
        for record in records
        if record.sig_exchange_id in requested
        and record.mapping_class is MappingClass.EXACT
        and record.mapping_direction is MappingDirection.SAME
        and record.status is MappingStatus.VERIFIED
        and record.direct_polymarket is not None
    }


def _taker_opportunities(
    records: list[MarketMapping],
    exchange_ids: set[str],
    sig: dict[str, TimeSeries],
    history: CaptureHistory,
    *,
    start_ns: int,
    end_ns: int,
    threshold: float,
) -> list[TakerOpportunity]:
    by_exchange = {record.sig_exchange_id: record for record in records}
    eligible = _eligible_taker_ids(records, exchange_ids)
    if not eligible:
        return []
    signal = ResidualTakerSignal(
        size=50,
        threshold=threshold,
        tracked_exchange_ids=frozenset(eligible),
    )
    first_minute = ((start_ns + _NS_MINUTE - 1) // _NS_MINUTE) * _NS_MINUTE
    result: list[TakerOpportunity] = []
    for at_ns in range(first_minute, end_ns, _NS_MINUTE):
        for exchange_id in sorted(eligible):
            record = by_exchange[exchange_id]
            sig_series = sig.get(exchange_id)
            sig_quote = (
                None if sig_series is None else sig_series.at(at_ns, max_age_ns=_MAX_SIG_AGE_NS)
            )
            identity = record.direct_polymarket
            if sig_quote is None or identity is None:
                continue
            pm_series = history.polymarket.get(identity.mapped_token_id)
            pm_quote = (
                None if pm_series is None else pm_series.at(at_ns, max_age_ns=_MAX_TAKER_PM_AGE_NS)
            )
            depth_series = history.depth.get(identity.mapped_token_id)
            pm_depth = (
                None
                if depth_series is None
                else depth_series.at(at_ns, max_age_ns=_MAX_TAKER_PM_AGE_NS)
            )
            if pm_quote is None or pm_depth is None:
                continue
            if pm_depth.bid_size is None or pm_depth.ask_size is None:
                continue
            state = ResidualInput(
                exchange_id=exchange_id,
                sig_bid=sig_quote.bid,
                sig_ask=sig_quote.ask,
                pm_mid=pm_quote.midpoint,
                pm_spread=pm_quote.ask - pm_quote.bid,
                pm_bid_size=pm_depth.bid_size,
                pm_ask_size=pm_depth.ask_size,
                observed_monotonic_ns=at_ns,
            )
            opportunity = signal.on_state(state)
            if opportunity is None:
                continue
            result.append(
                TakerOpportunity(
                    at_ns,
                    exchange_id,
                    1 if opportunity.direction == "BUY" else -1,
                    opportunity.entry_price,
                    float(opportunity.quantity),
                )
            )
    return result


def _external_quotes(
    record: MarketMapping,
    history: CaptureHistory,
    at_ns: int,
) -> dict[str, ExternalQuoteState]:
    result: dict[str, ExternalQuoteState] = {}
    for token_id in _mapping_tokens(record):
        series = history.polymarket.get(token_id)
        quote = None if series is None else series.at(at_ns, max_age_ns=_MAX_FV_AGE_NS)
        if quote is None:
            continue
        depth_series = history.depth.get(token_id)
        depth = (
            None
            if depth_series is None
            else depth_series.at(at_ns, max_age_ns=_MAX_TAKER_PM_AGE_NS)
        )
        result[token_id] = ExternalQuoteState(
            token_id=token_id,
            best_bid=quote.bid,
            best_ask=quote.ask,
            observed_monotonic_ns=quote.observed_ns,
            trusted=quote.trusted,
            source_version="polymarket-research-capture",
            observed_at=datetime.fromtimestamp(quote.observed_ns / _NS_SECOND, UTC),
            best_bid_size=None if depth is None else depth.bid_size,
            best_ask_size=None if depth is None else depth.ask_size,
        )
    return result


def _price_ticks(value: float | None) -> int | None:
    if value is None:
        return None
    ticks = value / _SIG_TICK
    rounded = round(ticks)
    if abs(ticks - rounded) > 1e-6 or not 1 <= rounded <= 199:
        return None
    return int(rounded)


def _maker_engine(
    document: MappingDocument,
    *,
    size_multiplier: int,
    edge_factor: float,
    cap: int,
) -> MakerEngine:
    spread = ConservativeSpreadPolicy(
        base_half_spread_ticks=0.0,
        uncertainty_multiplier=1.0,
        volatility_multiplier=0.0,
        toxicity_half_spread_ticks=0.0,
    )
    return MakerEngine(
        fair_value=DirectPolymarketFairValueProvider(document),
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=BinaryCaraInventoryModel(risk_aversion=0.0015),
        spread=ScaledSpreadPolicy(spread, edge_factor),
        size=InventoryConfidenceSizePolicy(base_size=50 * size_multiplier, minimum_size=1),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=_MAX_SIG_AGE_NS,
            max_fv_age_ns=_MAX_FV_AGE_NS,
            max_account_age_ns=15 * _NS_SECOND,
            max_inventory_age_ns=180 * _NS_SECOND,
            max_optional_signal_age_ns=_NS_SECOND,
            require_trusted_depth=False,
            min_fair_value=0.10,
            max_fair_value=0.90,
        ),
        config=MakerConfig(max_abs_inventory=float(cap)),
    )


def _snapshot(
    record: MarketMapping,
    sig_quote: QuoteView,
    external_quotes: dict[str, ExternalQuoteState],
    at_ns: int,
    position: float,
) -> MakerMarketSnapshot | None:
    bid_ticks = _price_ticks(sig_quote.bid)
    ask_ticks = _price_ticks(sig_quote.ask)
    if bid_ticks is None and ask_ticks is None:
        return None
    bids = () if bid_ticks is None else (RuntimeLevel(bid_ticks, 0.0),)
    asks = () if ask_ticks is None else (RuntimeLevel(ask_ticks, 0.0),)
    runtime_book = RuntimeBook(
        exchange_id=record.sig_exchange_id,
        market_id=record.sig_market_id,
        tournament_id=record.sig_tournament_id,
        bids=bids,
        asks=asks,
        trusted_depth=False,
        observed_monotonic_ns=sig_quote.observed_ns,
    )
    market = RuntimeMarket(
        market_id=record.sig_market_id,
        status="open",
        exchange_ids=(record.sig_exchange_id,),
        tournament_id=record.sig_tournament_id,
        mapping_accepted=record.status is MappingStatus.VERIFIED,
        tradeable=_sim_tradeable(record),
    )
    runtime_position = RuntimePosition(
        exchange_id=record.sig_exchange_id,
        market_id=record.sig_market_id,
        tournament_id=record.sig_tournament_id,
        gross_exposure=abs(position),
        signed_quantity=position,
    )
    runtime = RuntimeSnapshot(
        markets=(market,),
        books=(runtime_book,),
        portfolio=RuntimePortfolio(
            positions=(runtime_position,) if position else (),
            account_trusted=True,
        ),
        observation_monotonic_ns=at_ns,
    )
    return MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=record.sig_exchange_id,
        market_id=record.sig_market_id,
        tournament_id=record.sig_tournament_id,
        now_monotonic_ns=at_ns,
        sig_bbo_observed_ns=sig_quote.observed_ns,
        sig_bbo_trusted=sig_quote.trusted,
        sig_depth_observed_ns=None,
        sig_depth_trusted=False,
        account_observed_ns=at_ns,
        inventory_observed_ns=at_ns,
        external_quotes=external_quotes,
    )


def _clip_quantity(position: float, signed_quantity: float, cap: float) -> float:
    if signed_quantity > 0.0:
        return min(signed_quantity, max(0.0, cap - position))
    if signed_quantity < 0.0:
        return -min(-signed_quantity, max(0.0, cap + position))
    return 0.0


def _exit_mark(
    history: CaptureHistory, record: MarketMapping, at_ns: int
) -> tuple[float, float] | None:
    return history.mark(record, at_ns, max_age_ns=_MAX_MARK_AGE_NS)


def simulate_scenario(
    *,
    document: MappingDocument,
    history: CaptureHistory,
    sig: dict[str, TimeSeries],
    fill_model: Calibration,
    taker_fill_fraction: float,
    opportunities: dict[str, list[MakerOpportunity]],
    taker_opportunities: list[TakerOpportunity],
    records_by_exchange: dict[str, MarketMapping],
    size_multiplier: int,
    edge_factor: float,
    cap: int,
    taker_label: str,
    taker_threshold: float,
    market_set: str,
    end_ns: int,
    fill_lag_ns: int,
) -> ScenarioResult:
    maker_events = opportunities[market_set]
    maker_engine = _maker_engine(
        document,
        size_multiplier=size_multiplier,
        edge_factor=edge_factor,
        cap=cap,
    )
    positions: dict[str, float] = defaultdict(float)
    cash: dict[str, float] = defaultdict(float)
    fills: list[SimFill] = []
    max_inventory = 0.0
    max_inventory_exchange_id: str | None = None
    maker_quotes = 0
    maker_opportunity_count = 0
    maker_fill_shares = 0.0
    taker_fill_shares = 0.0
    selected_taker = [
        item for item in taker_opportunities if item.exchange_id in records_by_exchange
    ]
    events: list[tuple[int, int, MakerOpportunity | TakerOpportunity]] = [
        (item.at_ns, 1, item) for item in maker_events
    ]
    events.extend((item.at_ns, 0, item) for item in selected_taker)
    events.sort(key=lambda item: (item[0], item[1], item[2].exchange_id))

    for at_ns, event_kind, item in events:
        record = records_by_exchange.get(item.exchange_id)
        if record is None:
            continue
        if event_kind == 0:
            assert isinstance(item, TakerOpportunity)
            quantity = item.quantity * taker_fill_fraction
            signed_quantity = _clip_quantity(
                positions[item.exchange_id],
                item.sign * quantity,
                float(cap),
            )
            if signed_quantity == 0.0 or at_ns > end_ns:
                continue
            positions[item.exchange_id] += signed_quantity
            cash[item.exchange_id] -= signed_quantity * item.price
            taker_fill_shares += abs(signed_quantity)
            fills.append(
                SimFill(
                    at_ns,
                    item.exchange_id,
                    1 if signed_quantity > 0 else -1,
                    item.price,
                    abs(signed_quantity),
                    "FV-TAKE",
                )
            )
        else:
            assert isinstance(item, MakerOpportunity)
            maker_opportunity_count += 1
            sig_series = sig.get(item.exchange_id)
            sig_quote = (
                None if sig_series is None else sig_series.at(at_ns, max_age_ns=_MAX_SIG_AGE_NS)
            )
            if sig_quote is None:
                continue
            external = _external_quotes(record, history, at_ns)
            snapshot = _snapshot(record, sig_quote, external, at_ns, positions[item.exchange_id])
            if snapshot is None:
                continue
            decision = maker_engine.quote(snapshot)
            desired = decision.desired
            if desired is None:
                continue
            maker_quotes += 1
            if desired.bid_ticks is not None and desired.bid_size > 0:
                price = desired.bid_ticks * _SIG_TICK
                bucket = _distance_bucket(1, price, sig_quote)
                quantity = desired.bid_size * fill_model.fraction(bucket)
                signed_quantity = _clip_quantity(positions[item.exchange_id], quantity, float(cap))
                if signed_quantity > 0.0 and at_ns + fill_lag_ns <= end_ns:
                    positions[item.exchange_id] += signed_quantity
                    cash[item.exchange_id] -= signed_quantity * price
                    maker_fill_shares += signed_quantity
                    fills.append(
                        SimFill(
                            at_ns + fill_lag_ns, item.exchange_id, 1, price, signed_quantity, "MAKE"
                        )
                    )
            if desired.ask_ticks is not None and desired.ask_size > 0:
                price = desired.ask_ticks * _SIG_TICK
                bucket = _distance_bucket(-1, price, sig_quote)
                quantity = desired.ask_size * fill_model.fraction(bucket)
                signed_quantity = _clip_quantity(positions[item.exchange_id], -quantity, float(cap))
                if signed_quantity < 0.0 and at_ns + fill_lag_ns <= end_ns:
                    positions[item.exchange_id] += signed_quantity
                    cash[item.exchange_id] -= signed_quantity * price
                    maker_fill_shares += abs(signed_quantity)
                    fills.append(
                        SimFill(
                            at_ns + fill_lag_ns,
                            item.exchange_id,
                            -1,
                            price,
                            abs(signed_quantity),
                            "MAKE",
                        )
                    )
        current_abs = abs(positions[item.exchange_id])
        if current_abs > max_inventory:
            max_inventory = current_abs
            max_inventory_exchange_id = item.exchange_id

    total_pnl = 0.0
    marked_markets = 0
    unmarked_positions = 0
    unmarked_exchange_ids: list[str] = []
    for exchange_id, position in positions.items():
        if abs(position) <= 1e-9:
            total_pnl += cash[exchange_id]
            continue
        record = records_by_exchange[exchange_id]
        mark = _exit_mark(history, record, end_ns)
        if mark is None:
            unmarked_positions += 1
            unmarked_exchange_ids.append(exchange_id)
            continue
        exit_price = mark[0] if position > 0.0 else mark[1]
        total_pnl += cash[exchange_id] + position * exit_price
        marked_markets += 1

    markout_totals: dict[str, dict[int, list[float]]] = {
        family: {horizon: [0.0, 0.0] for horizon in _MARKOUT_HORIZONS}
        for family in ("MAKE", "FV-TAKE")
    }
    for fill in fills:
        record = records_by_exchange[fill.exchange_id]
        signed_quantity = fill.sign * fill.quantity
        for horizon in _MARKOUT_HORIZONS:
            markout = history.markout(record, fill.at_ns, horizon)
            if markout is None:
                continue
            exit_price = markout[0] if signed_quantity > 0.0 else markout[1]
            family_totals = markout_totals[fill.family][horizon]
            family_totals[0] += signed_quantity * (exit_price - fill.price)
            family_totals[1] += abs(signed_quantity)

    markouts_by_family: dict[str, dict[int, tuple[float | None, float]]] = {}
    for family, by_horizon in markout_totals.items():
        markouts: dict[int, tuple[float | None, float]] = {}
        for horizon, (pnl, quantity) in by_horizon.items():
            per_share = None if quantity <= 0.0 else pnl / quantity
            markouts[horizon] = (per_share, quantity)
        markouts_by_family[family] = markouts
    expected_fill_shares = maker_fill_shares + taker_fill_shares
    pnl_value = None if unmarked_positions else total_pnl
    return ScenarioResult(
        size_multiplier,
        edge_factor,
        cap,
        taker_label,
        taker_threshold,
        market_set,
        maker_opportunity_count,
        maker_quotes,
        len(selected_taker),
        expected_fill_shares,
        max_inventory,
        max_inventory_exchange_id,
        pnl_value,
        marked_markets,
        unmarked_positions,
        tuple(sorted(unmarked_exchange_ids)),
        markouts_by_family["MAKE"],
        markouts_by_family["FV-TAKE"],
    )


def run_grid(
    *,
    sig_db: Path,
    execution_journal: Path,
    polymarket_data: Path,
    mapping_path: Path,
    current_exchange_ids_path: Path,
    start_at: datetime,
    end_at: datetime,
) -> tuple[list[ScenarioResult], dict[str, object]]:
    document = load_document(mapping_path)
    records = list(document.records)
    records_by_exchange = {record.sig_exchange_id: record for record in records}
    current_ids = set(current_exchange_ids_path.read_text(encoding="utf-8").splitlines())
    missing_current = current_ids.difference(records_by_exchange)
    if missing_current:
        raise ValueError(
            f"current market set contains unmapped exchanges: {sorted(missing_current)}"
        )
    tradeable_records = [record for record in records if _sim_tradeable(record)]
    exchange_ids = {record.sig_exchange_id for record in tradeable_records}
    token_ids = {token for record in tradeable_records for token in _mapping_tokens(record)}
    start_ns = _time_ns(start_at)
    end_ns = _time_ns(end_at)
    if end_ns <= start_ns:
        raise ValueError("end_at must be after start_at")

    sig, sig_rows = load_sig_history(sig_db, exchange_ids, start_at, end_at)
    polymarket, depth, poly_counts = load_polymarket_history(
        polymarket_data,
        token_ids,
        start_at,
        end_at,
    )
    history = CaptureHistory(sig, polymarket, depth, sig_rows, poly_counts)
    actual_orders, journal_counts = load_actual_orders(
        execution_journal,
        sig,
        start=start_at,
        end=end_at,
    )
    maker_orders = [order for order in actual_orders if order.family == "MAKE"]
    taker_orders = [order for order in actual_orders if order.family == "FV-TAKE"]
    maker_calibration = fit_fill_calibration(maker_orders)
    calibration_check = check_fill_calibration(actual_orders)
    actual_maker_shares = sum(min(order.quantity, order.filled_quantity) for order in maker_orders)
    actual_taker_shares = sum(min(order.quantity, order.filled_quantity) for order in taker_orders)
    submitted_maker_shares = sum(order.quantity for order in maker_orders)
    taker_submitted = sum(order.quantity for order in taker_orders)
    taker_fill_fraction = (
        0.0 if taker_submitted <= 0.0 else min(1.0, actual_taker_shares / taker_submitted)
    )
    fill_lag_ns = _fill_lag_ns(actual_orders)

    opportunities, synthetic_cadence = _maker_opportunities(
        actual_orders,
        current_ids,
        records,
        sig,
        start_ns=start_ns,
        end_ns=end_ns,
    )
    current_taker_ids = _eligible_taker_ids(records, current_ids)
    all_taker_ids = _eligible_taker_ids(records, {record.sig_exchange_id for record in records})
    taker_cache: dict[tuple[str, float], list[TakerOpportunity]] = {}
    for market_set, ids in (("current76", current_taker_ids), ("all_mapped", all_taker_ids)):
        for _label, threshold in TAKER_THRESHOLDS:
            taker_cache[(market_set, threshold)] = _taker_opportunities(
                records,
                ids,
                sig,
                history,
                start_ns=start_ns,
                end_ns=end_ns,
                threshold=threshold,
            )

    results: list[ScenarioResult] = []
    for market_set in MARKET_SETS:
        for size_multiplier in SIZE_MULTIPLIERS:
            for edge_factor in EDGE_FACTORS:
                for cap in INVENTORY_CAPS:
                    for taker_label, taker_threshold in TAKER_THRESHOLDS:
                        results.append(
                            simulate_scenario(
                                document=document,
                                history=history,
                                sig=sig,
                                fill_model=maker_calibration,
                                taker_fill_fraction=taker_fill_fraction,
                                opportunities=opportunities,
                                taker_opportunities=taker_cache[(market_set, taker_threshold)],
                                records_by_exchange=records_by_exchange,
                                size_multiplier=size_multiplier,
                                edge_factor=edge_factor,
                                cap=cap,
                                taker_label=taker_label,
                                taker_threshold=taker_threshold,
                                market_set=market_set,
                                end_ns=end_ns,
                                fill_lag_ns=fill_lag_ns,
                            )
                        )

    report: dict[str, object] = {
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "mapping_records": len(records),
        "mapping_tradeable_records": len(tradeable_records),
        "mapping_classes": {
            mapping_class.value: sum(record.mapping_class is mapping_class for record in records)
            for mapping_class in MappingClass
        },
        "current_exchange_count": len(current_ids),
        "current_ids_mapped": len(current_ids.intersection(records_by_exchange)),
        "current_taker_eligible_count": len(current_taker_ids),
        "all_mapped_taker_eligible_count": len(all_taker_ids),
        "sig_price_observation_rows": sig_rows,
        "sig_exchange_ids_with_observations": len(sig),
        "polymarket_rows_by_stream": poly_counts,
        "polymarket_bbo_points_retained": sum(len(series.points) for series in polymarket.values()),
        "polymarket_tokens_loaded": len(polymarket),
        "polymarket_depth_tokens_loaded": len(depth),
        "journal": journal_counts,
        "actual_maker_orders": len(maker_orders),
        "actual_maker_filled_orders": sum(order.filled_quantity > 0.0 for order in maker_orders),
        "actual_maker_fill_shares": actual_maker_shares,
        "maker_full_sample_predicted_shares": (
            submitted_maker_shares * maker_calibration.overall_fraction
        ),
        "maker_full_sample_reproduction_error_percent": (
            None
            if actual_maker_shares <= 0.0
            else 100.0
            * abs(submitted_maker_shares * maker_calibration.overall_fraction - actual_maker_shares)
            / actual_maker_shares
        ),
        "actual_taker_orders": len(taker_orders),
        "actual_taker_filled_orders": sum(order.filled_quantity > 0.0 for order in taker_orders),
        "actual_taker_fill_shares": actual_taker_shares,
        "maker_fill_fraction": maker_calibration.overall_fraction,
        "taker_fill_fraction": taker_fill_fraction,
        "maker_validation": {
            "training_orders": calibration_check.training_orders,
            "validation_orders": calibration_check.validation_orders,
            "predicted_validation_shares": calibration_check.predicted_validation_shares,
            "actual_validation_shares": calibration_check.actual_validation_shares,
            "error_percent": calibration_check.error_percent,
        },
        "median_maker_operations_per_market": synthetic_cadence,
        "maker_opportunities_current": len(opportunities["current76"]),
        "maker_opportunities_all_mapped": len(opportunities["all_mapped"]),
        "maker_fill_lag_seconds_median": fill_lag_ns / _NS_SECOND,
    }
    return results, report


def write_grid(path: Path, results: list[ScenarioResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "size_multiplier",
        "edge_factor",
        "cap_shares",
        "taker_label",
        "taker_threshold",
        "market_set",
        "maker_opportunities",
        "maker_quotes",
        "taker_signals",
        "expected_fill_shares",
        "max_inventory_shares",
        "max_inventory_exchange_id",
        "pnl_pm_exit",
        "pnl_marked_markets",
        "pnl_unmarked_positions",
        "pnl_unmarked_exchange_ids",
    ) + tuple(
        field_name
        for family_name in ("maker", "taker")
        for suffix in ("1m", "5m", "30m", "2h")
        for field_name in (
            f"markout_{family_name}_{suffix}_per_share",
            f"markout_{family_name}_{suffix}_coverage_shares",
        )
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row: dict[str, object] = {
                "size_multiplier": result.size_multiplier,
                "edge_factor": result.edge_factor,
                "cap_shares": result.cap,
                "taker_label": result.taker_label,
                "taker_threshold": result.taker_threshold,
                "market_set": result.market_set,
                "maker_opportunities": result.maker_opportunities,
                "maker_quotes": result.maker_quotes,
                "taker_signals": result.taker_signals,
                "expected_fill_shares": result.expected_fill_shares,
                "max_inventory_shares": result.max_inventory,
                "max_inventory_exchange_id": result.max_inventory_exchange_id or "",
                "pnl_pm_exit": "UNKNOWN" if result.pnl_pm_exit is None else result.pnl_pm_exit,
                "pnl_marked_markets": result.pnl_marked_markets,
                "pnl_unmarked_positions": result.pnl_unmarked_positions,
                "pnl_unmarked_exchange_ids": ";".join(result.pnl_unmarked_exchange_ids),
            }
            for family_name, markouts in (
                ("maker", result.maker_markouts),
                ("taker", result.taker_markouts),
            ):
                for horizon, suffix in zip(
                    _MARKOUT_HORIZONS, ("1m", "5m", "30m", "2h"), strict=True
                ):
                    value, coverage = markouts[horizon]
                    row[f"markout_{family_name}_{suffix}_per_share"] = (
                        "UNKNOWN" if value is None else value
                    )
                    row[f"markout_{family_name}_{suffix}_coverage_shares"] = coverage
            writer.writerow(row)


def _json_report(report: dict[str, object]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sig-db", required=True, type=Path)
    parser.add_argument("--execution-journal", required=True, type=Path)
    parser.add_argument("--polymarket-data", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--current-exchange-ids", required=True, type=Path)
    parser.add_argument("--start", default="2026-10-01T16:00:00Z")
    parser.add_argument("--end", default="2026-10-02T09:25:00Z")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)

    results, report = run_grid(
        sig_db=args.sig_db,
        execution_journal=args.execution_journal,
        polymarket_data=args.polymarket_data,
        mapping_path=args.mapping,
        current_exchange_ids_path=args.current_exchange_ids,
        start_at=_parse_time(args.start),
        end_at=_parse_time(args.end),
    )
    write_grid(args.output, results)
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(_json_report(report) + "\n", encoding="utf-8")
    print(_json_report(report))
    print(f"scenario_rows={len(results)} grid_csv={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
