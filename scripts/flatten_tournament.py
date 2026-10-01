"""Fast tournament-scoped cancel-all with authoritative verification."""

from __future__ import annotations

import argparse
import asyncio
import json

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.execution.flatten import flatten_tournament as execute_flatten


async def flatten_tournament(
    settings: AppSettings,
    *,
    max_attempts: int = 3,
) -> int:
    """Thin CLI wrapper over the canonical execution flatten primitive."""
    result = await execute_flatten(
        settings,
        max_attempts=max_attempts,
    )
    print(json.dumps(result.as_dict(), sort_keys=True))
    if result.verified_flat:
        return 0
    return 2 if result.status == "FAILED" else 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cancel all tournament orders and prove the account is flat"
    )
    parser.add_argument("--runtime-env-only", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = load_settings(use_dotenv=not args.runtime_env_only)
    return asyncio.run(flatten_tournament(settings, max_attempts=args.max_attempts))


if __name__ == "__main__":
    raise SystemExit(main())
