"""Read-only adapters over existing runtime state."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from predictions_cup.config import AppSettings
from predictions_cup.observe import (
    ObservationStatusState,
    default_observation_health_status_path,
    read_observation_health_status,
)
from predictions_cup.supervisor.contracts import HostRole, SourceStatus, utc_now


@dataclass(frozen=True, slots=True)
class SourceCollection:
    statuses: tuple[SourceStatus, ...]
    sections: Mapping[str, object]


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(UTC)


def _age(now: datetime, observed: datetime | None) -> float | None:
    return None if observed is None else (now - observed).total_seconds()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _ro_connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=250")
    return connection


def _last_complete_jsonl(path: Path, limit: int = 131_072) -> dict[str, object] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - limit))
        chunk = handle.read()
    for raw in reversed(chunk.splitlines()):
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            return {str(key): value for key, value in decoded.items()}
    return None


class SupervisorSources:
    """Cheap read-only process-boundary probes."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        repo_root: Path,
        host_role: HostRole,
        expected_services: tuple[str, ...],
        observe_max_age_seconds: float = 5.0,
        capture_max_age_seconds: float = 30.0,
        shadow_max_age_seconds: float = 15.0,
        learn_max_age_seconds: float = 600.0,
        sig_progress_max_age_seconds: float = 60.0,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root
        self.host_role = host_role
        self.expected_services = expected_services
        self.observe_max_age_seconds = observe_max_age_seconds
        self.capture_max_age_seconds = capture_max_age_seconds
        self.shadow_max_age_seconds = shadow_max_age_seconds
        self.learn_max_age_seconds = learn_max_age_seconds
        self.sig_progress_max_age_seconds = sig_progress_max_age_seconds
        self._sig_bulk_refresh_count: int | None = None
        self._sig_bulk_refresh_progress_at = time.monotonic()
        self.host_id = socket.gethostname()
        self.git_head = self._git_head()
        self._system_cache_at = 0.0
        self._system_cache: dict[str, object] = {}
        self._clock_cache_at = 0.0
        self._clock_cache: dict[str, object] = {}

    def read_all(self) -> SourceCollection:
        statuses: list[SourceStatus] = []
        sections: dict[str, object] = {}
        for name, reader in (
            ("observe", self._read_observe),
            ("risk", self._read_risk),
            ("execution", self._read_execution),
            ("sig_capture", self._read_sig_capture),
            ("polymarket_capture", self._read_polymarket_capture),
            ("shadow", self._read_shadow),
            ("live_learn", self._read_live_learn),
            ("system", self._read_system),
            ("clock", self._read_clock),
        ):
            status, section = reader()
            statuses.append(status)
            sections[name] = section
        return SourceCollection(tuple(statuses), sections)

    def _read_observe(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        required = self.host_role is HostRole.WEST_EXECUTION and self.settings.maker_enabled
        path = default_observation_health_status_path(
            _resolve(self.repo_root, self.settings.sig_research_path)
        )
        read = read_observation_health_status(
            path,
            expected_owner="predictions-cup-maker.service",
            max_age_seconds=self.observe_max_age_seconds,
            now=now,
        )
        available = read.state is not ObservationStatusState.MISSING
        valid = read.state not in {
            ObservationStatusState.INVALID,
            ObservationStatusState.OWNER_MISMATCH,
            ObservationStatusState.PROCESS_MISMATCH,
        }
        fresh = read.state is not ObservationStatusState.STALE and read.observed_at is not None
        status = SourceStatus(
            "observe",
            required,
            available,
            valid,
            fresh,
            read.observed_at,
            now,
            _age(now, read.observed_at),
            read.reason,
            {"state": read.state.value, "path": str(path)},
        )
        return status, {
            "state": read.state.value,
            "reason": read.reason,
            "process_instance_id": read.process_instance_id,
            "owner": read.owner,
            "health": dict(read.health) if read.health is not None else None,
            "path": str(path),
        }

    def _read_risk(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        required = (
            self.host_role is HostRole.WEST_EXECUTION
            and self.settings.risk_capital_control_enabled
        )
        path = _resolve(self.repo_root, self.settings.risk_state_path)
        if not path.exists():
            return self._missing("risk", required, path, now)
        try:
            with _ro_connect(path) as connection:
                row = connection.execute(
                    "SELECT payload_json FROM risk_state WHERE singleton = 1"
                ).fetchone()
            if row is None:
                return self._missing("risk", required, path, now, "risk_state_empty")
            raw = json.loads(str(row[0]))
            if not isinstance(raw, dict):
                raise TypeError("risk payload must be object")
            section = {str(key): value for key, value in raw.items()}
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._invalid("risk", required, path, now, exc)
        return (
            SourceStatus(
                "risk",
                required,
                True,
                True,
                True,
                None,
                now,
                None,
                None,
                {"path": str(path), "wall_clock_age_available": False},
            ),
            section,
        )

    def _read_execution(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        required = self.host_role is HostRole.WEST_EXECUTION and self.settings.maker_enabled
        path = _resolve(self.repo_root, self.settings.execution_journal_path)
        if not path.exists():
            return self._missing("execution", required, path, now)
        try:
            with _ro_connect(path) as connection:
                counts = {
                    str(state): int(count)
                    for state, count in connection.execute(
                        """
                        SELECT lifecycle_state, COUNT(*)
                        FROM execution_envelopes
                        GROUP BY lifecycle_state
                        """
                    ).fetchall()
                }
                rows = connection.execute(
                    """
                    SELECT logical_operation_id, lifecycle_state, operation_kind, sink_mode
                    FROM execution_envelopes
                    WHERE lifecycle_state IN
                        ('PENDING','CANCEL_PENDING','UNCERTAIN','RECONCILING')
                    ORDER BY rowid DESC LIMIT 50
                    """
                ).fetchall()
            unresolved = [
                {
                    "logical_operation_id": str(row[0]),
                    "lifecycle_state": str(row[1]),
                    "operation_kind": str(row[2]),
                    "sink_mode": str(row[3]),
                }
                for row in rows
            ]
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            return self._invalid("execution", required, path, now, exc)
        return (
            SourceStatus(
                "execution",
                required,
                True,
                True,
                True,
                None,
                now,
                None,
                None,
                {"path": str(path), "wall_clock_age_available": False},
            ),
            {
                "lifecycle_counts": counts,
                "unresolved": unresolved,
                "unresolved_count": len(unresolved),
                "wall_clock_age_available": False,
                "path": str(path),
            },
        )

    def _read_sig_capture(self) -> tuple[SourceStatus, dict[str, object]]:
        path = _resolve(self.repo_root, self.settings.sig_realtime_storage_path)
        primary = self._read_health_sqlite(
            source_id="sig_capture",
            required=True,
            path=path,
            table="capture_health",
            timestamp_column="observed_at",
            payload_column="payload_json",
            max_age_seconds=self.capture_max_age_seconds,
        )
        status, section = primary
        if status.valid:
            section = dict(section)
            last_rest = _parse_datetime(section.get("last_rest_reconciliation"))
            section["last_rest_reconciliation_age_seconds"] = _age(
                status.read_at, last_rest
            )
            last_realtime = _parse_datetime(section.get("last_realtime_receive"))
            section["last_realtime_receive_age_seconds"] = _age(
                status.read_at, last_realtime
            )
            bulk_count = section.get("bulk_price_refresh_count")
            if not isinstance(bulk_count, bool) and isinstance(bulk_count, int):
                now_mono = time.monotonic()
                if (
                    self._sig_bulk_refresh_count is None
                    or bulk_count != self._sig_bulk_refresh_count
                ):
                    self._sig_bulk_refresh_count = bulk_count
                    self._sig_bulk_refresh_progress_at = now_mono
                section["bulk_price_refresh_progress_age_seconds"] = (
                    now_mono - self._sig_bulk_refresh_progress_at
                )
                section["bulk_price_refresh_progress_max_age_seconds"] = (
                    self.sig_progress_max_age_seconds
                )
            return status, section
        fallback_reason = str(section.get("reason", ""))
        fallback_error = str(section.get("error", ""))
        may_fallback = (
            fallback_reason == "capture_health_empty"
            or fallback_error.startswith("OperationalError:no such table")
        )
        if not may_fallback:
            return primary
        fallback = self._read_activity_sqlite(
            source_id="sig_capture",
            required=True,
            path=path,
            candidates=(
                ("price_observations", "rest_observed_at"),
                ("realtime_deliveries", "observed_at"),
                ("realtime_trades", "observed_at"),
                ("market_observations", "rest_observed_at"),
            ),
            max_age_seconds=self.capture_max_age_seconds,
        )
        fallback_status, fallback_section = fallback
        if fallback_status.valid:
            fallback_section["health_surface"] = "activity_fallback"
            fallback_section["health_surface_reason"] = (
                section.get("error") or section.get("reason")
            )
        return fallback

    def _read_activity_sqlite(
        self,
        *,
        source_id: str,
        required: bool,
        path: Path,
        candidates: tuple[tuple[str, str], ...],
        max_age_seconds: float,
    ) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        latest: datetime | None = None
        latest_table: str | None = None
        try:
            with _ro_connect(path) as connection:
                for table, timestamp_column in candidates:
                    try:
                        row = connection.execute(
                            f"SELECT {timestamp_column} FROM {table} "
                            f"ORDER BY rowid DESC LIMIT 1"
                        ).fetchone()
                    except sqlite3.OperationalError:
                        continue
                    observed = None if row is None else _parse_datetime(row[0])
                    if observed is not None and (latest is None or observed > latest):
                        latest = observed
                        latest_table = table
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            return self._invalid(source_id, required, path, now, exc)
        if latest is None:
            return self._missing(
                source_id,
                required,
                path,
                now,
                "capture_activity_unavailable",
            )
        age = _age(now, latest)
        fresh = age is not None and -1.0 <= age <= max_age_seconds
        return (
            SourceStatus(
                source_id,
                required,
                True,
                True,
                fresh,
                latest,
                now,
                age,
                None if fresh else "capture_activity_stale",
                {"path": str(path), "health_surface": "activity_fallback"},
            ),
            {
                "path": str(path),
                "health_surface": "activity_fallback",
                "latest_activity_table": latest_table,
                "latest_activity_at": latest.isoformat(),
            },
        )

    def _read_polymarket_capture(self) -> tuple[SourceStatus, dict[str, object]]:
        result = self._read_health_sqlite(
            source_id="polymarket_capture",
            required=self.settings.polymarket_capture_enabled,
            path=_resolve(self.repo_root, self.settings.polymarket_storage_path),
            table="ingestion_health",
            timestamp_column="recorded_at",
            payload_column="payload_json",
            max_age_seconds=self.capture_max_age_seconds,
        )
        status, section = result
        if not status.valid:
            return result
        last_message = _parse_datetime(section.get("last_message_at"))
        message_age = _age(status.read_at, last_message)
        section["last_message_age_seconds"] = message_age
        connected = section.get("websocket_connected")
        if connected is False:
            return (
                SourceStatus(
                    status.source_id,
                    status.required,
                    status.available,
                    status.valid,
                    False,
                    last_message or status.observed_at,
                    status.read_at,
                    message_age if last_message is not None else status.age_seconds,
                    "websocket_disconnected",
                    status.detail,
                ),
                section,
            )
        if (
            message_age is not None
            and message_age > self.capture_max_age_seconds
        ):
            return (
                SourceStatus(
                    status.source_id,
                    status.required,
                    status.available,
                    status.valid,
                    False,
                    last_message,
                    status.read_at,
                    message_age,
                    "feed_message_stale",
                    status.detail,
                ),
                section,
            )
        return status, section

    def _read_health_sqlite(
        self,
        *,
        source_id: str,
        required: bool,
        path: Path,
        table: str,
        timestamp_column: str,
        payload_column: str,
        max_age_seconds: float,
    ) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        if not path.exists():
            return self._missing(source_id, required, path, now)
        try:
            with _ro_connect(path) as connection:
                row = connection.execute(
                    f"SELECT {timestamp_column}, {payload_column} "
                    f"FROM {table} ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return self._missing(source_id, required, path, now, f"{table}_empty")
            observed = _parse_datetime(row[0])
            payload = json.loads(str(row[1]))
            if not isinstance(payload, dict):
                raise TypeError("health payload must be object")
            section = {str(key): value for key, value in payload.items()}
            section["health_observed_at"] = (
                None if observed is None else observed.isoformat()
            )
            section["path"] = str(path)
            age = _age(now, observed)
            fresh = (
                observed is not None
                and age is not None
                and -1.0 <= age <= max_age_seconds
            )
            status = SourceStatus(
                source_id,
                required,
                True,
                observed is not None,
                fresh,
                observed,
                now,
                age,
                None if fresh else "health_stale_or_timestamp_invalid",
                {"path": str(path)},
            )
            return status, section
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._invalid(source_id, required, path, now, exc)

    def _read_shadow(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        required = self.host_role is HostRole.WEST_EXECUTION and self.settings.shadow_enabled
        path = _resolve(self.repo_root, self.settings.shadow_journal_path)
        if not path.exists():
            return self._missing("shadow", required, path, now)
        try:
            stat = path.stat()
            observed = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            last = _last_complete_jsonl(path)
            age = _age(now, observed)
            fresh = age is not None and -1.0 <= age <= self.shadow_max_age_seconds
            section: dict[str, object] = {
                "path": str(path),
                "size_bytes": stat.st_size,
                "last_event": last,
            }
            return (
                SourceStatus(
                    "shadow",
                    required,
                    True,
                    last is not None,
                    fresh,
                    observed,
                    now,
                    age,
                    None if fresh else "shadow_journal_stale",
                    {"path": str(path)},
                ),
                section,
            )
        except OSError as exc:
            return self._invalid("shadow", required, path, now, exc)

    def _read_live_learn(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        required = (
            self.host_role is HostRole.WEST_EXECUTION and self.settings.live_learn_enabled
        )
        report_root = _resolve(self.repo_root, self.settings.live_learn_report_path)
        if not report_root.exists():
            return self._missing("live_learn", required, report_root, now)
        try:
            candidates = sorted(
                report_root.glob("**/*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                return self._missing(
                    "live_learn", required, report_root, now, "no_live_learn_report"
                )
            path = candidates[0]
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("LIVE-LEARN report must be object")
            observed = _parse_datetime(raw.get("window_end"))
            if observed is None:
                observed = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            age = _age(now, observed)
            fresh = age is not None and -1.0 <= age <= self.learn_max_age_seconds
            section = {str(key): value for key, value in raw.items()}
            section["path"] = str(path)
            return (
                SourceStatus(
                    "live_learn",
                    required,
                    True,
                    True,
                    fresh,
                    observed,
                    now,
                    age,
                    None if fresh else "live_learn_report_stale",
                    {"path": str(path)},
                ),
                section,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._invalid("live_learn", required, report_root, now, exc)

    def _read_system(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        current = time.monotonic()
        if current - self._system_cache_at >= 5.0 or not self._system_cache:
            services: dict[str, object] = {}
            for service in self.expected_services:
                services[service] = self._service_status(service)
            usage = shutil.disk_usage(self.repo_root)
            self._system_cache = {
                "services": services,
                "disk": {
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "used_fraction": usage.used / usage.total,
                },
            }
            self._system_cache_at = current
        services_raw = self._system_cache.get("services", {})
        all_active = True
        if isinstance(services_raw, dict):
            for value in services_raw.values():
                if not isinstance(value, dict) or value.get("active_state") != "active":
                    all_active = False
        return (
            SourceStatus(
                "system",
                True,
                True,
                True,
                True,
                now,
                now,
                0.0,
                None if all_active else "expected_service_not_active",
                {},
            ),
            dict(self._system_cache),
        )

    def _read_clock(self) -> tuple[SourceStatus, dict[str, object]]:
        now = utc_now()
        current = time.monotonic()
        if current - self._clock_cache_at >= 30.0 or not self._clock_cache:
            synchronized: bool | None = None
            error: str | None = None
            try:
                result = subprocess.run(
                    ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                )
                if result.returncode == 0:
                    raw = result.stdout.strip().lower()
                    if raw in {"yes", "true", "1"}:
                        synchronized = True
                    elif raw in {"no", "false", "0"}:
                        synchronized = False
                else:
                    error = f"timedatectl_exit_{result.returncode}"
            except (OSError, subprocess.SubprocessError) as exc:
                error = type(exc).__name__
            self._clock_cache = {
                "ntp_synchronized": synchronized,
                "probe_error": error,
            }
            self._clock_cache_at = current
        synchronized_value = self._clock_cache.get("ntp_synchronized")
        valid = isinstance(synchronized_value, bool)
        fresh = valid and synchronized_value is True
        return (
            SourceStatus(
                "clock",
                True,
                True,
                valid,
                fresh,
                now,
                now,
                0.0,
                None if fresh else "clock_not_confirmed_synchronized",
                {},
            ),
            dict(self._clock_cache),
        )

    def _service_status(self, service: str) -> dict[str, object]:
        properties = (
            "ActiveState",
            "SubState",
            "NRestarts",
            "MemoryCurrent",
            "MainPID",
            "ExecMainStatus",
        )
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    service,
                    "--property",
                    ",".join(properties),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "active_state": "unknown",
                "error": type(exc).__name__,
                "memory_current_bytes": None,
            }
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        memory_raw = values.get("MemoryCurrent", "")
        memory = int(memory_raw) if memory_raw.isdigit() else None
        restarts_raw = values.get("NRestarts", "")
        restarts = int(restarts_raw) if restarts_raw.isdigit() else None
        return {
            "active_state": values.get("ActiveState", "unknown"),
            "sub_state": values.get("SubState", "unknown"),
            "restart_count": restarts,
            "memory_current_bytes": memory,
            "main_pid": values.get("MainPID"),
            "exec_main_status": values.get("ExecMainStatus"),
            "probe_exit": result.returncode,
        }

    def _git_head(self) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return "UNKNOWN"
        return result.stdout.strip() or "UNKNOWN"

    def _missing(
        self,
        source_id: str,
        required: bool,
        path: Path,
        now: datetime,
        reason: str = "source_missing",
    ) -> tuple[SourceStatus, dict[str, object]]:
        return (
            SourceStatus(
                source_id,
                required,
                False,
                False,
                False,
                None,
                now,
                None,
                reason,
                {"path": str(path)},
            ),
            {"path": str(path), "reason": reason},
        )

    def _invalid(
        self,
        source_id: str,
        required: bool,
        path: Path,
        now: datetime,
        exc: BaseException,
    ) -> tuple[SourceStatus, dict[str, object]]:
        reason = f"source_invalid:{type(exc).__name__}"
        error = f"{type(exc).__name__}:{str(exc)[:200]}"
        return (
            SourceStatus(
                source_id,
                required,
                True,
                False,
                False,
                None,
                now,
                None,
                reason,
                {"path": str(path), "error": error},
            ),
            {"path": str(path), "reason": reason, "error": error},
        )
