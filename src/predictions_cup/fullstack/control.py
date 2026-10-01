"""FULLSTACK-002 current-main launch composition control plane.

This module intionally stays outside the quote hot path.  It aggregates existing
runtime evidence, writes one atomic status document, exposes small operator actions,
and emits append-only alert evidence.  It never grants LIVE authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.observe import (
    ObservationStatusState,
    default_observation_health_status_path,
    read_observation_health_status,
)

STATUS_SCHEMA_VERSION = "fullstack-002-status-v1"
ALERT_SCHEMA_VERSION = "fullstack-002-alert-v1"
REHEARSAL_SCHEMA_VERSION = "fullstack-002-rehearsal-v1"
FROZEN_STARTING_MAIN_SHA = "4a040a7d309df26098af5e81e96c7f1108d03117"

RUNTIME_TARGET = "predictions-cup-runtime.target"
SIG_CAPTURE_UNIT = "predictions-cup-sig-capture.service"
POLYMARKET_CAPTURE_UNIT = "predictions-cup-polymarket-capture.service"
MAKER_UNIT = "predictions-cup-maker.service"
STATUS_TIMER_UNIT = "predictions-cup-status.timer"

KNOWN_FROZEN_BASE_BLOCKERS = (
    "issue_84_live_sig_sink_observation_emitter_missing",
    "intended_host_acceptance_not_run",
    "real_sig_order_rehearsal_not_run",
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    status: Path
    alerts: Path

    @classmethod
    def from_environment(cls, settings: AppSettings) -> RuntimePaths:
        runtime_root = settings.sig_research_path.parent / "runtime"
        status = Path(
            os.environ.get(
                "PREDICTIONS_CUP_FULLSTACK_STATUS_PATH",
                str(runtime_root / "status" / "latest.json"),
            )
        )
        alerts = Path(
            os.environ.get(
                "PREDICTIONS_CUP_FULLSTACK_ALERT_PATH",
                str(runtime_root / "alerts" / "events.jsonl"),
            )
        )
        return cls(status=status, alerts=alerts)


def _alert_path_from_environment() -> Path:
    explicit = os.environ.get("PREDICTIONS_CUP_FULLSTACK_ALERT_PATH")
    if explicit:
        return Path(explicit)
    research_root = Path(
        os.environ.get("PREDICTIONS_CUP_SIG_RESEARCH_PATH", "data/sig_research")
    )
    return research_root.parent / "runtime" / "alerts" / "events.jsonl"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _json_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_hash(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: Sequence[str], *, timeout: float = 5.0) -> CommandResult:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", f"{type(exc).__name__}:{exc}")
    return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())


def _systemctl(*args: str) -> CommandResult:
    binary = os.environ.get("PREDICTIONS_CUP_SYSTEMCTL", "systemctl")
    return _run((binary, *args))


def _systemd_unit_state(unit: str) -> dict[str, object]:
    result = _systemctl(
        "show",
        unit,
        "--property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus",
        "--no-pager",
    )
    if result.returncode != 0:
        return {
            "unit": unit,
            "installed": False,
            "active": False,
            "state": "UNAVAILABLE",
            "detail": result.stderr or result.stdout or f"systemctl_rc={result.returncode}",
        }
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key] = value
    load = fields.get("LoadState", "unknown")
    active = fields.get("ActiveState", "unknown")
    return {
        "unit": unit,
        "installed": load == "loaded",
        "active": active == "active",
        "state": active.upper(),
        "sub_state": fields.get("SubState"),
        "result": fields.get("Result"),
        "restart_count": _safe_int(fields.get("NRestarts")),
        "exec_main_status": _safe_int(fields.get("ExecMainStatus")),
    }


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _clock_health() -> dict[str, object]:
    result = _run(("timedatectl", "show", "--property=NTPSynchronized", "--value"), timeout=2)
    if result.returncode != 0:
        return {"state": "UNKNOWN", "ntp_synchronized": None, "detail": result.stderr}
    value = result.stdout.strip().lower()
    synchronized = value == "yes"
    return {
        "state": "HEALTHY" if synchronized else "BLOCKED",
        "ntp_synchronized": synchronized,
        "detail": None if synchronized else "ntp_not_synchronized",
    }


def _nearest_existing_parent(path: Path) -> Path:
    current = path.expanduser().absolute()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _storage_health(settings: AppSettings, paths: RuntimePaths) -> dict[str, object]:
    tracked = (
        settings.sig_realtime_storage_path,
        settings.sig_research_path,
        settings.polymarket_storage_path,
        settings.polymarket_research_path,
        settings.execution_journal_path,
        settings.risk_state_path,
        settings.shadow_journal_path,
        settings.live_learn_outcome_path,
        paths.status,
        paths.alerts,
    )
    roots = {_nearest_existing_parent(path) for path in tracked}
    minimum_free = int(os.environ.get("PREDICTIONS_CUP_FULLSTACK_MIN_FREE_BYTES", str(1 << 30)))
    rows: list[dict[str, object]] = []
    danger = False
    for root in sorted(roots, key=str):
        try:
            usage = shutil.disk_usage(root)
            state = "HEALTHY" if usage.free >= minimum_free else "DANGER"
            danger = danger or state == "DANGER"
            rows.append(
                {
                    "path": str(root),
                    "state": state,
                    "free_bytes": usage.free,
                    "minimum_free_bytes": minimum_free,
                }
            )
        except OSError as exc:
            danger = True
            rows.append({"path": str(root), "state": "DANGER", "detail": type(exc).__name__})
    return {"state": "DANGER" if danger else "HEALTHY", "filesystems": rows}


def _mtime_freshness(path: Path, *, active: bool, max_age_seconds: float) -> dict[str, object]:
    if not active:
        return {"state": "NOT_RUNNING", "path": str(path), "age_seconds": None}
    candidates = (path, Path(f"{path}-wal"))
    observed_paths: list[tuple[Path, float]] = []
    for candidate in candidates:
        try:
            observed_paths.append((candidate, candidate.stat().st_mtime))
        except OSError:
            continue
    if not observed_paths:
        return {"state": "MISSING", "path": str(path), "age_seconds": None}
    evidence_path, observed_timestamp = max(observed_paths, key=lambda item: item[1])
    observed = datetime.fromtimestamp(observed_timestamp, UTC)
    age = max(0.0, (_now() - observed).total_seconds())
    return {
        "state": "HEALTHY" if age <= max_age_seconds else "STALE",
        "path": str(path),
        "evidence_path": str(evidence_path),
        "observed_at": _iso(observed),
        "age_seconds": round(age, 3),
        "max_age_seconds": max_age_seconds,
    }


def _polymarket_health(path: Path, *, active: bool, max_age_seconds: float) -> dict[str, object]:
    if not active:
        return {"state": "NOT_RUNNING", "path": str(path), "age_seconds": None}
    if not path.is_file():
        return {"state": "MISSING", "path": str(path), "age_seconds": None}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1) as connection:
            row = connection.execute(
                "SELECT recorded_at, payload_json FROM ingestion_health ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        return {"state": "INVALID", "path": str(path), "detail": type(exc).__name__}
    if row is None:
        return {"state": "MISSING", "path": str(path), "detail": "no_health_rows"}
    try:
        observed = datetime.fromisoformat(str(row[0])).astimezone(UTC)
        payload = json.loads(str(row[1]))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"state": "INVALID", "path": str(path), "detail": type(exc).__name__}
    age = max(0.0, (_now() - observed).total_seconds())
    storage_failures = (
        _safe_int(payload.get("storage_failures"))
        if isinstance(payload, dict)
        else None
    )
    state = "HEALTHY"
    if age > max_age_seconds:
        state = "STALE"
    if storage_failures:
        state = "DEGRADED"
    return {
        "state": state,
        "path": str(path),
        "observed_at": _iso(observed),
        "age_seconds": round(age, 3),
        "max_age_seconds": max_age_seconds,
        "storage_failures": storage_failures,
        "websocket_connected": (
            payload.get("websocket_connected") if isinstance(payload, dict) else None
        ),
    }


def _risk_status(settings: AppSettings) -> dict[str, object]:
    if not settings.risk_capital_control_enabled:
        return {
            "state": "DISABLED",
            "configured": False,
            "global_halt": None,
            "session_id": None,
        }
    path = settings.risk_state_path
    if not path.is_file():
        return {
            "state": "NOT_READY",
            "configured": True,
            "global_halt": None,
            "session_id": None,
            "detail": "risk_state_missing",
        }
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=0.5) as connection:
            row = connection.execute(
                "SELECT payload_json FROM risk_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return {
                "state": "NOT_READY",
                "configured": True,
                "global_halt": None,
                "session_id": None,
                "detail": "risk_state_uninitialized",
            }
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise TypeError("risk state payload must be an object")
    except (sqlite3.Error, OSError, TypeError, json.JSONDecodeError) as exc:
        return {
            "state": "BLOCKED",
            "configured": True,
            "global_halt": None,
            "session_id": None,
            "detail": f"risk_state_error:{type(exc).__name__}",
        }

    halt_raw = payload.get("global_halt")
    halt = halt_raw if isinstance(halt_raw, dict) else None
    halted = bool(halt is not None and halt.get("active") is True)
    reconciled = payload.get("reconciliation_complete") is True
    account_trusted = payload.get("account_trusted") is True
    marks_trusted = payload.get("marks_trusted") is True
    ready = reconciled and account_trusted and marks_trusted
    return {
        "state": "HALTED" if halted else "READY" if ready else "NOT_READY",
        "configured": True,
        "global_halt": halt,
        "session_id": payload.get("session_id"),
        "reconciliation_complete": reconciled,
        "account_trusted": account_trusted,
        "marks_trusted": marks_trusted,
        "detail": None if halted or ready else "risk_reconciliation_or_trust_incomplete",
    }


def _unresolved_operations(settings: AppSettings) -> dict[str, object]:
    path = settings.execution_journal_path
    if not path.is_file():
        return {"count": 0, "state": "NOT_PRESENT", "path": str(path)}
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    terminal = ("FILLED", "CANCELLED", "RECONCILED", "REJECTED")
    try:
        with sqlite3.connect(uri, uri=True, timeout=0.5) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM execution_envelopes
                WHERE lifecycle_state NOT IN (?, ?, ?, ?)
                """,
                terminal,
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        return {
            "count": None,
            "state": "BLOCKED",
            "path": str(path),
            "detail": type(exc).__name__,
        }
    count = 0 if row is None else int(row[0])
    return {
        "count": count,
        "state": "CLEAR" if count == 0 else "BLOCKED",
        "path": str(path),
    }


