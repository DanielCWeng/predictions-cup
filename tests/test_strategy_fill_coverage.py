"""Strategy coverage is read-only and must never invent realised maker/taker P&L."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "strategy_fill_coverage.py"
_SPEC = importlib.util.spec_from_file_location("strategy_fill_coverage", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
fill_coverage = _MODULE.fill_coverage


def _journal(tmp_path: Path) -> Path:
    path = tmp_path / "execution.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE execution_events (
          event_id INTEGER PRIMARY KEY, logical_operation_id TEXT,
          logical_intent_id TEXT, event_type TEXT, exchange_order_id TEXT,
          tournament_id TEXT, source_timestamp TEXT, exchange_id TEXT,
          fill_id TEXT, quantity TEXT, price TEXT, strategy_family TEXT
        );
        """
    )
    db.executemany(
        "INSERT INTO execution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "op-1", "intent-1", "SUBMISSION", None, "cup", None, None,
             None, None, None, "MAKE"),
            (2, "op-1", "intent-1", "ACK", "101", "cup", None, "1",
             None, None, None, None),
            (3, "op-1", "intent-1", "REALTIME_FILL", "101", "cup",
             "2026-10-31T12:00:01Z", "1", "fill-1", "10", ".4", "MAKE"),
            (4, "op-1", "intent-1", "AUTHORITATIVE_FILL", "101", "cup",
             "2026-10-31T12:00:01Z", "1", "fill-1", "10", ".4", "MAKE"),
            (5, "op-2", "intent-2", "REALTIME_FILL", "202", "cup",
             "2026-10-31T12:00:02Z", "2", "fill-2", "5", ".8", "TAKE"),
            (6, "op-3", "intent-3", "REALTIME_FILL", "303", "cup",
             "2026-10-31T12:00:03Z", "3", "fill-3", "7", ".6", None),
            (7, "op-4", "intent-4", "REALTIME_FILL", "404", "other",
             "2026-10-31T12:00:04Z", "4", "fill-4", "9", ".5", "TAKE"),
        ],
    )
    db.commit()
    db.close()
    return path


def test_strategy_fill_coverage_dedupes_and_never_reports_pnl(tmp_path: Path) -> None:
    path = _journal(tmp_path)
    report = fill_coverage(path, "cup")
    assert report["status"] == "EXECUTION_COVERAGE_ONLY_NOT_REALISED_PNL"
    assert report["fill_events"] == 3
    assert report["duplicate_rows_dropped"] == 1
    assert report["attributable_fill_events"] == 2
    groups = report["by_strategy_family"]
    assert isinstance(groups, dict)
    assert groups["MAKE"]["unique_fill_events"] == 1
    assert groups["MAKE"]["gross_fill_notional"] == "4.0"
    assert groups["TAKE"]["gross_fill_notional"] == "4.0"
    assert groups["UNATTRIBUTED"]["unique_fill_events"] == 1
    for name in ("MAKE", "TAKE", "UNATTRIBUTED"):
        assert groups[name]["realised_pnl"] is None


def test_strategy_fill_coverage_refuses_missing_source(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        fill_coverage(tmp_path / "missing.sqlite3", "cup")


def test_submitted_fv_take_and_realtime_take_are_one_strategy_family(
    tmp_path: Path,
) -> None:
    path = _journal(tmp_path)
    db = sqlite3.connect(path)
    db.executemany(
        "INSERT INTO execution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (8, "op-5", "intent-5", "SUBMISSION", None, "cup",
             None, None, None, None, None, "FV-TAKE"),
            (9, "op-5", "intent-5", "ACK", "505", "cup",
             None, "5", None, None, None, None),
            (10, "op-5", "intent-5", "REALTIME_FILL", "505", "cup",
             "2026-10-31T12:00:05Z", "5", "fill-5", "20", ".35", "TAKE"),
        ],
    )
    db.commit()
    db.close()
    report = fill_coverage(path, "cup")
    families = report["by_strategy_family"]
    assert isinstance(families, dict)
    assert families["TAKE"]["unique_fill_events"] == 2
    assert families["TAKE"]["gross_fill_notional"] == "11.00"
    assert report["conflicting_order_attributions"] == 0
    assert report["attributable_fill_events"] == 3
