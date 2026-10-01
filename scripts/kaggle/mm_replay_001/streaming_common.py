# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working")
PRIMARY_SCOPE = "baseline_sep"
START_NS = 1_788_220_800_000_000_000
END_NS = 1_790_640_000_000_000_000
GRID_NS = 15_000_000_000
FV_SAMPLE_NS = 60_000_000_000
GAP_NS = 300_000_000_000
REQUIRED_005F = "PRE_ELECTION|clock|UPDATE_HAZARD"

COMPACT_SCHEMA = pa.schema(
    [
        ("sig_market_id", pa.string()),
        ("condition_id", pa.string()),
        ("token_id", pa.string()),
        ("role", pa.string()),
        ("timestamp_ns", pa.int64()),
        ("sequence", pa.int64()),
        ("kind", pa.string()),
        ("best_bid", pa.float64()),
        ("best_ask", pa.float64()),
        ("trade_price", pa.float64()),
        ("trade_size", pa.float64()),
        ("trade_side", pa.string()),
        ("source_version", pa.string()),
    ]
)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str] | None = None,
) -> None:
    if not rows:
        rows = [{"status": "EMPTY"}]
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def valid_bbo(bid: float | None, ask: float | None) -> bool:
    return (
        bid is not None
        and ask is not None
        and 0.0 < bid <= ask < 1.0
    )


def dataset_root() -> Path:
    matches = sorted(INPUT.rglob(PRIMARY_SCOPE))
    matches = [
        path
        for path in matches
        if path.is_dir()
        and path.parent.name == "sig-cup-data003-orderbooks"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {PRIMARY_SCOPE} directory, got {matches}"
        )
    return matches[0].parent


def token_bytes(token_id: str) -> bytes:
    return int(token_id).to_bytes(32, byteorder="big", signed=False)


def datetime_ns(values: Any) -> np.ndarray:
    return (
        pd.DatetimeIndex(pd.to_datetime(values, utc=True))
        .as_unit("ns")
        .asi8.astype(np.int64, copy=False)
    )


def best_from_levels(levels: Any, *, bid: bool) -> float | None:
    if levels is None:
        return None
    prices: list[float] = []
    try:
        iterable = list(levels)
    except TypeError:
        return None
    for item in iterable:
        if item is None:
            continue
        value = (
            item.get("price")
            if isinstance(item, dict)
            else getattr(item, "price", None)
        )
        price = finite(value)
        if price is not None and 0.0 < price < 1.0:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if bid else min(prices)


