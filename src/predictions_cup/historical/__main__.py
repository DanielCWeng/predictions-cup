"""DATA-001 command line: acquire, inventory, build, validate and smoke-test the corpus.

Every path is an argument, so the same commands run locally or in a Kaggle kernel
(``/kaggle/input/...`` in, ``/kaggle/working/...`` out).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from predictions_cup.historical.sources import write_json


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m predictions_cup.historical")
    sub = parser.add_subparsers(dest="command", required=True)

    acquire = sub.add_parser("acquire", help="archive -> exact-token hourly extracts")
    acquire.add_argument("--fills", type=Path, required=True)
    acquire.add_argument("--output", type=Path, required=True)
    acquire.add_argument("--scratch", type=Path, required=True)
    acquire.add_argument("--worker", type=int, default=0)
    acquire.add_argument("--workers", type=int, default=1)

    sources = sub.add_parser("sources", help="write the source inventory manifest")
    sources.add_argument("--orderbooks", type=Path, required=True)
    sources.add_argument("--fills", type=Path, required=True)
    sources.add_argument("--output", type=Path, required=True)
    sources.add_argument("--remote-footers", action="store_true",
                         help="read raw archive Parquet footers over HTTP range requests")

    build = sub.add_parser("build", help="normalize extracts + fills into the replay corpus")
    build.add_argument("--orderbooks", type=Path, required=True)
    build.add_argument("--fills", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--material-gap-seconds", type=float, default=300.0)
    build.add_argument("--pipeline-commit", default=None)

    validate = sub.add_parser("validate", help="re-verify a produced corpus against its manifest")
    validate.add_argument("--corpus", type=Path, required=True)

    smoke = sub.add_parser("smoke", help="EXPERIMENT-002 mechanical smoke on a corpus slice")
    smoke.add_argument("--corpus", type=Path, required=True)
    smoke.add_argument("--regime", required=True)
    smoke.add_argument("--target-token", required=True)
    smoke.add_argument("--reference-token", required=True)
    smoke.add_argument("--start-at", type=_aware, required=True)
    smoke.add_argument("--end-at", type=_aware, required=True)
    smoke.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "acquire":
        from predictions_cup.historical.acquire import acquire as run_acquire

        run_acquire(
            fills_root=args.fills,
            output_root=args.output,
            scratch=args.scratch,
            worker=args.worker,
            workers=args.workers,
        )
        return 0
    if args.command == "sources":
        from predictions_cup.historical.sources import build_source_manifest

        manifest = build_source_manifest(
            orderbooks_root=args.orderbooks,
            fills_root=args.fills,
            remote_footers=args.remote_footers,
        )
        write_json(args.output, manifest)
        return 0
    if args.command == "build":
        from datetime import timedelta

        import pyarrow as pa

        from predictions_cup.historical.corpus import build_corpus

        # Return freed buffers to the OS between hours (bounded peak on small machines).
        pa.set_memory_pool(pa.system_memory_pool())

        manifest = build_corpus(
            orderbooks_root=args.orderbooks,
            fills_root=args.fills,
            output_root=args.output,
            material_gap=timedelta(seconds=args.material_gap_seconds),
            pipeline_commit=args.pipeline_commit or _git_commit(),
        )
        print(json.dumps(manifest["totals"], indent=2, sort_keys=True))
        return 0
    if args.command == "validate":
        from predictions_cup.historical.corpus import validate_corpus

        report = validate_corpus(args.corpus)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    if args.command == "smoke":
        from predictions_cup.historical.regimes import SCHEMA_VERSION
        from predictions_cup.historical.smoke import run_smoke

        result = run_smoke(
            args.corpus / f"schema_version={SCHEMA_VERSION}" / args.regime / "books",
            dataset_id=f"DATA-001:{args.regime}",
            target_token=args.target_token,
            reference_token=args.reference_token,
            start_at=args.start_at,
            end_at=args.end_at,
        )
        text = json.dumps(result, indent=2, sort_keys=True)
        if args.output is not None:
            write_json(args.output, result)
        print(text)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