def _observe_status(settings: AppSettings, *, maker_active: bool) -> dict[str, object]:
    path = default_observation_health_status_path(settings.sig_research_path)
    if not maker_active:
        return {"state": "NOT_RUNNING", "path": str(path), "process_instance_id": None}
    result = read_observation_health_status(
        path,
        expected_owner="predictions-cup-maker.service",
        max_age_seconds=float(
            os.environ.get("PREDICTIONS_CUP_FULLSTACK_OBSERVE_MAX_AGE_SECONDS", "5")
        ),
    )
    return {
        "state": result.state.value,
        "reason": result.reason,
        "path": str(path),
        "observed_at": _iso(result.observed_at),
        "process_instance_id": result.process_instance_id,
        "owner": result.owner,
        "health": result.health,
    }


def _component_state(
    *,
    installed: bool,
    configured: bool,
    enabled: bool,
    healthy: bool | None,
    authorized: bool,
    detail: str | None = None,
) -> dict[str, object]:
    return {
        "installed": installed,
        "configured": configured,
        "enabled": enabled,
        "healthy": healthy,
        "authorized": authorized,
        "detail": detail,
    }


def _git_sha(repo_root: Path) -> str | None:
    result = _run(("git", "-C", str(repo_root), "rev-parse", "HEAD"), timeout=2)
    if result.returncode == 0 and len(result.stdout) >= 7:
        return result.stdout
    return os.environ.get("PREDICTIONS_CUP_CODE_SHA")


