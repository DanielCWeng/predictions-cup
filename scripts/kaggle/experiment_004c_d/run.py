# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads
from sklearn.linear_model import LogisticRegression, Ridge

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/004c_d_conditional_response")
WORK.mkdir(parents=True, exist_ok=True)

FREEZE_SHA = "6adb6c2746fdba1453a5f845a515c9f56b5dab3b"
PREREG_SHA = "9eafa50476ffa3e3d793081ead48e1536c02bed5a8571ca92c981158773b2688"
REGISTRY_SHA = "de93ccddafd12729ef7c77875bf8c8bfc6a255cc44efb1bfee5d201c69100e52"
ASTRA_SHA = "95613edc6acfedd9b614143198366b1b2d62b9f10cbbcb5bd2425588760b6bff"
DATA001_SHA = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
MASTER_SEED = 20260927004
NULL_DRAWS = 9999
STEP = 30
NS = 1_000_000_000
UTC_US = pa.timestamp("us", tz="UTC")
DISCOVERY = ("hungary_election", "peru_first_round")
CHALLENGE = ("colombia_first_round", "peru_runoff", "colombia_runoff")
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")

D1_BASE = (
    "p_pre", "p_pre_var", "own_ret30", "own_abs_ret30", "own_vol300",
    "A_genuine_age", "record_age", "spread_pre", "own_count30", "own_count300",
    "E_family", "source_age", "F_family", "outside_abs_ret", "outside_F",
    "peer_count", "peer_fraction", "elapsed",
)
D2_BASE = (
    "D_genuine_age", "record_age", "own_count30", "own_count300",
    "own_mid_count300", "p_pre", "p_pre_var", "own_ret30", "own_vol300",
    "spread_pre", "F_family", "outside_F", "peer_count", "peer_fraction", "elapsed",
)
D4_BASE = D2_BASE + ("E_family",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_seed(component: str) -> int:
    digest = hashlib.sha256(f"{MASTER_SEED}|{component}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {name}, got {matches}")
    return matches[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["status"]
        rows = [{"status": "EMPTY"}]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp() * NS)


def load_code_bundle() -> tuple[Path, dict[str, Any]]:
    manifest_path = one("code_manifest.json")
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest["runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("Kaggle runner hash mismatch")
    if manifest["freeze_commit"] != FREEZE_SHA:
        raise RuntimeError("code bundle not bound to terminal preregistration freeze")
    if manifest["preregistration_sha256"] != PREREG_SHA:
        raise RuntimeError("code bundle preregistration hash mismatch")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"code bundle file hash mismatch: {name}")
    sys.path.insert(0, str(root / "predictions_cup_004c_d.bundle"))
    return root, manifest


def parquet_files(root: Path) -> list[str]:
    return [str(path) for path in sorted(root.rglob("*.parquet"))]


def table_for(
    root: Path,
    columns: list[str],
    *,
    tokens: list[str] | None,
    time_col: str,
    start: datetime,
    end: datetime,
) -> pa.Table:
    files = parquet_files(root)
    if not files:
        return pa.table({name: [] for name in columns})
    dataset = pads.dataset(files, format="parquet")
    expr = (pads.field(time_col) >= pa.scalar(start, UTC_US)) & (
        pads.field(time_col) < pa.scalar(end, UTC_US)
    )
    if tokens is not None:
        expr = expr & pads.field("token_id").isin(tokens)
    return dataset.to_table(columns=columns, filter=expr)


def verify_data001(corpus: Path, manifest: dict[str, Any], events: tuple[str, ...]) -> dict[str, Any]:
    records = {row["path"]: row for row in manifest["output_files"]}
    checked = 0
    rows = 0
    by_event: dict[str, int] = {}
    for event in events:
        count = 0
        for path in sorted((corpus / event).rglob("*.parquet")):
            rel = str(path.relative_to(corpus))
            rec = records.get(rel)
            if rec is None or sha256(path) != rec["sha256"]:
                raise RuntimeError(f"DATA-001 mismatch: {rel}")
            checked += 1
            count += 1
            rows += int(rec["rows"])
        by_event[event] = count
    return {"files_checked": checked, "rows_checked": rows, "files_by_event": by_event}


def load_reconstructions(
    corpus: Path,
    event: str,
    tokens: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    from predictions_cup.learning.conditional_response import reconstruct_genuine_bbo

    table = table_for(
        corpus / event / "books" / "book_changes",
        ["token_id", "observed_at", "best_bid", "best_ask"],
        tokens=tokens,
        time_col="observed_at",
        start=start,
        end=end,
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in table.to_pylist():
        grouped[str(row["token_id"])].append(row)
    return {
        token: reconstruct_genuine_bbo(grouped.get(token, []))
        for token in tokens
    }


def load_collector_times(
    corpus: Path,
    event: str,
    start: datetime,
    end: datetime,
) -> np.ndarray:
    table = table_for(
        corpus / event / "books" / "book_changes",
        ["observed_at"],
        tokens=None,
        time_col="observed_at",
        start=start,
        end=end,
    )
    if table.num_rows == 0:
        return np.zeros(0, np.int64)
    values = pc.cast(table["observed_at"], pa.int64()).to_numpy(zero_copy_only=False)
    return np.unique(np.asarray(values, np.int64) * 1000)


def load_depth_times(
    corpus: Path,
    event: str,
    tokens: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, np.ndarray]:
    table = table_for(
        corpus / event / "books" / "depth_snapshots",
        ["token_id", "recorded_at"],
        tokens=tokens,
        time_col="recorded_at",
        start=start,
        end=end,
    )
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in table.to_pylist():
        grouped[str(row["token_id"])].append(ns(row["recorded_at"]))
    return {
        token: np.asarray(sorted(set(grouped.get(token, []))), np.int64)
        for token in tokens
    }


def depth_count(times: np.ndarray, start_ns: int, end_ns: int) -> int:
    lo = int(np.searchsorted(times, start_ns, side="right"))
    hi = int(np.searchsorted(times, end_ns, side="right"))
    return max(0, hi - lo)


@dataclass
class TokenGrid:
    token: str
    q: np.ndarray
    p_pre: np.ndarray
    p_cur: np.ndarray
    p30: np.ndarray
    p60: np.ndarray
    p120: np.ndarray
    bid_cur: np.ndarray
    ask_cur: np.ndarray
    bid30: np.ndarray
    ask30: np.ndarray
    pre_spread: np.ndarray
    A: np.ndarray
    record_age: np.ndarray
    ret30: np.ndarray
    abs_ret30: np.ndarray
    vol300: np.ndarray
    count30: np.ndarray
    count300: np.ndarray
    mid_count300: np.ndarray
    raw30: np.ndarray
    unchanged30: np.ndarray
    depth30: np.ndarray
    exposure_source: np.ndarray
    exposure30: np.ndarray
    exposure60: np.ndarray
    exposure120: np.ndarray
    renew30: np.ndarray
    first_update_latency: np.ndarray


def build_token_grid(series: Any, depth_times: np.ndarray, collector: np.ndarray, q: np.ndarray) -> TokenGrid:
    from predictions_cup.learning.conditional_response import (
        any_genuine_change,
        asof_state,
        count_events,
        observation_exposure_valid,
    )

    n = len(q)
    def nan() -> np.ndarray:
        return np.full(n, np.nan, float)

    p_pre, p_cur, p30, p60, p120 = nan(), nan(), nan(), nan(), nan()
    bid_cur, ask_cur, bid30, ask30, spread = nan(), nan(), nan(), nan(), nan()
    age, record_age, ret, abs_ret, vol = nan(), nan(), nan(), nan(), nan()
    c30, c300, mc300, raw30, unchanged30, dep30 = (nan() for _ in range(6))
    exp_src = np.zeros(n, bool)
    exp30 = np.zeros(n, bool)
    exp60 = np.zeros(n, bool)
    exp120 = np.zeros(n, bool)
    renew30 = nan()
    latency = nan()

    for k, t in enumerate(q):
        pre = int(t - STEP * NS)
        s_pre = asof_state(series, pre)
        s_cur = asof_state(series, int(t))
        s30 = asof_state(series, int(t + 30 * NS))
        s60 = asof_state(series, int(t + 60 * NS))
        s120 = asof_state(series, int(t + 120 * NS))
        exp_src[k] = observation_exposure_valid(series, pre, int(t), collector)
        exp30[k] = observation_exposure_valid(series, int(t), int(t + 30 * NS), collector)
        exp60[k] = observation_exposure_valid(series, int(t), int(t + 60 * NS), collector)
        exp120[k] = observation_exposure_valid(series, int(t), int(t + 120 * NS), collector)
        if s_pre is not None:
            p_pre[k] = float(s_pre["mid"])
            spread[k] = float(s_pre["spread"])
            ga = float(s_pre["genuine_change_age_seconds"])
            ra = float(s_pre["record_age_seconds"])
            age[k] = np.log1p(ga / 30.0) if np.isfinite(ga) and ga >= 0 else np.nan
            record_age[k] = np.log1p(ra / 30.0) if np.isfinite(ra) and ra >= 0 else np.nan
        if s_cur is not None:
            p_cur[k] = float(s_cur["mid"])
            bid_cur[k] = float(s_cur["bid"])
            ask_cur[k] = float(s_cur["ask"])
        if s30 is not None:
            p30[k] = float(s30["mid"])
            bid30[k] = float(s30["bid"])
            ask30[k] = float(s30["ask"])
        if s60 is not None:
            p60[k] = float(s60["mid"])
        if s120 is not None:
            p120[k] = float(s120["mid"])
        if exp_src[k] and np.isfinite(p_pre[k]) and np.isfinite(p_cur[k]):
            ret[k] = p_cur[k] - p_pre[k]
            abs_ret[k] = abs(ret[k])
            c30[k] = count_events(series, pre, int(t), kind="genuine")
            raw30[k] = count_events(series, pre, int(t), kind="raw")
            unchanged30[k] = count_events(series, pre, int(t), kind="unchanged")
            dep30[k] = depth_count(depth_times, pre, int(t))
        if np.isfinite(p_cur[k]):
            c300[k] = count_events(series, int(t - 300 * NS), int(t), kind="genuine")
            mc300[k] = count_events(series, int(t - 300 * NS), int(t), kind="midpoint")
        if exp30[k]:
            renew30[k] = 1.0 if any_genuine_change(series, int(t), int(t + 30 * NS)) else 0.0
        future_changes = series.times_ns[
            series.genuine_change
            & (series.times_ns > int(t))
            & (series.times_ns <= int(t + 120 * NS))
        ]
        if len(future_changes):
            latency[k] = (int(future_changes[0]) - int(t)) / NS

    for k in range(n):
        lo = max(0, k - 9)
        vals = abs_ret[lo : k + 1]
        vals = vals[np.isfinite(vals)]
        if len(vals):
            vol[k] = float(np.mean(vals))

    return TokenGrid(
        token="", q=q, p_pre=p_pre, p_cur=p_cur, p30=p30, p60=p60, p120=p120,
        bid_cur=bid_cur, ask_cur=ask_cur, bid30=bid30, ask30=ask30,
        pre_spread=spread, A=age, record_age=record_age, ret30=ret,
        abs_ret30=abs_ret, vol300=vol, count30=c30, count300=c300,
        mid_count300=mc300, raw30=raw30, unchanged30=unchanged30,
        depth30=dep30, exposure_source=exp_src, exposure30=exp30,
        exposure60=exp60, exposure120=exp120, renew30=renew30,
        first_update_latency=latency,
    )



def q_grid(start: datetime, end: datetime) -> np.ndarray:
    start_ns = ns(start)
    end_ns = ns(end)
    first = start_ns + STEP * NS - 1
    last = end_ns - 30 * NS - 1
    if last < first:
        return np.zeros(0, np.int64)
    return np.arange(first, last + 1, STEP * NS, dtype=np.int64)


def valid_peer_indices(
    grids: dict[str, TokenGrid],
    tokens: list[str],
    k: int,
) -> list[str]:
    return [
        token for token in tokens
        if grids[token].exposure_source[k]
        and np.isfinite(grids[token].ret30[k])
        and np.isfinite(grids[token].A[k])
        and np.isfinite(grids[token].count30[k])
    ]


def matched_outside(
    grids: dict[str, TokenGrid],
    peers: list[str],
    outside: list[str],
    k: int,
) -> tuple[float, float, int]:
    peer_rows = [
        (
            grids[token].p_pre[k],
            grids[token].A[k],
            np.log1p(grids[token].count300[k]),
        )
        for token in peers
        if np.isfinite(grids[token].p_pre[k])
        and np.isfinite(grids[token].A[k])
        and np.isfinite(grids[token].count300[k])
    ]
    if not peer_rows:
        return np.nan, np.nan, 0
    center = np.median(np.asarray(peer_rows, float), axis=0)
    candidates: list[tuple[float, str]] = []
    for token in outside:
        g = grids[token]
        if not g.exposure_source[k]:
            continue
        vals = (g.p_pre[k], g.A[k], g.count300[k], g.abs_ret30[k], g.count30[k])
        if not all(np.isfinite(v) for v in vals):
            continue
        activity = np.log1p(g.count300[k])
        distance = (
            abs(g.p_pre[k] - center[0]) / 0.10
            + abs(g.A[k] - center[1])
            + abs(activity - center[2])
        )
        candidates.append((float(distance), token))
    candidates.sort(key=lambda item: (item[0], item[1]))
    take = min(len(peers), len(candidates))
    chosen = [token for _, token in candidates[:take]]
    if not chosen:
        return np.nan, np.nan, 0
    return (
        float(np.mean([grids[token].abs_ret30[k] for token in chosen])),
        float(np.mean([np.log1p(grids[token].count30[k]) for token in chosen])),
        len(chosen),
    )


def build_regime_frame(
    event: str,
    regime: str,
    event_family: str,
    admitted: list[dict[str, str]],
    recon: dict[str, Any],
    depth_times: dict[str, np.ndarray],
    collector: np.ndarray,
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, dict[str, list[int]]]:
    from predictions_cup.learning.conditional_response import any_genuine_change, count_events

    q = q_grid(start, end)
    if len(q) == 0:
        return pd.DataFrame(), {}
    by_token = {row["canonical_token_id"]: row for row in admitted}
    tokens = sorted(by_token)
    grids = {
        token: build_token_grid(recon[token], depth_times[token], collector, q)
        for token in tokens
    }
    for token, grid in grids.items():
        grid.token = token

    family_tokens: dict[str, list[str]] = defaultdict(list)
    for token, row in by_token.items():
        family_tokens[row["market_family"]].append(token)
    for values in family_tokens.values():
        values.sort()

    start_ns = ns(start)
    end_ns = ns(end)
    initial_changes: dict[str, list[int]] = {}
    for token in tokens:
        series = recon[token]
        prior = series.times_ns[series.genuine_change & (series.times_ns < start_ns)]
        keep = list(map(int, prior[prior > start_ns - 300 * NS]))
        if len(prior) and (not keep or int(prior[-1]) != keep[-1]):
            keep.insert(0, int(prior[-1]))
        initial_changes[token] = keep

    rows: list[dict[str, Any]] = []
    for target in tokens:
        target_meta = by_token[target]
        family = target_meta["market_family"]
        peers_all = [token for token in family_tokens[family] if token != target]
        outside_all = [token for token in tokens if by_token[token]["market_family"] != family]
        if not peers_all:
            continue
        target_grid = grids[target]
        target_series = recon[target]
        for k, t in enumerate(q):
            peers = valid_peer_indices(grids, peers_all, k)
            if not peers:
                continue
            peer_fraction = len(peers) / len(peers_all)
            E = float(np.mean([grids[p].abs_ret30[k] for p in peers]))
            F = float(np.mean([np.log1p(grids[p].count30[k]) for p in peers]))
            source_age = float(np.mean([grids[p].A[k] for p in peers]))
            raw_F = float(np.mean([np.log1p(grids[p].raw30[k]) for p in peers]))
            unchanged_F = float(np.mean([np.log1p(grids[p].unchanged30[k]) for p in peers]))
            depth_F = float(np.mean([np.log1p(grids[p].depth30[k]) for p in peers]))

            outside = valid_peer_indices(grids, outside_all, k)
            outside_abs = (
                float(np.mean([grids[p].abs_ret30[k] for p in outside]))
                if outside else np.nan
            )
            outside_F = (
                float(np.mean([np.log1p(grids[p].count30[k]) for p in outside]))
                if outside else np.nan
            )
            matched_E, matched_F, matched_n = matched_outside(grids, peers, outside_all, k)


            pre = int(t - STEP * NS)
            target_same_times = set(
                map(
                    int,
                    target_series.times_ns[
                        target_series.genuine_change
                        & (target_series.times_ns > pre)
                        & (target_series.times_ns <= int(t))
                    ],
                )
            )
            same_excl_values = [
                np.log1p(
                    count_events(
                        recon[p],
                        pre,
                        int(t),
                        kind="genuine",
                        exclude_times=target_same_times,
                    )
                )
                for p in peers
            ]
            F_same_excl = float(np.mean(same_excl_values))

            tg = target_grid
            ppre = tg.p_pre[k]
            A = tg.A[k]
            S = tg.pre_spread[k]
            response_ok = bool(tg.exposure30[k])
            y_d1 = (
                abs(tg.p30[k] - tg.p_cur[k])
                if response_ok and np.isfinite(tg.p30[k]) and np.isfinite(tg.p_cur[k])
                else np.nan
            )
            y_d2 = tg.renew30[k] if response_ok else np.nan
            y_d1_60 = (
                abs(tg.p60[k] - tg.p_cur[k])
                if tg.exposure60[k]
                and int(t + 60 * NS) < end_ns
                and np.isfinite(tg.p60[k])
                and np.isfinite(tg.p_cur[k])
                else np.nan
            )
            y_d1_120 = (
                abs(tg.p120[k] - tg.p_cur[k])
                if tg.exposure120[k]
                and int(t + 120 * NS) < end_ns
                and np.isfinite(tg.p120[k])
                and np.isfinite(tg.p_cur[k])
                else np.nan
            )
            y_d2_60 = (
                float(any_genuine_change(target_series, int(t), int(t + 60 * NS)))
                if tg.exposure60[k] and int(t + 60 * NS) < end_ns
                else np.nan
            )
            y_d2_120 = (
                float(any_genuine_change(target_series, int(t), int(t + 120 * NS)))
                if tg.exposure120[k] and int(t + 120 * NS) < end_ns
                else np.nan
            )

            db = tg.bid30[k] - tg.bid_cur[k] if response_ok else np.nan
            da = tg.ask30[k] - tg.ask_cur[k] if response_ok else np.nan
            quote_motion = "UNAVAILABLE"
            if np.isfinite(db) and np.isfinite(da):
                if abs(db) < 1e-15 and abs(da) < 1e-15:
                    quote_motion = "NO_CHANGE"
                elif abs(db) < 1e-15 or abs(da) < 1e-15:
                    quote_motion = "ONE_SIDED"
                elif db * da > 0:
                    quote_motion = "TRANSLATION"
                else:
                    quote_motion = "WIDTH_CHANGE"

            rows.append({
                "event": event,
                "event_family": event_family,
                "regime": regime,
                "target": target,
                "condition_id": target_meta["condition_id"],
                "market_family": family,
                "time_ns": int(t),
                "block": int((int(t) - start_ns) // (300 * NS)),
                "elapsed": (int(t) - start_ns) / NS,
                "p_pre": ppre,
                "p_pre_var": ppre * (1 - ppre) if np.isfinite(ppre) else np.nan,
                "own_ret30": tg.ret30[k],
                "own_abs_ret30": tg.abs_ret30[k],
                "own_vol300": tg.vol300[k],
                "A_genuine_age": A,
                "D_genuine_age": A,
                "record_age": tg.record_age[k],
                "spread_pre": S,
                "own_count30": tg.count30[k],
                "own_count300": tg.count300[k],
                "own_mid_count300": tg.mid_count300[k],
                "E_family": E,
                "source_age": source_age,
                "F_family": F,
                "outside_abs_ret": outside_abs,
                "outside_F": outside_F,
                "peer_count": len(peers),
                "peer_fraction": peer_fraction,
                "z_d1": E * A if np.isfinite(A) else np.nan,
                "z_d2": F * A if np.isfinite(A) else np.nan,
                "z_d4": E * S if np.isfinite(S) else np.nan,
                "z_d1_record_age": E * tg.record_age[k]
                    if np.isfinite(tg.record_age[k]) else np.nan,
                "z_d1_matched_outside": matched_E * A
                    if np.isfinite(matched_E) and np.isfinite(A) else np.nan,
                "z_d2_matched_outside": matched_F * A
                    if np.isfinite(matched_F) and np.isfinite(A) else np.nan,
                "z_d2_raw": raw_F * A if np.isfinite(A) else np.nan,
                "z_d2_unchanged": unchanged_F * A if np.isfinite(A) else np.nan,
                "z_d2_depth": depth_F * A if np.isfinite(A) else np.nan,
                "z_d2_same_timestamp_excluded": F_same_excl * A
                    if np.isfinite(A) else np.nan,
                "matched_outside_n": matched_n,
                "raw_F": raw_F,
                "unchanged_F": unchanged_F,
                "depth_F": depth_F,
                "F_same_timestamp_excluded": F_same_excl,
                "y_d1_30": y_d1,
                "y_d1_60": y_d1_60,
                "y_d1_120": y_d1_120,
                "y_d2_30": y_d2,
                "y_d2_60": y_d2_60,
                "y_d2_120": y_d2_120,
                "first_update_latency": tg.first_update_latency[k],
                "quote_motion": quote_motion,
                "source_exposure": bool(tg.exposure_source[k]),
                "future_exposure_30": response_ok,
            })

    return pd.DataFrame(rows), initial_changes


def event_assets(
    corpus: Path,
    event: str,
    registry: list[dict[str, str]],
    windows: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    admitted_all = [
        row for row in registry
        if row["event"] == event and row["admitted"] == "true"
    ]
    tokens = sorted({row["canonical_token_id"] for row in admitted_all})
    pre_start = dt(windows[event]["PRE_ELECTION"][0])
    active_end = dt(windows[event]["ACTIVE_RESULTS"][1])
    load_end = datetime.fromtimestamp(active_end.timestamp() + 301, tz=active_end.tzinfo)
    recon = load_reconstructions(corpus, event, tokens, pre_start, load_end)
    collector = load_collector_times(corpus, event, pre_start, load_end)
    depth_times = load_depth_times(corpus, event, tokens, pre_start, load_end)
    frames: dict[str, pd.DataFrame] = {}
    initial: dict[str, dict[str, list[int]]] = {}
    for regime in REGIMES:
        admitted = [row for row in admitted_all if row["regime"] == regime]
        start = dt(windows[event][regime][0])
        end = dt(windows[event][regime][1])
        family = admitted[0]["event_family"] if admitted else ""
        frame, history = build_regime_frame(
            event, regime, family, admitted, recon, depth_times, collector, start, end
        )
        frames[regime] = frame
        initial[regime] = history
    return {
        "event": event,
        "frames": frames,
        "initial_changes": initial,
        "collector_instants": int(len(collector)),
        "tokens": int(len(tokens)),
    }



def finite_model_frame(
    frame: pd.DataFrame,
    base_names: tuple[str, ...],
    z_name: str,
    y_name: str,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = np.ones(len(frame), bool)
    for name in list(base_names) + [z_name, y_name]:
        mask &= np.isfinite(frame[name].to_numpy(float))
    core = ["time_ns", "event", "block", "target"]
    extras = [
        name for name in (
            "event_family", "regime", "condition_id", "market_family",
            "quote_motion", "first_update_latency", "y_d1_60", "y_d1_120",
            "y_d2_60", "y_d2_120", "z_d1_record_age",
            "z_d1_matched_outside", "z_d2_matched_outside", "z_d2_raw",
            "z_d2_unchanged", "z_d2_depth", "z_d2_same_timestamp_excluded",
        ) if name in frame.columns
    ]
    cols = list(dict.fromkeys(list(base_names) + [z_name, y_name] + core + extras))
    return frame.loc[mask, cols].copy()


def hierarchical_weights(frame: pd.DataFrame) -> np.ndarray:
    from predictions_cup.learning.conditional_response import equal_hierarchical_weights

    blocks = [
        f"{event}|{block}"
        for event, block in zip(frame["event"], frame["block"], strict=True)
    ]
    return equal_hierarchical_weights(
        frame["event"].astype(str).to_numpy(),
        np.asarray(blocks, object),
        frame["target"].astype(str).to_numpy(),
    )


def weighted_scaler(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wn = w / w.sum()
    mean = np.sum(x * wn[:, None], axis=0)
    var = np.sum(((x - mean) ** 2) * wn[:, None], axis=0)
    scale = np.sqrt(np.maximum(var, 1e-12))
    return mean, scale


def fit_frozen_model(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    feature_names: list[str],
    *,
    task: str,
) -> dict[str, Any]:
    mean, scale = weighted_scaler(x, w)
    zx = (x - mean) / scale
    if task == "linear":
        estimator = Ridge(alpha=1.0, fit_intercept=True)
        estimator.fit(zx, y, sample_weight=w)
        coef = np.asarray(estimator.coef_, float)
        intercept = float(estimator.intercept_)
        kind = "linear"
    else:
        if len(np.unique(y)) < 2:
            raise ValueError("logistic fit requires both renewal classes")
        estimator = LogisticRegression(
            C=1.0, penalty="l2", solver="lbfgs", max_iter=2000,
            tol=1e-9, fit_intercept=True,
        )
        estimator.fit(zx, y.astype(int), sample_weight=w)
        coef = np.asarray(estimator.coef_[0], float)
        intercept = float(estimator.intercept_[0])
        kind = "logistic"
    if not np.all(np.isfinite(coef)) or not np.isfinite(intercept):
        raise ValueError("non-finite fitted model")
    return {
        "kind": kind, "feature_names": feature_names, "mean": mean,
        "scale": scale, "coef": coef, "intercept": intercept,
    }


def predict_frozen(model: dict[str, Any], x: np.ndarray) -> np.ndarray:
    zx = (x - model["mean"]) / model["scale"]
    eta = model["intercept"] + zx @ model["coef"]
    if model["kind"] == "logistic":
        return 1.0 / (1.0 + np.exp(-np.clip(eta, -35, 35)))
    return eta


def chronological_masks(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    train = np.zeros(len(frame), bool)
    validation = np.zeros(len(frame), bool)
    times = frame["time_ns"].to_numpy(np.int64)
    events = frame["event"].astype(str).to_numpy()
    embargo = 300 * NS
    for event in sorted(set(events)):
        idx = np.where(events == event)[0]
        lo = int(times[idx].min())
        hi = int(times[idx].max())
        cut = lo + (2 * (hi - lo)) // 3
        train[idx[times[idx] <= cut - embargo]] = True
        validation[idx[times[idx] >= cut + embargo]] = True
    return train, validation


def weighted_loss(y: np.ndarray, p: np.ndarray, w: np.ndarray) -> float:
    return float(np.average((y - p) ** 2, weights=w))


def model_spec(hypothesis: str) -> tuple[tuple[str, ...], str, str, str, int]:
    if hypothesis == "D1":
        return D1_BASE, "z_d1", "y_d1_30", "linear", 1
    if hypothesis == "D2":
        return D2_BASE, "z_d2", "y_d2_30", "logistic", 1
    if hypothesis == "D4":
        return D4_BASE, "z_d4", "y_d2_30", "logistic", -1
    raise ValueError(hypothesis)



def support_for_frame(
    frame: pd.DataFrame,
    base: tuple[str, ...],
    z_name: str,
    y_name: str,
    *,
    training: bool,
    include_y: bool = True,
) -> tuple[bool, list[str], dict[str, Any]]:
    from predictions_cup.learning.conditional_response import support_gate

    if frame.empty:
        return False, ["NO_VALID_ROWS"], {"rows": 0}
    x = frame.loc[:, list(base)].to_numpy(float)
    z = frame[z_name].to_numpy(float)
    y = frame[y_name].to_numpy(float) if include_y else None
    return support_gate(
        x, z, frame["time_ns"].to_numpy(np.int64), y=y, training=training
    )


def fit_discovery(
    discovery_frames: list[pd.DataFrame],
    regime: str,
    hypothesis: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    base, z_name, y_name, task, expected_sign = model_spec(hypothesis)
    parts: list[pd.DataFrame] = []
    for frame in discovery_frames:
        if frame.empty:
            continue
        if str(frame["regime"].iloc[0]) != regime:
            continue
        part = finite_model_frame(frame, base, z_name, y_name)
        if not part.empty:
            parts.append(part)
    audit: list[dict[str, Any]] = []
    if not parts:
        audit.append({
            "scope": "DISCOVERY", "regime": regime, "hypothesis": hypothesis,
            "status": "INCONCLUSIVE_NO_ROWS",
        })
        return None, None, audit

    all_rows = pd.concat(parts, ignore_index=True)
    train_mask, val_mask = chronological_masks(all_rows)
    train_rows = all_rows.loc[train_mask].copy()
    val_rows = all_rows.loc[val_mask].copy()
    ok, reasons, details = support_for_frame(
        train_rows, base, z_name, y_name, training=True
    )
    audit.append({
        "scope": "DISCOVERY_TRAIN", "regime": regime, "hypothesis": hypothesis,
        "status": "OK" if ok else "INCONCLUSIVE_SUPPORT",
        "reasons": "|".join(reasons), **details,
    })
    if not ok:
        return None, None, audit

    x_train = train_rows.loc[:, list(base)].to_numpy(float)
    z_train = train_rows[z_name].to_numpy(float)[:, None]
    y_train = train_rows[y_name].to_numpy(float)
    w_train = hierarchical_weights(train_rows)
    baseline = fit_frozen_model(x_train, y_train, w_train, list(base), task=task)
    challenger = fit_frozen_model(
        np.column_stack([x_train, z_train]), y_train, w_train,
        list(base) + [z_name], task=task,
    )

    if len(val_rows):
        x_val = val_rows.loc[:, list(base)].to_numpy(float)
        z_val = val_rows[z_name].to_numpy(float)
        y_val = val_rows[y_name].to_numpy(float)
        w_val = hierarchical_weights(val_rows)
        p0 = predict_frozen(baseline, x_val)
        p1 = predict_frozen(challenger, np.column_stack([x_val, z_val]))
        gain = weighted_loss(y_val, p0, w_val) - weighted_loss(y_val, p1, w_val)
        score = float(np.average(z_val * (y_val - p0), weights=w_val))
        audit.append({
            "scope": "DISCOVERY_VALIDATION",
            "regime": regime, "hypothesis": hypothesis, "rows": len(val_rows),
            "loss_gain": gain, "interaction_score": score,
            "expected_sign": expected_sign,
            "sign_pass": bool(score * expected_sign > 0),
            "gain_pass": bool(gain > 0),
        })

    full_ok, full_reasons, full_details = support_for_frame(
        all_rows, base, z_name, y_name, training=True
    )
    audit.append({
        "scope": "DISCOVERY_REFIT", "regime": regime, "hypothesis": hypothesis,
        "status": "OK" if full_ok else "INCONCLUSIVE_SUPPORT",
        "reasons": "|".join(full_reasons), **full_details,
    })
    if not full_ok:
        return None, None, audit

    x_all = all_rows.loc[:, list(base)].to_numpy(float)
    z_all = all_rows[z_name].to_numpy(float)[:, None]
    y_all = all_rows[y_name].to_numpy(float)
    w_all = hierarchical_weights(all_rows)
    baseline = fit_frozen_model(x_all, y_all, w_all, list(base), task=task)
    challenger = fit_frozen_model(
        np.column_stack([x_all, z_all]), y_all, w_all,
        list(base) + [z_name], task=task,
    )
    audit.append({
        "scope": "DISCOVERY_FROZEN_MODEL", "regime": regime,
        "hypothesis": hypothesis,
        "interaction_coefficient_standardized": float(challenger["coef"][-1]),
        "coefficient_expected_sign_pass": bool(
            challenger["coef"][-1] * expected_sign > 0
        ),
        "baseline_features": len(base),
        "challenger_features": len(base) + 1,
    })
    return baseline, challenger, audit



def evaluate_event(
    frame: pd.DataFrame,
    hypothesis: str,
    baseline: dict[str, Any],
    challenger: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    base, z_name, y_name, task, expected_sign = model_spec(hypothesis)
    rows = finite_model_frame(frame, base, z_name, y_name)
    if rows.empty:
        return {"status": "INCONCLUSIVE_NO_ROWS"}, rows
    ok, reasons, details = support_for_frame(
        rows, base, z_name, y_name, training=False
    )
    if not ok:
        return {
            "status": "INCONCLUSIVE_SUPPORT",
            "reasons": "|".join(reasons), **details,
        }, rows
    x = rows.loc[:, list(base)].to_numpy(float)
    z = rows[z_name].to_numpy(float)
    y = rows[y_name].to_numpy(float)
    w = hierarchical_weights(rows)
    p0 = predict_frozen(baseline, x)
    p1 = predict_frozen(challenger, np.column_stack([x, z]))
    score = float(np.average(z * (y - p0), weights=w))
    gain = weighted_loss(y, p0, w) - weighted_loss(y, p1, w)
    return {
        "status": "OK", **details,
        "loss_baseline": weighted_loss(y, p0, w),
        "loss_challenger": weighted_loss(y, p1, w),
        "loss_gain": gain,
        "interaction_score": score,
        "expected_sign": expected_sign,
        "sign_pass": bool(score * expected_sign > 0),
        "gain_pass": bool(gain > 0),
        "outcome_mean": float(np.average(y, weights=w)),
    }, rows


def model_record(
    model: dict[str, Any] | None,
    label: str,
    regime: str,
    hypothesis: str,
) -> list[dict[str, Any]]:
    if model is None:
        return []
    rows = [{
        "regime": regime, "hypothesis": hypothesis, "model": label,
        "feature": "INTERCEPT", "coefficient": model["intercept"],
        "mean": None, "scale": None,
    }]
    for name, coef, mean, scale in zip(
        model["feature_names"], model["coef"], model["mean"], model["scale"], strict=True
    ):
        rows.append({
            "regime": regime, "hypothesis": hypothesis, "model": label,
            "feature": name, "coefficient": float(coef),
            "mean": float(mean), "scale": float(scale),
        })
    return rows


def empirical_p(observed: float, exceed: int, draws: int) -> float:
    return (1.0 + int(exceed)) / (draws + 1.0)


def discovery_residual_blocks(
    frames: list[pd.DataFrame],
    regime: str,
    baseline: dict[str, Any],
) -> list[np.ndarray]:
    pools: list[np.ndarray] = []
    for frame in frames:
        if frame.empty or str(frame["regime"].iloc[0]) != regime:
            continue
        rows = finite_model_frame(frame, D1_BASE, "z_d1", "y_d1_30")
        if rows.empty:
            continue
        x = rows.loc[:, list(D1_BASE)].to_numpy(float)
        y = rows["y_d1_30"].to_numpy(float)
        residual = y - predict_frozen(baseline, x)
        for _, idx in rows.groupby(["event", "block"], sort=True).groups.items():
            values = residual[np.asarray(list(idx), dtype=int)]
            values = values[np.isfinite(values)]
            if len(values):
                pools.append(values - float(np.mean(values)))
    return pools


def discovery_common_offsets(
    frames: list[pd.DataFrame],
    regime: str,
    hypothesis: str,
    baseline: dict[str, Any],
) -> np.ndarray:
    base, z_name, y_name, task, _ = model_spec(hypothesis)
    offsets: list[float] = []
    for frame in frames:
        if frame.empty or str(frame["regime"].iloc[0]) != regime:
            continue
        rows = finite_model_frame(frame, base, z_name, y_name)
        if rows.empty:
            continue
        x = rows.loc[:, list(base)].to_numpy(float)
        y = rows[y_name].to_numpy(float)
        p = np.clip(predict_frozen(baseline, x), 1e-4, 1 - 1e-4)
        for _, group in rows.assign(_y=y, _p=p).groupby(["event", "block"], sort=True):
            yy = float(group["_y"].mean())
            pp = float(group["_p"].mean())
            yy = min(max(yy, 1e-4), 1 - 1e-4)
            pp = min(max(pp, 1e-4), 1 - 1e-4)
            offsets.append(math.log(yy / (1 - yy)) - math.log(pp / (1 - pp)))
    if not offsets:
        return np.asarray([0.0])
    values = np.asarray(offsets, float)
    return values - float(np.mean(values))



def d1_common_async_null(
    rows: pd.DataFrame,
    baseline: dict[str, Any],
    challenger: dict[str, Any],
    residual_pools: list[np.ndarray],
    component: str,
) -> dict[str, Any]:
    if not residual_pools:
        return {"status": "INCONCLUSIVE_NO_DISCOVERY_RESIDUAL_BLOCKS"}
    x = rows.loc[:, list(D1_BASE)].to_numpy(float)
    z = rows["z_d1"].to_numpy(float)
    y = rows["y_d1_30"].to_numpy(float)
    w = hierarchical_weights(rows)
    p0 = predict_frozen(baseline, x)
    p1 = predict_frozen(challenger, np.column_stack([x, z]))
    observed_score = float(np.average(z * (y - p0), weights=w))
    observed_gain = weighted_loss(y, p0, w) - weighted_loss(y, p1, w)

    group_positions: list[np.ndarray] = []
    for _, labels in rows.groupby("block", sort=True).groups.items():
        positions = rows.index.get_indexer(np.asarray(list(labels)))
        positions = positions[positions >= 0]
        if len(positions):
            group_positions.append(positions)

    rng = np.random.default_rng(deterministic_seed(component))
    score_exceed = 0
    gain_exceed = 0
    draws_done = 0
    batch_size = 100
    while draws_done < NULL_DRAWS:
        batch = min(batch_size, NULL_DRAWS - draws_done)
        ysim = np.tile(p0, (batch, 1))
        for positions in group_positions:
            n = len(positions)
            for b in range(batch):
                pool = residual_pools[int(rng.integers(0, len(residual_pools)))]
                sampled = rng.choice(pool, size=n, replace=True)
                ysim[b, positions] += sampled
        ysim = np.clip(ysim, 0.0, 1.0)
        score = np.sum((ysim - p0[None, :]) * (w * z)[None, :], axis=1) / w.sum()
        gain = (
            np.sum(
                w[None, :]
                * (
                    (ysim - p0[None, :]) ** 2
                    - (ysim - p1[None, :]) ** 2
                ),
                axis=1,
            )
            / w.sum()
        )
        score_exceed += int(np.sum(score >= observed_score))
        gain_exceed += int(np.sum(gain >= observed_gain))
        draws_done += batch

    return {
        "status": "OK",
        "null": "N2_COMMON_INFORMATION_ASYNC_OBSERVATION",
        "draws_attempted": NULL_DRAWS,
        "draws_valid": NULL_DRAWS,
        "observed_score": observed_score,
        "observed_gain": observed_gain,
        "p_score_expected_positive": empirical_p(
            observed_score, score_exceed, NULL_DRAWS
        ),
        "p_predictive_gain": empirical_p(observed_gain, gain_exceed, NULL_DRAWS),
        "preserves_observed_masks": True,
        "preserves_observed_age_state": True,
        "preserves_common_cross_sectional_residual_blocks": True,
        "cross_market_interaction_in_generator": 0.0,
    }


def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(q / (1 - q))


def sequential_hazard_null(
    rows: pd.DataFrame,
    hypothesis: str,
    baseline: dict[str, Any],
    challenger: dict[str, Any],
    common_offsets: np.ndarray,
    initial_changes: dict[str, list[int]],
    component: str,
) -> dict[str, Any]:
    base, z_name, y_name, task, expected_sign = model_spec(hypothesis)
    if task != "logistic":
        raise ValueError("sequential hazard null requires logistic task")
    work = rows.sort_values(["target", "time_ns"]).reset_index(drop=True)
    x_obs = work.loc[:, list(base)].to_numpy(float)
    z_obs = work[z_name].to_numpy(float)
    y_obs = work[y_name].to_numpy(float)
    weights = hierarchical_weights(work)
    p0_obs = predict_frozen(baseline, x_obs)
    p1_obs = predict_frozen(challenger, np.column_stack([x_obs, z_obs]))
    observed_score = float(np.average(z_obs * (y_obs - p0_obs), weights=weights))
    observed_gain = weighted_loss(y_obs, p0_obs, weights) - weighted_loss(
        y_obs, p1_obs, weights
    )

    feature_index = {name: i for i, name in enumerate(base)}
    target_groups = {
        str(target): np.asarray(list(labels), dtype=int)
        for target, labels in work.groupby("target", sort=True).groups.items()
    }
    block_by_row = work["block"].to_numpy(int)
    time_by_row = work["time_ns"].to_numpy(np.int64)
    rng = np.random.default_rng(deterministic_seed(component))
    score_exceed = 0
    gain_exceed = 0
    simulated_positive = 0.0
    total_weighted_rows = 0.0
    draws_done = 0
    batch_size = 100

    while draws_done < NULL_DRAWS:
        batch = min(batch_size, NULL_DRAWS - draws_done)
        block_offsets = {
            int(block): rng.choice(common_offsets, size=batch, replace=True)
            for block in np.unique(block_by_row)
        }
        score_sum = np.zeros(batch, float)
        gain_sum = np.zeros(batch, float)
        positive_sum = np.zeros(batch, float)
        weight_sum = float(weights.sum())

        for target, positions in target_groups.items():
            actual_initial = sorted(initial_changes.get(target, []))
            first_t = int(time_by_row[positions[0]])
            first_pre = first_t - 30 * NS
            prior = [t for t in actual_initial if t <= first_pre]
            if prior:
                initial_last = int(prior[-1])
            else:
                observed_d = float(work.loc[positions[0], "D_genuine_age"])
                age_seconds = 30.0 * math.expm1(observed_d)
                initial_last = int(first_pre - age_seconds * NS)
            matured_last = np.full(batch, initial_last, np.int64)
            scheduled: list[tuple[int, np.ndarray]] = []
            mature_cursor = 0
            deterministic_history = [
                int(t) for t in actual_initial if t > first_t - 330 * NS
            ]

            for pos in positions:
                t = int(time_by_row[pos])
                pre = t - 30 * NS
                while mature_cursor < len(scheduled) and scheduled[mature_cursor][0] <= pre:
                    et, mask = scheduled[mature_cursor]
                    matured_last[mask] = et
                    mature_cursor += 1

                recent_sim = [
                    (et, mask)
                    for et, mask in scheduled
                    if et <= t and et > t - 300 * NS
                ]
                d = np.log1p(np.maximum(0.0, (pre - matured_last) / NS) / 30.0)
                c30 = np.zeros(batch, float)
                c300 = np.zeros(batch, float)
                for et in deterministic_history:
                    if t - 30 * NS < et <= t:
                        c30 += 1.0
                    if t - 300 * NS < et <= t:
                        c300 += 1.0
                for et, mask in recent_sim:
                    if t - 30 * NS < et <= t:
                        c30 += mask.astype(float)
                    if t - 300 * NS < et <= t:
                        c300 += mask.astype(float)

                xb = np.tile(x_obs[pos], (batch, 1))
                xb[:, feature_index["D_genuine_age"]] = d
                xb[:, feature_index["own_count30"]] = c30
                xb[:, feature_index["own_count300"]] = c300
                if hypothesis == "D2":
                    zdyn = xb[:, feature_index["F_family"]] * d
                else:
                    zdyn = (
                        xb[:, feature_index["E_family"]]
                        * xb[:, feature_index["spread_pre"]]
                    )
                p0 = predict_frozen(baseline, xb)
                generator_p = 1.0 / (
                    1.0
                    + np.exp(
                        -np.clip(
                            _logit(p0) + block_offsets[int(block_by_row[pos])],
                            -35,
                            35,
                        )
                    )
                )
                draws = rng.random(batch) < generator_p
                p1 = predict_frozen(
                    challenger, np.column_stack([xb, zdyn])
                )
                weight = float(weights[pos])
                score_sum += weight * zdyn * (draws.astype(float) - p0)
                gain_sum += weight * (
                    (draws.astype(float) - p0) ** 2
                    - (draws.astype(float) - p1) ** 2
                )
                positive_sum += weight * draws.astype(float)
                if np.any(draws):
                    scheduled.append((t + 15 * NS, draws.copy()))

        score = score_sum / weight_sum
        gain = gain_sum / weight_sum
        if expected_sign > 0:
            score_exceed += int(np.sum(score >= observed_score))
        else:
            score_exceed += int(np.sum(score <= observed_score))
        gain_exceed += int(np.sum(gain >= observed_gain))
        simulated_positive += float(np.sum(positive_sum / weight_sum))
        total_weighted_rows += float(batch)
        draws_done += batch

    return {
        "status": "OK",
        "null": "N2_NONSTATIONARY_COMMON_INTENSITY_RENEWAL",
        "draws_attempted": NULL_DRAWS,
        "draws_valid": NULL_DRAWS,
        "observed_score": observed_score,
        "observed_gain": observed_gain,
        "p_score_expected_sign": empirical_p(
            observed_score, score_exceed, NULL_DRAWS
        ),
        "p_predictive_gain": empirical_p(observed_gain, gain_exceed, NULL_DRAWS),
        "observed_renewal_rate": float(np.average(y_obs, weights=weights)),
        "simulated_mean_renewal_rate": (
            simulated_positive / total_weighted_rows
            if total_weighted_rows else None
        ),
        "self_renewal_history_recomputed": True,
        "common_block_offsets_preserved_from_discovery": True,
        "capture_schedule_fixed_to_observed_risk_set": True,
        "cross_family_excitation_interaction_in_generator": 0.0,
    }



def fit_control_challenger(
    discovery_frames: list[pd.DataFrame],
    regime: str,
    hypothesis: str,
    control_z: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    base, primary_z, y_name, task, _ = model_spec(hypothesis)
    parts: list[pd.DataFrame] = []
    for frame in discovery_frames:
        if frame.empty or str(frame["regime"].iloc[0]) != regime:
            continue
        mask = np.ones(len(frame), bool)
        for name in list(base) + [control_z, y_name]:
            mask &= np.isfinite(frame[name].to_numpy(float))
        cols = list(base) + [control_z, y_name, "event", "block", "target"]
        part = frame.loc[mask, cols].copy().reset_index(drop=True)
        if not part.empty:
            parts.append(part)
    if not parts:
        return None, None
    rows = pd.concat(parts, ignore_index=True)
    x = rows.loc[:, list(base)].to_numpy(float)
    z = rows[control_z].to_numpy(float)
    y = rows[y_name].to_numpy(float)
    w = hierarchical_weights(rows)
    if task == "logistic" and len(np.unique(y)) < 2:
        return None, None
    base_model = fit_frozen_model(x, y, w, list(base), task=task)
    control_model = fit_frozen_model(
        np.column_stack([x, z]), y, w, list(base) + [control_z], task=task
    )
    return base_model, control_model


def evaluate_control(
    frame: pd.DataFrame,
    hypothesis: str,
    control_z: str,
    baseline: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
) -> dict[str, Any]:
    if baseline is None or challenger is None or frame.empty:
        return {"status": "INCONCLUSIVE_CONTROL_UNAVAILABLE"}
    base, primary_z, y_name, task, expected_sign = model_spec(hypothesis)
    mask = np.ones(len(frame), bool)
    for name in list(base) + [control_z, y_name]:
        mask &= np.isfinite(frame[name].to_numpy(float))
    rows = frame.loc[mask].copy().reset_index(drop=True)
    if rows.empty:
        return {"status": "INCONCLUSIVE_CONTROL_UNAVAILABLE"}
    x = rows.loc[:, list(base)].to_numpy(float)
    z = rows[control_z].to_numpy(float)
    y = rows[y_name].to_numpy(float)
    w = hierarchical_weights(rows)
    p0 = predict_frozen(baseline, x)
    p1 = predict_frozen(challenger, np.column_stack([x, z]))
    return {
        "status": "OK",
        "rows": len(rows),
        "loss_gain": weighted_loss(y, p0, w) - weighted_loss(y, p1, w),
        "interaction_score": float(np.average(z * (y - p0), weights=w)),
        "outcome_mean": float(np.average(y, weights=w)),
    }


def diagnostic_horizons(frame: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if frame.empty:
        return out
    for hypothesis, z_name, prefix in (
        ("D1", "z_d1", "y_d1"),
        ("D2", "z_d2", "y_d2"),
    ):
        for horizon in (60, 120):
            y_name = f"{prefix}_{horizon}"
            mask = np.isfinite(frame[z_name].to_numpy(float)) & np.isfinite(
                frame[y_name].to_numpy(float)
            )
            if not mask.any():
                out.append({
                    "event": str(frame["event"].iloc[0]),
                    "regime": str(frame["regime"].iloc[0]),
                    "hypothesis": hypothesis, "horizon_seconds": horizon,
                    "rows": 0, "status": "UNAVAILABLE",
                })
                continue
            z = frame.loc[mask, z_name].to_numpy(float)
            y = frame.loc[mask, y_name].to_numpy(float)
            corr = None
            if len(z) >= 30 and np.std(z) > 1e-15 and np.std(y) > 1e-15:
                corr = float(np.corrcoef(z, y)[0, 1])
            out.append({
                "event": str(frame["event"].iloc[0]),
                "regime": str(frame["regime"].iloc[0]),
                "hypothesis": hypothesis,
                "horizon_seconds": horizon,
                "rows": int(mask.sum()),
                "mean_outcome": float(np.mean(y)),
                "interaction_outcome_correlation": corr,
                "status": "DIAGNOSTIC_ONLY",
            })
    latency = frame["first_update_latency"].to_numpy(float)
    latency = latency[np.isfinite(latency)]
    out.append({
        "event": str(frame["event"].iloc[0]),
        "regime": str(frame["regime"].iloc[0]),
        "hypothesis": "D2",
        "horizon_seconds": 120,
        "diagnostic": "FIRST_GENUINE_RENEWAL_LATENCY",
        "rows": int(len(latency)),
        "mean_latency_seconds": float(np.mean(latency)) if len(latency) else None,
        "median_latency_seconds": float(np.median(latency)) if len(latency) else None,
        "status": "DIAGNOSTIC_ONLY",
    })
    return out


def quote_motion_audit(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    out = []
    for label, group in frame.groupby("quote_motion", sort=True):
        y = group["y_d1_30"].to_numpy(float)
        y = y[np.isfinite(y)]
        out.append({
            "event": str(frame["event"].iloc[0]),
            "regime": str(frame["regime"].iloc[0]),
            "quote_motion": label,
            "rows": len(group),
            "mean_abs_mid_response": float(np.mean(y)) if len(y) else None,
        })
    return out


def c04_activation(
    discovery_frames: list[pd.DataFrame],
    regime: str,
) -> tuple[bool, str, dict[str, Any]]:
    parts = []
    for frame in discovery_frames:
        if frame.empty or str(frame["regime"].iloc[0]) != regime:
            continue
        mask = np.ones(len(frame), bool)
        for name in list(D4_BASE) + ["z_d4"]:
            mask &= np.isfinite(frame[name].to_numpy(float))
        part = frame.loc[mask].copy()
        if not part.empty:
            parts.append(part)
    if not parts:
        return False, "NO_NONOUTCOME_SUPPORT_ROWS", {"rows": 0}
    rows = pd.concat(parts, ignore_index=True)
    ok, reasons, details = support_for_frame(
        rows, D4_BASE, "z_d4", "y_d2_30",
        training=False, include_y=False,
    )
    return ok, "OK" if ok else "|".join(reasons), details



def coverage_audit(scope: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return [{"scope": scope, "status": "NO_ROWS"}]
    out: list[dict[str, Any]] = []
    for target, group in frame.groupby("target", sort=True):
        out.append({
            "scope": scope,
            "event": str(group["event"].iloc[0]),
            "regime": str(group["regime"].iloc[0]),
            "target": target,
            "candidate_rows": len(group),
            "source_exposure_rows": int(group["source_exposure"].astype(bool).sum()),
            "future_exposure_30_rows": int(group["future_exposure_30"].astype(bool).sum()),
            "d1_endpoint_rows": int(np.isfinite(group["y_d1_30"].to_numpy(float)).sum()),
            "d2_endpoint_rows": int(np.isfinite(group["y_d2_30"].to_numpy(float)).sum()),
            "genuine_renewals_30": int(np.nansum(group["y_d2_30"].to_numpy(float))),
        })
    return out


def main() -> None:
    from predictions_cup.learning.conditional_response import (
        MULTIPLICITY_SLOTS,
        assert_execution_safety,
        holm_adjust,
    )

    assert_execution_safety()
    code_root, code_manifest = load_code_bundle()
    prereg_path = code_root / "preregistration.json"
    registry_path = code_root / "market_family_registry.csv"
    astra_path = code_root / "ASTRA_004C_HYPOTHESIS_SET_PINNED.md"
    if sha256(prereg_path) != PREREG_SHA:
        raise RuntimeError("terminal preregistration bytes changed")
    if sha256(registry_path) != REGISTRY_SHA:
        raise RuntimeError("frozen market registry bytes changed")
    if sha256(astra_path) != ASTRA_SHA:
        raise RuntimeError("pinned Astra document bytes changed")
    prereg = json.loads(prereg_path.read_text())
    registry = read_csv(registry_path)
    windows = prereg["partition"]["windows"]
    if tuple(prereg["multiplicity"]["slots"]) != MULTIPLICITY_SLOTS:
        raise RuntimeError("eight-slot multiplicity family changed")
    if prereg["scientific_restrictions"]["real_order_placement"] is not False:
        raise RuntimeError("order-placement safety invariant failed")

    data_manifest_path = one("corpus_manifest.json")
    if sha256(data_manifest_path) != DATA001_SHA:
        raise RuntimeError("accepted DATA-001 manifest hash mismatch")
    data_manifest = json.loads(data_manifest_path.read_text())
    corpus = data_manifest_path.parent
    verification = verify_data001(
        corpus, data_manifest, DISCOVERY + CHALLENGE
    )

    run_manifest = {
        "experiment_id": "EXPERIMENT-004C-D",
        "terminal_preregistration_freeze": FREEZE_SHA,
        "implementation_commit": code_manifest["implementation_commit"],
        "preregistration_sha256": PREREG_SHA,
        "market_family_registry_sha256": REGISTRY_SHA,
        "astra_commit": prereg["scientific_source"]["astra_commit"],
        "astra_sha256": ASTRA_SHA,
        "data001_manifest_sha256": DATA001_SHA,
        "null_draws": NULL_DRAWS,
        "kaggle_namespace": "004c_d_conditional_response",
        "challenge_empirical_access_before_freeze": False,
        "challenge_prior_status": "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED",
        "real_order_placement": False,
        "data_verification": verification,
    }
    (WORK / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n"
    )

    # Discovery is loaded first. No challenge outcome is opened until the frozen
    # discovery procedure and all prespecified diagnostic challengers are fitted.
    discovery_assets = [
        event_assets(corpus, event, registry, windows)
        for event in DISCOVERY
    ]
    discovery_frames = [
        asset["frames"][regime]
        for asset in discovery_assets
        for regime in REGIMES
    ]
    coverage_rows: list[dict[str, Any]] = []
    for frame in discovery_frames:
        if not frame.empty:
            coverage_rows.extend(
                coverage_audit("DISCOVERY", frame)
            )

    model_audit: list[dict[str, Any]] = []
    coefficient_rows: list[dict[str, Any]] = []
    models: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    activation_rows: list[dict[str, Any]] = [
        {
            "hypothesis": "D3_C03",
            "regime": regime,
            "status": "DORMANT",
            "reason": prereg["hypotheses"]["D3_C03"]["reason"],
            "multiplicity_slot_retained": True,
        }
        for regime in REGIMES
    ]

    for regime in REGIMES:
        for hypothesis in ("D1", "D2"):
            baseline, challenger, audit = fit_discovery(
                discovery_frames, regime, hypothesis
            )
            model_audit.extend(audit)
            if baseline is not None and challenger is not None:
                models[(regime, hypothesis)] = (baseline, challenger)
                coefficient_rows.extend(
                    model_record(baseline, "BASELINE", regime, hypothesis)
                )
                coefficient_rows.extend(
                    model_record(challenger, "CHALLENGER", regime, hypothesis)
                )

        c04_ok, c04_reason, c04_details = c04_activation(
            discovery_frames, regime
        )
        activation_rows.append({
            "hypothesis": "D4_C04",
            "regime": regime,
            "status": "ACTIVE" if c04_ok else "DORMANT_SUPPORT_FAIL",
            "reason": c04_reason,
            "multiplicity_slot_retained": True,
            **c04_details,
        })
        if c04_ok:
            baseline, challenger, audit = fit_discovery(
                discovery_frames, regime, "D4"
            )
            model_audit.extend(audit)
            if baseline is not None and challenger is not None:
                models[(regime, "D4")] = (baseline, challenger)
                coefficient_rows.extend(
                    model_record(baseline, "BASELINE", regime, "D4")
                )
                coefficient_rows.extend(
                    model_record(challenger, "CHALLENGER", regime, "D4")
                )

    control_models: dict[tuple[str, str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    control_spec = {
        "D1": ("z_d1_record_age", "z_d1_matched_outside"),
        "D2": (
            "z_d2_raw", "z_d2_unchanged", "z_d2_depth",
            "z_d2_matched_outside", "z_d2_same_timestamp_excluded",
        ),
    }
    for regime in REGIMES:
        for hypothesis, controls in control_spec.items():
            for control in controls:
                base_model, alt_model = fit_control_challenger(
                    discovery_frames, regime, hypothesis, control
                )
                if base_model is not None and alt_model is not None:
                    control_models[(regime, hypothesis, control)] = (
                        base_model, alt_model
                    )

    write_csv(WORK / "feature_support_audit.csv", model_audit)
    write_csv(WORK / "model_coefficients.csv", coefficient_rows)
    write_csv(WORK / "secondary_activation_status.csv", activation_rows)

    # This marker is written immediately before the first challenge outcome load.
    challenge_marker = {
        "terminal_freeze": FREEZE_SHA,
        "implementation_commit": code_manifest["implementation_commit"],
        "discovery_models_frozen_before_challenge_load": True,
        "challenge_events": list(CHALLENGE),
    }
    (WORK / "challenge_access_provenance.json").write_text(
        json.dumps(challenge_marker, indent=2, sort_keys=True) + "\n"
    )

    challenge_assets = [
        event_assets(corpus, event, registry, windows)
        for event in CHALLENGE
    ]
    challenge_by_event = {asset["event"]: asset for asset in challenge_assets}
    challenge_frames = [
        asset["frames"][regime]
        for asset in challenge_assets
        for regime in REGIMES
    ]
    for frame in challenge_frames:
        if not frame.empty:
            coverage_rows.extend(
                coverage_audit("CHALLENGE", frame)
            )
    write_csv(WORK / "observation_exposure_audit.csv", coverage_rows)


    event_results: list[dict[str, Any]] = []
    null_results: list[dict[str, Any]] = []
    control_results: list[dict[str, Any]] = []
    horizon_rows: list[dict[str, Any]] = []
    motion_rows: list[dict[str, Any]] = []

    for frame in challenge_frames:
        if not frame.empty:
            horizon_rows.extend(diagnostic_horizons(frame))
            motion_rows.extend(quote_motion_audit(frame))

    slot_p = {slot: 1.0 for slot in MULTIPLICITY_SLOTS}
    slot_testable_events: dict[str, list[str]] = {
        slot: [] for slot in MULTIPLICITY_SLOTS
    }
    slot_event_p: dict[str, dict[str, float]] = {
        slot: {} for slot in MULTIPLICITY_SLOTS
    }
    slot_map = {
        ("D1", "PRE_ELECTION"): "C01-PRE",
        ("D1", "ACTIVE_RESULTS"): "C01-ACTIVE",
        ("D2", "PRE_ELECTION"): "C02-PRE",
        ("D2", "ACTIVE_RESULTS"): "C02-ACTIVE",
        ("D4", "PRE_ELECTION"): "C04-PRE",
        ("D4", "ACTIVE_RESULTS"): "C04-ACTIVE",
    }

    residual_pools_by_regime: dict[str, list[np.ndarray]] = {}
    offsets_by_key: dict[tuple[str, str], np.ndarray] = {}
    for regime in REGIMES:
        d1_models = models.get((regime, "D1"))
        if d1_models is not None:
            residual_pools_by_regime[regime] = discovery_residual_blocks(
                discovery_frames, regime, d1_models[0]
            )
        for hypothesis in ("D2", "D4"):
            pair = models.get((regime, hypothesis))
            if pair is not None:
                offsets_by_key[(regime, hypothesis)] = discovery_common_offsets(
                    discovery_frames, regime, hypothesis, pair[0]
                )

    for regime in REGIMES:
        for hypothesis in ("D1", "D2", "D4"):
            slot = slot_map[(hypothesis, regime)]
            pair = models.get((regime, hypothesis))
            if pair is None:
                event_results.append({
                    "event": "ALL",
                    "regime": regime,
                    "hypothesis": hypothesis,
                    "slot": slot,
                    "status": "INCONCLUSIVE_DISCOVERY_MODEL_UNAVAILABLE",
                })
                continue
            baseline, challenger = pair
            event_ps: list[float] = []
            for event in CHALLENGE:
                frame = challenge_by_event[event]["frames"][regime]
                metrics, rows = evaluate_event(
                    frame, hypothesis, baseline, challenger
                )
                record = {
                    "event": event,
                    "regime": regime,
                    "hypothesis": hypothesis,
                    "slot": slot,
                    **metrics,
                }
                if metrics.get("status") != "OK":
                    event_results.append(record)
                    continue

                if hypothesis == "D1":
                    null = d1_common_async_null(
                        rows.reset_index(drop=True),
                        baseline,
                        challenger,
                        residual_pools_by_regime.get(regime, []),
                        f"{event}|{regime}|D1|N2",
                    )
                    p_score = null.get("p_score_expected_positive")
                else:
                    null = sequential_hazard_null(
                        rows.reset_index(drop=True),
                        hypothesis,
                        baseline,
                        challenger,
                        offsets_by_key[(regime, hypothesis)],
                        challenge_by_event[event]["initial_changes"][regime],
                        f"{event}|{regime}|{hypothesis}|N2",
                    )
                    p_score = null.get("p_score_expected_sign")

                null_record = {
                    "event": event,
                    "regime": regime,
                    "hypothesis": hypothesis,
                    "slot": slot,
                    **null,
                }
                null_results.append(null_record)
                p_gain = null.get("p_predictive_gain")
                if (
                    null.get("status") == "OK"
                    and p_score is not None
                    and p_gain is not None
                ):
                    event_p = max(float(p_score), float(p_gain))
                    if not metrics.get("sign_pass") or not metrics.get("gain_pass"):
                        event_p = 1.0
                    event_ps.append(event_p)
                    slot_testable_events[slot].append(event)
                    slot_event_p[slot][event] = event_p
                    record["intersection_union_event_p"] = event_p
                    record["n2_survival_unadjusted"] = bool(event_p <= 0.05)
                else:
                    record["intersection_union_event_p"] = 1.0
                    record["n2_survival_unadjusted"] = False
                event_results.append(record)

            if event_ps:
                slot_p[slot] = max(event_ps)

    # C03 is frozen dormant and retains both p=1 multiplicity slots.
    slot_p["C03-PRE"] = 1.0
    slot_p["C03-ACTIVE"] = 1.0

    holm = holm_adjust(slot_p)

    for regime in REGIMES:
        for hypothesis, controls in control_spec.items():
            for control in controls:
                pair = control_models.get((regime, hypothesis, control))
                for event in CHALLENGE:
                    frame = challenge_by_event[event]["frames"][regime]
                    metrics = evaluate_control(
                        frame,
                        hypothesis,
                        control,
                        pair[0] if pair else None,
                        pair[1] if pair else None,
                    )
                    control_results.append({
                        "event": event,
                        "regime": regime,
                        "hypothesis": hypothesis,
                        "control": control,
                        **metrics,
                    })

    slot_rows: list[dict[str, Any]] = []
    for slot in MULTIPLICITY_SLOTS:
        result = holm[slot]
        slot_rows.append({
            "slot": slot,
            "p_raw_intersection_union_replication": slot_p[slot],
            "p_holm": result["p_holm"],
            "rejected_holm_0_05": result["reject"],
            "testable_events": "|".join(slot_testable_events[slot]),
            "event_p_values": json.dumps(slot_event_p[slot], sort_keys=True),
            "dormant": slot.startswith("C03"),
        })

    write_csv(WORK / "challenge_event_results.csv", event_results)
    write_csv(WORK / "null_diagnostics.csv", null_results)
    write_csv(WORK / "negative_controls.csv", control_results)
    write_csv(WORK / "holm_family_results.csv", slot_rows)
    write_csv(WORK / "horizon_diagnostics.csv", horizon_rows)
    write_csv(WORK / "bid_ask_decomposition.csv", motion_rows)


    def slot_supported(slot: str, hypothesis: str) -> bool:
        if holm[slot]["reject"] is not True:
            return False
        if len(slot_testable_events[slot]) < 2:
            return False
        pair = models.get(("PRE_ELECTION", hypothesis))
        if pair is None:
            return False
        expected = model_spec(hypothesis)[4]
        if float(pair[1]["coef"][-1]) * expected <= 0:
            return False
        return all(value <= 0.05 for value in slot_event_p[slot].values())

    d1_supported = slot_supported("C01-PRE", "D1")
    d2_supported = slot_supported("C02-PRE", "D2")
    d1_testable = len(slot_testable_events["C01-PRE"]) >= 2
    d2_testable = len(slot_testable_events["C02-PRE"]) >= 2

    primary_by_event = {
        row["event"]: row
        for row in event_results
        if row.get("regime") == "PRE_ELECTION"
        and row.get("hypothesis") == "D2"
        and row.get("status") == "OK"
    }
    raw_by_event = {
        row["event"]: row
        for row in control_results
        if row.get("regime") == "PRE_ELECTION"
        and row.get("hypothesis") == "D2"
        and row.get("control") == "z_d2_raw"
        and row.get("status") == "OK"
    }
    raw_dominant_events = [
        event for event in sorted(set(primary_by_event) & set(raw_by_event))
        if raw_by_event[event].get("loss_gain") is not None
        and primary_by_event[event].get("loss_gain") is not None
        and float(raw_by_event[event]["loss_gain"])
            > max(0.0, float(primary_by_event[event]["loss_gain"]))
    ]
    apparent_genuine_timing = any(
        row.get("loss_gain") is not None
        and float(row["loss_gain"]) > 0
        and row.get("sign_pass") is True
        for row in primary_by_event.values()
    )
    observation_process_explains = (
        not d2_supported
        and apparent_genuine_timing
        and len(raw_dominant_events) >= 2
    )

    if d1_supported and d2_supported:
        disposition = "BOTH_STATE_DEPENDENT_MECHANISMS_SUPPORTED"
    elif d1_supported:
        disposition = "CONDITIONAL_MAGNITUDE_RESPONSE_SUPPORTED"
    elif d2_supported:
        disposition = "CONDITIONAL_QUOTE_RENEWAL_SUPPORTED"
    elif observation_process_explains:
        disposition = "OBSERVATION_PROCESS_EXPLAINS_RESULT"
    elif d1_testable and d2_testable:
        disposition = "NO_CONDITIONAL_EDGE"
    else:
        disposition = "INCONCLUSIVE"

    def active_summary(hypothesis: str, slot: str) -> str:
        testable = slot_testable_events[slot]
        if not testable:
            return "ACTIVE_NOT_TESTABLE"
        if holm[slot]["reject"]:
            return "ACTIVE_SUPPORTS_SAME_MECHANISM"
        positives = [
            row for row in event_results
            if row.get("regime") == "ACTIVE_RESULTS"
            and row.get("hypothesis") == hypothesis
            and row.get("status") == "OK"
            and row.get("sign_pass") is True
            and row.get("gain_pass") is True
        ]
        return (
            "ACTIVE_DIRECTIONALLY_COMPATIBLE_BUT_NOT_HOLM_SUPPORTED"
            if positives else "ACTIVE_DOES_NOT_SUPPORT"
        )

    c04_status = {
        row["regime"]: row["status"]
        for row in activation_rows
        if row["hypothesis"] == "D4_C04"
    }
    report = {
        "experiment_id": "EXPERIMENT-004C-D",
        "disposition": disposition,
        "d1_age_conditioned_magnitude_survived": d1_supported,
        "d2_genuine_quote_renewal_survived": d2_supported,
        "d1_testable_events_pre": slot_testable_events["C01-PRE"],
        "d2_testable_events_pre": slot_testable_events["C02-PRE"],
        "d1_realistic_null_holm_reject": bool(holm["C01-PRE"]["reject"]),
        "d2_realistic_null_holm_reject": bool(holm["C02-PRE"]["reject"]),
        "raw_record_dominant_events": raw_dominant_events,
        "effects_measured_on_genuine_bbo_changes": True,
        "replication_rule": (
            "expected sign and positive frozen predictive gain in every "
            "predesignated testable event; row pooling is not replication"
        ),
        "active_d1": active_summary("D1", "C01-ACTIVE"),
        "active_d2": active_summary("D2", "C02-ACTIVE"),
        "c03_status": "DORMANT_GRAPH_SUPPORT_FAIL",
        "c04_status": c04_status,
        "surviving_prediction_type": (
            "magnitude_and_timing" if d1_supported and d2_supported
            else "magnitude" if d1_supported
            else "timing" if d2_supported
            else "none"
        ),
        "must_not_infer": [
            "no election-direction forecast was tested",
            "no direct trading P&L or execution edge was tested",
            "ACTIVE cannot rescue a failed PRE primary",
            "challenge events were sealed from 004B but were previously exposed to EXPERIMENT-003",
            "row count is not independent election replication",
        ],
        "provenance": {
            "branch": "experiment/004c-d-conditional-response-renewal",
            "terminal_freeze_sha": FREEZE_SHA,
            "implementation_sha": code_manifest["implementation_commit"],
            "preregistration_sha256": PREREG_SHA,
            "market_registry_sha256": REGISTRY_SHA,
            "astra_sha256": ASTRA_SHA,
            "data001_sha256": DATA001_SHA,
            "kaggle_namespace": "004c_d_conditional_response",
        },
        "holm_family": holm,
    }
    (WORK / "research_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )


    report_lines = [
        "# EXPERIMENT-004C-D — Final Research Report",
        "",
        f"**Disposition:** {disposition}",
        "",
        "## Confirmatory PRE results",
        "",
        f"- D1 age-conditioned magnitude response survived: **{d1_supported}**.",
        f"- D2 genuine quote-renewal timing survived: **{d2_supported}**.",
        "- D1 PRE testable challenge events: "
            + (", ".join(slot_testable_events["C01-PRE"]) or "none") + ".",
        "- D2 PRE testable challenge events: "
            + (", ".join(slot_testable_events["C02-PRE"]) or "none") + ".",
        f"- D1 Holm p: {holm['C01-PRE']['p_holm']}.",
        f"- D2 Holm p: {holm['C02-PRE']['p_holm']}.",
        "",
        "## Observation-process falsification",
        "",
        "- Raw-record placebo dominates genuine renewal in: "
            + (", ".join(raw_dominant_events) or "none") + ".",
        "- Confirmatory D2 uses genuine bid/ask state changes, never raw record rows.",
        "- Missing/censored target intervals are unavailable, not zero-renewal labels.",
        "",
        "## Secondary status",
        "",
        f"- ACTIVE D1: {report['active_d1']}.",
        f"- ACTIVE D2: {report['active_d2']}.",
        "- C03 remains dormant under its frozen graph support failure and retains both Holm slots.",
        "- C04 activation: " + json.dumps(c04_status, sort_keys=True) + ".",
        "- 60s/120s and first-renewal latency outputs are diagnostic only.",
        "",
        "## Interpretation",
        "",
        "- Surviving prediction type: " + report["surviving_prediction_type"] + ".",
        "- D1, if supported, predicts adjustment magnitude, not direction.",
        "- D2, if supported, predicts renewal timing, not price direction.",
        "- No P&L, execution, or election-direction claim is licensed.",
        "",
        "## Provenance",
        "",
        "- Branch: experiment/004c-d-conditional-response-renewal.",
        f"- Terminal preregistration freeze: {FREEZE_SHA}.",
        f"- Implementation commit: {code_manifest['implementation_commit']}.",
        f"- Preregistration SHA-256: {PREREG_SHA}.",
        f"- Astra SHA-256: {ASTRA_SHA}.",
        f"- DATA-001 manifest SHA-256: {DATA001_SHA}.",
    ]
    (WORK / "FINAL_RESEARCH_REPORT.md").write_text(
        "\n".join(report_lines) + "\n"
    )

    handoff_lines = [
        "# MASTER Handoff — EXPERIMENT-004C-D",
        "",
        "Formal disposition: " + disposition,
        "",
        "1. D1 age-conditioned magnitude response: "
            + ("SURVIVED." if d1_supported else "DID NOT ACHIEVE SUPPORT."),
        "2. D2 genuine quote-renewal timing: "
            + ("SURVIVED." if d2_supported else "DID NOT ACHIEVE SUPPORT."),
        "3. Realistic null: "
            + f"D1 Holm reject={holm['C01-PRE']['reject']}; "
            + f"D2 Holm reject={holm['C02-PRE']['reject']}.",
        "4. Genuine vs raw activity: raw-record-dominant events="
            + (", ".join(raw_dominant_events) or "none")
            + "; confirmatory D2 always uses genuine BBO changes.",
        "5. Replication: D1 testable PRE events="
            + (", ".join(slot_testable_events["C01-PRE"]) or "none")
            + "; D2 testable PRE events="
            + (", ".join(slot_testable_events["C02-PRE"]) or "none")
            + ".",
        "6. ACTIVE: D1=" + report["active_d1"]
            + "; D2=" + report["active_d2"] + ".",
        "7. C03/C04: C03 dormant by frozen graph gate; C04="
            + json.dumps(c04_status, sort_keys=True) + ".",
        "8. What survives predicts: " + report["surviving_prediction_type"] + ".",
        "9. Do not infer direction, election forecasting, execution alpha, or P&L.",
        "10. Provenance: branch experiment/004c-d-conditional-response-renewal; "
            + f"freeze {FREEZE_SHA}; implementation {code_manifest['implementation_commit']}; "
            + "Kaggle namespace 004c_d_conditional_response; exact output hashes in "
            + "artifact_hashes.json and kaggle_run_summary.json.",
        "",
        "## Canonical artifacts",
        "",
        "- run_manifest.json",
        "- challenge_access_provenance.json",
        "- observation_exposure_audit.csv",
        "- feature_support_audit.csv",
        "- model_coefficients.csv",
        "- challenge_event_results.csv",
        "- null_diagnostics.csv",
        "- negative_controls.csv",
        "- holm_family_results.csv",
        "- secondary_activation_status.csv",
        "- horizon_diagnostics.csv",
        "- bid_ask_decomposition.csv",
        "- research_report.json",
        "- FINAL_RESEARCH_REPORT.md",
    ]
    (WORK / "MASTER_HANDOFF_004C_D.md").write_text(
        "\n".join(handoff_lines) + "\n"
    )

    artifact_hashes = {
        path.name: sha256(path)
        for path in sorted(WORK.iterdir())
        if path.is_file()
        and path.name not in ("artifact_hashes.json", "kaggle_run_summary.json")
    }
    (WORK / "artifact_hashes.json").write_text(
        json.dumps(artifact_hashes, indent=2, sort_keys=True) + "\n"
    )
    summary = {
        "phase": "complete",
        "experiment_id": "EXPERIMENT-004C-D",
        "disposition": disposition,
        "terminal_freeze_commit": FREEZE_SHA,
        "implementation_commit": code_manifest["implementation_commit"],
        "data001_manifest_sha256": DATA001_SHA,
        "preregistration_sha256": PREREG_SHA,
        "null_draws": NULL_DRAWS,
        "outputs": {
            **artifact_hashes,
            "artifact_hashes.json": sha256(WORK / "artifact_hashes.json"),
        },
    }
    (WORK / "kaggle_run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
