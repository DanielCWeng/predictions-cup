# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_PACKAGE_INITS = sorted(Path("/kaggle/input").rglob("predictions_cup/__init__.py"))
if len(_PACKAGE_INITS) != 1:
    raise RuntimeError(
        f"expected one unpacked predictions_cup package, found {_PACKAGE_INITS}"
    )
_CODE_ROOT = _PACKAGE_INITS[0].parent.parent
sys.path.insert(0, str(_CODE_ROOT))

from predictions_cup.mm_replay_001 import (
    CANCEL_LATENCIES_MS,
    MARKOUT_HORIZONS_S,
    BookObservation,
    ConservativeTradeFillModel,
    Frozen005FTransferAdapter,
    TradeThroughSensitivityFillModel,
    Side,
    default_policies,
    genuine_005f_change_times,
    group_bbo_for_005f,
    replay_market,
)

RAW_SLUG = "sig-cup-data003-orderbooks"
WINDOW = "ev18_ok_sc_runoff_ga_runoff"
WINDOW_START = pd.Timestamp("2026-08-24T00:00:00Z")
WINDOW_END = pd.Timestamp("2026-08-28T00:00:00Z")
GRID_ORIGIN_NS = int(WINDOW_START.value)
BUCKETS = 32
WORK = Path("/kaggle/working")
HERE = Path(__file__).resolve().parent
CODE_DATASET_ROOT = _CODE_ROOT.parent
MAPPING_PATH = CODE_DATASET_ROOT / "sig_polymarket_2026.json"

