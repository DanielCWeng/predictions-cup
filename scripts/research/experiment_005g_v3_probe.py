# ruff: noqa
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "data/mappings/sig_polymarket_2026.json"


def text_id(value: object) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), byteorder="big", signed=False))
    return str(value)


def mapped_tokens() -> tuple[set[str], set[str]]:
    payload = json.loads(MAPPING.read_text(encoding="utf-8"))
    all_tokens: set[str] = set()
    representative: set[str] = set()
    for record in payload["records"]:
        identities = []
        if record.get("direct_polymarket"):
            identities.append(record["direct_polymarket"])
        identities.extend(record.get("polymarket_components") or [])
        for identity in identities:
            tokens = [str(value) for value in identity["token_ids"]]
            outcomes = [str(value).lower() for value in identity["outcomes"]]
            all_tokens.update(tokens)
            representative.add(tokens[outcomes.index("yes")] if "yes" in outcomes else tokens[0])
    return all_tokens, representative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    manifest = json.loads(Path(args.input_manifest).read_text(encoding="utf-8"))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    all_mapped, representative = mapped_tokens()

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
        assets = {text_id(value) for value in table["asset_id"].drop_null().to_pylist()}
        price_change = table.filter(pc.equal(table["event_type"], "price_change"))
        pc_assets = [text_id(value) for value in price_change["asset_id"].to_pylist()]
        rep_mask = [value in representative for value in pc_assets]
        representative_rows = sum(rep_mask)
        valid_bbo_rows = 0
        for keep, bid, ask in zip(
            rep_mask,
            price_change["best_bid"].to_pylist(),
            price_change["best_ask"].to_pylist(),
            strict=True,
        ):
            if not keep or bid is None or ask is None:
                continue
            bid_f = float(bid)
            ask_f = float(ask)
            valid_bbo_rows += 0 < bid_f <= ask_f < 1

        rows.append({
            "remote": item["remote"],
            "rows": table.num_rows,
            "schema": [{"name": field.name, "type": str(field.type)} for field in parquet.schema_arrow],
            "events": events,
            "distinct_assets": len(assets),
            "mapped_asset_intersection": len(assets & all_mapped),
            "representative_asset_intersection": len(assets & representative),
            "representative_price_change_rows": representative_rows,
            "representative_valid_bbo_price_change_rows": int(valid_bbo_rows),
            "asset_samples": sorted(assets)[:10],
        })

    (output / "v3_semantic_probe.json").write_text(
        json.dumps({"experiment": "EXPERIMENT-005G", "holdout_read": False, "files": rows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
