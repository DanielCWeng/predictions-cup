"""Regression tests for capture host-survival systemd budgets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_capture_units_have_bounded_memory_cpu_and_restart_rate():
    for name, high, maximum in (
        ("polymarket", 550, 700),
        ("sig", 420, 580),
    ):
        unit = (ROOT / "deploy" / "systemd" / f"predictions-cup-{name}-capture.service").read_text()
        assert f"MemoryHigh={high}M" in unit
        assert f"MemoryMax={maximum}M" in unit
        assert "MemorySwapMax=0" in unit
        assert "CPUQuota=70%" in unit
        assert "StartLimitIntervalSec=900" in unit
        assert "StartLimitBurst=3" in unit
        assert "RestartSec=60s" in unit
        assert "OOMPolicy=stop" in unit
        assert "Restart=on-failure" in unit
        assert "NoNewPrivileges=true" in unit


def test_capture_budget_does_not_touch_maker():
    for name in ("polymarket", "sig"):
        unit = (ROOT / "deploy" / "systemd" / f"predictions-cup-{name}-capture.service").read_text()
        assert "predictions_cup.maker" not in unit