def compact_baseline(
    root: Path,
    universe: dict[str, Any],
) -> dict[str, Any]:
    compact_dir = WORK / "compact"
    compact_dir.mkdir(parents=True, exist_ok=True)

    token_meta: dict[bytes, dict[str, str]] = {}
    sig_map: dict[bytes, str] = {}
    condition_map: dict[bytes, str] = {}
    token_id_map: dict[bytes, str] = {}
    role_map: dict[bytes, str] = {}

    for record in universe["records"]:
        sig_market_id = str(record["sig_market_id"])
        for token_key, role in (
            ("local_token_id", "LOCAL"),
            ("complement_token_id", "COMPLEMENT"),
        ):
            token_id = str(record[token_key])
            raw = token_bytes(token_id)
            meta = {
                "sig_market_id": sig_market_id,
                "condition_id": str(record["condition_id"]),
                "token_id": token_id,
                "role": role,
            }
            token_meta[raw] = meta
            sig_map[raw] = sig_market_id
            condition_map[raw] = str(record["condition_id"])
            token_id_map[raw] = token_id
            role_map[raw] = role

    files = sorted((root / PRIMARY_SCOPE).rglob("events.parquet"))
    writers: dict[str, pq.ParquetWriter] = {}
    counters: defaultdict[str, int] = defaultdict(int)
    selected_raw = list(token_meta)
    min_ts: int | None = None
    max_ts: int | None = None

    try:
        for file_index, path in enumerate(files):
            parquet_file = pq.ParquetFile(path)
            names = set(parquet_file.schema_arrow.names)
            requested = [
                "event_type",
                "timestamp_received",
                "sequence",
                "asset_id",
                "best_bid",
                "best_ask",
                "bids",
                "asks",
                "price",
                "size",
                "side",
                "source_version",
            ]
            columns = [name for name in requested if name in names]
            table = pq.read_table(
                path,
                columns=columns,
                use_threads=False,
            )
            counters["raw_rows"] += table.num_rows
            if table.num_rows == 0 or "asset_id" not in table.column_names:
                continue

            asset_type = table.schema.field("asset_id").type
            values = pa.array(selected_raw, type=asset_type)
            mask = pc.fill_null(
                pc.is_in(table["asset_id"], value_set=values),
                False,
            )
            table = table.filter(mask)
            counters["selected_raw_rows"] += table.num_rows
            if table.num_rows == 0:
                continue

            frame = table.to_pandas()
            frame["asset_key"] = frame["asset_id"].map(
                lambda value: bytes(value) if value is not None else None
            )
            frame["sig_market_id"] = frame["asset_key"].map(sig_map)
            frame["condition_id"] = frame["asset_key"].map(condition_map)
            frame["token_id"] = frame["asset_key"].map(token_id_map)
            frame["role"] = frame["asset_key"].map(role_map)
            frame = frame[frame["sig_market_id"].notna()].copy()
            if frame.empty:
                continue

            frame["timestamp_ns"] = datetime_ns(frame["timestamp_received"])
            min_file = int(frame["timestamp_ns"].min())
            max_file = int(frame["timestamp_ns"].max())
            min_ts = min_file if min_ts is None else min(min_ts, min_file)
            max_ts = max_file if max_ts is None else max(max_ts, max_file)

            sequence_source = (
                frame["sequence"]
                if "sequence" in frame.columns
                else pd.Series(-1, index=frame.index)
            )
            frame["sequence"] = (
                pd.to_numeric(sequence_source, errors="coerce")
                .fillna(-1)
                .astype("int64")
            )
            frame["event_type"] = frame["event_type"].astype(str)
            for source, destination in (
                ("best_bid", "best_bid_num"),
                ("best_ask", "best_ask_num"),
                ("price", "price_num"),
                ("size", "size_num"),
            ):
                if source in frame.columns:
                    frame[destination] = pd.to_numeric(
                        frame[source],
                        errors="coerce",
                    )
                else:
                    frame[destination] = np.nan

            key_columns = [
                "sig_market_id",
                "condition_id",
                "token_id",
                "role",
                "timestamp_ns",
            ]

            scalar = frame[
                frame["event_type"].isin(["price_change", "best_bid_ask"])
            ].copy()
            counters["scalar_bbo_rows"] += len(scalar)
            bbo_parts: list[pd.DataFrame] = []
            if not scalar.empty:
                grouped = scalar.groupby(
                    key_columns,
                    sort=False,
                    observed=True,
                )
                first = grouped[
                    ["best_bid_num", "best_ask_num", "sequence", "source_version"]
                ].first()
                unique = grouped[
                    ["best_bid_num", "best_ask_num"]
                ].nunique(dropna=True)
                first["best_bid"] = first["best_bid_num"].where(
                    (unique["best_bid_num"] == 1)
                    & (unique["best_ask_num"] == 1)
                )
                first["best_ask"] = first["best_ask_num"].where(
                    (unique["best_bid_num"] == 1)
                    & (unique["best_ask_num"] == 1)
                )
                scalar_compact = first.reset_index()
                scalar_compact["kind"] = "BBO"
                scalar_compact["trade_price"] = np.nan
                scalar_compact["trade_size"] = np.nan
                scalar_compact["trade_side"] = None
                bbo_parts.append(
                    scalar_compact[
                        [
                            *key_columns,
                            "sequence",
                            "kind",
                            "best_bid",
                            "best_ask",
                            "trade_price",
                            "trade_size",
                            "trade_side",
                            "source_version",
                        ]
                    ]
                )
                counters["scalar_bbo_groups"] += len(scalar_compact)

            books = frame[frame["event_type"] == "book"].copy()
            counters["book_rows"] += len(books)
            if not books.empty:
                boundary = pd.concat(
                    [
                        books.groupby(
                            ["sig_market_id", "token_id"],
                            sort=False,
                            observed=True,
                        ).head(1),
                        books.groupby(
                            ["sig_market_id", "token_id"],
                            sort=False,
                            observed=True,
                        ).tail(1),
                    ],
                    ignore_index=False,
                ).drop_duplicates(
                    subset=[
                        "sig_market_id",
                        "token_id",
                        "timestamp_ns",
                        "sequence",
                    ]
                )
                book_rows: list[dict[str, Any]] = []
                for row in boundary.itertuples(index=False):
                    book_rows.append(
                        {
                            "sig_market_id": str(row.sig_market_id),
                            "condition_id": str(row.condition_id),
                            "token_id": str(row.token_id),
                            "role": str(row.role),
                            "timestamp_ns": int(row.timestamp_ns),
                            "sequence": int(row.sequence),
                            "kind": "BBO",
                            "best_bid": best_from_levels(row.bids, bid=True),
                            "best_ask": best_from_levels(row.asks, bid=False),
                            "trade_price": None,
                            "trade_size": None,
                            "trade_side": None,
                            "source_version": str(row.source_version),
                        }
                    )
                if book_rows:
                    bbo_parts.append(pd.DataFrame(book_rows))
                counters["book_boundary_rows"] += len(book_rows)

            trades = frame[frame["event_type"] == "last_trade_price"].copy()
            counters["trade_rows_raw"] += len(trades)
            trade_parts: list[pd.DataFrame] = []
            if not trades.empty:
                trades["trade_side"] = trades["side"].astype(str).str.upper()
                good = (
                    trades["price_num"].notna()
                    & trades["size_num"].notna()
                    & (trades["size_num"] > 0.0)
                    & trades["trade_side"].isin(["BUY", "SELL"])
                )
                counters["invalid_trade_rows"] += int((~good).sum())
                trades = trades[good].copy()
                if not trades.empty:
                    trades["kind"] = "TRADE"
                    trades["best_bid"] = np.nan
                    trades["best_ask"] = np.nan
                    trades["trade_price"] = trades["price_num"].astype(float)
                    trades["trade_size"] = trades["size_num"].astype(float)
                    trade_parts.append(
                        trades[
                            [
                                *key_columns,
                                "sequence",
                                "kind",
                                "best_bid",
                                "best_ask",
                                "trade_price",
                                "trade_size",
                                "trade_side",
                                "source_version",
                            ]
                        ]
                    )
                counters["trade_rows"] += len(trades)

            counters["best_bid_ask_rows"] += int(
                (frame["event_type"] == "best_bid_ask").sum()
            )
            counters["ignored_selected_rows"] += int(
                (
                    ~frame["event_type"].isin(
                        [
                            "price_change",
                            "best_bid_ask",
                            "book",
                            "last_trade_price",
                        ]
                    )
                ).sum()
            )

            parts = bbo_parts + trade_parts
            if not parts:
                continue
            compact = pd.concat(parts, ignore_index=True, sort=False)
            compact.sort_values(
                ["sig_market_id", "timestamp_ns", "sequence", "kind"],
                inplace=True,
                kind="stable",
            )
            for sig_market_id, group in compact.groupby(
                "sig_market_id",
                sort=False,
                observed=True,
            ):
                sig_market_id = str(sig_market_id)
                writer = writers.get(sig_market_id)
                if writer is None:
                    writer = pq.ParquetWriter(
                        compact_dir / f"{sig_market_id}.parquet",
                        COMPACT_SCHEMA,
                        compression="zstd",
                    )
                    writers[sig_market_id] = writer
                writer.write_table(
                    pa.Table.from_pandas(
                        group,
                        schema=COMPACT_SCHEMA,
                        preserve_index=False,
                    )
                )
                counters["compact_rows"] += len(group)

            if (file_index + 1) % 48 == 0:
                print(
                    "compact progress "
                    f"{file_index + 1}/{len(files)} "
                    f"selected={counters['selected_raw_rows']} "
                    f"compact={counters['compact_rows']}",
                    flush=True,
                )
    finally:
        for writer in writers.values():
            writer.close()

    return {
        "baseline_files": len(files),
        "markets_expected": len(universe["records"]),
        "market_files_written": len(writers),
        "selected_tokens": len(token_meta),
        "min_timestamp_ns": min_ts,
        "max_timestamp_ns": max_ts,
        "book_compaction": "first_and_last_snapshot_per_token_per_hour",
        "scalar_bbo_compaction": "token_timestamp_group_with_ambiguity_preserved",
        **dict(counters),
    }


