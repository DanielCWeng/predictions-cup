# ruff: noqa
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

WORK = Path("/kaggle/working")
ORDERBOOK_SLUG = "sig-cup-data003-orderbooks"
FILL_SLUG = "sig-cup-data-003-sig-actual-fills"
SIZE_TOLERANCE = 1e-8
YES_NOTIONAL_TOLERANCE = 1e-3
TARGET_WINDOW_RE = re.compile(r"^W(?:0[6-9]|1[0-6])(?:_PART)?$")


def normalize_tx_hash(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value).strip().lower()
    if not text or text in {"none", "nan", "<na>"}:
        return ""
    return text if text.startswith("0x") else "0x" + text


def bytes_to_token(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), "big", signed=False))
    return str(value)


def dataset_root(slug: str) -> Path:
    roots = [
        path
        for path in Path("/kaggle/input").rglob(slug)
        if path.is_dir()
    ]
    if len(roots) != 1:
        raise RuntimeError(f"expected one {slug} root, found {roots}")
    return roots[0]


def fill_files_by_window(root: Path) -> dict[str, list[Path]]:
    output: defaultdict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*.parquet"):
        if "fills" not in path.parts:
            continue
        if any(
            marker in path.parts
            for marker in ("fees", "rebates", "unattributed_fee_legs")
        ):
            continue
        window = None
        for part in path.parts:
            if part.startswith("window_id="):
                window = part.split("=", 1)[1]
                break
        if window and TARGET_WINDOW_RE.match(window):
            output[window].append(path)
    return {
        window: sorted(paths)
        for window, paths in sorted(output.items())
    }


def accepted_active_entities(
    files: list[Path],
    window: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames = [
        pq.ParquetFile(path).read().to_pandas()
        for path in files
    ]
    frame = pd.concat(frames, ignore_index=True)
    required = {
        "tx_hash",
        "condition_id",
        "token_id",
        "outcome_side",
        "price",
        "size_shares",
        "order_is_match_taker_order",
    }
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(
            f"{window} fill schema missing {sorted(missing)}"
        )
    frame["tx_hash"] = frame["tx_hash"].map(normalize_tx_hash)
    frame["condition_id"] = frame["condition_id"].astype(str)
    frame["token_id"] = frame["token_id"].astype(str)
    frame["price"] = pd.to_numeric(
        frame["price"],
        errors="raise",
    ).astype(float)
    frame["size_shares"] = pd.to_numeric(
        frame["size_shares"],
        errors="raise",
    ).astype(float)
    frame["is_active"] = (
        frame["order_is_match_taker_order"].astype(bool)
    )
    frame["outcome"] = (
        frame["outcome_side"].astype(str).str.upper()
    )

    rows: list[dict[str, Any]] = []
    rejected: defaultdict[str, int] = defaultdict(int)
    grouped = frame.groupby(
        ["condition_id", "tx_hash"],
        sort=False,
    )
    for (condition_id, tx_hash), group in grouped:
        active = group[group["is_active"]]
        passive = group[~group["is_active"]]
        if len(active) != 1:
            rejected["active_count_not_one"] += 1
            continue
        if passive.empty:
            rejected["no_passive"] += 1
            continue
        if not group["outcome"].isin(["YES", "NO"]).all():
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
            if a["outcome"] == "YES"
            else 1.0 - float(a["price"])
        )
        active_yes = active_p_yes * active_size
        passive_yes = 0.0
        for passive_row in passive.itertuples(index=False):
            p_yes = (
                float(passive_row.price)
                if str(passive_row.outcome) == "YES"
                else 1.0 - float(passive_row.price)
            )
            passive_yes += p_yes * float(passive_row.size_shares)
        yes_limit = YES_NOTIONAL_TOLERANCE * max(
            1.0,
            abs(active_yes),
            abs(passive_yes),
        )
        if abs(active_yes - passive_yes) > yes_limit:
            rejected["yes_notional_conservation"] += 1
            continue
        rows.append(
            {
                "window_id": window,
                "condition_id": str(condition_id),
                "tx_hash": str(tx_hash),
                "token_id": str(a["token_id"]),
            }
        )
    out = pd.DataFrame(rows)
    return out, {
        "window_id": window,
        "participant_rows": int(len(frame)),
        "transaction_condition_groups": int(grouped.ngroups),
        "accepted_groups": int(len(out)),
        "failure_counts": dict(rejected),
    }


def source_directories(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and path.name != "_manifests"
        and any(path.rglob("*.parquet"))
    )


