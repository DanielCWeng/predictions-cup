from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.runtime.fullstack import (
    CapabilityMode,
    EvidenceState,
    GateState,
    collect_status,
    config_hash,
    configured_services,
    create_survival_checkpoint,
    evaluate_health,
    failure_injection_matrix,
    kill_switch_probe,
    launch_snapshot,
    run_rehearsal,
    safe_restart,
    verify_survival_checkpoint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"


def _fake_systemctl(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "systemctl"
    calls = tmp_path / "systemctl.calls"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
        "if [[ \"$1\" == \"show\" ]]; then\n"
        "  printf 'ActiveState=active\\nSubState=running\\nMainPID=4242\\n'\n"
        "  printf 'NRestarts=0\\nActiveEnterTimestampMonotonic=1\\n'\n"
        "fi\n"
        "if [[ \"$1\" == \"is-enabled\" ]]; then printf 'enabled\\n'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, calls


def _sig_db(path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    health = {
        "research_storage": {
            "queue_depth": 1,
            "queue_capacity": 100,
            "dropped_rows": 0,
            "storage_failures": 0,
        }
    }
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE realtime_deliveries (observed_at TEXT)")
        connection.execute(
            "CREATE TABLE capture_health ("
            "id INTEGER PRIMARY KEY, observed_at TEXT, payload_json TEXT)"
        )
        connection.execute("INSERT INTO realtime_deliveries VALUES (?)", (now,))
        connection.execute(
            "INSERT INTO capture_health VALUES (1, ?, ?)",
            (now, json.dumps(health)),
        )


def _pm_db(path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    payload = {"last_message_at": now, "websocket_connected": True}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ingestion_health ("
            "id INTEGER PRIMARY KEY, recorded_at TEXT, payload_json TEXT)"
        )
        connection.execute(
            "INSERT INTO ingestion_health VALUES (1, ?, ?)",
            (now, json.dumps(payload)),
        )


def _journal(path: Path, state: str | None = None) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE execution_envelopes (lifecycle_state TEXT NOT NULL)"
        )
        if state is not None:
            connection.execute("INSERT INTO execution_envelopes VALUES (?)", (state,))



def _risk_state_db(path: Path) -> None:
    payload: dict[str, object] = {
        "account_trusted": True,
        "marks_trusted": True,
        "reconciliation_complete": True,
        "account_observed_monotonic_ns": 123456,
        "global_halt": None,
        "strategy_halts": [],
        "current_equity": "100",
        "peak_session_equity": "100",
        "drawdown": "0",
        "realised_pnl": "0",
        "unrealised_pnl": "0",
        "exposure": {
            "trusted": True,
            "gross_exposure": 0.0,
            "net_directional_exposure": 0.0,
            "open_order_exposure": 0.0,
            "uncertain_order_exposure": 0.0,
        },
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE risk_state (singleton INTEGER PRIMARY KEY, payload_json TEXT)"
        )
        connection.execute(
            "INSERT INTO risk_state VALUES (1, ?)",
            (json.dumps(payload),),
        )


def _healthy_values(tmp_path: Path) -> dict[str, str]:
    sig = tmp_path / "sig.sqlite3"
    pm = tmp_path / "pm.sqlite3"
    journal = tmp_path / "execution.sqlite3"
    _sig_db(sig)
    _pm_db(pm)
    _journal(journal)
    return {
        "PREDICTIONS_CUP_ENVIRONMENT": "test",
        "PREDICTIONS_CUP_GLOBAL_KILL_SWITCH": "false",
        "PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED": "true",
        "PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH": str(sig),
        "PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH": str(pm),
        "PREDICTIONS_CUP_EXECUTION_JOURNAL_PATH": str(journal),
        "PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB": "0",
        "PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB": "0",
        "PREDICTIONS_CUP_FULLSTACK_MAX_FEED_AGE_SECONDS": "60",
    }


def test_config_hash_excludes_secret_values() -> None:
    first = {
        "PREDICTIONS_CUP_ENVIRONMENT": "prod",
        "PREDICTIONS_CUP_SIG_READ_CREDENTIAL": "secret-a",
    }
    second = dict(first)
    second["PREDICTIONS_CUP_SIG_READ_CREDENTIAL"] = "secret-b"
    assert config_hash(first) == config_hash(second)


def test_snapshot_contains_no_secret_value(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"schema_version":"mapping-v1"}', encoding="utf-8")
    values = {
        "PREDICTIONS_CUP_MAKER_MAPPING_PATH": str(mapping),
        "PREDICTIONS_CUP_SIG_READ_CREDENTIAL": "DO_NOT_LEAK",
        "PREDICTIONS_CUP_RISK_MAX_ORDER_SIZE": "5",
    }
    snapshot = launch_snapshot(PROJECT_ROOT, values)
    rendered = json.dumps(snapshot, sort_keys=True)
    assert "DO_NOT_LEAK" not in rendered
    assert snapshot["mapping"]["version"] == "mapping-v1"
    assert snapshot["risk_profile"]["PREDICTIONS_CUP_RISK_MAX_ORDER_SIZE"] == "5"


def test_healthy_persistent_surfaces_pass(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    status = collect_status(PROJECT_ROOT, values)
    health = evaluate_health(status, values, require_real=False)
    assert health["state"] == GateState.PASS.value


def test_unresolved_execution_blocks(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    journal = Path(values["PREDICTIONS_CUP_EXECUTION_JOURNAL_PATH"])
    journal.unlink()
    _journal(journal, "UNCERTAIN")
    status = collect_status(PROJECT_ROOT, values)
    health = evaluate_health(status, values, require_real=False)
    assert health["state"] == GateState.BLOCKED.value
    assert any(
        item["name"] == "execution_recovery" and item["state"] == "BLOCKED"
        for item in health["checks"]
    )


def test_fixture_provider_cannot_pass_require_real(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values["PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED"] = "true"
    values["PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE"] = "EXTERNAL_SERVICE"
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    values["PREDICTIONS_CUP_FULLSTACK_STATUS_DIR"] = str(status_dir)
    (status_dir / "live-learn.json").write_text(
        json.dumps(
            {
                "state": "DEGRADED",
                "provider_mode": "fixture",
                "capability_mode": "EXTERNAL_SERVICE",
                "owner_services": ["predictions-cup-live-learn.service"],
                "observed_at": datetime.now(UTC).isoformat(),
                "reason": "fixture",
            }
        ),
        encoding="utf-8",
    )
    status = collect_status(PROJECT_ROOT, values)
    health = evaluate_health(status, values, require_real=True)
    assert health["state"] == GateState.BLOCKED.value


def test_safe_restart_is_ordered_and_graceful(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": (
                CapabilityMode.EXTERNAL_SERVICE.value
            ),
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": (
                CapabilityMode.EXTERNAL_SERVICE.value
            ),
        }
    )
    result = safe_restart(
        values,
        timeout_seconds=1.0,
        require_observation_advance=False,
    )
    assert result["state"] == GateState.PASS.value
    actions = [
        line
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.startswith("stop ") or line.startswith("start ")
    ]
    assert actions[:5] == [
        "stop predictions-cup-live-learn.service",
        "stop predictions-cup-observe.service",
        "stop predictions-cup-maker.service",
        "stop predictions-cup-sig-capture.service",
        "stop predictions-cup-polymarket-capture.service",
    ]
    assert actions[5:10] == [
        "start predictions-cup-sig-capture.service",
        "start predictions-cup-polymarket-capture.service",
        "start predictions-cup-maker.service",
        "start predictions-cup-live-learn.service",
        "start predictions-cup-observe.service",
    ]


def test_ssh_survival_checkpoint_preserves_service_identity(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values["PREDICTIONS_CUP_FULLSTACK_STATUS_DIR"] = str(tmp_path / "status")
    create_survival_checkpoint(PROJECT_ROOT, values, "ssh")
    result = verify_survival_checkpoint(PROJECT_ROOT, values, "ssh")
    assert result["state"] == GateState.PASS.value


def test_fullstack_systemd_templates_fail_closed() -> None:
    maker = (SYSTEMD_DIR / "predictions-cup-maker.service").read_text(encoding="utf-8")
    learner = (SYSTEMD_DIR / "predictions-cup-live-learn.service").read_text(
        encoding="utf-8"
    )
    observe = (SYSTEMD_DIR / "predictions-cup-observe.service").read_text(
        encoding="utf-8"
    )
    target = (SYSTEMD_DIR / "predictions-cup-runtime.target").read_text(encoding="utf-8")
    for unit in (maker, learner, observe):
        assert "EnvironmentFile=@@RUNTIME_ENV@@" in unit
        assert "UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL" in unit
        assert "SendSIGKILL=no" in unit
        assert "Restart=on-failure" in unit
        assert "--runtime-env-only" in unit
    assert "--live" not in maker
    assert "@@ACTIVE_UNITS@@" in target


def test_failure_injection_matrix_covers_launch_faults() -> None:
    result = failure_injection_matrix({})
    assert result["state"] == GateState.PASS.value
    assert result["evidence_state"] == EvidenceState.SIMULATION_PASS.value
    assert result["simulation_only"] is True
    cases = {item["name"]: item for item in result["cases"]}
    assert set(cases) == {
        "sig_realtime_dies",
        "pm_feed_dies",
        "account_trust_lost",
        "observer_process_dies",
        "disk_low",
        "capture_queue_high",
        "unresolved_execution",
        "stale_external_fv",
        "risk_global_halt",
        "candidate_throws",
        "live_learn_backlog",
    }
    assert all(item["passed"] for item in cases.values())


def test_kill_switch_probe_is_non_economic_and_one_way() -> None:
    result = kill_switch_probe()
    assert result["state"] == GateState.PASS.value
    assert result["evidence_state"] == EvidenceState.SIMULATION_PASS.value
    assert result["simulation_only"] is True
    assert result["active_after"] is True
    assert result["reason_latched"] is True
    assert result["economic_order_sent"] is False



def test_require_real_does_not_credit_simulation(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(tmp_path / "status"),
            "PREDICTIONS_CUP_FULLSTACK_SNAPSHOT_PATH": str(tmp_path / "snapshot.json"),
            "PREDICTIONS_CUP_FULLSTACK_EVIDENCE_ROOT": str(tmp_path / "evidence"),
        }
    )

    result = run_rehearsal(
        PROJECT_ROOT,
        values,
        require_real=True,
        restart=False,
    )

    assert result["simulation_validation"]["failure_injection"]["evidence_state"] == (
        EvidenceState.SIMULATION_PASS.value
    )
    assert result["simulation_validation"]["local_kill_switch_probe"]["evidence_state"] == (
        EvidenceState.SIMULATION_PASS.value
    )
    assert result["real_acceptance"]["evidence_state"] == EvidenceState.BLOCKED.value
    assert "risk_global_halt" in result["real_acceptance"]["missing_or_failed"]
    assert "maker_kill_cancel" in result["real_acceptance"]["missing_or_failed"]
    assert "execution_recovery" in result["real_acceptance"]["missing_or_failed"]
    assert "sig_reconnect" in result["real_acceptance"]["missing_or_failed"]
    assert "safe_restart" in result["real_acceptance"]["missing_or_failed"]
    assert "ssh_survival" in result["real_acceptance"]["missing_or_failed"]
    assert result["state"] == GateState.BLOCKED.value


def test_reboot_is_explicit_waiver_until_authorized(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(tmp_path / "status"),
            "PREDICTIONS_CUP_FULLSTACK_SNAPSHOT_PATH": str(tmp_path / "snapshot.json"),
            "PREDICTIONS_CUP_FULLSTACK_EVIDENCE_ROOT": str(tmp_path / "evidence"),
        }
    )

    result = run_rehearsal(
        PROJECT_ROOT,
        values,
        require_real=True,
        restart=False,
    )

    reboot = result["real_acceptance"]["exercises"]["reboot_survival"]
    assert reboot["evidence_state"] == (
        EvidenceState.NOT_RUN_REQUIRES_AUTHORIZATION.value
    )
    assert "reboot_survival" not in result["real_acceptance"]["required_exercises"]



def test_in_process_capabilities_do_not_spawn_adapter_units(tmp_path: Path) -> None:
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": "IN_PROCESS",
        }
    )

    services = configured_services(values)

    assert services == (
        "predictions-cup-sig-capture.service",
        "predictions-cup-polymarket-capture.service",
        "predictions-cup-maker.service",
    )
    assert "predictions-cup-live-learn.service" not in services
    assert "predictions-cup-observe.service" not in services


