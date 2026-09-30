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
import json
import math
from bisect import bisect_left
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
    "external_fv_timestamp": (
        "external_fv_timestamp_ns",
        "reference_timestamp_ns",
        "pm_timestamp_ns",
    ),
    "bid_depth": ("bid_depth", "best_bid_size", "bid_size"),
    "ask_depth": ("ask_depth", "best_ask_size", "ask_size"),
    "queue_ahead_bid": ("queue_ahead_bid",),
    "queue_ahead_ask": ("queue_ahead_ask",),
    "category": ("category", "market_category"),
    "event_id": ("event_id", "sequence_id", "update_id"),
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


def mapped_external_fv(
    *,
    best_bid: float,
    best_ask: float,
    mapping_class: str,
    mapping_direction: str,
    allow_near: bool = False,
) -> float:
    if not (0.0 <= best_bid <= best_ask <= 1.0):
        raise ValueError("invalid external BBO")
    mapping_class = mapping_class.upper()
    mapping_direction = mapping_direction.upper()
    if mapping_class == "NEAR" and not allow_near:
        raise InputContractError("NEAR mapping is not approved as EXACT fair value")
    if mapping_class not in {"EXACT", "NEAR"}:
        raise InputContractError("direct external FV requires EXACT (or explicitly approved NEAR)")
    value = (best_bid + best_ask) / 2.0
    if mapping_direction in {"SAME", "YES"}:
        return value
    if mapping_direction in {"COMPLEMENT", "INVERSE", "NO"}:
        return 1.0 - value
    raise InputContractError(f"unsupported mapping_direction={mapping_direction}")


def binary_cara_reservation(fair_value: float, inventory: float, gamma: float) -> float:
    if not 0.0 < fair_value < 1.0:
        raise ValueError("fair value must be strictly inside (0,1)")
    if gamma < 0.0 or not math.isfinite(gamma):
        raise ValueError("gamma must be finite and non-negative")
    logit = math.log(fair_value / (1.0 - fair_value)) - gamma * inventory
    if logit >= 0.0:
        reservation = 1.0 / (1.0 + math.exp(-logit))
    else:
        exp_value = math.exp(logit)
        reservation = exp_value / (1.0 + exp_value)
    return min(0.995, max(0.005, reservation))


def build_quote(
    observation: BookObservation,
    policy: MakerPolicy,
    *,
    inventory: float = 0.0,
    base_size: float = 1.0,
) -> QuoteIntent:
    local_mid = observation.local_mid
    if local_mid is None:
        return QuoteIntent(
            observation.market_id,
            observation.timestamp_ns,
            policy.policy_id,
            0.5,
            None,
            None,
            0.0,
            0.0,
            "invalid_local_bbo",
        )

    if policy.anchor == "EXTERNAL_FV":
        if observation.external_fv is None or observation.external_fv_timestamp_ns is None:
            return QuoteIntent(
                observation.market_id,
                observation.timestamp_ns,
                policy.policy_id,
                local_mid,
                None,
                None,
                0.0,
                0.0,
                "external_fv_unavailable",
            )
        age_ns = observation.timestamp_ns - observation.external_fv_timestamp_ns
        if age_ns < 0 or age_ns >= policy.max_fv_age_ms * 1_000_000:
            return QuoteIntent(
                observation.market_id,
                observation.timestamp_ns,
                policy.policy_id,
                observation.external_fv,
                None,
                None,
                0.0,
                0.0,
                "external_fv_stale",
            )
        fair_value = observation.external_fv
    else:
        fair_value = local_mid

    if not 0.0 < fair_value < 1.0:
        return QuoteIntent(
            observation.market_id,
            observation.timestamp_ns,
            policy.policy_id,
            fair_value,
            None,
            None,
            0.0,
            0.0,
            "fair_value_out_of_bounds",
        )

    reservation = binary_cara_reservation(fair_value, inventory, policy.inventory_gamma)
    if (
        policy.toxicity_withdraw_at is not None
        and observation.update_hazard is None
    ):
        return QuoteIntent(
            observation.market_id,
            observation.timestamp_ns,
            policy.policy_id,
            reservation,
            None,
            None,
            0.0,
            0.0,
            "toxicity_unavailable",
        )
    hazard = observation.update_hazard or 0.0
    if (
        policy.toxicity_withdraw_at is not None
        and hazard >= policy.toxicity_withdraw_at
    ):
        return QuoteIntent(
            observation.market_id,
            observation.timestamp_ns,
            policy.policy_id,
            reservation,
            None,
            None,
            0.0,
            0.0,
            "toxicity_withdraw",
        )

    half_spread = policy.base_half_spread_ticks * TICK
    size_scale = 1.0
    reason = "normal"
    if policy.toxicity_widen_at is not None and hazard >= policy.toxicity_widen_at:
        half_spread += policy.toxicity_extra_ticks * hazard * TICK
        size_scale = max(0.1, 1.0 - hazard)
        reason = "toxicity_widen"

    raw_bid = reservation - half_spread
    raw_ask = reservation + half_spread
    bid = _floor_tick(raw_bid)
    ask = _ceil_tick(raw_ask)

    assert observation.best_bid is not None
    assert observation.best_ask is not None
    bid = min(bid, observation.best_ask - TICK)
    ask = max(ask, observation.best_bid + TICK)

    max_distance = policy.max_distance_ticks * TICK
    if reservation - bid > max_distance:
        bid = None
    if ask - reservation > max_distance:
        ask = None

    min_edge = policy.min_external_edge_ticks * TICK
    if policy.anchor == "EXTERNAL_FV":
        if bid is not None and fair_value - bid < min_edge:
            bid = None
        if ask is not None and ask - fair_value < min_edge:
            ask = None

    bid = _legal_probability(bid)
    ask = _legal_probability(ask)
    if bid is not None and ask is not None and bid >= ask:
        bid = None
        ask = None

    scaled_size = max(0.0, base_size * size_scale)
    return QuoteIntent(
        observation.market_id,
        observation.timestamp_ns,
        policy.policy_id,
        reservation,
        bid,
        ask,
        scaled_size if bid is not None else 0.0,
        scaled_size if ask is not None else 0.0,
        reason if bid is not None or ask is not None else "no_eligible_side",
    )


