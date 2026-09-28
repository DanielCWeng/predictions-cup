"""Fill-level role markouts and participant-conditioned flow primitives for EXPERIMENT-005A."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa

from predictions_cup.learning.fee_role_structure import canonical_yes_pressure_sign
from predictions_cup.learning.flow_response import (
    NS,
    BBOReconstruction,
    asof_index,
    observation_confirmation_ns,
)


@dataclass(frozen=True)
class ConditionTarget:
    token_id: str
    market_family: str


def end_of_chain_second_ns(timestamp_seconds: int) -> int:
    return (int(timestamp_seconds) + 1) * NS - 1


def before_chain_second_ns(timestamp_seconds: int) -> int:
    return int(timestamp_seconds) * NS - 1


def build_role_markouts(
    role_table: pa.Table,
    condition_targets: Mapping[str, ConditionTarget],
    bbo_by_token: Mapping[str, BBOReconstruction],
    collector_times_ns: np.ndarray,
    *,
    horizon_seconds: int,
    allowed_role_classes: frozenset[str],
    excluded_participants: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Build owner-perspective fill markouts on canonical YES midpoint.

    The decision instant is the final nanosecond of the fill's chain second. A markout
    is available only when the target interval passes the same observation-exposure
    gate used by the grid response panels. label_available_ns is the first valid target
    observation at or after the horizon endpoint, not merely the nominal endpoint.
    """

    required = (
        "maker_address",
        "role_condition_id",
        "role_timestamp",
        "role_class",
        "role_outcome_side",
        "role_participant_side",
        "role_value_usd",
        "role_size_shares",
    )
    missing = [name for name in required if name not in role_table.column_names]
    if missing:
        raise ValueError(f"role-markout columns missing: {missing}")
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")

    output: list[dict[str, Any]] = []
    for row in role_table.select(required).to_pylist():
        role_class = str(row["role_class"])
        if role_class not in allowed_role_classes:
            continue
        participant = str(row["maker_address"]).lower()
        if participant in excluded_participants:
            continue
        condition = row["role_condition_id"]
        timestamp = row["role_timestamp"]
        if condition is None or timestamp is None:
            continue
        target = condition_targets.get(str(condition))
        if target is None:
            continue
        series = bbo_by_token.get(target.token_id)
        if series is None:
            continue

        decision = end_of_chain_second_ns(int(timestamp))
        endpoint = decision + horizon_seconds * NS
        current_index = asof_index(series, decision)
        future_index = asof_index(series, endpoint)
        confirmation = observation_confirmation_ns(
            series,
            decision,
            endpoint,
            collector_times_ns,
        )

        future_move = np.nan
        owner_markout = np.nan
        if current_index is not None and future_index is not None and confirmation is not None:
            future_move = float(series.mid[future_index] - series.mid[current_index])
            sign = canonical_yes_pressure_sign(
                str(row["role_outcome_side"]),
                str(row["role_participant_side"]),
            )
            owner_markout = float(sign * future_move)

        prior_index = asof_index(series, before_chain_second_ns(int(timestamp)))
        same_second_move = np.nan
        if current_index is not None and prior_index is not None:
            same_second_move = float(series.mid[current_index] - series.mid[prior_index])

        output.append(
            {
                "participant": participant,
                "condition_id": str(condition),
                "token_id": target.token_id,
                "market_family": target.market_family,
                "role_class": role_class,
                "role": "TAKER" if role_class.startswith("TAKER_") else "MAKER",
                "timestamp_seconds": int(timestamp),
                "decision_ns": decision,
                "label_available_ns": -1 if confirmation is None else int(confirmation),
                "outcome_side": str(row["role_outcome_side"]),
                "participant_side": str(row["role_participant_side"]),
                "owner_yes_sign": canonical_yes_pressure_sign(
                    str(row["role_outcome_side"]),
                    str(row["role_participant_side"]),
                ),
                "value_usd": float(row["role_value_usd"]),
                "size_shares": float(row["role_size_shares"]),
                "same_second_move": same_second_move,
                "future_move": future_move,
                "owner_markout": owner_markout,
                "adverse_markout": -owner_markout if np.isfinite(owner_markout) else np.nan,
            }
        )
    return output


def aggregate_participant_conditioned_flow(
    markout_rows: list[dict[str, Any]],
    historical_scores: np.ndarray,
    grid_ns: np.ndarray,
    *,
    lookback_seconds: int = 30,
    role: str = "TAKER",
) -> dict[str, dict[str, np.ndarray]]:
    """Aggregate generic and participant-conditioned signed flow by condition and grid."""

    if len(markout_rows) != len(historical_scores):
        raise ValueError("markout rows and historical_scores lengths differ")
    if lookback_seconds <= 0:
        raise ValueError("lookback_seconds must be positive")

    grouped: dict[str, list[tuple[int, float, float, float, float]]] = defaultdict(list)
    for row, score in zip(markout_rows, historical_scores, strict=True):
        if str(row["role"]).upper() != role.upper():
            continue
        timestamp_ns = int(row["timestamp_seconds"]) * NS
        sign = float(row["owner_yes_sign"])
        value = float(row["value_usd"])
        shares = float(row["size_shares"])
        grouped[str(row["condition_id"])].append(
            (
                timestamp_ns,
                sign * value,
                sign * shares,
                sign * value * float(score),
                sign * shares * float(score),
            )
        )

    grid = np.asarray(grid_ns, np.int64)
    lookback_ns = lookback_seconds * NS
    result: dict[str, dict[str, np.ndarray]] = {}
    for condition, values in grouped.items():
        values.sort(key=lambda item: item[0])
        array = np.asarray(values, float)
        times = array[:, 0].astype(np.int64)
        generic_value = np.zeros(len(grid), float)
        generic_shares = np.zeros(len(grid), float)
        conditioned_value = np.zeros(len(grid), float)
        conditioned_shares = np.zeros(len(grid), float)
        for index, decision in enumerate(grid):
            lo = int(np.searchsorted(times, decision - lookback_ns, side="right"))
            hi = int(np.searchsorted(times, decision, side="left"))
            if hi <= lo:
                continue
            generic_value[index] = float(array[lo:hi, 1].sum())
            generic_shares[index] = float(array[lo:hi, 2].sum())
            conditioned_value[index] = float(array[lo:hi, 3].sum())
            conditioned_shares[index] = float(array[lo:hi, 4].sum())
        result[condition] = {
            "signed_value": generic_value,
            "signed_shares": generic_shares,
            "participant_conditioned_value": conditioned_value,
            "participant_conditioned_shares": conditioned_shares,
        }
    return result