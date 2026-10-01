# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def levels(raw: Any) -> dict[float, float]:
    if raw is None:
        return {}
    out: dict[float, float] = {}
    for item in list(raw):
        try:
            if isinstance(item, dict):
                p = float(item["price"])
                s = float(item["size"])
            elif hasattr(item, "as_py"):
                v = item.as_py()
                p = float(v["price"])
                s = float(v["size"])
            else:
                p = float(item[0])
                s = float(item[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out[p] = s
    return out


def top(book: dict[float, float], bid: bool) -> float | None:
    if not book:
        return None
    return max(book) if bid else min(book)


def equal_price(a: float | None, b: Any, tol: float = 0.000051) -> bool:
    if a is None or b is None or pd.isna(b):
        return a is None and (b is None or pd.isna(b))
    return abs(float(a) - float(b)) <= tol


def apply_update(book: dict[float, float], price: float, size: float, absolute: bool) -> None:
    new_size = size if absolute else book.get(price, 0.0) + size
    if new_size <= 0:
        book.pop(price, None)
    else:
        book[price] = new_size


def evaluate(path: Path) -> dict[str, Any]:
    cols = [
        "event_type", "timestamp_received", "sequence", "asset_id", "best_bid", "best_ask",
        "bids", "asks", "price", "size", "side",
    ]
    pf = pq.ParquetFile(path)
    names = set(pf.schema_arrow.names)
    missing = [x for x in cols if x not in names]
    if missing:
        raise RuntimeError(f"missing required columns: {missing}")
    df = pf.read(columns=cols, use_threads=True).to_pandas()
    df["timestamp_received"] = pd.to_datetime(df["timestamp_received"], utc=True)
    df["_seq"] = pd.to_numeric(df["sequence"], errors="coerce").fillna(-1).astype("int64")
    df.sort_values(["timestamp_received", "_seq"], inplace=True, kind="stable")

    hypotheses = {
        "BUY_BID_ABSOLUTE": {"buy_is_bid": True, "absolute": True},
        "BUY_ASK_ABSOLUTE": {"buy_is_bid": False, "absolute": True},
        "BUY_BID_DELTA": {"buy_is_bid": True, "absolute": False},
        "BUY_ASK_DELTA": {"buy_is_bid": False, "absolute": False},
    }
    state: dict[str, dict[str, dict[str, dict[float, float]]]] = {
        name: defaultdict(lambda: {"bid": {}, "ask": {}}) for name in hypotheses
    }
    stats = {
        name: {
            "price_change_after_seed": 0,
            "both_bbo_match": 0,
            "bid_match": 0,
            "ask_match": 0,
            "side_unknown": 0,
            "invalid_update": 0,
        }
        for name in hypotheses
    }
    seeds: set[str] = set()
    rows_seen = 0

    for row in df.itertuples(index=False):
        event_type = str(row.event_type)
        asset = str(row.asset_id)
        if event_type == "book":
            bid = levels(row.bids)
            ask = levels(row.asks)
            if not bid and not ask:
                continue
            seeds.add(asset)
            for name in hypotheses:
                state[name][asset] = {"bid": dict(bid), "ask": dict(ask)}
            continue
        if event_type != "price_change" or asset not in seeds:
            continue
        rows_seen += 1
        try:
            price = float(row.price)
            size = float(row.size)
        except (TypeError, ValueError):
            for name in hypotheses:
                stats[name]["invalid_update"] += 1
            continue
        side = str(row.side).upper()
        for name, rule in hypotheses.items():
            s = stats[name]
            s["price_change_after_seed"] += 1
            if side not in {"BUY", "SELL"}:
                s["side_unknown"] += 1
                continue
            is_bid = (side == "BUY") if rule["buy_is_bid"] else (side == "SELL")
            target = state[name][asset]["bid" if is_bid else "ask"]
            apply_update(target, price, size, bool(rule["absolute"]))
            b = top(state[name][asset]["bid"], True)
            a = top(state[name][asset]["ask"], False)
            bm = equal_price(b, row.best_bid)
            am = equal_price(a, row.best_ask)
            s["bid_match"] += int(bm)
            s["ask_match"] += int(am)
            s["both_bbo_match"] += int(bm and am)

    for _name, s in stats.items():
        n = max(int(s["price_change_after_seed"]), 1)
        s["both_match_rate"] = s["both_bbo_match"] / n
        s["bid_match_rate"] = s["bid_match"] / n
        s["ask_match_rate"] = s["ask_match"] / n

    ranked = sorted(
        stats,
        key=lambda name: (
            stats[name]["both_match_rate"],
            stats[name]["bid_match_rate"] + stats[name]["ask_match_rate"],
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    return {
        "file": path.name,
        "rows": int(pf.metadata.num_rows),
        "seeded_assets": len(seeds),
        "price_change_rows_after_seed": rows_seen,
        "hypotheses": stats,
        "ranking": ranked,
        "best": best,
        "best_both_match_rate": stats[best]["both_match_rate"] if best else None,
        "runner_up_both_match_rate": stats[runner_up]["both_match_rate"] if runner_up else None,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    dataset = str(spec["dataset"])
    files = [str(x) for x in spec["files"]]
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    temp = out / "_downloads"
    temp.mkdir(parents=True, exist_ok=True)

    results = []
    for i, file_name in enumerate(files):
        dest = temp / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            run(["kaggle", "datasets", "download", dataset, "-f", file_name, "-p", str(dest), "--unzip"])
            parquets = list(dest.rglob("*.parquet"))
            if len(parquets) != 1:
                raise RuntimeError(f"expected one parquet, got {len(parquets)}")
            result = evaluate(parquets[0])
            result["source_file"] = file_name
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
        finally:
            shutil.rmtree(dest, ignore_errors=True)
    (out / "LEVEL_SEMANTICS.json").write_text(
        json.dumps({"experiment": "EXPERIMENT-005I", "results": results}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
