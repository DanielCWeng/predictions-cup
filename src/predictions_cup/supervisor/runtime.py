"""Main deterministic SUPERVISOR-001 loop."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC

from predictions_cup.config import AppSettings
from predictions_cup.supervisor.bundles import BundleWriter
from predictions_cup.supervisor.config import SupervisorRuntimeConfig
from predictions_cup.supervisor.contracts import (
    ActionCode,
    Finding,
    SupervisorSnapshot,
    deterministic_id,
    utc_now,
)
from predictions_cup.supervisor.persistence import SupervisorStore
from predictions_cup.supervisor.remediation import RemediationExecutor
from predictions_cup.supervisor.rules import evaluate, severity_and_gate
from predictions_cup.supervisor.sources import SourceCollection, SupervisorSources


class ResourceGrowthTracker:
    def __init__(
        self,
        window_seconds: float = 300.0,
        minimum_elapsed_seconds: float = 180.0,
    ) -> None:
        if minimum_elapsed_seconds <= 0 or minimum_elapsed_seconds > window_seconds:
            raise ValueError("minimum elapsed must be positive and within the window")
        self.window_seconds = window_seconds
        self.minimum_elapsed_seconds = minimum_elapsed_seconds
        self._memory_history: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._memory_pid: dict[str, str] = {}
        self._disk_history: deque[tuple[float, int]] = deque()

    def update(self, system: object) -> tuple[dict[str, float], float | None]:
        now = time.monotonic()
        memory_growth: dict[str, float] = {}
        disk_growth: float | None = None
        if not isinstance(system, dict):
            return memory_growth, disk_growth

        services = system.get("services")
        if isinstance(services, dict):
            for service, raw in services.items():
                if not isinstance(service, str) or not isinstance(raw, dict):
                    continue
                memory = raw.get("memory_current_bytes")
                if isinstance(memory, bool) or not isinstance(memory, int):
                    continue
                pid = str(raw.get("main_pid") or "")
                history = self._memory_history[service]
                if self._memory_pid.get(service) != pid:
                    history.clear()
                    self._memory_pid[service] = pid
                history.append((now, memory))
                while history and now - history[0][0] > self.window_seconds:
                    history.popleft()
                if len(history) < 2:
                    continue
                elapsed = history[-1][0] - history[0][0]
                if elapsed < self.minimum_elapsed_seconds:
                    continue
                delta_mb = (history[-1][1] - history[0][1]) / (1024**2)
                memory_growth[service] = delta_mb / (elapsed / 60.0)

        disk = system.get("disk")
        if isinstance(disk, dict):
            used = disk.get("used_bytes")
            if not isinstance(used, bool) and isinstance(used, int):
                self._disk_history.append((now, used))
                while (
                    self._disk_history
                    and now - self._disk_history[0][0] > self.window_seconds
                ):
                    self._disk_history.popleft()
                if len(self._disk_history) >= 2:
                    elapsed = self._disk_history[-1][0] - self._disk_history[0][0]
                    if elapsed >= self.minimum_elapsed_seconds:
                        delta = self._disk_history[-1][1] - self._disk_history[0][1]
                        disk_growth = delta / (elapsed / 60.0)

        return memory_growth, disk_growth


class ServiceRestartTracker:
    """Track systemd restart-count and PID changes over a rolling hour."""

    def __init__(
        self,
        *,
        window_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        max_events_per_service: int = 3,
    ) -> None:
        self.window_seconds = window_seconds
        self._clock = clock
        self._max_events_per_service = max_events_per_service
        self._last_restart_count: dict[str, int] = {}
        self._last_pid: dict[str, str] = {}
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._first_seen: dict[str, float] = {}

    def update(self, system: object) -> dict[str, dict[str, float | int]]:
        now = self._clock()
        if not isinstance(system, dict):
            return {}
        services = system.get("services")
        if not isinstance(services, dict):
            return {}

        result: dict[str, dict[str, float | int]] = {}
        for service, raw in services.items():
            if not isinstance(service, str) or not isinstance(raw, dict):
                continue
            count = raw.get("restart_count")
            if isinstance(count, bool) or not isinstance(count, int):
                count = None
            pid_raw = raw.get("main_pid")
            pid = str(pid_raw) if pid_raw not in (None, "", "0", 0) else ""
            previous_count = self._last_restart_count.get(service)
            previous_pid = self._last_pid.get(service)
            events = self._events[service]
            count_delta = (
                0
                if count is None or previous_count is None or count < previous_count
                else count - previous_count
            )
            pid_changed = bool(pid and previous_pid and pid != previous_pid)
            event_count = max(count_delta, int(pid_changed))
            for _ in range(min(event_count, self._max_events_per_service)):
                events.append(now)
            if count is not None:
                self._last_restart_count[service] = count
            if pid:
                self._last_pid[service] = pid
            self._first_seen.setdefault(service, now)
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            result[service] = {
                "restart_count_last_hour": len(events),
                "restart_tracking_seconds": now - self._first_seen[service],
            }
        return result


class SupervisorRuntime:
    def __init__(self, settings: AppSettings, config: SupervisorRuntimeConfig) -> None:
        self.settings = settings
        self.config = config
        self.sources = SupervisorSources(
            settings,
            repo_root=config.repo_root,
            host_role=config.host_role,
            expected_services=config.expected_services,
        )
        self.store = SupervisorStore(config.output_root)
        self.bundle_writer = BundleWriter(config.output_root)
        self.remediator = RemediationExecutor(config.remediation)
        self.resource_growth = ResourceGrowthTracker()
        self.service_restarts = ServiceRestartTracker(
            max_events_per_service=config.policy.service_max_restarts_per_hour + 1
        )
        self._last_snapshot_write_at = 0.0
        self._last_event_signature: tuple[object, ...] | None = None
        self._last_event_bundle_at = 0.0
        self._last_hour_key: str | None = None
        self._last_bundle_id: str | None = None

    def run_once(self) -> SupervisorSnapshot:
        collection = self.sources.read_all()
        growth, disk_growth = self.resource_growth.update(
            collection.sections.get("system")
        )
        sections = dict(collection.sections)
        system = sections.get("system")
        if isinstance(system, dict):
            system_copy = dict(system)
            restart_counts = self.service_restarts.update(system)
            services = system_copy.get("services")
            if isinstance(services, dict):
                service_copy = dict(services)
                for service, status in service_copy.items():
                    if not isinstance(status, dict):
                        continue
                    restart_state = restart_counts.get(service)
                    if restart_state is not None:
                        status_copy = dict(status)
                        status_copy.update(restart_state)
                        service_copy[service] = status_copy
                system_copy["services"] = service_copy
            disk = system_copy.get("disk")
            if isinstance(disk, dict):
                disk_copy = dict(disk)
                disk_copy["growth_bytes_per_min"] = disk_growth
                system_copy["disk"] = disk_copy
            sections["system"] = system_copy
        sections["derived"] = {
            "memory_growth_mb_per_min": growth,
            "disk_growth_bytes_per_min": disk_growth,
        }
        enriched = SourceCollection(collection.statuses, sections)
        findings = evaluate(
            enriched,
            self.config.policy,
            memory_growth_mb_per_min=growth,
        )
        severity, launch_gate = severity_and_gate(findings)
        now = utc_now()
        semantic = _semantic_payload(
            host_id=self.sources.host_id,
            host_role=self.config.host_role.value,
            git_head=self.sources.git_head,
            collection=enriched,
            findings=findings,
            severity=severity.value,
            launch_gate=launch_gate.value,
        )
        snapshot = SupervisorSnapshot(
            snapshot_id=deterministic_id("supervisor", semantic),
            host_id=self.sources.host_id,
            host_role=self.config.host_role,
            git_head=self.sources.git_head,
            observed_at=now,
            severity=severity,
            launch_gate=launch_gate,
            findings=findings,
            sources=enriched.statuses,
            sections=enriched.sections,
        )
        self.store.publish_latest(snapshot)

        current_mono = time.monotonic()
        if (
            self._last_snapshot_write_at == 0.0
            or current_mono - self._last_snapshot_write_at
            >= self.config.snapshot_interval_seconds
        ):
            self.store.append_snapshot(snapshot)
            self._last_snapshot_write_at = current_mono

        self._handle_transition(snapshot, current_mono)
        self._handle_hourly(snapshot)
        self._run_automatic_remediation(snapshot)
        for result in self.remediator.process_requests():
            self.store.append_action(result)
        return snapshot

    def run_forever(self, *, run_seconds: float | None = None) -> None:
        started = time.monotonic()
        while True:
            self.run_once()
            if run_seconds is not None and time.monotonic() - started >= run_seconds:
                return
            time.sleep(self.config.poll_seconds)

    def _handle_transition(
        self,
        snapshot: SupervisorSnapshot,
        current_mono: float,
    ) -> None:
        current_codes = tuple(snapshot.reason_codes)
        signature: tuple[object, ...] = (
            snapshot.severity.value,
            snapshot.launch_gate.value,
            current_codes,
        )
        if signature == self._last_event_signature:
            return
        previous_codes: set[str] = set()
        if self._last_event_signature is not None:
            raw_codes = self._last_event_signature[2]
            if isinstance(raw_codes, tuple):
                previous_codes = {str(item) for item in raw_codes}
        current_set = set(current_codes)
        added = tuple(sorted(current_set - previous_codes))
        cleared = tuple(sorted(previous_codes - current_set))
        event = {
            "schema_version": "supervisor-001-event-v1",
            "observed_at": snapshot.observed_at.astimezone(UTC).isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "severity": snapshot.severity.value,
            "launch_gate": snapshot.launch_gate.value,
            "added_reason_codes": list(added),
            "cleared_reason_codes": list(cleared),
            "reason_codes": list(current_codes),
        }
        self.store.append_event(event)

        critical_added = any(
            finding.code in added and finding.severity.value == "CRITICAL"
            for finding in snapshot.findings
        )
        due = (
            self._last_event_bundle_at == 0.0
            or current_mono - self._last_event_bundle_at
            >= self.config.event_bundle_debounce_seconds
        )
        if critical_added or due:
            path = self.bundle_writer.emit(
                bundle_type="events",
                snapshot=snapshot,
                trigger_codes=added or current_codes,
                previous_bundle_id=self._last_bundle_id,
            )
            self._last_bundle_id = path.name
            self._last_event_bundle_at = current_mono
        self._last_event_signature = signature

    def _handle_hourly(self, snapshot: SupervisorSnapshot) -> None:
        hour_key = snapshot.observed_at.astimezone(UTC).strftime("%Y%m%dT%H")
        if self._last_hour_key is None:
            self._last_hour_key = hour_key
            return
        if hour_key == self._last_hour_key:
            return
        path = self.bundle_writer.emit(
            bundle_type="hourly",
            snapshot=snapshot,
            trigger_codes=snapshot.reason_codes,
            previous_bundle_id=self._last_bundle_id,
        )
        self._last_bundle_id = path.name
        self._last_hour_key = hour_key

    def _run_automatic_remediation(self, snapshot: SupervisorSnapshot) -> None:
        reasons_by_action = _reasons_by_action(snapshot)
        for code, target in self.remediator.plan(snapshot):
            result = self.remediator.execute(
                code,
                target=target,
                requested_by="deterministic_rule",
                reason_code=reasons_by_action.get((code, target)),
            )
            detail = result.get("detail")
            if (
                result.get("result") == "SUCCESS"
                and isinstance(detail, dict)
                and detail.get("skipped") is not None
            ):
                continue
            self.store.append_action(result)


def _semantic_payload(
    *,
    host_id: str,
    host_role: str,
    git_head: str,
    collection: SourceCollection,
    findings: tuple[Finding, ...],
    severity: str,
    launch_gate: str,
) -> dict[str, object]:
    source_state = [
        {
            "source_id": item.source_id,
            "required": item.required,
            "available": item.available,
            "valid": item.valid,
            "fresh": item.fresh,
            "reason": item.reason,
        }
        for item in collection.statuses
    ]
    return {
        "host_id": host_id,
        "host_role": host_role,
        "git_head": git_head,
        "source_state": source_state,
        "sections": dict(collection.sections),
        "findings": [item.to_dict() for item in findings],
        "severity": severity,
        "launch_gate": launch_gate,
    }


def _reasons_by_action(
    snapshot: SupervisorSnapshot,
) -> dict[tuple[ActionCode, str | None], str]:
    result: dict[tuple[ActionCode, str | None], str] = {}
    for finding in snapshot.findings:
        service = finding.evidence.get("service")
        if finding.code.startswith(("DISK_PRESSURE_", "DISK_GROWTH_")):
            result[(ActionCode.PRUNE_SUPERVISOR_BUNDLES, None)] = finding.code
            result[(ActionCode.PRUNE_HOT_PARQUET, None)] = finding.code
        capture_recovery_targets = {
            "FEED_SIG_DISCONNECTED": "predictions-cup-sig-capture.service",
            "FEED_SIG_REST_PROGRESS_STALE": "predictions-cup-sig-capture.service",
            "SIG_CAPTURE_ACTIVITY_STALE": "predictions-cup-sig-capture.service",
            "SOURCE_SIG_CAPTURE_STALE": "predictions-cup-sig-capture.service",
            "CAPTURE_SIG_STORAGE_FAILURE": "predictions-cup-sig-capture.service",
            "CAPTURE_SIG_WRITER_DEAD": "predictions-cup-sig-capture.service",
            "CAPTURE_SIG_DROPPED_ROWS": "predictions-cup-sig-capture.service",
            "FEED_POLYMARKET_DISCONNECTED": "predictions-cup-polymarket-capture.service",
            "FEED_POLYMARKET_STALE": "predictions-cup-polymarket-capture.service",
            "SOURCE_POLYMARKET_CAPTURE_STALE": "predictions-cup-polymarket-capture.service",
            "CAPTURE_POLYMARKET_STORAGE_FAILURE": "predictions-cup-polymarket-capture.service",
            "CAPTURE_POLYMARKET_WRITER_DEAD": "predictions-cup-polymarket-capture.service",
            "CAPTURE_POLYMARKET_DROPPED_ROWS": "predictions-cup-polymarket-capture.service",
            "MAKER_OUTCOME_STALE": "predictions-cup-maker-live.service",
            "MAKER_KILL_LATCHED": "predictions-cup-maker-live.service",
        }
        recovery_target = capture_recovery_targets.get(finding.code)
        if recovery_target is not None:
            result[(ActionCode.RESTART_SAFE_SERVICE, recovery_target)] = finding.code
        if (
            finding.code
            in {"SERVICE_FAILURE", "SERVICE_MEMORY_HIGH", "SERVICE_MEMORY_GROWTH"}
            and isinstance(service, str)
        ):
            result[(ActionCode.RESTART_SAFE_SERVICE, service)] = finding.code
    return result
