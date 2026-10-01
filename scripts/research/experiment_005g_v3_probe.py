# ruff: noqa
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    manifest = json.loads(Path(args.input_manifest).read_text(encoding="utf-8"))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = []

    for item in manifest["files"]:
        parquet = pq.ParquetFile(Path(item["local"]))
        table = parquet.read()
        events = {}
        for event_type in pc.unique(table["event_type"]).to_pylist():
            sub = table.filter(pc.equal(table["event_type"], event_type))
            events[str(event_type)] = {
                "rows": sub.num_rows,
                "best_bid_non_null": sub["best_bid"].null_count < sub.num_rows,
                "best_ask_non_null": sub["best_ask"].null_count < sub.num_rows,
                "price_non_null": sub["price"].null_count < sub.num_rows,
                "size_non_null": sub["size"].null_count < sub.num_rows,
                "side_non_null": sub["side"].null_count < sub.num_rows,
            }
        rows.append({
            "remote": item["remote"],
            "rows": table.num_rows,
            "schema": [{"name": field.name, "type": str(field.type)} for field in parquet.schema_arrow],
            "events": events,
        })

    (output / "v3_semantic_probe.json").write_text(
        json.dumps({"experiment": "EXPERIMENT-005G", "holdout_read": False, "files": rows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
