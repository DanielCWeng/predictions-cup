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
    toxicity_widen_at: float | None = None
    toxicity_withdraw_at: float | None = None
    toxicity_extra_ticks: float = 0.0

    def __post_init__(self) -> None:
        if self.inventory_gamma < 0.0:
            raise ValueError("inventory_gamma must be non-negative")
        if self.base_half_spread_ticks <= 0.0:
            raise ValueError("base_half_spread_ticks must be positive")
        if self.max_fv_age_ms <= 0:
            raise ValueError("max_fv_age_ms must be positive")
        if self.max_distance_ticks <= 0.0:
            raise ValueError("max_distance_ticks must be positive")
        if self.min_external_edge_ticks < 0.0:
            raise ValueError("min_external_edge_ticks must be non-negative")
        if self.toxicity_widen_at is not None and not 0.0 <= self.toxicity_widen_at <= 1.0:
            raise ValueError("invalid toxicity_widen_at")
        if self.toxicity_withdraw_at is not None and not 0.0 <= self.toxicity_withdraw_at <= 1.0:
            raise ValueError("invalid toxicity_withdraw_at")
        if (
            self.toxicity_widen_at is not None
            and self.toxicity_withdraw_at is not None
            and self.toxicity_widen_at > self.toxicity_withdraw_at
        ):
            raise ValueError("toxicity widen threshold exceeds withdraw threshold")


@dataclass(frozen=True, slots=True)
class QuoteIntent:
    market_id: str
    timestamp_ns: int
    policy_id: str
    reservation_fv: float
    bid: float | None
    ask: float | None
    bid_size: float
    ask_size: float
    reason: str


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    market_id: str
    timestamp_ns: int
    policy_id: str
    side: Side
    price: float
    size: float
    assumption: FillAssumption


@dataclass(frozen=True, slots=True)
class Markout:
    horizon_s: int
    side: Side
    fill_price: float
    future_fv: float
    value: float


@dataclass(frozen=True, slots=True)
class ReplayFillResult:
    fill: SimulatedFill
    quote_timestamp_ns: int
    reservation_fv: float
    gross_spread_capture: float
    markouts: Mapping[int, float]
    fee_cost: float
    unwind_cost: float
    estimated_edge_5m: float | None


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    policy_id: str
    fill_assumption: FillAssumption
    quotes: int
    active_quotes: int
    fills: int
    active_fraction: float
    mean_gross_spread_capture: float | None
    mean_markout_5m: float | None
    mean_estimated_edge_5m: float | None


class FillModel(Protocol):
    assumption: FillAssumption

    def fills(self, quote: QuoteIntent, event: BookObservation) -> tuple[SimulatedFill, ...]: ...


class ConservativeTradeFillModel:
    """Fill only from observed aggressive trades, never from a mere price touch."""

    assumption = FillAssumption.OBSERVED_TRADE

    def fills(self, quote: QuoteIntent, event: BookObservation) -> tuple[SimulatedFill, ...]:
        if event.trade_price is None or event.trade_size is None or event.trade_size <= 0.0:
            return ()
        side = event.aggressor_side
        fills: list[SimulatedFill] = []
        if (
            side is Side.SELL
            and quote.bid is not None
            and quote.bid_size > 0.0
            and event.trade_price <= quote.bid
        ):
            fills.append(
                SimulatedFill(
                    market_id=quote.market_id,
                    timestamp_ns=event.timestamp_ns,
                    policy_id=quote.policy_id,
                    side=Side.BUY,
                    price=quote.bid,
                    size=min(quote.bid_size, event.trade_size),
                    assumption=self.assumption,
                )
            )
        if (
            side is Side.BUY
            and quote.ask is not None
            and quote.ask_size > 0.0
            and event.trade_price >= quote.ask
        ):
            fills.append(
                SimulatedFill(
                    market_id=quote.market_id,
                    timestamp_ns=event.timestamp_ns,
                    policy_id=quote.policy_id,
                    side=Side.SELL,
                    price=quote.ask,
                    size=min(quote.ask_size, event.trade_size),
                    assumption=self.assumption,
                )
            )
        return tuple(fills)


class QueueAwareFillModel:
    """Observed-trade fill model with explicit queue-ahead depletion."""

    assumption = FillAssumption.QUEUE_AWARE

    def fills(self, quote: QuoteIntent, event: BookObservation) -> tuple[SimulatedFill, ...]:
        if event.trade_price is None or event.trade_size is None or event.trade_size <= 0.0:
            return ()
        fills: list[SimulatedFill] = []
        if (
            event.aggressor_side is Side.SELL
            and quote.bid is not None
            and event.trade_price <= quote.bid
            and event.queue_ahead_bid is not None
        ):
            residual = max(0.0, event.trade_size - event.queue_ahead_bid)
            if residual > 0.0:
                fills.append(
                    SimulatedFill(
                        quote.market_id,
                        event.timestamp_ns,
                        quote.policy_id,
                        Side.BUY,
                        quote.bid,
                        min(quote.bid_size, residual),
                        self.assumption,
                    )
                )
        if (
            event.aggressor_side is Side.BUY
            and quote.ask is not None
            and event.trade_price >= quote.ask
            and event.queue_ahead_ask is not None
        ):
            residual = max(0.0, event.trade_size - event.queue_ahead_ask)
            if residual > 0.0:
                fills.append(
                    SimulatedFill(
                        quote.market_id,
                        event.timestamp_ns,
                        quote.policy_id,
                        Side.SELL,
                        quote.ask,
                        min(quote.ask_size, residual),
                        self.assumption,
                    )
                )
        return tuple(fills)


