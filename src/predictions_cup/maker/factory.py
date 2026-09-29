"""Startup-time MAKE-001 composition from validated settings."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.config import AppSettings
from predictions_cup.execution.models import ExecutionMode
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.maker.direct_pm import DirectPolymarketFairValueProvider
from predictions_cup.maker.engine import MakerConfig, MakerEngine
from predictions_cup.maker.lifecycle import (
    QuoteLifecycleConfig,
    QuoteLifecycleManager,
    QuoteRegistry,
)
from predictions_cup.maker.policies import (
    AvellanedaStoikovInventoryModel,
    ConservativeEligibilityPolicy,
    ConservativeSpreadPolicy,
    InventoryConfidenceSizePolicy,
    NullPredictiveAdjuster,
    NullToxicityProvider,
)
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.risk.core import RiskContext, RiskLimits


@dataclass(frozen=True, slots=True)
class MakerRuntimeComponents:
    mapping: MappingDocument
    engine: MakerEngine
    lifecycle: QuoteLifecycleManager
    quotes: QuoteRegistry
    risk_context: RiskContext
    reservations: ExecutionReservationBook
    kill_switch: MakerKillSwitch


def build_maker_components(
    settings: AppSettings,
    *,
    mapping: MappingDocument | None = None,
) -> MakerRuntimeComponents:
    """Build immutable/pure maker components once at process startup."""
    mapping = mapping or load_document(settings.maker_mapping_path)
    if (
        settings.tournament_id is not None
        and mapping.tournament_id != settings.tournament_id
    ):
        raise ValueError("maker mapping tournament does not match configured tournament")

    ms = 1_000_000
    engine = MakerEngine(
        fair_value=DirectPolymarketFairValueProvider(
            mapping,
            max_age_ns=settings.maker_max_fv_age_ms * ms,
        ),
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=AvellanedaStoikovInventoryModel(
            risk_aversion=settings.maker_inventory_risk_aversion,
            variance_horizon=settings.maker_variance_horizon,
            variance_floor=settings.maker_variance_floor,
        ),
        spread=ConservativeSpreadPolicy(
            base_half_spread_ticks=settings.maker_base_half_spread_ticks,
            uncertainty_multiplier=settings.maker_uncertainty_multiplier,
            volatility_multiplier=settings.maker_volatility_multiplier,
            toxicity_half_spread_ticks=settings.maker_toxicity_half_spread_ticks,
        ),
        size=InventoryConfidenceSizePolicy(
            base_size=settings.maker_base_size,
            minimum_size=settings.maker_minimum_size,
        ),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=settings.maker_max_bbo_age_ms * ms,
            max_fv_age_ns=settings.maker_max_fv_age_ms * ms,
            max_account_age_ns=settings.maker_max_account_age_ms * ms,
            max_inventory_age_ns=settings.maker_max_inventory_age_ms * ms,
            max_optional_signal_age_ns=settings.maker_max_signal_age_ms * ms,
            require_trusted_depth=settings.maker_require_trusted_depth,
            max_depth_age_ns=settings.maker_max_depth_age_ms * ms,
        ),
        config=MakerConfig(
            strategy_id="make-direct-pm",
            strategy_version="make-001-v1",
            max_abs_inventory=settings.maker_max_abs_inventory,
        ),
    )
    lifecycle = QuoteLifecycleManager(
        QuoteLifecycleConfig(
            min_replace_ticks=settings.maker_min_replace_ticks,
            min_replace_size=settings.maker_min_replace_size,
            min_requote_interval_ns=settings.maker_min_requote_interval_ms * ms,
        )
    )
    kill_switch = MakerKillSwitch()
    if settings.global_kill_switch:
        kill_switch.activate("startup_configuration")

    return MakerRuntimeComponents(
        mapping=mapping,
        engine=engine,
        lifecycle=lifecycle,
        quotes=QuoteRegistry(),
        risk_context=RiskContext(
            mode=ExecutionMode(settings.execution_mode),
            kill_switch=settings.global_kill_switch,
            limits=_risk_limits(settings),
            max_state_age_ns=settings.risk_max_state_age_ms * ms,
        ),
        reservations=ExecutionReservationBook(),
        kill_switch=kill_switch,
    )


def _risk_limits(settings: AppSettings) -> RiskLimits | None:
    values = (
        settings.risk_max_order_size,
        settings.risk_max_gross_exposure,
        settings.risk_max_per_market_exposure,
        settings.risk_max_open_order_exposure,
        settings.risk_max_concurrent_open_orders,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("central risk limits must be configured as one complete set")
    assert settings.risk_max_order_size is not None
    assert settings.risk_max_gross_exposure is not None
    assert settings.risk_max_per_market_exposure is not None
    assert settings.risk_max_open_order_exposure is not None
    assert settings.risk_max_concurrent_open_orders is not None
    return RiskLimits(
        max_order_size=settings.risk_max_order_size,
        max_gross_exposure=settings.risk_max_gross_exposure,
        max_per_market_exposure=settings.risk_max_per_market_exposure,
        max_open_order_exposure=settings.risk_max_open_order_exposure,
        max_concurrent_open_orders=settings.risk_max_concurrent_open_orders,
    )