def source_audit(
    source_dir: Path,
    target_by_key: dict[tuple[str, str], str],
) -> tuple[dict[str, Any], pd.DataFrame]:
    files = sorted(source_dir.rglob("*.parquet"))
    if not files:
        raise RuntimeError(f"no parquet files in {source_dir}")

    schema_sets: defaultdict[str, int] = defaultdict(int)
    event_counts: defaultdict[str, int] = defaultdict(int)
    source_versions: defaultdict[str, int] = defaultdict(int)
    linked_keys: defaultdict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)
    rows_scanned = 0
    receive_min: pd.Timestamp | None = None
    receive_max: pd.Timestamp | None = None
    ordering_columns: set[str] = set()
    transaction_hash_present = False
    asset_id_present = False

    preferred = [
        "event_type",
        "update_type",
        "timestamp_received",
        "observed_at",
        "timestamp",
        "sequence",
        "asset_id",
        "token_id",
        "transaction_hash",
        "tx_hash",
        "source_version",
        "price",
        "size",
    ]
    for number, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        names = list(pf.schema_arrow.names)
        schema_sets["|".join(names)] += 1
        for name in (
            "timestamp_received",
            "observed_at",
            "timestamp",
            "sequence",
        ):
            if name in names:
                ordering_columns.add(name)
        transaction_hash_present |= (
            "transaction_hash" in names
            or "tx_hash" in names
        )
        asset_id_present |= (
            "asset_id" in names
            or "token_id" in names
        )
        available = [
            name
            for name in preferred
            if name in names
        ]
        if not available:
            continue
        for batch in pf.iter_batches(
            batch_size=500_000,
            columns=available,
        ):
            frame = batch.to_pandas()
            rows_scanned += len(frame)
            if frame.empty:
                continue
            event_col = (
                "event_type"
                if "event_type" in frame.columns
                else "update_type"
                if "update_type" in frame.columns
                else None
            )
            if event_col is not None:
                counts = (
                    frame[event_col]
                    .astype(str)
                    .value_counts(dropna=False)
                )
                for key, value in counts.items():
                    event_counts[str(key)] += int(value)

            if "source_version" in frame.columns:
                counts = (
                    frame["source_version"]
                    .astype(str)
                    .value_counts(dropna=False)
                )
                for key, value in counts.items():
                    source_versions[str(key)] += int(value)

            receive_col = next(
                (
                    name
                    for name in (
                        "timestamp_received",
                        "observed_at",
                        "timestamp",
                    )
                    if name in frame.columns
                ),
                None,
            )
            if receive_col is not None:
                converted = pd.to_datetime(
                    frame[receive_col],
                    utc=True,
                    errors="coerce",
                ).dropna()
                if not converted.empty:
                    current_min = converted.min()
                    current_max = converted.max()
                    receive_min = (
                        current_min
                        if receive_min is None
                        else min(receive_min, current_min)
                    )
                    receive_max = (
                        current_max
                        if receive_max is None
                        else max(receive_max, current_max)
                    )

            if event_col is None:
                continue
            trade = frame[
                frame[event_col].astype(str)
                == "last_trade_price"
            ].copy()
            if trade.empty:
                continue
            hash_col = (
                "transaction_hash"
                if "transaction_hash" in trade.columns
                else "tx_hash"
                if "tx_hash" in trade.columns
                else None
            )
            token_col = (
                "asset_id"
                if "asset_id" in trade.columns
                else "token_id"
                if "token_id" in trade.columns
                else None
            )
            if hash_col is None or token_col is None:
                continue
            for record in trade.to_dict(orient="records"):
                tx_hash = normalize_tx_hash(
                    record.get(hash_col)
                )
                token_id = bytes_to_token(
                    record.get(token_col)
                )
                key = (tx_hash, token_id)
                if key not in target_by_key:
                    continue
                linked_keys[key].append(
                    {
                        "window_id": target_by_key[key],
                        "source_directory": source_dir.name,
                        "has_sequence": "sequence" in frame.columns,
                        "receive_column": receive_col,
                    }
                )
        if number % 100 == 0 or number == len(files):
            print(
                json.dumps(
                    {
                        "source": source_dir.name,
                        "file": number,
                        "of": len(files),
                        "linked_keys": len(linked_keys),
                    }
                ),
                flush=True,
            )

    coverage: list[dict[str, Any]] = []
    by_window_total: defaultdict[str, int] = defaultdict(int)
    for window in target_by_key.values():
        by_window_total[window] += 1
    by_window_linked: defaultdict[str, int] = defaultdict(int)
    by_window_ambiguous: defaultdict[str, int] = defaultdict(int)
    for key, matches in linked_keys.items():
        window = target_by_key[key]
        if len(matches) == 1:
            by_window_linked[window] += 1
        else:
            by_window_ambiguous[window] += 1
    for window in sorted(by_window_total):
        coverage.append(
            {
                "source_directory": source_dir.name,
                "window_id": window,
                "accepted_groups": int(
                    by_window_total[window]
                ),
                "unique_hash_token_links": int(
                    by_window_linked[window]
                ),
                "ambiguous_hash_token_links": int(
                    by_window_ambiguous[window]
                ),
                "link_fraction": (
                    by_window_linked[window]
                    / by_window_total[window]
                    if by_window_total[window]
                    else math.nan
                ),
            }
        )

    audit = {
        "source_directory": source_dir.name,
        "parquet_files": int(len(files)),
        "rows_scanned": int(rows_scanned),
        "ordering_columns": sorted(ordering_columns),
        "sequence_available": "sequence" in ordering_columns,
        "transaction_hash_present": bool(
            transaction_hash_present
        ),
        "token_identity_present": bool(asset_id_present),
        "event_type_counts": dict(event_counts),
        "source_version_counts": dict(source_versions),
        "receive_min_utc": (
            receive_min.isoformat()
            if receive_min is not None
            else None
        ),
        "receive_max_utc": (
            receive_max.isoformat()
            if receive_max is not None
            else None
        ),
        "schema_signatures": [
            {
                "columns": key.split("|"),
                "files": int(value),
            }
            for key, value in schema_sets.items()
        ],
        "linked_target_keys": int(len(linked_keys)),
    }
    return audit, pd.DataFrame(coverage)


