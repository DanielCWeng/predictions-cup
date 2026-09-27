# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/004c_a_freshness")
WORK.mkdir(parents=True, exist_ok=True)

FREEZE_SHA = "4aafab4bd77d58fd82a510fa2b95842689f49ba2"
DATA001_SHA = "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4"
PRIMARY_H = 30
SECONDARY_H = (60, 120)
STEP = 30
FRESHNESS = 300
NULL_DRAWS = 250
MASTER_SEED = 202609270041
MIN_EDGE_ROWS = 30
MIN_NULL_RETAIN = 0.80
MIN_NULL_EVENT_ROWS = 30
UTC_US = pa.timestamp("us", tz="UTC")

BASE_NAMES = (
    "target_midpoint",
    "target_return_30s",
    "target_abs_return_30s",
    "target_quote_age_seconds",
    "target_time_since_any_bbo_update_seconds",
    "target_bbo_update_count_30s",
    "target_spread",
    "target_log1p_depth_total",
    "target_depth_missing",
    "target_fill_count_30s",
    "target_depth_update_count_30s",
    "common_excl_pair_return_30s",
    "common_excl_pair_abs_return_30s",
    "common_excl_pair_bbo_update_count_30s",
    "common_excl_pair_fill_count_30s",
    "common_excl_pair_depth_update_count_30s",
)
SRC_PRIMITIVE_NAMES = (
    "source_return_30s",
    "source_abs_return_30s",
    "source_quote_age_seconds",
    "source_time_since_any_bbo_update_seconds",
    "source_bbo_update_count_30s",
    "source_fill_count_30s",
    "source_depth_update_count_30s",
    "source_spread",
    "source_log1p_depth_total",
    "source_depth_missing",
)
CHALLENGER_NAMES = BASE_NAMES + (
    "source_return_30s",
    "source_abs_return_30s",
    "source_quote_age_seconds",
    "relative_target_minus_source_quote_age_seconds",
    "source_time_since_any_bbo_update_seconds",
    "source_bbo_update_count_30s",
    "source_fill_count_30s",
    "source_depth_update_count_30s",
    "source_spread",
    "source_log1p_depth_total",
    "source_depth_missing",
    "source_return_30s_x_relative_staleness_scaled",
)

DISCOVERY = {"hungary_election", "peru_first_round"}
CHALLENGE = {"colombia_first_round", "peru_runoff", "colombia_runoff"}
REGIMES = ("PRE_ELECTION", "ACTIVE_RESULTS")
STOCH_NULLS = ("NULL_A_CIRCULAR_SHIFT", "NULL_B_BLOCK_TIME_PERMUTATION")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one(name: str) -> Path:
    matches = sorted(INPUT.rglob(name))
    if len(matches) != 1:
        raise SystemExit(f"expected one {name}, got {matches}")
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
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def deterministic_seed(component: str) -> int:
    digest = hashlib.sha256(f"{MASTER_SEED}|{component}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def load_code_bundle() -> tuple[Path, dict[str, Any]]:
    manifest_path = one("code_manifest.json")
    code_root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["freeze_commit"] != FREEZE_SHA:
        raise RuntimeError("code dataset is not bound to the terminal preregistration freeze")
    for name, expected in manifest["files"].items():
        path = code_root / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"code dataset hash mismatch: {name}")
    sys.path.insert(0, str(code_root / "predictions_cup_004c_a.zip"))
    return code_root, manifest


def build_harnesses(code_manifest: dict[str, Any]) -> dict[str, Any]:
    from predictions_cup.learning.evaluation_harness import ResearchEvaluationHarness
    from predictions_cup.learning.research_spec import (
        BootstrapProtocol,
        DatasetVersion,
        EventBootstrapWeighting,
        EvidencePolicy,
        FDRProtocol,
        ResearchEvaluationSpec,
        SplitMethod,
        StabilityProtocol,
    )

    dataset = DatasetVersion(
        dataset_id="DATA-001",
        schema_version="1",
        manifest_sha256=DATA001_SHA,
        source_version="accepted-historical-corpus",
    )
    hypothesis_ids = tuple(
        f"{regime}|{null_name}" for regime in REGIMES for null_name in STOCH_NULLS
    )
    targets = {
        "A1": ("UPDATE_HAZARD_30S", (timedelta(seconds=30),)),
        "A2": ("FIRST_UPDATE_MARK_30S", (timedelta(seconds=30),)),
        "A3": (
            "FULL_GRID_TARGET_STATE_CHANGE",
            tuple(timedelta(seconds=x) for x in (30, 60, 120)),
        ),
    }
    harnesses: dict[str, Any] = {}
    for family, (target, horizons) in targets.items():
        spec = ResearchEvaluationSpec(
            experiment_id="EXPERIMENT-004C-A",
            hypothesis_family=family,
            economic_mechanism=(
                "incremental source information beyond target history, freshness, "
                "update timing, common-event movement and activity"
            ),
            dataset=dataset,
            feature_set_id="004C_A_BASELINE_CHALLENGER_V1",
            feature_availability_rule="information_time <= decision_time",
            target=target,
            target_horizons=horizons,
            market_universe=(),
            event_universe=tuple(sorted(DISCOVERY | CHALLENGE)),
            event_family_universe=("COL_2026", "HUN_2026", "PER_2026"),
            split_method=SplitMethod.CHRONOLOGICAL_EVENT_HOLDOUT,
            walk_forward=None,
            fdr=FDRProtocol(
                family_id=f"004C_A_{family}",
                alpha=Decimal("0.05"),
                hypothesis_ids=hypothesis_ids,
            ),
            bootstrap=BootstrapProtocol(
                method="moving_block",
                draws=5000,
                block_size=10,
                event_weighting=EventBootstrapWeighting.EQUAL_EVENT,
            ),
            stability=StabilityProtocol(tolerance=Decimal("0.10")),
            disposition_policy=EvidencePolicy(
                require_statistical_tests=True,
                require_fdr=True,
                require_bootstrap=True,
                require_stability=False,
                require_negative_controls=True,
                require_ablations=False,
                require_execution_stress=False,
                min_fdr_rejections=1,
                require_nonisolated_stability=False,
                require_ablations_pass=False,
                require_execution_stresses_pass=False,
            ),
            purge=True,
            embargo=timedelta(seconds=30),
            parameter_grid=(("ridge_penalty", ("0.5", "1.0", "2.0")),),
            expected_failure_condition=(
                "controlled source information does not improve 30s OOS forecast "
                "or timing/source falsification controls explain the apparent effect"
            ),
        )
        harnesses[family] = ResearchEvaluationHarness(
            spec, code_manifest["implementation_commit"]
        )
    return harnesses


def verify_data001(corpus: Path, manifest: dict[str, Any], events: set[str]) -> dict[str, Any]:
    records = {row["path"]: row for row in manifest["output_files"]}
    checked = 0
    rows = 0
    by_event: dict[str, int] = {}
    for event in sorted(events):
        root = corpus / "schema_version=1" / event
        event_checked = 0
        for path in sorted(root.rglob("*.parquet")):
            rel = str(path.relative_to(corpus / "schema_version=1"))
            rec = records.get(rel)
            if rec is None:
                raise RuntimeError(f"DATA-001 manifest missing {rel}")
            if sha256(path) != rec["sha256"]:
                raise RuntimeError(f"DATA-001 file hash mismatch: {rel}")
            checked += 1
            event_checked += 1
            rows += int(rec["rows"])
        by_event[event] = event_checked
    return {"files_checked": checked, "manifest_rows_checked": rows, "files_by_event": by_event}


def event_root(corpus: Path, event: str) -> Path:
    return corpus / "schema_version=1" / event


def parquet_files(root: Path) -> list[str]:
    return [str(path) for path in sorted(root.rglob("*.parquet"))]


def table_for(
    root: Path,
    columns: list[str],
    tokens: list[str],
    time_col: str,
    start: datetime,
    end: datetime,
) -> pa.Table:
    files = parquet_files(root)
    if not files:
        return pa.table({name: [] for name in columns})
    dataset = pads.dataset(files, format="parquet")
    expr = pads.field("token_id").isin(tokens)
    expr = expr & (pads.field(time_col) >= pa.scalar(start, UTC_US))
    expr = expr & (pads.field(time_col) < pa.scalar(end, UTC_US))
    return dataset.to_table(columns=columns, filter=expr)


