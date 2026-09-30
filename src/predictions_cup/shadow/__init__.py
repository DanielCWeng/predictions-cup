"""SHADOW-002 champion/challenger runtime bus."""

from predictions_cup.shadow.adapters import (
    DirectPmCandidate,
    Hazard005FCandidate,
    Hazard005FEvaluator,
    Hazard005FSignal,
    MakerCandidate,
    NullStructuralFairValueProvider,
    Pred006Candidate,
    Pred006RuntimeEvaluator,
    Pred006Signal,
    StructuralFairValue,
    StructuralFairValueCandidate,
    StructuralFairValueProvider,
)
from predictions_cup.shadow.bus import (
    CandidateHealth,
    ShadowBus,
    ShadowHealth,
)
from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
    ShadowCandidate,
)
from predictions_cup.shadow.persistence import (
    CaptureStrategyEventStore,
    CompositeShadowEventStore,
    InMemoryEventStore,
    JsonlEventStore,
    PersistenceHealth,
)
from predictions_cup.shadow.replay import (
    ReplayResult,
    ShadowReplayRunner,
    load_persisted_snapshots,
)

__all__ = [
    "CandidateDecision",
    "CandidateHealth",
    "CandidateOutput",
    "CaptureStrategyEventStore",
    "CompositeShadowEventStore",
    "CanonicalShadowSnapshot",
    "DecisionStatus",
    "DirectPmCandidate",
    "Hazard005FCandidate",
    "Hazard005FEvaluator",
    "Hazard005FSignal",
    "InMemoryEventStore",
    "JsonlEventStore",
    "MakerCandidate",
    "NullStructuralFairValueProvider",
    "PersistenceHealth",
    "Pred006Candidate",
    "Pred006RuntimeEvaluator",
    "Pred006Signal",
    "ReplayResult",
    "ShadowBus",
    "ShadowCandidate",
    "ShadowHealth",
    "ShadowReplayRunner",
    "StructuralFairValue",
    "StructuralFairValueCandidate",
    "StructuralFairValueProvider",
    "load_persisted_snapshots",
]
