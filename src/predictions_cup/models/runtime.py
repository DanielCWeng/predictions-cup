"""Ultra-low-latency hot-swappable model runtime.

Configuration is resolved once at startup.  The evaluate path is pure in-memory
work: provider -> bounded sizing -> accepted RISK-002 -> BUILD-009 plan.  This
module never owns a venue client, credential, journal or order sink.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from time import monotonic_ns

from predictions_cup.execution.models import ExecutionEvent, ExecutionMode
from predictions_cup.execution.planner import build_execution_plan
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.execution.sinks import ExecutionPlan
from predictions_cup.maker.contracts import MakerMarketSnapshot
from predictions_cup.maker.coordinator import MakerStateChange
from predictions_cup.models.contracts import (
    ModelCapability,
    ModelDecision,
    ModelDecisionKind,
    ModelProvider,
    ModelSpec,
)
from predictions_cup.models.registry import ModelRegistry, parse_model_ids
from predictions_cup.models.sizing import SizingResult, size_model_decision
from predictions_cup.risk.core import RiskContext, RiskDecision, evaluate_risk
from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
    decision_from_output,
    failure_output,
)
from predictions_cup.strategy.core import CandidateLeg, Opportunity

ClockNs = Callable[[], int]
RiskContextSource = RiskContext | Callable[[], RiskContext]
PlanDispatcher = Callable[
    [ExecutionPlan, MakerMarketSnapshot],
    Awaitable[ExecutionEvent],
]
DecisionObserver = Callable[[CandidateDecision], Awaitable[None]]


class EffectiveModelMode(StrEnum):
    NOT_LIVE = "NOT_LIVE"
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(frozen=True, slots=True)
class ModelEvaluation:
    decision: CandidateDecision
    sizing: SizingResult | None
    risk_decision: RiskDecision | None
    execution_plan: ExecutionPlan | None
    effective_mode: EffectiveModelMode


@dataclass(frozen=True, slots=True)
class ModelRuntimeStatus:
    model_id: str
    model_version: str
    capability: str
    paper_enabled: bool
    code_live_gate: bool
    env_live_gate: bool
    platform_live_ready: bool
    effective_mode: EffectiveModelMode
    healthy: bool
    quarantined: bool
    failure_count: int
    last_decision_id: str | None
    last_error: str | None
    source_hash: str


@dataclass(slots=True)
class _ModelHealth:
    failure_count: int = 0
    consecutive_failures: int = 0
    quarantined: bool = False
    last_decision_id: str | None = None
    last_error: str | None = None


class _DecisionCandidate:
    """Adapter used only to reuse the accepted CandidateDecision envelope."""

    def __init__(
        self,
        provider: ModelProvider,
        mode: EffectiveModelMode,
    ) -> None:
        self.candidate_id = f"model:{provider.spec.model_id}:{mode.value.lower()}"
        self.candidate_version = provider.spec.model_version
        self.strategy_family = provider.spec.strategy_family.value

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        del snapshot
        raise RuntimeError("decision adapter is identity-only")


class PaperModelCandidate:
    """SHADOW-002 adapter: same provider logic, permanently no order-write path."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        risk_context: RiskContextSource | None = None,
    ) -> None:
        self._provider = provider
        self._risk_context_source = risk_context
        self.candidate_id = f"model:{provider.spec.model_id}:paper"
        self.candidate_version = provider.spec.model_version
        self.strategy_family = provider.spec.strategy_family.value

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        decision = self._provider.evaluate(snapshot)
        if not isinstance(decision, ModelDecision):
            raise TypeError("model provider returned a non-ModelDecision")
        sizing, risk = _size_and_risk(
            spec=self._provider.spec,
            decision=decision,
            snapshot=snapshot,
            risk_context=_resolve_risk_context(self._risk_context_source),
        )
        payload = _candidate_payload(
            spec=self._provider.spec,
            decision=decision,
            mode=EffectiveModelMode.PAPER,
            sizing=sizing,
            risk=risk,
            logical_operation_id=None,
        )
        return _candidate_output(decision, payload, sizing=sizing)


