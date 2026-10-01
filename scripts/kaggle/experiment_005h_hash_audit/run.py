from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

WORK = Path("/kaggle/working")
ORDERBOOK_SLUG = "sig-cup-data003-orderbooks"
ORDERBOOK_WINDOW = "ev18_ok_sc_runoff_ga_runoff"
FILL_WINDOW = "W18"
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3


def bytes_to_token(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), "big", signed=False))
    return str(value)


def normalize_tx_hash(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value).strip().lower()
    if not text or text in {"none", "nan", "<na>"}:
        return ""
    return text if text.startswith("0x") else "0x" + text


def locate_orderbook_root() -> Path:
    roots = [
        path
        for path in Path("/kaggle/input").rglob(ORDERBOOK_SLUG)
        if path.is_dir() and (path / "_manifests").is_dir()
    ]
    if len(roots) != 1:
        raise RuntimeError(f"expected one orderbook root, found {roots}")
    return roots[0]


def locate_fill_files() -> list[Path]:
    files = sorted(
        path
        for path in Path("/kaggle/input").rglob("*.parquet")
        if "fills" in path.parts
        and f"window_id={FILL_WINDOW}" in path.parts
        and "fees" not in path.parts
        and "rebates" not in path.parts
        and "unattributed_fee_legs" not in path.parts
    )
    if not files:
        raise RuntimeError("no DATA-003 W18 fill parquet files found")
    return files


def load_fills(files: list[Path]) -> pd.DataFrame:
    frames = [
        pq.ParquetFile(path).read().to_pandas()
        for path in files
    ]
    df = pd.concat(frames, ignore_index=True)
    required = {
        "timestamp",
        "tx_hash",
        "log_index",
        "condition_id",
        "token_id",
        "outcome_side",
        "price",
        "size_shares",
        "order_is_match_taker_order",
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"fill schema missing {sorted(missing)}")
    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="raise",
    ).astype("int64")
    df["log_index"] = pd.to_numeric(
        df["log_index"],
        errors="raise",
    ).astype("int64")
    df["price"] = pd.to_numeric(df["price"], errors="raise")
    df["size_shares"] = pd.to_numeric(
        df["size_shares"],
        errors="raise",
    )
    df["token_id"] = df["token_id"].astype(str)
    df["condition_id"] = df["condition_id"].astype(str)
    df["tx_hash"] = df["tx_hash"].map(normalize_tx_hash)
    df["outcome_side_norm"] = (
        df["outcome_side"].astype(str).str.upper()
    )
    df["is_active"] = (
        df["order_is_match_taker_order"].astype(bool)
    )
    return df


