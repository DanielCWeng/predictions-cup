"""FULLSTACK-001 systemd composition, health and rehearsal surface."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import pwd
import shlex
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from predictions_cup.observe import (
    ObservationStatusState,
    read_observation_health_status,
)

from predictions_cup.runtime.control_plane import (
    build_control_plane,
    build_session_manifest,
    clock_health,
    persist_session_manifest,
    publish_control_plane,
    read_session_pointer,
    storage_health,
)

SCHEMA_VERSION = "fullstack-001-v1"
SIG = "predictions-cup-sig-capture.service"
PM = "predictions-cup-polymarket-capture.service"
MAKE = "predictions-cup-maker.service"
LEARN = "predictions-cup-live-learn.service"
OBSERVE = "predictions-cup-observe.service"
_SECRET = ("CREDENTIAL", "SECRET", "PASSWORD", "PRIVATE_KEY", "API_KEY")
_TERMINAL = {"FILLED", "CANCELLED", "RECONCILED", "REJECTED"}


class GateState(StrEnum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class EvidenceState(StrEnum):
    SIMULATION_PASS = "SIMULATION_PASS"
    REAL_PASS = "REAL_PASS"
    NOT_RUN = "NOT_RUN"
    NOT_RUN_REQUIRES_AUTHORIZATION = "NOT_RUN_REQUIRES_AUTHORIZATION"
    BLOCKED = "BLOCKED"


class CapabilityMode(StrEnum):
    IN_PROCESS = "IN_PROCESS"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"


@dataclass(frozen=True, slots=True)
class CapabilityConfig:
    name: str
    enabled: bool
    mode: CapabilityMode
    owner_services: tuple[str, ...]
    external_service: str | None


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    state: GateState
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ServiceState:
    unit: str
    active_state: str
    sub_state: str
    enabled_state: str | None
    main_pid: int | None
    uptime_seconds: float | None
    restarts: int | None


class HealthProvider(Protocol):
    @property
    def name(self) -> str: ...

    def health(self) -> Check: ...


def runtime_env_path() -> Path:
    explicit = os.environ.get("PREDICTIONS_CUP_RUNTIME_ENV")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("PREDICTIONS_CUP_RUNTIME_HOME")
    if home:
        root = Path(home).expanduser()
    elif os.environ.get("SUDO_USER") not in (None, "root"):
        root = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
    else:
        root = Path.home()
    return root / ".config" / "predictions-cup" / "runtime.env"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if value and value[0] in "\"'":
            try:
                parts = shlex.split(value)
            except ValueError:
                parts = []
            if len(parts) == 1:
                value = parts[0]
        if key:
            values[key] = value
    return values


def env_flag(values: dict[str, str], key: str, default: bool = False) -> bool:
    raw = values.get(key)
    return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def nonsecret_config(values: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in sorted(values.items())
        if not any(marker in key.upper() for marker in _SECRET)
    }


def config_hash(values: dict[str, str]) -> str:
    raw = json.dumps(nonsecret_config(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _run(command: Sequence[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(command), 127, "", str(exc))


def _git_sha(repo: Path) -> str | None:
    result = _run(("git", "-C", str(repo), "rev-parse", "HEAD"), 3.0)
    return result.stdout.strip() if result.returncode == 0 else os.environ.get(
        "PREDICTIONS_CUP_GIT_SHA"
    )


def _capability_mode(values: dict[str, str], key: str) -> CapabilityMode:
    raw = values.get(key, CapabilityMode.IN_PROCESS.value).strip().upper()
    try:
        return CapabilityMode(raw)
    except ValueError as exc:
        raise ValueError(
            f"{key} must be IN_PROCESS or EXTERNAL_SERVICE, got {raw!r}"
        ) from exc


def _owner_services(
    values: dict[str, str],
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    raw = values.get(key)
    if raw is None:
        return default
    owners = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not owners:
        raise ValueError(f"{key} must contain at least one service when set")
    return owners


def capability_configs(values: dict[str, str]) -> dict[str, CapabilityConfig]:
    live_mode = _capability_mode(
        values,
        "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE",
    )
    observe_mode = _capability_mode(
        values,
        "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE",
    )
    live_enabled = env_flag(values, "PREDICTIONS_CUP_LIVE_LEARN_ENABLED") or env_flag(
        values,
        "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED",
    )
    observe_enabled = env_flag(values, "PREDICTIONS_CUP_MAKER_ENABLED") or env_flag(
        values,
        "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED",
    )
    return {
        "live_learn": CapabilityConfig(
            name="live_learn",
            enabled=live_enabled,
            mode=live_mode,
            owner_services=_owner_services(
                values,
                "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_OWNER_SERVICES",
                (MAKE,),
            ),
            external_service=LEARN
            if live_mode is CapabilityMode.EXTERNAL_SERVICE
            else None,
        ),
        "observe": CapabilityConfig(
            name="observe",
            enabled=observe_enabled,
            mode=observe_mode,
            owner_services=_owner_services(
                values,
                "PREDICTIONS_CUP_FULLSTACK_OBSERVE_OWNER_SERVICES",
                (MAKE,),
            ),
            external_service=OBSERVE
            if observe_mode is CapabilityMode.EXTERNAL_SERVICE
            else None,
        ),
    }


def configured_services(values: dict[str, str]) -> tuple[str, ...]:
    services = [SIG]
    if env_flag(values, "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED"):
        services.append(PM)
    if env_flag(values, "PREDICTIONS_CUP_MAKER_ENABLED"):
        services.append(MAKE)
    for capability in capability_configs(values).values():
        if (
            capability.enabled
            and capability.mode is CapabilityMode.EXTERNAL_SERVICE
            and capability.external_service is not None
        ):
            services.append(capability.external_service)
    return tuple(dict.fromkeys(services))


def _systemctl() -> list[str]:
    return shlex.split(os.environ.get("PREDICTIONS_CUP_SYSTEMCTL", "systemctl"))


def _service(unit: str) -> ServiceState:
    props = (
        "ActiveState",
        "SubState",
        "MainPID",
        "NRestarts",
        "ActiveEnterTimestampMonotonic",
    )
    command = [*_systemctl(), "show", unit, *[f"--property={item}" for item in props]]
    result = _run(command, 4.0)
    data: dict[str, str] = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value
    enabled = _run([*_systemctl(), "is-enabled", unit], 3.0)
    raw_start = data.get("ActiveEnterTimestampMonotonic", "")
    uptime = None
    if raw_start.isdigit() and int(raw_start):
        uptime = max(0.0, time.monotonic() - int(raw_start) / 1_000_000)
    return ServiceState(
        unit=unit,
        active_state=data.get("ActiveState", "unknown"),
        sub_state=data.get("SubState", "unknown"),
        enabled_state=(enabled.stdout.strip() or enabled.stderr.strip() or None),
        main_pid=int(data["MainPID"]) if data.get("MainPID", "").isdigit() else None,
        uptime_seconds=uptime,
        restarts=int(data["NRestarts"]) if data.get("NRestarts", "").isdigit() else None,
    )


def _row(path: Path, sql: str) -> tuple[Any, ...] | None:
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25) as db:
            row = db.execute(sql).fetchone()
            return cast(tuple[Any, ...] | None, row)
    except sqlite3.Error:
        return None


def _sig(values: dict[str, str]) -> dict[str, Any]:
    path = Path(
        values.get("PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH", "data/sig_realtime.sqlite3")
    )
    observed = _row(path, "SELECT MAX(observed_at) FROM realtime_deliveries")
    health = _row(
        path,
        "SELECT observed_at,payload_json FROM capture_health ORDER BY id DESC LIMIT 1",
    )
    payload = _decode_json(health[1]) if health else None
    return {
        "path": str(path),
        "last_observation": observed[0] if observed else None,
        "last_health_observation": health[0] if health else None,
        "health": payload,
    }


def _pm(values: dict[str, str]) -> dict[str, Any]:
    path = Path(
        values.get(
            "PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH",
            "data/polymarket_operational.sqlite3",
        )
    )
    health = _row(
        path,
        "SELECT recorded_at,payload_json FROM ingestion_health ORDER BY id DESC LIMIT 1",
    )
    payload = _decode_json(health[1]) if health else None
    return {
        "path": str(path),
        "last_health_observation": health[0] if health else None,
        "last_observation": payload.get("last_message_at") if payload else None,
        "health": payload,
    }


def _decode_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _execution(values: dict[str, str]) -> dict[str, Any]:
    path = Path(
        values.get("PREDICTIONS_CUP_EXECUTION_JOURNAL_PATH", "data/execution_journal.sqlite3")
    )
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "state_counts": {},
        "pending_count": 0,
        "uncertain_count": 0,
        "unresolved_count": 0,
    }
    if not path.exists():
        return result
    try:
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=0.25) as db:
            rows = db.execute(
                "SELECT lifecycle_state,COUNT(*) FROM execution_envelopes "
                "GROUP BY lifecycle_state"
            ).fetchall()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    counts = {str(state): int(count) for state, count in rows}
    result["state_counts"] = counts
    result["pending_count"] = counts.get("PENDING", 0) + counts.get("CANCEL_PENDING", 0)
    result["uncertain_count"] = counts.get("UNCERTAIN", 0)
    result["unresolved_count"] = sum(v for k, v in counts.items() if k not in _TERMINAL)
    return result


def _status_dir(values: dict[str, str]) -> Path:
    return Path(values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status"))


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _provider(values: dict[str, str], name: str) -> dict[str, Any] | None:
    return _json(_status_dir(values) / f"{name}.json")


def _accepted_status_dir(values: dict[str, str]) -> Path:
    return _status_dir(values)


def _risk_provider(values: dict[str, str]) -> dict[str, Any] | None:
    path = Path(values.get("PREDICTIONS_CUP_RISK_STATE_PATH", "data/risk_002.sqlite3"))
    row = _row(path, "SELECT payload_json FROM risk_state WHERE singleton = 1")
    payload = _decode_json(row[0]) if row else None
    if payload is None:
        return None
    halt = payload.get("global_halt")
    halt_dict = halt if isinstance(halt, dict) else {}
    active = bool(halt_dict.get("active", False))
    exposure = payload.get("exposure")
    exposure_dict = exposure if isinstance(exposure, dict) else {}
    trusted = (
        bool(payload.get("account_trusted", False))
        and bool(payload.get("marks_trusted", False))
        and bool(payload.get("reconciliation_complete", False))
        and bool(exposure_dict.get("trusted", False))
    )
    reason = (
        str(halt_dict.get("reason", "global_risk_halt"))
        if active
        else "risk_002_reconciled"
        if trusted
        else "risk_002_not_reconciled_or_untrusted"
    )
    return {
        "state": GateState.PASS.value if trusted else GateState.BLOCKED.value,
        "provider_mode": "real",
        "source": "risk-002",
        "path": str(path),
        "active": active,
        "reason": reason,
        "account_trusted": bool(payload.get("account_trusted", False)),
        "marks_trusted": bool(payload.get("marks_trusted", False)),
        "reconciliation_complete": bool(payload.get("reconciliation_complete", False)),
        "account_observed_monotonic_ns": payload.get("account_observed_monotonic_ns"),
        "global_halt": halt,
        "strategy_halts": payload.get("strategy_halts", []),
        "current_equity": payload.get("current_equity"),
        "peak_session_equity": payload.get("peak_session_equity"),
        "drawdown": payload.get("drawdown"),
        "realised_pnl": payload.get("realised_pnl"),
        "unrealised_pnl": payload.get("unrealised_pnl"),
        "exposure": exposure_dict,
    }


def _account_provider(risk: dict[str, Any] | None) -> dict[str, Any] | None:
    if risk is None:
        return None
    trusted = bool(risk.get("account_trusted", False)) and bool(
        risk.get("reconciliation_complete", False)
    )
    return {
        "state": GateState.PASS.value if trusted else GateState.BLOCKED.value,
        "provider_mode": "real",
        "source": "risk-002-authoritative-account",
        "trusted": trusted,
        "reason": "account_reconciled" if trusted else "account_untrusted_or_unreconciled",
        "observed_monotonic_ns": risk.get("account_observed_monotonic_ns"),
    }


def _in_process_provider(values: dict[str, str], name: str) -> dict[str, Any] | None:
    provider = _json(_accepted_status_dir(values) / f"{name}.json")
    if provider is None:
        return None
    age = _age(provider.get("observed_at"))
    max_age = float(
        values.get("PREDICTIONS_CUP_FULLSTACK_MAX_PROVIDER_AGE_SECONDS", "5")
    )
    if age is None or age > max_age:
        return {
            **provider,
            "state": GateState.BLOCKED.value,
            "reason": "provider_status_stale",
            "status_age_seconds": age,
        }
    return {**provider, "status_age_seconds": age}


def _observe_provider(
    values: dict[str, str],
    capability: CapabilityConfig,
) -> dict[str, Any] | None:
    if capability.mode is CapabilityMode.EXTERNAL_SERVICE:
        return _provider(values, "observe")
    path = _accepted_status_dir(values) / "observe.json"
    status = read_observation_health_status(
        path,
        expected_owner="predictions-cup-maker.service",
        max_age_seconds=float(
            values.get("PREDICTIONS_CUP_FULLSTACK_MAX_PROVIDER_AGE_SECONDS", "5")
        ),
    )
    mapped = {
        ObservationStatusState.HEALTHY: GateState.PASS,
        ObservationStatusState.DEGRADED: GateState.DEGRADED,
    }
    state = mapped.get(status.state, GateState.BLOCKED)
    return {
        "state": state.value,
        "provider_mode": "real",
        "capability_mode": CapabilityMode.IN_PROCESS.value,
        "owner_services": list(capability.owner_services),
        "reason": status.reason or status.state.value.lower(),
        "observed_at": None if status.observed_at is None else status.observed_at.isoformat(),
        "process_instance_id": status.process_instance_id,
        "owner": status.owner,
        "health": None if status.health is None else dict(status.health),
        "status_path": str(path),
    }


def _shadow_evidence(values: dict[str, str]) -> dict[str, Any]:
    path = Path(
        values.get(
            "PREDICTIONS_CUP_SHADOW_JOURNAL_PATH",
            "data/shadow_002/events.jsonl",
        )
    )
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "candidates": _candidates(path),
    }


def collect_status(repo: Path, values: dict[str, str]) -> dict[str, Any]:
    usage = shutil.disk_usage(repo)
    sig = _sig(values)
    pm = _pm(values) if env_flag(values, "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED") else None
    capabilities = capability_configs(values)
    risk = _risk_provider(values)
    live_learn = (
        _provider(values, "live-learn")
        if capabilities["live_learn"].mode is CapabilityMode.EXTERNAL_SERVICE
        else _in_process_provider(values, "live-learn")
    )
    observe = (
        _observe_provider(values, capabilities["observe"])
        if capabilities["observe"].enabled
        else None
    )
    shadow = _in_process_provider(values, "shadow")
    research_storage = (sig.get("health") or {}).get("research_storage")
    queue_health = research_storage if isinstance(research_storage, dict) else None
    clock_threshold_raw = values.get("PREDICTIONS_CUP_FULLSTACK_MAX_CLOCK_OFFSET_SECONDS")
    clock = clock_health(
        threshold_seconds=(
            float(clock_threshold_raw) if clock_threshold_raw is not None else None
        )
    )
    storage = storage_health(repo, values, queue_health=queue_health)
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(repo),
        "environment": values.get("PREDICTIONS_CUP_ENVIRONMENT", "development"),
        "runtime_profile": values.get("PREDICTIONS_CUP_FULLSTACK_PROFILE", "REHEARSAL"),
        "trading_enabled": env_flag(values, "PREDICTIONS_CUP_TRADING_ENABLED"),
        "maker_enabled": env_flag(values, "PREDICTIONS_CUP_MAKER_ENABLED"),
        "shadow_enabled": env_flag(values, "PREDICTIONS_CUP_SHADOW_ENABLED"),
        "services": [asdict(_service(unit)) for unit in configured_services(values)],
        "sig": sig,
        "polymarket": pm,
        "kalshi": _provider(values, "kalshi"),
        "account": _account_provider(risk),
        "shadow": shadow,
        "shadow_evidence": _shadow_evidence(values),
        "risk_halt": risk
        or {
            "source": "runtime.env",
            "active": env_flag(values, "PREDICTIONS_CUP_GLOBAL_KILL_SWITCH", True),
            "reason": "startup_global_kill_switch",
        },
        "execution": _execution(values),
        "live_learn": live_learn,
        "observe": observe,
        "session": read_session_pointer(values),
        "clock": clock,
        "storage": storage,
        "capabilities": {
            name: {
                "enabled": config.enabled,
                "mode": config.mode.value,
                "owner_services": list(config.owner_services),
                "external_service": config.external_service,
            }
            for name, config in capabilities.items()
        },
        "disk": {
            "path": str(repo),
            "free_bytes": usage.free,
            "free_gib": usage.free / 1024**3,
        },
        "config": {
            "runtime_env": str(runtime_env_path()),
            "schema_version": values.get(
                "PREDICTIONS_CUP_FULLSTACK_CONFIG_SCHEMA_VERSION", SCHEMA_VERSION
            ),
            "non_secret_sha256": config_hash(values),
        },
    }


def _age(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())


def evaluate_health(
    status: dict[str, Any], values: dict[str, str], *, require_real: bool
) -> dict[str, Any]:
    checks: list[Check] = []
    for service in status["services"]:
        active = service["active_state"] == "active"
        checks.append(
            Check(
                f"service:{service['unit']}",
                GateState.PASS if active else GateState.BLOCKED,
                "active" if active else f"state={service['active_state']}/{service['sub_state']}",
            )
        )
    max_age = float(values.get("PREDICTIONS_CUP_FULLSTACK_MAX_FEED_AGE_SECONDS", "60"))
    for name, surface in (("sig", status["sig"]), ("pm", status["polymarket"])):
        if surface is None:
            continue
        age = _age(surface.get("last_observation"))
        state = GateState.PASS if age is not None and age <= max_age else GateState.BLOCKED
        reason = "fresh" if state is GateState.PASS else "missing or stale observation"
        checks.append(Check(f"{name}_freshness", state, reason, {"age_seconds": age}))
    execution = status["execution"]
    unresolved = int(execution.get("unresolved_count", 0) or 0)
    uncertain = int(execution.get("uncertain_count", 0) or 0)
    checks.append(
        Check(
            "execution_recovery",
            GateState.BLOCKED if unresolved or uncertain else GateState.PASS,
            "unresolved journal operations" if unresolved or uncertain else "clean",
            {"unresolved": unresolved, "uncertain": uncertain},
        )
    )
    storage = (status["sig"].get("health") or {}).get("research_storage")
    if isinstance(storage, dict):
        depth = int(storage.get("queue_depth", 0) or 0)
        capacity = int(storage.get("queue_capacity", 0) or 0)
        fraction = depth / capacity if capacity else 0.0
        dropped = int(storage.get("dropped_rows", 0) or 0)
        failures = int(storage.get("storage_failures", 0) or 0)
        warn = float(values.get("PREDICTIONS_CUP_FULLSTACK_QUEUE_WARN_FRACTION", "0.75"))
        block = float(values.get("PREDICTIONS_CUP_FULLSTACK_QUEUE_BLOCK_FRACTION", "0.95"))
        state = (
            GateState.BLOCKED
            if dropped or failures or fraction >= block
            else GateState.DEGRADED
            if fraction >= warn
            else GateState.PASS
        )
        checks.append(Check("capture_queue", state, "bounded queue", {"fraction": fraction}))
    else:
        checks.append(Check("capture_queue", GateState.DEGRADED, "queue health unavailable"))
    free = float(status["disk"]["free_gib"])
    block_disk = float(values.get("PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB", "3"))
    warn_disk = float(values.get("PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB", "8"))
    disk_state = (
        GateState.BLOCKED
        if free < block_disk
        else GateState.DEGRADED
        if free < warn_disk
        else GateState.PASS
    )
    checks.append(Check("disk", disk_state, f"free={free:.2f}GiB"))
    risk = status["risk_halt"]
    if isinstance(risk, dict):
        raw_risk_state = str(risk.get("state", "PASS"))
        if risk.get("source") != "runtime.env":
            risk_state = (
                GateState(raw_risk_state)
                if raw_risk_state in GateState._value2member_map_
                else GateState.UNKNOWN
            )
            checks.append(
                Check(
                    "risk_provider",
                    risk_state,
                    str(risk.get("reason", "risk provider")),
                )
            )
        elif status["maker_enabled"] and require_real:
            checks.append(
                Check(
                    "risk_provider",
                    GateState.BLOCKED,
                    "real merged Risk provider required",
                )
            )
        if bool(risk.get("active", False)):
            checks.append(
                Check(
                    "risk_halt",
                    GateState.BLOCKED if require_real else GateState.DEGRADED,
                    str(risk.get("reason", "global risk halt active")),
                )
            )
    configured_units = {service["unit"] for service in status["services"]}
    for key, capability in capability_configs(values).items():
        if not capability.enabled:
            continue
        provider = status[key]
        missing_owners = [
            owner for owner in capability.owner_services if owner not in configured_units
        ]
        if capability.mode is CapabilityMode.IN_PROCESS and missing_owners:
            checks.append(
                Check(
                    key,
                    GateState.BLOCKED if require_real else GateState.DEGRADED,
                    "in-process owner service is not part of configured runtime",
                    {"missing_owner_services": missing_owners},
                )
            )
        if not provider:
            state = GateState.BLOCKED if require_real else GateState.DEGRADED
            checks.append(
                Check(
                    key,
                    state,
                    "provider health unavailable",
                    {
                        "capability_mode": capability.mode.value,
                        "owner_services": list(capability.owner_services),
                    },
                )
            )
            continue
        provider_mode = provider.get("provider_mode")
        provider_capability_mode = provider.get("capability_mode")
        provider_owners = tuple(provider.get("owner_services", ()))
        if require_real and provider_mode != "real":
            checks.append(Check(key, GateState.BLOCKED, "real provider required"))
            continue
        if require_real and provider_capability_mode != capability.mode.value:
            checks.append(
                Check(
                    key,
                    GateState.BLOCKED,
                    "provider capability ownership mode mismatch",
                    {
                        "expected": capability.mode.value,
                        "actual": provider_capability_mode,
                    },
                )
            )
            continue
        if (
            require_real
            and capability.mode is CapabilityMode.IN_PROCESS
            and provider_owners != capability.owner_services
        ):
            checks.append(
                Check(
                    key,
                    GateState.BLOCKED,
                    "in-process provider owner mismatch",
                    {
                        "expected": list(capability.owner_services),
                        "actual": list(provider_owners),
                    },
                )
            )
            continue
        raw = str(provider.get("state", "DEGRADED"))
        state = GateState(raw) if raw in GateState._value2member_map_ else GateState.UNKNOWN
        checks.append(
            Check(
                key,
                state,
                str(provider.get("reason", "provider")),
                {
                    "capability_mode": capability.mode.value,
                    "owner_services": list(capability.owner_services),
                },
            )
        )
    if status["maker_enabled"]:
        for key in ("account", "shadow"):
            provider = status[key]
            if provider is None:
                state = GateState.BLOCKED if require_real else GateState.DEGRADED
                checks.append(Check(key, state, "runtime provider not integrated yet"))
                continue
            raw = str(provider.get("state", "PASS"))
            provider_state = (
                GateState(raw) if raw in GateState._value2member_map_ else GateState.UNKNOWN
            )
            checks.append(
                Check(
                    f"{key}_provider",
                    provider_state,
                    str(provider.get("reason", f"{key} provider")),
                )
            )
        account = status["account"]
        if account is not None and not bool(account.get("trusted", False)):
            checks.append(Check("account_trust", GateState.BLOCKED, "account trust lost"))
        if status["shadow_enabled"]:
            candidates = status["shadow_evidence"].get("candidates", [])
            if not candidates:
                state = GateState.BLOCKED if require_real else GateState.DEGRADED
                checks.append(Check("shadow_decisions", state, "no candidate decisions observed"))
    if require_real:
        for name in ("clock", "storage"):
            watchdog = status.get(name)
            if not isinstance(watchdog, dict):
                checks.append(Check(name, GateState.BLOCKED, "watchdog unavailable"))
                continue
            raw = str(watchdog.get("state", "UNKNOWN"))
            mapped = {
                "HEALTHY": GateState.PASS,
                "DEGRADED": GateState.DEGRADED,
                "BLOCKED": GateState.BLOCKED,
                "UNKNOWN": GateState.BLOCKED,
                "NOT_CONFIGURED": GateState.BLOCKED,
            }.get(raw, GateState.BLOCKED)
            checks.append(
                Check(
                    name,
                    mapped,
                    ",".join(str(code) for code in watchdog.get("reason_codes", ())) or "healthy",
                )
            )
        session = status.get("session")
        session_ok = isinstance(session, dict) and session.get("state") == "HEALTHY"
        checks.append(
            Check(
                "session_provenance",
                GateState.PASS if session_ok else GateState.BLOCKED,
                "manifest persisted" if session_ok else "launch-session manifest unavailable",
            )
        )
    states = {check.state for check in checks}
    overall = (
        GateState.BLOCKED
        if GateState.BLOCKED in states
        else GateState.DEGRADED
        if GateState.DEGRADED in states or GateState.UNKNOWN in states
        else GateState.PASS
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "state": overall.value,
        "observed_at": datetime.now(UTC).isoformat(),
        "checks": [asdict(item) | {"state": item.state.value} for item in checks],
    }


def _synthetic_status(values: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    now = datetime.now(UTC).isoformat()
    injection_values = dict(values)
    injection_values.update(
        {
            "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED": "true",
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_SHADOW_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB": "3",
            "PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB": "8",
            "PREDICTIONS_CUP_FULLSTACK_MAX_FEED_AGE_SECONDS": "60",
        }
    )
    services = [
        {
            "unit": unit,
            "active_state": "active",
            "sub_state": "running",
            "enabled_state": "enabled",
            "main_pid": 1,
            "uptime_seconds": 60.0,
            "restarts": 0,
        }
        for unit in configured_services(injection_values)
    ]
    status: dict[str, Any] = {
        "services": services,
        "sig": {
            "last_observation": now,
            "health": {
                "research_storage": {
                    "queue_depth": 1,
                    "queue_capacity": 100,
                    "dropped_rows": 0,
                    "storage_failures": 0,
                }
            },
        },
        "polymarket": {"last_observation": now},
        "account": {
            "state": "PASS",
            "reason": "synthetic baseline",
            "trusted": True,
            "last_observation": now,
        },
        "shadow": {
            "state": "PASS",
            "reason": "synthetic baseline",
        },
        "shadow_evidence": {
            "candidates": [{"candidate_id": "fixture", "candidate_version": "v1"}]
        },
        "risk_halt": {
            "source": "synthetic-risk-provider",
            "state": "PASS",
            "reason": "synthetic baseline",
            "active": False,
        },
        "execution": {
            "pending_count": 0,
            "uncertain_count": 0,
            "unresolved_count": 0,
        },
        "live_learn": {
            "state": "PASS",
            "provider_mode": "real",
            "capability_mode": CapabilityMode.IN_PROCESS.value,
            "owner_services": [MAKE],
            "reason": "synthetic baseline",
        },
        "observe": {
            "state": "PASS",
            "provider_mode": "real",
            "capability_mode": CapabilityMode.IN_PROCESS.value,
            "owner_services": [MAKE],
            "reason": "synthetic baseline",
        },
        "disk": {"free_gib": 100.0},
        "maker_enabled": True,
        "shadow_enabled": True,
    }
    return status, injection_values


def failure_injection_matrix(values: dict[str, str]) -> dict[str, Any]:
    baseline, injection_values = _synthetic_status(values)
    stale = datetime.fromtimestamp(time.time() - 3600, UTC).isoformat()
    cases: dict[str, tuple[dict[str, Any], str]] = {}

    sig_dead = json.loads(json.dumps(baseline))
    sig_dead["sig"]["last_observation"] = stale
    cases["sig_realtime_dies"] = (sig_dead, GateState.BLOCKED.value)

    pm_dead = json.loads(json.dumps(baseline))
    pm_dead["polymarket"]["last_observation"] = stale
    cases["pm_feed_dies"] = (pm_dead, GateState.BLOCKED.value)

    account_lost = json.loads(json.dumps(baseline))
    account_lost["account"]["trusted"] = False
    account_lost["account"]["reason"] = "account trust lost"
    cases["account_trust_lost"] = (account_lost, GateState.BLOCKED.value)

    observer_dead = json.loads(json.dumps(baseline))
    observer_dead["observe"]["state"] = "BLOCKED"
    observer_dead["observe"]["reason"] = "in-process OBSERVE capability unhealthy"
    cases["observer_process_dies"] = (observer_dead, GateState.BLOCKED.value)

    disk_low = json.loads(json.dumps(baseline))
    disk_low["disk"]["free_gib"] = 1.0
    cases["disk_low"] = (disk_low, GateState.BLOCKED.value)

    queue_high = json.loads(json.dumps(baseline))
    storage = queue_high["sig"]["health"]["research_storage"]
    storage["queue_depth"] = 99
    cases["capture_queue_high"] = (queue_high, GateState.BLOCKED.value)

    unresolved = json.loads(json.dumps(baseline))
    unresolved["execution"]["unresolved_count"] = 1
    unresolved["execution"]["uncertain_count"] = 1
    cases["unresolved_execution"] = (unresolved, GateState.BLOCKED.value)

    stale_fv = json.loads(json.dumps(baseline))
    stale_fv["risk_halt"]["state"] = "BLOCKED"
    stale_fv["risk_halt"]["reason"] = "stale external FV"
    cases["stale_external_fv"] = (stale_fv, GateState.BLOCKED.value)

    global_halt = json.loads(json.dumps(baseline))
    global_halt["risk_halt"]["active"] = True
    global_halt["risk_halt"]["reason"] = "global risk halt active"
    cases["risk_global_halt"] = (global_halt, GateState.BLOCKED.value)

    candidate_throws = json.loads(json.dumps(baseline))
    candidate_throws["shadow"]["state"] = "DEGRADED"
    candidate_throws["shadow"]["reason"] = "candidate exception isolated"
    cases["candidate_throws"] = (candidate_throws, GateState.DEGRADED.value)

    learner_backlog = json.loads(json.dumps(baseline))
    learner_backlog["live_learn"]["state"] = "DEGRADED"
    learner_backlog["live_learn"]["reason"] = "backlog above warning threshold"
    cases["live_learn_backlog"] = (learner_backlog, GateState.DEGRADED.value)

    results: list[dict[str, Any]] = []
    for name, (status, expected) in cases.items():
        health = evaluate_health(status, injection_values, require_real=True)
        actual = str(health["state"])
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    passed = all(bool(item["passed"]) for item in results)
    return {
        "state": GateState.PASS.value if passed else GateState.BLOCKED.value,
        "evidence_state": (
            EvidenceState.SIMULATION_PASS.value
            if passed
            else EvidenceState.BLOCKED.value
        ),
        "simulation_only": True,
        "cases": results,
    }


def kill_switch_probe() -> dict[str, Any]:
    from predictions_cup.maker.safety import MakerKillSwitch

    switch = MakerKillSwitch()
    initially_clear = not switch.active
    switch.activate("fullstack_rehearsal_probe")
    first_reason = switch.reason
    switch.activate("must_not_replace_first_reason")
    passed = (
        initially_clear
        and switch.active
        and first_reason == "fullstack_rehearsal_probe"
        and switch.reason == first_reason
    )
    return {
        "state": GateState.PASS.value if passed else GateState.BLOCKED.value,
        "evidence_state": (
            EvidenceState.SIMULATION_PASS.value
            if passed
            else EvidenceState.BLOCKED.value
        ),
        "simulation_only": True,
        "initially_clear": initially_clear,
        "active_after": switch.active,
        "reason_latched": switch.reason == first_reason,
        "economic_order_sent": False,
    }


def capture_continuity(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    real_exercise: bool = False,
    require_observation_advance: bool = False,
) -> dict[str, Any]:
    before_storage = (before["sig"].get("health") or {}).get("research_storage")
    after_storage = (after["sig"].get("health") or {}).get("research_storage")
    evidence_on_pass = (
        EvidenceState.REAL_PASS.value
        if real_exercise
        else EvidenceState.NOT_RUN.value
    )
    if not isinstance(before_storage, dict) or not isinstance(after_storage, dict):
        return {
            "state": GateState.BLOCKED.value if real_exercise else GateState.DEGRADED.value,
            "evidence_state": (
                EvidenceState.BLOCKED.value
                if real_exercise
                else EvidenceState.NOT_RUN.value
            ),
            "reason": "capture publication counters unavailable",
        }
    before_shards = int(before_storage.get("written_shards", 0) or 0)
    after_shards = int(after_storage.get("written_shards", 0) or 0)
    dropped = int(after_storage.get("dropped_rows", 0) or 0)
    failures = int(after_storage.get("storage_failures", 0) or 0)
    observation_advanced = True
    if require_observation_advance:
        before_observed = _parse_observation(before["sig"].get("last_observation"))
        after_observed = _parse_observation(after["sig"].get("last_observation"))
        observation_advanced = (
            before_observed is not None
            and after_observed is not None
            and after_observed > before_observed
        )
    passed = (
        after_shards >= before_shards
        and dropped == 0
        and failures == 0
        and observation_advanced
    )
    return {
        "state": GateState.PASS.value if passed else GateState.BLOCKED.value,
        "evidence_state": evidence_on_pass if passed else EvidenceState.BLOCKED.value,
        "reason": "capture counters monotonic and observations resumed"
        if passed
        else "capture continuity failure",
        "before_written_shards": before_shards,
        "after_written_shards": after_shards,
        "observation_advanced": observation_advanced,
        "dropped_rows": dropped,
        "storage_failures": failures,
    }


def _mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "sha256": None, "version": None}
    raw = path.read_bytes()
    doc = _decode_json(raw)
    version = None if doc is None else doc.get("mapping_version") or doc.get("schema_version")
    return {
        "path": str(path),
        "exists": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "version": version,
    }


def _candidates(path: Path) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    if not path.exists():
        return []
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > 8 * 1024**2:
            handle.seek(size - 8 * 1024**2)
            handle.readline()
        for line in handle:
            item = _decode_json(line)
            if not item:
                continue
            raw_payload = item.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else item
            cid = payload.get("candidate_id")
            version = payload.get("candidate_version")
            if isinstance(cid, str) and isinstance(version, str):
                found[(cid, version)] = {"candidate_id": cid, "candidate_version": version}
    return [found[key] for key in sorted(found)]


def launch_snapshot(repo: Path, values: dict[str, str]) -> dict[str, Any]:
    mapping = Path(
        values.get("PREDICTIONS_CUP_MAKER_MAPPING_PATH", "data/mappings/sig_polymarket_2026.json")
    )
    shadow = Path(values.get("PREDICTIONS_CUP_SHADOW_JOURNAL_PATH", "data/shadow_002/events.jsonl"))
    sha = _git_sha(repo)
    risk = {
        k: v
        for k, v in nonsecret_config(values).items()
        if k.startswith("PREDICTIONS_CUP_RISK_")
        or k
        in {
            "PREDICTIONS_CUP_EXECUTION_MODE",
            "PREDICTIONS_CUP_GLOBAL_KILL_SWITCH",
            "PREDICTIONS_CUP_TRADING_ENABLED",
        }
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "git_sha": sha,
        "config_schema_version": values.get(
            "PREDICTIONS_CUP_FULLSTACK_CONFIG_SCHEMA_VERSION", SCHEMA_VERSION
        ),
        "non_secret_config_sha256": config_hash(values),
        "mapping": _mapping(mapping),
        "enabled_candidates_observed": _candidates(shadow),
        "risk_profile": risk,
        "service_versions": {unit: {"git_sha": sha} for unit in configured_services(values)},
        "capabilities": {
            name: {
                "enabled": config.enabled,
                "mode": config.mode.value,
                "owner_services": list(config.owner_services),
                "external_service": config.external_service,
            }
            for name, config in capability_configs(values).items()
        },
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "region": values.get("PREDICTIONS_CUP_AWS_REGION")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION"),
            "runtime_profile": values.get("PREDICTIONS_CUP_FULLSTACK_PROFILE", "REHEARSAL"),
        },
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        tmp = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def safe_restart(
    values: dict[str, str],
    timeout_seconds: float = 90.0,
    *,
    require_observation_advance: bool = True,
) -> dict[str, Any]:
    services = list(configured_services(values))
    consumers = [unit for unit in (LEARN, OBSERVE) if unit in services]
    maker = [MAKE] if MAKE in services else []
    captures = [unit for unit in (SIG, PM) if unit in services]
    previous = {
        "sig": _feed_observation(values, "sig"),
        "pm": (
            _feed_observation(values, "pm")
            if PM in captures
            else None
        ),
    }
    actions: list[dict[str, Any]] = []
    order = (
        ("stop", [*consumers, *maker, *captures]),
        ("start", [*captures, *maker, *consumers]),
    )
    for action, units in order:
        for unit in units:
            result = _run([*_systemctl(), action, unit], timeout_seconds)
            actions.append({"action": action, "unit": unit, "returncode": result.returncode})
            if result.returncode:
                payload = {
                    "state": GateState.BLOCKED.value,
                    "evidence_state": EvidenceState.BLOCKED.value,
                    "provider_mode": "real",
                    "reason": f"{action} failed for {unit}",
                    "actions": actions,
                    "economic_order_sent": False,
                }
                return (
                    _write_exercise_evidence(values, "safe_restart", payload)
                    if require_observation_advance
                    else payload
                )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(_service(unit).active_state == "active" for unit in services):
            break
        time.sleep(0.5)
    else:
        payload = {
            "state": GateState.BLOCKED.value,
            "evidence_state": EvidenceState.BLOCKED.value,
            "provider_mode": "real",
            "reason": "services did not return active before timeout",
            "actions": actions,
            "economic_order_sent": False,
        }
        return (
            _write_exercise_evidence(values, "safe_restart", payload)
            if require_observation_advance
            else payload
        )

    if not require_observation_advance:
        return {
            "state": GateState.PASS.value,
            "evidence_state": EvidenceState.SIMULATION_PASS.value,
            "simulation_only": True,
            "reason": "ordered restart sequence passed without real observation-resume gate",
            "actions": actions,
            "economic_order_sent": False,
        }

    resume = {
        "sig": _wait_for_feed_resume(values, "sig", previous["sig"], timeout_seconds),
    }
    if PM in captures:
        resume["pm"] = _wait_for_feed_resume(
            values,
            "pm",
            previous["pm"],
            timeout_seconds,
        )
    resumed = all(bool(item["resumed"]) for item in resume.values())
    payload = {
        "state": GateState.PASS.value if resumed else GateState.BLOCKED.value,
        "evidence_state": (
            EvidenceState.REAL_PASS.value if resumed else EvidenceState.BLOCKED.value
        ),
        "provider_mode": "real",
        "reason": (
            "ordered graceful restart complete and feed observations resumed"
            if resumed
            else "services active but post-restart feed observations did not resume"
        ),
        "actions": actions,
        "observation_resume": resume,
        "economic_order_sent": False,
    }
    return _write_exercise_evidence(values, "safe_restart", payload)

def _exercise_path(values: dict[str, str], name: str) -> Path:
    return _status_dir(values) / "exercises" / f"{name}.json"


def _exercise_evidence(values: dict[str, str], name: str) -> dict[str, Any]:
    evidence = _json(_exercise_path(values, name))
    if evidence is None:
        return {
            "name": name,
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "real exercise evidence not present",
        }
    return evidence


def _write_exercise_evidence(
    values: dict[str, str],
    name: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "observed_at": datetime.now(UTC).isoformat(),
        **evidence,
    }
    write_json_atomic(_exercise_path(values, name), payload)
    return payload


def _parse_observation(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _feed_observation(values: dict[str, str], feed: str) -> str | None:
    surface = _sig(values) if feed == "sig" else _pm(values)
    value = surface.get("last_observation")
    return value if isinstance(value, str) else None


def _wait_for_feed_resume(
    values: dict[str, str],
    feed: str,
    previous: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    previous_at = _parse_observation(previous)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = _feed_observation(values, feed)
        current_at = _parse_observation(current)
        if current_at is not None:
            advanced = previous_at is None or current_at > previous_at
            age = _age(current)
            if advanced and age is not None and age <= float(
                values.get("PREDICTIONS_CUP_FULLSTACK_MAX_FEED_AGE_SECONDS", "60")
            ):
                return {
                    "resumed": True,
                    "previous_observation": previous,
                    "post_observation": current,
                    "post_age_seconds": age,
                }
        time.sleep(0.25)
    return {
        "resumed": False,
        "previous_observation": previous,
        "post_observation": _feed_observation(values, feed),
    }


def exercise_reconnect(
    values: dict[str, str],
    feed: str,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    if feed not in {"sig", "pm"}:
        raise ValueError("feed must be sig or pm")
    if feed == "pm" and not env_flag(values, "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED"):
        return _write_exercise_evidence(
            values,
            "pm_reconnect",
            {
                "evidence_state": EvidenceState.NOT_RUN.value,
                "provider_mode": "real",
                "reason": "Polymarket capture is disabled",
                "economic_order_sent": False,
            },
        )
    unit = SIG if feed == "sig" else PM
    previous = _feed_observation(values, feed)
    before = _sig(values) if feed == "sig" else _pm(values)
    stop = _run([*_systemctl(), "stop", unit], timeout_seconds)
    if stop.returncode:
        return _write_exercise_evidence(
            values,
            f"{feed}_reconnect",
            {
                "evidence_state": EvidenceState.BLOCKED.value,
                "provider_mode": "real",
                "reason": f"failed to stop {unit}",
                "economic_order_sent": False,
            },
        )
    start = _run([*_systemctl(), "start", unit], timeout_seconds)
    if start.returncode:
        return _write_exercise_evidence(
            values,
            f"{feed}_reconnect",
            {
                "evidence_state": EvidenceState.BLOCKED.value,
                "provider_mode": "real",
                "reason": f"failed to start {unit}",
                "economic_order_sent": False,
            },
        )
    resumed = _wait_for_feed_resume(values, feed, previous, timeout_seconds)
    active = _service(unit).active_state == "active"
    after = _sig(values) if feed == "sig" else _pm(values)
    continuity = (
        capture_continuity(
            {"sig": before},
            {"sig": after},
            require_observation_advance=True,
        )
        if feed == "sig"
        else None
    )
    passed = active and bool(resumed["resumed"])
    if continuity is not None:
        passed = passed and continuity["evidence_state"] == EvidenceState.REAL_PASS.value
    return _write_exercise_evidence(
        values,
        f"{feed}_reconnect",
        {
            "evidence_state": (
                EvidenceState.REAL_PASS.value if passed else EvidenceState.BLOCKED.value
            ),
            "provider_mode": "real",
            "service_active": active,
            "observation_resume": resumed,
            "capture_continuity": continuity,
            "economic_order_sent": False,
        },
    )


def run_provider_exercise(values: dict[str, str], name: str) -> dict[str, Any]:
    command_keys = {
        "risk_global_halt": "PREDICTIONS_CUP_RISK_HALT_EXERCISE_COMMAND",
        "maker_kill_cancel": "PREDICTIONS_CUP_MAKER_KILL_CANCEL_EXERCISE_COMMAND",
        "execution_recovery": "PREDICTIONS_CUP_EXECUTION_RECOVERY_EXERCISE_COMMAND",
    }
    key = command_keys[name]
    raw = values.get(key, "").strip()
    if not raw:
        return _write_exercise_evidence(
            values,
            name,
            {
                "evidence_state": EvidenceState.NOT_RUN.value,
                "provider_mode": "missing",
                "reason": f"{key} is not configured",
                "economic_order_sent": False,
            },
        )
    command = shlex.split(raw)
    env = os.environ.copy()
    env.update(values)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=float(
                values.get("PREDICTIONS_CUP_FULLSTACK_EXERCISE_TIMEOUT_SECONDS", "90")
            ),
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _write_exercise_evidence(
            values,
            name,
            {
                "evidence_state": EvidenceState.BLOCKED.value,
                "provider_mode": "real",
                "reason": f"exercise command failed: {type(exc).__name__}",
                "economic_order_sent": False,
            },
        )
    payload: dict[str, Any] | None = None
    if result.stdout.strip():
        try:
            decoded = json.loads(result.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded
    valid = (
        result.returncode == 0
        and payload is not None
        and payload.get("evidence_state") == EvidenceState.REAL_PASS.value
        and payload.get("provider_mode") == "real"
        and payload.get("economic_order_sent") is False
    )
    return _write_exercise_evidence(
        values,
        name,
        {
            "evidence_state": (
                EvidenceState.REAL_PASS.value if valid else EvidenceState.BLOCKED.value
            ),
            "provider_mode": "real",
            "reason": (
                str(payload.get("reason", "real provider exercise passed"))
                if valid and payload is not None
                else f"real provider exercise invalid or failed rc={result.returncode}"
            ),
            "provider_evidence": payload,
            "economic_order_sent": False,
        },
    )


def _checkpoint_path(values: dict[str, str], kind: str) -> Path:
    return _status_dir(values) / f"{kind}-checkpoint.json"


def create_survival_checkpoint(repo: Path, values: dict[str, str], kind: str) -> dict[str, Any]:
    boot = Path("/proc/sys/kernel/random/boot_id")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(repo),
        "boot_id": boot.read_text().strip() if boot.exists() else None,
        "services": [asdict(_service(unit)) for unit in configured_services(values)],
    }
    write_json_atomic(_checkpoint_path(values, kind), payload)
    return payload


def verify_survival_checkpoint(repo: Path, values: dict[str, str], kind: str) -> dict[str, Any]:
    before = _json(_checkpoint_path(values, kind))
    if before is None:
        return _write_exercise_evidence(
            values,
            f"{kind}_survival",
            {
                "state": GateState.BLOCKED.value,
                "evidence_state": EvidenceState.BLOCKED.value,
                "provider_mode": "real",
                "reason": f"missing {kind} checkpoint",
                "economic_order_sent": False,
            },
        )
    after = create_survival_checkpoint(repo, values, f"{kind}-after")
    current = {item["unit"]: item for item in after["services"]}
    active = all(item["active_state"] == "active" for item in after["services"])
    if kind == "ssh":
        same = all(
            item.get("main_pid") == current.get(item["unit"], {}).get("main_pid")
            for item in before["services"]
        )
        passed = active and same
    else:
        passed = active and before.get("boot_id") != after.get("boot_id")
    return _write_exercise_evidence(
        values,
        f"{kind}_survival",
        {
            "state": GateState.PASS.value if passed else GateState.BLOCKED.value,
            "evidence_state": (
                EvidenceState.REAL_PASS.value if passed else EvidenceState.BLOCKED.value
            ),
            "provider_mode": "real",
            "before": before,
            "after": after,
            "economic_order_sent": False,
        },
    )


def _provider_acceptance_evidence(
    name: str,
    provider: dict[str, Any] | None,
    capability: CapabilityConfig,
) -> dict[str, Any]:
    if not capability.enabled:
        return {
            "name": name,
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "capability disabled",
            "capability_mode": capability.mode.value,
            "owner_services": list(capability.owner_services),
        }
    if provider is None:
        return {
            "name": name,
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "provider health unavailable",
            "capability_mode": capability.mode.value,
            "owner_services": list(capability.owner_services),
        }
    real = provider.get("provider_mode") == "real"
    passed = provider.get("state") == GateState.PASS.value
    mode_matches = provider.get("capability_mode") == capability.mode.value
    owners_match = (
        capability.mode is CapabilityMode.EXTERNAL_SERVICE
        or tuple(provider.get("owner_services", ())) == capability.owner_services
    )
    accepted = real and passed and mode_matches and owners_match
    return {
        "name": name,
        "evidence_state": (
            EvidenceState.REAL_PASS.value
            if accepted
            else EvidenceState.BLOCKED.value
        ),
        "provider_mode": provider.get("provider_mode"),
        "capability_mode": capability.mode.value,
        "owner_services": list(capability.owner_services),
        "reason": provider.get("reason", "provider health"),
    }


def _watchdog_acceptance(
    name: str,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        return {
            "name": name,
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "watchdog evidence unavailable",
        }
    healthy = value.get("state") == "HEALTHY"
    return {
        "name": name,
        "evidence_state": (
            EvidenceState.REAL_PASS.value if healthy else EvidenceState.BLOCKED.value
        ),
        "provider_mode": "real",
        "reason": ",".join(str(item) for item in value.get("reason_codes", ()))
        or ("healthy" if healthy else str(value.get("state", "UNKNOWN"))),
        "evidence": value,
    }


def _session_acceptance(value: dict[str, Any] | None) -> dict[str, Any]:
    healthy = isinstance(value, dict) and value.get("state") == "HEALTHY"
    return {
        "name": "session_provenance",
        "evidence_state": (
            EvidenceState.REAL_PASS.value if healthy else EvidenceState.BLOCKED.value
        ),
        "provider_mode": "real" if healthy else "missing",
        "reason": "manifest persisted" if healthy else "session manifest unavailable",
        "evidence": value,
    }


def _real_acceptance_evidence(
    values: dict[str, str],
    status: dict[str, Any],
    *,
    restart_result: dict[str, Any] | None,
    continuity: dict[str, Any],
) -> dict[str, Any]:
    capabilities = capability_configs(values)
    exercises: dict[str, dict[str, Any]] = {
        "risk_global_halt": _exercise_evidence(values, "risk_global_halt"),
        "maker_kill_cancel": _exercise_evidence(values, "maker_kill_cancel"),
        "execution_recovery": _exercise_evidence(values, "execution_recovery"),
        "sig_reconnect": _exercise_evidence(values, "sig_reconnect"),
        "safe_restart": restart_result
        or {
            "name": "safe_restart",
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "rehearsal did not request --safe-restart",
        },
        "capture_continuity": continuity,
        "live_learn": _provider_acceptance_evidence(
            "live_learn",
            status.get("live_learn"),
            capabilities["live_learn"],
        ),
        "observe": _provider_acceptance_evidence(
            "observe",
            status.get("observe"),
            capabilities["observe"],
        ),
        "clock": _watchdog_acceptance(
            "clock",
            status.get("clock") if isinstance(status.get("clock"), dict) else None,
        ),
        "storage": _watchdog_acceptance(
            "storage",
            status.get("storage") if isinstance(status.get("storage"), dict) else None,
        ),
        "session_provenance": _session_acceptance(
            status.get("session") if isinstance(status.get("session"), dict) else None
        ),
        "ssh_survival": _exercise_evidence(values, "ssh_survival"),
    }
    if env_flag(values, "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED"):
        exercises["pm_reconnect"] = _exercise_evidence(values, "pm_reconnect")

    reboot_authorized = env_flag(
        values,
        "PREDICTIONS_CUP_FULLSTACK_REBOOT_AUTHORIZED",
        False,
    )
    if reboot_authorized:
        exercises["reboot_survival"] = _exercise_evidence(values, "reboot_survival")
    else:
        exercises["reboot_survival"] = {
            "name": "reboot_survival",
            "evidence_state": EvidenceState.NOT_RUN_REQUIRES_AUTHORIZATION.value,
            "reason": "reboot has not been authorized",
        }

    required = [
        name
        for name in exercises
        if name != "reboot_survival" or reboot_authorized
    ]
    missing = [
        name
        for name in required
        if exercises[name].get("evidence_state") != EvidenceState.REAL_PASS.value
    ]
    return {
        "evidence_state": (
            EvidenceState.REAL_PASS.value
            if not missing
            else EvidenceState.BLOCKED.value
        ),
        "required_exercises": required,
        "missing_or_failed": missing,
        "exercises": exercises,
    }


def run_rehearsal(
    repo: Path, values: dict[str, str], *, require_real: bool, restart: bool
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    snapshot = launch_snapshot(repo, values)
    snapshot_path = Path(
        values.get("PREDICTIONS_CUP_FULLSTACK_SNAPSHOT_PATH", "data/runtime/launch_snapshot.json")
    )
    write_json_atomic(snapshot_path, snapshot)
    initial_status = collect_status(repo, values)
    session_id = values.get("PREDICTIONS_CUP_SESSION_ID") or (
        "rehearsal-" + started_at.strftime("%Y%m%dT%H%M%SZ")
    )
    manifest = build_session_manifest(
        repo,
        values,
        session_id=session_id,
        started_at=started_at,
        snapshot=snapshot,
        source_state={
            "sig": initial_status.get("sig"),
            "polymarket": initial_status.get("polymarket"),
            "kalshi": initial_status.get("kalshi"),
        },
    )
    session_pointer = persist_session_manifest(values, manifest)
    before = collect_status(repo, values)
    before_health = evaluate_health(before, values, require_real=require_real)
    restart_result = None
    if restart and before_health["state"] != GateState.BLOCKED.value:
        restart_result = safe_restart(values)
    after = collect_status(repo, values)
    after_health = evaluate_health(after, values, require_real=require_real)
    control_plane = build_control_plane(after, after_health)
    control_plane_path = publish_control_plane(values, control_plane)
    overall_health = control_plane.get("overall_health")
    control_plane_state = (
        overall_health.get("state")
        if isinstance(overall_health, dict)
        else "UNKNOWN"
    )

    simulations = {
        "failure_injection": failure_injection_matrix(values),
        "local_kill_switch_probe": kill_switch_probe(),
    }
    continuity = capture_continuity(
        before,
        after,
        real_exercise=restart,
        require_observation_advance=restart,
    )
    real_acceptance = _real_acceptance_evidence(
        values,
        after,
        restart_result=restart_result,
        continuity=continuity,
    )

    states = {
        before_health["state"],
        after_health["state"],
    }
    if any(
        item["evidence_state"] == EvidenceState.BLOCKED.value
        for item in simulations.values()
    ):
        states.add(GateState.BLOCKED.value)
    if restart_result and restart_result["state"] == GateState.BLOCKED.value:
        states.add(GateState.BLOCKED.value)
    if require_real and real_acceptance["evidence_state"] != EvidenceState.REAL_PASS.value:
        states.add(GateState.BLOCKED.value)
    elif not require_real and continuity["state"] == GateState.DEGRADED.value:
        states.add(GateState.DEGRADED.value)

    state = (
        GateState.BLOCKED
        if GateState.BLOCKED.value in states
        else GateState.DEGRADED
        if GateState.DEGRADED.value in states
        else GateState.PASS
    )
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "state": state.value,
        "acceptance_mode": "REAL" if require_real else "SCAFFOLD",
        "created_at": datetime.now(UTC).isoformat(),
        "real_sig_orders_sent": False,
        "snapshot": snapshot,
        "session": session_pointer,
        "control_plane": {
            "path": str(control_plane_path),
            "state": control_plane_state,
        },
        "before": {"status": before, "health": before_health},
        "safe_restart": restart_result
        or {
            "evidence_state": EvidenceState.NOT_RUN.value,
            "reason": "not requested",
        },
        "simulation_validation": simulations,
        "capture_continuity": continuity,
        "real_acceptance": real_acceptance,
        "after": {"status": after, "health": after_health},
    }
    root = Path(values.get("PREDICTIONS_CUP_FULLSTACK_EVIDENCE_ROOT", "data/runtime/rehearsals"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    write_json_atomic(root / f"rehearsal-{stamp}.json", evidence)
    return evidence

def _human(status: dict[str, Any], health: dict[str, Any]) -> None:
    print(
        f"FULLSTACK {health['state']} sha={status.get('git_sha') or 'UNKNOWN'} "
        f"profile={status['runtime_profile']}"
    )
    print(
        f"trading={status['trading_enabled']} MAKE={status['maker_enabled']} "
        f"SHADOW={status['shadow_enabled']}"
    )
    for service in status["services"]:
        uptime = service["uptime_seconds"]
        print(
            f"{service['unit']}: {service['active_state']}/{service['sub_state']} "
            f"uptime={'?' if uptime is None else f'{uptime:.0f}s'}"
        )
    print(f"SIG last={status['sig']['last_observation']}")
    pm = status["polymarket"]
    print(f"PM last={None if pm is None else pm['last_observation']}")
    account = status["account"] or {}
    print(
        f"account last={account.get('last_observation')} "
        f"trusted={account.get('trusted', 'UNKNOWN')}"
    )
    print(f"risk halt={status['risk_halt'].get('active', 'UNKNOWN')}")
    journal = status["execution"]
    print(
        f"journal pending={journal['pending_count']} uncertain={journal['uncertain_count']} "
        f"unresolved={journal['unresolved_count']}"
    )
    storage = (status["sig"].get("health") or {}).get("research_storage") or {}
    print(
        f"capture queue={storage.get('queue_depth', 'UNKNOWN')}/"
        f"{storage.get('queue_capacity', 'UNKNOWN')} "
        f"dropped={storage.get('dropped_rows', 'UNKNOWN')} "
        f"storage_failures={storage.get('storage_failures', 'UNKNOWN')}"
    )
    shadow_evidence = status["shadow_evidence"]
    print(
        f"SHADOW health={_summary(status['shadow'])} "
        f"decisions/candidates={len(shadow_evidence.get('candidates', []))}"
    )
    print(f"disk free={status['disk']['free_gib']:.2f}GiB")
    print(
        "LIVE-LEARN="
        + _capability_summary(status, "live_learn")
        + " OBSERVE="
        + _capability_summary(status, "observe")
    )


def _summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "NOT_INSTALLED"
    return f"{value.get('state', 'UNKNOWN')}/{value.get('provider_mode', 'unknown')}"


def _capability_summary(status: dict[str, Any], name: str) -> str:
    config = status["capabilities"][name]
    if not config["enabled"]:
        return "DISABLED"
    provider = status[name]
    state = "UNKNOWN" if not isinstance(provider, dict) else provider.get("state", "UNKNOWN")
    provider_mode = (
        "missing"
        if not isinstance(provider, dict)
        else provider.get("provider_mode", "unknown")
    )
    owners = ",".join(config["owner_services"])
    return (
        f"{state}/{provider_mode}/{config['mode']}"
        f"/owner={owners}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FULLSTACK-001 operator surface")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    health = sub.add_parser("health")
    health.add_argument("--require-real", action="store_true")
    sub.add_parser("snapshot")
    sub.add_parser("failure-injection")
    reconnect = sub.add_parser("exercise-reconnect")
    reconnect.add_argument("feed", choices=("sig", "pm"))
    reconnect.add_argument("--timeout-seconds", type=float, default=60.0)
    provider_exercise = sub.add_parser("exercise-provider")
    provider_exercise.add_argument(
        "name",
        choices=("risk_global_halt", "maker_kill_cancel", "execution_recovery"),
    )
    restart = sub.add_parser("safe-restart")
    restart.add_argument("--timeout-seconds", type=float, default=90.0)
    rehearse = sub.add_parser("rehearse")
    rehearse.add_argument("--require-real", action="store_true")
    rehearse.add_argument("--safe-restart", action="store_true")
    for name in ("checkpoint", "verify"):
        command = sub.add_parser(name)
        command.add_argument("kind", choices=("ssh", "reboot"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = args.repo_root.resolve()
    values = parse_env_file(runtime_env_path())
    if args.command == "status":
        status = collect_status(repo, values)
        health = evaluate_health(status, values, require_real=False)
        control_plane = build_control_plane(status, health)
        publish_control_plane(values, control_plane)
        print(json.dumps(control_plane, sort_keys=True)) if args.json else _human(
            status, health
        )
        return 0
    if args.command == "health":
        health = evaluate_health(
            collect_status(repo, values), values, require_real=args.require_real
        )
        print(json.dumps(health, sort_keys=True))
        return 2 if health["state"] == "BLOCKED" else 1 if health["state"] == "DEGRADED" else 0
    if args.command == "snapshot":
        started_at = datetime.now(UTC)
        snapshot = launch_snapshot(repo, values)
        path = Path(
            values.get(
                "PREDICTIONS_CUP_FULLSTACK_SNAPSHOT_PATH",
                "data/runtime/launch_snapshot.json",
            )
        )
        write_json_atomic(path, snapshot)
        initial_status = collect_status(repo, values)
        session_id = values.get("PREDICTIONS_CUP_SESSION_ID") or (
            "snapshot-" + started_at.strftime("%Y%m%dT%H%M%SZ")
        )
        manifest = build_session_manifest(
            repo,
            values,
            session_id=session_id,
            started_at=started_at,
            snapshot=snapshot,
            source_state={
                "sig": initial_status.get("sig"),
                "polymarket": initial_status.get("polymarket"),
                "kalshi": initial_status.get("kalshi"),
            },
        )
        session_pointer = persist_session_manifest(values, manifest)
        print(json.dumps({"snapshot": snapshot, "session": session_pointer}, sort_keys=True))
        return 0
    if args.command == "failure-injection":
        result = failure_injection_matrix(values)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["state"] == "PASS" else 2
    if args.command == "exercise-reconnect":
        result = exercise_reconnect(values, args.feed, args.timeout_seconds)
        print(json.dumps(result, sort_keys=True))
        return (
            0
            if result["evidence_state"] == EvidenceState.REAL_PASS.value
            else 2
        )
    if args.command == "exercise-provider":
        result = run_provider_exercise(values, args.name)
        print(json.dumps(result, sort_keys=True))
        return (
            0
            if result["evidence_state"] == EvidenceState.REAL_PASS.value
            else 2
        )
    if args.command == "safe-restart":
        result = safe_restart(values, args.timeout_seconds)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["state"] == "PASS" else 2
    if args.command == "rehearse":
        result = run_rehearsal(
            repo, values, require_real=args.require_real, restart=args.safe_restart
        )
        print(result["state"])
        print(json.dumps(result, sort_keys=True))
        return 0 if result["state"] == "PASS" else 1 if result["state"] == "DEGRADED" else 2
    if args.command == "checkpoint":
        print(json.dumps(create_survival_checkpoint(repo, values, args.kind), sort_keys=True))
        return 0
    result = verify_survival_checkpoint(repo, values, args.kind)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
