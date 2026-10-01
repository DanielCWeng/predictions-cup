"""005F-LIVE-001 future state-transfer diagnostics over exact accepted state."""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from predictions_cup.analysis.evidence import canonical_config_hash
from predictions_cup.live_learn.contracts import (
    DecisionOutcome,
    OutcomeStatus,
    outcome_from_record,
)
from predictions_cup.shadow.persistence import read_jsonl_records

STATE_TRANSFER_VERSION = "005f-live-transfer-001-v2"
STANDARD_HORIZONS = (1, 5, 15, 30, 60, 300)


@dataclass(frozen=True, slots=True)
class Persisted005FState:
    input_snapshot_id: str
    state_decision_id: str
    market_id: str
    exchange_id: str
    regime: str | None
    provider_id: str
    provider_version: str
    source_version: str
    grid_time_ns: int
    observed_monotonic_ns: int
    values: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class StateTransferSample:
    decision_id: str
    market_id: str
    exchange_id: str
    horizon_seconds: int
    state_bucket: str
    regime: str | None
    genuine_age_s: float
    genuine_15: float
    genuine_60: float
    abs_ret_15: float
    rv_60: float
    state_version: str
    fill_rate: float | None
    markout: float | None
    adverse_selection: float | None
    spread_capture: float | None
    realised_pnl: float | None
    net_economic_metric: float | None
    net_economic_metric_name: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "market_id": self.market_id,
            "exchange_id": self.exchange_id,
            "horizon_seconds": self.horizon_seconds,
            "state_bucket": self.state_bucket,
            "regime": self.regime,
            "genuine_age_s": self.genuine_age_s,
            "genuine_15": self.genuine_15,
            "genuine_60": self.genuine_60,
            "abs_ret_15": self.abs_ret_15,
            "rv_60": self.rv_60,
            "state_version": self.state_version,
            "fill_rate": self.fill_rate,
            "markout": self.markout,
            "adverse_selection": self.adverse_selection,
            "spread_capture": self.spread_capture,
            "realised_pnl": self.realised_pnl,
            "net_economic_metric": self.net_economic_metric,
            "net_economic_metric_name": self.net_economic_metric_name,
            "evidence_refs": list(self.evidence_refs),
        }


def genuine_age_bucket(age_s: float) -> str:
    """Predeclared future-transfer age buckets; never tuned on outcomes."""
    if age_s < 0.0:
        raise ValueError("genuine_age_s cannot be negative")
    if age_s < 5.0:
        return "AGE_0_5"
    if age_s < 15.0:
        return "AGE_5_15"
    if age_s < 60.0:
        return "AGE_15_60"
    return "AGE_60_PLUS"


def _optional_metric(
    metrics: Mapping[str, float],
    key: str,
) -> float | None:
    value = metrics.get(key)
    return None if value is None else float(value)


def _load_outcomes(path: Path) -> tuple[DecisionOutcome, ...]:
    outcomes: list[DecisionOutcome] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if not isinstance(value, dict):
                continue
            record = cast(dict[str, object], value)
            outcomes.append(outcome_from_record(record))
    return tuple(outcomes)


def load_persisted_005f_states(
    path: Path,
) -> dict[str, Persisted005FState]:
    """Load exact decision-time 005F state persisted by the live SHADOW candidate."""
    required = (
        "genuine_age_s",
        "genuine_15",
        "genuine_60",
        "abs_ret_15",
        "rv_60",
    )
    states: dict[str, Persisted005FState] = {}
    for record in read_jsonl_records(path):
        if (
            record.get("event_type") != "decision"
            or record.get("candidate_id") != "experiment-005f-hazard"
        ):
            continue
        payload = record.get("candidate_payload")
        if not isinstance(payload, dict):
            continue
        raw_state = payload.get("005f_state")
        if not isinstance(raw_state, dict):
            continue
        raw_features = raw_state.get("features")
        if not isinstance(raw_features, dict):
            continue

        values: dict[str, float] = {}
        valid = True
        for name in required:
            raw = raw_features.get(name)
            if isinstance(raw, bool) or raw is None:
                valid = False
                break
            try:
                value = float(str(raw))
            except (TypeError, ValueError):
                valid = False
                break
            if not math.isfinite(value):
                valid = False
                break
            values[name] = value
        if not valid:
            continue

        snapshot_id = record.get("input_snapshot_id")
        decision_id = record.get("decision_id")
        market_id = record.get("market_id")
        exchange_id = record.get("exchange_id")
        provider_id = raw_state.get("provider_id")
        provider_version = raw_state.get("provider_version")
        source_version = raw_state.get("source_version")
        grid_time_ns = raw_state.get("grid_time_ns")
        observed_monotonic_ns = raw_state.get("observed_monotonic_ns")
        if (
            not isinstance(snapshot_id, str)
            or not isinstance(decision_id, str)
            or not isinstance(market_id, str)
            or not isinstance(exchange_id, str)
            or not isinstance(provider_id, str)
            or not isinstance(provider_version, str)
            or not isinstance(source_version, str)
            or isinstance(grid_time_ns, bool)
            or not isinstance(grid_time_ns, int)
            or isinstance(observed_monotonic_ns, bool)
            or not isinstance(observed_monotonic_ns, int)
        ):
            continue
        raw_regime = raw_state.get("regime")
        regime = raw_regime if isinstance(raw_regime, str) else None
        state = Persisted005FState(
            input_snapshot_id=snapshot_id,
            state_decision_id=decision_id,
            market_id=market_id,
            exchange_id=exchange_id,
            regime=regime,
            provider_id=provider_id,
            provider_version=provider_version,
            source_version=source_version,
            grid_time_ns=grid_time_ns,
            observed_monotonic_ns=observed_monotonic_ns,
            values=values,
        )
        previous = states.get(snapshot_id)
        if previous is not None and previous != state:
            raise ValueError(
                "conflicting persisted 005F state for one SHADOW snapshot"
            )
        states[snapshot_id] = state
    return states


