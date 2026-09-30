from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.mm_replay_001 import (
    CANCEL_LATENCIES_MS,
    MARKOUT_HORIZONS_S,
    BookDelta,
    BookObservation,
    ConservativeTradeFillModel,
    DatasetBinding,
    FillAssumption,
    Frozen005FTransferAdapter,
    InputContractError,
    QueueAwareFillModel,
    Side,
    SimulatedFill,
    TopOfBookReconstructor,
    binary_cara_reservation,
    build_quote,
    default_policies,
    expected_output_schema,
    fair_value_convergence,
    genuine_005f_change_times,
    group_bbo_for_005f,
    inspect_input,
    mapped_external_fv,
    markout,
    quote_survives_reaction_delay,
    replay_market,
)


def bound(**overrides: object) -> DatasetBinding:
    raw: dict[str, object] = {
        "status": "BOUND",
        "kaggle_dataset_slug": "owner/current-data003-ob",
        "dataset_version": "1",
        "source": "current Cup historical order-book acquisition",
        "schema_version": "v1",
        "relation_to_data003": "DATA-003-linked current Cup mapped universe",
        "acquisition_version": "capture-v1",
        "root_hint": "/kaggle/input",
        "expected_markets": 237,
        "expected_tokens": None,
        "expected_time_start": None,
        "expected_time_end": None,
        "file_hashes": {},
        "column_map": {},
        "book_encoding": "SNAPSHOT",
    }
    raw.update(overrides)
    return DatasetBinding.from_json(raw)


def test_waiting_manifest_fails_closed() -> None:
    binding = bound(status="WAITING_FOR_DATA")
    with pytest.raises(InputContractError, match="WAITING_FOR_DATA"):
        binding.require_bound()


def test_relation_to_data003_is_mandatory() -> None:
    binding = bound(relation_to_data003="some order book")
    with pytest.raises(InputContractError, match="DATA-003"):
        binding.require_bound()


def test_input_audit_detects_snapshot_bbo_and_trade_evidence(tmp_path: Path) -> None:
    table = pa.table(
        {
            "market_id": ["m1"],
            "timestamp_ns": [1_000_000_000],
            "best_bid": [0.49],
            "best_ask": [0.51],
            "trade_price": [0.49],
            "trade_size": [1.0],
            "aggressor_side": ["SELL"],
            "external_fv": [0.50],
        }
    )
    pq.write_table(table, tmp_path / "book.parquet")
    audit = inspect_input(tmp_path, bound())
    assert audit.passed
    assert audit.capabilities["snapshot_bbo"]
    assert audit.capabilities["observed_trade_fill_evidence"]
    assert audit.capabilities["external_fair_value"]


def test_delta_reconstructor_rejects_duplicate_and_reordered_events() -> None:
    reconstructor = TopOfBookReconstructor()
    first = reconstructor.apply(
        BookDelta("m1", 1, "BID", 0.49, 2.0, "SET", "e1")
    )
    assert first.best_bid == pytest.approx(0.49)
    second = reconstructor.apply(
        BookDelta("m1", 1, "ASK", 0.51, 3.0, "SET", "e2")
    )
    assert second.best_ask == pytest.approx(0.51)
    with pytest.raises(InputContractError, match="duplicate"):
        reconstructor.apply(
            BookDelta("m1", 2, "ASK", 0.52, 1.0, "SET", "e2")
        )
    with pytest.raises(InputContractError, match="regression"):
        reconstructor.apply(
            BookDelta("m1", 0, "ASK", 0.52, 1.0, "SET", "e3")
        )


def test_mapping_exact_complement_and_near_gate() -> None:
    assert mapped_external_fv(
        best_bid=0.39,
        best_ask=0.41,
        mapping_class="EXACT",
        mapping_direction="SAME",
    ) == pytest.approx(0.40)
    assert mapped_external_fv(
        best_bid=0.39,
        best_ask=0.41,
        mapping_class="EXACT",
        mapping_direction="COMPLEMENT",
    ) == pytest.approx(0.60)
    with pytest.raises(InputContractError, match="NEAR"):
        mapped_external_fv(
            best_bid=0.39,
            best_ask=0.41,
            mapping_class="NEAR",
            mapping_direction="SAME",
        )


def test_binary_cara_moves_reservation_against_inventory() -> None:
    neutral = binary_cara_reservation(0.50, 0.0, 0.02)
    long = binary_cara_reservation(0.50, 10.0, 0.02)
    short = binary_cara_reservation(0.50, -10.0, 0.02)
    assert long < neutral < short


