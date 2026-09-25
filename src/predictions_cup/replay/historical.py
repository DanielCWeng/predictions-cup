"""Fail-closed adapter for historical full-depth snapshot extracts.

Historical PMXT v1/v2 timestamps are not our historical network-observation times.
This adapter therefore exposes an explicit SOURCE_TIME_PROXY contract and only
accepts full-depth snapshot rows. It deliberately does not replay ambiguous raw
PMXT event ordering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from predictions_cup.replay.model import (
    BookLevel,
    QuotePayload,
    ReplayEvent,
    ReplayEventType,
    ReplaySource,
)


class HistoricalSnapshotError(ValueError):
    """Raised when a historical snapshot extract violates the accepted schema."""


@dataclass(frozen=True, slots=True)
class HistoricalReplayBatch:
    events: tuple[ReplayEvent, ...]
    source_name: str
    timestamp_semantics: str
    latency_model: str
    limitations: tuple[str, ...]


def load_historical_snapshot_jsonl(path: Path) -> HistoricalReplayBatch:
    """Load normalized full-depth historical snapshots into BUILD-005 ReplayEvents."""
    events: list[ReplayEvent] = []
    seen: set[tuple[str, int]] = set()

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HistoricalSnapshotError(
                    f"line {line_no}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise HistoricalSnapshotError(f"line {line_no}: row must be a JSON object")

            minute_ts = _integer(row.get("minute_ts"), f"line {line_no} minute_ts")
            asset_id = _text(row.get("asset_id"), f"line {line_no} asset_id")
            condition_id = _text(row.get("condition_id"), f"line {line_no} condition_id")
            key = (asset_id, minute_ts)
            if key in seen:
                raise HistoricalSnapshotError(
                    f"line {line_no}: duplicate asset/minute snapshot {asset_id}@{minute_ts}"
                )
            seen.add(key)

            bids = _levels(row.get("bids"), f"line {line_no} bids", descending=True)
            asks = _levels(row.get("asks"), f"line {line_no} asks", descending=False)
            best_bid = bids[0].price if bids else None
            best_ask = asks[0].price if asks else None
            if best_bid is not None and best_ask is not None and best_bid > best_ask:
                raise HistoricalSnapshotError(
                    f"line {line_no}: crossed snapshot ({best_bid} > {best_ask})"
                )

            at = datetime.fromtimestamp(minute_ts, tz=UTC)
            events.append(
                ReplayEvent(
                    observed_at=at,
                    source_at=at,
                    source=ReplaySource.POLYMARKET,
                    event_type=ReplayEventType.DEPTH_SNAPSHOT,
                    instrument_id=asset_id,
                    market_id=condition_id,
                    sequence=line_no,
                    payload=QuotePayload(
                        best_bid=best_bid,
                        best_ask=best_ask,
                        quote_observed_at=at,
                        bids=bids,
                        asks=asks,
                        last_trade=_optional_decimal(
                            row.get("last_trade_price"),
                            f"line {line_no} last_trade_price",
                        ),
                        book_valid=True,
                    ),
                )
            )

    events.sort(key=lambda event: event.sort_key)
    return HistoricalReplayBatch(
        events=tuple(events),
        source_name="historical_snapshot_jsonl",
        timestamp_semantics="SOURCE_TIME_PROXY",
        latency_model="NONE_INVENTED",
        limitations=(
            (
                "observed_at is a deterministic source-time proxy, not measured "
                "historical network observation time"
            ),
            "PMXT-era within-millisecond event ordering is not reconstructed",
            "snapshot replay cannot recover queue position, cancellations or passive maker fills",
        ),
    )


def _levels(value: Any, field: str, *, descending: bool) -> tuple[BookLevel, ...]:
    if not isinstance(value, list):
        raise HistoricalSnapshotError(f"{field} must be a list")
    levels: list[BookLevel] = []
    for index, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            raise HistoricalSnapshotError(f"{field}[{index}] must be [price, size]")
        price = _decimal(item[0], f"{field}[{index}].price")
        quantity = _decimal(item[1], f"{field}[{index}].size")
        if price < Decimal("0") or price > Decimal("1"):
            raise HistoricalSnapshotError(f"{field}[{index}].price must be in [0, 1]")
        if quantity < Decimal("0"):
            raise HistoricalSnapshotError(f"{field}[{index}].size must be non-negative")
        if quantity == 0:
            continue
        levels.append(BookLevel(price=price, quantity=quantity))
    levels.sort(key=lambda level: level.price, reverse=descending)
    return tuple(levels)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, float):
        raise HistoricalSnapshotError(f"{field} must be encoded as a string, not binary float")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HistoricalSnapshotError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise HistoricalSnapshotError(f"{field} must be finite")
    return result


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value, field)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise HistoricalSnapshotError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalSnapshotError(f"{field} must be an integer") from exc
    if str(result) != str(value):
        raise HistoricalSnapshotError(f"{field} must be an exact integer")
    if result < 0:
        raise HistoricalSnapshotError(f"{field} must be non-negative")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalSnapshotError(f"{field} must be a non-empty string")
    return value.strip()