def accepted_entities(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    passive_rows: list[dict[str, Any]] = []
    rejected: defaultdict[str, int] = defaultdict(int)
    grouped = df.groupby(["condition_id", "tx_hash"], sort=False)
    for (condition_id, tx_hash), rows in grouped:
        active = rows[rows["is_active"]]
        passive = rows[~rows["is_active"]]
        if len(active) != 1:
            rejected["active_count_not_one"] += 1
            continue
        if passive.empty:
            rejected["no_passive"] += 1
            continue
        if not rows["outcome_side_norm"].isin(["YES", "NO"]).all():
            rejected["non_binary"] += 1
            continue

        a = active.iloc[0]
        active_size = float(a["size_shares"])
        passive_size = float(passive["size_shares"].sum())
        size_limit = SIZE_TOLERANCE * max(
            1.0,
            abs(active_size),
            abs(passive_size),
        )
        if abs(active_size - passive_size) > size_limit:
            rejected["size_conservation"] += 1
            continue

        active_p_yes = (
            float(a["price"])
            if a["outcome_side_norm"] == "YES"
            else 1.0 - float(a["price"])
        )
        active_yes_notional = active_p_yes * active_size
        passive_yes_notional = 0.0
        for _, p in passive.iterrows():
            p_yes = (
                float(p["price"])
                if p["outcome_side_norm"] == "YES"
                else 1.0 - float(p["price"])
            )
            passive_yes_notional += (
                p_yes * float(p["size_shares"])
            )
        yes_limit = YES_NOTIONAL_TOLERANCE * max(
            1.0,
            abs(active_yes_notional),
            abs(passive_yes_notional),
        )
        if (
            abs(active_yes_notional - passive_yes_notional)
            > yes_limit
        ):
            rejected["yes_notional_conservation"] += 1
            continue

        group_id = f"{condition_id}|{tx_hash}"
        groups.append(
            {
                "group_id": group_id,
                "condition_id": condition_id,
                "tx_hash": tx_hash,
                "block_timestamp_s": int(a["timestamp"]),
                "active_token_id": str(a["token_id"]),
                "active_price": float(a["price"]),
                "active_size": active_size,
                "active_outcome": str(a["outcome_side_norm"]),
                "passive_row_count": int(len(passive)),
                "passive_size_sum": passive_size,
                "active_yes_notional": active_yes_notional,
                "passive_yes_notional": passive_yes_notional,
            }
        )
        for _, p in passive.iterrows():
            passive_rows.append(
                {
                    "group_id": group_id,
                    "condition_id": condition_id,
                    "tx_hash": tx_hash,
                    "block_timestamp_s": int(p["timestamp"]),
                    "log_index": int(p["log_index"]),
                    "token_id": str(p["token_id"]),
                    "price": float(p["price"]),
                    "size": float(p["size_shares"]),
                    "outcome": str(p["outcome_side_norm"]),
                }
            )
    group_df = pd.DataFrame(groups)
    passive_df = pd.DataFrame(passive_rows)
    return group_df, passive_df, {
        "participant_rows": int(len(df)),
        "transaction_condition_groups": int(grouped.ngroups),
        "accepted_groups": int(len(group_df)),
        "accepted_passive_rows": int(len(passive_df)),
        "rejected_groups": int(grouped.ngroups - len(group_df)),
        "failure_counts": dict(rejected),
    }


def scan_venue_trades(
    root: Path,
    accepted_hashes: set[str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    files = sorted((root / ORDERBOOK_WINDOW).rglob("*.parquet"))
    if len(files) != 96:
        raise RuntimeError(
            f"expected 96 EV18 files, found {len(files)}"
        )
    kept: list[dict[str, Any]] = []
    counts: defaultdict[str, int] = defaultdict(int)
    columns = [
        "event_type",
        "timestamp_received",
        "sequence",
        "asset_id",
        "price",
        "size",
        "side",
        "transaction_hash",
    ]
    for file_number, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [
            column
            for column in columns
            if column in pf.schema_arrow.names
        ]
        if "sequence" not in available:
            raise RuntimeError(
                f"EV18 V3 sequence missing in {path}"
            )
        for batch in pf.iter_batches(
            batch_size=500_000,
            columns=available,
        ):
            frame = batch.to_pandas()
            counts["raw_rows_scanned"] += len(frame)
            if frame.empty:
                continue
            frame = frame[
                frame["event_type"].astype(str)
                == "last_trade_price"
            ]
            counts["venue_trade_rows"] += len(frame)
            if frame.empty:
                continue
            for rec in frame.to_dict(orient="records"):
                tx_hash = normalize_tx_hash(
                    rec.get("transaction_hash")
                )
                if not tx_hash:
                    counts["venue_trade_rows_missing_hash"] += 1
                    continue
                counts["venue_trade_rows_with_hash"] += 1
                if tx_hash not in accepted_hashes:
                    continue
                try:
                    price = float(rec["price"])
                    size = float(rec["size"])
                except (TypeError, ValueError):
                    counts["accepted_hash_trade_invalid_economics"] += 1
                    continue
                if not (
                    math.isfinite(price)
                    and math.isfinite(size)
                    and 0.0 <= price <= 1.0
                    and size > 0.0
                ):
                    counts["accepted_hash_trade_invalid_economics"] += 1
                    continue
                kept.append(
                    {
                        "tx_hash": tx_hash,
                        "token_id": bytes_to_token(
                            rec["asset_id"]
                        ),
                        "trade_ts_ns": int(
                            pd.Timestamp(
                                rec["timestamp_received"],
                                tz="UTC",
                            ).value
                            if pd.Timestamp(
                                rec["timestamp_received"]
                            ).tzinfo is None
                            else pd.Timestamp(
                                rec["timestamp_received"]
                            ).tz_convert("UTC").value
                        ),
                        "sequence": int(rec["sequence"]),
                        "price": price,
                        "size": size,
                        "side": str(
                            rec.get("side") or ""
                        ).upper(),
                    }
                )
                counts["accepted_hash_trade_rows"] += 1
        print(
            json.dumps(
                {
                    "file": file_number,
                    "of": len(files),
                    "trade_rows": counts["venue_trade_rows"],
                    "accepted_hash_rows": (
                        counts["accepted_hash_trade_rows"]
                    ),
                }
            ),
            flush=True,
        )
    trades = pd.DataFrame(kept)
    if not trades.empty:
        trades.sort_values(
            ["trade_ts_ns", "sequence", "tx_hash", "token_id"],
            kind="stable",
            inplace=True,
            ignore_index=True,
        )
    return trades, dict(counts)


def audit_entities(
    entities: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    entity_type: str,
    token_column: str,
    price_column: str,
    size_column: str,
) -> pd.DataFrame:
    by_hash_token: defaultdict[
        tuple[str, str],
        list[int],
    ] = defaultdict(list)
    for index, row in trades.iterrows():
        by_hash_token[
            (str(row["tx_hash"]), str(row["token_id"]))
        ].append(int(index))

    records: list[dict[str, Any]] = []
    for _, row in entities.iterrows():
        tx_hash = str(row["tx_hash"])
        token_id = str(row[token_column])
        price = float(row[price_column])
        size = float(row[size_column])
        candidates = by_hash_token.get(
            (tx_hash, token_id),
            [],
        )
        exact = [
            i
            for i in candidates
            if round(float(trades.at[i, "price"]), 6)
            == round(price, 6)
            and round(float(trades.at[i, "size"]), 6)
            == round(size, 6)
        ]
        price_matches = [
            i
            for i in candidates
            if round(float(trades.at[i, "price"]), 6)
            == round(price, 6)
        ]
        block_ns = int(row["block_timestamp_s"]) * 1_000_000_000
        lags = [
            (
                int(trades.at[i, "trade_ts_ns"])
                - block_ns
            )
            / 1e9
            for i in candidates
        ]
        candidate_sizes = [
            float(trades.at[i, "size"])
            for i in candidates
        ]
        price_sizes = [
            float(trades.at[i, "size"])
            for i in price_matches
        ]
        status = "NO_HASH_TOKEN_PRINT"
        if len(exact) == 1:
            status = "EXACT_SIGNATURE_UNIQUE"
        elif len(exact) > 1:
            status = "EXACT_SIGNATURE_AMBIGUOUS"
        elif candidates:
            status = "HASH_TOKEN_ONLY"
        records.append(
            {
                "entity_type": entity_type,
                "group_id": str(row["group_id"]),
                "tx_hash": tx_hash,
                "token_id": token_id,
                "fill_price": price,
                "fill_size": size,
                "hash_token_prints": int(len(candidates)),
                "exact_signature_prints": int(len(exact)),
                "same_price_prints": int(len(price_matches)),
                "candidate_size_sum": (
                    float(sum(candidate_sizes))
                    if candidate_sizes
                    else np.nan
                ),
                "same_price_size_sum": (
                    float(sum(price_sizes))
                    if price_sizes
                    else np.nan
                ),
                "min_trade_minus_block_s": (
                    float(min(lags))
                    if lags
                    else np.nan
                ),
                "max_trade_minus_block_s": (
                    float(max(lags))
                    if lags
                    else np.nan
                ),
                "status": status,
            }
        )
    return pd.DataFrame(records)


def audit_episodes(
    groups: pd.DataFrame,
    passive: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    trades_by_hash: defaultdict[str, list[int]] = defaultdict(list)
    for index, row in trades.iterrows():
        trades_by_hash[str(row["tx_hash"])].append(int(index))
    passive_by_group = {
        group_id: rows
        for group_id, rows in passive.groupby(
            "group_id",
            sort=False,
        )
    }
    records: list[dict[str, Any]] = []
    for _, group in groups.iterrows():
        group_id = str(group["group_id"])
        tx_hash = str(group["tx_hash"])
        trade_indices = trades_by_hash.get(tx_hash, [])
        trade_tokens = {
            str(trades.at[i, "token_id"])
            for i in trade_indices
        }
        p = passive_by_group.get(group_id)
        passive_tokens = (
            set(p["token_id"].astype(str))
            if p is not None
            else set()
        )
        block_ns = (
            int(group["block_timestamp_s"])
            * 1_000_000_000
        )
        lags = [
            (
                int(trades.at[i, "trade_ts_ns"])
                - block_ns
            )
            / 1e9
            for i in trade_indices
        ]
        records.append(
            {
                "group_id": group_id,
                "tx_hash": tx_hash,
                "venue_prints_same_tx": int(
                    len(trade_indices)
                ),
                "venue_tokens_same_tx": int(
                    len(trade_tokens)
                ),
                "active_token_seen": str(
                    group["active_token_id"]
                )
                in trade_tokens,
                "passive_tokens_total": int(
                    len(passive_tokens)
                ),
                "passive_tokens_seen": int(
                    len(passive_tokens & trade_tokens)
                ),
                "all_passive_tokens_seen": (
                    bool(passive_tokens)
                    and passive_tokens.issubset(trade_tokens)
                ),
                "min_trade_minus_block_s": (
                    float(min(lags))
                    if lags
                    else np.nan
                ),
                "max_trade_minus_block_s": (
                    float(max(lags))
                    if lags
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(records)


def status_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in frame["status"].value_counts().items()
    }


def finite_quantiles(
    values: pd.Series,
) -> dict[str, float]:
    clean = pd.to_numeric(
        values,
        errors="coerce",
    ).dropna()
    if clean.empty:
        return {}
    return {
        str(q): float(clean.quantile(q))
        for q in [0.0, 0.01, 0.05, 0.5, 0.95, 0.99, 1.0]
    }


def main() -> None:
    fills = load_fills(locate_fill_files())
    groups, passive, group_audit = accepted_entities(fills)
    accepted_hashes = set(groups["tx_hash"].astype(str))
    trades, scan_counts = scan_venue_trades(
        locate_orderbook_root(),
        accepted_hashes,
    )

    active_audit = audit_entities(
        groups,
        trades,
        entity_type="ACTIVE_GROUP",
        token_column="active_token_id",
        price_column="active_price",
        size_column="active_size",
    )
    passive_audit = audit_entities(
        passive,
        trades,
        entity_type="PASSIVE_FILL",
        token_column="token_id",
        price_column="price",
        size_column="size",
    )
    episodes = audit_episodes(groups, passive, trades)

    trades.to_parquet(
        WORK / "W18_LINKED_VENUE_TRADES.parquet",
        index=False,
    )
    active_audit.to_parquet(
        WORK / "W18_ACTIVE_HASH_AUDIT.parquet",
        index=False,
    )
    passive_audit.to_parquet(
        WORK / "W18_PASSIVE_HASH_AUDIT.parquet",
        index=False,
    )
    episodes.to_parquet(
        WORK / "W18_EPISODE_HASH_AUDIT.parquet",
        index=False,
    )

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "W18_HASH_IDENTITY_AUDIT",
        "scientific_role": (
            "identity and unit-of-analysis audit only; "
            "no book-state outcome is tested"
        ),
        "group_audit": group_audit,
        "venue_scan": scan_counts,
        "accepted_tx_hashes": int(len(accepted_hashes)),
        "accepted_tx_hashes_with_venue_print": int(
            episodes["venue_prints_same_tx"].gt(0).sum()
        ),
        "active_status_counts": status_counts(active_audit),
        "passive_status_counts": status_counts(passive_audit),
        "episode_counts": {
            "groups": int(len(episodes)),
            "groups_with_venue_print": int(
                episodes["venue_prints_same_tx"].gt(0).sum()
            ),
            "groups_active_token_seen": int(
                episodes["active_token_seen"].sum()
            ),
            "groups_all_passive_tokens_seen": int(
                episodes["all_passive_tokens_seen"].sum()
            ),
        },
        "episode_min_lag_s_quantiles": finite_quantiles(
            episodes["min_trade_minus_block_s"]
        ),
        "episode_max_lag_s_quantiles": finite_quantiles(
            episodes["max_trade_minus_block_s"]
        ),
        "real_sig_orders_sent": False,
    }
    (WORK / "W18_HASH_AUDIT.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    report = [
        "# EXPERIMENT-005H — W18 Hash Identity Audit",
        "",
        (
            "This audit determines the canonical observed-trade unit "
            "before any fill × book outcome analysis."
        ),
        "",
        f"- Accepted DATA-003 groups: {group_audit['accepted_groups']}",
        f"- Accepted passive rows: {group_audit['accepted_passive_rows']}",
        f"- Accepted tx hashes: {len(accepted_hashes)}",
        (
            "- Accepted tx hashes observed in venue trade stream: "
            f"{summary['accepted_tx_hashes_with_venue_print']}"
        ),
        f"- Active statuses: {summary['active_status_counts']}",
        f"- Passive statuses: {summary['passive_status_counts']}",
        "",
        (
            "Price/size/time similarity is never promoted to identity. "
            "Exact transaction identity controls this audit."
        ),
        "",
        "REAL SIG ORDERS SENT: NO",
    ]
    (WORK / "W18_HASH_AUDIT.md").write_text(
        "\n".join(report) + "\n"
    )
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
