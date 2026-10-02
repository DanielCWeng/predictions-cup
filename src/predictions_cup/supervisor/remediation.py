"""Bounded remediation executor for SUPERVISOR-001.

Only allowlisted housekeeping and service recovery actions exist here.
There is deliberately no order, LIVE, capital-limit, strategy or risk-halt action.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.supervisor.contracts import (
    ACTION_SCHEMA_VERSION,
    ActionCode,
    HostRole,
    RemediationLevel,
    SupervisorSnapshot,
    deterministic_id,
)

logger = logging.getLogger(__name__)

_ACTION_LEVEL = {
    ActionCode.PRUNE_SUPERVISOR_BUNDLES: RemediationLevel.HOUSEKEEPING,
    ActionCode.PRUNE_HOT_PARQUET: RemediationLevel.HOUSEKEEPING,
    ActionCode.CLEAR_SAFE_CACHE: RemediationLevel.HOUSEKEEPING,
    ActionCode.RESTART_SAFE_SERVICE: RemediationLevel.SERVICE_RECOVERY,
    ActionCode.STOP_NONESSENTIAL_SERVICE: RemediationLevel.HOST_PROTECTION,
}


@dataclass(frozen=True, slots=True)
class RemediationConfig:
    max_level: RemediationLevel
    host_role: HostRole
    supervisor_root: Path
    hot_capture_roots: tuple[Path, ...]
    hot_capture_retention_hours: float
    bundle_retention_hours: float
    safe_cache_paths: tuple[Path, ...]
    safe_restart_services: tuple[str, ...]
    nonessential_services: tuple[str, ...] = ()
    restart_cooldown_seconds: float = 300.0
    max_restarts_per_hour: int = 2
    # A feed/staleness condition must persist this long before a capture
    # restart; capture services reconnect in-process within seconds, and a
    # restart turns a transient drop into a cold reseed (PM: ~11 min).
    restart_grace_seconds: float = 0.0
    # After a service's MainPID changes, suppress feed/staleness and
    # memory-growth restarts for this long so a cold start can finish.
    startup_grace_seconds: float = 0.0


class ActionLimiter:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._restart_times: dict[str, deque[float]] = defaultdict(deque)

    def allow(
        self,
        key: str,
        *,
        cooldown_seconds: float,
        restart_service: str | None = None,
        max_restarts_per_hour: int = 2,
    ) -> bool:
        now = time.monotonic()
        previous = self._last.get(key)
        if previous is not None and now - previous < cooldown_seconds:
            return False
        if restart_service is not None:
            history = self._restart_times[restart_service]
            while history and now - history[0] >= 3600.0:
                history.popleft()
            if len(history) >= max_restarts_per_hour:
                return False
            history.append(now)
        self._last[key] = now
        return True


class RemediationExecutor:
    def __init__(
        self,
        config: RemediationConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._limiter = ActionLimiter()
        self._clock = clock
        self._condition_since: dict[str, float] = {}
        self._pid_since: dict[str, tuple[object, float]] = {}

    def _observe_service_pids(self, snapshot: SupervisorSnapshot, now: float) -> None:
        system = snapshot.sections.get("system")
        services = system.get("services") if isinstance(system, dict) else None
        if not isinstance(services, dict):
            return
        for service in self.config.safe_restart_services:
            entry = services.get(service)
            pid = entry.get("main_pid") if isinstance(entry, dict) else None
            if pid in (None, "", "0", 0):
                continue
            previous = self._pid_since.get(service)
            if previous is None or previous[0] != pid:
                # First sighting after a Supervisor restart counts as a fresh
                # start: process age is not probed, so stay conservative.
                self._pid_since[service] = (pid, now)

    def _service_in_startup(self, service: str, now: float) -> bool:
        if self.config.startup_grace_seconds <= 0:
            return False
        seen = self._pid_since.get(service)
        return seen is not None and now - seen[1] < self.config.startup_grace_seconds

    def _recovery_due(self, service: str, now: float) -> bool:
        since = self._condition_since.setdefault(service, now)
        if now - since < self.config.restart_grace_seconds:
            logger.info("Supervisor restart deferred service=%s reason=grace", service)
            return False
        if self._service_in_startup(service, now):
            logger.info("Supervisor restart deferred service=%s reason=startup", service)
            return False
        return True

    def plan(self, snapshot: SupervisorSnapshot) -> tuple[tuple[ActionCode, str | None], ...]:
        now = self._clock()
        self._observe_service_pids(snapshot, now)
        codes = set(snapshot.reason_codes)
        planned: list[tuple[ActionCode, str | None]] = []
        if codes & {
            "DISK_PRESSURE_WARN",
            "DISK_PRESSURE_CRITICAL",
            "DISK_PRESSURE_EMERGENCY",
            "DISK_GROWTH_WARN",
            "DISK_GROWTH_CRITICAL",
        }:
            planned.append((ActionCode.PRUNE_SUPERVISOR_BUNDLES, None))
            if self.config.host_role is HostRole.WEST_EXECUTION:
                planned.append((ActionCode.PRUNE_HOT_PARQUET, None))
        if codes & {"DISK_PRESSURE_EMERGENCY", "DISK_GROWTH_CRITICAL"}:
            planned.extend(
                (ActionCode.STOP_NONESSENTIAL_SERVICE, service)
                for service in self.config.nonessential_services
            )

        recovery_reasons = {
            "predictions-cup-sig-capture.service": {
                "SOURCE_SIG_CAPTURE_STALE",
                "FEED_SIG_DISCONNECTED",
                "FEED_SIG_REST_PROGRESS_STALE",
                "SIG_CAPTURE_ACTIVITY_STALE",
                "CAPTURE_SIG_STORAGE_FAILURE",
                "CAPTURE_SIG_WRITER_DEAD",
                "CAPTURE_SIG_DROPPED_ROWS",
            },
            "predictions-cup-polymarket-capture.service": {
                "SOURCE_POLYMARKET_CAPTURE_STALE",
                "FEED_POLYMARKET_DISCONNECTED",
                "FEED_POLYMARKET_STALE",
                "CAPTURE_POLYMARKET_STORAGE_FAILURE",
                "CAPTURE_POLYMARKET_WRITER_DEAD",
                "CAPTURE_POLYMARKET_DROPPED_ROWS",
            },
            "predictions-cup-maker-live.service": {
                "MAKER_OUTCOME_STALE",
                "MAKER_KILL_LATCHED",
            },
        }
        restart_loop_services = {
            str(finding.evidence["service"])
            for finding in snapshot.findings
            if finding.code == "SERVICE_RESTART_LOOP"
            and isinstance(finding.evidence.get("service"), str)
        }
        for service, reasons in recovery_reasons.items():
            if service not in self.config.safe_restart_services:
                continue
            if service in restart_loop_services:
                self._condition_since.pop(service, None)
                continue
            if not codes & reasons:
                self._condition_since.pop(service, None)
                continue
            if (
                service == "predictions-cup-maker-live.service"
                and "MAKER_KILL_LATCHED" in codes
            ):
                # A kill latch is immediate evidence of a failed maker process;
                # an explicit service allowlist authorizes recovery without the
                # normal stale-feed/startup grace. The action limiter still
                # enforces its cooldown and hourly budget.
                planned.append((ActionCode.RESTART_SAFE_SERVICE, service))
            elif self._recovery_due(service, now):
                planned.append((ActionCode.RESTART_SAFE_SERVICE, service))

        for finding in snapshot.findings:
            if finding.code not in {
                "SERVICE_FAILURE",
                "SERVICE_MEMORY_HIGH",
                "SERVICE_MEMORY_GROWTH",
            }:
                continue
            if finding.severity.value != "CRITICAL":
                continue
            finding_service = finding.evidence.get("service")
            if (
                isinstance(finding_service, str)
                and finding_service in self.config.safe_restart_services
                and finding_service not in restart_loop_services
            ):
                if finding.code == "SERVICE_MEMORY_GROWTH" and self._service_in_startup(
                    finding_service, now
                ):
                    logger.info(
                        "Supervisor restart deferred service=%s reason=startup_memory_growth",
                        finding_service,
                    )
                    continue
                planned.append((ActionCode.RESTART_SAFE_SERVICE, finding_service))
        unique = tuple(dict.fromkeys(planned))
        return tuple(
            item
            for item in unique
            if _ACTION_LEVEL[item[0]] <= self.config.max_level
        )

    def execute(
        self,
        code: ActionCode,
        *,
        target: str | None,
        requested_by: str,
        reason_code: str | None = None,
    ) -> dict[str, object]:
        level = _ACTION_LEVEL[code]
        started = datetime.now(UTC)
        base: dict[str, object] = {
            "schema_version": ACTION_SCHEMA_VERSION,
            "action": code.value,
            "target": target,
            "level": int(level),
            "requested_by": requested_by,
            "reason_code": reason_code,
            "started_at": started.isoformat(),
        }
        if level > self.config.max_level:
            return self._finish(base, "DENIED_LEVEL", {})
        try:
            if code is ActionCode.PRUNE_SUPERVISOR_BUNDLES:
                detail = self._prune_supervisor_bundles()
            elif code is ActionCode.PRUNE_HOT_PARQUET:
                detail = self._prune_hot_parquet()
            elif code is ActionCode.CLEAR_SAFE_CACHE:
                detail = self._clear_safe_cache(target)
            elif code is ActionCode.RESTART_SAFE_SERVICE:
                detail = self._restart_safe_service(target)
            elif code is ActionCode.STOP_NONESSENTIAL_SERVICE:
                detail = self._stop_nonessential_service(target)
            else:
                return self._finish(base, "DENIED_UNKNOWN_ACTION", {})
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return self._finish(
                base,
                "FAILED",
                {"error": f"{type(exc).__name__}:{exc}"},
            )
        return self._finish(base, "SUCCESS", detail)

    def process_requests(self) -> tuple[dict[str, object], ...]:
        request_root = self.config.supervisor_root / "remediation_requests"
        processed_root = self.config.supervisor_root / "remediation_processed"
        request_root.mkdir(parents=True, exist_ok=True)
        processed_root.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        for path in sorted(request_root.glob("*.json"))[:20]:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise TypeError("request must be object")
                code = ActionCode(str(raw["action"]))
                target_raw = raw.get("target")
                target = None if target_raw is None else str(target_raw)
                request_id = str(raw.get("request_id") or path.stem)
                reason_raw = raw.get("reason_code")
                reason_code = None if reason_raw is None else str(reason_raw)
                result = self.execute(
                    code,
                    target=target,
                    requested_by="external_request",
                    reason_code=reason_code,
                )
                result["request_id"] = request_id
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                result = {
                    "schema_version": ACTION_SCHEMA_VERSION,
                    "request_id": path.stem,
                    "result": "INVALID_REQUEST",
                    "error": f"{type(exc).__name__}:{exc}",
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            destination = processed_root / path.name
            destination.write_text(
                json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            path.unlink(missing_ok=True)
            results.append(result)
        return tuple(results)

    def _prune_supervisor_bundles(self) -> dict[str, object]:
        if not self._limiter.allow("prune-supervisor-bundles", cooldown_seconds=600.0):
            return {"skipped": "cooldown", "reclaimed_bytes": 0}
        cutoff = time.time() - self.config.bundle_retention_hours * 3600.0
        bundle_root = self.config.supervisor_root / "bundles"
        reclaimed = 0
        removed = 0
        if bundle_root.exists():
            for path in bundle_root.glob("*/*"):
                if not path.is_dir() or path.stat().st_mtime >= cutoff:
                    continue
                size = _tree_size(path)
                shutil.rmtree(path)
                reclaimed += size
                removed += 1
        return {"removed_bundles": removed, "reclaimed_bytes": reclaimed}

    def _prune_hot_parquet(self) -> dict[str, object]:
        if self.config.host_role is not HostRole.WEST_EXECUTION:
            raise ValueError("hot capture pruning is west-only")
        if not self._limiter.allow("prune-hot-parquet", cooldown_seconds=600.0):
            return {"skipped": "cooldown", "reclaimed_bytes": 0}
        cutoff = time.time() - self.config.hot_capture_retention_hours * 3600.0
        reclaimed = 0
        removed = 0
        for root in self.config.hot_capture_roots:
            if not root.exists():
                continue
            for path in root.rglob("*.parquet"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime >= cutoff:
                    continue
                size = stat.st_size
                path.unlink(missing_ok=True)
                reclaimed += size
                removed += 1
            _remove_empty_directories(root)
        return {"removed_files": removed, "reclaimed_bytes": reclaimed}

    def _clear_safe_cache(self, target: str | None) -> dict[str, object]:
        if target is None:
            raise ValueError("safe-cache cleanup requires target")
        path = Path(target).resolve()
        allowed = {item.resolve() for item in self.config.safe_cache_paths}
        if path not in allowed:
            raise ValueError("cache path not allowlisted")
        if not self._limiter.allow(f"clear-cache:{path}", cooldown_seconds=1800.0):
            return {"skipped": "cooldown", "reclaimed_bytes": 0}
        before = _tree_size(path) if path.exists() else 0
        if path.exists():
            if path.is_dir():
                for child in path.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink(missing_ok=True)
            else:
                path.unlink(missing_ok=True)
        return {"reclaimed_bytes": before, "path": str(path)}

    def _restart_safe_service(self, target: str | None) -> dict[str, object]:
        if target is None or target not in self.config.safe_restart_services:
            raise ValueError("service not restart-allowlisted")
        if not self._limiter.allow(
            f"restart:{target}",
            cooldown_seconds=self.config.restart_cooldown_seconds,
            restart_service=target,
            max_restarts_per_hour=self.config.max_restarts_per_hour,
        ):
            return {"skipped": "restart_budget_or_cooldown", "service": target}
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", target],
            check=False,
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return {"service": target, "restart_exit": result.returncode}

    def _stop_nonessential_service(self, target: str | None) -> dict[str, object]:
        if target is None or target not in self.config.nonessential_services:
            raise ValueError("service not stop-allowlisted")
        if not self._limiter.allow(f"stop:{target}", cooldown_seconds=600.0):
            return {"skipped": "cooldown", "service": target}
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "stop", target],
            check=False,
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return {"service": target, "stop_exit": result.returncode}

    @staticmethod
    def _finish(
        base: Mapping[str, object],
        result: str,
        detail: Mapping[str, object],
    ) -> dict[str, object]:
        payload = dict(base)
        payload["result"] = result
        payload["detail"] = dict(detail)
        payload["completed_at"] = datetime.now(UTC).isoformat()
        payload["action_id"] = deterministic_id("action", payload)
        return payload


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        directory = Path(root)
        for name in files:
            try:
                total += (directory / name).stat().st_size
            except OSError:
                continue
    return total


def _remove_empty_directories(root: Path) -> None:
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in directories:
        with contextlib.suppress(OSError):
            path.rmdir()