def quote_series(
    corpus: Path,
    event: str,
    tokens: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, dict[str, np.ndarray]]:
    table = table_for(
        event_root(corpus, event) / "books" / "book_changes",
        ["token_id", "observed_at", "best_bid", "best_ask"],
        tokens,
        "observed_at",
        start,
        end,
    )
    if table.num_rows == 0:
        return {}
    table = pa.table(
        {
            "token_id": table["token_id"],
            "observed_at": table["observed_at"],
            "best_bid": pc.cast(table["best_bid"], pa.float64()),
            "best_ask": pc.cast(table["best_ask"], pa.float64()),
        }
    )
    order = pc.sort_indices(
        table,
        sort_keys=[
            ("token_id", "ascending"),
            ("observed_at", "ascending"),
            ("best_bid", "ascending"),
            ("best_ask", "ascending"),
        ],
    )
    rows = pc.take(table, order).to_pylist()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["token_id"])].append(row)
    out: dict[str, dict[str, np.ndarray]] = {}
    for token, values in grouped.items():
        times: list[int] = []
        bids: list[float] = []
        asks: list[float] = []
        i = 0
        while i < len(values):
            j = i + 1
            while j < len(values) and values[j]["observed_at"] == values[i]["observed_at"]:
                j += 1
            states = {
                (float(v["best_bid"]), float(v["best_ask"]))
                for v in values[i:j]
                if v["best_bid"] is not None and v["best_ask"] is not None
            }
            times.append(ns(values[i]["observed_at"]))
            if len(states) == 1:
                bid, ask = next(iter(states))
                if 0 < bid <= ask < 1:
                    bids.append(bid)
                    asks.append(ask)
                else:
                    bids.append(np.nan)
                    asks.append(np.nan)
            else:
                bids.append(np.nan)
                asks.append(np.nan)
            i = j
        bid = np.asarray(bids, dtype=float)
        ask = np.asarray(asks, dtype=float)
        mid = (bid + ask) / 2.0
        out[token] = {
            "t": np.asarray(times, dtype=np.int64),
            "bid": bid,
            "ask": ask,
            "mid": mid,
        }
    return out


