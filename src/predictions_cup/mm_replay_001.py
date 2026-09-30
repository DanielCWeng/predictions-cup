"""MM-REPLAY-001 research primitives.

This module is deliberately data-source agnostic. It provides the provenance gate,
schema audit, canonical order-book events, fill-model boundary, fair-value quote
construction, markouts, latency helpers, and a thin adapter around the *existing*
frozen EXPERIMENT-005F feature implementation.

It never places orders and never silently substitutes a dataset.
"""

from __future__ import annotations

import csv
import hashlib
from bisect import bisect_left
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

import pyarrow.parquet as pq

from predictions_cup.runtime.models import SIG_TICK
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.shadow.frozen_runtime import (
    Hazard005FBboObservation,
    IncrementalHazard005FState,
)

EXPERIMENT_ID: Final = "MM-REPLAY-001"
DATA_STATUS_WAITING: Final = "WAITING_FOR_DATA"
DATA_STATUS_BOUND: Final = "BOUND"
SCIENTIFIC_RESULT_NOT_RUN: Final = "NOT_RUN"
TICK: Final = float(SIG_TICK)

MARKOUT_HORIZONS_S: Final[tuple[int, ...]] = (1, 5, 15, 30, 60, 300)
CANCEL_LATENCIES_MS: Final[tuple[int, ...]] = (0, 25, 50, 100, 250, 500, 1000)

REQUIRED_PROVENANCE_FIELDS: Final[tuple[str, ...]] = (
    "kaggle_dataset_slug",
    "dataset_version",
    "source",
    "schema_version",
    "relation_to_data003",
    "acquisition_version",
)

REQUIRED_OUTPUT_FILES: Final[tuple[str, ...]] = (
    "INPUT_AUDIT.json",
    "INPUT_AUDIT.md",
    "005F_TRANSFER_RESULTS.csv",
    "005F_TRANSFER_SUMMARY.json",
    "MM_FILL_RESULTS.parquet",
    "MM_POLICY_RESULTS.csv",
    "MM_MARKOUTS.csv",
    "MM_TOXICITY_BUCKETS.csv",
    "FV_CONVERGENCE.csv",
    "LATENCY_SENSITIVITY.csv",
    "MARKET_BREAKDOWN.csv",
    "FINAL_REPORT.md",
    "MASTER_HANDOFF_MM_REPLAY_001.md",
)
OPTIONAL_OUTPUT_FILES: Final[tuple[str, ...]] = ("MM_CANDIDATE_CONFIG.json",)

COLUMN_SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "market_id": ("market_id", "condition_id", "token_id", "exchange_id", "asset_id"),
    "event_timestamp": (
        "timestamp_ns",
        "event_timestamp_ns",
        "exchange_timestamp_ns",
        "timestamp",
        "ts",
        "datetime",
    ),
    "best_bid": ("best_bid", "bid", "bbo_bid", "bid_price"),
    "best_ask": ("best_ask", "ask", "bbo_ask", "ask_price"),
    "trade_price": ("trade_price", "last_trade_price", "price"),
    "trade_size": ("trade_size", "size", "quantity", "amount"),
    "aggressor_side": ("aggressor_side", "taker_side", "trade_side"),
    "external_fv": ("external_fv", "fair_value", "reference_fv", "pm_fv"),
    "source_timestamp": (
        "source_timestamp_ns",
        "exchange_timestamp_ns",
        "server_timestamp_ns",
    ),
    "receive_timestamp": (
        "receive_timestamp_ns",
        "observed_timestamp_ns",
        "local_timestamp_ns",
    ),
    "book_side": ("side", "book_side"),
    "book_price": ("book_price", "level_price", "price"),
    "book_size": ("book_size", "level_size", "size"),
    "book_action": ("action", "event_type", "update_type"),
}


class InputContractError(RuntimeError):
    """Input provenance/schema does not satisfy the fail-closed research contract."""


