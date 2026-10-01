"""Production wiring from MAKE's immutable decision boundary into SHADOW-002."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from predictions_cup.config import AppSettings
from predictions_cup.live_learn import LiveLearnEngine
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.maker.direct_pm import DirectPolymarketFairValueProvider
from predictions_cup.maker.factory import MakerRuntimeComponents
from predictions_cup.mapping.models import MappingDocument
from predictions_cup.models.frozen_research import (
    FROZEN_RESEARCH_MODEL_IDS,
    Live005IMinuteState,
    frozen_research_paper_providers,
)
from predictions_cup.models.registry import ModelRegistry, default_model_registry
from predictions_cup.models.runtime import paper_shadow_candidates
from predictions_cup.shadow.adapters import (
    DirectPmCandidate,
    Hazard005FCandidate,
    MakerCandidate,
    Pred006Candidate,
    StructuralFairValueCandidate,
)
from predictions_cup.shadow.bus import ShadowBus
from predictions_cup.shadow.contracts import CandidateDecision
from predictions_cup.shadow.frozen_runtime import (
    Frozen005FEvaluator,
    FrozenPred006Evaluator,
)
from predictions_cup.shadow.live_005f import Live005FStateProvider
from predictions_cup.shadow.persistence import (
    CaptureStrategyEventStore,
    CompositeShadowEventStore,
    JsonlEventStore,
    ShadowEventStore,
)


@dataclass(slots=True)
class LiveShadowRuntime:
    """Own SHADOW lifecycle and expose a synchronous non-blocking MAKE observer."""

    bus: ShadowBus
    store: ShadowEventStore
    mapping_version: str
    hazard_005f: Live005FStateProvider | None = None
    context_005i: Live005IMinuteState | None = None
    rejected_boundaries: int = 0

    async def start(self) -> None:
        await self.bus.start()

    async def close(self) -> None:
        await self.bus.close()

    async def persist_model_decision(self, decision: CandidateDecision) -> None:
        """Persist one LIVE model decision through the accepted SHADOW store."""
        await self.store.persist_decision(decision)

    def observe_polymarket_bbo(
        self,
        *,
        token_id: str,
        observed_at: datetime,
        observed_monotonic_ns: int,
        best_bid: float | None,
        best_ask: float | None,
        source_version: str,
        trusted: bool,
    ) -> bool:
        accepted = False
        provider_005f = self.hazard_005f
        if provider_005f is not None:
            accepted = provider_005f.observe_bbo(
                scope_id=token_id,
                observed_at=observed_at,
                observed_monotonic_ns=observed_monotonic_ns,
                best_bid=best_bid,
                best_ask=best_ask,
                source_version=source_version,
                trusted=trusted,
            ) or accepted
        provider_005i = self.context_005i
        if provider_005i is not None:
            accepted = provider_005i.observe_bbo(
                scope_id=token_id,
                observed_at=observed_at,
                best_bid=best_bid,
                best_ask=best_ask,
                source_version=source_version,
                trusted=trusted,
            ) or accepted
        return accepted

    def observe(
        self,
        change: MakerStateChange,
        observed_at: datetime,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> None:
        for exchange_id in sorted(snapshots):
            snapshot = snapshots[exchange_id]
            accepted = self.bus.submit_maker(
                snapshot,
                observed_at=observed_at,
                mapping_version=self.mapping_version,
                source_revision=change.event_id,
                source_provenance={
                    "runtime": "MakerRuntimeLoop",
                    "maker_event_id": change.event_id,
                },
            )
            if not accepted:
                self.rejected_boundaries += 1


def build_live_shadow_runtime(
    settings: AppSettings,
    core: MakerRuntimeComponents,
) -> LiveShadowRuntime:
    """Compose the launch SHADOW bus without exposing any order-write capability."""
    primary = JsonlEventStore(
        settings.shadow_journal_path,
        queue_capacity=settings.shadow_persistence_queue_capacity,
        batch_size=settings.shadow_persistence_batch_size,
    )
    mirrors: list[ShadowEventStore] = []
    if settings.shadow_capture_mirror_enabled:
        mirrors.append(
            CaptureStrategyEventStore(
                settings.sig_research_path,
                queue_capacity=settings.sig_capture_queue_max,
                shard_seconds=settings.sig_capture_parquet_shard_seconds,
                max_rows_per_shard=settings.sig_capture_parquet_max_rows_per_shard,
            )
        )
    if settings.live_learn_enabled:
        mirrors.append(
            LiveLearnEngine.from_paths(
                shadow_journal_path=settings.shadow_journal_path,
                outcome_path=settings.live_learn_outcome_path,
                report_root=settings.live_learn_report_path,
                execution_journal_path=settings.execution_journal_path,
                queue_capacity=settings.live_learn_queue_capacity,
                evidence_grace_seconds=settings.live_learn_evidence_grace_seconds,
                max_evidence_age_seconds=settings.live_learn_max_evidence_age_seconds,
                max_retained_decisions=settings.live_learn_max_retained_decisions,
            )
        )
    store: ShadowEventStore = (
        primary
        if not mirrors
        else CompositeShadowEventStore(primary, tuple(mirrors))
    )

    direct_pm = DirectPolymarketFairValueProvider(core.mapping)
    model_candidates = paper_shadow_candidates(
        default_model_registry(),
        settings.model_paper_ids,
        risk_context=core.risk_context,
    )
    hazard_005f = Live005FStateProvider(
        mapping=core.mapping,
        grid_origin=settings.shadow_005f_grid_origin,
    )
    context_005i, frozen_research_providers = frozen_research_paper_providers(
        mapping=core.mapping,
        hazard_005f=hazard_005f,
    )
    frozen_research_candidates = paper_shadow_candidates(
        ModelRegistry(frozen_research_providers),
        FROZEN_RESEARCH_MODEL_IDS,
    )
    bus = ShadowBus(
        (
            MakerCandidate(core.engine),
            DirectPmCandidate(direct_pm, mapping=core.mapping),
            Pred006Candidate(FrozenPred006Evaluator()),
            Hazard005FCandidate(
                Frozen005FEvaluator(provider=hazard_005f)
            ),
            StructuralFairValueCandidate(),
            *frozen_research_candidates,
            *model_candidates,
        ),
        store=store,
        queue_capacity=settings.shadow_candidate_queue_capacity,
        ingress_capacity=settings.shadow_ingress_queue_capacity,
        candidate_timeout_seconds=settings.shadow_candidate_timeout_ms / 1_000.0,
        minimum_maker_snapshot_interval_seconds=(
            settings.shadow_snapshot_min_interval_seconds
        ),
        trading_enabled=False,
    )
    return LiveShadowRuntime(
        bus=bus,
        store=store,
        mapping_version=_mapping_version(core.mapping),
        hazard_005f=hazard_005f,
        context_005i=context_005i,
    )


def _mapping_version(mapping: MappingDocument) -> str:
    normalized = mapping.normalized().model_dump_json()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"mapping-v1-{digest[:16]}"