@dataclass(frozen=True)
class BboGroup:
    timestamp_ns: int
    best_bid: float | None
    best_ask: float | None
    ambiguous: bool

    @property
    def midpoint(self) -> float | None:
        if not valid_bbo(self.best_bid, self.best_ask):
            return None
        assert self.best_bid is not None
        assert self.best_ask is not None
        return (self.best_bid + self.best_ask) / 2.0


def bbo_groups(
    frame: pd.DataFrame,
    token_id: str,
) -> list[BboGroup]:
    subset = frame[
        (frame["kind"] == "BBO")
        & (frame["token_id"].astype(str) == token_id)
    ]
    groups: list[BboGroup] = []
    for timestamp_ns, group in subset.groupby(
        "timestamp_ns",
        sort=True,
    ):
        pairs: set[tuple[float, float]] = set()
        for bid, ask in zip(
            group["best_bid"],
            group["best_ask"],
            strict=False,
        ):
            bid_value = finite(bid)
            ask_value = finite(ask)
            if valid_bbo(bid_value, ask_value):
                assert bid_value is not None
                assert ask_value is not None
                pairs.add(
                    (
                        round(bid_value, 12),
                        round(ask_value, 12),
                    )
                )
        if len(pairs) == 1:
            bid_value, ask_value = next(iter(pairs))
            groups.append(
                BboGroup(
                    int(timestamp_ns),
                    bid_value,
                    ask_value,
                    False,
                )
            )
        else:
            groups.append(
                BboGroup(
                    int(timestamp_ns),
                    None,
                    None,
                    len(pairs) > 1,
                )
            )
    return groups


