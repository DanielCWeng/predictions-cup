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
from predictions_cup.shadow.adapters import (
    DirectPmCandidate,
    Hazard005FCandidate,
    MakerCandidate,
    Pred006Candidate,
    StructuralFairValueCandidate,
)
from predictions_cup.shadow.bus import ShadowBus
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
    mapping_version: str
    rejected_boundaries: int = 0

    async def start(self) -> None:
        await self.bus.start()

    async def close(self) -> None:
        await self.bus.close()

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
            )
        )
    store: ShadowEventStore = (
        primary
        if not mirrors
        else CompositeShadowEventStore(primary, tuple(mirrors))
    )

    direct_pm = DirectPolymarketFairValueProvider(core.mapping)
    bus = ShadowBus(
        (
            MakerCandidate(core.engine),
            DirectPmCandidate(direct_pm, mapping=core.mapping),
            Pred006Candidate(),
            Hazard005FCandidate(),
            StructuralFairValueCandidate(),
        ),
        store=store,
        queue_capacity=settings.shadow_candidate_queue_capacity,
        ingress_capacity=settings.shadow_ingress_queue_capacity,
        candidate_timeout_seconds=settings.shadow_candidate_timeout_ms / 1_000.0,
        trading_enabled=False,
    )
    return LiveShadowRuntime(
        bus=bus,
        mapping_version=_mapping_version(core.mapping),
    )


def _mapping_version(mapping: MappingDocument) -> str:
    normalized = mapping.normalized().model_dump_json()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"mapping-v1-{digest[:16]}"
