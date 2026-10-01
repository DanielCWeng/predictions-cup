"""SQLite/WAL durability for RISK-002 state and append-only safety evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from predictions_cup.risk.capital import (
    CapitalRiskState,
    ExposureBucket,
    HaltScope,
    HaltState,
    RiskExposureSnapshot,
)


class SqliteRiskStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def load(self) -> CapitalRiskState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM risk_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        return _decode_state(str(row[0]))

    def save(
        self,
        state: CapitalRiskState,
        *,
        event_type: str,
        detail: str,
    ) -> None:
        if not event_type.strip() or not detail.strip():
            raise ValueError("risk event type/detail must not be blank")
        payload = json.dumps(_encode_state(state), separators=(",", ":"), sort_keys=True)
        with self._lock, self._connection:
            self._connection.execute(
                    """
                    INSERT INTO risk_state(singleton, payload_json)
                    VALUES (1, ?)
                    ON CONFLICT(singleton) DO UPDATE SET payload_json=excluded.payload_json
                    """,
                    (payload,),
                )
            self._connection.execute(
                """
                INSERT INTO risk_events(event_type, detail, payload_json)
                VALUES (?, ?, ?)
                """,
                (event_type, detail, payload),
            )

    def event_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM risk_events"
            ).fetchone()
        if row is None:
            return 0
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SqliteRiskStateStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


def _encode_state(state: CapitalRiskState) -> dict[str, object]:
    return {
        "session_id": state.session_id,
        "session_start_equity": str(state.session_start_equity),
        "session_start_unrealised_pnl": str(state.session_start_unrealised_pnl),
        "realised_pnl": str(state.realised_pnl),
        "unrealised_pnl": str(state.unrealised_pnl),
        "current_equity": str(state.current_equity),
        "peak_session_equity": str(state.peak_session_equity),
        "drawdown": str(state.drawdown),
        "net_external_cash_flow": str(state.net_external_cash_flow),
        "exposure": _encode_exposure(state.exposure),
        "account_trusted": state.account_trusted,
        "account_observed_monotonic_ns": state.account_observed_monotonic_ns,
        "marks_trusted": state.marks_trusted,
        "oldest_mark_observed_monotonic_ns": state.oldest_mark_observed_monotonic_ns,
        "reconciliation_complete": state.reconciliation_complete,
        "global_halt": _encode_halt(state.global_halt),
        "strategy_halts": [_encode_halt(item) for item in state.strategy_halts],
        "limit_profile_version": state.limit_profile_version,
        "realised_pnl_cursor": state.realised_pnl_cursor,
        "external_cash_flow_cursor": state.external_cash_flow_cursor,
    }


def _decode_state(payload: str) -> CapitalRiskState:
    from decimal import Decimal

    data = json.loads(payload)
    return CapitalRiskState(
        session_id=str(data["session_id"]),
        session_start_equity=Decimal(str(data["session_start_equity"])),
        session_start_unrealised_pnl=Decimal(
            str(data.get("session_start_unrealised_pnl", "0"))
        ),
        realised_pnl=Decimal(str(data["realised_pnl"])),
        unrealised_pnl=Decimal(str(data["unrealised_pnl"])),
        current_equity=Decimal(str(data["current_equity"])),
        peak_session_equity=Decimal(str(data["peak_session_equity"])),
        drawdown=Decimal(str(data["drawdown"])),
        net_external_cash_flow=Decimal(
            str(data.get("net_external_cash_flow", "0"))
        ),
        exposure=_decode_exposure(data["exposure"]),
        account_trusted=bool(data["account_trusted"]),
        account_observed_monotonic_ns=int(data["account_observed_monotonic_ns"]),
        marks_trusted=bool(data["marks_trusted"]),
        oldest_mark_observed_monotonic_ns=(
            None
            if data["oldest_mark_observed_monotonic_ns"] is None
            else int(data["oldest_mark_observed_monotonic_ns"])
        ),
        reconciliation_complete=bool(data["reconciliation_complete"]),
        global_halt=_decode_halt(data["global_halt"]),
        strategy_halts=tuple(
            item
            for raw in data["strategy_halts"]
            if (item := _decode_halt(raw)) is not None
        ),
        limit_profile_version=str(data["limit_profile_version"]),
        realised_pnl_cursor=(
            None
            if data["realised_pnl_cursor"] is None
            else str(data["realised_pnl_cursor"])
        ),
        external_cash_flow_cursor=(
            None
            if data.get("external_cash_flow_cursor") is None
            else str(data["external_cash_flow_cursor"])
        ),
    )


def _encode_exposure(exposure: RiskExposureSnapshot) -> dict[str, object]:
    return {
        "gross_exposure": exposure.gross_exposure,
        "net_directional_exposure": exposure.net_directional_exposure,
        "open_order_exposure": exposure.open_order_exposure,
        "uncertain_order_exposure": exposure.uncertain_order_exposure,
        "by_market": _encode_buckets(exposure.by_market),
        "by_strategy": _encode_buckets(exposure.by_strategy),
        "by_tournament": _encode_buckets(exposure.by_tournament),
        "by_group": _encode_buckets(exposure.by_group),
        "trusted": exposure.trusted,
        "strategy_attribution_complete": exposure.strategy_attribution_complete,
        "group_classification_complete": exposure.group_classification_complete,
    }


def _decode_exposure(raw: object) -> RiskExposureSnapshot:
    data = _dict(raw)
    return RiskExposureSnapshot(
        gross_exposure=float(str(data["gross_exposure"])),
        net_directional_exposure=float(str(data["net_directional_exposure"])),
        open_order_exposure=float(str(data["open_order_exposure"])),
        uncertain_order_exposure=float(str(data["uncertain_order_exposure"])),
        by_market=_decode_buckets(data["by_market"]),
        by_strategy=_decode_buckets(data["by_strategy"]),
        by_tournament=_decode_buckets(data["by_tournament"]),
        by_group=_decode_buckets(data["by_group"]),
        trusted=bool(data["trusted"]),
        strategy_attribution_complete=bool(
            data.get("strategy_attribution_complete", False)
        ),
        group_classification_complete=bool(
            data.get("group_classification_complete", False)
        ),
    )


def _encode_buckets(values: tuple[ExposureBucket, ...]) -> list[dict[str, object]]:
    return [{"key": item.key, "exposure": item.exposure} for item in values]


def _decode_buckets(raw: object) -> tuple[ExposureBucket, ...]:
    if not isinstance(raw, list):
        raise ValueError("invalid risk-state bucket payload")
    output: list[ExposureBucket] = []
    for item in raw:
        data = _dict(item)
        output.append(
            ExposureBucket(
                key=str(data["key"]),
                exposure=float(str(data["exposure"])),
            )
        )
    return tuple(output)


def _encode_halt(halt: HaltState | None) -> dict[str, object] | None:
    if halt is None:
        return None
    return {
        "scope": halt.scope.value,
        "scope_value": halt.scope_value,
        "reason": halt.reason,
        "tripped_monotonic_ns": halt.tripped_monotonic_ns,
        "active": halt.active,
        "reset_by": halt.reset_by,
        "reset_monotonic_ns": halt.reset_monotonic_ns,
    }


def _decode_halt(raw: object) -> HaltState | None:
    if raw is None:
        return None
    data = _dict(raw)
    return HaltState(
        scope=HaltScope(str(data["scope"])),
        scope_value=str(data["scope_value"]),
        reason=str(data["reason"]),
        tripped_monotonic_ns=int(str(data["tripped_monotonic_ns"])),
        active=bool(data["active"]),
        reset_by=None if data["reset_by"] is None else str(data["reset_by"]),
        reset_monotonic_ns=(
            None
            if data["reset_monotonic_ns"] is None
            else int(str(data["reset_monotonic_ns"]))
        ),
    )


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("invalid risk-state payload")
    return {str(key): value for key, value in raw.items()}
