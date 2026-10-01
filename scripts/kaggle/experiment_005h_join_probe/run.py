from __future__ import annotations

import bisect
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
WINDOW = "ev18_ok_sc_runoff_ga_runoff"
FILL_WINDOW = "W18"
MATCH_TOLERANCE_S = 120.0

EXPECTED_BLOCK_GATE = {
    "economic_fills_sha256": "4528926586ef41cdf785016fff657dbb3034bdc41973a307b99561dd0d9ce265",
    "tx_block_sha256": "e1af2075db53a547135bad8e61db6e2e2fe37eb830e6f40aab6f1e7a56246bbc",
    "block_timestamp_sha256": "42231574b442ab30721a6271311d914a08ec4aa80059e131690d2610e321853b",
}


def bytes_to_token(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return str(int.from_bytes(bytes(value), "big", signed=False))
    return str(value)


def best_from_levels(levels: Any, *, bid: bool) -> float | None:
    if levels is None:
        return None
    values: list[float] = []
    try:
        for level in levels:
            if isinstance(level, dict):
                p = float(level["price"])
            else:
                p = float(level[0])
            if math.isfinite(p):
                values.append(p)
    except (TypeError, ValueError, KeyError):
        return None
    if not values:
        return None
    return max(values) if bid else min(values)


def locate_orderbook_root() -> Path:
    roots = [
        p for p in Path("/kaggle/input").rglob(ORDERBOOK_SLUG)
        if p.is_dir() and (p / "_manifests").is_dir()
    ]
    if len(roots) != 1:
        raise RuntimeError(f"expected one orderbook root, found {roots}")
    return roots[0]


def locate_fill_files() -> list[Path]:
    files = sorted(
        p for p in Path("/kaggle/input").rglob("*.parquet")
        if "fills" in p.parts
        and f"window_id={FILL_WINDOW}" in p.parts
        and "fees" not in p.parts
        and "rebates" not in p.parts
        and "unattributed_fee_legs" not in p.parts
    )
    if not files:
        raise RuntimeError("no DATA-003 W18 fill parquet files found")
    return files


def load_fills(files: list[Path]) -> pd.DataFrame:
    frames = [pq.read_table(p).to_pandas() for p in files]
    df = pd.concat(frames, ignore_index=True)
    required = {
        "timestamp", "tx_hash", "log_index", "condition_id", "token_id",
        "price", "size_shares", "order_is_match_taker_order",
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"fill schema missing {sorted(missing)}")
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64")
    df["log_index"] = pd.to_numeric(df["log_index"], errors="raise").astype("int64")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["size_shares"] = pd.to_numeric(df["size_shares"], errors="coerce")
    df["token_id"] = df["token_id"].astype(str)
    df["tx_hash"] = df["tx_hash"].astype(str).str.lower()
    df["condition_id"] = df["condition_id"].astype(str)
    df["is_active"] = df["order_is_match_taker_order"].astype(bool)
    if "side" in df.columns:
        df["side_norm"] = df["side"].astype(str).str.upper()
    else:
        df["side_norm"] = ""
    if "outcome_side" in df.columns:
        df["outcome_side_norm"] = df["outcome_side"].astype(str).str.upper()
    else:
        df["outcome_side_norm"] = ""
    return df


def accepted_groups(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rejected: defaultdict[str, int] = defaultdict(int)
    for (condition_id, tx_hash), g in df.groupby(["condition_id", "tx_hash"], sort=False):
        active = g[g["is_active"]]
        passive = g[~g["is_active"]]
        if len(active) != 1:
            rejected["active_count_not_one"] += 1
            continue
        if passive.empty:
            rejected["no_passive"] += 1
            continue
        a = active.iloc[0]
        active_size = float(a["size_shares"])
        passive_size = float(passive["size_shares"].sum())
        if not np.isclose(active_size, passive_size, rtol=1e-8, atol=1e-8):
            rejected["size_conservation"] += 1
            continue
        rec = a.to_dict()
        rec["group_id"] = f"{condition_id}|{tx_hash}"
        rec["passive_rows"] = int(len(passive))
        rec["passive_size_sum"] = passive_size
        rows.append(rec)
    out = pd.DataFrame(rows)
    return out, {
        "participant_rows": int(len(df)),
        "transaction_condition_groups": int(df.groupby(["condition_id", "tx_hash"]).ngroups),
        "accepted_groups": int(len(out)),
        "rejected": dict(rejected),
    }


def load_ev18(
    root: Path,
    tokens: set[str],
) -> tuple[
    pd.DataFrame,
    dict[str, list[tuple[int, int, float, float]]],
    dict[str, Any],
]:
    files = sorted((root / WINDOW).rglob("*.parquet"))
    if len(files) != 96:
        raise RuntimeError(f"expected 96 EV18 files, found {len(files)}")
    token_bytes = {int(t).to_bytes(32, "big") for t in tokens}
    trades: list[dict[str, Any]] = []
    bbo: defaultdict[str, list[tuple[int, int, float, float]]] = defaultdict(list)
    counts: defaultdict[str, int] = defaultdict(int)
    wanted = [
        "event_type", "timestamp_received", "sequence", "asset_id",
        "best_bid", "best_ask", "bids", "asks", "price", "size", "side",
    ]
    for idx, path in enumerate(files):
        pf = pq.ParquetFile(path)
        available = [c for c in wanted if c in pf.schema_arrow.names]
        if "sequence" not in available or "timestamp_received" not in available:
            raise RuntimeError(f"EV18 V3 ordering columns absent in {path}")
        for batch in pf.iter_batches(batch_size=250_000, columns=available):
            frame = batch.to_pandas()
            counts["raw_rows"] += len(frame)
            if frame.empty:
                continue
            frame = frame[frame["asset_id"].isin(token_bytes)]
            counts["mapped_rows"] += len(frame)
            if frame.empty:
                continue
            frame["ts_ns"] = pd.to_datetime(frame["timestamp_received"], utc=True).astype("int64")
            frame["sequence"] = pd.to_numeric(frame["sequence"], errors="raise").astype("uint64")
            for rec in frame.to_dict(orient="records"):
                kind = str(rec["event_type"])
                token = bytes_to_token(rec["asset_id"])
                if kind == "last_trade_price":
                    try:
                        price = float(rec["price"])
                        size = float(rec["size"])
                    except (TypeError, ValueError):
                        continue
                    if not (math.isfinite(price) and math.isfinite(size) and size > 0):
                        continue
                    trades.append({
                        "token_id": token,
                        "ts_ns": int(rec["ts_ns"]),
                        "sequence": int(rec["sequence"]),
                        "price": price,
                        "size": size,
                        "side": str(rec.get("side") or "").upper(),
                    })
                    counts["trade_rows"] += 1
                elif kind in {"price_change", "book"}:
                    if kind == "price_change":
                        try:
                            bb = float(rec["best_bid"])
                            ba = float(rec["best_ask"])
                        except (TypeError, ValueError):
                            continue
                    else:
                        bb = best_from_levels(rec.get("bids"), bid=True)
                        ba = best_from_levels(rec.get("asks"), bid=False)
                        if bb is None or ba is None:
                            continue
                    if 0.0 < bb <= ba < 1.0:
                        bbo[token].append((int(rec["ts_ns"]), int(rec["sequence"]), bb, ba))
                        counts["bbo_rows"] += 1
        progress = {
            "file": idx + 1,
            "of": len(files),
            "trades": counts["trade_rows"],
            "bbo": counts["bbo_rows"],
        }
        print(json.dumps(progress), flush=True)
    tdf = pd.DataFrame(trades)
    for token in bbo:
        bbo[token].sort(key=lambda x: (x[0], x[1]))
    return tdf, dict(bbo), dict(counts)


def rounded_key(token: str, price: float, size: float, side: str | None = None) -> tuple[Any, ...]:
    base: tuple[Any, ...] = (token, round(float(price), 6), round(float(size), 6))
    return base if side is None else base + (str(side or "").upper(),)


def build_index(
    trades: pd.DataFrame,
    with_side: bool,
) -> dict[tuple[Any, ...], list[tuple[int, int, int]]]:
    index: defaultdict[tuple[Any, ...], list[tuple[int, int, int]]] = defaultdict(list)
    for i, r in trades.iterrows():
        key = rounded_key(r["token_id"], r["price"], r["size"], r["side"] if with_side else None)
        index[key].append((int(r["ts_ns"]), int(r["sequence"]), int(i)))
    for key in index:
        index[key].sort()
    return dict(index)


def candidate_rows(
    series: list[tuple[int, int, int]],
    center_ns: int,
    tolerance_ns: int,
) -> list[tuple[int, int, int]]:
    times = [x[0] for x in series]
    lo = bisect.bisect_left(times, center_ns - tolerance_ns)
    hi = bisect.bisect_right(times, center_ns + tolerance_ns)
    return series[lo:hi]


def match_groups(
    groups: pd.DataFrame,
    trades: pd.DataFrame,
    bbo: dict[str, list[tuple[int, int, float, float]]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    idx_plain = build_index(trades, with_side=False)
    idx_side = build_index(trades, with_side=True)
    tol_ns = int(MATCH_TOLERANCE_S * 1e9)
    out: list[dict[str, Any]] = []
    for _, r in groups.iterrows():
        token = str(r["token_id"])
        block_ns = int(r["timestamp"]) * 1_000_000_000
        plain_key = rounded_key(token, r["price"], r["size_shares"])
        plain = candidate_rows(
            idx_plain.get(plain_key, []),
            block_ns,
            tol_ns,
        )
        side = str(r.get("side_norm") or "")
        sided: list[tuple[int, int, int]] = []
        if side:
            side_key = rounded_key(
                token,
                r["price"],
                r["size_shares"],
                side,
            )
            sided = candidate_rows(
                idx_side.get(side_key, []),
                block_ns,
                tol_ns,
            )
        chosen = sided if len(sided) == 1 else plain
        status = "UNMATCHED"
        match = None
        if len(chosen) == 1:
            status = "UNIQUE"
            match = chosen[0]
        elif len(chosen) > 1:
            distances = [abs(x[0] - block_ns) for x in chosen]
            m = min(distances)
            nearest = [
                x
                for x, d in zip(chosen, distances, strict=True)
                if d == m
            ]
            if len(nearest) == 1:
                status = "NEAREST_UNIQUE_AMONG_MULTIPLE"
                match = nearest[0]
            else:
                status = "AMBIGUOUS"
        rec = {
            "group_id": r["group_id"],
            "token_id": token,
            "block_timestamp_s": int(r["timestamp"]),
            "fill_price": float(r["price"]),
            "fill_size": float(r["size_shares"]),
            "fill_side": side,
            "plain_candidates_120s": len(plain),
            "side_candidates_120s": len(sided),
            "match_status": status,
        }
        if match is not None:
            ts_ns, seq, trade_index = match
            rec["trade_ts_ns"] = ts_ns
            rec["trade_sequence"] = seq
            rec["trade_index"] = trade_index
            rec["trade_minus_block_s"] = (ts_ns - block_ns) / 1e9
            books = bbo.get(token, [])
            order_keys = [(x[0], x[1]) for x in books]
            j = bisect.bisect_left(order_keys, (ts_ns, seq)) - 1
            if j >= 0:
                bts, bseq, bb, ba = books[j]
                rec.update({
                    "pre_book_found": True,
                    "pre_book_ts_ns": bts,
                    "pre_book_sequence": bseq,
                    "pre_bid": bb,
                    "pre_ask": ba,
                    "pre_mid": (bb + ba) / 2.0,
                    "pre_spread": ba - bb,
                    "book_age_ms": (ts_ns - bts) / 1e6,
                })
            else:
                rec["pre_book_found"] = False
        out.append(rec)
    matches = pd.DataFrame(out)
    return matches, summarize_matches(matches)


def summarize_matches(m: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "groups": int(len(m)),
        "status_counts": {
            str(k): int(v)
            for k, v in m["match_status"].value_counts().items()
        },
        "unique_or_nearest": int(
            m["match_status"]
            .isin(["UNIQUE", "NEAREST_UNIQUE_AMONG_MULTIPLE"])
            .sum()
        ),
        "strict_unique": int((m["match_status"] == "UNIQUE").sum()),
        "ambiguous": int((m["match_status"] == "AMBIGUOUS").sum()),
        "unmatched": int((m["match_status"] == "UNMATCHED").sum()),
    }
    matched = m[m["trade_ts_ns"].notna()].copy() if "trade_ts_ns" in m.columns else pd.DataFrame()
    if not matched.empty:
        lag = matched["trade_minus_block_s"].astype(float).to_numpy()
        summary["lag_seconds_quantiles"] = {
            str(q): float(np.quantile(lag, q)) for q in [0, .01, .05, .25, .5, .75, .95, .99, 1]
        }
        summary["abs_lag_le_5s"] = int((np.abs(lag) <= 5).sum())
        summary["abs_lag_le_15s"] = int((np.abs(lag) <= 15).sum())
        summary["abs_lag_le_30s"] = int((np.abs(lag) <= 30).sum())
        pre_book = matched.get(
            "pre_book_found",
            pd.Series(False, index=matched.index),
        )
        summary["pre_book_found"] = int(pre_book.fillna(False).sum())
        ids = matched["trade_index"].astype(int)
        summary["distinct_matched_trade_events"] = int(ids.nunique())
        summary["data003_groups_per_trade_event_max"] = int(ids.value_counts().max())
        summary["trade_events_reused_by_multiple_groups"] = int((ids.value_counts() > 1).sum())
    return summary


def main() -> None:
    fill_files = locate_fill_files()
    fills = load_fills(fill_files)
    groups, group_audit = accepted_groups(fills)
    if groups.empty:
        raise RuntimeError("zero accepted W18 trade groups")
    root = locate_orderbook_root()
    trades, bbo, ob_counts = load_ev18(root, set(groups["token_id"].astype(str)))
    if trades.empty:
        raise RuntimeError("zero EV18 orderbook trade events for W18 tokens")
    matches, match_summary = match_groups(groups, trades, bbo)

    matches.to_parquet(WORK / "JOIN_PROBE_MATCHES.parquet", index=False)
    summary = {
        "experiment": "EXPERIMENT-005H",
        "stage": "JOIN_PROBE_EV18",
        "fill_source": "accepted DATA-003 W18 participant fills",
        "orderbook_source": "DATA-003-linked EV18 V3 orderbook corpus",
        "orderbook_ordering": "timestamp_received then sequence",
        "fill_time_semantics": (
            "canonical Polygon block timestamp; accepted block gate proved "
            "equality to block header timestamp"
        ),
        "accepted_block_gate_hashes": EXPECTED_BLOCK_GATE,
        "match_tolerance_seconds": MATCH_TOLERANCE_S,
        "group_audit": group_audit,
        "orderbook_counts": ob_counts,
        "match_summary": match_summary,
        "scientific_boundary": (
            "probe only; nearest match is diagnostic and is not yet accepted "
            "as a causal join rule"
        ),
        "real_sig_orders_sent": False,
    }
    (WORK / "JOIN_PROBE_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# EXPERIMENT-005H — EV18 Join Probe",
        "",
        (
            "This probe tests whether accepted DATA-003 transaction-condition "
            "trade episodes can be aligned to observed EV18 CLOB trade events "
            "before the full experiment is allowed to proceed."
        ),
        "",
        f"- DATA-003 participant rows: {group_audit['participant_rows']}",
        f"- Accepted transaction-condition groups: {group_audit['accepted_groups']}",
        f"- Observed EV18 trade events: {ob_counts.get('trade_rows', 0)}",
        f"- Strict unique signature matches: {match_summary['strict_unique']}",
        f"- Unique/nearest diagnostic matches: {match_summary['unique_or_nearest']}",
        f"- Ambiguous: {match_summary['ambiguous']}",
        f"- Unmatched: {match_summary['unmatched']}",
        "",
        (
            "Nearest-among-multiple matches are diagnostic only. The full 005H "
            "join may use only a rule justified by collision rates, lag "
            "structure, source ordering, and matched-event reuse from this probe."
        ),
        "",
        "REAL SIG ORDERS SENT: NO",
    ]
    (WORK / "JOIN_PROBE.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