class ModelRuntime:
    """Frozen startup dispatch table plus isolated model health state."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        paper_model_ids: Sequence[str] = (),
        live_model_ids: Sequence[str] = (),
        platform_live_ready: bool = False,
        failure_quarantine_threshold: int = 3,
        clock_ns: ClockNs = monotonic_ns,
    ) -> None:
        if failure_quarantine_threshold <= 0:
            raise ValueError("failure_quarantine_threshold must be positive")
        self._registry = registry
        self._paper_ids = frozenset(paper_model_ids)
        self._live_ids = frozenset(live_model_ids)
        self._platform_live_ready = platform_live_ready
        self._failure_threshold = failure_quarantine_threshold
        self._clock_ns = clock_ns

        configured = self._paper_ids | self._live_ids
        unknown = configured.difference(registry.ids())
        if unknown:
            raise ValueError("unknown configured model ids: " + ",".join(sorted(unknown)))
        blocked = tuple(
            model_id
            for model_id in sorted(self._live_ids)
            if not registry.get(model_id).spec.live_eligible
        )
        if blocked:
            raise ValueError(
                "host requested model without code LIVE authorization: "
                + ",".join(blocked)
            )

        self._providers = {
            model_id: registry.get(model_id)
            for model_id in registry.ids()
        }
        self._health = {
            model_id: _ModelHealth()
            for model_id in registry.ids()
        }

    @classmethod
    def from_allowlists(
        cls,
        registry: ModelRegistry,
        *,
        paper_model_ids: str,
        live_model_ids: str,
        platform_live_ready: bool,
        failure_quarantine_threshold: int = 3,
        clock_ns: ClockNs = monotonic_ns,
    ) -> ModelRuntime:
        return cls(
            registry,
            paper_model_ids=parse_model_ids(paper_model_ids),
            live_model_ids=parse_model_ids(live_model_ids),
            platform_live_ready=platform_live_ready,
            failure_quarantine_threshold=failure_quarantine_threshold,
            clock_ns=clock_ns,
        )

    @property
    def authorized_live_model_ids(self) -> tuple[str, ...]:
        if not self._platform_live_ready:
            return ()
        return tuple(
            model_id
            for model_id in self._registry.ids()
            if model_id in self._live_ids
        )

    def effective_mode(self, model_id: str) -> EffectiveModelMode:
        provider = self._registry.get(model_id)
        if (
            provider.spec.live_eligible
            and model_id in self._live_ids
            and self._platform_live_ready
        ):
            return EffectiveModelMode.LIVE
        if model_id in self._paper_ids:
            return EffectiveModelMode.PAPER
        return EffectiveModelMode.NOT_LIVE

    def recover(self, model_id: str) -> None:
        health = self._health[model_id]
        health.consecutive_failures = 0
        health.quarantined = False
        health.last_error = None

    def status(self) -> tuple[ModelRuntimeStatus, ...]:
        rows: list[ModelRuntimeStatus] = []
        for model_id in self._registry.ids():
            provider = self._providers[model_id]
            health = self._health[model_id]
            rows.append(
                ModelRuntimeStatus(
                    model_id=model_id,
                    model_version=provider.spec.model_version,
                    capability=provider.spec.capability.value,
                    paper_enabled=model_id in self._paper_ids,
                    code_live_gate=provider.spec.live_eligible,
                    env_live_gate=model_id in self._live_ids,
                    platform_live_ready=self._platform_live_ready,
                    effective_mode=self.effective_mode(model_id),
                    healthy=not health.quarantined and health.last_error is None,
                    quarantined=health.quarantined,
                    failure_count=health.failure_count,
                    last_decision_id=health.last_decision_id,
                    last_error=health.last_error,
                    source_hash=provider.spec.source_hash,
                )
            )
        return tuple(rows)

    def evaluate(
        self,
        model_id: str,
        snapshot: CanonicalShadowSnapshot,
        *,
        risk_context: RiskContext | None = None,
        available_capital: float | None = None,
        logical_operation_id: str | None = None,
    ) -> ModelEvaluation:
        provider = self._providers[model_id]
        health = self._health[model_id]
        mode = self.effective_mode(model_id)
        started = self._clock_ns()

        if health.quarantined:
            finished = self._clock_ns()
            output = failure_output(
                DecisionStatus.DISABLED,
                "model_quarantined",
                detail=health.last_error,
            )
            durable = decision_from_output(
                snapshot=snapshot,
                candidate=_DecisionCandidate(provider, mode),
                output=_augment_failure_payload(output, provider.spec, mode),
                compute_started_at=started,
                compute_finished_at=finished,
            )
            health.last_decision_id = durable.decision_id
            return ModelEvaluation(durable, None, None, None, mode)

        validation = _input_failure(provider.spec, snapshot)
        if validation is not None:
            finished = self._clock_ns()
            output = failure_output(validation[0], validation[1])
            durable = decision_from_output(
                snapshot=snapshot,
                candidate=_DecisionCandidate(provider, mode),
                output=_augment_failure_payload(output, provider.spec, mode),
                compute_started_at=started,
                compute_finished_at=finished,
            )
            health.last_decision_id = durable.decision_id
            health.last_error = validation[1]
            return ModelEvaluation(durable, None, None, None, mode)

        try:
            model_decision = provider.evaluate(snapshot)
            if not isinstance(model_decision, ModelDecision):
                raise TypeError("model provider returned a non-ModelDecision")
        except Exception as exc:
            finished = self._clock_ns()
            self._record_failure(model_id, f"{type(exc).__name__}: {exc}")
            output = failure_output(
                DecisionStatus.EXCEPTION,
                "model_exception",
                detail=type(exc).__name__,
            )
            durable = decision_from_output(
                snapshot=snapshot,
                candidate=_DecisionCandidate(provider, mode),
                output=_augment_failure_payload(output, provider.spec, mode),
                compute_started_at=started,
                compute_finished_at=finished,
            )
            health.last_decision_id = durable.decision_id
            return ModelEvaluation(durable, None, None, None, mode)

        health.consecutive_failures = 0
        health.last_error = None
        sizing, risk = _size_and_risk(
            spec=provider.spec,
            decision=model_decision,
            snapshot=snapshot,
            risk_context=risk_context,
            available_capital=available_capital,
        )

        plan: ExecutionPlan | None = None
        operation_id = logical_operation_id
        if (
            mode is EffectiveModelMode.LIVE
            and sizing is not None
            and sizing.tradeable
            and risk is not None
            and risk.approved
        ):
            if risk_context is None or risk_context.mode is not ExecutionMode.LIVE:
                risk = RiskDecision(
                    approved=False,
                    reason="live_requires_live_risk_context",
                )
            else:
                operation_id = operation_id or (
                    f"model:{provider.spec.model_id}:"
                    f"{snapshot.snapshot_id}"
                )
                plan = build_execution_plan(
                    risk,
                    logical_operation_id=operation_id,
                    created_monotonic_ns=self._clock_ns(),
                )

        payload = _candidate_payload(
            spec=provider.spec,
            decision=model_decision,
            mode=mode,
            sizing=sizing,
            risk=risk,
            logical_operation_id=operation_id if plan is not None else None,
        )
        output = _candidate_output(model_decision, payload, sizing=sizing)
        finished = self._clock_ns()
        durable = decision_from_output(
            snapshot=snapshot,
            candidate=_DecisionCandidate(provider, mode),
            output=output,
            compute_started_at=started,
            compute_finished_at=finished,
        )
        health.last_decision_id = durable.decision_id
        return ModelEvaluation(durable, sizing, risk, plan, mode)

    def _record_failure(self, model_id: str, error: str) -> None:
        health = self._health[model_id]
        health.failure_count += 1
        health.consecutive_failures += 1
        health.last_error = error
        if health.consecutive_failures >= self._failure_threshold:
            health.quarantined = True


class LiveModelCoordinator:
    """Accepted service-shell bridge from model plans into BUILD-009 dispatch."""

    def __init__(
        self,
        runtime: ModelRuntime,
        *,
        mapping_version: str,
        risk_context: RiskContextSource,
        reservations: ExecutionReservationBook,
        dispatch: PlanDispatcher,
        decision_observer: DecisionObserver | None = None,
    ) -> None:
        if not mapping_version.strip():
            raise ValueError("mapping_version must not be blank")
        self._runtime = runtime
        self._mapping_version = mapping_version
        self._risk_context_source = risk_context
        self._reservations = reservations
        self._dispatch = dispatch
        self._decision_observer = decision_observer

    async def on_state_change(
        self,
        change: MakerStateChange,
        observed_at: datetime,
        snapshots: Mapping[str, MakerMarketSnapshot],
    ) -> tuple[ExecutionEvent, ...]:
        events: list[ExecutionEvent] = []
        for exchange_id in sorted(snapshots):
            original = snapshots[exchange_id]
            for model_id in self._runtime.authorized_live_model_ids:
                overlaid_runtime = self._reservations.overlay_snapshot(original.runtime)
                maker = replace(original, runtime=overlaid_runtime)
                canonical = CanonicalShadowSnapshot.freeze(
                    maker,
                    observed_at=observed_at,
                    mapping_version=self._mapping_version,
                    source_revision=change.event_id,
                    source_provenance={
                        "runtime": "MODEL_RUNTIME_001",
                        "mode": "LIVE",
                    },
                )
                context = _resolve_risk_context(self._risk_context_source)
                if context is None:
                    continue
                result = self._runtime.evaluate(
                    model_id,
                    canonical,
                    risk_context=context,
                    logical_operation_id=(
                        f"{change.event_id}:model:{model_id}:{exchange_id}"
                    ),
                )
                if self._decision_observer is not None:
                    await self._decision_observer(result.decision)
                plan = result.execution_plan
                if plan is None:
                    continue
                self._reservations.reserve(
                    plan.envelope.logical_operation_id,
                    plan.intents,
                )
                events.append(await self._dispatch(plan, maker))
        return tuple(events)


def paper_shadow_candidates(
    registry: ModelRegistry,
    model_ids: str | Sequence[str],
    *,
    risk_context: RiskContextSource | None = None,
) -> tuple[PaperModelCandidate, ...]:
    ids = parse_model_ids(model_ids) if isinstance(model_ids, str) else tuple(model_ids)
    providers = registry.resolve(ids)
    return tuple(
        PaperModelCandidate(provider, risk_context=risk_context)
        for provider in providers
    )


def model_strategy_id(spec: ModelSpec) -> str:
    return (
        f"MODEL::{spec.model_id}::{spec.model_version}::"
        f"{spec.source_hash[:12]}"
    )


def _resolve_risk_context(source: RiskContextSource | None) -> RiskContext | None:
    if source is None:
        return None
    return source() if callable(source) else source


def _input_failure(
    spec: ModelSpec,
    snapshot: CanonicalShadowSnapshot,
) -> tuple[DecisionStatus, str] | None:
    market = snapshot.maker.runtime.market(snapshot.market_id)
    book = snapshot.maker.runtime.book(snapshot.exchange_id)
    if market is None or book is None:
        return (DecisionStatus.NOT_READY, "model_market_or_book_missing")
    age = snapshot.maker.now_monotonic_ns - book.observed_monotonic_ns
    if age < 0 or age > spec.max_input_age_ns:
        return (DecisionStatus.STALE_INPUT, "model_input_stale")
    return None


def _size_and_risk(
    *,
    spec: ModelSpec,
    decision: ModelDecision,
    snapshot: CanonicalShadowSnapshot,
    risk_context: RiskContext | None,
    available_capital: float | None = None,
) -> tuple[SizingResult | None, RiskDecision | None]:
    if spec.capability is ModelCapability.CONTEXT_ONLY:
        return (SizingResult(0, None, "context_only_model"), None)
    sizing = size_model_decision(
        spec=spec,
        decision=decision,
        snapshot=snapshot,
        available_capital=available_capital,
    )
    if not sizing.tradeable or decision.direction is None:
        return (sizing, None)
    if risk_context is None:
        return (sizing, None)

    opportunity = Opportunity(
        family=spec.strategy_family,
        strategy_id=model_strategy_id(spec),
        legs=(
            CandidateLeg(
                exchange_id=snapshot.exchange_id,
                market_id=snapshot.market_id,
                tournament_id=snapshot.tournament_id,
                outcome_side=decision.outcome_side,
                action=decision.direction,
                quantity=sizing.quantity,
                limit_price_ticks=sizing.limit_price_ticks,
            ),
        ),
        gross_edge=decision.signal_value,
        fair_value=decision.fair_value,
        decision_observation_ns=snapshot.observed_monotonic_ns,
        requires_trusted_depth=spec.requires_trusted_depth,
    )
    return (
        sizing,
        evaluate_risk(opportunity, snapshot.maker.runtime, risk_context),
    )


def _candidate_payload(
    *,
    spec: ModelSpec,
    decision: ModelDecision,
    mode: EffectiveModelMode,
    sizing: SizingResult | None,
    risk: RiskDecision | None,
    logical_operation_id: str | None,
) -> dict[str, object]:
    return {
        "model_id": spec.model_id,
        "model_version": spec.model_version,
        "model_source_hash": spec.source_hash,
        "model_mode": mode.value,
        "model_capability": spec.capability.value,
        "decision_kind": decision.kind.value,
        "decision_reason": decision.reason,
        "urgency": decision.urgency,
        "hypothetical_size": None if sizing is None else sizing.quantity,
        "sizing_reason": None if sizing is None else sizing.reason,
        "risk_approved": None if risk is None else risk.approved,
        "risk_reason": None if risk is None else risk.reason,
        "execution_operation_id": logical_operation_id,
        "model_context": dict(decision.context),
    }


def _candidate_output(
    decision: ModelDecision,
    payload: Mapping[str, object],
    *,
    sizing: SizingResult | None,
) -> CandidateOutput:
    economic = decision.kind in {
        ModelDecisionKind.DIRECTIONAL,
        ModelDecisionKind.EXECUTION,
        ModelDecisionKind.QUOTING,
    }
    if decision.kind is ModelDecisionKind.NOT_READY:
        status = DecisionStatus.NOT_READY
        abstain = decision.reason
    elif decision.kind is ModelDecisionKind.NO_TRADE:
        status = DecisionStatus.ABSTAIN
        abstain = decision.reason
    elif economic and sizing is not None and not sizing.tradeable:
        status = DecisionStatus.ABSTAIN
        abstain = sizing.reason
    else:
        status = DecisionStatus.OK
        abstain = None
    return CandidateOutput(
        status=status,
        fair_value=decision.fair_value,
        confidence=decision.confidence,
        direction=(
            None if decision.direction is None else decision.direction.value
        ),
        score=decision.signal_value,
        action_intent=(
            None if decision.direction is None else decision.direction.value
        ),
        abstain_reason=abstain,
        quality_flags=("model-runtime-001",),
        candidate_payload=dict(payload),
    )


def _augment_failure_payload(
    output: CandidateOutput,
    spec: ModelSpec,
    mode: EffectiveModelMode,
) -> CandidateOutput:
    payload = dict(output.candidate_payload)
    payload.update(
        {
            "model_id": spec.model_id,
            "model_version": spec.model_version,
            "model_source_hash": spec.source_hash,
            "model_mode": mode.value,
            "model_capability": spec.capability.value,
        }
    )
    return replace(output, candidate_payload=payload)
