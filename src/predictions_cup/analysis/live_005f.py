"""005F-LIVE-001 future state-transfer diagnostics over exact accepted state."""

from __future__ import annotations

import json
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
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.shadow.frozen_runtime import (
    Hazard005FFeatureVector,
    Hazard005FRegimeProvider,
)
from predictions_cup.shadow.live_005f import Live005FStateProvider
from predictions_cup.shadow.replay import load_persisted_snapshots

STATE_TRANSFER_VERSION = "005f-live-transfer-001-v1"
STANDARD_HORIZONS = (1, 5, 15, 30, 60, 300)


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


def replay_state_features(
    *,
    provider: Live005FStateProvider,
    snapshots: Sequence[CanonicalShadowSnapshot],
) -> dict[str, Hazard005FFeatureVector]:
    """Replay persisted observable snapshots into the exact accepted state provider."""
    features: dict[str, Hazard005FFeatureVector] = {}
    for snapshot in snapshots:
        vector = provider.feature_vector(snapshot)
        if vector is not None:
            features[snapshot.snapshot_id] = vector
    return features


def build_state_transfer_samples(
    *,
    provider: Live005FStateProvider,
    snapshots: Sequence[CanonicalShadowSnapshot],
    outcomes: Sequence[DecisionOutcome],
    regime_provider: Hazard005FRegimeProvider | None = None,
) -> tuple[StateTransferSample, ...]:
    """Join exact 005F state to actual future MAKE economics without model scoring."""
    snapshot_by_id = {snapshot.snapshot_id: snapshot for snapshot in snapshots}
    feature_by_snapshot = replay_state_features(
        provider=provider,
        snapshots=snapshots,
    )
    samples: list[StateTransferSample] = []
    for outcome in outcomes:
        if (
            outcome.strategy_family != "MAKE"
            or outcome.outcome_status is not OutcomeStatus.MATURED_SCORED
            or outcome.horizon_seconds not in STANDARD_HORIZONS
        ):
            continue
        snapshot = snapshot_by_id.get(outcome.input_snapshot_id)
        vector = feature_by_snapshot.get(outcome.input_snapshot_id)
        if snapshot is None or vector is None:
            continue
        age = float(vector.values["genuine_age_s"])
        regime = outcome.dimensions.get("regime")
        if regime_provider is not None:
            provided = regime_provider.regime(snapshot)
            if provided is not None:
                regime = provided
        markout = _optional_metric(
            outcome.metric_values,
            "post_fill_markout",
        )
        samples.append(
            StateTransferSample(
                decision_id=outcome.decision_id,
                market_id=snapshot.market_id,
                exchange_id=snapshot.exchange_id,
                horizon_seconds=outcome.horizon_seconds,
                state_bucket=genuine_age_bucket(age),
                regime=regime,
                genuine_age_s=age,
                genuine_15=float(vector.values["genuine_15"]),
                genuine_60=float(vector.values["genuine_60"]),
                abs_ret_15=float(vector.values["abs_ret_15"]),
                rv_60=float(vector.values["rv_60"]),
                state_version=provider.version,
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
                evidence_refs=outcome.evidence_source_ids,
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
    provider: Live005FStateProvider,
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
    }
    current = {
        "analysis_id": "005F-LIVE-001",
        "analysis_version": STATE_TRANSFER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "config_hash": canonical_config_hash(config),
        "provider": provider.status(),
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
    provider: Live005FStateProvider,
    shadow_journal_path: Path,
    live_learn_outcome_path: Path,
    output_root: Path,
    regime_provider: Hazard005FRegimeProvider | None = None,
) -> dict[str, object]:
    """Replay captured SHADOW boundaries and future outcomes into state-only 005F evidence."""
    if provider.grid_origin_ns is None:
        current = {
            "analysis_id": "005F-LIVE-001",
            "analysis_version": STATE_TRANSFER_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "NOT_READY",
            "reasons": ["GRID_ORIGIN_NOT_PREDECLARED"],
            "provider": provider.status(),
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "current.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return current
    if not shadow_journal_path.exists() or not live_learn_outcome_path.exists():
        current = {
            "analysis_id": "005F-LIVE-001",
            "analysis_version": STATE_TRANSFER_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "NOT_READY",
            "reasons": ["SHADOW_OR_LIVE_LEARN_EVIDENCE_UNAVAILABLE"],
            "provider": provider.status(),
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "current.json").write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return current

    snapshots = load_persisted_snapshots(shadow_journal_path)
    outcomes = _load_outcomes(live_learn_outcome_path)
    samples = build_state_transfer_samples(
        provider=provider,
        snapshots=snapshots,
        outcomes=outcomes,
        regime_provider=regime_provider,
    )
    summaries = summarize_state_transfer(samples)
    return write_state_transfer(
        output_root=output_root,
        provider=provider,
        samples=samples,
        summaries=summaries,
    )