def main() -> None:
    fill_root = dataset_root(FILL_SLUG)
    orderbook_root = dataset_root(ORDERBOOK_SLUG)
    by_window = fill_files_by_window(fill_root)
    if not by_window:
        raise RuntimeError("no W06-W16 DATA-003 windows found")

    entities: list[pd.DataFrame] = []
    fill_audits: list[dict[str, Any]] = []
    for window, files in by_window.items():
        frame, audit = accepted_active_entities(
            files,
            window,
        )
        entities.append(frame)
        fill_audits.append(audit)
    accepted = pd.concat(
        entities,
        ignore_index=True,
        sort=False,
    )
    duplicate_identity = int(
        accepted.duplicated(
            ["tx_hash", "token_id"],
            keep=False,
        ).sum()
    )
    if duplicate_identity:
        raise RuntimeError(
            "accepted active hash+token identity reused across "
            f"W06-W16 rows={duplicate_identity}"
        )
    target_by_key = {
        (str(row.tx_hash), str(row.token_id)): str(row.window_id)
        for row in accepted.itertuples(index=False)
    }

    source_rows: list[dict[str, Any]] = []
    coverage_frames: list[pd.DataFrame] = []
    for source_dir in source_directories(orderbook_root):
        audit, coverage = source_audit(
            source_dir,
            target_by_key,
        )
        source_rows.append(audit)
        coverage_frames.append(coverage)

    coverage = pd.concat(
        coverage_frames,
        ignore_index=True,
        sort=False,
    )
    coverage.to_parquet(
        WORK / "SOURCE_LINK_COVERAGE.parquet",
        index=False,
    )

    useful = coverage[
        coverage["unique_hash_token_links"] > 0
    ].copy()
    source_by_name = {
        row["source_directory"]: row
        for row in source_rows
    }
    decisions: list[dict[str, Any]] = []
    for window, group in useful.groupby("window_id"):
        ranked = group.sort_values(
            [
                "unique_hash_token_links",
                "link_fraction",
            ],
            ascending=False,
        )
        best = ranked.iloc[0]
        source = source_by_name[
            str(best["source_directory"])
        ]
        sequence = bool(source["sequence_available"])
        decisions.append(
            {
                "window_id": str(window),
                "source_directory": str(
                    best["source_directory"]
                ),
                "unique_hash_token_links": int(
                    best["unique_hash_token_links"]
                ),
                "accepted_groups": int(
                    best["accepted_groups"]
                ),
                "link_fraction": float(
                    best["link_fraction"]
                ),
                "sequence_available": sequence,
                "chronology_rule": (
                    "timestamp_received + sequence"
                    if sequence
                    else (
                        "strictly earlier recorder receive time; "
                        "equal receive time ambiguous"
                    )
                ),
                "sequential_eligibility": (
                    "ELIGIBLE"
                    if float(best["link_fraction"]) >= 0.90
                    else "LIMITED"
                ),
            }
        )

    report = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "PRE_V3_SOURCE_VERSION_LINKAGE_AUDIT",
        "scientific_scope": (
            "identity/chronology capability only; no price "
            "response or predictive outcome inspected"
        ),
        "fill_windows": fill_audits,
        "accepted_active_entities": int(len(accepted)),
        "source_directories": source_rows,
        "best_window_source_decisions": decisions,
        "b0_opened": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "SOURCE_VERSION_AUDIT.json").write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    lines = [
        "# EXPERIMENT-005H — Source-Version Linkage Audit",
        "",
        (
            "This audit inspects identity and chronology only. "
            "It does not inspect post-fill outcomes."
        ),
        "",
    ]
    for row in decisions:
        lines.append(
            "- "
            + row["window_id"]
            + " -> "
            + row["source_directory"]
            + ": "
            + str(row["unique_hash_token_links"])
            + "/"
            + str(row["accepted_groups"])
            + " unique hash+token links; "
            + row["chronology_rule"]
        )
    lines.extend(
        [
            "",
            "B0 opened: NO",
            "",
            "REAL SIG ORDERS SENT: NO",
        ]
    )
    (WORK / "SOURCE_VERSION_AUDIT.md").write_text(
        "\n".join(lines) + "\n"
    )
    print(
        json.dumps(
            {
                "accepted_active_entities": len(accepted),
                "windows": sorted(by_window),
                "decisions": decisions,
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