def test_in_process_safe_restart_restarts_owners_only_once(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": "IN_PROCESS",
        }
    )

    result = safe_restart(
        values,
        timeout_seconds=1.0,
        require_observation_advance=False,
    )

    assert result["state"] == GateState.PASS.value
    actions = [
        line
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.startswith("stop ") or line.startswith("start ")
    ]
    assert actions == [
        "stop predictions-cup-maker.service",
        "stop predictions-cup-sig-capture.service",
        "stop predictions-cup-polymarket-capture.service",
        "start predictions-cup-sig-capture.service",
        "start predictions-cup-polymarket-capture.service",
        "start predictions-cup-maker.service",
    ]


def test_external_service_capabilities_remain_supported(tmp_path: Path) -> None:
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "EXTERNAL_SERVICE",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": "EXTERNAL_SERVICE",
        }
    )

    services = configured_services(values)

    assert "predictions-cup-live-learn.service" in services
    assert "predictions-cup-observe.service" in services


def test_require_real_accepts_real_in_process_capability_health(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(status_dir),
        }
    )
    provider_records = {
        "live-learn": {
            "state": "PASS",
            "provider_mode": "real",
            "capability_mode": "IN_PROCESS",
            "owner_services": ["predictions-cup-maker.service"],
            "observed_at": datetime.now(UTC).isoformat(),
            "reason": "LiveLearnEngine health healthy",
        },
        "observe": {
            "schema_version": "observe-001-health-v1",
            "observed_at": datetime.now(UTC).isoformat(),
            "process_instance_id": "observe-test-process",
            "owner": "predictions-cup-maker.service",
            "health": {"state": "HEALTHY"},
        },
        "shadow": {
            "state": "PASS",
            "provider_mode": "real",
            "observed_at": datetime.now(UTC).isoformat(),
            "reason": "shadow healthy",
        },
    }
    for name, payload in provider_records.items():
        (status_dir / f"{name}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    status = collect_status(PROJECT_ROOT, values)
    health = evaluate_health(status, values, require_real=True)
    capability_checks = {
        item["name"]: item for item in health["checks"] if item["name"] in {"live_learn", "observe"}
    }

    assert capability_checks["live_learn"]["state"] == GateState.PASS.value
    assert capability_checks["observe"]["state"] == GateState.PASS.value
    assert "predictions-cup-live-learn.service" not in configured_services(values)
    assert "predictions-cup-observe.service" not in configured_services(values)


def test_require_real_rejects_wrong_in_process_owner(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    values = _healthy_values(tmp_path)
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(status_dir),
        }
    )
    (status_dir / "live-learn.json").write_text(
        json.dumps(
            {
                "state": "PASS",
                "provider_mode": "real",
                "capability_mode": "IN_PROCESS",
                "owner_services": ["predictions-cup-live-learn.service"],
                "observed_at": datetime.now(UTC).isoformat(),
                "reason": "wrong owner",
            }
        ),
        encoding="utf-8",
    )

    health = evaluate_health(
        collect_status(PROJECT_ROOT, values),
        values,
        require_real=True,
    )

    assert any(
        item["name"] == "live_learn"
        and item["state"] == GateState.BLOCKED.value
        and "owner mismatch" in item["reason"]
        for item in health["checks"]
    )



