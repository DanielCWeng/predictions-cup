"""Smoke check that the election strategy scorer works from an offline JSON file."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_offline_cli_ranks_synthetic_joint_slate_without_trading() -> None:
    path = Path("tests/fixtures/election_terminal_example.json")
    completed = subprocess.run(
        [sys.executable, "scripts/ops/election_terminal_score.py", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["mode"] == "OFFLINE_RESEARCH_ONLY"
    assert result["execution_authority"] is False
    assert result["calibration_claim"] == "NONE_INPUT_PROBABILITIES_UNVERIFIED"
    assert [s["name"] for s in result["ranked_plans"]] == ["joint-swing", "single-a"]
    assert result["ranked_plans"][0]["min_probability_improvement"] == .3