def markout(fill: SimulatedFill, *, horizon_s: int, future_fv: float) -> Markout:
    if horizon_s not in MARKOUT_HORIZONS_S:
        raise ValueError("unsupported markout horizon")
    if not 0.0 <= future_fv <= 1.0:
        raise ValueError("future_fv outside [0,1]")
    value = (
        future_fv - fill.price
        if fill.side is Side.BUY
        else fill.price - future_fv
    )
    return Markout(horizon_s, fill.side, fill.price, future_fv, value)


def estimated_maker_edge(
    *,
    gross_spread_capture: float,
    adverse_selection_cost: float,
    fees: float,
    unwind_cost: float,
) -> float:
    return gross_spread_capture - adverse_selection_cost - fees - unwind_cost


def quote_survives_reaction_delay(
    *,
    quote_timestamp_ns: int,
    invalidation_timestamp_ns: int,
    event_timestamp_ns: int,
    reaction_delay_ms: int,
) -> bool:
    if reaction_delay_ms < 0:
        raise ValueError("reaction_delay_ms must be non-negative")
    if event_timestamp_ns < quote_timestamp_ns:
        return False
    cancel_effective_ns = invalidation_timestamp_ns + reaction_delay_ms * 1_000_000
    return event_timestamp_ns < cancel_effective_ns


