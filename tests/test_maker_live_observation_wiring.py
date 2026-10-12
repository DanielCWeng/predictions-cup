"""STOP-SHIP #84: fresh and recovery LIVE sinks must share wire telemetry.

The adapter's quote-level observation emitter is not an alternative to the
LIVE sink's per-attempt REQUEST_DISPATCHED/ACK/UNCERTAIN emitter. This static
composition test prevents a silent omission when maker service is refactored.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_every_live_sink_construction_binds_execution_observation_emitter() -> None:
    service_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predictions_cup"
        / "maker"
        / "service.py"
    )
    tree = ast.parse(service_path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SigLiveSink"
    ]
    assert len(calls) == 2, (
        "Explicitly review every fresh/recovery LIVE sink constructor; "
        "do not accidentally add an uninstrumented order sender"
    )

    observed_permits: set[str] = set()
    for call in calls:
        assert not call.args, "LIVE sink must use named audited dependencies"
        assert all(kw.arg is not None for kw in call.keywords)
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        assert kwargs.keys() == {
            "client",
            "journal",
            "permit",
            "reservations",
            "observation_emitter",
            "observation_process_instance_id",
        }
        assert isinstance(kwargs["observation_emitter"], ast.Name)
        assert kwargs["observation_emitter"].id == "observation_emitter"
        assert ast.unparse(kwargs["observation_process_instance_id"]) == (
            "observe_recorder.session_id"
        )
        assert isinstance(kwargs["permit"], ast.Name)
        observed_permits.add(kwargs["permit"].id)
    assert observed_permits == {"recovery_permit", "permit"}
