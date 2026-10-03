from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predictions_cup.config import AppSettings
from predictions_cup.execution.models import ExecutionMode
from predictions_cup.external.polymarket.orderbook import OrderBookStore
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.risk.core import RiskContext, evaluate_risk
from predictions_cup.risk.swing import (
    PolymarketOrderBookSwingMarkProvider,
    SwingCrosswalk,
    SwingMappedMarket,
    SwingPmMark,
    SwingRiskControl,
    evaluate_swing_cap,
    load_swing_crosswalk,
)
from predictions_cup.runtime.models import (
    OrderAction,
    OutcomeSide,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity, StrategyFamily

EXCHANGE_ID = "36"
MARKET_ID = "m1"
TOURNAMENT_ID = "t1"
PM_CONDITION_ID = "pm-condition-1"
PM_YES_TOKEN_ID = "pm-yes-1"


class _MarkProvider:
    def __init__(self, mark: SwingPmMark | None) -> None:
        self.mark = mark

    def mark_for(self, token_id: str) -> SwingPmMark | None:
        assert token_id == PM_YES_TOKEN_ID
        return self.mark


class _MultiMarkProvider:
    def __init__(self, marks: dict[str, SwingPmMark]) -> None:
        self.marks = marks

    def mark_for(self, token_id: str) -> SwingPmMark | None:
        return self.marks.get(token_id)


def _control(
    *,
    mark: SwingPmMark | None = None,
    markets: tuple[SwingMappedMarket, ...] | None = None,
    verified: bool = True,
    max_loss: float = 2_000.0,
) -> SwingRiskControl:
    crosswalk = SwingCrosswalk(
        verified=verified,
        markets=(
            _market_mapping(),
        )
        if markets is None
        else markets,
    )
    return SwingRiskControl(
        shock_points=5.0,
        max_loss=max_loss,
        max_pm_mark_age_ns=35_000_000_000,
        crosswalk=crosswalk,
        mark_provider=_MarkProvider(mark),
    )


def _market_mapping(
    *,
    exchange_id: str = EXCHANGE_ID,
    market_id: str = MARKET_ID,
    party_sign: int = 1,
    pm_condition_id: str = PM_CONDITION_ID,
    pm_yes_token_id: str = PM_YES_TOKEN_ID,
) -> SwingMappedMarket:
    return SwingMappedMarket(
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id=TOURNAMENT_ID,
        race_id=f"pm-event:race-{exchange_id}",
        chamber="House",
        party_sign=party_sign,
        pm_condition_id=pm_condition_id,
        pm_yes_token_id=pm_yes_token_id,
        sig_yes_is_pm_yes=True,
    )


def _mark(*, age_seconds: float = 0.0) -> SwingPmMark:
    return SwingPmMark(
        pm_condition_id=PM_CONDITION_ID,
        midpoint=0.5,
        observed_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def _snapshot(
    *,
    positions: tuple[RuntimePosition, ...] = (),
    orders: tuple[RuntimeOrderState, ...] = (),
    exchange_id: str = EXCHANGE_ID,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=MARKET_ID,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id=TOURNAMENT_ID,
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(
            positions=positions,
            orders=orders,
            account_trusted=True,
        ),
        observation_monotonic_ns=1_000_000_000,
    )


def _opportunity(
    *,
    exchange_id: str = EXCHANGE_ID,
    market_id: str = MARKET_ID,
    quantity: int = 1,
    action: OrderAction = OrderAction.BUY,
) -> Opportunity:
    return Opportunity(
        family=StrategyFamily.FV_TAKE,
        strategy_id="swing-test",
        legs=(
            CandidateLeg(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id=TOURNAMENT_ID,
                outcome_side=OutcomeSide.YES,
                action=action,
                quantity=quantity,
                limit_price_ticks=100,
            ),
        ),
        gross_edge=0.02,
        fair_value=0.5,
        decision_observation_ns=1_000_000_000,
    )


def _context(control: SwingRiskControl | None) -> RiskContext:
    return RiskContext(
        mode=ExecutionMode.SHADOW,
        kill_switch=False,
        limits=None,
        max_state_age_ns=1_000_000_000,
        swing_control=control,
    )


def test_swing_cap_is_off_by_default_and_preserves_risk_result_shape() -> None:
    settings = AppSettings()
    assert settings.risk_swing_cap_enabled is False
    assert settings.risk_swing_shock_points == 5.0
    assert settings.risk_swing_max_loss == 2_000.0
    assert settings.risk_swing_max_pm_mark_age_ms == 35_000
    assert "risk_swing_cap_enabled" not in settings.diagnostic_fields()
    assert "swing_control" not in repr(_context(None))

    snapshot = _snapshot(
        positions=(RuntimePosition(EXCHANGE_ID, MARKET_ID, TOURNAMENT_ID, 16_500, 16_500),),
    )
    decision = evaluate_risk(_opportunity(), snapshot, _context(None))
    assert decision.approved is True
    assert decision.reason == "approved"
    assert decision.swing_diagnostics is None


def test_only_accepted_direct_party_mappings_enter_the_swing_crosswalk() -> None:
    root = Path(__file__).resolve().parents[1]
    mapping_path = root / "data/mappings/sig_polymarket_2026.json"
    crosswalk = load_swing_crosswalk(
        load_document(mapping_path),
        mapping_path=mapping_path,
    )

    assert crosswalk.verified is True
    assert len(crosswalk.markets) == 137
    house = crosswalk.market_for("840")
    assert house is not None
    assert house.market_id == "151"
    assert house.race_id == "pm-event:32225"
    assert house.chamber == "House"
    assert house.party_sign == 1
    assert house.pm_condition_id == (
        "0xd5d9fc47718bd553592d126b1fa5e87183d27f3936975b0c04cc0f2dec1f1bb4"
    )
    assert house.pm_yes_token_id == (
        "83247781037352156539108067944461291821683755894607244160607042790356561625563"
    )
    # The accepted file's independent and derived rows lack a supported party
    # YES midpoint contract, so they remain conservative/unmapped to this factor.
    assert crosswalk.market_for("969") is None
    assert crosswalk.market_for("942") is None


def test_verified_acceptance_allows_a_filtered_runtime_mapping() -> None:
    root = Path(__file__).resolve().parents[1]
    mapping_path = root / "data/mappings/sig_polymarket_2026.json"
    document = load_document(mapping_path)
    filtered = document.model_copy(
        update={"records": (document.mapping_for_sig_exchange("840"),)}
    )

    crosswalk = load_swing_crosswalk(filtered, mapping_path=mapping_path)

    assert crosswalk.verified is True
    assert crosswalk.market_for("840") is not None
    assert crosswalk.market_for("969") is None


def test_runtime_mapping_mismatch_fails_closed_and_logs_the_clause(caplog) -> None:
    root = Path(__file__).resolve().parents[1]
    mapping_path = root / "data/mappings/sig_polymarket_2026.json"
    document = load_document(mapping_path)
    record = document.mapping_for_sig_exchange("840").model_copy(
        update={"sig_market_title": "Will the Democratic Party win a different House?"}
    )
    mismatched = document.model_copy(update={"records": (record,)})

    with caplog.at_level("WARNING", logger="predictions_cup.risk.swing"):
        crosswalk = load_swing_crosswalk(mismatched, mapping_path=mapping_path)

    assert crosswalk.verified is False
    failures = [record for record in caplog.records if "verification failed" in record.message]
    assert len(failures) == 1
    assert "clause=runtime_mapping_not_accepted_subset" in failures[0].message


def test_unverified_acceptance_artifact_fails_closed_and_logs_the_clause(tmp_path, caplog) -> None:
    root = Path(__file__).resolve().parents[1]
    original_path = root / "data/mappings/sig_polymarket_2026.json"
    mapping_path = tmp_path / original_path.name
    acceptance_path = tmp_path / "sig_polymarket_2026_acceptance.json"
    mapping_path.write_bytes(original_path.read_bytes())
    acceptance = json.loads(
        original_path.with_name("sig_polymarket_2026_acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    acceptance["all_records_verified"] = False
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with caplog.at_level("WARNING", logger="predictions_cup.risk.swing"):
        crosswalk = load_swing_crosswalk(
            load_document(original_path),
            mapping_path=mapping_path,
        )

    assert crosswalk.verified is False
    failures = [record for record in caplog.records if "verification failed" in record.message]
    assert len(failures) == 1
    assert "clause=all_records_verified_false" in failures[0].message


def test_order_book_provider_returns_the_yes_token_midpoint_and_condition_id() -> None:
    observed = datetime.now(UTC)
    books = OrderBookStore()
    books.apply_full_snapshot(
        {
            "market": PM_CONDITION_ID,
            "asset_id": PM_YES_TOKEN_ID,
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.51", "size": "10"}],
        },
        observed,
    )

    mark = PolymarketOrderBookSwingMarkProvider(books).mark_for(PM_YES_TOKEN_ID)
    assert mark == SwingPmMark(PM_CONDITION_ID, 0.5, observed)


def test_rejects_breach_after_positions_orders_reservations_and_candidate() -> None:
    control = _control(mark=_mark())
    snapshot = _snapshot(
        positions=(RuntimePosition(EXCHANGE_ID, MARKET_ID, TOURNAMENT_ID, 10_000, 10_000),),
        orders=(
            RuntimeOrderState(
                "open",
                EXCHANGE_ID,
                MARKET_ID,
                TOURNAMENT_ID,
                reserved_exposure=4_000,
                open=True,
                uncertain=False,
                signed_quantity=4_000,
            ),
            RuntimeOrderState(
                "reservation",
                EXCHANGE_ID,
                MARKET_ID,
                TOURNAMENT_ID,
                reserved_exposure=2_000,
                open=False,
                uncertain=True,
                signed_quantity=2_000,
            ),
        ),
    )
    # At p=.5, the -5 point shift moves YES to logistic(-.5)=.37754.
    # 16,400 projected shares therefore lose about $2,008, over this test's
    # configured $2,000 maximum.
    decision = evaluate_risk(
        _opportunity(quantity=400),
        snapshot,
        _context(control),
    )
    assert decision.approved is False
    assert decision.reason == "swing_loss_limit"
    assert decision.swing_diagnostics is not None
    assert decision.swing_diagnostics.negative_swing_loss > 2_000
    assert decision.swing_diagnostics.house_derivative_per_point == 410.0
    assert decision.swing_diagnostics.senate_derivative_per_point == 0.0


def test_swing_hedge_that_reduces_worst_case_loss_is_approved_above_limit() -> None:
    opposing_exchange_id = "37"
    opposing_market_id = "m2"
    opposing_condition_id = "pm-condition-2"
    opposing_token_id = "pm-yes-2"
    mark = _mark()
    opposing_mapping = _market_mapping(
        exchange_id=opposing_exchange_id,
        market_id=opposing_market_id,
        party_sign=-1,
        pm_condition_id=opposing_condition_id,
        pm_yes_token_id=opposing_token_id,
    )
    control = SwingRiskControl(
        shock_points=5.0,
        max_loss=2_000.0,
        max_pm_mark_age_ns=35_000_000_000,
        crosswalk=SwingCrosswalk(
            verified=True,
            markets=(_market_mapping(), opposing_mapping),
        ),
        mark_provider=_MultiMarkProvider(
            {
                PM_YES_TOKEN_ID: mark,
                opposing_token_id: SwingPmMark(
                    pm_condition_id=opposing_condition_id,
                    midpoint=0.5,
                    observed_at=mark.observed_at,
                ),
            }
        ),
    )
    snapshot = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=MARKET_ID,
                status="open",
                exchange_ids=(EXCHANGE_ID,),
                tournament_id=TOURNAMENT_ID,
                mapping_accepted=True,
                tradeable=True,
            ),
            RuntimeMarket(
                market_id=opposing_market_id,
                status="open",
                exchange_ids=(opposing_exchange_id,),
                tournament_id=TOURNAMENT_ID,
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(),
        portfolio=RuntimePortfolio(
            positions=(
                RuntimePosition(
                    EXCHANGE_ID,
                    MARKET_ID,
                    TOURNAMENT_ID,
                    17_000,
                    17_000,
                ),
            ),
            account_trusted=True,
        ),
        observation_monotonic_ns=1_000_000_000,
    )
    _, baseline_diagnostics = evaluate_swing_cap(control, snapshot, ())

    decision = evaluate_risk(
        _opportunity(
            exchange_id=opposing_exchange_id,
            market_id=opposing_market_id,
            quantity=400,
        ),
        snapshot,
        _context(control),
    )

    assert baseline_diagnostics is not None
    baseline_worst_case_loss = max(
        baseline_diagnostics.positive_swing_loss,
        baseline_diagnostics.negative_swing_loss,
    )
    assert baseline_worst_case_loss > control.max_loss
    assert decision.approved is True
    assert decision.reason == "approved"
    assert decision.swing_diagnostics is not None
    projected_worst_case_loss = max(
        decision.swing_diagnostics.positive_swing_loss,
        decision.swing_diagnostics.negative_swing_loss,
    )
    assert projected_worst_case_loss > control.max_loss
    assert projected_worst_case_loss < baseline_worst_case_loss


def test_reducing_order_is_allowed_even_when_pm_mark_is_stale() -> None:
    snapshot = _snapshot(
        positions=(RuntimePosition(EXCHANGE_ID, MARKET_ID, TOURNAMENT_ID, 1_000, 1_000),),
    )
    decision = evaluate_risk(
        _opportunity(quantity=1_000, action=OrderAction.SELL),
        snapshot,
        _context(_control(mark=_mark(age_seconds=60))),
    )
    assert decision.approved is True
    assert decision.reason == "approved"
    assert decision.swing_diagnostics is None


def test_stale_pm_mark_fails_closed_for_risk_increase() -> None:
    decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        _context(_control(mark=_mark(age_seconds=36))),
    )
    assert decision.approved is False
    assert decision.reason == "swing_pm_mark_stale"


def test_unmapped_market_fails_closed_for_risk_increase() -> None:
    decision = evaluate_risk(
        _opportunity(exchange_id="99"),
        _snapshot(exchange_id="99"),
        _context(_control(mark=_mark())),
    )
    assert decision.approved is False
    assert decision.reason == "swing_market_unmapped"


def test_unverified_crosswalk_fails_closed_for_risk_increase() -> None:
    decision = evaluate_risk(
        _opportunity(),
        _snapshot(),
        _context(_control(mark=_mark(), verified=False)),
    )
    assert decision.approved is False
    assert decision.reason == "swing_mapping_unverified"