COMPACT_SCHEMA = pa.schema(
    [
        ("token_id", pa.string()),
        ("condition_id", pa.string()),
        ("timestamp_ns", pa.int64()),
        ("sequence", pa.uint64()),
        ("event_kind", pa.string()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("trade_price", pa.float64()),
        ("trade_size", pa.float64()),
        ("trade_side", pa.string()),
    ]
)


def find_raw_root() -> Path:
    exact = []
    for path in Path("/kaggle/input").rglob(RAW_SLUG):
        if path.is_dir() and (path / "_manifests").is_dir():
            exact.append(path)
    if len(exact) != 1:
        raise RuntimeError(f"cannot uniquely locate {RAW_SLUG}: {exact}")
    return exact[0]


def mapped_tokens() -> set[str]:
    payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    tokens: set[str] = set()
    for record in payload.get("records", []):
        if record.get("status") != "VERIFIED":
            continue
        direct = record.get("direct_polymarket")
        if isinstance(direct, dict) and direct.get("mapped_token_id"):
            tokens.add(str(direct["mapped_token_id"]))
        for component in record.get("polymarket_components") or []:
            if isinstance(component, dict) and component.get("mapped_token_id"):
                tokens.add(str(component["mapped_token_id"]))
    if not tokens:
        raise RuntimeError("accepted mapping produced zero mapped tokens")
    return tokens


def bytes_to_token(value: bytes) -> str:
    return str(int.from_bytes(value, "big", signed=False))


def bytes_to_condition(value: bytes) -> str:
    return "0x" + value.hex()


def best_from_levels(levels: Any, *, bid: bool) -> float | None:
    if levels is None:
        return None
    values = []
    try:
        for level in levels:
            if isinstance(level, dict):
                price = float(level["price"])
            else:
                price = float(level[0])
            if math.isfinite(price):
                values.append(price)
    except (TypeError, ValueError, KeyError):
        return None
    if not values:
        return None
    return max(values) if bid else min(values)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compact_ev18(root: Path, accepted_tokens: set[str]) -> dict[str, Any]:
    window_dir = root / WINDOW
    all_files = sorted(window_dir.rglob("*.parquet"))
    if len(all_files) != 96:
        raise RuntimeError(f"EV18 expected 96 hourly files, found {len(all_files)}")
    files = all_files[:6]

    target_bytes = {int(token).to_bytes(32, "big") for token in accepted_tokens}
    compact_root = WORK / "_ev18_compact"
    if compact_root.exists():
        shutil.rmtree(compact_root)
    compact_root.mkdir(parents=True)

    last_bbo: dict[bytes, tuple[float, float]] = {}
    counts: defaultdict[str, int] = defaultdict(int)
    unique_tokens: set[str] = set()
    event_type_counts: defaultdict[str, int] = defaultdict(int)

    wanted = [
        "event_type",
        "timestamp_received",
        "sequence",
        "timestamp",
        "market",
        "asset_id",
        "best_bid",
        "best_ask",
        "bids",
        "asks",
        "price",
        "size",
        "side",
        "fee_rate_bps",
        "source_version",
    ]

    for file_index, path in enumerate(files):
        pf = pq.ParquetFile(path)
        available = [c for c in wanted if c in pf.schema_arrow.names]
        table = pf.read(columns=available)
        counts["raw_rows"] += table.num_rows
        df = table.to_pandas()
        if df.empty:
            continue

        df = df[df["asset_id"].isin(target_bytes)]
        counts["mapped_token_rows"] += len(df)
        if df.empty:
            continue

        relevant = df["event_type"].astype(str).isin(
            ["book", "price_change", "last_trade_price"]
        )
        df = df[relevant].copy()
        counts["relevant_rows"] += len(df)
        if df.empty:
            continue
        for kind, n in df["event_type"].astype(str).value_counts().items():
            event_type_counts[str(kind)] += int(n)

        df["timestamp_ns"] = pd.to_datetime(
            df["timestamp_received"], utc=True
        ).astype("int64")
        if "sequence" not in df.columns:
            raise RuntimeError("EV18 V3 must carry sequence")
        df["sequence"] = pd.to_numeric(df["sequence"], errors="raise").astype("uint64")

        pc_rows = df[df["event_type"].astype(str) == "price_change"].copy()
        if not pc_rows.empty:
            pc_rows["bb"] = pd.to_numeric(pc_rows["best_bid"], errors="coerce")
            pc_rows["ba"] = pd.to_numeric(pc_rows["best_ask"], errors="coerce")
            pc_rows = pc_rows[
                pc_rows["bb"].notna()
                & pc_rows["ba"].notna()
                & (pc_rows["bb"] > 0.0)
                & (pc_rows["ba"] < 1.0)
                & (pc_rows["bb"] <= pc_rows["ba"])
            ]

        book_rows = df[df["event_type"].astype(str) == "book"].copy()
        book_records: list[dict[str, Any]] = []
        if not book_rows.empty:
            for rec in book_rows[
                ["asset_id", "market", "timestamp_ns", "sequence", "bids", "asks"]
            ].to_dict(orient="records"):
                bb = best_from_levels(rec["bids"], bid=True)
                ba = best_from_levels(rec["asks"], bid=False)
                if bb is None or ba is None or not (0.0 < bb <= ba < 1.0):
                    counts["book_rows_invalid_bbo"] += 1
                    continue
                book_records.append(
                    {
                        "asset_id": rec["asset_id"],
                        "market": rec["market"],
                        "timestamp_ns": int(rec["timestamp_ns"]),
                        "sequence": int(rec["sequence"]),
                        "bb": bb,
                        "ba": ba,
                    }
                )

        bbo_frames = []
        if not pc_rows.empty:
            bbo_frames.append(
                pc_rows[
                    ["asset_id", "market", "timestamp_ns", "sequence", "bb", "ba"]
                ]
            )
        if book_records:
            bbo_frames.append(pd.DataFrame(book_records))

        kept_bbo = pd.DataFrame()
        if bbo_frames:
            bbo = pd.concat(bbo_frames, ignore_index=True)
            bbo.sort_values(
                ["asset_id", "timestamp_ns", "sequence"],
                kind="stable",
                inplace=True,
            )
            prev_bb = bbo.groupby("asset_id", sort=False)["bb"].shift()
            prev_ba = bbo.groupby("asset_id", sort=False)["ba"].shift()
            changed = (
                prev_bb.isna()
                | prev_ba.isna()
                | (bbo["bb"] != prev_bb)
                | (bbo["ba"] != prev_ba)
            )

            first_indices = bbo.groupby("asset_id", sort=False).head(1).index
            for idx in first_indices:
                token = bbo.at[idx, "asset_id"]
                prior = last_bbo.get(token)
                if prior is not None:
                    changed.at[idx] = (
                        float(bbo.at[idx, "bb"]) != prior[0]
                        or float(bbo.at[idx, "ba"]) != prior[1]
                    )

            tails = bbo.groupby("asset_id", sort=False).tail(1)
            for rec in tails[["asset_id", "bb", "ba"]].to_dict(orient="records"):
                last_bbo[rec["asset_id"]] = (float(rec["bb"]), float(rec["ba"]))

            kept_bbo = bbo[changed].copy()
            counts["bbo_candidate_rows"] += len(bbo)
            counts["bbo_change_rows"] += len(kept_bbo)

        trades = df[df["event_type"].astype(str) == "last_trade_price"].copy()
        if not trades.empty:
            trades["trade_price"] = pd.to_numeric(trades["price"], errors="coerce")
            trades["trade_size"] = pd.to_numeric(trades["size"], errors="coerce")
            trades["trade_side"] = trades["side"].astype(str).str.upper()
            trades = trades[
                trades["trade_price"].notna()
                & trades["trade_size"].notna()
                & (trades["trade_price"] >= 0.0)
                & (trades["trade_price"] <= 1.0)
                & (trades["trade_size"] > 0.0)
                & trades["trade_side"].isin(["BUY", "SELL"])
            ]
            counts["trade_rows"] += len(trades)

        compact_rows: list[dict[str, Any]] = []
        if not kept_bbo.empty:
            for rec in kept_bbo.to_dict(orient="records"):
                token = bytes_to_token(rec["asset_id"])
                unique_tokens.add(token)
                compact_rows.append(
                    {
                        "token_id": token,
                        "condition_id": bytes_to_condition(rec["market"]),
                        "timestamp_ns": int(rec["timestamp_ns"]),
                        "sequence": int(rec["sequence"]),
                        "event_kind": "BBO",
                        "best_bid": float(rec["bb"]),
                        "best_ask": float(rec["ba"]),
                        "trade_price": None,
                        "trade_size": None,
                        "trade_side": None,
                    }
                )
        if not trades.empty:
            for rec in trades[
                [
                    "asset_id",
                    "market",
                    "timestamp_ns",
                    "sequence",
                    "trade_price",
                    "trade_size",
                    "trade_side",
                ]
            ].to_dict(orient="records"):
                token = bytes_to_token(rec["asset_id"])
                unique_tokens.add(token)
                compact_rows.append(
                    {
                        "token_id": token,
                        "condition_id": bytes_to_condition(rec["market"]),
                        "timestamp_ns": int(rec["timestamp_ns"]),
                        "sequence": int(rec["sequence"]),
                        "event_kind": "TRADE",
                        "best_bid": None,
                        "best_ask": None,
                        "trade_price": float(rec["trade_price"]),
                        "trade_size": float(rec["trade_size"]),
                        "trade_side": str(rec["trade_side"]),
                    }
                )

        by_bucket: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in compact_rows:
            bucket = int(row["token_id"]) % BUCKETS
            by_bucket[bucket].append(row)
        hour_label = path.parent.parent.name.replace("date=", "") + "T" + path.parent.name.replace("hour=", "")
        for bucket, bucket_rows in by_bucket.items():
            out_dir = compact_root / f"bucket={bucket:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{hour_label}.parquet"
            pq.write_table(
                pa.Table.from_pylist(bucket_rows, schema=COMPACT_SCHEMA),
                out,
                compression="zstd",
            )
        counts["compact_rows"] += len(compact_rows)
        print(
            json.dumps(
                {
                    "file": file_index + 1,
                    "of": len(files),
                    "path": str(path.relative_to(root)),
                    "raw": table.num_rows,
                    "mapped": len(df),
                    "bbo_changes": len(kept_bbo),
                    "trades": len(trades),
                    "compact_total": counts["compact_rows"],
                }
            ),
            flush=True,
        )

    audit = {
        "window": WINDOW,
        "window_start": WINDOW_START.isoformat(),
        "window_end": WINDOW_END.isoformat(),
        "hourly_files": len(files),
        "accepted_mapping_tokens": len(accepted_tokens),
        "observed_mapped_tokens": len(unique_tokens),
        "counts": dict(counts),
        "event_type_counts": dict(event_type_counts),
        "source_versions": ["V3"],
        "ordering": "timestamp_received then V3 sequence",
        "external_fv_bound": False,
        "scientific_scope": "EV18_6H_ENGINEERING_SMOKE_ONLY",
    }
    (WORK / "EV18_COMPACTION_AUDIT.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return audit


def load_bucket(bucket_dir: Path) -> pd.DataFrame:
    tables = [pq.read_table(path) for path in sorted(bucket_dir.glob("*.parquet"))]
    if not tables:
        return pd.DataFrame()
    frame = pa.concat_tables(tables).to_pandas()
    frame.sort_values(
        ["token_id", "timestamp_ns", "sequence", "event_kind"],
        kind="stable",
        inplace=True,
    )
    return frame


def observations_for_token(frame: pd.DataFrame) -> tuple[list[BookObservation], list[BookObservation]]:
    replay: list[BookObservation] = []
    bbo_only: list[BookObservation] = []
    best_bid: float | None = None
    best_ask: float | None = None
    token_id = str(frame.iloc[0]["token_id"])

    for rec in frame.to_dict(orient="records"):
        kind = str(rec["event_kind"])
        if kind == "BBO":
            best_bid = float(rec["best_bid"])
            best_ask = float(rec["best_ask"])
            obs = BookObservation(
                market_id=token_id,
                timestamp_ns=int(rec["timestamp_ns"]),
                best_bid=best_bid,
                best_ask=best_ask,
                category=WINDOW,
            )
            replay.append(obs)
            bbo_only.append(obs)
            continue
        if best_bid is None or best_ask is None:
            continue
        replay.append(
            BookObservation(
                market_id=token_id,
                timestamp_ns=int(rec["timestamp_ns"]),
                best_bid=best_bid,
                best_ask=best_ask,
                trade_price=float(rec["trade_price"]),
                trade_size=float(rec["trade_size"]),
                aggressor_side=(
                    Side.BUY if str(rec["trade_side"]) == "BUY" else Side.SELL
                ),
                category=WINDOW,
            )
        )
    return replay, bbo_only


AGE_BUCKETS = [
    (0.0, 5.0, "0-5s"),
    (5.0, 15.0, "5-15s"),
    (15.0, 30.0, "15-30s"),
    (30.0, 60.0, "30-60s"),
    (60.0, 120.0, "60-120s"),
    (120.0, 300.0, "120-300s"),
    (300.0, 600.0, "300-600s"),
    (600.0, 1800.0, "600-1800s"),
    (1800.0, math.inf, "1800s+"),
]


def age_bucket(value: float) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if lo <= value < hi:
            return label
    return "UNKNOWN"


def evaluate_compact(audit: dict[str, Any]) -> None:
    compact_root = WORK / "_ev18_compact"
    b0 = default_policies()[0]
    models = [ConservativeTradeFillModel(), TradeThroughSensitivityFillModel()]

    policy_rows: list[dict[str, Any]] = []
    fill_sample: list[dict[str, Any]] = []
    markout_agg: defaultdict[tuple[str, str, int, int], list[float]] = defaultdict(list)
    transfer_agg: defaultdict[str, dict[str, float]] = defaultdict(
        lambda: {"rows": 0.0, "updates": 0.0, "g15": 0.0, "g60": 0.0}
    )
    transfer_tokens = 0
    transfer_rows = 0
    replay_tokens = 0

    for bucket_dir in sorted(compact_root.glob("bucket=*")):
        frame = load_bucket(bucket_dir)
        if frame.empty:
            continue
        for token_id, token_frame in frame.groupby("token_id", sort=False):
            observations, bbo_only = observations_for_token(token_frame)
            if len(observations) < 2 or not bbo_only:
                continue
            replay_tokens += 1

            for model in models:
                for latency in CANCEL_LATENCIES_MS:
                    results, summary = replay_market(
                        observations,
                        policy=b0,
                        fill_model=model,
                        reaction_delay_ms=latency,
                    )
                    row = asdict(summary)
                    row["token_id"] = str(token_id)
                    row["window"] = WINDOW
                    policy_rows.append(row)
                    for result in results:
                        if len(fill_sample) < 5000:
                            fill_sample.append(
                                {
                                    "token_id": str(token_id),
                                    "timestamp_ns": result.fill.timestamp_ns,
                                    "side": result.fill.side.value,
                                    "price": result.fill.price,
                                    "size": result.fill.size,
                                    "assumption": result.fill.assumption.value,
                                    "latency_ms": result.reaction_delay_ms,
                                    "gross_spread_capture": result.gross_spread_capture,
                                    "markout_300s": result.markouts.get(300),
                                }
                            )
                        for horizon, value in result.markouts.items():
                            markout_agg[
                                (
                                    b0.policy_id,
                                    result.fill.assumption.value,
                                    latency,
                                    int(horizon),
                                )
                            ].append(float(value))

            # Deterministic quarter-sample for an exact 15-second feature-transfer canary.
            if int(str(token_id)) % 4 != 0 or len(bbo_only) < 2:
                continue
            transfer_tokens += 1
            adapter = Frozen005FTransferAdapter(
                scope_id=str(token_id),
                grid_origin_ns=GRID_ORIGIN_NS,
            )
            grouped = group_bbo_for_005f(bbo_only)
            for obs in grouped:
                adapter.observe(
                    timestamp_ns=obs.timestamp_ns,
                    best_bid=obs.best_bid,
                    best_ask=obs.best_ask,
                    ambiguous=obs.ambiguous,
                )
            genuine = genuine_005f_change_times(grouped)
            if not genuine:
                continue
            last_ns = bbo_only[-1].timestamp_ns
            query = max(GRID_ORIGIN_NS, bbo_only[0].timestamp_ns)
            remainder = (query - GRID_ORIGIN_NS) % 15_000_000_000
            if remainder:
                query += 15_000_000_000 - remainder
            genuine_list = list(genuine)
            import bisect

            while query + 300_000_000_000 <= last_ns:
                features = adapter.features(query_timestamp_ns=query)
                if features is not None:
                    left = bisect.bisect_right(genuine_list, query)
                    right = bisect.bisect_right(
                        genuine_list, query + 300_000_000_000
                    )
                    update = float(right > left)
                    label = age_bucket(float(features["genuine_age_s"]))
                    slot = transfer_agg[label]
                    slot["rows"] += 1.0
                    slot["updates"] += update
                    slot["g15"] += float(features["genuine_15"])
                    slot["g60"] += float(features["genuine_60"])
                    transfer_rows += 1
                query += 15_000_000_000

    markout_rows: list[dict[str, Any]] = []
    for (policy_id, assumption, latency, horizon), values in sorted(markout_agg.items()):
        arr = np.asarray(values, dtype=float)
        markout_rows.append(
            {
                "policy_id": policy_id,
                "fill_assumption": assumption,
                "latency_ms": latency,
                "horizon_s": horizon,
                "fills_with_markout": len(values),
                "mean_markout": float(np.mean(arr)),
                "median_markout": float(np.median(arr)),
                "negative_markout_share": float(np.mean(arr < 0.0)),
                "p10_markout": float(np.quantile(arr, 0.10)),
                "p90_markout": float(np.quantile(arr, 0.90)),
            }
        )

    transfer_rows_out: list[dict[str, Any]] = []
    for _, _, label in AGE_BUCKETS:
        slot = transfer_agg.get(label)
        if not slot or slot["rows"] <= 0:
            continue
        n = slot["rows"]
        transfer_rows_out.append(
            {
                "age_bucket": label,
                "grid_rows": int(n),
                "update_h300_rate": slot["updates"] / n,
                "mean_genuine_15": slot["g15"] / n,
                "mean_genuine_60": slot["g60"] / n,
            }
        )

    write_csv(WORK / "EV18_MM_POLICY_RESULTS.csv", policy_rows)
    write_csv(WORK / "EV18_MM_MARKOUTS.csv", markout_rows)
    write_csv(WORK / "EV18_FILL_SAMPLE.csv", fill_sample)
    write_csv(WORK / "EV18_005F_TRANSFER_BUCKETS.csv", transfer_rows_out)

    summary = {
        "window": WINDOW,
        "replay_tokens": replay_tokens,
        "transfer_tokens_deterministic_quarter_sample": transfer_tokens,
        "transfer_grid_rows": transfer_rows,
        "policy_rows": len(policy_rows),
        "markout_rows": len(markout_rows),
        "external_fv": "UNAVAILABLE_NOT_SUBSTITUTED",
        "frozen_005f_model_scoring": "NOT_RUN_REGIME_NOT_BOUND",
        "real_sig_orders_sent": False,
        "audit": audit,
    }
    (WORK / "EV18_PILOT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (WORK / "EV18_FINAL_REPORT.md").write_text(
        "# MM-REPLAY-001 — EV18 6h Engineering Smoke\n\n"
        f"- Raw rows scanned: **{audit['counts'].get('raw_rows', 0):,}**\n"
        f"- Compact rows: **{audit['counts'].get('compact_rows', 0):,}**\n"
        f"- Replay tokens: **{replay_tokens}**\n"
        f"- 005F deterministic quarter-sample tokens: **{transfer_tokens}**\n"
        f"- 005F 15s grid rows: **{transfer_rows:,}**\n"
        "- Historical venue: **Polymarket proxy corpus**\n"
        "- External FV: **NOT BOUND; B1/B2/B3 not evaluated**\n"
        "- Frozen 005F scoring: **NOT RUN until PRE/ACTIVE regime clocks are bound**\n"
        "- Real SIG orders sent: **NO**\n\n"
        "This is an engineering smoke only, not a scientific disposition. "
        "It validates the landed V3 bytes, canonical mapped-token compaction, local-mid "
        "passive replay and feature-level 005F transfer before scaling to older source versions.\n",
        encoding="utf-8",
    )


def main() -> None:
    root = find_raw_root()
    tokens = mapped_tokens()
    audit = compact_ev18(root, tokens)
    evaluate_compact(audit)
    shutil.rmtree(WORK / "_ev18_compact", ignore_errors=True)


if __name__ == "__main__":
    main()
