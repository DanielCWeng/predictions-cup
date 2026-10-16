"""LANE-5 adversarial integration gates: known regressions kept visible.

Strict XFAIL means missing defenses do not masquerade as a PASS.
No execution, deployment, configuration or LIVE orders.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from predictions_cup.execution.recovery import recover_startup


@pytest.mark.xfail(
    strict=True,
    reason=(
        "STOP-SHIP #79 regression: merged #115 required an explicit approving "
        "placement_replay_allowed predicate; current main lost it"
    ),
)
def test_recovery_replay_must_have_default_deny_authorization() -> None:
    signature = inspect.signature(recover_startup)
    assert "placement_replay_allowed" in signature.parameters
    assert signature.parameters["placement_replay_allowed"].default is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "STOP-SHIP #84: fresh-admission LIVE sink omits observation_emitter "
        "despite recovery-only sink wiring"
    ),
)
def test_every_make_live_sink_must_record_execution_telemetry() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src/predictions_cup/maker/service.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    sinks = [
        item for item in ast.walk(tree)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "SigLiveSink"
    ]
    assert len(sinks) == 2, "Review any newly introduced LIVE sender separately"
    for sink in sinks:
        names = {kw.arg for kw in sink.keywords}
        assert {"observation_emitter", "observation_process_instance_id"} <= names
