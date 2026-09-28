"""Core leakage-safe primitives for EXPERIMENT-005B."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

import numpy as np

BINARY_OUTCOMES = frozenset({"YES", "NO"})
FORBIDDEN_PREDICTOR_FRAGMENTS = (
    "address",
    "fee",
    "role",
    "rebate",
    "taker",
    "maker",
    "counterparty",
)


@dataclass(frozen=True)
class ReconstructedTrade:
    family: str
    event_id: str
    market_id: str
    condition_id: str
    timestamp: int
    tx_hash: str
    log_index: int
    p_yes: Decimal
    size_shares: Decimal
    value_usd: Decimal

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("p_yes", "size_shares", "value_usd"):
            payload[key] = str(payload[key])
        return payload


class ReconstructionError(ValueError):
    """A transaction-condition group cannot be proven to represent valid binary trades."""


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive conversion boundary
        raise ReconstructionError(f"{name} is not numeric") from exc
    if not result.is_finite():
        raise ReconstructionError(f"{name} is not finite")
    return result


def canonical_yes_price(outcome: str, price: Any) -> Decimal:
    p = _decimal(price, "price")
    if not Decimal("0") <= p <= Decimal("1"):
        raise ReconstructionError("price outside [0,1]")
    side = str(outcome).upper()
    if side == "YES":
        return p
    if side == "NO":
        return Decimal("1") - p
    raise ReconstructionError(f"non-binary outcome {outcome!r}")


def _within_tolerance(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    scale = max(Decimal("1"), abs(left), abs(right))
    return abs(left - right) <= tolerance * scale


def reconstruct_transaction_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    tolerance: Decimal = Decimal("1e-8"),
) -> tuple[ReconstructedTrade, ...]:
    if not rows:
        raise ReconstructionError("empty group")
    active = [row for row in rows if bool(row.get("order_is_match_taker_order"))]
    passive = [row for row in rows if not bool(row.get("order_is_match_taker_order"))]
    if len(active) != 1:
        raise ReconstructionError(f"expected exactly one active row, found {len(active)}")
    if not passive:
        raise ReconstructionError("no passive rows")

    family = str(rows[0]["family"])
    condition_id = str(rows[0]["condition_id"])
    tx_hash = str(rows[0]["tx_hash"])
    for row in rows:
        if str(row["family"]) != family:
            raise ReconstructionError("mixed family in group")
        if str(row["condition_id"]) != condition_id:
            raise ReconstructionError("mixed condition_id in group")
        if str(row["tx_hash"]) != tx_hash:
            raise ReconstructionError("mixed tx_hash in group")
        if str(row["outcome_side"]).upper() not in BINARY_OUTCOMES:
            raise ReconstructionError("non-binary outcome in group")

    active_row = active[0]
    active_size = _decimal(active_row["size_shares"], "active size")
    passive_size = sum((_decimal(row["size_shares"], "passive size") for row in passive), Decimal())
    if not _within_tolerance(active_size, passive_size, tolerance):
        raise ReconstructionError("size conservation failed")

    active_yes_notional = canonical_yes_price(
        str(active_row["outcome_side"]), active_row["price"]
    ) * active_size
    passive_yes_notional = sum(
        (
            canonical_yes_price(str(row["outcome_side"]), row["price"])
            * _decimal(row["size_shares"], "passive size")
            for row in passive
        ),
        Decimal(),
    )
    if not _within_tolerance(active_yes_notional, passive_yes_notional, tolerance):
        raise ReconstructionError("YES-axis price/size conservation failed")

    trades = tuple(
        ReconstructedTrade(
            family=str(row["family"]),
            event_id=str(row["event_id"]),
            market_id=str(row["market_id"]),
            condition_id=str(row["condition_id"]),
            timestamp=int(row["timestamp"]),
            tx_hash=str(row["tx_hash"]),
            log_index=int(row["log_index"]),
            p_yes=canonical_yes_price(str(row["outcome_side"]), row["price"]),
            size_shares=_decimal(row["size_shares"], "size_shares"),
            value_usd=_decimal(row["value_usd"], "value_usd"),
        )
        for row in sorted(
            passive,
            key=lambda item: (
                int(item["timestamp"]),
                str(item["tx_hash"]),
                int(item["log_index"]),
            ),
        )
    )
    return trades


def family_time_boundaries(
    minimum_timestamp: int,
    maximum_timestamp: int,
    *,
    train_fraction: float = 0.60,
    dev_fraction: float = 0.20,
) -> tuple[int, int]:
    if maximum_timestamp <= minimum_timestamp:
        raise ValueError("timestamp coverage must have positive span")
    if not (0.0 < train_fraction < 1.0 and 0.0 < dev_fraction < 1.0):
        raise ValueError("split fractions must lie inside (0,1)")
    if train_fraction + dev_fraction >= 1.0:
        raise ValueError("TRAIN+DEV must leave a positive HOLDOUT span")
    span = maximum_timestamp - minimum_timestamp
    train_end = minimum_timestamp + int(span * train_fraction)
    dev_end = minimum_timestamp + int(span * (train_fraction + dev_fraction))
    if not minimum_timestamp < train_end < dev_end < maximum_timestamp:
        raise ValueError("timestamp span too small for requested split")
    return train_end, dev_end


def clock_target_indices(times: np.ndarray, horizon_seconds: int) -> np.ndarray:
    ordered = np.asarray(times, dtype=np.int64)
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if np.any(np.diff(ordered) < 0):
        raise ValueError("times must be non-decreasing")
    out = np.full(len(ordered), -1, dtype=np.int64)
    for index, timestamp in enumerate(ordered):
        lo = int(np.searchsorted(ordered, timestamp, side="right"))
        hi = int(np.searchsorted(ordered, timestamp + horizon_seconds, side="right"))
        if hi > lo:
            out[index] = hi - 1
    return out


def event_trade_target_indices(length: int, trade_count: int) -> np.ndarray:
    if length < 0 or trade_count <= 0:
        raise ValueError("invalid length or trade_count")
    indices = np.arange(length, dtype=np.int64) + trade_count
    indices[indices >= length] = -1
    return indices


def leave_one_out_mean(total: np.ndarray, count: np.ndarray, own: np.ndarray) -> np.ndarray:
    total_values = np.asarray(total, dtype=np.float64)
    count_values = np.asarray(count, dtype=np.float64)
    own_values = np.asarray(own, dtype=np.float64)
    if not (total_values.shape == count_values.shape == own_values.shape):
        raise ValueError("leave-one-out arrays must have matching shapes")
    denominator = count_values - 1.0
    return np.divide(
        total_values - own_values,
        denominator,
        out=np.full(total_values.shape, np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def assert_identity_blind_predictors(columns: Sequence[str]) -> None:
    violations = sorted(
        {
            name
            for name in columns
            if any(fragment in name.lower() for fragment in FORBIDDEN_PREDICTOR_FRAGMENTS)
        }
    )
    if violations:
        raise ValueError(f"identity/role/fee predictors forbidden in 005B: {violations}")


def canonical_spec_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
