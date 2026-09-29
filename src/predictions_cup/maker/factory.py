"""Startup factory for MAKE-001 production components."""

from __future__ import annotations

from dataclasses import dataclass

from predictions_cup.config import AppSettings
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
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDocument


@dataclass(frozen=True, slots=True)
class MakerRuntimeComponents:
    mapping: MappingDocument
    engine: MakerEngine
    lifecycle: QuoteLifecycleManager
    quotes: QuoteRegistry


def build_maker_components(
    settings: AppSettings,
    *,
    mapping: MappingDocument | None = None,
) -> MakerRuntimeComponents:
    """Build the default audited maker stack once at process startup."""
    mapping = mapping or load_document(settings.maker_mapping_path)
    if settings.tournament_id is not None and mapping.tournament_id != settings.tournament_id:
        raise ValueError("maker mapping tournament does not match configured tournament")

    ms = 1_000_000
    engine = MakerEngine(
        fair_value=DirectPolymarketFairValueProvider(
            mapping,
            max_age_ns=settings.maker_fv_max_age_ms * ms,
        ),
        predictive=NullPredictiveAdjuster(),
        toxicity=NullToxicityProvider(),
        inventory=AvellanedaStoikovInventoryModel(),
        spread=ConservativeSpreadPolicy(
            base_half_spread_ticks=settings.maker_base_half_spread_ticks,
        ),
        size=InventoryConfidenceSizePolicy(
            base_size=settings.maker_base_size,
            minimum_size=settings.maker_minimum_size,
        ),
        eligibility=ConservativeEligibilityPolicy(
            max_bbo_age_ns=settings.maker_bbo_max_age_ms * ms,
            max_fv_age_ns=settings.maker_fv_max_age_ms * ms,
            max_account_age_ns=settings.maker_account_max_age_ms * ms,
            max_inventory_age_ns=settings.maker_inventory_max_age_ms * ms,
            max_optional_signal_age_ns=settings.maker_signal_max_age_ms * ms,
            require_trusted_depth=settings.maker_require_trusted_depth,
            max_depth_age_ns=settings.maker_depth_max_age_ms * ms,
        ),
        config=MakerConfig(
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
    return MakerRuntimeComponents(
        mapping=mapping,
        engine=engine,
        lifecycle=lifecycle,
        quotes=QuoteRegistry(),
    )