def replay_market(
    observations: Sequence[BookObservation],
    *,
    policy: MakerPolicy,
    fill_model: FillModel,
    base_size: float = 1.0,
    fee_per_share: float = 0.0,
    unwind_cost_per_share: float = 0.0,
) -> tuple[tuple[ReplayFillResult, ...], ReplaySummary]:
    if fee_per_share < 0.0 or unwind_cost_per_share < 0.0:
        raise ValueError("cost assumptions must be non-negative")
    if not observations:
        return (
            (),
            ReplaySummary(
                policy.policy_id,
                fill_model.assumption,
                0,
                0,
                0,
                0.0,
                None,
                None,
                None,
            ),
        )
    for previous, current in zip(observations, observations[1:], strict=False):
        if current.market_id != previous.market_id:
            raise InputContractError("replay_market accepts exactly one market")
        if current.timestamp_ns < previous.timestamp_ns:
            raise InputContractError("observations must be ordered without lookahead")

    timestamps = [item.timestamp_ns for item in observations]
    inventory = 0.0
    quote = build_quote(observations[0], policy, inventory=inventory, base_size=base_size)
    quotes = 1
    active_quotes = int(quote.bid is not None or quote.ask is not None)
    results: list[ReplayFillResult] = []

    for index in range(1, len(observations)):
        event = observations[index]
        event_fills = fill_model.fills(quote, event)
        for fill in event_fills:
            inventory += fill.size if fill.side is Side.BUY else -fill.size
            markout_values: dict[int, float] = {}
            for horizon_s in MARKOUT_HORIZONS_S:
                future = _future_fv(observations, timestamps, index, horizon_s)
                if future is not None:
                    markout_values[horizon_s] = markout(
                        fill,
                        horizon_s=horizon_s,
                        future_fv=future,
                    ).value
            gross = (
                quote.reservation_fv - fill.price
                if fill.side is Side.BUY
                else fill.price - quote.reservation_fv
            )
            fee_cost = fee_per_share * fill.size
            unwind_cost = unwind_cost_per_share * fill.size
            markout_5m = markout_values.get(300)
            estimated = (
                markout_5m * fill.size - fee_cost - unwind_cost
                if markout_5m is not None
                else None
            )
            results.append(
                ReplayFillResult(
                    fill=fill,
                    quote_timestamp_ns=quote.timestamp_ns,
                    reservation_fv=quote.reservation_fv,
                    gross_spread_capture=gross,
                    markouts=markout_values,
                    fee_cost=fee_cost,
                    unwind_cost=unwind_cost,
                    estimated_edge_5m=estimated,
                )
            )

        quote = build_quote(event, policy, inventory=inventory, base_size=base_size)
        quotes += 1
        active_quotes += int(quote.bid is not None or quote.ask is not None)

    gross_values = [item.gross_spread_capture for item in results]
    markouts_5m = [item.markouts[300] for item in results if 300 in item.markouts]
    edge_5m = [
        item.estimated_edge_5m
        for item in results
        if item.estimated_edge_5m is not None
    ]
    summary = ReplaySummary(
        policy_id=policy.policy_id,
        fill_assumption=fill_model.assumption,
        quotes=quotes,
        active_quotes=active_quotes,
        fills=len(results),
        active_fraction=active_quotes / quotes if quotes else 0.0,
        mean_gross_spread_capture=_mean_or_none(gross_values),
        mean_markout_5m=_mean_or_none(markouts_5m),
        mean_estimated_edge_5m=_mean_or_none(
            [cast(float, value) for value in edge_5m]
        ),
    )
    return tuple(results), summary


def fair_value_convergence(
    observations: Sequence[BookObservation],
) -> tuple[dict[str, float | int | str], ...]:
    if not observations:
        return ()
    timestamps = [item.timestamp_ns for item in observations]
    rows: list[dict[str, float | int | str]] = []
    for index, observation in enumerate(observations):
        local_mid = observation.local_mid
        external = observation.external_fv
        if local_mid is None or external is None:
            continue
        row: dict[str, float | int | str] = {
            "market_id": observation.market_id,
            "timestamp_ns": observation.timestamp_ns,
            "gap_t": local_mid - external,
        }
        for horizon_s in MARKOUT_HORIZONS_S:
            future_index = bisect_left(
                timestamps,
                observation.timestamp_ns + horizon_s * 1_000_000_000,
                lo=index,
            )
            if future_index >= len(observations):
                continue
            future = observations[future_index]
            future_mid = future.local_mid
            future_external = future.external_fv
            if future_mid is not None:
                row[f"target_move_{horizon_s}s"] = future_mid - local_mid
            if future_external is not None:
                row[f"external_move_{horizon_s}s"] = future_external - external
            if future_mid is not None and future_external is not None:
                row[f"gap_{horizon_s}s"] = future_mid - future_external
        rows.append(row)
    return tuple(rows)


def toxicity_bucket(score: float | None) -> str:
    if score is None or not math.isfinite(score):
        return "UNAVAILABLE"
    if score < 0.20:
        return "SAFE"
    if score < 0.50:
        return "NORMAL"
    if score < 0.80:
        return "CAUTION"
    return "WITHDRAW"


def _future_fv(
    observations: Sequence[BookObservation],
    timestamps: Sequence[int],
    current_index: int,
    horizon_s: int,
) -> float | None:
    target = observations[current_index].timestamp_ns + horizon_s * 1_000_000_000
    future_index = bisect_left(timestamps, target, lo=current_index)
    while future_index < len(observations):
        value = observations[future_index].external_fv
        if value is not None:
            return value
        future_index += 1
    return None


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True, slots=True)
class _HistoricalSnapshot:
    observed_at: datetime
    observed_monotonic_ns: int


