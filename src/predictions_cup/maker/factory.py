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
    BinaryCaraInventoryModel,
    ConservativeEligibilityPolicy,
    ConservativeSpreadPolicy,
    InventoryConfidenceSizePolicy,
    NullPredictiveAdjuster,
    NullToxicityProvider,
)
from predictions_cup.maker.safety import MakerKillSwitch
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
)
from predictions_cup.risk.capital import MarketExposureGroup
from predictions_cup.risk.core import RiskContext, RiskLimits, RiskProfile
from predictions_cup.risk.groups import load_exposure_group_provider
from predictions_cup.risk.swing import (
    SwingPmMarkProvider,
    SwingRiskControl,
    load_swing_crosswalk,
)


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
    swing_mark_provider: SwingPmMarkProvider | None = None,
) -> MakerRuntimeComponents:
    """Build immutable/pure maker components once at process startup."""
    mapping = mapping or load_document(settings.maker_mapping_path)
    if settings.tournament_id is not None and mapping.tournament_id != settings.tournament_id:
        raise ValueError("maker mapping tournament does not match configured tournament")

    if (
        settings.risk_max_event_group_exposure is not None
        and settings.risk_exposure_groups_path is None
    ):
        raise ValueError("event-group risk cap requires risk_exposure_groups_path")
    exposure_groups: tuple[MarketExposureGroup, ...] = ()
    if settings.risk_exposure_groups_path is not None:
        group_provider = load_exposure_group_provider(settings.risk_exposure_groups_path)
        exposure_groups = group_provider.for_tournament(mapping.tournament_id)

    swing_control: SwingRiskControl | None = None
    if settings.risk_swing_cap_enabled:
        swing_control = SwingRiskControl(
            shock_points=settings.risk_swing_shock_points,
            max_loss=settings.risk_swing_max_loss,
            max_pm_mark_age_ns=settings.risk_swing_max_pm_mark_age_ms * 1_000_000,
            crosswalk=load_swing_crosswalk(
                mapping,
                mapping_path=settings.maker_mapping_path,
            ),
            mark_provider=swing_mark_provider,
        )

    market_token_ids = {
        record.sig_exchange_id: record.direct_polymarket.mapped_token_id
        for record in mapping.records
        if record.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}
        and record.mapping_direction is MappingDirection.SAME
        and record.status is MappingStatus.VERIFIED
        and record.direct_polymarket is not None
    }

    ms = 1_000_000
    engine = MakerEngine(
        fair_value=DirectPolymarketFairValueProvider(mapping),
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=BinaryCaraInventoryModel(
            risk_aversion=settings.maker_inventory_risk_aversion,
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
            min_fair_value=settings.maker_min_fair_value,
            max_fair_value=settings.maker_max_fair_value,
            account_proxy_enabled=settings.account_proxy_enabled,
            max_account_proxy_age_ns=int(
                settings.account_proxy_max_age_minutes * 60 * 1_000_000_000
            ),
            proxy_soft_unwind_limit=settings.account_proxy_soft_unwind_limit,
        ),
        config=MakerConfig(
            strategy_id="make-direct-pm",
            strategy_version="make-001-v1",
            max_abs_inventory=settings.maker_max_abs_inventory,
            account_proxy_size_factor=settings.account_proxy_size_factor,
            fill_seeking_enabled=settings.maker_fill_seeking_enabled,
            fill_seeking_min_edge=settings.maker_fill_seeking_min_edge,
            deep_ladder_enabled=settings.maker_deep_ladder_enabled,
            deep_ladder_level_offsets=settings.maker_deep_ladder_level_offsets,
            deep_ladder_level_sizes=settings.maker_deep_ladder_level_sizes,
            deep_ladder_position_cap=settings.maker_deep_ladder_position_cap,
        ),
        market_token_ids=market_token_ids,
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
            profile=_risk_profile(settings),
            max_state_age_ns=settings.risk_max_state_age_ms * ms,
            # Account continuity is event-driven through AccountRealtimeStateEngine:
            # gaps/reconnects/fills revoke portfolio trust and force REST reconciliation.
            max_account_age_ns=None,
            max_mark_age_ns=settings.risk_max_mark_age_ms * ms,
            require_capital_state=settings.risk_capital_control_enabled,
            exposure_groups=exposure_groups,
            swing_control=swing_control,
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
        max_per_strategy_exposure=settings.risk_max_per_strategy_exposure,
        max_event_group_exposure=settings.risk_max_event_group_exposure,
        max_tournament_exposure=settings.risk_max_tournament_exposure,
        session_loss_limit=settings.risk_session_loss_limit,
        drawdown_limit=settings.risk_drawdown_limit,
    )


def _risk_profile(settings: AppSettings) -> RiskProfile | None:
    hard = _risk_limits(settings)
    if hard is None:
        return None
    version = f"{settings.risk_profile_version}:{settings.risk_profile_mode}"
    if settings.risk_profile_mode == "STANDARD":
        return RiskProfile(
            name=settings.risk_profile_name,
            version=version,
            limits=hard,
        )

    required = (
        settings.risk_exploratory_max_order_size,
        settings.risk_exploratory_max_gross_exposure,
        settings.risk_exploratory_max_per_market_exposure,
        settings.risk_exploratory_max_open_order_exposure,
        settings.risk_exploratory_max_concurrent_open_orders,
    )
    if any(value is None for value in required):
        raise ValueError("exploratory risk profile is incomplete")
    assert settings.risk_exploratory_max_order_size is not None
    assert settings.risk_exploratory_max_gross_exposure is not None
    assert settings.risk_exploratory_max_per_market_exposure is not None
    assert settings.risk_exploratory_max_open_order_exposure is not None
    assert settings.risk_exploratory_max_concurrent_open_orders is not None
    exploratory = RiskLimits(
        max_order_size=settings.risk_exploratory_max_order_size,
        max_gross_exposure=settings.risk_exploratory_max_gross_exposure,
        max_per_market_exposure=settings.risk_exploratory_max_per_market_exposure,
        max_open_order_exposure=settings.risk_exploratory_max_open_order_exposure,
        max_concurrent_open_orders=(settings.risk_exploratory_max_concurrent_open_orders),
        max_per_strategy_exposure=(settings.risk_exploratory_max_per_strategy_exposure),
        max_event_group_exposure=(settings.risk_exploratory_max_event_group_exposure),
        max_tournament_exposure=(settings.risk_exploratory_max_tournament_exposure),
        session_loss_limit=settings.risk_session_loss_limit,
        drawdown_limit=settings.risk_drawdown_limit,
    )
    return RiskProfile(
        name=settings.risk_profile_name,
        version=version,
        limits=exploratory,
        exploratory=True,
        hard_limits=hard,
    )