def observation(
    *,
    timestamp_ns: int = 1_000_000_000,
    trade_price: float | None = None,
    trade_size: float | None = None,
    aggressor_side: Side | None = None,
    hazard: float | None = None,
) -> BookObservation:
    return BookObservation(
        market_id="m1",
        timestamp_ns=timestamp_ns,
        best_bid=0.49,
        best_ask=0.51,
        external_fv=0.50,
        external_fv_timestamp_ns=timestamp_ns,
        trade_price=trade_price,
        trade_size=trade_size,
        aggressor_side=aggressor_side,
        update_hazard=hazard,
    )


def test_external_fv_staleness_withdraws_quote() -> None:
    obs = observation(timestamp_ns=2_100_000_000)
    obs = replace(obs, external_fv_timestamp_ns=1_000_000_000)
    quote = build_quote(obs, default_policies()[1])
    assert quote.bid is None and quote.ask is None
    assert quote.reason == "external_fv_stale"


def test_toxicity_policy_fails_closed_without_score() -> None:
    quote = build_quote(observation(), default_policies()[3])
    assert quote.bid is None and quote.ask is None
    assert quote.reason == "toxicity_unavailable"


def test_touch_without_trade_never_fills() -> None:
    quote = build_quote(observation(), default_policies()[1])
    event = observation(timestamp_ns=2_000_000_000)
    assert ConservativeTradeFillModel().fills(quote, event) == ()


def test_observed_aggressive_trade_can_fill() -> None:
    quote = build_quote(observation(), default_policies()[1])
    assert quote.bid is not None
    event = observation(
        timestamp_ns=2_000_000_000,
        trade_price=quote.bid,
        trade_size=2.0,
        aggressor_side=Side.SELL,
    )
    fills = ConservativeTradeFillModel().fills(quote, event)
    assert len(fills) == 1
    assert fills[0].assumption is FillAssumption.OBSERVED_TRADE


def test_queue_model_requires_depletion() -> None:
    quote = build_quote(observation(), default_policies()[1])
    assert quote.bid is not None
    event = BookObservation(
        market_id="m1",
        timestamp_ns=2_000_000_000,
        best_bid=0.49,
        best_ask=0.51,
        trade_price=quote.bid,
        trade_size=2.0,
        aggressor_side=Side.SELL,
        queue_ahead_bid=3.0,
    )
    assert QueueAwareFillModel().fills(quote, event) == ()


def test_markout_signs_are_side_correct() -> None:
    buy = SimulatedFill(
        "m1",
        1,
        "p",
        Side.BUY,
        0.45,
        1.0,
        FillAssumption.OBSERVED_TRADE,
    )
    sell = SimulatedFill(
        "m1",
        1,
        "p",
        Side.SELL,
        0.55,
        1.0,
        FillAssumption.OBSERVED_TRADE,
    )
    assert markout(buy, horizon_s=5, future_fv=0.50).value == pytest.approx(0.05)
    assert markout(sell, horizon_s=5, future_fv=0.50).value == pytest.approx(0.05)


def test_latency_boundary_is_explicit() -> None:
    assert quote_survives_reaction_delay(
        quote_timestamp_ns=0,
        invalidation_timestamp_ns=1_000_000_000,
        event_timestamp_ns=1_049_000_000,
        reaction_delay_ms=50,
    )
    assert not quote_survives_reaction_delay(
        quote_timestamp_ns=0,
        invalidation_timestamp_ns=1_000_000_000,
        event_timestamp_ns=1_050_000_000,
        reaction_delay_ms=50,
    )


def test_replay_latency_changes_stale_quote_fill_exposure() -> None:
    policy = default_policies()[1]
    observations = [
        BookObservation(
            market_id="m1",
            timestamp_ns=0,
            best_bid=0.49,
            best_ask=0.51,
            external_fv=0.50,
            external_fv_timestamp_ns=0,
        ),
        BookObservation(
            market_id="m1",
            timestamp_ns=1_000_000_000,
            best_bid=0.44,
            best_ask=0.46,
            external_fv=0.45,
            external_fv_timestamp_ns=1_000_000_000,
        ),
        BookObservation(
            market_id="m1",
            timestamp_ns=1_050_000_000,
            best_bid=0.44,
            best_ask=0.46,
            external_fv=0.45,
            external_fv_timestamp_ns=1_050_000_000,
            trade_price=0.495,
            trade_size=1.0,
            aggressor_side=Side.SELL,
        ),
    ]
    _, instant = replay_market(
        observations,
        policy=policy,
        fill_model=ConservativeTradeFillModel(),
        reaction_delay_ms=0,
    )
    slow_results, slow = replay_market(
        observations,
        policy=policy,
        fill_model=ConservativeTradeFillModel(),
        reaction_delay_ms=100,
    )
    assert instant.fills == 0
    assert slow.fills == 1
    assert slow_results[0].reaction_delay_ms == 100