class Frozen005FTransferAdapter:
    """Historical bridge into the accepted CANDIDATE-RUNTIME-001 005F feature state."""

    def __init__(self, *, scope_id: str, grid_origin_ns: int) -> None:
        self._scope_id = scope_id

        def resolve_scope(_snapshot: CanonicalShadowSnapshot) -> str:
            return self._scope_id

        self._state = IncrementalHazard005FState(
            grid_origin_ns=grid_origin_ns,
            scope_resolver=resolve_scope,
        )

    def observe(
        self,
        *,
        timestamp_ns: int,
        best_bid: float | None,
        best_ask: float | None,
        observed_monotonic_ns: int | None = None,
        source_version: str = "mm-replay-001",
        ambiguous: bool = False,
    ) -> None:
        self._state.observe(
            Hazard005FBboObservation(
                scope_id=self._scope_id,
                timestamp_ns=timestamp_ns,
                observed_monotonic_ns=(
                    timestamp_ns if observed_monotonic_ns is None else observed_monotonic_ns
                ),
                best_bid=best_bid,
                best_ask=best_ask,
                source_version=source_version,
                ambiguous=ambiguous,
            )
        )

    def features(self, *, query_timestamp_ns: int) -> Mapping[str, float] | None:
        observed_at = datetime.fromtimestamp(query_timestamp_ns / 1_000_000_000, tz=UTC)
        historical = _HistoricalSnapshot(
            observed_at=observed_at,
            observed_monotonic_ns=query_timestamp_ns,
        )
        vector = self._state.feature_vector(cast(CanonicalShadowSnapshot, historical))
        return None if vector is None else dict(vector.values)


def default_policies() -> tuple[MakerPolicy, ...]:
    return (
        MakerPolicy(
            policy_id="B0-local-mid",
            anchor="LOCAL_MID",
            inventory_gamma=0.0,
            base_half_spread_ticks=1.0,
            max_fv_age_ms=1000,
            max_distance_ticks=8.0,
            min_external_edge_ticks=0.0,
        ),
        MakerPolicy(
            policy_id="B1-external-fv",
            anchor="EXTERNAL_FV",
            inventory_gamma=0.0,
            base_half_spread_ticks=1.0,
            max_fv_age_ms=1000,
            max_distance_ticks=8.0,
            min_external_edge_ticks=1.0,
        ),
        MakerPolicy(
            policy_id="B2-external-fv-inventory",
            anchor="EXTERNAL_FV",
            inventory_gamma=0.02,
            base_half_spread_ticks=1.0,
            max_fv_age_ms=1000,
            max_distance_ticks=8.0,
            min_external_edge_ticks=1.0,
        ),
        MakerPolicy(
            policy_id="B3-external-fv-toxicity",
            anchor="EXTERNAL_FV",
            inventory_gamma=0.02,
            base_half_spread_ticks=1.0,
            max_fv_age_ms=1000,
            max_distance_ticks=8.0,
            min_external_edge_ticks=1.0,
            toxicity_widen_at=0.35,
            toxicity_withdraw_at=0.80,
            toxicity_extra_ticks=4.0,
        ),
    )


def expected_output_schema() -> dict[str, Any]:
    return {
        "experiment": EXPERIMENT_ID,
        "markout_horizons_s": list(MARKOUT_HORIZONS_S),
        "cancel_latency_ms": list(CANCEL_LATENCIES_MS),
        "fill_assumptions": [item.value for item in FillAssumption],
        "policies": [asdict(policy) for policy in default_policies()],
        "outputs": list(REQUIRED_OUTPUT_FILES),
        "optional_outputs": list(OPTIONAL_OUTPUT_FILES),
        "real_sig_orders": False,
    }


def _suggest_column_map(columns: Iterable[str]) -> dict[str, str]:
    normalized = {name.lower(): name for name in columns}
    result: dict[str, str] = {}
    for canonical, candidates in COLUMN_SYNONYMS.items():
        matches = [normalized[name.lower()] for name in candidates if name.lower() in normalized]
        if len(matches) == 1:
            result[canonical] = matches[0]
    return result


def _floor_tick(value: float) -> float:
    return math.floor(value / TICK + 1e-12) * TICK


def _ceil_tick(value: float) -> float:
    return math.ceil(value / TICK - 1e-12) * TICK


def _legal_probability(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    if value < TICK or value > 1.0 - TICK:
        return None
    return round(value, 12)


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InputContractError("boolean is not a valid integer manifest field")
    return int(value)


def _string_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InputContractError("manifest mapping field must be an object")
    result: dict[str, str] = {}
    for key, item in cast(dict[Any, Any], value).items():
        result[str(key)] = str(item)
    return result
