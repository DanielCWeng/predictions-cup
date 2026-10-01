"""Deterministic SUPERVISOR-001 reason/severity rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from predictions_cup.supervisor.contracts import Finding, LaunchGate, Severity
from predictions_cup.supervisor.sources import SourceCollection

_GIB = 1024**3
_MIB = 1024**2


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    disk_warn_fraction: float = 0.65
    disk_critical_fraction: float = 0.80
    disk_emergency_fraction: float = 0.90
    disk_warn_free_bytes: int = 8 * _GIB
    disk_critical_free_bytes: int = 4 * _GIB
    disk_emergency_free_bytes: int = 2 * _GIB
    disk_growth_warn_bytes_per_min: float = 256 * _MIB
    disk_growth_critical_bytes_per_min: float = 512 * _MIB
    service_memory_warn_bytes: int = 650 * _MIB
    service_memory_critical_bytes: int = 900 * _MIB
    service_memory_growth_warn_mb_per_min: float = 35.0
    service_memory_growth_critical_mb_per_min: float = 80.0
    exposure_warn_fraction: float = 0.80
    concentration_warn_fraction: float = 0.50
    max_gross_exposure: float | None = None
    max_per_market_exposure: float | None = None
    max_tournament_exposure: float | None = None
    max_adverse_selection: float | None = None
    min_realised_spread: float | None = None
    min_post_fill_markout: float | None = None
    min_economic_samples: int = 20


def evaluate(
    collection: SourceCollection,
    policy: SupervisorPolicy,
    *,
    memory_growth_mb_per_min: Mapping[str, float] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    memory_growth = memory_growth_mb_per_min or {}

    for source in collection.statuses:
        if not source.required:
            continue
        source_code = source.source_id.upper()
        if not source.available:
            findings.append(
                Finding(
                    f"SOURCE_{source_code}_MISSING",
                    Severity.CRITICAL,
                    f"Required source {source.source_id} is missing",
                    {"reason": source.reason},
                )
            )
        elif not source.valid:
            findings.append(
                Finding(
                    f"SOURCE_{source_code}_INVALID",
                    Severity.CRITICAL,
                    f"Required source {source.source_id} is invalid",
                    {"reason": source.reason},
                )
            )
        elif not source.fresh:
            findings.append(
                Finding(
                    f"SOURCE_{source_code}_STALE",
                    Severity.CRITICAL,
                    f"Required source {source.source_id} is stale",
                    {"reason": source.reason, "age_seconds": source.age_seconds},
                )
            )

    _system_findings(collection.sections.get("system"), policy, memory_growth, findings)
    _clock_findings(collection.sections.get("clock"), findings)
    _capture_findings("SIG", collection.sections.get("sig_capture"), findings)
    _capture_findings("POLYMARKET", collection.sections.get("polymarket_capture"), findings)
    _observe_findings(collection.sections.get("observe"), findings)
    _risk_findings(collection.sections.get("risk"), policy, findings)
    _execution_findings(collection.sections.get("execution"), findings)
    _shadow_findings(collection.sections.get("shadow"), findings)
    _live_learn_findings(collection.sections.get("live_learn"), policy, findings)

    findings.sort(key=lambda item: (severity_rank(item.severity), item.code), reverse=True)
    return tuple(findings)


def severity_and_gate(findings: tuple[Finding, ...]) -> tuple[Severity, LaunchGate]:
    if any(item.severity is Severity.CRITICAL for item in findings):
        return Severity.CRITICAL, LaunchGate.HOLD
    if any(item.severity is Severity.UNKNOWN for item in findings):
        return Severity.UNKNOWN, LaunchGate.HOLD
    if any(item.severity is Severity.WARN for item in findings):
        return Severity.WARN, LaunchGate.PASS
    return Severity.OK, LaunchGate.PASS


def severity_rank(value: Severity) -> int:
    return {
        Severity.OK: 0,
        Severity.WARN: 1,
        Severity.UNKNOWN: 2,
        Severity.CRITICAL: 3,
    }[value]


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _system_findings(
    raw: object,
    policy: SupervisorPolicy,
    memory_growth: Mapping[str, float],
    findings: list[Finding],
) -> None:
    system = _as_dict(raw)
    if system is None:
        return
    services = _as_dict(system.get("services"))
    if services is not None:
        for service, raw_status in sorted(services.items()):
            status = _as_dict(raw_status)
            if status is None:
                continue
            if status.get("active_state") != "active":
                findings.append(
                    Finding(
                        "SERVICE_FAILURE",
                        Severity.CRITICAL,
                        f"Expected service is not active: {service}",
                        {"service": service, "state": status.get("active_state")},
                    )
                )
            memory = _integer(status.get("memory_current_bytes"))
            if memory is not None and memory >= policy.service_memory_critical_bytes:
                findings.append(
                    Finding(
                        "SERVICE_MEMORY_HIGH",
                        Severity.CRITICAL,
                        f"Service memory is above critical threshold: {service}",
                        {"service": service, "memory_bytes": memory},
                    )
                )
            elif memory is not None and memory >= policy.service_memory_warn_bytes:
                findings.append(
                    Finding(
                        "SERVICE_MEMORY_HIGH",
                        Severity.WARN,
                        f"Service memory is above warning threshold: {service}",
                        {"service": service, "memory_bytes": memory},
                    )
                )
            growth = memory_growth.get(service)
            if growth is not None and growth >= policy.service_memory_growth_critical_mb_per_min:
                findings.append(
                    Finding(
                        "SERVICE_MEMORY_GROWTH",
                        Severity.CRITICAL,
                        f"Service memory is growing rapidly: {service}",
                        {"service": service, "growth_mb_per_min": growth},
                    )
                )
            elif growth is not None and growth >= policy.service_memory_growth_warn_mb_per_min:
                findings.append(
                    Finding(
                        "SERVICE_MEMORY_GROWTH",
                        Severity.WARN,
                        f"Service memory growth is elevated: {service}",
                        {"service": service, "growth_mb_per_min": growth},
                    )
                )

    disk = _as_dict(system.get("disk"))
    if disk is None:
        return
    used_fraction = _number(disk.get("used_fraction"))
    free_bytes = _integer(disk.get("free_bytes"))
    if used_fraction is None or free_bytes is None:
        return
    if (
        used_fraction >= policy.disk_emergency_fraction
        or free_bytes <= policy.disk_emergency_free_bytes
    ):
        findings.append(
            Finding(
                "DISK_PRESSURE_EMERGENCY",
                Severity.CRITICAL,
                "Disk headroom is in emergency territory",
                {"used_fraction": used_fraction, "free_bytes": free_bytes},
            )
        )
    elif (
        used_fraction >= policy.disk_critical_fraction
        or free_bytes <= policy.disk_critical_free_bytes
    ):
        findings.append(
            Finding(
                "DISK_PRESSURE_CRITICAL",
                Severity.CRITICAL,
                "Disk headroom is below critical threshold",
                {"used_fraction": used_fraction, "free_bytes": free_bytes},
            )
        )
    elif (
        used_fraction >= policy.disk_warn_fraction
        or free_bytes <= policy.disk_warn_free_bytes
    ):
        findings.append(
            Finding(
                "DISK_PRESSURE_WARN",
                Severity.WARN,
                "Disk headroom is below warning threshold",
                {"used_fraction": used_fraction, "free_bytes": free_bytes},
            )
        )

    growth = _number(disk.get("growth_bytes_per_min"))
    if growth is not None and growth > 0:
        minutes_to_full = free_bytes / growth
        if growth >= policy.disk_growth_critical_bytes_per_min:
            findings.append(
                Finding(
                    "DISK_GROWTH_CRITICAL",
                    Severity.CRITICAL,
                    "Disk usage is growing at a critical rate",
                    {
                        "growth_bytes_per_min": growth,
                        "minutes_to_full_at_current_rate": minutes_to_full,
                    },
                )
            )
        elif growth >= policy.disk_growth_warn_bytes_per_min:
            findings.append(
                Finding(
                    "DISK_GROWTH_WARN",
                    Severity.WARN,
                    "Disk usage is growing rapidly",
                    {
                        "growth_bytes_per_min": growth,
                        "minutes_to_full_at_current_rate": minutes_to_full,
                    },
                )
            )


def _clock_findings(raw: object, findings: list[Finding]) -> None:
    clock = _as_dict(raw)
    if clock is None:
        return
    if clock.get("ntp_synchronized") is not True:
        findings.append(
            Finding(
                "CLOCK_NOT_SYNCHRONIZED",
                Severity.CRITICAL,
                "Host NTP synchronization is not confirmed",
                {"probe_error": clock.get("probe_error")},
            )
        )


def _capture_findings(venue: str, raw: object, findings: list[Finding]) -> None:
    capture = _as_dict(raw)
    if capture is None:
        return
    storage_failures = _integer(capture.get("storage_failures"))
    dropped_rows = _integer(capture.get("dropped_rows"))
    writer_alive = capture.get("writer_alive")
    if storage_failures is not None and storage_failures > 0:
        findings.append(
            Finding(
                f"CAPTURE_{venue}_STORAGE_FAILURE",
                Severity.CRITICAL,
                f"{venue} capture has storage failures",
                {"storage_failures": storage_failures},
            )
        )
    if dropped_rows is not None and dropped_rows > 0:
        findings.append(
            Finding(
                f"CAPTURE_{venue}_DROPPED_ROWS",
                Severity.CRITICAL,
                f"{venue} capture dropped rows",
                {"dropped_rows": dropped_rows},
            )
        )
    if writer_alive is False:
        findings.append(
            Finding(
                f"CAPTURE_{venue}_WRITER_DEAD",
                Severity.CRITICAL,
                f"{venue} capture writer is not alive",
                {},
            )
        )
    queue_depth = _integer(capture.get("queue_depth"))
    queue_capacity = _integer(capture.get("queue_capacity"))
    if (
        queue_depth is not None
        and queue_capacity is not None
        and queue_capacity > 0
        and queue_depth / queue_capacity >= 0.80
    ):
        findings.append(
            Finding(
                f"CAPTURE_{venue}_QUEUE_PRESSURE",
                Severity.WARN,
                f"{venue} capture queue is above 80% capacity",
                {"queue_depth": queue_depth, "queue_capacity": queue_capacity},
            )
        )

    if venue == "SIG":
        connected = capture.get("connected")
        if connected is False:
            findings.append(
                Finding(
                    "FEED_SIG_DISCONNECTED",
                    Severity.CRITICAL,
                    "SIG realtime connection is disconnected",
                    {},
                )
            )
        rest_age = _number(capture.get("last_rest_reconciliation_age_seconds"))
        if rest_age is not None and rest_age > 60.0:
            findings.append(
                Finding(
                    "FEED_SIG_RECONCILIATION_STALE",
                    Severity.CRITICAL,
                    "SIG authoritative REST reconciliation is stale",
                    {"last_rest_reconciliation_age_seconds": rest_age},
                )
            )

    if venue == "POLYMARKET":
        connected = capture.get("websocket_connected")
        if connected is False:
            findings.append(
                Finding(
                    "FEED_POLYMARKET_DISCONNECTED",
                    Severity.CRITICAL,
                    "Polymarket websocket is disconnected",
                    {"last_reconnect_reason": capture.get("last_reconnect_reason")},
                )
            )
        message_age = _number(capture.get("last_message_age_seconds"))
        if message_age is not None and message_age > 30.0:
            findings.append(
                Finding(
                    "FEED_POLYMARKET_STALE",
                    Severity.CRITICAL,
                    "Polymarket websocket messages are stale",
                    {"last_message_age_seconds": message_age},
                )
            )
    if venue == "SIG" and capture.get("health_surface") == "activity_fallback":
        findings.append(
            Finding(
                "SIG_STRUCTURED_HEALTH_UNAVAILABLE",
                Severity.WARN,
                "SIG capture is fresh, but structured CAPTURE-001 health is unavailable",
                {"health_surface_reason": capture.get("health_surface_reason")},
            )
        )


def _observe_findings(raw: object, findings: list[Finding]) -> None:
    observe = _as_dict(raw)
    if observe is None:
        return
    state = observe.get("state")
    if state in {"DEGRADED", "BLOCKED"}:
        findings.append(
            Finding(
                "OBSERVE_UNHEALTHY",
                Severity.WARN if state == "DEGRADED" else Severity.CRITICAL,
                f"OBSERVE status is {state}",
                {"reason": observe.get("reason")},
            )
        )
    health = _as_dict(observe.get("health"))
    if health is None:
        return
    emitter = _as_dict(health.get("observe"))
    if emitter is not None:
        dropped = _integer(emitter.get("dropped"))
        failures = _integer(emitter.get("sink_failures"))
        if dropped is not None and dropped > 0:
            findings.append(
                Finding(
                    "OBSERVE_DROPPED",
                    Severity.WARN,
                    "OBSERVE dropped instrumentation events",
                    {"dropped": dropped},
                )
            )
        if failures is not None and failures > 0:
            findings.append(
                Finding(
                    "OBSERVE_SINK_FAILURE",
                    Severity.WARN,
                    "OBSERVE sink has failures",
                    {"sink_failures": failures},
                )
            )


def _risk_findings(raw: object, policy: SupervisorPolicy, findings: list[Finding]) -> None:
    risk = _as_dict(raw)
    if risk is None or "session_id" not in risk:
        return
    global_halt = _as_dict(risk.get("global_halt"))
    if global_halt is not None and global_halt.get("active") is True:
        findings.append(
            Finding(
                "RISK_GLOBAL_HALT",
                Severity.CRITICAL,
                "RISK-002 global halt is active",
                {"reason": global_halt.get("reason")},
            )
        )
    strategy_halts = risk.get("strategy_halts")
    if isinstance(strategy_halts, list):
        active = [
            item
            for item in strategy_halts
            if isinstance(item, dict) and item.get("active") is True
        ]
        if active:
            findings.append(
                Finding(
                    "RISK_STRATEGY_HALT",
                    Severity.WARN,
                    "One or more strategy halts are active",
                    {"active_halts": len(active)},
                )
            )
    if risk.get("account_trusted") is False or risk.get("marks_trusted") is False:
        findings.append(
            Finding(
                "RISK_STATE_UNTRUSTED",
                Severity.CRITICAL,
                "RISK-002 account or marks are untrusted",
                {
                    "account_trusted": risk.get("account_trusted"),
                    "marks_trusted": risk.get("marks_trusted"),
                },
            )
        )
    if risk.get("reconciliation_complete") is False:
        findings.append(
            Finding(
                "RISK_RECONCILIATION_INCOMPLETE",
                Severity.CRITICAL,
                "RISK-002 reconciliation is incomplete",
                {},
            )
        )
    exposure = _as_dict(risk.get("exposure"))
    if exposure is None:
        return
    if exposure.get("trusted") is False:
        findings.append(
            Finding(
                "EXPOSURE_UNTRUSTED",
                Severity.CRITICAL,
                "Risk exposure snapshot is untrusted",
                {},
            )
        )
    gross = _number(exposure.get("gross_exposure"))
    _limit_finding(
        "EXPOSURE_GROSS_NEAR_LIMIT",
        gross,
        policy.max_gross_exposure,
        policy,
        findings,
        "Gross exposure",
    )
    markets = exposure.get("by_market")
    if isinstance(markets, list):
        market_values: list[float] = []
        for item in markets:
            bucket = _as_dict(item)
            if bucket is None:
                continue
            value = _number(bucket.get("exposure"))
            if value is not None:
                market_values.append(value)
                _limit_finding(
                    "EXPOSURE_MARKET_NEAR_LIMIT",
                    value,
                    policy.max_per_market_exposure,
                    policy,
                    findings,
                    "Per-market exposure",
                    extra={"market": bucket.get("key")},
                )
        if gross is not None and gross > 0 and market_values:
            concentration = max(market_values) / gross
            if concentration >= policy.concentration_warn_fraction:
                findings.append(
                    Finding(
                        "EXPOSURE_CONCENTRATED",
                        Severity.WARN,
                        "A large share of gross exposure is concentrated in one market",
                        {"top_market_share": concentration},
                    )
                )
    tournaments = exposure.get("by_tournament")
    if isinstance(tournaments, list):
        for item in tournaments:
            bucket = _as_dict(item)
            if bucket is None:
                continue
            value = _number(bucket.get("exposure"))
            if value is not None:
                _limit_finding(
                    "EXPOSURE_TOURNAMENT_NEAR_LIMIT",
                    value,
                    policy.max_tournament_exposure,
                    policy,
                    findings,
                    "Tournament exposure",
                    extra={"tournament": bucket.get("key")},
                )


def _limit_finding(
    code: str,
    value: float | None,
    limit: float | None,
    policy: SupervisorPolicy,
    findings: list[Finding],
    label: str,
    *,
    extra: Mapping[str, object] | None = None,
) -> None:
    if value is None or limit is None or limit <= 0:
        return
    ratio = value / limit
    if ratio < policy.exposure_warn_fraction:
        return
    evidence: dict[str, object] = {"value": value, "limit": limit, "fraction": ratio}
    if extra is not None:
        evidence.update(extra)
    findings.append(
        Finding(
            code,
            Severity.CRITICAL if ratio >= 1.0 else Severity.WARN,
            f"{label} is {'at/over' if ratio >= 1.0 else 'near'} configured limit",
            evidence,
        )
    )


def _execution_findings(raw: object, findings: list[Finding]) -> None:
    execution = _as_dict(raw)
    if execution is None:
        return
    unresolved = execution.get("unresolved")
    if not isinstance(unresolved, list) or not unresolved:
        return
    states = {
        str(item.get("lifecycle_state"))
        for item in unresolved
        if isinstance(item, dict)
    }
    if "UNCERTAIN" in states:
        findings.append(
            Finding(
                "EXECUTION_UNCERTAIN",
                Severity.CRITICAL,
                "Execution journal contains UNCERTAIN operations",
                {
                    "count": sum(
                        isinstance(item, dict)
                        and item.get("lifecycle_state") == "UNCERTAIN"
                        for item in unresolved
                    )
                },
            )
        )
    if "RECONCILING" in states:
        findings.append(
            Finding(
                "EXECUTION_RECONCILING",
                Severity.WARN,
                "Execution journal contains RECONCILING operations",
                {},
            )
        )
    if states & {"PENDING", "CANCEL_PENDING"}:
        findings.append(
            Finding(
                "EXECUTION_UNRESOLVED",
                Severity.WARN,
                "Execution journal contains unresolved operations",
                {"states": sorted(states)},
            )
        )
    if execution.get("wall_clock_age_available") is False:
        findings.append(
            Finding(
                "EXECUTION_AGE_UNAVAILABLE",
                Severity.WARN,
                "Wall-clock age of unresolved operations cannot be proven",
                {},
            )
        )


def _shadow_findings(raw: object, findings: list[Finding]) -> None:
    shadow = _as_dict(raw)
    if shadow is None:
        return
    last_event = _as_dict(shadow.get("last_event"))
    if last_event is None:
        return
    status = last_event.get("decision_status")
    if status in {"TIMEOUT", "EXCEPTION", "INVALID_OUTPUT"}:
        findings.append(
            Finding(
                "SHADOW_CANDIDATE_FAILURE",
                Severity.WARN,
                "Latest SHADOW event reports candidate failure",
                {"decision_status": status},
            )
        )


def _live_learn_findings(
    raw: object,
    policy: SupervisorPolicy,
    findings: list[Finding],
) -> None:
    report = _as_dict(raw)
    if report is None:
        return
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        return
    for item in candidates:
        row = _as_dict(item)
        if row is None:
            continue
        scored = _integer(row.get("scored_outcomes"))
        if scored is None or scored < policy.min_economic_samples:
            continue
        metrics = _as_dict(row.get("metrics"))
        if metrics is None:
            continue
        adverse = _number(metrics.get("adverse_selection"))
        realised = _number(metrics.get("realised_spread"))
        markout = _number(metrics.get("post_fill_markout"))
        candidate = row.get("candidate_id")
        if (
            policy.max_adverse_selection is not None
            and adverse is not None
            and adverse > policy.max_adverse_selection
        ):
            findings.append(
                Finding(
                    "MAKER_ADVERSE_SELECTION",
                    Severity.WARN,
                    "Adverse-selection metric exceeds configured threshold",
                    {"candidate": candidate, "value": adverse, "samples": scored},
                )
            )
        if (
            policy.min_realised_spread is not None
            and realised is not None
            and realised < policy.min_realised_spread
        ):
            findings.append(
                Finding(
                    "MAKER_REALISED_SPREAD_DETERIORATION",
                    Severity.WARN,
                    "Realised spread is below configured threshold",
                    {"candidate": candidate, "value": realised, "samples": scored},
                )
            )
        if (
            policy.min_post_fill_markout is not None
            and markout is not None
            and markout < policy.min_post_fill_markout
        ):
            findings.append(
                Finding(
                    "MAKER_MARKOUT_DETERIORATION",
                    Severity.WARN,
                    "Post-fill markout is below configured threshold",
                    {"candidate": candidate, "value": markout, "samples": scored},
                )
            )
