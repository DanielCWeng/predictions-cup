"""Host-neutral FULLSTACK control-plane, provenance, clock and storage helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTROL_PLANE_SCHEMA = "fullstack-control-plane-v1"
SESSION_MANIFEST_SCHEMA = "launch-session-manifest-v1"
_STORAGE_HISTORY_SCHEMA = "storage-runway-history-v1"
_CHRONY_OFFSET = re.compile(r"Last offset\s*:\s*([+-]?[0-9.eE-]+)\s+seconds")


CommandRunner = Callable[[Sequence[str]], tuple[int, str]]


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout.strip()


def canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def content_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(payload) + b"\n"
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def artifact_identity(path: Path) -> dict[str, object]:
    if not path.exists() or not path.is_file():
        return {"path": str(path), "exists": False, "sha256": None, "bytes": None}
    raw = path.read_bytes()
    return {
        "path": str(path),
        "exists": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def clock_health(
    *,
    threshold_seconds: float,
    runner: CommandRunner = _run,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    if threshold_seconds is not None and threshold_seconds <= 0:
        raise ValueError("clock threshold must be positive")
    checked_at = wall_clock().astimezone(UTC)
    monotonic_info = time.get_clock_info("monotonic")
    synchronized: bool | None = None
    offset_seconds: float | None = None
    sources: list[str] = []
    reasons: list[str] = []

    rc, text = runner(("timedatectl", "show", "--property=NTPSynchronized", "--value"))
    if rc == 0 and text:
        sources.append("timedatectl:NTPSynchronized")
        normalized = text.strip().lower()
        if normalized in {"yes", "true", "1"}:
            synchronized = True
        elif normalized in {"no", "false", "0"}:
            synchronized = False

    chrony_rc, chrony_text = runner(("chronyc", "tracking"))
    if chrony_rc == 0 and chrony_text:
        sources.append("chronyc:tracking")
        match = _CHRONY_OFFSET.search(chrony_text)
        if match is not None:
            try:
                offset_seconds = float(match.group(1))
            except ValueError:
                offset_seconds = None
        if "Leap status" in chrony_text:
            if "Not synchronised" in chrony_text:
                synchronized = False
            elif "Normal" in chrony_text and synchronized is None:
                synchronized = True

    if not monotonic_info.monotonic:
        reasons.append("MONOTONIC_CLOCK_UNAVAILABLE")
    if synchronized is None:
        state = "UNKNOWN"
        reasons.append("SYNC_STATE_UNAVAILABLE")
    elif not synchronized:
        state = "BLOCKED"
        reasons.append("CLOCK_NOT_SYNCHRONIZED")
    elif threshold_seconds is None:
        state = "NOT_CONFIGURED"
        reasons.append("CLOCK_OFFSET_THRESHOLD_NOT_CONFIGURED")
    elif offset_seconds is not None and abs(offset_seconds) > threshold_seconds:
        state = "BLOCKED"
        reasons.append("CLOCK_OFFSET_EXCEEDS_THRESHOLD")
    elif not monotonic_info.monotonic:
        state = "BLOCKED"
    else:
        state = "HEALTHY"
        if offset_seconds is None:
            reasons.append("OFFSET_UNAVAILABLE_FROM_HOST")

    return {
        "state": state,
        "reason_codes": reasons,
        "checked_at": checked_at.isoformat(),
        "synchronized": synchronized,
        "estimated_offset_seconds": offset_seconds,
        "evidence_sources": sources,
        "monotonic_clock_available": monotonic_info.monotonic,
        "monotonic_resolution_seconds": monotonic_info.resolution,
        "threshold_seconds": threshold_seconds,
    }


def _bounded_tree_size(path: Path, *, max_entries: int = 5000) -> dict[str, object]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "bytes": 0,
            "entries_sampled": 0,
            "truncated": False,
            "latest_mtime_ns": None,
        }
    if path.is_file():
        stat = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "bytes": stat.st_size,
            "entries_sampled": 1,
            "truncated": False,
            "latest_mtime_ns": stat.st_mtime_ns,
        }
    total = 0
    count = 0
    latest: int | None = None
    truncated = False
    pending = [path]
    while pending and count < max_entries:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if count >= max_entries:
                        truncated = True
                        break
                    count += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += stat.st_size
                    latest = stat.st_mtime_ns if latest is None else max(latest, stat.st_mtime_ns)
        except OSError:
            continue
    if pending:
        truncated = True
    return {
        "path": str(path),
        "exists": True,
        "bytes": total,
        "entries_sampled": count,
        "truncated": truncated,
        "latest_mtime_ns": latest,
    }


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return 0


def _file_with_wal(path: Path) -> dict[str, object]:
    values: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
    }
    wal = Path(str(path) + "-wal")
    values["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
    return values


def _load_history(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != _STORAGE_HISTORY_SCHEMA:
        return None
    return payload


def storage_health(
    repo: Path,
    values: Mapping[str, str],
    *,
    queue_health: Mapping[str, object] | None = None,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    checked_at = wall_clock().astimezone(UTC)
    usage = shutil.disk_usage(repo)
    free_percent = 100.0 * usage.free / usage.total if usage.total else 0.0
    warn_gib = float(values.get("PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB", "8"))
    critical_gib = float(values.get("PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB", "3"))
    free_gib = usage.free / 1024**3
    reasons: list[str] = []
    state = "HEALTHY"
    if free_gib < critical_gib:
        state = "BLOCKED"
        reasons.append("FILESYSTEM_FREE_CRITICAL")
    elif free_gib < warn_gib:
        state = "DEGRADED"
        reasons.append("FILESYSTEM_FREE_WARNING")

    capture_dir = Path(
        values.get("PREDICTIONS_CUP_SIG_RESEARCH_PATH", "data/sig_research")
    )
    capture = _bounded_tree_size(capture_dir)
    if not bool(capture["exists"]):
        if state == "HEALTHY":
            state = "DEGRADED"
        reasons.append("CAPTURE_PATH_MISSING")
    if bool(capture["truncated"]):
        reasons.append("CAPTURE_SIZE_BOUNDED_SAMPLE")

    sqlite = {
        "sig_realtime": _file_with_wal(
            Path(
                values.get(
                    "PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH",
                    "data/sig_realtime.sqlite3",
                )
            )
        ),
        "polymarket": _file_with_wal(
            Path(
                values.get(
                    "PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH",
                    "data/polymarket_operational.sqlite3",
                )
            )
        ),
        "execution": _file_with_wal(
            Path(
                values.get(
                    "PREDICTIONS_CUP_EXECUTION_JOURNAL_PATH",
                    "data/execution_journal.sqlite3",
                )
            )
        ),
        "risk": _file_with_wal(
            Path(values.get("PREDICTIONS_CUP_RISK_STATE_PATH", "data/risk_002.sqlite3"))
        ),
    }

    if queue_health is not None:
        depth = _int_value(queue_health.get("queue_depth", 0))
        capacity = _int_value(queue_health.get("queue_capacity", 0))
        high_water = _int_value(queue_health.get("high_water_mark", 0))
        drops = _int_value(queue_health.get("dropped_rows", 0))
        failures = _int_value(queue_health.get("storage_failures", 0))
        fraction = depth / capacity if capacity > 0 else None
        warn_fraction = float(
            values.get("PREDICTIONS_CUP_FULLSTACK_QUEUE_WARN_FRACTION", "0.75")
        )
        critical_fraction = float(
            values.get("PREDICTIONS_CUP_FULLSTACK_QUEUE_BLOCK_FRACTION", "0.95")
        )
        if drops or failures or (fraction is not None and fraction >= critical_fraction):
            state = "BLOCKED"
            reasons.append("CAPTURE_QUEUE_OR_WRITER_FAILURE")
        elif fraction is not None and fraction >= warn_fraction and state == "HEALTHY":
            state = "DEGRADED"
            reasons.append("CAPTURE_QUEUE_PRESSURE")
        queue = {
            "depth": depth,
            "capacity": capacity,
            "high_water_mark": high_water,
            "drops": drops,
            "storage_failures": failures,
            "fraction": fraction,
        }
    else:
        queue = None

    status_dir = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status")
    )
    history_path = status_dir / "storage-history.json"
    previous = _load_history(history_path)
    current_bytes = _int_value(capture["bytes"])
    runway_hours: float | None = None
    runway_reason = "INSUFFICIENT_HISTORY"
    growth_bytes_per_hour: float | None = None
    if previous is not None:
        previous_at_raw = previous.get("observed_at")
        previous_bytes = previous.get("capture_bytes")
        if isinstance(previous_at_raw, str) and isinstance(previous_bytes, int):
            try:
                previous_at = datetime.fromisoformat(previous_at_raw.replace("Z", "+00:00"))
            except ValueError:
                previous_at = checked_at
            elapsed_hours = (checked_at - previous_at.astimezone(UTC)).total_seconds() / 3600
            growth = current_bytes - previous_bytes
            if elapsed_hours > 0 and growth > 0:
                growth_bytes_per_hour = growth / elapsed_hours
                runway_hours = usage.free / growth_bytes_per_hour
                runway_reason = "OBSERVED_GROWTH"
            elif elapsed_hours > 0:
                runway_reason = "NON_POSITIVE_GROWTH"
    atomic_json(
        history_path,
        {
            "schema_version": _STORAGE_HISTORY_SCHEMA,
            "observed_at": checked_at.isoformat(),
            "capture_bytes": current_bytes,
        },
    )

    return {
        "state": state,
        "reason_codes": sorted(set(reasons)),
        "checked_at": checked_at.isoformat(),
        "filesystem": {
            "path": str(repo),
            "free_bytes": usage.free,
            "free_percent": free_percent,
            "free_gib": free_gib,
            "warning_free_gib": warn_gib,
            "critical_free_gib": critical_gib,
        },
        "capture": capture,
        "sqlite": sqlite,
        "queue": queue,
        "runway_hours": runway_hours,
        "runway_reason": runway_reason,
        "observed_growth_bytes_per_hour": growth_bytes_per_hour,
        "history_path": str(history_path),
    }


def _git(repo: Path, args: Sequence[str]) -> str | None:
    rc, output = _run(("git", "-C", str(repo), *args))
    return output if rc == 0 and output else None


def _git_dirty(repo: Path) -> bool | None:
    rc, output = _run(("git", "-C", str(repo), "status", "--porcelain"))
    return None if rc != 0 else bool(output)


def build_session_manifest(
    repo: Path,
    values: Mapping[str, str],
    *,
    session_id: str,
    started_at: datetime,
    snapshot: Mapping[str, object],
    source_state: Mapping[str, object],
) -> dict[str, object]:
    ref = _git(repo, ("symbolic-ref", "--short", "-q", "HEAD"))
    dirty = _git_dirty(repo)
    shock_path = Path(
        values.get(
            "PREDICTIONS_CUP_STRUCTURAL_SHOCK_REGISTRY",
            "data/capture/r3_live_shadow_polymarket_ids.json",
        )
    )
    manifest: dict[str, object] = {
        "schema_version": SESSION_MANIFEST_SCHEMA,
        "session_id": session_id,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "code": {
            "repository": "DanielCWeng/predictions-cup",
            "git_sha": snapshot.get("git_sha"),
            "ref": ref,
            "dirty_tree": dirty,
        },
        "runtime_configuration": {
            "schema_version": snapshot.get("config_schema_version"),
            "non_secret_sha256": snapshot.get("non_secret_config_sha256"),
            "execution_mode": values.get("PREDICTIONS_CUP_EXECUTION_MODE", "SHADOW"),
            "enabled_components": {
                "maker": values.get("PREDICTIONS_CUP_MAKER_ENABLED", "false"),
                "shadow": values.get("PREDICTIONS_CUP_SHADOW_ENABLED", "false"),
                "live_learn": values.get(
                    "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED", "false"
                ),
                "observe": values.get(
                    "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED", "false"
                ),
            },
        },
        "mapping": snapshot.get("mapping"),
        "strategy_model_providers": snapshot.get("enabled_candidates_observed"),
        "structural_shock_registry": artifact_identity(shock_path),
        "process": {
            "hostname": socket.gethostname(),
            "environment": values.get("PREDICTIONS_CUP_ENVIRONMENT", "development"),
            "pid": os.getpid(),
        },
        "source_state": dict(source_state),
    }
    manifest["manifest_sha256"] = content_hash(manifest)
    return manifest


def persist_session_manifest(
    values: Mapping[str, str],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    session_id = str(manifest["session_id"])
    root = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_SESSION_ROOT", "data/runtime/sessions")
    )
    path = root / session_id / "manifest.json"
    atomic_json(path, manifest)
    pointer = {
        "schema_version": SESSION_MANIFEST_SCHEMA,
        "session_id": session_id,
        "manifest_path": str(path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "state": "HEALTHY",
    }
    status_dir = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status")
    )
    atomic_json(status_dir / "session.json", pointer)
    return pointer


def read_session_pointer(values: Mapping[str, str]) -> dict[str, object]:
    status_dir = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status")
    )
    path = status_dir / "session.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "NOT_CONFIGURED",
            "reason_codes": ["SESSION_MANIFEST_NOT_CREATED"],
            "session_id": values.get("PREDICTIONS_CUP_SESSION_ID"),
            "manifest_path": None,
            "manifest_sha256": None,
        }
    return payload if isinstance(payload, dict) else {
        "state": "UNKNOWN",
        "reason_codes": ["SESSION_POINTER_INVALID"],
    }


def _canonical_state(raw: object) -> str:
    value = str(raw or "UNKNOWN")
    return {
        "PASS": "HEALTHY",
        "HEALTHY": "HEALTHY",
        "DEGRADED": "DEGRADED",
        "BLOCKED": "BLOCKED",
        "NOT_CONFIGURED": "NOT_CONFIGURED",
        "UNKNOWN": "UNKNOWN",
    }.get(value, "UNKNOWN")


def build_control_plane(
    status: Mapping[str, Any],
    health: Mapping[str, Any],
) -> dict[str, object]:
    sig = status.get("sig")
    pm = status.get("polymarket")
    sig_dict = sig if isinstance(sig, dict) else {}
    pm_dict = pm if isinstance(pm, dict) else {}
    capture_health = sig_dict.get("health")
    capture_dict = capture_health if isinstance(capture_health, dict) else {}
    queue = capture_dict.get("research_storage")
    queue_dict = queue if isinstance(queue, dict) else {}
    execution = status.get("execution")
    execution_dict = execution if isinstance(execution, dict) else {}
    risk = status.get("risk_halt")
    risk_dict = risk if isinstance(risk, dict) else {}
    overall_state = _canonical_state(health.get("state"))
    checks = health.get("checks")
    reason_codes = [
        str(item.get("name"))
        for item in checks
        if isinstance(item, dict) and _canonical_state(item.get("state")) != "HEALTHY"
    ] if isinstance(checks, list) else []
    return {
        "schema_version": CONTROL_PLANE_SCHEMA,
        "identity": {
            "git_sha": status.get("git_sha"),
            "environment": status.get("environment"),
            "runtime_profile": status.get("runtime_profile"),
        },
        "session": status.get("session"),
        "services": status.get("services"),
        "feeds": {
            "sig": sig,
            "polymarket": pm,
            "kalshi": status.get("kalshi"),
        },
        "queues": {
            "capture": {
                "depth": queue_dict.get("queue_depth"),
                "capacity": queue_dict.get("queue_capacity"),
                "high_water_mark": queue_dict.get("high_water_mark"),
                "drops": queue_dict.get("dropped_rows"),
                "sink_failures": queue_dict.get("storage_failures"),
            },
            "learner": (
                status.get("live_learn", {}).get("queue")
                if isinstance(status.get("live_learn"), dict)
                else None
            ),
        },
        "capture": {
            "writer_health": capture_dict,
            "latest_durable_watermark": sig_dict.get("last_observation"),
            "storage": status.get("storage"),
        },
        "observe": status.get("observe"),
        "learner": status.get("live_learn"),
        "candidates": {
            "provider": status.get("shadow"),
            "observed": status.get("shadow_evidence"),
        },
        "risk": risk,
        "execution": {
            **execution_dict,
            "fresh_economic_admission_possible": (
                execution_dict.get("unresolved_count", 0) == 0
                and not bool(risk_dict.get("active", True))
                and _canonical_state(risk_dict.get("state")) == "HEALTHY"
            ),
        },
        "storage": status.get("storage"),
        "clock": status.get("clock"),
        "versions": {
            "fullstack": status.get("schema_version"),
            "config": status.get("config"),
            "capabilities": status.get("capabilities"),
        },
        "overall_health": {
            "state": overall_state,
            "reason_codes": reason_codes,
            "checked_at": health.get("observed_at"),
        },
    }


def publish_control_plane(
    values: Mapping[str, str],
    control_plane: Mapping[str, object],
) -> Path:
    status_dir = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status")
    )
    path = status_dir / "control-plane.json"
    atomic_json(path, control_plane)
    return path