def genuine_times(groups: list[BboGroup]) -> list[int]:
    out: list[int] = []
    previous: BboGroup | None = None
    for current in groups:
        current_valid = (
            current.midpoint is not None
            and not current.ambiguous
        )
        if previous is not None:
            previous_valid = (
                previous.midpoint is not None
                and not previous.ambiguous
            )
            contiguous = (
                current_valid
                and previous_valid
                and current.timestamp_ns
                - previous.timestamp_ns
                <= GAP_NS
            )
            same_bbo = (
                contiguous
                and current.best_bid is not None
                and current.best_ask is not None
                and previous.best_bid is not None
                and previous.best_ask is not None
                and math.isclose(
                    current.best_bid,
                    previous.best_bid,
                    abs_tol=1e-12,
                    rel_tol=0.0,
                )
                and math.isclose(
                    current.best_ask,
                    previous.best_ask,
                    abs_tol=1e-12,
                    rel_tol=0.0,
                )
            )
            if contiguous and not same_bbo:
                out.append(current.timestamp_ns)
        previous = current
    return out


def asof_group(
    groups: list[BboGroup],
    times: list[int],
    timestamp_ns: int,
) -> BboGroup | None:
    index = bisect_right(times, timestamp_ns) - 1
    return groups[index] if index >= 0 else None


def load_frozen_pre_model(
    fit_manifest_path: Path,
) -> tuple[Any, Any, dict[str, Any], dict[str, str]]:
    fit = load_json(fit_manifest_path)
    artifact_row = next(
        row
        for row in fit["candidates"]
        if row["candidate_id"] == REQUIRED_005F
    )
    hashes = artifact_row["artifact_sha256"]
    wanted = {
        "model": str(hashes["challenger_model.joblib"]),
        "scaler": str(hashes["challenger_scaler.joblib"]),
    }
    found: dict[str, Path] = {}
    for path in INPUT.rglob("*.joblib"):
        digest = sha256_file(path)
        for key, expected in wanted.items():
            if digest == expected:
                found[key] = path
        if len(found) == 2:
            break
    if set(found) != {"model", "scaler"}:
        raise RuntimeError(
            "frozen PRE_ELECTION 005F artifacts unavailable: "
            f"found={found}"
        )
    return (
        joblib.load(found["model"]),
        joblib.load(found["scaler"]),
        artifact_row,
        wanted,
    )


def score_features(
    model: Any,
    scaler: Any,
    artifact_row: dict[str, Any],
    features: dict[str, float],
) -> float | None:
    columns = [
        str(name)
        for name in artifact_row["challenger_columns"]
    ]
    values = [
        features.get(name, math.nan)
        for name in columns
    ]
    x = np.asarray([values], dtype=float)
    if not np.all(np.isfinite(x)):
        return None
    transformed = scaler.transform(x)
    if bool(artifact_row.get("classification")):
        score = float(
            model.predict_proba(transformed)[0, 1]
        )
    else:
        score = float(model.predict(transformed)[0])
    return score if math.isfinite(score) else None


def mean(values: Iterable[float | None]) -> float | None:
    finite_values = [
        float(value)
        for value in values
        if value is not None
        and math.isfinite(float(value))
    ]
    return (
        float(np.mean(finite_values))
        if finite_values
        else None
    )


def split_for(timestamp_ns: int) -> str:
    fraction = (
        (timestamp_ns - START_NS)
        / max(1, END_NS - START_NS)
    )
    if fraction < 0.60:
        return "TRAIN"
    if fraction < 0.80:
        return "DEV"
    return "FINAL"