def build_state_transfer_samples(
    *,
    states: Mapping[str, Persisted005FState],
    outcomes: Sequence[DecisionOutcome],
) -> tuple[StateTransferSample, ...]:
    """Join persisted exact 005F decision state to future MAKE economics."""
    samples: list[StateTransferSample] = []
    for outcome in outcomes:
        if (
            outcome.strategy_family != "MAKE"
            or outcome.outcome_status is not OutcomeStatus.MATURED_SCORED
            or outcome.horizon_seconds not in STANDARD_HORIZONS
        ):
            continue
        state = states.get(outcome.input_snapshot_id)
        if state is None:
            continue
        age = state.values["genuine_age_s"]
        markout = _optional_metric(
            outcome.metric_values,
            "post_fill_markout",
        )
        samples.append(
            StateTransferSample(
                decision_id=outcome.decision_id,
                market_id=state.market_id,
                exchange_id=state.exchange_id,
                horizon_seconds=outcome.horizon_seconds,
                state_bucket=genuine_age_bucket(age),
                regime=state.regime,
                genuine_age_s=age,
                genuine_15=state.values["genuine_15"],
                genuine_60=state.values["genuine_60"],
                abs_ret_15=state.values["abs_ret_15"],
                rv_60=state.values["rv_60"],
                state_version=state.provider_version,
                fill_rate=_optional_metric(outcome.metric_values, "fill_rate"),
                markout=markout,
                adverse_selection=_optional_metric(
                    outcome.metric_values,
                    "adverse_selection",
                ),
                spread_capture=_optional_metric(
                    outcome.metric_values,
                    "spread_capture",
                ),
                realised_pnl=None,
                net_economic_metric=markout,
                net_economic_metric_name="diagnostic_post_fill_markout",
                evidence_refs=(
                    *outcome.evidence_source_ids,
                    f"shadow-decision:{state.state_decision_id}",
                ),
            )
        )
    return tuple(samples)


