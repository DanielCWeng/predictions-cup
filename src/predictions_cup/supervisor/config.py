"""Runtime configuration for SUPERVISOR-001 without expanding AppSettings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from predictions_cup.config import AppSettings
from predictions_cup.supervisor.contracts import HostRole, RemediationLevel
from predictions_cup.supervisor.remediation import RemediationConfig
from predictions_cup.supervisor.rules import SupervisorPolicy


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value is None or not value.strip() else value.strip()


def _float(name: str, default: float) -> float:
    value = _env(name)
    return default if value is None else float(value)


def _int(name: str, default: int) -> int:
    value = _env(name)
    return default if value is None else int(value)


def _optional_float(name: str) -> float | None:
    value = _env(name)
    return None if value is None else float(value)


def _csv(name: str) -> tuple[str, ...]:
    value = _env(name)
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _positive_min(*values: float | None) -> float | None:
    selected = [value for value in values if value is not None and value > 0]
    return min(selected) if selected else None


@dataclass(frozen=True, slots=True)
class SupervisorRuntimeConfig:
    repo_root: Path
    output_root: Path
    host_role: HostRole
    expected_services: tuple[str, ...]
    poll_seconds: float
    snapshot_interval_seconds: float
    event_bundle_debounce_seconds: float
    policy: SupervisorPolicy
    remediation: RemediationConfig

    @classmethod
    def from_env(
        cls,
        settings: AppSettings,
        *,
        repo_root: Path,
        role_override: HostRole | None = None,
        output_override: Path | None = None,
        remediation_level_override: RemediationLevel | None = None,
    ) -> SupervisorRuntimeConfig:
        host_role = role_override or _host_role()
        output_root = output_override or _resolve(
            repo_root,
            Path(_env("PREDICTIONS_CUP_SUPERVISOR_OUTPUT_ROOT") or "data/supervisor"),
        )
        defaults = ["predictions-cup-sig-capture.service"]
        if settings.polymarket_capture_enabled:
            defaults.append("predictions-cup-polymarket-capture.service")
        expected_services = _csv("PREDICTIONS_CUP_SUPERVISOR_EXPECTED_SERVICES")
        if not expected_services:
            expected_services = tuple(defaults)

        safe_restart = _csv("PREDICTIONS_CUP_SUPERVISOR_SAFE_RESTART_SERVICES")
        if not safe_restart:
            safe_restart = tuple(defaults)
        nonessential = _csv("PREDICTIONS_CUP_SUPERVISOR_NONESSENTIAL_SERVICES")
        safe_caches = tuple(
            Path(item).expanduser()
            for item in _csv("PREDICTIONS_CUP_SUPERVISOR_SAFE_CACHE_PATHS")
        )

        remediation_level = remediation_level_override
        if remediation_level is None:
            remediation_level = RemediationLevel(
                _int("PREDICTIONS_CUP_SUPERVISOR_MAX_REMEDIATION_LEVEL", 0)
            )

        gross_limit = _positive_min(
            settings.risk_max_gross_exposure,
            settings.risk_exploratory_max_gross_exposure,
        )
        market_limit = _positive_min(
            settings.risk_max_per_market_exposure,
            settings.risk_exploratory_max_per_market_exposure,
        )
        tournament_limit = _positive_min(
            settings.risk_max_tournament_exposure,
            settings.risk_exploratory_max_tournament_exposure,
        )
        policy = SupervisorPolicy(
            disk_warn_fraction=_float(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_WARN_FRACTION", 0.65
            ),
            disk_critical_fraction=_float(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_CRITICAL_FRACTION", 0.80
            ),
            disk_emergency_fraction=_float(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_EMERGENCY_FRACTION", 0.90
            ),
            disk_warn_free_bytes=_int(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_WARN_FREE_BYTES", 8 * 1024**3
            ),
            disk_critical_free_bytes=_int(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_CRITICAL_FREE_BYTES", 4 * 1024**3
            ),
            disk_emergency_free_bytes=_int(
                "PREDICTIONS_CUP_SUPERVISOR_DISK_EMERGENCY_FREE_BYTES", 2 * 1024**3
            ),
            service_memory_warn_bytes=_int(
                "PREDICTIONS_CUP_SUPERVISOR_MEMORY_WARN_BYTES", 650 * 1024**2
            ),
            service_memory_critical_bytes=_int(
                "PREDICTIONS_CUP_SUPERVISOR_MEMORY_CRITICAL_BYTES", 900 * 1024**2
            ),
            service_memory_growth_warn_mb_per_min=_float(
                "PREDICTIONS_CUP_SUPERVISOR_MEMORY_GROWTH_WARN_MB_PER_MIN", 35.0
            ),
            service_memory_growth_critical_mb_per_min=_float(
                "PREDICTIONS_CUP_SUPERVISOR_MEMORY_GROWTH_CRITICAL_MB_PER_MIN", 80.0
            ),
            exposure_warn_fraction=_float(
                "PREDICTIONS_CUP_SUPERVISOR_EXPOSURE_WARN_FRACTION", 0.80
            ),
            concentration_warn_fraction=_float(
                "PREDICTIONS_CUP_SUPERVISOR_CONCENTRATION_WARN_FRACTION", 0.50
            ),
            max_gross_exposure=gross_limit,
            max_per_market_exposure=market_limit,
            max_tournament_exposure=tournament_limit,
            max_adverse_selection=_optional_float(
                "PREDICTIONS_CUP_SUPERVISOR_MAX_ADVERSE_SELECTION"
            ),
            min_realised_spread=_optional_float(
                "PREDICTIONS_CUP_SUPERVISOR_MIN_REALISED_SPREAD"
            ),
            min_post_fill_markout=_optional_float(
                "PREDICTIONS_CUP_SUPERVISOR_MIN_POST_FILL_MARKOUT"
            ),
            min_economic_samples=_int(
                "PREDICTIONS_CUP_SUPERVISOR_MIN_ECONOMIC_SAMPLES", 20
            ),
        )
        remediation = RemediationConfig(
            max_level=remediation_level,
            host_role=host_role,
            supervisor_root=output_root,
            hot_capture_roots=(
                _resolve(repo_root, settings.sig_research_path),
                _resolve(repo_root, settings.polymarket_research_path),
            ),
            hot_capture_retention_hours=_float(
                "PREDICTIONS_CUP_SUPERVISOR_HOT_CAPTURE_RETENTION_HOURS", 24.0
            ),
            bundle_retention_hours=_float(
                "PREDICTIONS_CUP_SUPERVISOR_BUNDLE_RETENTION_HOURS", 48.0
            ),
            safe_cache_paths=safe_caches,
            safe_restart_services=safe_restart,
            nonessential_services=nonessential,
            restart_cooldown_seconds=_float(
                "PREDICTIONS_CUP_SUPERVISOR_RESTART_COOLDOWN_SECONDS", 300.0
            ),
            max_restarts_per_hour=_int(
                "PREDICTIONS_CUP_SUPERVISOR_MAX_RESTARTS_PER_HOUR", 2
            ),
        )
        return cls(
            repo_root=repo_root,
            output_root=output_root,
            host_role=host_role,
            expected_services=expected_services,
            poll_seconds=_float("PREDICTIONS_CUP_SUPERVISOR_POLL_SECONDS", 1.0),
            snapshot_interval_seconds=_float(
                "PREDICTIONS_CUP_SUPERVISOR_SNAPSHOT_INTERVAL_SECONDS", 60.0
            ),
            event_bundle_debounce_seconds=_float(
                "PREDICTIONS_CUP_SUPERVISOR_EVENT_BUNDLE_DEBOUNCE_SECONDS", 60.0
            ),
            policy=policy,
            remediation=remediation,
        )


def _host_role() -> HostRole:
    configured = _env("PREDICTIONS_CUP_SUPERVISOR_HOST_ROLE")
    if configured is not None:
        return HostRole(configured)
    role_file = Path.home() / ".config" / "predictions-cup" / "host-role"
    if role_file.exists():
        return HostRole(role_file.read_text(encoding="utf-8").strip())
    return HostRole.WEST_EXECUTION
