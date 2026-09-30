"""Operator controls for durable RISK-002 halt state.

Mutations intentionally require acknowledgement that the trading service is
stopped. The running process owns an in-memory copy of capital state, so editing
only the SQLite file underneath a live process would not be an authoritative
reset.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import monotonic_ns

from predictions_cup.config import load_settings
from predictions_cup.risk import (
    HaltScope,
    SqliteRiskStateStore,
    clear_strategy_halt,
    reset_global_halt,
    trip_global_halt,
    trip_strategy_halt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect or mutate RISK-002 durable halts")
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--runtime-env-only", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    halt_global = sub.add_parser("halt-global")
    halt_global.add_argument("--reason", required=True)
    halt_global.add_argument("--ack-service-stopped", action="store_true", required=True)

    reset_global = sub.add_parser("reset-global")
    reset_global.add_argument("--expected-reason", required=True)
    reset_global.add_argument("--operator", required=True)
    reset_global.add_argument("--ack-service-stopped", action="store_true", required=True)

    halt_strategy = sub.add_parser("halt-strategy")
    halt_strategy.add_argument("--scope", choices=("id", "family"), required=True)
    halt_strategy.add_argument("--value", required=True)
    halt_strategy.add_argument("--reason", required=True)
    halt_strategy.add_argument("--ack-service-stopped", action="store_true", required=True)

    reset_strategy = sub.add_parser("reset-strategy")
    reset_strategy.add_argument("--scope", choices=("id", "family"), required=True)
    reset_strategy.add_argument("--value", required=True)
    reset_strategy.add_argument("--operator", required=True)
    reset_strategy.add_argument("--ack-service-stopped", action="store_true", required=True)
    return parser


def _scope(value: str) -> HaltScope:
    return HaltScope.STRATEGY_ID if value == "id" else HaltScope.STRATEGY_FAMILY


def _status_payload(state: object) -> str:
    return json.dumps(asdict(state), default=str, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings(use_dotenv=not args.runtime_env_only)
    path = settings.risk_state_path if args.state_path is None else args.state_path
    with SqliteRiskStateStore(path) as store:
        state = store.load()
        if state is None:
            raise RuntimeError(f"no RISK-002 state exists at {path}")

        if args.command == "status":
            print(_status_payload(state))
            return 0

        now_ns = monotonic_ns()
        if args.command == "halt-global":
            updated = trip_global_halt(
                state,
                reason=args.reason,
                now_monotonic_ns=now_ns,
            )
            event_type = "OPERATOR_GLOBAL_HALT"
            detail = args.reason
        elif args.command == "reset-global":
            updated = reset_global_halt(
                state,
                expected_reason=args.expected_reason,
                operator=args.operator,
                now_monotonic_ns=now_ns,
            )
            event_type = "OPERATOR_GLOBAL_RESET"
            detail = args.operator
        elif args.command == "halt-strategy":
            updated = trip_strategy_halt(
                state,
                scope=_scope(args.scope),
                scope_value=args.value,
                reason=args.reason,
                now_monotonic_ns=now_ns,
            )
            event_type = "OPERATOR_STRATEGY_HALT"
            detail = f"{args.scope}:{args.value}:{args.reason}"
        elif args.command == "reset-strategy":
            updated = clear_strategy_halt(
                state,
                scope=_scope(args.scope),
                scope_value=args.value,
                operator=args.operator,
                now_monotonic_ns=now_ns,
            )
            event_type = "OPERATOR_STRATEGY_RESET"
            detail = f"{args.scope}:{args.value}:{args.operator}"
        else:
            raise AssertionError("unreachable command")

        store.save(updated, event_type=event_type, detail=detail)
        print(_status_payload(updated))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
