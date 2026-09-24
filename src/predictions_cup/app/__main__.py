"""Command-line entry point for ``python -m predictions_cup.app``."""

from __future__ import annotations

import argparse

from predictions_cup.app import run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Predictions Cup foundation shell.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run the startup path and exit immediately without external I/O.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(smoke_test=args.smoke_test)


if __name__ == "__main__":
    raise SystemExit(main())