def _read_previous(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return cast(dict[str, object], payload) if isinstance(payload, dict) else None


def build_status(settings: AppSettings, *, repo_root: Path | None = None) -> dict[str, object]:
    repo_root = repo_root or Path.cwd()
    paths = RuntimePaths.from_environment(settings)
    services = {
        SIG_CAPTURE_UNIT: _systemd_unit_state(SIG_CAPTURE_UNIT),
        POLYMARKET_CAPTURE_UNIT: _systemd_unit_state(POLYMARKET_CAPTURE_UNIT),
        MAKER_UNIT: _systemd_unit_state(MAKER_UNIT),
        STATUS_TIMER_UNIT: _systemd_unit_state(STATUS_TIMER_UNIT),
    }
    sig_active = bool(services[SIG_CAPTURE_UNIT]["active"])
    polymarket_active = bool(services[POLYMARKET_CAPTURE_UNIT]["active"])
    maker_active = bool(services[MAKER_UNIT]["active"])

    freshness_seconds = float(
        os.environ.get("PREDICTIONS_CUP_FULLSTACK_CAPTURE_MAX_AGE_SECONDS", "60")
    )
    captures = {
        "sig": _mtime_freshness(
            settings.sig_realtime_storage_path,
            active=sig_active,
            max_age_seconds=freshness_seconds,
        ),
        "polymarket": _polymarket_health(
            settings.polymarket_storage_path,
            active=polymarket_active,
            max_age_seconds=freshness_seconds,
        ),
    }
    risk = _risk_status(settings)
    unresolved = _unresolved_operations(settings)
    observe = _observe_status(settings, maker_active=maker_active)
    clock = _clock_health()
    storage = _storage_health(settings, paths)
    diagnostics = settings.diagnostic_fields()

    maker_shadow_configured = (
        settings.maker_enabled
        and settings.shadow_enabled
        and not settings.trading_enabled
        and settings.execution_mode == "SHADOW"
    )
    live_learn_configured = maker_shadow_configured and settings.live_learn_enabled
    risk_configured = settings.risk_capital_control_enabled
    observe_healthy = observe["state"] == ObservationStatusState.HEALTHY.value
    risk_halted = risk.get("state") == "HALTED"
    global_kill = bool(settings.global_kill_switch)

    # FULLSTACK-002 deliberately has no LIVE service/drop-in.  Current frozen main
    # also has issue #84 unresolved, so authorization is false even if somebody
    # supplies LIVE-looking environment values outside this composition.
    live_blockers = list(KNOWN_FROZEN_BASE_BLOCKERS)
    if not settings.sig_trade_credential:
        live_blockers.append("execution_credential_absent")
    if not risk_configured:
        live_blockers.append("risk_capital_control_disabled")
    if risk.get("state") not in {"READY"}:
        live_blockers.append(f"risk_state_{str(risk.get('state')).lower()}")
    if clock.get("state") != "HEALTHY":
        live_blockers.append(f"clock_{str(clock.get('state')).lower()}")
    if storage.get("state") != "HEALTHY":
        live_blockers.append("storage_danger")
    if unresolved.get("count") not in {0}:
        live_blockers.append("unresolved_execution_operations")
    if risk_halted or global_kill:
        live_blockers.append("global_halt_or_kill")
    if observe.get("state") != "HEALTHY":
        live_blockers.append("observe_not_healthy")

    session_id = observe.get("process_instance_id") or risk.get("session_id")
    mapping_hash = _file_hash(settings.maker_mapping_path)
    risk_registry_hash = _file_hash(settings.risk_exposure_groups_path)

    components = {
        "CAPTURE_SIG": _component_state(
            installed=bool(services[SIG_CAPTURE_UNIT]["installed"]),
            configured=settings.sig_read_credential is not None and bool(settings.tournament_id),
            enabled=sig_active,
            healthy=captures["sig"]["state"] == "HEALTHY" if sig_active else None,
            authorized=False,
        ),
        "CAPTURE_POLYMARKET": _component_state(
            installed=bool(services[POLYMARKET_CAPTURE_UNIT]["installed"]),
            configured=(
                settings.polymarket_capture_enabled
                and bool(settings.polymarket_supervised_ids.strip())
            ),
            enabled=polymarket_active,
            healthy=captures["polymarket"]["state"] == "HEALTHY" if polymarket_active else None,
            authorized=False,
        ),
        "MAKE": _component_state(
            installed=bool(services[MAKER_UNIT]["installed"]),
            configured=settings.maker_enabled,
            enabled=maker_active,
            healthy=(observe_healthy and not global_kill) if maker_active else None,
            authorized=False,
            detail="FULLSTACK-002 service is forced SHADOW; LIVE authorization is NOT_READY",
        ),
        "SHADOW": _component_state(
            installed=True,
            configured=maker_shadow_configured,
            enabled=maker_active and settings.shadow_enabled,
            healthy=None,
            authorized=True,
            detail="in-process with MAKE; no independent health surface",
        ),
        "OBSERVE": _component_state(
            installed=True,
            configured=settings.maker_enabled,
            enabled=maker_active,
            healthy=observe_healthy if maker_active else None,
            authorized=True,
            detail="in-process with MAKE",
        ),
        "LIVE_LEARN": _component_state(
            installed=True,
            configured=live_learn_configured,
            enabled=maker_active and settings.live_learn_enabled,
            healthy=None,
            authorized=True,
            detail=(
                "SHADOW mirror in-process with MAKE; process state only, "
                "no independent health surface"
            ),
        ),
        "RISK": _component_state(
            installed=True,
            configured=risk_configured,
            enabled=maker_active and risk_configured,
            healthy=(risk.get("state") == "READY") if risk_configured else None,
            authorized=False,
            detail="capital-control service is in-process with MAKE when configured",
        ),
        "BUILD_009_EXECUTION": _component_state(
            installed=True,
            configured=True,
            enabled=maker_active,
            healthy=None,
            authorized=False,
            detail="LIVE not exposed by FULLSTACK-002",
        ),
    }

    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "observed_at": _iso(_now()),
        "starting_main_sha": FROZEN_STARTING_MAIN_SHA,
        "code_sha": _git_sha(repo_root),
        "host_identity": socket.gethostname(),
        "config_hash": _json_hash(diagnostics),
        "mapping_hash": mapping_hash,
        "mapping_version": settings.maker_mapping_path.name if mapping_hash else None,
        "risk_registry_hash": risk_registry_hash,
        "risk_registry_version": settings.risk_profile_version,
        "session_id": session_id,
        "trading_authorization": {
            "authorized": False,
            "state": "NOT_READY",
            "reason": "FULLSTACK-002 intentionally exposes no LIVE launch path on frozen main",
            "blockers": sorted(set(live_blockers)),
        },
        "effective_enabled_components": sorted(
            name for name, state in components.items() if bool(state["enabled"])
        ),
        "components": components,
        "process_health": services,
        "capture_freshness": captures,
        "clock_health": clock,
        "storage_health": storage,
        "risk_state": risk,
        "global_halt_state": {
            "risk_halted": risk_halted,
            "global_kill_switch": global_kill,
            "active": risk_halted or global_kill,
        },
        "unresolved_operation_count": unresolved.get("count"),
        "unresolved_operations": unresolved,
        "maker_state": {
            "active": maker_active,
            "configured": settings.maker_enabled,
            "mode": "SHADOW" if maker_active else None,
            "live_service_available": False,
        },
        "observe_health": observe,
        "live_learn_health": {
            "configured": settings.live_learn_enabled,
            "state": (
                "PROCESS_RUNNING_HEALTH_UNKNOWN"
                if maker_active and settings.live_learn_enabled
                else "NOT_RUNNING"
            ),
            "health_evidence": "PROCESS_ONLY",
            "outcome_path": str(settings.live_learn_outcome_path),
            "report_path": str(settings.live_learn_report_path),
        },
        "known_blockers": list(KNOWN_FROZEN_BASE_BLOCKERS),
    }


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _append_alert_payload(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _deliver_alert_webhook(payload: Mapping[str, object]) -> str:
    url = os.environ.get("PREDICTIONS_CUP_FULLSTACK_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return "NOT_CONFIGURED"
    if not url.startswith(("https://", "http://")):
        return "INVALID_URL"
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            status = getattr(response, "status", None)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return f"FAILED:{type(exc).__name__}"
    if isinstance(status, int) and not 200 <= status < 300:
        return f"HTTP_{status}"
    return "DELIVERED"


def append_alert(path: Path, *, event_type: str, detail: Mapping[str, object]) -> None:
    payload = {
        "schema_version": ALERT_SCHEMA_VERSION,
        "observed_at": _iso(_now()),
        "event_type": event_type,
        "host_identity": socket.gethostname(),
        "detail": dict(detail),
    }
    # Durable local evidence is authoritative and is committed before any
    # optional network notification attempt.
    _append_alert_payload(path, payload)
    delivery = _deliver_alert_webhook(payload)
    if delivery in {"NOT_CONFIGURED", "DELIVERED"}:
        return
    _append_alert_payload(
        path,
        {
            "schema_version": ALERT_SCHEMA_VERSION,
            "observed_at": _iso(_now()),
            "event_type": "ALERT_DELIVERY_FAILED",
            "host_identity": socket.gethostname(),
            "detail": {
                "source_event_type": event_type,
                "delivery_state": delivery,
            },
        },
    )


def _transition_alerts(
    previous: Mapping[str, object] | None,
    current: Mapping[str, object],
    path: Path,
) -> None:
    if previous is None:
        return

    def nested(payload: Mapping[str, object], *keys: str) -> object:
        value: object = payload
        for key in keys:
            if not isinstance(value, Mapping):
                return None
            value = value.get(key)
        return value

    transitions = (
        (
            "GLOBAL_HALT",
            nested(previous, "global_halt_state", "active"),
            nested(current, "global_halt_state", "active"),
            True,
        ),
        (
            "SIG_CAPTURE_STALE",
            nested(previous, "capture_freshness", "sig", "state"),
            nested(current, "capture_freshness", "sig", "state"),
            "STALE",
        ),
        (
            "POLYMARKET_CAPTURE_STALE",
            nested(previous, "capture_freshness", "polymarket", "state"),
            nested(current, "capture_freshness", "polymarket", "state"),
            "STALE",
        ),
        (
            "STORAGE_DANGER",
            nested(previous, "storage_health", "state"),
            nested(current, "storage_health", "state"),
            "DANGER",
        ),
        (
            "CLOCK_UNHEALTHY",
            nested(previous, "clock_health", "state"),
            nested(current, "clock_health", "state"),
            "BLOCKED",
        ),
        (
            "UNRESOLVED_EXECUTION_OPERATIONS",
            nested(previous, "unresolved_operations", "state"),
            nested(current, "unresolved_operations", "state"),
            "BLOCKED",
        ),
    )
    for event_type, before, after, trigger in transitions:
        if after == trigger and before != trigger:
            append_alert(path, event_type=event_type, detail={"before": before, "after": after})

    previous_process = previous.get("process_health")
    current_process = current.get("process_health")
    if isinstance(previous_process, Mapping) and isinstance(current_process, Mapping):
        for unit, state in current_process.items():
            old = previous_process.get(unit)
            if not isinstance(state, Mapping) or not isinstance(old, Mapping):
                continue
            failed = state.get("state") == "FAILED" or state.get("result") == "start-limit-hit"
            old_failed = old.get("state") == "FAILED" or old.get("result") == "start-limit-hit"
            if failed and not old_failed:
                append_alert(
                    path,
                    event_type="SERVICE_FAILED",
                    detail={
                        "unit": unit,
                        "state": state.get("state"),
                        "result": state.get("result"),
                    },
                )


def write_status(settings: AppSettings, *, repo_root: Path | None = None) -> dict[str, object]:
    paths = RuntimePaths.from_environment(settings)
    previous = _read_previous(paths.status)
    current = build_status(settings, repo_root=repo_root)
    _transition_alerts(previous, current, paths.alerts)
    _atomic_json(paths.status, current)
    return current


def check_component(settings: AppSettings, name: str) -> tuple[bool, str]:
    if name == "sig-capture":
        if settings.sig_read_credential is None:
            return False, "sig_read_credential_missing"
        if settings.tournament_id is None:
            return False, "tournament_id_missing"
        return True, "READY"
    if name == "maker-shadow":
        if not settings.maker_enabled:
            return False, "maker_disabled"
        if not settings.shadow_enabled:
            return False, "shadow_disabled"
        if settings.trading_enabled or settings.execution_mode != "SHADOW":
            return False, "unsafe_live_configuration"
        if settings.global_kill_switch:
            return False, "global_kill_switch_active"
        if settings.tournament_id is None or settings.tournament_slug is None:
            return False, "tournament_context_missing"
        if not settings.maker_mapping_path.is_file():
            return False, "maker_mapping_missing"
        return True, "READY"
    if name == "polymarket-capture":
        if not settings.polymarket_capture_enabled:
            return False, "polymarket_capture_disabled"
        if not settings.polymarket_supervised_ids.strip():
            return False, "polymarket_supervised_ids_missing"
        return True, "READY"
    raise ValueError(f"unsupported component check: {name}")


def _stop_maker() -> CommandResult:
    return _systemctl("stop", MAKER_UNIT)


def halt_global(settings: AppSettings, *, reason: str) -> dict[str, object]:
    paths = RuntimePaths.from_environment(settings)
    stop = _stop_maker()
    active = _systemctl("is-active", "--quiet", MAKER_UNIT)
    payload: dict[str, object]
    if active.returncode == 0:
        payload = {"state": "BLOCKED", "reason": "maker_service_still_active"}
        append_alert(paths.alerts, event_type="HALT_FAILED", detail=payload)
        return payload
    if not settings.risk_state_path.is_file():
        payload = {"state": "BLOCKED", "reason": "risk_state_missing"}
        append_alert(paths.alerts, event_type="HALT_FAILED", detail=payload)
        return payload
    command = (
        sys.executable,
        "scripts/risk002_control.py",
        "--runtime-env-only",
        "halt-global",
        "--reason",
        reason,
        "--ack-service-stopped",
    )
    result = _run(command, timeout=10)
    if result.returncode != 0:
        payload = {
            "state": "BLOCKED",
            "reason": "risk_halt_command_failed",
            "stderr": result.stderr,
            "maker_stop_rc": stop.returncode,
        }
        append_alert(paths.alerts, event_type="HALT_FAILED", detail=payload)
        return payload
    payload = {"state": "HALTED", "reason": reason, "maker_stop_rc": stop.returncode}
    append_alert(paths.alerts, event_type="GLOBAL_HALT", detail=payload)
    return payload


def _service_action(
    action: str,
    units: Sequence[str],
    *,
    verify_active: Sequence[str] = (),
) -> dict[str, object]:
    result = _systemctl(action, *units)
    failed_active: list[str] = []
    if result.returncode == 0:
        for unit in verify_active:
            active = _systemctl("is-active", "--quiet", unit)
            if active.returncode != 0:
                failed_active.append(unit)
    return {
        "state": (
            "OK"
            if result.returncode == 0 and not failed_active
            else "BLOCKED"
        ),
        "action": action,
        "units": list(units),
        "returncode": result.returncode,
        "inactive_after_action": failed_active,
        "stderr": result.stderr,
    }


def start_shadow(settings: AppSettings) -> dict[str, object]:
    sig_ready, sig_reason = check_component(settings, "sig-capture")
    if not sig_ready:
        return {"state": "BLOCKED", "reason": sig_reason}
    maker_ready, maker_reason = check_component(settings, "maker-shadow")
    if not maker_ready:
        return {"state": "BLOCKED", "reason": maker_reason}
    required = [SIG_CAPTURE_UNIT, MAKER_UNIT]
    if settings.polymarket_capture_enabled:
        polymarket_ready, polymarket_reason = check_component(
            settings, "polymarket-capture"
        )
        if not polymarket_ready:
            return {"state": "BLOCKED", "reason": polymarket_reason}
        required.append(POLYMARKET_CAPTURE_UNIT)
    return _service_action(
        "start",
        (RUNTIME_TARGET,),
        verify_active=tuple(required),
    )


def stop_runtime() -> dict[str, object]:
    # Stop explicit services as well as the target; systemd target stop does not
    # imply StopWhenUnneeded for services that may be enabled independently.
    return _service_action(
        "stop",
        (MAKER_UNIT, POLYMARKET_CAPTURE_UNIT, SIG_CAPTURE_UNIT, RUNTIME_TARGET),
    )


def restart_safe(settings: AppSettings) -> dict[str, object]:
    sig_ready, sig_reason = check_component(settings, "sig-capture")
    if not sig_ready:
        return {"state": "BLOCKED", "reason": sig_reason}
    maker_ready, maker_reason = check_component(settings, "maker-shadow")
    if not maker_ready:
        return {"state": "BLOCKED", "reason": maker_reason}
    units = [SIG_CAPTURE_UNIT]
    if settings.polymarket_capture_enabled:
        polymarket_ready, polymarket_reason = check_component(
            settings, "polymarket-capture"
        )
        if not polymarket_ready:
            return {"state": "BLOCKED", "reason": polymarket_reason}
        units.append(POLYMARKET_CAPTURE_UNIT)
    units.append(MAKER_UNIT)
    return _service_action(
        "restart",
        tuple(units),
        verify_active=tuple(units),
    )


def rehearsal(settings: AppSettings, *, repo_root: Path) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def record(name: str, state: str, detail: object = None) -> None:
        checks.append({"name": name, "state": state, "detail": detail})

    required_imports = (
        "predictions_cup.sig.capture",
        "predictions_cup.external.polymarket.recorder",
        "predictions_cup.maker.service",
        "predictions_cup.observe",
        "predictions_cup.live_learn",
        "predictions_cup.risk",
        "predictions_cup.shadow",
    )
    import_result = _run(
        (
            sys.executable,
            "-c",
            ";".join(f"import {module}" for module in required_imports),
        ),
        timeout=15,
    )
    record(
        "component_imports",
        "PASS" if import_result.returncode == 0 else "BLOCKED",
        import_result.stderr,
    )

    mapping_hash = _file_hash(settings.maker_mapping_path)
    record(
        "mapping_available",
        "PASS" if mapping_hash else "BLOCKED",
        str(settings.maker_mapping_path),
    )

    safe_config = not settings.trading_enabled and settings.execution_mode == "SHADOW"
    record("safe_configuration", "PASS" if safe_config else "BLOCKED", settings.execution_mode)

    status = build_status(settings, repo_root=repo_root)
    record("status_aggregation", "PASS", status.get("schema_version"))

    units = sorted((repo_root / "deploy" / "systemd").glob("predictions-cup-*"))
    if shutil.which("systemd-analyze") and units:
        with tempfile.TemporaryDirectory(prefix="fullstack002-systemd-") as raw:
            rendered_root = Path(raw)
            fixture_env = rendered_root / "runtime.env"
            fixture_env.write_text("PREDICTIONS_CUP_TRADING_ENABLED=false\n", encoding="utf-8")
            rendered: list[str] = []
            for unit in units:
                target = rendered_root / unit.name
                content = unit.read_text(encoding="utf-8")
                content = (
                    content.replace("@@RUNTIME_USER@@", os.environ.get("USER", "nobody"))
                    .replace("@@REPO_ROOT@@", str(repo_root))
                    .replace("@@RUNTIME_ENV@@", str(fixture_env))
                    .replace("@@PYTHON_BIN@@", sys.executable)
                )
                target.write_text(content, encoding="utf-8")
                rendered.append(str(target))
            verify = _run(("systemd-analyze", "verify", *rendered), timeout=15)
        record(
            "systemd_analyze_verify",
            "PASS" if verify.returncode == 0 else "BLOCKED",
            verify.stderr,
        )
    else:
        record("systemd_analyze_verify", "NOT_RUN", "systemd-analyze unavailable")

    shell = repo_root / "scripts" / "install_fullstack002.sh"
    if shell.is_file() and shutil.which("bash"):
        syntax = _run(("bash", "-n", str(shell)), timeout=5)
        record("shell_syntax", "PASS" if syntax.returncode == 0 else "BLOCKED", syntax.stderr)
    else:
        record("shell_syntax", "BLOCKED", "installer_missing")

    blocked = [item for item in checks if item["state"] == "BLOCKED"]
    result = "SIMULATION_PASS" if not blocked else "BLOCKED"
    return {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "observed_at": _iso(_now()),
        "result": result,
        "economic_order_sent": False,
        "real_host_evidence": "NOT_RUN",
        "real_result": "NOT_RUN",
        "checks": checks,
    }


def _print(payload: object, *, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _apply_env_file(path: Path) -> None:
    """Load a simple KEY=VALUE runtime env file without evaluating shell code."""
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "A").isalnum():
            raise ValueError(f"invalid runtime env assignment at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FULLSTACK-002 launch control surface")
    sub = parser.add_subparsers(dest="command", required=True)

    def runtime_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--runtime-env-only", action="store_true")
        command.add_argument("--env-file", type=Path)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--write", action="store_true")
    runtime_args(status)

    alert = sub.add_parser("alert")
    alert.add_argument("--unit", required=True)
    runtime_args(alert)

    check = sub.add_parser("check")
    check.add_argument("component", choices=("sig-capture", "maker-shadow", "polymarket-capture"))
    runtime_args(check)

    halt = sub.add_parser("halt")
    halt.add_argument("--reason", default="operator_global_halt")
    runtime_args(halt)

    start = sub.add_parser("start-shadow")
    runtime_args(start)

    stop = sub.add_parser("stop")
    runtime_args(stop)

    restart = sub.add_parser("restart")
    runtime_args(restart)

    flatten = sub.add_parser("flatten")
    runtime_args(flatten)

    rehearse = sub.add_parser("rehearse")
    runtime_args(rehearse)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env_file = getattr(args, "env_file", None)
    if env_file is not None:
        _apply_env_file(env_file)
    repo_root = Path(__file__).resolve().parents[3]

    # Failure alerts must survive malformed runtime settings; do not require
    # AppSettings validation just to persist the failure evidence.
    if args.command == "alert":
        alert_path = _alert_path_from_environment()
        append_alert(
            alert_path,
            event_type="SYSTEMD_ON_FAILURE",
            detail={"unit": args.unit},
        )
        _print({"state": "ALERT_RECORDED", "unit": args.unit}, compact=True)
        return 0

    use_dotenv = not getattr(args, "runtime_env_only", False) and env_file is None
    settings = load_settings(use_dotenv=use_dotenv)
    paths = RuntimePaths.from_environment(settings)

    if args.command == "status":
        payload = (
            write_status(settings, repo_root=repo_root)
            if args.write
            else build_status(settings, repo_root=repo_root)
        )
        _print(payload, compact=args.json)
        return 0
    if args.command == "check":
        ready, reason = check_component(settings, args.component)
        _print({"state": "READY" if ready else "NOT_CONFIGURED", "reason": reason}, compact=True)
        # ExecCondition: 0 continue, 1 cleanly skip without marking the unit failed.
        return 0 if ready else 1
    if args.command == "halt":
        payload = halt_global(settings, reason=args.reason)
        _print(payload, compact=True)
        return 0 if payload["state"] == "HALTED" else 2
    if args.command == "start-shadow":
        payload = start_shadow(settings)
        _print(payload, compact=True)
        return 0 if payload["state"] == "OK" else 2
    if args.command == "stop":
        payload = stop_runtime()
        _print(payload, compact=True)
        return 0 if payload["state"] == "OK" else 2
    if args.command == "restart":
        payload = restart_safe(settings)
        _print(payload, compact=True)
        return 0 if payload["state"] == "OK" else 2
    if args.command == "flatten":
        payload = {
            "state": "NOT_READY",
            "reason": (
                "no accepted current-main operator flatten contract is safe "
                "for FULLSTACK-002"
            ),
        }
        _print(payload, compact=True)
        return 2
    if args.command == "rehearse":
        payload = rehearsal(settings, repo_root=repo_root)
        _print(payload, compact=False)
        return 0 if payload["result"] == "SIMULATION_PASS" else 2
    raise AssertionError("unreachable command")