def test_same_timestamp_conflicting_bbo_is_ambiguous_for_005f() -> None:
    rows = [
        BookObservation("m1", 0, 0.40, 0.60),
        BookObservation("m1", 5_000_000_000, 0.41, 0.59),
        BookObservation("m1", 5_000_000_000, 0.42, 0.58),
        BookObservation("m1", 10_000_000_000, 0.43, 0.57),
    ]
    grouped = group_bbo_for_005f(rows)
    assert len(grouped) == 3
    assert grouped[1].ambiguous
    assert genuine_005f_change_times(grouped) == ()


def test_005f_adapter_uses_existing_exact_state() -> None:
    adapter = Frozen005FTransferAdapter(scope_id="token", grid_origin_ns=0)
    adapter.observe(timestamp_ns=0, best_bid=0.40, best_ask=0.60)
    adapter.observe(
        timestamp_ns=5_000_000_000,
        best_bid=0.41,
        best_ask=0.59,
    )
    adapter.observe(
        timestamp_ns=10_000_000_000,
        best_bid=0.41,
        best_ask=0.59,
    )
    features = adapter.features(query_timestamp_ns=15_000_000_000)
    assert features is not None
    assert features["genuine_15"] == pytest.approx(1.0)
    assert features["genuine_60"] == pytest.approx(1.0)
    assert features["genuine_age_s"] == pytest.approx(10.0)


def test_replay_accounting_is_separate_from_markouts_and_explicit_costs() -> None:
    policy = default_policies()[1]
    observations = [
        BookObservation(
            market_id="m1",
            timestamp_ns=0,
            best_bid=0.49,
            best_ask=0.51,
            external_fv=0.50,
            external_fv_timestamp_ns=0,
        ),
        BookObservation(
            market_id="m1",
            timestamp_ns=1_000_000_000,
            best_bid=0.49,
            best_ask=0.51,
            external_fv=0.50,
            external_fv_timestamp_ns=1_000_000_000,
            trade_price=0.495,
            trade_size=1.0,
            aggressor_side=Side.SELL,
        ),
        BookObservation(
            market_id="m1",
            timestamp_ns=301_000_000_000,
            best_bid=0.51,
            best_ask=0.53,
            external_fv=0.52,
            external_fv_timestamp_ns=301_000_000_000,
        ),
    ]
    _, gross_only = replay_market(
        observations,
        policy=policy,
        fill_model=ConservativeTradeFillModel(),
    )
    assert gross_only.net_terminal_local_pnl is None
    assert gross_only.net_terminal_external_pnl is None

    _, costed = replay_market(
        observations,
        policy=policy,
        fill_model=ConservativeTradeFillModel(),
        fee_per_share=0.001,
        unwind_cost_per_share=0.002,
    )
    assert costed.fills == 1
    assert costed.final_inventory == pytest.approx(1.0)
    assert costed.gross_cash_flow == pytest.approx(-0.495)
    assert costed.gross_terminal_local_pnl == pytest.approx(0.025)
    assert costed.gross_terminal_external_pnl == pytest.approx(0.025)
    assert costed.total_fee_cost == pytest.approx(0.001)
    assert costed.terminal_unwind_cost == pytest.approx(0.002)
    assert costed.net_terminal_local_pnl == pytest.approx(0.022)


def test_replay_and_convergence_are_available_without_live_orders() -> None:
    policy = default_policies()[1]
    observations = [
        observation(timestamp_ns=0),
        observation(
            timestamp_ns=1_000_000_000,
            trade_price=0.495,
            trade_size=1.0,
            aggressor_side=Side.SELL,
        ),
        BookObservation(
            market_id="m1",
            timestamp_ns=301_000_000_000,
            best_bid=0.50,
            best_ask=0.52,
            external_fv=0.51,
            external_fv_timestamp_ns=301_000_000_000,
        ),
    ]
    results, summary = replay_market(
        observations,
        policy=policy,
        fill_model=ConservativeTradeFillModel(),
    )
    assert summary.quotes == 3
    assert isinstance(results, tuple)
    assert fair_value_convergence(observations)


def test_output_contract_declares_required_axes_and_no_live_orders() -> None:
    schema = expected_output_schema()
    assert schema["markout_horizons_s"] == list(MARKOUT_HORIZONS_S)
    assert schema["cancel_latency_ms"] == list(CANCEL_LATENCIES_MS)
    assert schema["real_sig_orders"] is False
    assert "FINAL_REPORT.md" in schema["outputs"]
    assert "MM_CANDIDATE_CONFIG.json" in schema["optional_outputs"]