def test_merged_provider_stack_health_passes_require_real(tmp_path: Path) -> None:
    script, calls = _fake_systemctl(tmp_path)
    os.environ["PREDICTIONS_CUP_SYSTEMCTL"] = str(script)
    os.environ["CALL_LOG"] = str(calls)
    values = _healthy_values(tmp_path)
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    risk_path = tmp_path / "risk.sqlite3"
    _risk_state_db(risk_path)
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "payload": {
                    "candidate_id": "pred-006",
                    "candidate_version": "frozen-test",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    now = datetime.now(UTC).isoformat()
    values.update(
        {
            "PREDICTIONS_CUP_MAKER_ENABLED": "true",
            "PREDICTIONS_CUP_SHADOW_ENABLED": "true",
            "PREDICTIONS_CUP_LIVE_LEARN_ENABLED": "true",
            "PREDICTIONS_CUP_RISK_CAPITAL_CONTROL_ENABLED": "true",
            "PREDICTIONS_CUP_RISK_STATE_PATH": str(risk_path),
            "PREDICTIONS_CUP_SHADOW_JOURNAL_PATH": str(shadow_path),
            "PREDICTIONS_CUP_FULLSTACK_STATUS_DIR": str(status_dir),
            "PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE": "IN_PROCESS",
            "PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE": "IN_PROCESS",
        }
    )
    (status_dir / "observe.json").write_text(
        json.dumps(
            {
                "schema_version": "observe-001-health-v1",
                "observed_at": now,
                "process_instance_id": "maker-observe-test",
                "owner": "predictions-cup-maker.service",
                "health": {"state": "HEALTHY"},
            }
        ),
        encoding="utf-8",
    )
    for name, reason in (
        ("shadow", "shadow_healthy"),
        ("live-learn", "live_learn_healthy"),
    ):
        (status_dir / f"{name}.json").write_text(
            json.dumps(
                {
                    "schema_version": "runtime-capability-status-v1",
                    "observed_at": now,
                    "state": "PASS",
                    "provider_mode": "real",
                    "capability_mode": "IN_PROCESS",
                    "owner_services": ["predictions-cup-maker.service"],
                    "reason": reason,
                }
            ),
            encoding="utf-8",
        )

    status = collect_status(PROJECT_ROOT, values)
    status["clock"] = {
        "state": "HEALTHY",
        "reason_codes": [],
        "synchronized": True,
        "estimated_offset_seconds": 0.0,
    }
    status["storage"] = {
        "state": "HEALTHY",
        "reason_codes": [],
        "runway_hours": 100.0,
    }
    status["session"] = {
        "state": "HEALTHY",
        "session_id": "merged-provider-test",
        "manifest_sha256": "fixture",
    }
    health = evaluate_health(status, values, require_real=True)

    assert status["risk_halt"]["source"] == "risk-002"
    assert status["account"]["trusted"] is True
    assert status["observe"]["state"] == GateState.PASS.value
    assert status["live_learn"]["state"] == GateState.PASS.value
    assert status["shadow"]["state"] == GateState.PASS.value
    assert health["state"] == GateState.PASS.value