def sample_quote(
    series: dict[str, np.ndarray] | None, query_ns: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(query_ns)
    if series is None or len(series["t"]) == 0:
        nan = np.full(n, np.nan)
        return nan.copy(), nan.copy(), nan.copy(), nan.copy(), nan.copy()
    times = series["t"]
    idx = np.searchsorted(times, query_ns, side="right") - 1
    has = idx >= 0
    safe = np.maximum(idx, 0)
    age = (query_ns - times[safe]) / 1e9
    any_age = age.copy()
    valid = has & (age >= 0) & (age <= FRESHNESS)
    mid = series["mid"][safe].copy()
    bid = series["bid"][safe].copy()
    ask = series["ask"][safe].copy()
    valid &= np.isfinite(mid) & np.isfinite(bid) & np.isfinite(ask)
    for arr in (mid, bid, ask, age, any_age):
        arr[~valid] = np.nan
    return mid, bid, ask, age, any_age


def binned_activity_and_depth(
    corpus: Path,
    event: str,
    tokens: list[str],
    start: datetime,
    end: datetime,
    bins: int,
) -> dict[str, dict[str, np.ndarray]]:
    base = ns(start)
    step_ns = STEP * 1_000_000_000
    out = {
        token: {
            "bbo": np.zeros(bins, dtype=float),
            "fill": np.zeros(bins, dtype=float),
            "depth_updates": np.zeros(bins, dtype=float),
            "logdepth": np.full(bins, np.nan),
        }
        for token in tokens
    }

    changes = table_for(
        event_root(corpus, event) / "books" / "book_changes",
        ["token_id", "observed_at"],
        tokens,
        "observed_at",
        start,
        end,
    )
    for row in changes.to_pylist():
        k = (ns(row["observed_at"]) - base) // step_ns
        if 0 <= k < bins:
            out[str(row["token_id"])]["bbo"][int(k)] += 1.0

    fills = table_for(
        event_root(corpus, event) / "fills",
        ["token_id", "observed_at"],
        tokens,
        "observed_at",
        start,
        end,
    )
    for row in fills.to_pylist():
        k = (ns(row["observed_at"]) - base) // step_ns
        if 0 <= k < bins:
            out[str(row["token_id"])]["fill"][int(k)] += 1.0

    depth = table_for(
        event_root(corpus, event) / "books" / "depth_snapshots",
        ["token_id", "recorded_at", "best_bid", "best_ask", "bids", "asks"],
        tokens,
        "recorded_at",
        start,
        end,
    )
    rows = sorted(
        depth.to_pylist(),
        key=lambda r: (
            str(r["token_id"]),
            r["recorded_at"],
            str(r["best_bid"]),
            str(r["best_ask"]),
            repr(r["bids"]),
            repr(r["asks"]),
        ),
    )
    i = 0
    while i < len(rows):
        j = i + 1
        while (
            j < len(rows)
            and rows[j]["token_id"] == rows[i]["token_id"]
            and rows[j]["recorded_at"] == rows[i]["recorded_at"]
        ):
            j += 1
        token = str(rows[i]["token_id"])
        k = (ns(rows[i]["recorded_at"]) - base) // step_ns
        if 0 <= k < bins:
            out[token]["depth_updates"][int(k)] += 1.0
            fingerprints = {
                (
                    str(v["best_bid"]),
                    str(v["best_ask"]),
                    repr(v["bids"]),
                    repr(v["asks"]),
                )
                for v in rows[i:j]
            }
            if len(fingerprints) == 1:
                row = rows[i]
                bids = row["bids"] or []
                asks = row["asks"] or []
                if bids and asks:
                    try:
                        bid = float(row["best_bid"])
                        ask = float(row["best_ask"])
                        qb = float(bids[0]["size"])
                        qa = float(asks[0]["size"])
                        if 0 < bid <= ask < 1 and qb >= 0 and qa >= 0:
                            out[token]["logdepth"][int(k)] = math.log1p(qb + qa)
                    except (TypeError, ValueError, KeyError):
                        pass
        i = j
    return out


def windows_from_defs(defs: dict[str, Any]) -> dict[tuple[str, str], tuple[datetime, datetime]]:
    out: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for event in defs["events"]:
        event_id = event["regime_id"]
        for regime in event["regimes"]:
            if regime["name"] in REGIMES:
                out[(event_id, regime["name"])] = (
                    dt(regime["start_utc"]),
                    dt(regime["end_utc"]),
                )
    return out


def matched_control_registry(
    pair_rows: list[dict[str, str]], universe_rows: list[dict[str, str]]
) -> tuple[dict[str, str], list[dict[str, str]]]:
    universe_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in universe_rows:
        universe_by_key[(row["event"], row["regime"])].append(row)
    adjacency: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in pair_rows:
        key_s = (row["event"], row["regime"], row["source_condition_id"])
        key_t = (row["event"], row["regime"], row["target_condition_id"])
        adjacency[key_s].add(row["target_condition_id"])
        adjacency[key_t].add(row["source_condition_id"])

    mapping: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    for pair in pair_rows:
        edge = edge_id(pair)
        event = pair["event"]
        regime = pair["regime"]
        target = pair["target_condition_id"]
        source = pair["source_condition_id"]
        candidates = [
            row
            for row in universe_by_key[(event, regime)]
            if row["condition_id"] not in {target, source}
            and row["condition_id"] not in adjacency[(event, regime, target)]
        ]
        same_family = [
            row for row in candidates if row["market_family"] == pair["source_market_family"]
        ]
        pool = same_family or candidates
        chosen = min(pool, key=lambda r: r["condition_id"]) if pool else None
        mapping[edge] = "" if chosen is None else chosen["canonical_token_id"]
        rows.append(
            {
                "event": event,
                "regime": regime,
                "edge_id": edge,
                "source_condition_id": source,
                "target_condition_id": target,
                "control_condition_id": "" if chosen is None else chosen["condition_id"],
                "control_token_id": "" if chosen is None else chosen["canonical_token_id"],
                "same_source_family": str(bool(same_family)).lower(),
                "matching_rule": (
                    "same_source_family_then_lexical"
                    if chosen is not None and same_family
                    else "lexical_unrelated_fallback" if chosen is not None else "UNAVAILABLE"
                ),
            }
        )
    return mapping, rows


def edge_id(pair: dict[str, str]) -> str:
    return f'{pair["source_condition_id"]}>{pair["target_condition_id"]}'


def safe_nanmean(matrix: np.ndarray, minimum: int) -> np.ndarray:
    count = np.sum(np.isfinite(matrix), axis=1)
    total = np.nansum(matrix, axis=1)
    out = np.full(matrix.shape[0], np.nan)
    ok = count >= minimum
    out[ok] = total[ok] / count[ok]
    return out


def build_event_data(
    corpus: Path,
    event: str,
    regime: str,
    pairs: list[dict[str, str]],
    universe: list[dict[str, str]],
    start: datetime,
    end: datetime,
    control_tokens: dict[str, str],
) -> dict[str, Any]:
    tokens = sorted({row["canonical_token_id"] for row in universe})
    condition_to_token = {row["condition_id"]: row["canonical_token_id"] for row in universe}
    condition_to_family = {row["condition_id"]: row["market_family"] for row in universe}
    token_index = {token: i for i, token in enumerate(tokens)}
    step_ns = STEP * 1_000_000_000
    start_ns = ns(start)
    end_ns = ns(end)
    right = np.arange(start_ns + step_ns, end_ns + 1, step_ns, dtype=np.int64)
    right = right[right <= end_ns]
    q = right - 1
    bins = len(q)
    quotes = quote_series(corpus, event, tokens, start, end)
    activity = binned_activity_and_depth(corpus, event, tokens, start, end, bins)

    m = len(tokens)
    mid = np.full((bins, m), np.nan)
    ret = np.full((bins, m), np.nan)
    age = np.full((bins, m), np.nan)
    any_age = np.full((bins, m), np.nan)
    spread = np.full((bins, m), np.nan)
    bbo = np.zeros((bins, m), dtype=float)
    fill = np.zeros((bins, m), dtype=float)
    depth_updates = np.zeros((bins, m), dtype=float)
    logdepth = np.full((bins, m), np.nan)
    primitive_by_token: dict[str, np.ndarray] = {}

    for token, j in token_index.items():
        series = quotes.get(token)
        cur_mid, bid, ask, cur_age, cur_any = sample_quote(series, q)
        prev_mid, _, _, _, _ = sample_quote(series, q - step_ns)
        move = cur_mid - prev_mid
        mid[:, j] = cur_mid
        ret[:, j] = move
        age[:, j] = cur_age
        any_age[:, j] = cur_any
        spread[:, j] = ask - bid
        bbo[:, j] = activity[token]["bbo"]
        fill[:, j] = activity[token]["fill"]
        depth_updates[:, j] = activity[token]["depth_updates"]
        logdepth[:, j] = activity[token]["logdepth"]
        primitive_by_token[token] = np.column_stack(
            [
                move,
                np.abs(move),
                cur_age,
                cur_any,
                bbo[:, j],
                fill[:, j],
                depth_updates[:, j],
                spread[:, j],
                logdepth[:, j],
                (~np.isfinite(logdepth[:, j])).astype(float),
            ]
        )

    base_parts: list[np.ndarray] = []
    prim_parts: list[np.ndarray] = []
    time_parts: list[np.ndarray] = []
    grid_parts: list[np.ndarray] = []
    edge_parts: list[np.ndarray] = []
    source_parts: list[np.ndarray] = []
    control_parts: list[np.ndarray] = []
    y_parts: dict[int, list[np.ndarray]] = {30: [], 60: [], 120: []}
    hazard_parts: list[np.ndarray] = []
    mark_parts: list[np.ndarray] = []
    latency_parts: list[np.ndarray] = []

    for pair in pairs:
        s_token = pair["source_token_id"]
        t_token = pair["target_token_id"]
        s = token_index[s_token]
        t = token_index[t_token]
        other = [j for j in range(m) if j not in (s, t)]
        if len(other) < 2:
            continue

        common_ret = safe_nanmean(ret[:, other], minimum=2)
        common_abs = safe_nanmean(np.abs(ret[:, other]), minimum=2)
        common_bbo = np.mean(bbo[:, other], axis=1)
        common_fill = np.mean(fill[:, other], axis=1)
        common_depth = np.mean(depth_updates[:, other], axis=1)

        base = np.column_stack(
            [
                mid[:, t],
                ret[:, t],
                np.abs(ret[:, t]),
                age[:, t],
                any_age[:, t],
                bbo[:, t],
                spread[:, t],
                logdepth[:, t],
                (~np.isfinite(logdepth[:, t])).astype(float),
                fill[:, t],
                depth_updates[:, t],
                common_ret,
                common_abs,
                common_bbo,
                common_fill,
                common_depth,
            ]
        )
        prim = primitive_by_token[s_token]
        essential = np.all(
            np.isfinite(base[:, [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15]]),
            axis=1,
        )
        essential &= np.all(np.isfinite(prim[:, [0, 1, 2, 3, 4, 5, 6, 7, 9]]), axis=1)
        essential &= q + PRIMARY_H * 1_000_000_000 < end_ns
        idx = np.flatnonzero(essential)
        if len(idx) == 0:
            continue

        series = quotes.get(t_token)
        valid_update_t = (
            series["t"][np.isfinite(series["mid"])] if series is not None else np.asarray([], np.int64)
        )
        valid_update_mid = (
            series["mid"][np.isfinite(series["mid"])] if series is not None else np.asarray([], float)
        )
        next_idx = np.searchsorted(valid_update_t, q, side="right")
        has_next = next_idx < len(valid_update_t)
        safe_next = np.minimum(next_idx, max(0, len(valid_update_t) - 1))
        next_time = np.full(bins, np.nan)
        next_mid = np.full(bins, np.nan)
        if len(valid_update_t):
            next_time[has_next] = valid_update_t[safe_next[has_next]]
            next_mid[has_next] = valid_update_mid[safe_next[has_next]]
        within = has_next & (next_time <= q + PRIMARY_H * 1_000_000_000)
        hazard = within.astype(float)
        mark = np.full(bins, np.nan)
        mark[within] = next_mid[within] - mid[within, t]
        latency = np.full(bins, np.nan)
        latency[within] = (next_time[within] - q[within]) / 1e9

        y_by_h: dict[int, np.ndarray] = {}
        for horizon in (30, 60, 120):
            future_mid, _, _, _, _ = sample_quote(
                series, q + horizon * 1_000_000_000
            )
            y = future_mid - mid[:, t]
            y[q + horizon * 1_000_000_000 >= end_ns] = np.nan
            y_by_h[horizon] = y

        e = edge_id(pair)
        control = control_tokens.get(e, "")
        base_parts.append(base[idx])
        prim_parts.append(prim[idx])
        time_parts.append(q[idx])
        grid_parts.append(idx.astype(np.int64))
        edge_parts.append(np.asarray([e] * len(idx), dtype=object))
        source_parts.append(np.asarray([s_token] * len(idx), dtype=object))
        control_parts.append(np.asarray([control] * len(idx), dtype=object))
        for horizon in (30, 60, 120):
            y_parts[horizon].append(y_by_h[horizon][idx])
        hazard_parts.append(hazard[idx])
        mark_parts.append(mark[idx])
        latency_parts.append(latency[idx])

    if not base_parts:
        return {
            "event": event,
            "event_family": pairs[0]["event_family"] if pairs else "",
            "regime": regime,
            "base": np.empty((0, len(BASE_NAMES))),
            "prim": np.empty((0, len(SRC_PRIMITIVE_NAMES))),
            "times": np.asarray([], np.int64),
            "grid_idx": np.asarray([], np.int64),
            "edge": np.asarray([], object),
            "source_token": np.asarray([], object),
            "control_token": np.asarray([], object),
            "y": {h: np.asarray([], float) for h in (30, 60, 120)},
            "hazard": np.asarray([], float),
            "mark": np.asarray([], float),
            "latency": np.asarray([], float),
            "full_primitive": {},
            "grid_q": q,
        }

    needed = set(np.concatenate(source_parts).tolist()) | {
        value for value in np.concatenate(control_parts).tolist() if value
    }
    return {
        "event": event,
        "event_family": pairs[0]["event_family"],
        "regime": regime,
        "base": np.concatenate(base_parts).astype(float),
        "prim": np.concatenate(prim_parts).astype(float),
        "times": np.concatenate(time_parts),
        "grid_idx": np.concatenate(grid_parts),
        "edge": np.concatenate(edge_parts),
        "source_token": np.concatenate(source_parts),
        "control_token": np.concatenate(control_parts),
        "y": {h: np.concatenate(y_parts[h]) for h in (30, 60, 120)},
        "hazard": np.concatenate(hazard_parts),
        "mark": np.concatenate(mark_parts),
        "latency": np.concatenate(latency_parts),
        "full_primitive": {token: primitive_by_token[token] for token in needed},
        "grid_q": q,
        "condition_to_token": condition_to_token,
        "condition_to_family": condition_to_family,
    }


def challenger_matrix(base: np.ndarray, prim: np.ndarray) -> np.ndarray:
    relative = base[:, 3] - prim[:, 2]
    interaction = prim[:, 0] * relative / 300.0
    additions = np.column_stack(
        [
            prim[:, 0],
            prim[:, 1],
            prim[:, 2],
            relative,
            prim[:, 3],
            prim[:, 4],
            prim[:, 5],
            prim[:, 6],
            prim[:, 7],
            prim[:, 8],
            prim[:, 9],
            interaction,
        ]
    )
    return np.column_stack([base, additions])


def essential_primitive(prim: np.ndarray) -> np.ndarray:
    return np.all(np.isfinite(prim[:, [0, 1, 2, 3, 4, 5, 6, 7, 9]]), axis=1)


def edge_weights(edge: np.ndarray) -> np.ndarray:
    counts: dict[str, int] = defaultdict(int)
    for value in edge:
        counts[str(value)] += 1
    w = np.asarray([1.0 / counts[str(value)] for value in edge], dtype=float)
    if w.sum() <= 0:
        return np.ones(len(edge), dtype=float)
    return w * len(w) / w.sum()


def fit_preprocessor(x: np.ndarray, weights: np.ndarray) -> dict[str, np.ndarray]:
    med = np.zeros(x.shape[1], dtype=float)
    for j in range(x.shape[1]):
        finite = x[np.isfinite(x[:, j]), j]
        med[j] = float(np.median(finite)) if len(finite) else 0.0
    xi = np.where(np.isfinite(x), x, med)
    w = weights / weights.sum()
    mean = np.sum(xi * w[:, None], axis=0)
    var = np.sum(((xi - mean) ** 2) * w[:, None], axis=0)
    scale = np.sqrt(np.maximum(var, 1e-12))
    return {"median": med, "mean": mean, "scale": scale}


def apply_preprocessor(x: np.ndarray, prep: dict[str, np.ndarray]) -> np.ndarray:
    xi = np.where(np.isfinite(x), x, prep["median"])
    return (xi - prep["mean"]) / prep["scale"]


def fit_ridge(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray, penalty: float = 1.0
) -> dict[str, Any]:
    prep = fit_preprocessor(x, weights)
    z = apply_preprocessor(x, prep)
    design = np.column_stack([np.ones(len(z)), z])
    sw = np.sqrt(weights)
    weighted = design * sw[:, None]
    lhs = weighted.T @ weighted
    reg = np.eye(lhs.shape[0]) * penalty
    reg[0, 0] = 0.0
    rhs = weighted.T @ (y * sw)
    beta = np.linalg.solve(lhs + reg + np.eye(lhs.shape[0]) * 1e-12, rhs)
    return {"kind": "ridge", "prep": prep, "beta": beta, "penalty": penalty}


def fit_logistic(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray, penalty: float = 1.0
) -> dict[str, Any]:
    prep = fit_preprocessor(x, weights)
    z = apply_preprocessor(x, prep)
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    mean_y = float(np.average(y, weights=weights))
    mean_y = min(max(mean_y, 1e-6), 1 - 1e-6)
    beta[0] = math.log(mean_y / (1 - mean_y))
    reg = np.eye(design.shape[1]) * penalty
    reg[0, 0] = 0.0
    for _ in range(50):
        eta = np.clip(design @ beta, -35, 35)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = weights * p * (1 - p)
        hess = design.T @ (design * w[:, None]) + reg + np.eye(design.shape[1]) * 1e-10
        grad = design.T @ (weights * (p - y)) + reg @ beta
        delta = np.linalg.solve(hess, grad)
        beta -= delta
        if float(np.max(np.abs(delta))) < 1e-8:
            break
    return {"kind": "logistic", "prep": prep, "beta": beta, "penalty": penalty}


def predict(model: dict[str, Any], x: np.ndarray) -> np.ndarray:
    z = apply_preprocessor(x, model["prep"])
    design = np.column_stack([np.ones(len(z)), z])
    value = design @ model["beta"]
    if model["kind"] == "logistic":
        return 1.0 / (1.0 + np.exp(-np.clip(value, -35, 35)))
    return value


def safe_corr(x: np.ndarray, y: np.ndarray) -> float | None:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 30:
        return None
    a = x[mask]
    b = y[mask]
    if np.std(a) <= 1e-15 or np.std(b) <= 1e-15:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def split_masks(times: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(times) == 0:
        empty = np.zeros(0, dtype=bool)
        return empty, empty, empty
    lo = int(times.min())
    hi = int(times.max())
    span = hi - lo
    dev_start = lo + int(0.60 * span)
    hold_start = lo + int(0.80 * span)
    embargo_ns = 30 * 1_000_000_000
    horizon_ns = horizon * 1_000_000_000
    train_cut = min(dev_start - horizon_ns, dev_start - embargo_ns)
    dev_cut = min(hold_start - horizon_ns, hold_start - embargo_ns)
    train = times < train_cut
    development = (times >= dev_start) & (times < dev_cut)
    holdout = times >= hold_start
    return train, development, holdout


def concat_rows(
    datas: list[dict[str, Any]], label: str, horizon: int = 30
) -> dict[str, np.ndarray]:
    bases: list[np.ndarray] = []
    prims: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    times: list[np.ndarray] = []
    edges: list[np.ndarray] = []
    events: list[np.ndarray] = []
    families: list[np.ndarray] = []
    for data in datas:
        if label == "A3":
            y = data["y"][horizon]
        elif label == "A2":
            y = data["mark"]
        else:
            y = data["hazard"]
        mask = np.isfinite(y)
        if not mask.any():
            continue
        bases.append(data["base"][mask])
        prims.append(data["prim"][mask])
        ys.append(y[mask])
        times.append(data["times"][mask])
        edges.append(data["edge"][mask])
        events.append(np.asarray([data["event"]] * int(mask.sum()), dtype=object))
        families.append(np.asarray([data["event_family"]] * int(mask.sum()), dtype=object))
    if not bases:
        return {
            "base": np.empty((0, len(BASE_NAMES))),
            "prim": np.empty((0, len(SRC_PRIMITIVE_NAMES))),
            "y": np.asarray([], float),
            "times": np.asarray([], np.int64),
            "edge": np.asarray([], object),
            "event": np.asarray([], object),
            "family": np.asarray([], object),
        }
    return {
        "base": np.concatenate(bases),
        "prim": np.concatenate(prims),
        "y": np.concatenate(ys),
        "times": np.concatenate(times),
        "edge": np.concatenate(edges),
        "event": np.concatenate(events),
        "family": np.concatenate(families),
    }


def fit_pair_models(
    rows: dict[str, np.ndarray],
    task: str,
    penalty: float = 1.0,
    mask: np.ndarray | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    use = np.ones(len(rows["y"]), dtype=bool) if mask is None else mask.copy()
    y = rows["y"][use]
    base = rows["base"][use]
    challenger = challenger_matrix(base, rows["prim"][use])
    edge = rows["edge"][use]
    if len(y) == 0:
        raise ValueError("no training rows")
    weights = edge_weights(edge)
    if task == "A1":
        return (
            fit_logistic(base, y, weights, penalty),
            fit_logistic(challenger, y, weights, penalty),
        )
    return (
        fit_ridge(base, y, weights, penalty),
        fit_ridge(challenger, y, weights, penalty),
    )


def training_sufficient(rows: dict[str, np.ndarray], task: str, mask: np.ndarray) -> tuple[bool, str]:
    y = rows["y"][mask]
    edges = set(str(x) for x in rows["edge"][mask])
    minimum = 100 if task == "A2" else 200
    if len(y) < minimum:
        return False, f"ROWS_LT_{minimum}"
    if len(edges) < 2:
        return False, "DIRECTED_EDGES_LT_2"
    if task == "A1":
        positives = int(np.sum(y == 1))
        negatives = int(np.sum(y == 0))
        if positives < 20 or negatives < 20:
            return False, "HAZARD_CLASS_COUNT_LT_20"
    return True, "OK"


def edge_metrics(
    edge: np.ndarray,
    y: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    task: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for edge_id_value in sorted(set(str(x) for x in edge)):
        mask = np.asarray([str(x) == edge_id_value for x in edge])
        mask &= np.isfinite(y) & np.isfinite(p0) & np.isfinite(p1)
        n = int(mask.sum())
        if n < MIN_EDGE_ROWS:
            continue
        yy = y[mask]
        b = p0[mask]
        c = p1[mask]
        if task == "A1":
            eps = 1e-12
            brier_b = float(np.mean((yy - b) ** 2))
            brier_c = float(np.mean((yy - c) ** 2))
            ll_b = float(
                -np.mean(yy * np.log(np.clip(b, eps, 1 - eps)) + (1 - yy) * np.log(np.clip(1 - b, eps, 1 - eps)))
            )
            ll_c = float(
                -np.mean(yy * np.log(np.clip(c, eps, 1 - eps)) + (1 - yy) * np.log(np.clip(1 - c, eps, 1 - eps)))
            )
            rows.append(
                {
                    "edge_id": edge_id_value,
                    "n": n,
                    "baseline_primary": brier_b,
                    "challenger_primary": brier_c,
                    "delta_primary": brier_b - brier_c,
                    "baseline_secondary": ll_b,
                    "challenger_secondary": ll_c,
                    "delta_secondary": ll_b - ll_c,
                    "metric": "BRIER",
                }
            )
        else:
            mse_b = float(np.mean((yy - b) ** 2))
            mse_c = float(np.mean((yy - c) ** 2))
            rmse_b = math.sqrt(mse_b)
            rmse_c = math.sqrt(mse_c)
            mae_b = float(np.mean(np.abs(yy - b)))
            mae_c = float(np.mean(np.abs(yy - c)))
            rows.append(
                {
                    "edge_id": edge_id_value,
                    "n": n,
                    "baseline_primary": mse_b,
                    "challenger_primary": mse_c,
                    "delta_primary": mse_b - mse_c,
                    "baseline_secondary": mae_b,
                    "challenger_secondary": mae_c,
                    "delta_secondary": mae_b - mae_c,
                    "metric": "MSE",
                    "baseline_rmse": rmse_b,
                    "challenger_rmse": rmse_c,
                    "rmse_improvement": rmse_b - rmse_c,
                    "relative_mse_improvement": (
                        (mse_b - mse_c) / mse_b if mse_b > 0 else None
                    ),
                }
            )
    return rows


def event_summary(
    data: dict[str, Any],
    y: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    task: str,
    mask: np.ndarray | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    use = np.ones(len(y), dtype=bool) if mask is None else mask.copy()
    rows = edge_metrics(data["edge"][use], y[use], p0[use], p1[use], task)
    if not rows:
        return None, []
    numeric = [
        key
        for key in rows[0]
        if key not in {"edge_id", "metric", "n"}
        and isinstance(rows[0].get(key), (int, float))
    ]
    summary: dict[str, Any] = {
        "event": data["event"],
        "event_family": data["event_family"],
        "regime": data["regime"],
        "valid_edges": len(rows),
        "valid_rows": sum(int(row["n"]) for row in rows),
        "metric": rows[0]["metric"],
    }
    for key in numeric:
        vals = [float(row[key]) for row in rows if row.get(key) is not None]
        summary[key] = float(np.mean(vals)) if vals else None
    for row in rows:
        row["event"] = data["event"]
        row["event_family"] = data["event_family"]
        row["regime"] = data["regime"]
    return summary, rows


def aggregate_system(event_rows: list[dict[str, Any]], key: str) -> tuple[float | None, dict[str, float]]:
    by_family: dict[str, list[float]] = defaultdict(list)
    for row in event_rows:
        value = row.get(key)
        if value is not None:
            by_family[row["event_family"]].append(float(value))
    family_values = {
        family: float(np.mean(values)) for family, values in by_family.items() if values
    }
    if not family_values:
        return None, {}
    return float(np.mean(list(family_values.values()))), family_values


def source_null_primitive(
    data: dict[str, Any],
    null_name: str,
    draw: int,
) -> tuple[np.ndarray, np.ndarray]:
    out = np.full_like(data["prim"], np.nan)
    valid = np.zeros(len(out), dtype=bool)
    for token in sorted(set(str(x) for x in data["source_token"])):
        row_mask = np.asarray([str(x) == token for x in data["source_token"]])
        grid_idx = data["grid_idx"][row_mask]
        full = data["full_primitive"][token]
        t = len(full)
        if t <= 20:
            continue
        rng = np.random.default_rng(
            deterministic_seed(f'{data["event"]}|{data["regime"]}|{null_name}|{draw}|{token}')
        )
        if null_name == "NULL_A_CIRCULAR_SHIFT":
            shift = int(rng.integers(10, t - 9))
            transformed = np.roll(full, shift, axis=0)
        else:
            blocks = [(s, min(t, s + 10)) for s in range(0, t, 10)]
            order = rng.permutation(len(blocks))
            transformed = np.concatenate([full[s:e] for s, e in (blocks[k] for k in order)], axis=0)[:t]
        values = transformed[grid_idx]
        out[row_mask] = values
        valid[row_mask] = essential_primitive(values)
    return out, valid


def delayed_primitive(data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    out = np.full_like(data["prim"], np.nan)
    valid = np.zeros(len(out), dtype=bool)
    for token in sorted(set(str(x) for x in data["source_token"])):
        row_mask = np.asarray([str(x) == token for x in data["source_token"]])
        idx = data["grid_idx"][row_mask] - 10
        ok = idx >= 0
        values = np.full((int(row_mask.sum()), len(SRC_PRIMITIVE_NAMES)), np.nan)
        values[ok] = data["full_primitive"][token][idx[ok]]
        out[row_mask] = values
        valid[row_mask] = essential_primitive(values)
    return out, valid


def unrelated_primitive(data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    out = np.full_like(data["prim"], np.nan)
    valid = np.zeros(len(out), dtype=bool)
    for control in sorted(set(str(x) for x in data["control_token"] if str(x))):
        row_mask = np.asarray([str(x) == control for x in data["control_token"]])
        idx = data["grid_idx"][row_mask]
        values = data["full_primitive"][control][idx]
        out[row_mask] = values
        valid[row_mask] = essential_primitive(values)
    return out, valid


def null_event_stat(
    data: dict[str, Any],
    task: str,
    baseline_model: dict[str, Any],
    challenger_model: dict[str, Any],
    prim: np.ndarray,
    valid_source: np.ndarray,
    horizon: int = 30,
) -> tuple[float | None, int, float]:
    if task == "A3":
        y = data["y"][horizon]
    elif task == "A2":
        y = data["mark"]
    else:
        y = data["hazard"]
    valid_label = np.isfinite(y)
    observed_rows = int(valid_label.sum())
    mask = valid_label & valid_source
    retained = int(mask.sum())
    retention = retained / max(1, observed_rows)
    if retained < MIN_NULL_EVENT_ROWS or retention < MIN_NULL_RETAIN:
        return None, retained, retention
    p0 = predict(baseline_model, data["base"][mask])
    p1 = predict(challenger_model, challenger_matrix(data["base"][mask], prim[mask]))
    rows = edge_metrics(data["edge"][mask], y[mask], p0, p1, task)
    if len(rows) < 2:
        return None, retained, retention
    return float(np.mean([float(row["delta_primary"]) for row in rows])), retained, retention


def challenge_observed(
    challenge_data: list[dict[str, Any]],
    task: str,
    baseline_model: dict[str, Any],
    challenger_model: dict[str, Any],
    horizon: int = 30,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float | None, dict[str, float]]:
    events: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for data in challenge_data:
        y = data["y"][horizon] if task == "A3" else data["mark"] if task == "A2" else data["hazard"]
        mask = np.isfinite(y)
        p0 = np.full(len(y), np.nan)
        p1 = np.full(len(y), np.nan)
        if mask.any():
            p0[mask] = predict(baseline_model, data["base"][mask])
            p1[mask] = predict(
                challenger_model, challenger_matrix(data["base"][mask], data["prim"][mask])
            )
        summary, edge_rows = event_summary(data, y, p0, p1, task)
        if summary is not None:
            summary["task"] = task
            summary["horizon_seconds"] = horizon
            events.append(summary)
            for row in edge_rows:
                row["task"] = task
                row["horizon_seconds"] = horizon
            edges.extend(edge_rows)
    eligible_events = [row for row in events if int(row.get("valid_edges", 0)) >= 2]
    system, families = aggregate_system(eligible_events, "delta_primary")
    return events, edges, system, families


def null_distribution(
    challenge_data: list[dict[str, Any]],
    regime: str,
    task: str,
    baseline_model: dict[str, Any],
    challenger_model: dict[str, Any],
    null_name: str,
) -> tuple[list[float], list[dict[str, Any]]]:
    draws: list[float] = []
    audit: list[dict[str, Any]] = []
    data_regime = [data for data in challenge_data if data["regime"] == regime]
    for draw in range(NULL_DRAWS):
        event_stats: list[dict[str, Any]] = []
        draw_ok = True
        for data in data_regime:
            prim, valid = source_null_primitive(data, null_name, draw)
            value, retained, retention = null_event_stat(
                data, task, baseline_model, challenger_model, prim, valid
            )
            audit.append(
                {
                    "task": task,
                    "regime": regime,
                    "null_model": null_name,
                    "draw": draw,
                    "event": data["event"],
                    "event_family": data["event_family"],
                    "event_stat": value,
                    "retained_rows": retained,
                    "retention_fraction": retention,
                    "event_draw_valid": value is not None,
                }
            )
            if value is None:
                draw_ok = False
            else:
                event_stats.append(
                    {
                        "event_family": data["event_family"],
                        "delta_primary": value,
                    }
                )
        if draw_ok and event_stats:
            system, _ = aggregate_system(event_stats, "delta_primary")
            if system is not None:
                draws.append(system)
    return draws, audit


def deterministic_control(
    challenge_data: list[dict[str, Any]],
    regime: str,
    task: str,
    baseline_model: dict[str, Any],
    challenger_model: dict[str, Any],
    control_name: str,
) -> dict[str, Any]:
    event_stats: list[dict[str, Any]] = []
    event_details: list[dict[str, Any]] = []
    for data in challenge_data:
        if data["regime"] != regime:
            continue
        if control_name == "CONTROL_C_MATCHED_UNRELATED":
            prim, valid = unrelated_primitive(data)
        else:
            prim, valid = delayed_primitive(data)
        value, retained, retention = null_event_stat(
            data, task, baseline_model, challenger_model, prim, valid
        )
        event_details.append(
            {
                "event": data["event"],
                "event_family": data["event_family"],
                "event_stat": value,
                "retained_rows": retained,
                "retention_fraction": retention,
            }
        )
        if value is not None:
            event_stats.append(
                {"event_family": data["event_family"], "delta_primary": value}
            )
    system, families = aggregate_system(event_stats, "delta_primary")
    return {
        "task": task,
        "regime": regime,
        "control": control_name,
        "system_delta_primary": system,
        "family_values": json.dumps(families, sort_keys=True),
        "event_details": json.dumps(event_details, sort_keys=True),
    }


def empirical_p(observed: float | None, draws: list[float]) -> float | None:
    if observed is None or len(draws) != NULL_DRAWS:
        return None
    return (1 + sum(value >= observed for value in draws)) / (len(draws) + 1)


def main() -> None:
    code_root, code_manifest = load_code_bundle()
    prereg = json.loads((code_root / "preregistration.json").read_text(encoding="utf-8"))
    freeze = json.loads((code_root / "FREEZE_V3.json").read_text(encoding="utf-8"))
    if freeze["freeze_status"] != "FINAL_FROZEN_BEFORE_CHALLENGE_EMPIRICAL_ACCESS":
        raise RuntimeError("terminal freeze marker invalid")
    if prereg["primary_horizon_seconds"] != PRIMARY_H:
        raise RuntimeError("runtime primary horizon disagrees with preregistration")

    harnesses = build_harnesses(code_manifest)
    pair_rows = read_csv(code_root / "pair_registry.csv")
    universe_rows = read_csv(code_root / "universe.csv")
    prior_exposure = json.loads(
        (code_root / "prior_exposure_manifest.json").read_text(encoding="utf-8")
    )
    regime_defs = json.loads((code_root / "regime_definitions.json").read_text(encoding="utf-8"))
    windows = windows_from_defs(regime_defs)

    control_tokens, control_registry_rows = matched_control_registry(pair_rows, universe_rows)
    write_csv(WORK / "matched_control_registry.csv", control_registry_rows)

    data_manifest_path = one("corpus_manifest.json")
    if sha256(data_manifest_path) != DATA001_SHA:
        raise RuntimeError("accepted DATA-001 manifest hash mismatch")
    manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    corpus = data_manifest_path.parent.parent
    required_events = {row["event"] for row in pair_rows}
    verification = verify_data001(corpus, manifest, required_events)

    pairs_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    universe_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in pair_rows:
        pairs_by_key[(row["event"], row["regime"])].append(row)
    for row in universe_rows:
        universe_by_key[(row["event"], row["regime"])].append(row)

    discovery_data: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for event in sorted(DISCOVERY):
        for regime in REGIMES:
            pairs = pairs_by_key.get((event, regime), [])
            if not pairs:
                coverage_rows.append(
                    {
                        "scope": "DISCOVERY",
                        "event": event,
                        "regime": regime,
                        "pair_rows": 0,
                        "feature_rows": 0,
                        "note": "NO_FROZEN_SEMANTIC_PAIRS",
                    }
                )
                continue
            start, end = windows[(event, regime)]
            data = build_event_data(
                corpus,
                event,
                regime,
                pairs,
                universe_by_key[(event, regime)],
                start,
                end,
                control_tokens,
            )
            discovery_data.append(data)
            coverage_rows.append(
                {
                    "scope": "DISCOVERY",
                    "event": event,
                    "regime": regime,
                    "pair_rows": len(pairs),
                    "feature_rows": len(data["base"]),
                    "unique_edges": len(set(str(x) for x in data["edge"])),
                    "A1_rows": int(np.isfinite(data["hazard"]).sum()),
                    "A2_rows": int(np.isfinite(data["mark"]).sum()),
                    "A3_30_rows": int(np.isfinite(data["y"][30]).sum()),
                }
            )

    models: dict[tuple[str, str, int], tuple[dict[str, Any], dict[str, Any]]] = {}
    discovery_model_rows: list[dict[str, Any]] = []
    discovery_edge_rows: list[dict[str, Any]] = []
    collapse_rows: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []
    bootstrap_rows: list[dict[str, Any]] = []

    from predictions_cup.learning.stability import ParameterCell

    for regime in REGIMES:
        regime_disc = [data for data in discovery_data if data["regime"] == regime]
        for task, horizons in (("A1", (30,)), ("A2", (30,)), ("A3", (30, 60, 120))):
            for horizon in horizons:
                rows = concat_rows(regime_disc, task, horizon)
                if len(rows["y"]) == 0:
                    discovery_model_rows.append(
                        {
                            "scope": "DISCOVERY_OOS",
                            "regime": regime,
                            "task": task,
                            "horizon_seconds": horizon,
                            "status": "NO_ROWS",
                        }
                    )
                    continue
                train, dev, hold = split_masks(rows["times"], horizon)
                sufficient, reason = training_sufficient(rows, task, train)
                if not sufficient:
                    discovery_model_rows.append(
                        {
                            "scope": "DISCOVERY_OOS",
                            "regime": regime,
                            "task": task,
                            "horizon_seconds": horizon,
                            "status": f"INCONCLUSIVE_{reason}",
                            "train_rows": int(train.sum()),
                            "development_rows": int(dev.sum()),
                            "holdout_rows": int(hold.sum()),
                        }
                    )
                    continue
                baseline, challenger = fit_pair_models(rows, task, 1.0, train)
                hold_mask = hold & np.isfinite(rows["y"])
                p0 = np.full(len(rows["y"]), np.nan)
                p1 = np.full(len(rows["y"]), np.nan)
                p0[hold_mask] = predict(baseline, rows["base"][hold_mask])
                p1[hold_mask] = predict(
                    challenger,
                    challenger_matrix(rows["base"][hold_mask], rows["prim"][hold_mask]),
                )
                pseudo_data = {
                    "event": "discovery_pooled",
                    "event_family": "DISCOVERY",
                    "regime": regime,
                    "edge": rows["edge"],
                }
                summary, edges = event_summary(
                    pseudo_data, rows["y"], p0, p1, task, hold_mask
                )
                record = {
                    "scope": "DISCOVERY_OOS",
                    "regime": regime,
                    "task": task,
                    "horizon_seconds": horizon,
                    "status": "OK" if summary is not None else "INCONCLUSIVE_NO_EDGE_METRICS",
                    "train_rows": int(train.sum()),
                    "development_rows": int(dev.sum()),
                    "holdout_rows": int(hold.sum()),
                }
                if summary:
                    record.update(
                        {
                            key: value
                            for key, value in summary.items()
                            if key not in {"event", "event_family", "regime"}
                        }
                    )
                discovery_model_rows.append(record)
                for row in edges:
                    row["scope"] = "DISCOVERY_OOS"
                    row["horizon_seconds"] = horizon
                    row["task"] = task
                discovery_edge_rows.extend(edges)

                all_valid = np.isfinite(rows["y"])
                sufficient_all, reason_all = training_sufficient(rows, task, all_valid)
                if sufficient_all:
                    models[(regime, task, horizon)] = fit_pair_models(
                        rows, task, 1.0, all_valid
                    )

                if task == "A3" and horizon == 30 and summary is not None:
                    weights = edge_weights(rows["edge"][train])
                    naive = fit_ridge(
                        rows["prim"][train, 0:1],
                        rows["y"][train],
                        weights,
                        1.0,
                    )
                    naive_beta = float(naive["beta"][1])
                    source_index = len(BASE_NAMES)
                    controlled_beta = float(challenger["beta"][1 + source_index])
                    coef_collapse = (
                        1.0 - abs(controlled_beta) / abs(naive_beta)
                        if abs(naive_beta) > 1e-12
                        else None
                    )
                    residual = rows["y"][hold_mask] - p0[hold_mask]
                    raw_corr = safe_corr(rows["prim"][hold_mask, 0], rows["y"][hold_mask])
                    residual_corr = safe_corr(rows["prim"][hold_mask, 0], residual)
                    corr_collapse = (
                        1.0 - abs(residual_corr) / abs(raw_corr)
                        if raw_corr is not None
                        and residual_corr is not None
                        and abs(raw_corr) > 1e-12
                        else None
                    )
                    collapse_rows.append(
                        {
                            "regime": regime,
                            "naive_standardized_source_beta": naive_beta,
                            "controlled_standardized_source_beta": controlled_beta,
                            "coefficient_collapse_fraction": coef_collapse,
                            "discovery_holdout_raw_source_corr": raw_corr,
                            "discovery_holdout_source_vs_baseline_residual_corr": residual_corr,
                            "correlation_collapse_fraction": corr_collapse,
                        }
                    )

                    cells = []
                    for position, penalty in enumerate((0.5, 1.0, 2.0)):
                        b_stab, c_stab = fit_pair_models(rows, task, penalty, train)
                        s0 = predict(b_stab, rows["base"][hold_mask])
                        s1 = predict(
                            c_stab,
                            challenger_matrix(
                                rows["base"][hold_mask], rows["prim"][hold_mask]
                            ),
                        )
                        stab_rows = edge_metrics(
                            rows["edge"][hold_mask],
                            rows["y"][hold_mask],
                            s0,
                            s1,
                            task,
                        )
                        score = (
                            float(np.mean([r["delta_primary"] for r in stab_rows]))
                            if stab_rows
                            else float("-inf")
                        )
                        cells.append(
                            ParameterCell(
                                coordinates=(position,),
                                parameters=(("ridge_penalty", str(penalty)),),
                                score=Decimal(str(score)),
                            )
                        )
                        stability_rows.append(
                            {
                                "regime": regime,
                                "ridge_penalty": penalty,
                                "discovery_oos_delta_mse": score,
                                "selected": penalty == 1.0,
                            }
                        )
                    stability_report = harnesses["A3"].parameter_surface(tuple(cells))
                    stability_rows.append(
                        {
                            "regime": regime,
                            "ridge_penalty": "SUMMARY",
                            "peak_is_isolated": stability_report.peak_is_isolated,
                            "selected": False,
                            "note": "champion remains 1.0 regardless of diagnostic",
                        }
                    )

                    time_values: list[Decimal] = []
                    unique_times = sorted(set(int(x) for x in rows["times"][hold_mask]))
                    for time_value in unique_times:
                        tm = hold_mask & (rows["times"] == time_value)
                        if int(tm.sum()) == 0:
                            continue
                        err0 = (rows["y"][tm] - p0[tm]) ** 2
                        err1 = (rows["y"][tm] - p1[tm]) ** 2
                        time_values.append(Decimal(str(float(np.mean(err0 - err1)))))
                    if len(time_values) >= 10:
                        boot = harnesses["A3"].moving_block_bootstrap(
                            time_values,
                            component_id=f"discovery|{regime}|A3|30",
                        )
                        bootstrap_rows.append(
                            {
                                "scope": "DISCOVERY_BLOCK",
                                "regime": regime,
                                "task": "A3",
                                "horizon_seconds": 30,
                                "units": boot.units,
                                "draws": boot.draws,
                                "point_estimate": str(boot.point_estimate),
                                "lower": str(boot.lower),
                                "upper": str(boot.upper),
                                "seed": boot.seed,
                            }
                        )

    # Challenge empirical access begins only here, after terminal freeze verification and frozen-model fit.
    challenge_data: list[dict[str, Any]] = []
    for event in sorted(CHALLENGE):
        for regime in REGIMES:
            pairs = pairs_by_key.get((event, regime), [])
            if not pairs:
                coverage_rows.append(
                    {
                        "scope": "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED",
                        "event": event,
                        "regime": regime,
                        "pair_rows": 0,
                        "feature_rows": 0,
                        "note": "NO_FROZEN_SEMANTIC_PAIRS",
                    }
                )
                continue
            start, end = windows[(event, regime)]
            data = build_event_data(
                corpus,
                event,
                regime,
                pairs,
                universe_by_key[(event, regime)],
                start,
                end,
                control_tokens,
            )
            challenge_data.append(data)
            coverage_rows.append(
                {
                    "scope": "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED",
                    "event": event,
                    "regime": regime,
                    "pair_rows": len(pairs),
                    "feature_rows": len(data["base"]),
                    "unique_edges": len(set(str(x) for x in data["edge"])),
                    "A1_rows": int(np.isfinite(data["hazard"]).sum()),
                    "A2_rows": int(np.isfinite(data["mark"]).sum()),
                    "A3_30_rows": int(np.isfinite(data["y"][30]).sum()),
                }
            )

    model_comparison_rows = discovery_model_rows[:]
    edge_output_rows = discovery_edge_rows[:]
    system_rows: list[dict[str, Any]] = []
    null_summary_rows: list[dict[str, Any]] = []
    null_draw_rows: list[dict[str, Any]] = []
    null_audit_rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    raw_tests_by_task: dict[str, list[Any]] = defaultdict(list)

    from predictions_cup.learning.statistics import HypothesisTest

    for regime in REGIMES:
        regime_challenge = [data for data in challenge_data if data["regime"] == regime]
        for task, horizons in (("A1", (30,)), ("A2", (30,)), ("A3", (30, 60, 120))):
            for horizon in horizons:
                key = (regime, task, horizon)
                if key not in models:
                    system_rows.append(
                        {
                            "scope": "CHALLENGE",
                            "regime": regime,
                            "task": task,
                            "horizon_seconds": horizon,
                            "status": "INCONCLUSIVE_NO_DISCOVERY_MODEL",
                        }
                    )
                    continue
                baseline, challenger = models[key]
                events, edges, system, families = challenge_observed(
                    regime_challenge, task, baseline, challenger, horizon
                )
                for row in events:
                    row["scope"] = "CHALLENGE"
                    model_comparison_rows.append(
                        {
                            "scope": "CHALLENGE",
                            "event": row["event"],
                            "event_family": row["event_family"],
                            "regime": regime,
                            "task": task,
                            "horizon_seconds": horizon,
                            **{
                                k: v
                                for k, v in row.items()
                                if k
                                not in {
                                    "event",
                                    "event_family",
                                    "regime",
                                    "task",
                                    "horizon_seconds",
                                    "scope",
                                }
                            },
                        }
                    )
                for row in edges:
                    row["scope"] = "CHALLENGE"
                edge_output_rows.extend(edges)
                family_replication = (
                    "COL_2026" in families and "PER_2026" in families
                )
                system_record: dict[str, Any] = {
                    "scope": "CHALLENGE",
                    "regime": regime,
                    "task": task,
                    "horizon_seconds": horizon,
                    "system_delta_primary": system,
                    "family_values": json.dumps(families, sort_keys=True),
                    "family_replication_available": family_replication,
                    "represented_families": len(families),
                    "status": "OK" if system is not None else "INCONCLUSIVE_NO_EVENT_METRICS",
                }
                if task == "A3" and horizon == 30 and events:
                    system_mse_b, _ = aggregate_system(events, "baseline_primary")
                    system_mse_c, _ = aggregate_system(events, "challenger_primary")
                    system_rmse_gain, _ = aggregate_system(events, "rmse_improvement")
                    relative = (
                        (system_mse_b - system_mse_c) / system_mse_b
                        if system_mse_b is not None
                        and system_mse_c is not None
                        and system_mse_b > 0
                        else None
                    )
                    meaningful = bool(
                        relative is not None
                        and system_rmse_gain is not None
                        and relative >= 0.01
                        and system_rmse_gain >= 0.00025
                    )
                    system_record["system_relative_mse_improvement"] = relative
                    system_record["system_rmse_improvement"] = system_rmse_gain
                    system_record["smallest_meaningful_effect_pass"] = meaningful
                system_rows.append(system_record)

                if horizon != 30:
                    continue

                for control_name in (
                    "CONTROL_C_MATCHED_UNRELATED",
                    "CONTROL_D_DELAYED_SOURCE",
                ):
                    control_rows.append(
                        deterministic_control(
                            regime_challenge,
                            regime,
                            task,
                            baseline,
                            challenger,
                            control_name,
                        )
                    )

                for null_name in STOCH_NULLS:
                    draws, audit = null_distribution(
                        regime_challenge,
                        regime,
                        task,
                        baseline,
                        challenger,
                        null_name,
                    )
                    null_audit_rows.extend(audit)
                    for draw_index, value in enumerate(draws):
                        null_draw_rows.append(
                            {
                                "task": task,
                                "regime": regime,
                                "null_model": null_name,
                                "draw": draw_index,
                                "system_delta_primary": value,
                            }
                        )
                    p_value = empirical_p(system, draws)
                    summary_row = {
                        "task": task,
                        "regime": regime,
                        "null_model": null_name,
                        "observed_system_delta_primary": system,
                        "valid_draws": len(draws),
                        "required_valid_draws": NULL_DRAWS,
                        "null_mean": float(np.mean(draws)) if draws else None,
                        "null_std": float(np.std(draws)) if draws else None,
                        "p_value": p_value,
                        "q_value": None,
                        "rejected_bh_0_05": None,
                        "status": (
                            "OK"
                            if len(draws) == NULL_DRAWS and p_value is not None
                            else "INCONCLUSIVE_NULL_SHORTFALL"
                        ),
                    }
                    null_summary_rows.append(summary_row)
                    if p_value is not None:
                        raw_tests_by_task[task].append(
                            HypothesisTest(
                                hypothesis_id=f"{regime}|{null_name}",
                                family_id=f"004C_A_{task}",
                                p_value=Decimal(str(p_value)),
                                test_name="fixed-model source-timing randomization",
                                null_hypothesis=(
                                    "correct source timing has no greater incremental "
                                    "predictive value than the preregistered timing null"
                                ),
                                test_statistic="equal-family system delta primary loss",
                                dependence_assumption=(
                                    "source serial structure preserved by clock-safe timing transform; "
                                    "event-family aggregation used for headline statistic"
                                ),
                            )
                        )

                if task == "A3" and events:
                    event_values: dict[str, list[Decimal]] = defaultdict(list)
                    for row in events:
                        event_values[row["event_family"]].append(
                            Decimal(str(row["delta_primary"]))
                        )
                    if event_values:
                        boot = harnesses["A3"].event_bootstrap(
                            event_values,
                            component_id=f"challenge|{regime}|A3|30",
                        )
                        bootstrap_rows.append(
                            {
                                "scope": "CHALLENGE_EVENT_FAMILY",
                                "regime": regime,
                                "task": "A3",
                                "horizon_seconds": 30,
                                "units": boot.units,
                                "draws": boot.draws,
                                "point_estimate": str(boot.point_estimate),
                                "lower": str(boot.lower),
                                "upper": str(boot.upper),
                                "seed": boot.seed,
                            }
                        )

    for task in ("A1", "A2", "A3"):
        tests = raw_tests_by_task.get(task, [])
        if len(tests) == 4:
            results = harnesses[task].apply_fdr(tests)
            by_id = {row.hypothesis_id: row for row in results}
            for row in null_summary_rows:
                if row["task"] != task:
                    continue
                result = by_id[f'{row["regime"]}|{row["null_model"]}']
                row["q_value"] = str(result.q_value)
                row["rejected_bh_0_05"] = result.rejected
        else:
            for row in null_summary_rows:
                if row["task"] == task:
                    row["fdr_family_status"] = "INCONCLUSIVE_INCOMPLETE_TEST_FAMILY"

    disposition = "INCONCLUSIVE"
    disposition_reason: list[str] = []
    a3_system = [
        row
        for row in system_rows
        if row.get("task") == "A3" and row.get("horizon_seconds") == 30
    ]
    controls_by_regime = {
        (row["regime"], row["control"]): row for row in control_rows if row["task"] == "A3"
    }
    nulls_by_key = {
        (row["regime"], row["null_model"]): row
        for row in null_summary_rows
        if row["task"] == "A3"
    }
    passing_regimes: list[str] = []
    for row in a3_system:
        regime = row["regime"]
        if not row.get("family_replication_available"):
            disposition_reason.append(
                f"{regime}: no frozen challenge coverage spanning both COL_2026 and PER_2026"
            )
            continue
        if not row.get("smallest_meaningful_effect_pass"):
            disposition_reason.append(f"{regime}: A3 effect below frozen meaningful-effect gate")
            continue
        timing_ok = all(
            nulls_by_key.get((regime, null_name), {}).get("rejected_bh_0_05") is True
            for null_name in STOCH_NULLS
        )
        if not timing_ok:
            disposition_reason.append(f"{regime}: A3 did not survive both stochastic timing nulls")
            continue
        observed = row.get("system_delta_primary")
        unrelated = controls_by_regime.get((regime, "CONTROL_C_MATCHED_UNRELATED"), {}).get(
            "system_delta_primary"
        )
        delayed = controls_by_regime.get((regime, "CONTROL_D_DELAYED_SOURCE"), {}).get(
            "system_delta_primary"
        )
        if observed is None:
            continue
        if unrelated is None:
            disposition_reason.append(f"{regime}: unrelated-market control lacked frozen comparable coverage")
            continue
        if delayed is None:
            disposition_reason.append(f"{regime}: delayed-source control lacked frozen comparable coverage")
            continue
        if unrelated >= observed:
            disposition_reason.append(f"{regime}: unrelated-market control matched/exceeded A3 lift")
            continue
        if delayed >= observed:
            disposition_reason.append(f"{regime}: delayed-source control matched/exceeded A3 lift")
            continue
        passing_regimes.append(regime)

    collapse_values = [
        row["coefficient_collapse_fraction"]
        for row in collapse_rows
        if row.get("coefficient_collapse_fraction") is not None
    ]
    mean_collapse = float(np.mean(collapse_values)) if collapse_values else None

    if passing_regimes:
        disposition = "TRANSMISSION_SURVIVES"
        disposition_reason.insert(
            0, "primary A3 gate passed in " + ", ".join(sorted(passing_regimes))
        )
    else:
        a3_meaningful = any(
            row.get("smallest_meaningful_effect_pass") is True
            for row in a3_system
        )
        timing_rejections = any(
            row.get("rejected_bh_0_05") is True
            for row in null_summary_rows
            if row["task"] == "A3"
        )
        if (
            not a3_meaningful
            and not timing_rejections
            and mean_collapse is not None
            and mean_collapse >= 0.50
        ):
            disposition = "STALENESS_EXPLAINS_STRUCTURE"
            disposition_reason.insert(
                0,
                "controlled A3 failed to improve while naive source coefficient collapsed by at least 50%",
            )

    write_csv(WORK / "coverage.csv", coverage_rows)
    write_csv(WORK / "model_comparison.csv", model_comparison_rows)
    write_csv(WORK / "edge_diagnostics_descriptive_only.csv", edge_output_rows)
    write_csv(WORK / "system_summary.csv", system_rows)
    write_csv(WORK / "coefficient_collapse.csv", collapse_rows)
    write_csv(WORK / "parameter_stability.csv", stability_rows)
    write_csv(WORK / "bootstrap_summary.csv", bootstrap_rows)
    write_csv(WORK / "null_results.csv", null_summary_rows)
    write_csv(WORK / "null_draws.csv", null_draw_rows)
    write_csv(WORK / "null_draw_audit.csv", null_audit_rows)
    write_csv(WORK / "control_results.csv", control_rows)

    report = {
        "experiment_id": "EXPERIMENT-004C-A",
        "terminal_freeze_commit": FREEZE_SHA,
        "implementation_commit": code_manifest["implementation_commit"],
        "data001_manifest_sha256": DATA001_SHA,
        "harness_run_ids": {
            family: harness.identity.run_id for family, harness in harnesses.items()
        },
        "disposition": disposition,
        "disposition_reason": disposition_reason,
        "mean_discovery_source_coefficient_collapse_fraction": mean_collapse,
        "passing_regimes": passing_regimes,
        "challenge_prior_exposure": prior_exposure,
        "verification": verification,
        "limitations": [
            "challenge events were sealed from 004B but were previously exposed to EXPERIMENT-003",
            "only metadata-frozen indirect semantic pairs are tested; no 004B edge effect was used for selection",
            "PRE and ACTIVE are fit/evaluated separately",
            "A2 conditions on a future target update and is diagnostic only",
            "full-depth features can be missing and snapshot activity can reflect archive cadence",
            "event/family counts are small; row count is not treated as independent election evidence",
            "no execution/P&L/order-placement inference is made",
        ],
    }
    (WORK / "research_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary_lines = [
        "# EXPERIMENT-004C-A — Final Research Report",
        "",
        f"**Disposition:** {disposition}",
        "",
        "## Primary conclusion",
        "",
    ]
    if disposition == "TRANSMISSION_SURVIVES":
        summary_lines.append(
            "Incremental source information survives the frozen freshness/timing identification controls "
            "in the passing regime(s): " + ", ".join(sorted(passing_regimes)) + "."
        )
    elif disposition == "STALENESS_EXPLAINS_STRUCTURE":
        summary_lines.append(
            "The 004B-like source association materially collapses after freshness/update/common-event "
            "controls and the source challenger does not survive the frozen timing nulls."
        )
    else:
        summary_lines.append(
            "The battery is inconclusive under the frozen promotion/rejection rules; no subgroup, "
            "threshold or longer horizon is used to rescue the 30-second primary."
        )
    summary_lines += [
        "",
        "## Frozen interpretation",
        "",
        f"- Terminal preregistration freeze: `{FREEZE_SHA}`.",
        f"- Implementation commit: `{code_manifest['implementation_commit']}`.",
        f"- DATA-001 manifest: `{DATA001_SHA}`.",
        f"- Mean discovery standardized source-coefficient collapse: `{mean_collapse}`.",
        f"- Passing regimes: `{', '.join(sorted(passing_regimes)) if passing_regimes else 'none'}`.",
        "",
        "A1 update hazard and A2 conditional mark response are diagnostics. A3 full-grid 30-second "
        "forecast is the headline object. 60/120-second A3 results are robustness only.",
        "",
        "Per-edge rows are descriptive only and are not promoted as leaders.",
        "",
        "## Limitations",
        "",
    ]
    summary_lines += [f"- {item}" for item in report["limitations"]]
    (WORK / "FINAL_RESEARCH_REPORT.md").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )

    handoff = [
        "# MASTER Handoff — EXPERIMENT-004C-A",
        "",
        f"- Disposition: **{disposition}**.",
        f"- Terminal freeze: `{FREEZE_SHA}`.",
        f"- Implementation: `{code_manifest['implementation_commit']}`.",
        f"- Kaggle namespace: `004c_a_freshness` / `polyleviathan/sig-cup-004c-a-freshness`.",
        f"- DATA-001 manifest: `{DATA001_SHA}`.",
        f"- Mean source-coefficient collapse after controls: `{mean_collapse}`.",
        f"- Regimes passing the full A3 gate: `{', '.join(sorted(passing_regimes)) if passing_regimes else 'none'}`.",
        "- Challenge evidence spans only frozen metadata-valid pairs; missing regime/family coverage remains missing.",
        "- A1/A2 are supporting diagnostics; A3 30s is primary.",
        "- No real order placement, P&L optimization or execution claim was enabled.",
        "",
        "## Reasons / blockers",
        "",
    ]
    handoff += [f"- {reason}" for reason in disposition_reason] or ["- none"]
    handoff += [
        "",
        "## Canonical Kaggle outputs",
        "",
        "- `model_comparison.csv`",
        "- `system_summary.csv`",
        "- `coefficient_collapse.csv`",
        "- `parameter_stability.csv`",
        "- `bootstrap_summary.csv`",
        "- `null_results.csv` / `null_draws.csv` / `null_draw_audit.csv`",
        "- `control_results.csv`",
        "- `coverage.csv`",
        "- `research_report.json`",
        "- `FINAL_RESEARCH_REPORT.md`",
    ]
    (WORK / "MASTER_HANDOFF_004C_A.md").write_text(
        "\n".join(handoff) + "\n", encoding="utf-8"
    )

    self_test = {
        "baseline_has_source_features": any(name.startswith("source_") for name in BASE_NAMES),
        "challenger_is_strict_superset": CHALLENGER_NAMES[: len(BASE_NAMES)] == BASE_NAMES,
        "terminal_freeze_verified": True,
        "build008_run_ids": {
            family: harness.identity.run_id for family, harness in harnesses.items()
        },
    }
    if self_test["baseline_has_source_features"] or not self_test["challenger_is_strict_superset"]:
        raise RuntimeError("self-test failed: nested model feature contract")
    (WORK / "self_test.json").write_text(
        json.dumps(self_test, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    output_hashes = {
        path.name: sha256(path)
        for path in sorted(WORK.iterdir())
        if path.is_file() and path.name != "kaggle_run_summary.json"
    }
    run_summary = {
        "phase": "complete",
        "experiment_id": "EXPERIMENT-004C-A",
        "terminal_freeze_commit": FREEZE_SHA,
        "implementation_commit": code_manifest["implementation_commit"],
        "disposition": disposition,
        "data001_manifest_sha256": DATA001_SHA,
        "outputs": output_hashes,
    }
    (WORK / "kaggle_run_summary.json").write_text(
        json.dumps(run_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(run_summary, sort_keys=True))


if __name__ == "__main__":
    main()
