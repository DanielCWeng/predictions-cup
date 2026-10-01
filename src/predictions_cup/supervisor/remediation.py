"""Bounded remediation executor for SUPERVISOR-001.

Only allowlisted housekeeping and read-only-service recovery actions exist here.
There is deliberately no order, LIVE, capital-limit, strategy or risk-halt action.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from predictions_cup.supervisor.contracts import (
    ACTION_SCHEMA_VERSION,
    ActionCode,
    HostRole,
    RemediationLevel,
    SupervisorSnapshot,
    deterministic_id,
)

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
    def __init__(self, config: RemediationConfig) -> None:
        self.config = config
        self._limiter = ActionLimiter()

    def plan(self, snapshot: SupervisorSnapshot) -> tuple[tuple[ActionCode, str | None], ...]:
        codes = set(snapshot.reason_codes)
        planned: list[tuple[ActionCode, str | None]] = []
        if codes & {
            "DISK_PRESSURE_WARN",
            "DISK_PRESSURE_CRITICAL",
            "DISK_PRESSURE_EMERGENCY",
        }:
            planned.append((ActionCode.PRUNE_SUPERVISOR_BUNDLES, None))
            if self.config.host_role is HostRole.WEST_EXECUTION:
                planned.append((ActionCode.PRUNE_HOT_PARQUET, None))
        if "DISK_PRESSURE_EMERGENCY" in codes:
            planned.extend(
                (ActionCode.STOP_NONESSENTIAL_SERVICE, service)
                for service in self.config.nonessential_services
            )
        for finding in snapshot.findings:
            if finding.code not in {
                "SERVICE_FAILURE",
                "SERVICE_MEMORY_HIGH",
                "SERVICE_MEMORY_GROWTH",
            }:
                continue
            if finding.severity.value != "CRITICAL":
                continue
            service = finding.evidence.get("service")
            if isinstance(service, str) and service in self.config.safe_restart_services:
                planned.append((ActionCode.RESTART_SAFE_SERVICE, service))
        return tuple(dict.fromkeys(planned))

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
        try:
            path.rmdir()
        except OSError:
            pass