class FillAssumption(StrEnum):
    OBSERVED_TRADE = "OBSERVED_TRADE"
    QUEUE_AWARE = "QUEUE_AWARE"
    TRADE_THROUGH_SENSITIVITY = "TRADE_THROUGH_SENSITIVITY"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class DatasetBinding:
    status: str
    kaggle_dataset_slug: str | None
    dataset_version: str | None
    source: str | None
    schema_version: str | None
    relation_to_data003: str | None
    acquisition_version: str | None
    root_hint: str | None
    expected_markets: int | None
    expected_tokens: int | None
    expected_time_start: str | None
    expected_time_end: str | None
    file_hashes: Mapping[str, str]
    column_map: Mapping[str, str]
    book_encoding: str | None

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> DatasetBinding:
        return cls(
            status=str(raw.get("status", DATA_STATUS_WAITING)),
            kaggle_dataset_slug=_maybe_str(raw.get("kaggle_dataset_slug")),
            dataset_version=_maybe_str(raw.get("dataset_version")),
            source=_maybe_str(raw.get("source")),
            schema_version=_maybe_str(raw.get("schema_version")),
            relation_to_data003=_maybe_str(raw.get("relation_to_data003")),
            acquisition_version=_maybe_str(raw.get("acquisition_version")),
            root_hint=_maybe_str(raw.get("root_hint")),
            expected_markets=_maybe_int(raw.get("expected_markets")),
            expected_tokens=_maybe_int(raw.get("expected_tokens")),
            expected_time_start=_maybe_str(raw.get("expected_time_start")),
            expected_time_end=_maybe_str(raw.get("expected_time_end")),
            file_hashes=_string_mapping(raw.get("file_hashes")),
            column_map=_string_mapping(raw.get("column_map")),
            book_encoding=_maybe_str(raw.get("book_encoding")),
        )

    @classmethod
    def load(cls, path: Path) -> DatasetBinding:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise InputContractError("input manifest must be a JSON object")
        return cls.from_json(cast(dict[str, Any], payload))

    def missing_provenance(self) -> tuple[str, ...]:
        values: dict[str, str | None] = {
            "kaggle_dataset_slug": self.kaggle_dataset_slug,
            "dataset_version": self.dataset_version,
            "source": self.source,
            "schema_version": self.schema_version,
            "relation_to_data003": self.relation_to_data003,
            "acquisition_version": self.acquisition_version,
        }
        return tuple(name for name in REQUIRED_PROVENANCE_FIELDS if not values[name])

    def require_bound(self) -> None:
        if self.status != DATA_STATUS_BOUND:
            raise InputContractError(
                f"{EXPERIMENT_ID}: DATA_STATUS={self.status}; expected {DATA_STATUS_BOUND}"
            )
        missing = self.missing_provenance()
        if missing:
            raise InputContractError("missing provenance fields: " + ", ".join(missing))
        relation = (self.relation_to_data003 or "").upper()
        if "DATA-003" not in relation and "DATA_003" not in relation:
            raise InputContractError(
                "relation_to_data003 must explicitly bind the order-book corpus to DATA-003"
            )


