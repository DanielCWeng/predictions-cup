"""Cut the tiny committed DATA-001 test fixture from a built corpus (real rows, no rewriting).

Usage:
    python scripts/data_001_make_fixture.py --corpus <corpus root> --regime <regime_id> \
        --token <token_id> --token <token_id> --start-at <ISO> --end-at <ISO> \
        --output tests/fixtures/data_001_corpus
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from predictions_cup.historical.fills import FILL_SCHEMA
from predictions_cup.historical.pmxt import SCHEMAS, STREAM_TIME_FIELD
from predictions_cup.historical.regimes import SCHEMA_VERSION

STREAMS = ("depth_snapshots", "book_changes", "trades")


def _aware(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--regime", required=True)
    parser.add_argument("--token", action="append", required=True)
    parser.add_argument("--start-at", type=_aware, required=True)
    parser.add_argument("--end-at", type=_aware, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.corpus / f"schema_version={SCHEMA_VERSION}" / args.regime
    target = args.output / args.regime
    files = {}
    for stream in (*STREAMS, "fills"):
        stream_dir = source / "fills" if stream == "fills" else source / "books" / stream
        schema = FILL_SCHEMA if stream == "fills" else SCHEMAS[stream]
        time_field = "observed_at" if stream == "fills" else STREAM_TIME_FIELD[stream]
        paths = sorted(str(p) for p in stream_dir.rglob("*.parquet"))
        dataset = pads.dataset(paths, format="parquet", schema=schema)
        table = dataset.to_table(
            filter=(pads.field(time_field) >= args.start_at)
            & (pads.field(time_field) < args.end_at)
            & pads.field("token_id").isin(args.token)
        )
        table = table.take(pc.sort_indices(table, sort_keys=[(time_field, "ascending")]))
        out = target / ("fills" if stream == "fills" else f"books/{stream}") / "part-0.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table.cast(schema), out, compression="zstd")
        files[out.relative_to(args.output).as_posix()] = {
            "rows": table.num_rows,
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        }

    corpus_manifest = args.corpus / f"schema_version={SCHEMA_VERSION}" / "corpus_manifest.json"
    manifest = {
        "description": "Real DATA-001 corpus rows, unmodified, for BUILD-005/EXPERIMENT-002 tests",
        "regime_id": args.regime,
        "tokens": sorted(args.token),
        "start_at": args.start_at.isoformat(),
        "end_at_exclusive": args.end_at.isoformat(),
        "source_corpus_manifest_sha256": hashlib.sha256(
            corpus_manifest.read_bytes()
        ).hexdigest(),
        "files": files,
    }
    (args.output / "fixture_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