def _median(values: Sequence[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def _metric_values(
    samples: Sequence[StateTransferSample],
    field: str,
) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = getattr(sample, field)
        if isinstance(value, float):
            values.append(value)
    return values


def _bootstrap_median(
    samples: Sequence[StateTransferSample],
    field: str,
) -> dict[str, object]:
    """Dependence-aware bootstrap clustered by decision, not outcome row."""
    by_decision: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        value = getattr(sample, field)
        if isinstance(value, float):
            by_decision[sample.decision_id].append(value)
    decisions = sorted(by_decision)
    pooled = [
        value
        for decision_id in decisions
        for value in by_decision[decision_id]
    ]
    point = _median(pooled)
    if len(decisions) < 10 or point is None:
        return {
            "point_estimate": point,
            "interval": None,
            "method": None,
            "cluster_unit": "decision_id",
            "support": len(decisions),
            "reason": "INSUFFICIENT_INDEPENDENT_EVENTS",
        }
    rng = random.Random(0)
    boot: list[float] = []
    for _ in range(500):
        selected = rng.choices(decisions, k=len(decisions))
        values = [
            value
            for decision_id in selected
            for value in by_decision[decision_id]
        ]
        median = _median(values)
        if median is not None:
            boot.append(median)
    boot.sort()
    return {
        "point_estimate": point,
        "interval": [boot[11], boot[487]],
        "method": "decision_cluster_bootstrap_percentile",
        "cluster_unit": "decision_id",
        "support": len(decisions),
        "reason": None,
    }


def summarize_state_transfer(
    samples: Sequence[StateTransferSample],
) -> list[dict[str, object]]:
    """Persist every declared state bucket; never select only the profitable bucket."""
    grouped: dict[
        tuple[str, str, str | None, int],
        list[StateTransferSample],
    ] = defaultdict(list)
    for sample in samples:
        grouped[
            (
                sample.state_bucket,
                sample.market_id,
                sample.regime,
                sample.horizon_seconds,
            )
        ].append(sample)

    rows: list[dict[str, object]] = []
    for (
        state_bucket,
        market_id,
        regime,
        horizon_seconds,
    ), group in sorted(grouped.items(), key=lambda item: str(item[0])):
        decisions = {sample.decision_id for sample in group}
        rows.append(
            {
                "state_bucket": state_bucket,
                "market_id": market_id,
                "regime": regime,
                "horizon_seconds": horizon_seconds,
                "sample_count": len(group),
                "independent_event_count": len(decisions),
                "fill_rate": _median(_metric_values(group, "fill_rate")),
                "markout": _median(_metric_values(group, "markout")),
                "adverse_selection": _median(
                    _metric_values(group, "adverse_selection")
                ),
                "spread_capture": _median(
                    _metric_values(group, "spread_capture")
                ),
                "realised_pnl": None,
                "net_economic_metric": _median(
                    _metric_values(group, "net_economic_metric")
                ),
                "net_economic_metric_name": "diagnostic_post_fill_markout",
                "uncertainty": _bootstrap_median(group, "markout"),
                "status": (
                    "INSUFFICIENT_EVIDENCE"
                    if len(decisions) < 5
                    else "DESCRIPTIVE_ONLY"
                ),
                "reasons": [
                    "STATE_ONLY_NOT_FROZEN_005F_MODEL",
                    "RISK_002_ACCOUNTING_PNL_NOT_JOINED",
                    *(
                        ["REGIME_UNAVAILABLE"]
                        if regime is None
                        else []
                    ),
                ],
            }
        )
    return rows


def write_state_transfer(
    *,
    output_root: Path,
    states: Mapping[str, Persisted005FState],
    samples: Sequence[StateTransferSample],
    summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Write current JSON plus rectangular/append-only future-transfer evidence."""
    output_root.mkdir(parents=True, exist_ok=True)
    sample_rows = [sample.to_dict() for sample in samples]
    summary_rows = [dict(row) for row in summaries]
    if sample_rows:
        pq.write_table(
            pa.Table.from_pylist(sample_rows),
            output_root / "state_samples.parquet",
        )
    if summary_rows:
        pq.write_table(
            pa.Table.from_pylist(summary_rows),
            output_root / "state_transfer.parquet",
        )
        with (output_root / "state_transfer.jsonl").open(
            "a",
            encoding="utf-8",
        ) as handle:
            for row in summary_rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

    config = {
        "analysis_version": STATE_TRANSFER_VERSION,
        "state_buckets": ["AGE_0_5", "AGE_5_15", "AGE_15_60", "AGE_60_PLUS"],
        "horizons_seconds": list(STANDARD_HORIZONS),
        "directional_conversion": False,
        "frozen_model_scoring": False,
        "state_source": "persisted_shadow_candidate_decision",
    }
    provider_versions = sorted(
        {state.provider_version for state in states.values()}
    )
    source_versions = sorted(
        {state.source_version for state in states.values()}
    )
    current = {
        "analysis_id": "005F-LIVE-001",
        "analysis_version": STATE_TRANSFER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "config_hash": canonical_config_hash(config),
        "state_source": {
            "candidate_id": "experiment-005f-hazard",
            "candidate_payload_field": "005f_state",
            "persisted_state_count": len(states),
            "provider_versions": provider_versions,
            "source_versions": source_versions,
            "reconstruction": False,
        },
        "sample_count": len(samples),
        "independent_event_count": len(
            {sample.decision_id for sample in samples}
        ),
        "summaries": summary_rows,
        "claim_boundary": {
            "may_claim": (
                "future exact-state association with observed maker diagnostics"
            ),
            "may_not_claim": [
                "frozen 005F model score when artifacts are unavailable",
                "directional BUY/SELL signal",
                "causal toxicity effect",
                "authoritative realised P&L without RISK-002 accounting join",
            ],
        },
    }
    temporary = output_root / "current.json.tmp"
    temporary.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_root / "current.json")
    return current


def analyze_state_transfer_from_paths(
    *,
    shadow_journal_path: Path,
    live_learn_outcome_path: Path,
    output_root: Path,
) -> dict[str, object]:
    """Join persisted exact decision-time 005F state to future MAKE outcomes."""
    if not shadow_journal_path.exists() or not live_learn_outcome_path.exists():
        current: dict[str, object] = {
            "analysis_id": "005F-LIVE-001",
            "analysis_version": STATE_TRANSFER_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "NOT_READY",
            "reasons": ["SHADOW_OR_LIVE_LEARN_EVIDENCE_UNAVAILABLE"],
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "current.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return current

    states = load_persisted_005f_states(shadow_journal_path)
    if not states:
        current = {
            "analysis_id": "005F-LIVE-001",
            "analysis_version": STATE_TRANSFER_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "NOT_READY",
            "reasons": ["PERSISTED_EXACT_005F_STATE_UNAVAILABLE"],
            "state_source": "experiment-005f-hazard.candidate_payload.005f_state",
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "current.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return current

    outcomes = _load_outcomes(live_learn_outcome_path)
    samples = build_state_transfer_samples(
        states=states,
        outcomes=outcomes,
    )
    summaries = summarize_state_transfer(samples)
    return write_state_transfer(
        output_root=output_root,
        states=states,
        samples=samples,
        summaries=summaries,
    )
