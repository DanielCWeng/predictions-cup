"""Small non-secret operator/status surface for MODEL-RUNTIME-001."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from predictions_cup.models.registry import default_model_registry, parse_model_ids

_TRUE = frozenset({"1", "true", "yes", "on"})
_REQUIRED_LIVE_KEYS = (
    "PREDICTIONS_CUP_TOURNAMENT_ID",
    "PREDICTIONS_CUP_TOURNAMENT_SLUG",
    "PREDICTIONS_CUP_RISK_MAX_ORDER_SIZE",
    "PREDICTIONS_CUP_RISK_MAX_GROSS_EXPOSURE",
    "PREDICTIONS_CUP_RISK_MAX_PER_MARKET_EXPOSURE",
    "PREDICTIONS_CUP_RISK_MAX_OPEN_ORDER_EXPOSURE",
    "PREDICTIONS_CUP_RISK_MAX_CONCURRENT_OPEN_ORDERS",
    "PREDICTIONS_CUP_RISK_MAX_TOURNAMENT_EXPOSURE",
    "PREDICTIONS_CUP_RISK_SESSION_LOSS_LIMIT",
    "PREDICTIONS_CUP_RISK_DRAWDOWN_LIMIT",
)


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _flag(values: dict[str, str], key: str) -> bool:
    return values.get(key, "").strip().lower() in _TRUE


def _platform_config(
    runtime: dict[str, str],
    trade: dict[str, str],
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if runtime.get("PREDICTIONS_CUP_EXECUTION_MODE", "SHADOW").upper() != "LIVE":
        reasons.append("execution_mode_not_live")
    if not _flag(runtime, "PREDICTIONS_CUP_TRADING_ENABLED"):
        reasons.append("trading_not_enabled")
    if _flag(runtime, "PREDICTIONS_CUP_GLOBAL_KILL_SWITCH"):
        reasons.append("global_kill_switch_active")
    if not _flag(runtime, "PREDICTIONS_CUP_RISK_CAPITAL_CONTROL_ENABLED"):
        reasons.append("capital_control_not_enabled")
    for key in _REQUIRED_LIVE_KEYS:
        if not runtime.get(key, "").strip():
            reasons.append(f"missing:{key}")
    if not trade.get("PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL", "").strip():
        reasons.append("trade_credential_not_configured")
    return (not reasons, tuple(reasons))


def configuration_status(
    *,
    runtime_env: Path,
    trade_env: Path,
) -> dict[str, Any]:
    registry = default_model_registry()
    runtime = _read_env(runtime_env)
    trade = _read_env(trade_env)
    errors: list[str] = []

    try:
        paper_ids = parse_model_ids(
            runtime.get("PREDICTIONS_CUP_PAPER_MODEL_IDS", "")
        )
    except ValueError as exc:
        paper_ids = ()
        errors.append(f"paper_allowlist:{exc}")
    try:
        live_ids = parse_model_ids(
            runtime.get("PREDICTIONS_CUP_LIVE_MODEL_IDS", "")
        )
    except ValueError as exc:
        live_ids = ()
        errors.append(f"live_allowlist:{exc}")

    known = set(registry.ids())
    unknown = (set(paper_ids) | set(live_ids)).difference(known)
    if unknown:
        errors.append("unknown_model_ids:" + ",".join(sorted(unknown)))

    config_ready, readiness_reasons = _platform_config(runtime, trade)
    rows: list[dict[str, object]] = []
    for provider in registry.providers():
        spec = provider.spec
        paper = spec.model_id in paper_ids
        env_live = spec.model_id in live_ids
        blocked = env_live and not spec.live_eligible
        if blocked:
            errors.append(f"live_without_code_gate:{spec.model_id}")

        # This command can prove static host configuration, but it cannot prove
        # current account/marks/reconciliation health.  Keep PLATFORM_READY
        # false rather than presenting a dangerous green status from a file read.
        platform_live_ready = False
        if blocked:
            effective = "BLOCKED_CONFIGURATION"
        elif paper:
            effective = "PAPER"
        else:
            effective = "NOT_LIVE"
        rows.append(
            {
                "model": spec.model_id,
                "version": spec.model_version,
                "capability": spec.capability.value,
                "paper_enabled": paper,
                "code_live_gate": spec.live_eligible,
                "env_live_gate": env_live,
                "platform_live_ready": platform_live_ready,
                "platform_config_ready": config_ready,
                "effective_mode": effective,
                "health": "NOT_RUNNING_EVIDENCE",
                "last_decision": None,
                "source_hash": spec.source_hash,
            }
        )

    return {
        "schema_version": 1,
        "model_code_gate_source": (
            "src/predictions_cup/models/model_<name>.py:ModelSpec.live_eligible"
        ),
        "model_env_gate_source": (
            f"{runtime_env}:PREDICTIONS_CUP_LIVE_MODEL_IDS"
        ),
        "paper_allowlist_source": (
            f"{runtime_env}:PREDICTIONS_CUP_PAPER_MODEL_IDS"
        ),
        "trade_credential_source": (
            f"{trade_env}:PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL"
        ),
        "trade_credential_configured": bool(
            trade.get("PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL", "").strip()
        ),
        "platform_config_ready": config_ready,
        "platform_live_ready": False,
        "platform_live_ready_reason": (
            "runtime account/marks/reconciliation readiness is not provable "
            "from offline env files"
        ),
        "platform_config_blockers": readiness_reasons,
        "configuration_errors": tuple(dict.fromkeys(errors)),
        "models": rows,
    }


def _print_table(payload: dict[str, Any]) -> None:
    errors = payload["configuration_errors"]
    if errors:
        print("CONFIGURATION: BLOCKED")
        for error in errors:
            print(f"  - {error}")
    else:
        print("CONFIGURATION: VALID")
    print(
        "MODEL VERSION CAPABILITY PAPER CODE_LIVE ENV_LIVE "
        "PLATFORM_READY EFFECTIVE HEALTH"
    )
    for row in payload["models"]:
        assert isinstance(row, dict)
        print(
            row["model"],
            row["version"],
            row["capability"],
            str(row["paper_enabled"]).upper(),
            str(row["code_live_gate"]).upper(),
            str(row["env_live_gate"]).upper(),
            str(row["platform_live_ready"]).upper(),
            row["effective_mode"],
            row["health"],
        )
    print(f"CODE_GATE_SOURCE={payload['model_code_gate_source']}")
    print(f"ENV_GATE_SOURCE={payload['model_env_gate_source']}")
    print(f"TRADE_CREDENTIAL_SOURCE={payload['trade_credential_source']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cupctl models")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--env-file", type=Path, required=True)
    status.add_argument(
        "--trade-env",
        type=Path,
        default=Path.home() / ".config/predictions-cup/trade.env",
    )
    status.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = configuration_status(
        runtime_env=args.env_file,
        trade_env=args.trade_env,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_table(payload)
    return 2 if payload["configuration_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