class TradeThroughSensitivityFillModel:
    """Sensitivity case: require a trade strictly through the passive quote."""

    assumption = FillAssumption.TRADE_THROUGH_SENSITIVITY

    def fills(self, quote: QuoteIntent, event: BookObservation) -> tuple[SimulatedFill, ...]:
        if event.trade_price is None or event.trade_size is None or event.trade_size <= 0.0:
            return ()
        if (
            event.aggressor_side is Side.SELL
            and quote.bid is not None
            and event.trade_price < quote.bid
        ):
            return (
                SimulatedFill(
                    quote.market_id,
                    event.timestamp_ns,
                    quote.policy_id,
                    Side.BUY,
                    quote.bid,
                    min(quote.bid_size, event.trade_size),
                    self.assumption,
                ),
            )
        if (
            event.aggressor_side is Side.BUY
            and quote.ask is not None
            and event.trade_price > quote.ask
        ):
            return (
                SimulatedFill(
                    quote.market_id,
                    event.timestamp_ns,
                    quote.policy_id,
                    Side.SELL,
                    quote.ask,
                    min(quote.ask_size, event.trade_size),
                    self.assumption,
                ),
            )
        return ()


def load_binding(path: Path) -> DatasetBinding:
    return DatasetBinding.load(path)


def inspect_input(root: Path, binding: DatasetBinding) -> InputAudit:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        binding.require_bound()
    except InputContractError as exc:
        errors.append(str(exc))

    files: list[FileSchema] = []
    all_columns: set[str] = set()
    formats: set[str] = set()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        suffix = path.suffix.lower()
        if suffix not in {".parquet", ".csv", ".jsonl", ".json"}:
            continue
        rel = str(path.relative_to(root))
        columns: tuple[str, ...] = ()
        rows: int | None = None
        fmt = suffix.lstrip(".")
        formats.add(fmt)
        try:
            if suffix == ".parquet":
                parquet_file = pq.ParquetFile(path)
                columns = tuple(parquet_file.schema_arrow.names)
                rows = parquet_file.metadata.num_rows
            elif suffix == ".csv":
                with path.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.reader(handle)
                    columns = tuple(next(reader, ()))
            else:
                warnings.append(f"{rel}: schema inspection limited for {fmt}")
        except (OSError, ValueError) as exc:
            errors.append(f"{rel}: schema inspection failed: {exc}")
        all_columns.update(columns)
        expected_hash = binding.file_hashes.get(rel)
        actual_hash = sha256_file(path) if expected_hash is not None else None
        if expected_hash is not None and actual_hash != expected_hash:
            errors.append(f"{rel}: sha256 mismatch")
        files.append(FileSchema(rel, fmt, columns, rows, actual_hash))

    if not files:
        errors.append("no supported parquet/csv/jsonl/json input files found")

    suggested = _suggest_column_map(all_columns)
    resolved = dict(suggested)
    resolved.update(binding.column_map)

    has_identity = "market_id" in resolved
    has_time = "event_timestamp" in resolved
    has_snapshot_bbo = "best_bid" in resolved and "best_ask" in resolved
    has_delta_book = all(
        key in resolved for key in ("book_side", "book_price", "book_size", "book_action")
    )
    has_trade = all(key in resolved for key in ("trade_price", "trade_size", "aggressor_side"))
    has_external_fv = "external_fv" in resolved

    if not has_identity:
        errors.append("market/token identity column could not be resolved")
    if not has_time:
        errors.append("event timestamp column could not be resolved")
    if not (has_snapshot_bbo or has_delta_book):
        errors.append("top-of-book cannot be reconstructed from resolved columns")
    if not has_trade:
        warnings.append(
            "observed trade evidence not resolved; conservative passive-fill "
            "replay may be unavailable"
        )
    if not has_external_fv:
        warnings.append(
            "external FV column not resolved; B1/B2/B3 require an explicit "
            "external-FV provider/join"
        )

    capabilities = {
        "snapshot_bbo": has_snapshot_bbo,
        "delta_book_reconstruction": has_delta_book,
        "observed_trade_fill_evidence": has_trade,
        "external_fair_value": has_external_fv,
        "005f_exact_bbo_features": (
            has_identity and has_time and (has_snapshot_bbo or has_delta_book)
        ),
        "queue_aware_fill": "queue_ahead_bid" in resolved and "queue_ahead_ask" in resolved,
    }
    return InputAudit(
        experiment=EXPERIMENT_ID,
        passed=not errors,
        binding_status=binding.status,
        file_count=len(files),
        formats=tuple(sorted(formats)),
        columns=tuple(sorted(all_columns)),
        suggested_column_map=resolved,
        capabilities=capabilities,
        errors=tuple(errors),
        warnings=tuple(warnings),
        files=tuple(files),
    )


def write_audit(audit: InputAudit, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "INPUT_AUDIT.json").write_text(
        json.dumps(audit.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "INPUT_AUDIT.md").write_text(audit.to_markdown(), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


