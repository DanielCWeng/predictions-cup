# ruff: noqa
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from predictions_cup.mm_replay_001 import (
    CANCEL_LATENCIES_MS,
    MARKOUT_HORIZONS_S,
    BookObservation,
    FillAssumption,
    Frozen005FTransferAdapter,
    MakerPolicy,
    QuoteIntent,
    Side,
    build_quote,
    default_policies,
    toxicity_bucket,
)

from streaming_common import (
    END_NS,
    FV_SAMPLE_NS,
    GRID_NS,
    START_NS,
    BboGroup,
    asof_group,
    bbo_groups,
    finite,
    genuine_times,
    mean,
    score_features,
    split_for,
)


def quote_equivalent(
    left: QuoteIntent,
    right: QuoteIntent,
) -> bool:
    return (
        left.bid == right.bid
        and left.ask == right.ask
        and math.isclose(
            left.bid_size,
            right.bid_size,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(
            left.ask_size,
            right.ask_size,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    )


def deplete_quote(
    quote: QuoteIntent,
    *,
    side: Side,
    size: float,
) -> QuoteIntent:
    bid_size = quote.bid_size
    ask_size = quote.ask_size
    if side is Side.BUY:
        bid_size = max(0.0, bid_size - size)
    else:
        ask_size = max(0.0, ask_size - size)
    return replace(
        quote,
        bid=quote.bid if bid_size > 0.0 else None,
        ask=quote.ask if ask_size > 0.0 else None,
        bid_size=bid_size,
        ask_size=ask_size,
    )


@dataclass
class ScenarioState:
    policy: MakerPolicy
    assumption: FillAssumption
    latency_ms: int
    family: str
    initialized: bool = False
    current_quote: QuoteIntent | None = None
    current_hazard: float | None = None
    pending_quote: QuoteIntent | None = None
    pending_hazard: float | None = None
    pending_effective_ns: int | None = None
    inventory: float = 0.0
    cash_flow: float = 0.0
    traded_size: float = 0.0
    quotes: int = 0
    active_quotes: int = 0
    fills: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.fills is None:
            self.fills = []

    @property
    def scenario_id(self) -> str:
        return (
            f"{self.family}|{self.policy.policy_id}|"
            f"{self.assumption.value}|lat{self.latency_ms}"
        )


def edge_policies() -> list[MakerPolicy]:
    out: list[MakerPolicy] = []
    for base in default_policies()[1:]:
        for ticks in (0.0, 1.0, 2.0, 3.0, 4.0):
            out.append(
                replace(
                    base,
                    policy_id=(
                        f"{base.policy_id}-edge{ticks:g}"
                    ),
                    min_external_edge_ticks=ticks,
                )
            )
    return out


def scenario_states() -> list[ScenarioState]:
    out: list[ScenarioState] = []
    for policy in default_policies():
        for assumption in (
            FillAssumption.OBSERVED_TRADE,
            FillAssumption.TRADE_THROUGH_SENSITIVITY,
        ):
            for latency in CANCEL_LATENCIES_MS:
                out.append(
                    ScenarioState(
                        policy,
                        assumption,
                        int(latency),
                        "LATENCY_SWEEP",
                    )
                )
    for policy in edge_policies():
        out.append(
            ScenarioState(
                policy,
                FillAssumption.OBSERVED_TRADE,
                100,
                "EDGE_GRID_REFERENCE_LATENCY",
            )
        )
    return out


def fill_against_trade(
    state: ScenarioState,
    trade: Any,
    timestamp_ns: int,
) -> None:
    quote = state.current_quote
    if quote is None:
        return

    price = finite(trade.trade_price)
    size = finite(trade.trade_size)
    aggressor = str(trade.trade_side or "").upper()
    if price is None or size is None or size <= 0.0:
        return

    fill_side: Side | None = None
    fill_price: float | None = None
    available = 0.0

    if (
        aggressor == "SELL"
        and quote.bid is not None
        and quote.bid_size > 0.0
    ):
        crossed = (
            price <= quote.bid
            if state.assumption
            is FillAssumption.OBSERVED_TRADE
            else price < quote.bid
        )
        if crossed:
            fill_side = Side.BUY
            fill_price = quote.bid
            available = quote.bid_size

    elif (
        aggressor == "BUY"
        and quote.ask is not None
        and quote.ask_size > 0.0
    ):
        crossed = (
            price >= quote.ask
            if state.assumption
            is FillAssumption.OBSERVED_TRADE
            else price > quote.ask
        )
        if crossed:
            fill_side = Side.SELL
            fill_price = quote.ask
            available = quote.ask_size

    if fill_side is None or fill_price is None:
        return

    fill_size = min(available, size)
    signed = (
        fill_size
        if fill_side is Side.BUY
        else -fill_size
    )
    state.inventory += signed
    state.cash_flow -= fill_price * signed
    state.traded_size += fill_size

    gross = (
        quote.reservation_fv - fill_price
        if fill_side is Side.BUY
        else fill_price - quote.reservation_fv
    )
    assert state.fills is not None
    state.fills.append(
        {
            "scenario_id": state.scenario_id,
            "policy_id": state.policy.policy_id,
            "fill_assumption": state.assumption.value,
            "reaction_delay_ms": state.latency_ms,
            "scenario_family": state.family,
            "timestamp_ns": timestamp_ns,
            "split": split_for(timestamp_ns),
            "side": fill_side.value,
            "fill_price": fill_price,
            "size": fill_size,
            "reservation_fv": quote.reservation_fv,
            "gross_spread_capture": gross,
            "toxicity_score": state.current_hazard,
            "toxicity_bucket": toxicity_bucket(
                state.current_hazard
            ),
        }
    )
    state.current_quote = deplete_quote(
        quote,
        side=fill_side,
        size=fill_size,
    )


def markout_value(
    side: str,
    fill_price: float,
    reference: float | None,
) -> float | None:
    if (
        reference is None
        or not math.isfinite(reference)
    ):
        return None
    return (
        reference - fill_price
        if side == Side.BUY.value
        else fill_price - reference
    )


def replay_condition(
    compact_path: Path,
    record: dict[str, Any],
    model: Any,
    scaler: Any,
    artifact_row: dict[str, Any],
) -> dict[str, Any]:
    frame = pq.read_table(compact_path).to_pandas()
    frame.sort_values(
        ["timestamp_ns", "sequence", "kind"],
        inplace=True,
        kind="stable",
    )

    local_token = str(record["local_token_id"])
    complement_token = str(
        record["complement_token_id"]
    )
    local_groups = bbo_groups(frame, local_token)
    complement_groups = bbo_groups(
        frame,
        complement_token,
    )
    local_times = [
        group.timestamp_ns
        for group in local_groups
    ]
    complement_times = [
        group.timestamp_ns
        for group in complement_groups
    ]
    genuine = genuine_times(local_groups)

    trades = frame[
        (frame["kind"] == "TRADE")
        & (frame["token_id"].astype(str) == local_token)
    ].copy()
    trades.sort_values(
        ["timestamp_ns", "sequence"],
        inplace=True,
        kind="stable",
    )
    trade_groups = {
        int(timestamp_ns): group
        for timestamp_ns, group
        in trades.groupby("timestamp_ns", sort=True)
    }
    local_map = {
        group.timestamp_ns: group
        for group in local_groups
    }
    complement_map = {
        group.timestamp_ns: group
        for group in complement_groups
    }

    timestamps = np.unique(
        np.asarray(
            local_times
            + complement_times
            + list(trade_groups),
            dtype=np.int64,
        )
    )

    adapter = Frozen005FTransferAdapter(
        scope_id=local_token,
        grid_origin_ns=START_NS,
    )
    hazard_cache: dict[
        int,
        tuple[
            float | None,
            dict[str, float] | None,
        ],
    ] = {}
    states = scenario_states()
    current_local: BboGroup | None = None
    current_complement: BboGroup | None = None
    fv_samples: list[
        tuple[int, float, float]
    ] = []
    last_fv_bucket: int | None = None

    def hazard_at(timestamp_ns: int) -> float | None:
        if timestamp_ns < START_NS:
            return None
        grid = START_NS + (
            (timestamp_ns - START_NS) // GRID_NS
        ) * GRID_NS
        cached = hazard_cache.get(grid)
        if cached is not None:
            return cached[0]

        raw = adapter.features(
            query_timestamp_ns=grid
        )
        features = (
            dict(raw)
            if raw is not None
            else None
        )
        score = (
            score_features(
                model,
                scaler,
                artifact_row,
                features,
            )
            if features is not None
            else None
        )
        hazard_cache[grid] = (
            score,
            features,
        )
        return score

    for raw_timestamp_ns in timestamps:
        timestamp_ns = int(raw_timestamp_ns)

        for state in states:
            if (
                state.pending_effective_ns is not None
                and timestamp_ns
                >= state.pending_effective_ns
            ):
                state.current_quote = (
                    state.pending_quote
                )
                state.current_hazard = (
                    state.pending_hazard
                )
                state.pending_quote = None
                state.pending_hazard = None
                state.pending_effective_ns = None

        trade_group = trade_groups.get(
            timestamp_ns
        )
        if trade_group is not None:
            for trade in trade_group.itertuples(
                index=False
            ):
                for state in states:
                    fill_against_trade(
                        state,
                        trade,
                        timestamp_ns,
                    )

        local_update = local_map.get(
            timestamp_ns
        )
        if local_update is not None:
            current_local = local_update
            adapter.observe(
                timestamp_ns=timestamp_ns,
                best_bid=local_update.best_bid,
                best_ask=local_update.best_ask,
                ambiguous=local_update.ambiguous,
                source_version="PMXT_V3",
            )

        complement_update = complement_map.get(
            timestamp_ns
        )
        if complement_update is not None:
            current_complement = (
                complement_update
            )

        local_mid = (
            current_local.midpoint
            if current_local is not None
            else None
        )
        complement_mid = (
            current_complement.midpoint
            if current_complement is not None
            else None
        )
        external_fv = (
            1.0 - complement_mid
            if complement_mid is not None
            else None
        )
        external_timestamp_ns = (
            current_complement.timestamp_ns
            if (
                external_fv is not None
                and current_complement is not None
            )
            else None
        )
        hazard = hazard_at(timestamp_ns)

        observation = BookObservation(
            market_id=local_token,
            timestamp_ns=timestamp_ns,
            best_bid=(
                current_local.best_bid
                if current_local is not None
                else None
            ),
            best_ask=(
                current_local.best_ask
                if current_local is not None
                else None
            ),
            external_fv=external_fv,
            external_fv_timestamp_ns=(
                external_timestamp_ns
            ),
            update_hazard=hazard,
            category="EXACT",
        )

        for state in states:
            desired = build_quote(
                observation,
                state.policy,
                inventory=state.inventory,
                base_size=1.0,
            )
            state.quotes += 1
            state.active_quotes += int(
                desired.bid is not None
                or desired.ask is not None
            )

            if not state.initialized:
                state.current_quote = desired
                state.current_hazard = hazard
                state.initialized = True
                continue

            if state.latency_ms == 0:
                state.current_quote = desired
                state.current_hazard = hazard
                state.pending_quote = None
                state.pending_hazard = None
                state.pending_effective_ns = None
                continue

            if (
                state.current_quote is not None
                and quote_equivalent(
                    desired,
                    state.current_quote,
                )
            ):
                if state.pending_quote is not None:
                    state.pending_quote = desired
                    state.pending_hazard = hazard
                continue

            state.pending_quote = desired
            state.pending_hazard = hazard
            if state.pending_effective_ns is None:
                state.pending_effective_ns = (
                    timestamp_ns
                    + state.latency_ms * 1_000_000
                )

        if (
            local_mid is not None
            and external_fv is not None
        ):
            bucket = (
                (timestamp_ns - START_NS)
                // FV_SAMPLE_NS
            )
            if bucket != last_fv_bucket:
                fv_samples.append(
                    (
                        timestamp_ns,
                        local_mid,
                        external_fv,
                    )
                )
                last_fv_bucket = int(bucket)

    all_fills: list[dict[str, Any]] = []
    markout_agg: defaultdict[
        tuple[str, int],
        list[
            tuple[
                float | None,
                float | None,
            ]
        ],
    ] = defaultdict(list)
    toxicity_agg: defaultdict[
        tuple[str, str],
        list[float],
    ] = defaultdict(list)
    policy_rows: list[dict[str, Any]] = []

    terminal_local = (
        local_groups[-1].midpoint
        if local_groups
        else None
    )
    terminal_complement = (
        complement_groups[-1].midpoint
        if complement_groups
        else None
    )
    terminal_external = (
        1.0 - terminal_complement
        if terminal_complement is not None
        else None
    )

    for state in states:
        assert state.fills is not None
        gross_values: list[float] = []
        local_300: list[float] = []
        external_300: list[float] = []

        for fill in state.fills:
            price = float(fill["fill_price"])
            side = str(fill["side"])
            fill_timestamp_ns = int(
                fill["timestamp_ns"]
            )
            gross_values.append(
                float(
                    fill[
                        "gross_spread_capture"
                    ]
                )
            )

            for horizon in MARKOUT_HORIZONS_S:
                target = (
                    fill_timestamp_ns
                    + int(horizon)
                    * 1_000_000_000
                )
                local_group = asof_group(
                    local_groups,
                    local_times,
                    target,
                )
                complement_group = asof_group(
                    complement_groups,
                    complement_times,
                    target,
                )
                local_reference = (
                    local_group.midpoint
                    if local_group is not None
                    else None
                )
                complement_reference = (
                    complement_group.midpoint
                    if complement_group
                    is not None
                    else None
                )
                external_reference = (
                    1.0 - complement_reference
                    if complement_reference
                    is not None
                    else None
                )
                local_markout = markout_value(
                    side,
                    price,
                    local_reference,
                )
                external_markout = markout_value(
                    side,
                    price,
                    external_reference,
                )
                fill[
                    f"local_markout_{horizon}s"
                ] = local_markout
                fill[
                    f"external_markout_{horizon}s"
                ] = external_markout
                markout_agg[
                    (
                        state.scenario_id,
                        int(horizon),
                    )
                ].append(
                    (
                        local_markout,
                        external_markout,
                    )
                )
                if horizon == 300:
                    if local_markout is not None:
                        local_300.append(
                            local_markout
                        )
                    if (
                        external_markout
                        is not None
                    ):
                        external_300.append(
                            external_markout
                        )

            if fill.get(
                "toxicity_score"
            ) is not None:
                value = fill.get(
                    "external_markout_300s"
                )
                toxicity_agg[
                    (
                        state.scenario_id,
                        str(
                            fill[
                                "toxicity_bucket"
                            ]
                        ),
                    )
                ].append(
                    float(value)
                    if value is not None
                    else math.nan
                )

            fill.update(
                {
                    "sig_market_id": str(
                        record["sig_market_id"]
                    ),
                    "sig_exchange_id": str(
                        record[
                            "sig_exchange_id"
                        ]
                    ),
                    "sig_market_title": str(
                        record[
                            "sig_market_title"
                        ]
                    ),
                    "condition_id": str(
                        record["condition_id"]
                    ),
                    "local_token_id": (
                        local_token
                    ),
                    "complement_token_id": (
                        complement_token
                    ),
                }
            )
            all_fills.append(fill)

        gross_terminal_local = (
            state.cash_flow
            + state.inventory
            * terminal_local
            if terminal_local is not None
            else None
        )
        gross_terminal_external = (
            state.cash_flow
            + state.inventory
            * terminal_external
            if terminal_external is not None
            else None
        )

        policy_rows.append(
            {
                "sig_market_id": str(
                    record["sig_market_id"]
                ),
                "sig_exchange_id": str(
                    record["sig_exchange_id"]
                ),
                "sig_market_title": str(
                    record["sig_market_title"]
                ),
                "condition_id": str(
                    record["condition_id"]
                ),
                "local_token_id": local_token,
                "complement_token_id": (
                    complement_token
                ),
                "scenario_id": (
                    state.scenario_id
                ),
                "scenario_family": (
                    state.family
                ),
                "policy_id": (
                    state.policy.policy_id
                ),
                "fill_assumption": (
                    state.assumption.value
                ),
                "reaction_delay_ms": (
                    state.latency_ms
                ),
                "quotes": state.quotes,
                "active_quotes": (
                    state.active_quotes
                ),
                "active_fraction": (
                    state.active_quotes
                    / state.quotes
                    if state.quotes
                    else 0.0
                ),
                "fills": len(state.fills),
                "mean_gross_spread_capture": (
                    mean(gross_values)
                ),
                "mean_local_markout_5m": (
                    mean(local_300)
                ),
                "mean_external_markout_5m": (
                    mean(external_300)
                ),
                "final_inventory": (
                    state.inventory
                ),
                "gross_cash_flow": (
                    state.cash_flow
                ),
                "terminal_local_mid": (
                    terminal_local
                ),
                "terminal_external_fv": (
                    terminal_external
                ),
                "gross_terminal_local_pnl": (
                    gross_terminal_local
                ),
                "gross_terminal_external_pnl": (
                    gross_terminal_external
                ),
                "total_fee_cost": None,
                "terminal_unwind_cost": None,
                "net_terminal_local_pnl": None,
                "net_terminal_external_pnl": None,
            }
        )

    markout_rows: list[dict[str, Any]] = []
    policy_lookup = {
        row["scenario_id"]: row
        for row in policy_rows
    }
    for (
        scenario_id,
        horizon,
    ), values in markout_agg.items():
        exemplar = policy_lookup[scenario_id]
        markout_rows.append(
            {
                "sig_market_id": str(
                    record["sig_market_id"]
                ),
                "scenario_id": scenario_id,
                "policy_id": exemplar[
                    "policy_id"
                ],
                "fill_assumption": exemplar[
                    "fill_assumption"
                ],
                "reaction_delay_ms": (
                    exemplar[
                        "reaction_delay_ms"
                    ]
                ),
                "horizon_s": horizon,
                "fills": len(values),
                "mean_local_markout": mean(
                    [value[0] for value in values]
                ),
                "mean_external_markout": mean(
                    [value[1] for value in values]
                ),
            }
        )

    toxicity_rows: list[
        dict[str, Any]
    ] = []
    for (
        scenario_id,
        bucket,
    ), values in toxicity_agg.items():
        toxicity_rows.append(
            {
                "sig_market_id": str(
                    record["sig_market_id"]
                ),
                "scenario_id": scenario_id,
                "bucket": bucket,
                "fills": len(values),
                "mean_external_markout_5m": (
                    mean(values)
                ),
            }
        )

    fv_rows: list[dict[str, Any]] = []
    for horizon in MARKOUT_HORIZONS_S:
        gap_now: list[float] = []
        local_moves: list[float] = []
        external_moves: list[float] = []
        future_gaps: list[float] = []

        for (
            timestamp_ns,
            local_now,
            external_now,
        ) in fv_samples:
            target = (
                timestamp_ns
                + int(horizon)
                * 1_000_000_000
            )
            local_group = asof_group(
                local_groups,
                local_times,
                target,
            )
            complement_group = asof_group(
                complement_groups,
                complement_times,
                target,
            )
            local_future = (
                local_group.midpoint
                if local_group is not None
                else None
            )
            complement_future = (
                complement_group.midpoint
                if complement_group
                is not None
                else None
            )
            external_future = (
                1.0 - complement_future
                if complement_future is not None
                else None
            )
            if (
                local_future is None
                or external_future is None
            ):
                continue
            gap_now.append(
                local_now - external_now
            )
            local_moves.append(
                local_future - local_now
            )
            external_moves.append(
                external_future
                - external_now
            )
            future_gaps.append(
                local_future
                - external_future
            )

        fv_rows.append(
            {
                "sig_market_id": str(
                    record["sig_market_id"]
                ),
                "horizon_s": int(horizon),
                "samples": len(gap_now),
                "mean_gap_t": mean(gap_now),
                "mean_local_move": mean(
                    local_moves
                ),
                "mean_external_move": mean(
                    external_moves
                ),
                "mean_gap_h": mean(
                    future_gaps
                ),
            }
        )

    scored: list[
        tuple[int, float, float]
    ] = []
    for grid, (
        score,
        features,
    ) in sorted(hazard_cache.items()):
        if (
            score is None
            or features is None
        ):
            continue
        left = bisect_right(
            genuine,
            grid,
        )
        right = bisect_right(
            genuine,
            grid
            + 300_000_000_000,
        )
        scored.append(
            (
                grid,
                float(score),
                float(right > left),
            )
        )

    transfer_row: dict[str, Any] = {
        "sig_market_id": str(
            record["sig_market_id"]
        ),
        "local_token_id": local_token,
        "grid_evaluation": (
            "QUOTE_EVENT_15S_BUCKETS"
        ),
        "scored_rows": len(scored),
        "genuine_changes": len(genuine),
    }

    if scored:
        scores = np.asarray(
            [item[1] for item in scored],
            float,
        )
        targets = np.asarray(
            [item[2] for item in scored],
            float,
        )
        transfer_row.update(
            {
                "brier": float(
                    np.mean(
                        (scores - targets) ** 2
                    )
                ),
                "mean_score": float(
                    np.mean(scores)
                ),
                "update_rate": float(
                    np.mean(targets)
                ),
                "mean_score_when_update": (
                    float(
                        np.mean(
                            scores[
                                targets == 1
                            ]
                        )
                    )
                    if np.any(
                        targets == 1
                    )
                    else None
                ),
                "mean_score_when_no_update": (
                    float(
                        np.mean(
                            scores[
                                targets == 0
                            ]
                        )
                    )
                    if np.any(
                        targets == 0
                    )
                    else None
                ),
            }
        )
        order = np.argsort(scores)
        quartile = max(
            1,
            len(order) // 4,
        )
        transfer_row[
            "update_rate_low_score_quartile"
        ] = float(
            np.mean(
                targets[
                    order[:quartile]
                ]
            )
        )
        transfer_row[
            "update_rate_high_score_quartile"
        ] = float(
            np.mean(
                targets[
                    order[-quartile:]
                ]
            )
        )

    return {
        "policy_rows": policy_rows,
        "fills": all_fills,
        "markouts": markout_rows,
        "toxicity": toxicity_rows,
        "fv": fv_rows,
        "transfer": transfer_row,
        "hazard_pairs": [
            (item[1], item[2])
            for item in scored
        ],
        "local_bbo_groups": len(
            local_groups
        ),
        "complement_bbo_groups": len(
            complement_groups
        ),
        "local_trades": len(trades),
    }


def aggregate_latency(
    policy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: defaultdict[
        tuple[str, str, str, int],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in policy_rows:
        key = (
            str(row["scenario_family"]),
            str(row["policy_id"]),
            str(row["fill_assumption"]),
            int(row["reaction_delay_ms"]),
        )
        groups[key].append(row)

    out: list[dict[str, Any]] = []
    for key, rows in groups.items():
        (
            family,
            policy,
            fill_assumption,
            latency,
        ) = key
        out.append(
            {
                "scenario_family": family,
                "policy_id": policy,
                "fill_assumption": (
                    fill_assumption
                ),
                "reaction_delay_ms": latency,
                "markets": len(rows),
                "fills": sum(
                    int(row["fills"])
                    for row in rows
                ),
                "mean_active_fraction": mean(
                    [
                        row[
                            "active_fraction"
                        ]
                        for row in rows
                    ]
                ),
                "mean_external_markout_5m": mean(
                    [
                        row[
                            "mean_external_markout_5m"
                        ]
                        for row in rows
                    ]
                ),
                "mean_local_markout_5m": mean(
                    [
                        row[
                            "mean_local_markout_5m"
                        ]
                        for row in rows
                    ]
                ),
            }
        )

    return sorted(
        out,
        key=lambda row: (
            row["scenario_family"],
            row["policy_id"],
            row["fill_assumption"],
            row["reaction_delay_ms"],
        ),
    )


def aggregate_toxicity(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: defaultdict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in rows:
        groups[
            (
                str(row["scenario_id"]),
                str(row["bucket"]),
            )
        ].append(row)

    out: list[dict[str, Any]] = []
    for (
        scenario_id,
        bucket,
    ), items in groups.items():
        out.append(
            {
                "scenario_id": scenario_id,
                "bucket": bucket,
                "markets": len(
                    {
                        str(
                            item[
                                "sig_market_id"
                            ]
                        )
                        for item in items
                    }
                ),
                "fills": sum(
                    int(item["fills"])
                    for item in items
                ),
                "mean_external_markout_5m": mean(
                    [
                        item[
                            "mean_external_markout_5m"
                        ]
                        for item in items
                    ]
                ),
            }
        )
    return out


def transfer_summary(
    rows: list[dict[str, Any]],
    pairs: list[tuple[float, float]],
    artifact_hashes: dict[str, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "regime": "PRE_ELECTION",
        "candidate_id": (
            "PRE_ELECTION|clock|"
            "UPDATE_HAZARD"
        ),
        "feature_definition": (
            "existing "
            "IncrementalHazard005FState"
        ),
        "grid_evaluation": (
            "QUOTE_EVENT_15S_BUCKETS"
        ),
        "markets": len(rows),
        "scored_rows": sum(
            int(row.get("scored_rows", 0))
            for row in rows
        ),
        "artifact_hashes": artifact_hashes,
        "production_threshold_selected": False,
        "status": (
            "SCORED"
            if pairs
            else "NOT_RUN"
        ),
    }

    if pairs:
        score = np.asarray(
            [pair[0] for pair in pairs],
            float,
        )
        target = np.asarray(
            [pair[1] for pair in pairs],
            float,
        )
        result["brier"] = float(
            np.mean(
                (score - target) ** 2
            )
        )
        result["update_rate"] = float(
            np.mean(target)
        )
        result["mean_score"] = float(
            np.mean(score)
        )
        order = np.argsort(score)
        quartile = max(
            1,
            len(order) // 4,
        )
        result[
            "update_rate_low_score_quartile"
        ] = float(
            np.mean(
                target[
                    order[:quartile]
                ]
            )
        )
        result[
            "update_rate_high_score_quartile"
        ] = float(
            np.mean(
                target[
                    order[-quartile:]
                ]
            )
        )

    return result