@dataclass(frozen=True, slots=True)
class FileSchema:
    path: str
    format: str
    columns: tuple[str, ...]
    rows: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class InputAudit:
    experiment: str
    passed: bool
    binding_status: str
    file_count: int
    formats: tuple[str, ...]
    columns: tuple[str, ...]
    suggested_column_map: Mapping[str, str]
    capabilities: Mapping[str, bool]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    files: tuple[FileSchema, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"# {EXPERIMENT_ID} — Input Audit",
            "",
            f"- Status: **{status}**",
            f"- Binding: `{self.binding_status}`",
            f"- Files: {self.file_count}",
            f"- Formats: {', '.join(self.formats) or 'none'}",
            "",
            "## Capabilities",
            "",
        ]
        for name, value in sorted(self.capabilities.items()):
            lines.append(f"- `{name}`: {'YES' if value else 'NO'}")
        lines += ["", "## Suggested canonical columns", ""]
        if self.suggested_column_map:
            for canonical, source in sorted(self.suggested_column_map.items()):
                lines.append(f"- `{canonical}` <- `{source}`")
        else:
            lines.append("- none")
        lines += ["", "## Errors", ""]
        lines.extend(f"- {item}" for item in self.errors)
        if not self.errors:
            lines.append("- none")
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {item}" for item in self.warnings)
        if not self.warnings:
            lines.append("- none")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class BookObservation:
    market_id: str
    timestamp_ns: int
    best_bid: float | None
    best_ask: float | None
    external_fv: float | None = None
    external_fv_timestamp_ns: int | None = None
    trade_price: float | None = None
    trade_size: float | None = None
    aggressor_side: Side | None = None
    bid_depth: float | None = None
    ask_depth: float | None = None
    queue_ahead_bid: float | None = None
    queue_ahead_ask: float | None = None
    update_hazard: float | None = None
    category: str | None = None

    @property
    def local_mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        if not (0.0 < self.best_bid <= self.best_ask < 1.0):
            return None
        return (self.best_bid + self.best_ask) / 2.0


@dataclass(frozen=True, slots=True)
class BookDelta:
    market_id: str
    timestamp_ns: int
    side: Literal["BID", "ASK"]
    price: float
    size: float
    action: Literal["SET", "DELETE"]
    event_id: str | None = None

    def __post_init__(self) -> None:
        if not self.market_id:
            raise ValueError("market_id must not be blank")
        if self.timestamp_ns < 0:
            raise ValueError("timestamp_ns must be non-negative")
        if not 0.0 < self.price < 1.0:
            raise ValueError("book price must be inside (0,1)")
        if self.size < 0.0 or not math.isfinite(self.size):
            raise ValueError("book size must be finite and non-negative")


@dataclass(slots=True)
class _BookState:
    bids: dict[float, float]
    asks: dict[float, float]
    last_timestamp_ns: int | None = None
    seen_event_ids: set[str] | None = None


class TopOfBookReconstructor:
    """No-lookahead price-level book reconstruction for explicit SET/DELETE deltas."""

    def __init__(self) -> None:
        self._state: dict[str, _BookState] = {}

    def apply(self, delta: BookDelta) -> BookObservation:
        state = self._state.setdefault(
            delta.market_id,
            _BookState(bids={}, asks={}, seen_event_ids=set()),
        )
        if state.last_timestamp_ns is not None and delta.timestamp_ns < state.last_timestamp_ns:
            raise InputContractError("book delta timestamp regression")
        if delta.event_id is not None:
            assert state.seen_event_ids is not None
            if delta.event_id in state.seen_event_ids:
                raise InputContractError("duplicate book delta event_id")
            state.seen_event_ids.add(delta.event_id)

        levels = state.bids if delta.side == "BID" else state.asks
        if delta.action == "DELETE" or delta.size == 0.0:
            levels.pop(delta.price, None)
        else:
            levels[delta.price] = delta.size
        state.last_timestamp_ns = delta.timestamp_ns

        best_bid = max(state.bids) if state.bids else None
        best_ask = min(state.asks) if state.asks else None
        if best_bid is not None and best_ask is not None and best_bid > best_ask:
            raise InputContractError("reconstructed book is crossed")
        return BookObservation(
            market_id=delta.market_id,
            timestamp_ns=delta.timestamp_ns,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth=state.bids.get(best_bid) if best_bid is not None else None,
            ask_depth=state.asks.get(best_ask) if best_ask is not None else None,
        )


@dataclass(frozen=True, slots=True)
class MakerPolicy:
    policy_id: str
    anchor: Literal["LOCAL_MID", "EXTERNAL_FV"]
    inventory_gamma: float
    base_half_spread_ticks: float
    max_fv_age_ms: int
    max_distance_ticks: float
    min_external_edge_ticks: float
