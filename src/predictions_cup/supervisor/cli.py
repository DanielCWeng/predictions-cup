"""CLI for SUPERVISOR-001."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from predictions_cup.config import load_settings
from predictions_cup.supervisor.config import SupervisorRuntimeConfig
from predictions_cup.supervisor.contracts import HostRole, RemediationLevel
from predictions_cup.supervisor.runtime import SupervisorRuntime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SUPERVISOR-001 launch sentry")
    parser.add_argument(
        "--runtime-env-only",
        action="store_true",
        help="Disable repository .env loading for supervised runtime.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--run-seconds", type=float)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--host-role", choices=[item.value for item in HostRole])
    parser.add_argument(
        "--max-remediation-level",
        type=int,
        choices=[int(item) for item in RemediationLevel],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings(use_dotenv=not args.runtime_env_only)
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    role = None if args.host_role is None else HostRole(args.host_role)
    level = (
        None
        if args.max_remediation_level is None
        else RemediationLevel(args.max_remediation_level)
    )
    config = SupervisorRuntimeConfig.from_env(
        settings,
        repo_root=args.repo_root.resolve(),
        role_override=role,
        output_override=None if args.output_root is None else args.output_root.resolve(),
        remediation_level_override=level,
    )
    runtime = SupervisorRuntime(settings, config)
    if args.once:
        snapshot = runtime.run_once()
        print(snapshot.to_dict())
        return 0
    runtime.run_forever(run_seconds=args.run_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
