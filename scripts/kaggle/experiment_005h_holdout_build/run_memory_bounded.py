# ruff: noqa
from __future__ import annotations

import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import run as base

WORK = base.WORK
CONTROL_BUFFER_ROWS = 100_000
CONTROL_BUCKETS = 64
MATCH_FEATURES = ("mid", "spread", "log_depth_2c", "activity_60")
CONTROL_OUTCOME_COLUMNS = ("mid_30s", "mid_300s")


def frozen_control_features(freeze: dict[str, Any]) -> list[str]:
    features = set(MATCH_FEATURES)
    for candidate in freeze.get("candidates", []):
        if str(candidate.get("candidate_id")) != "005H-C04-ARRIVAL-STATE":
            continue
        model = candidate.get("frozen_spec", {}).get("arrival_model", {})
        features.update(str(value) for value in model.get("features", []))
    return sorted(features)


class ControlSpool:
    """Disk-backed compact control pool.

    Controls remain mutable only while their <=300s outcomes are unresolved.
    Once expired they are reduced to the exact columns required by the frozen
    C04 scorer and the frozen without-replacement matcher, then written to one
    of a fixed number of Parquet bucket files.  This changes storage only; it
    does not change control cadence, eligibility, features, horizons, or
    matching.
    """

    def __init__(
        self,
        token_codes: dict[str, int],
        feature_columns: list[str],
    ) -> None:
        self.token_codes = token_codes
        self.feature_columns = list(dict.fromkeys(feature_columns))
        self.columns = list(
            dict.fromkeys(
                [
                    "control_order",
                    "token_code",
                    "anchor_ts_ns",
                    *self.feature_columns,
                    *CONTROL_OUTCOME_COLUMNS,
                ]
            )
        )
        self.root = WORK / "_holdout_control_spool"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.buffer: list[dict[str, Any]] = []
        self.writers: dict[int, pq.ParquetWriter] = {}
        self.count = 0

    def add(self, anchor: dict[str, Any]) -> None:
        token = str(anchor["token_id"])
        row: dict[str, Any] = {
            "control_order": int(self.count),
            "token_code": int(self.token_codes[token]),
            "anchor_ts_ns": int(anchor["anchor_ts_ns"]),
        }
        for column in self.feature_columns:
            value = anchor.get(column)
            row[column] = float(value) if value is not None and pd.notna(value) else math.nan
        for column in CONTROL_OUTCOME_COLUMNS:
            value = anchor.get(column)
            row[column] = float(value) if value is not None and pd.notna(value) else math.nan
        self.buffer.append(row)
        self.count += 1
        if len(self.buffer) >= CONTROL_BUFFER_ROWS:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        frame = pd.DataFrame.from_records(self.buffer, columns=self.columns)
        frame["control_order"] = frame["control_order"].astype("int64")
        frame["token_code"] = frame["token_code"].astype("int32")
        frame["anchor_ts_ns"] = frame["anchor_ts_ns"].astype("int64")
        for column in self.columns:
            if column in {"control_order", "token_code", "anchor_ts_ns"}:
                continue
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        frame["_bucket"] = frame["token_code"] % CONTROL_BUCKETS
        for bucket, group in frame.groupby("_bucket", sort=False):
            bucket_int = int(bucket)
            table = pa.Table.from_pandas(
                group[self.columns],
                preserve_index=False,
            )
            writer = self.writers.get(bucket_int)
            if writer is None:
                path = self.root / f"bucket-{bucket_int:02d}.parquet"
                writer = pq.ParquetWriter(
                    path,
                    table.schema,
                    compression="zstd",
                )
                self.writers[bucket_int] = writer
            writer.write_table(table)
        self.buffer.clear()

    def close(self) -> None:
        self.flush()
        for writer in self.writers.values():
            writer.close()
        self.writers.clear()

    def load_bucket(self, bucket: int) -> pd.DataFrame:
        path = self.root / f"bucket-{int(bucket):02d}.parquet"
        if not path.exists():
            return pd.DataFrame(columns=self.columns)
        frame = pd.read_parquet(path)
        return frame.sort_values("control_order", kind="stable")

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)


def expire_pending(
    rows: list[dict[str, Any]],
    ts_ns: int,
    spool: ControlSpool,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for anchor in rows:
        if ts_ns <= int(anchor["anchor_ts_ns"]) + 305_000_000_000:
            kept.append(anchor)
        elif anchor.get("kind") == "CONTROL":
            spool.add(anchor)
    return kept


def process_window_bounded(
    root: Path,
    groups: pd.DataFrame,
    linked: dict[tuple[str, str], dict[str, Any]],
    ob_window: str,
    window: str,
    split: str,
    control_features: list[str],
) -> tuple[pd.DataFrame, ControlSpool, dict[str, Any]]:
    group_by_key = {
        (str(row.tx_hash), str(row.token_id)): row._asdict()
        for row in groups.itertuples(index=False)
        if (str(row.tx_hash), str(row.token_id)) in linked
    }
    target_tokens = {key[1] for key in group_by_key}
    token_codes = {
        token: index
        for index, token in enumerate(sorted(target_tokens))
    }
    target_bytes = {int(token).to_bytes(32, "big") for token in target_tokens}
    states = {token: base.TokenState() for token in target_tokens}
    pending: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    fills: list[dict[str, Any]] = []
    spool = ControlSpool(token_codes, control_features)
    controls_built = 0
    seen_link_keys: set[tuple[str, str]] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    files = base.eligible_orderbook_files(root, ob_window)
    wanted = [
        "event_type", "timestamp_received", "sequence", "asset_id",
        "bids", "asks", "price", "size", "side", "best_bid", "best_ask",
        "transaction_hash",
    ]
    last_seen_by_token: dict[str, int] = {}
    window_end_ns = 0

    for number, path in enumerate(files, start=1):
        pf = pq.ParquetFile(path)
        available = [column for column in wanted if column in pf.schema_arrow.names]
        frame = pf.read(columns=available).to_pandas()
        counts["raw_rows"] += len(frame)
        if frame.empty:
            continue
        raw_received_ns = base.receive_ns(frame["timestamp_received"])
        if len(raw_received_ns):
            window_end_ns = max(window_end_ns, int(raw_received_ns.max()))
        frame = frame[frame["asset_id"].isin(target_bytes)].copy()
        counts["target_token_rows"] += len(frame)
        if frame.empty:
            continue
        frame = frame[
            frame["event_type"].astype(str).isin(
                ["book", "price_change", "last_trade_price"]
            )
        ].copy()
        if frame.empty:
            continue
        frame["ts_ns"] = base.receive_ns(frame["timestamp_received"])
        frame["sequence"] = pd.to_numeric(
            frame["sequence"],
            errors="raise",
        ).astype("uint64")
        frame.sort_values(
            ["ts_ns", "sequence"],
            kind="stable",
            inplace=True,
        )

        for rec in frame.itertuples(index=False):
            token = base.bytes_to_token(rec.asset_id)
            state = states[token]
            ts_ns = int(rec.ts_ns)
            sequence = int(rec.sequence)
            last_seen_by_token[token] = ts_ns

            if pending[token]:
                base.resolve_clock(pending[token], state, ts_ns)
                pending[token] = expire_pending(
                    pending[token],
                    ts_ns,
                    spool,
                )

            kind = str(rec.event_type)
            if kind == "book":
                old, new = state.apply_snapshot(rec.bids, rec.asks, ts_ns)
                if new is not None:
                    base.trim_histories(state, ts_ns)
                    state.update_times.append(ts_ns)
                    state.ofi_hist.append(
                        (ts_ns, base.ofi_value(old, new))
                    )
                    if (
                        not state.mid_hist
                        or new["mid"] != state.mid_hist[-1][1]
                    ):
                        state.mid_hist.append(
                            (ts_ns, float(new["mid"]))
                        )
                    base.process_post_update(
                        pending[token],
                        state,
                        ts_ns,
                        sequence,
                    )
                    bucket = ts_ns // base.CONTROL_INTERVAL_NS
                    if state.last_control_bucket != bucket:
                        control = base.build_anchor(
                            "CONTROL",
                            window,
                            split,
                            token,
                            ts_ns,
                            sequence,
                            state,
                            1,
                        )
                        if control is not None:
                            controls_built += 1
                            pending[token].append(control)
                            state.last_control_bucket = bucket
                continue

            if kind == "price_change":
                old, new = state.apply_delta(
                    str(rec.side or "").upper(),
                    rec.price,
                    rec.size,
                    ts_ns,
                    rec.best_bid,
                    rec.best_ask,
                )
                if new is not None:
                    base.trim_histories(state, ts_ns)
                    state.update_times.append(ts_ns)
                    state.ofi_hist.append(
                        (ts_ns, base.ofi_value(old, new))
                    )
                    if (
                        not state.mid_hist
                        or new["mid"] != state.mid_hist[-1][1]
                    ):
                        state.mid_hist.append(
                            (ts_ns, float(new["mid"]))
                        )
                    base.process_post_update(
                        pending[token],
                        state,
                        ts_ns,
                        sequence,
                    )
                    bucket = ts_ns // base.CONTROL_INTERVAL_NS
                    if state.last_control_bucket != bucket:
                        control = base.build_anchor(
                            "CONTROL",
                            window,
                            split,
                            token,
                            ts_ns,
                            sequence,
                            state,
                            1,
                        )
                        if control is not None:
                            controls_built += 1
                            pending[token].append(control)
                            state.last_control_bucket = bucket
                continue

            if kind == "last_trade_price":
                side = str(rec.side or "").upper()
                q = 1 if side == "BUY" else -1 if side == "SELL" else 0
                tx_hash = base.normalize_tx_hash(rec.transaction_hash)
                key = (tx_hash, token)
                if key in group_by_key and key not in seen_link_keys:
                    group = group_by_key[key]
                    anchor = base.build_anchor(
                        "FILL",
                        window,
                        split,
                        token,
                        ts_ns,
                        sequence,
                        state,
                        q,
                    )
                    if anchor is not None and q != 0:
                        venue_price = float(rec.price)
                        venue_size = float(rec.size)
                        exact = (
                            round(venue_price, 6)
                            == round(float(group["episode_price"]), 6)
                            and round(venue_size, 6)
                            == round(float(group["episode_size"]), 6)
                        )
                        consumed_touch = (
                            anchor["ask_size"]
                            if q > 0
                            else anchor["bid_size"]
                        )
                        consumed_2c = (
                            anchor["ask_depth_2c"]
                            if q > 0
                            else anchor["bid_depth_2c"]
                        )
                        anchor.update(
                            {
                                **group,
                                "venue_price": venue_price,
                                "venue_size": venue_size,
                                "trade_side": side,
                                "economic_exact": bool(exact),
                                "venue_minus_block_s": (
                                    ts_ns
                                    - int(group["block_timestamp_s"])
                                    * 1_000_000_000
                                )
                                / 1e9,
                                "pre_touch_price": (
                                    anchor["best_ask"]
                                    if q > 0
                                    else anchor["best_bid"]
                                ),
                                "pre_consumed_touch_depth": float(
                                    consumed_touch
                                ),
                                "pre_consumed_depth_2c": float(
                                    consumed_2c
                                ),
                                "venue_size_over_touch": (
                                    venue_size / consumed_touch
                                    if consumed_touch > 0
                                    else math.nan
                                ),
                                "venue_size_over_depth_2c": (
                                    venue_size / consumed_2c
                                    if consumed_2c > 0
                                    else math.nan
                                ),
                                "episode_size_over_touch": (
                                    float(group["episode_size"])
                                    / consumed_touch
                                    if exact and consumed_touch > 0
                                    else math.nan
                                ),
                                "episode_size_over_depth_2c": (
                                    float(group["episode_size"])
                                    / consumed_2c
                                    if exact and consumed_2c > 0
                                    else math.nan
                                ),
                            }
                        )
                        fills.append(anchor)
                        pending[token].append(anchor)
                        seen_link_keys.add(key)
                if q != 0:
                    base.trim_histories(state, ts_ns)
                    try:
                        trade_size = float(rec.size)
                    except (TypeError, ValueError):
                        trade_size = 0.0
                    if trade_size > 0:
                        state.trade_hist.append(
                            (ts_ns, q, trade_size)
                        )

        print(
            json.dumps(
                {
                    "stage": "state_replay",
                    "window": window,
                    "file": number,
                    "of": len(files),
                    "fills": len(fills),
                    "controls": controls_built,
                    "controls_spooled": spool.count,
                    "target_rows": counts["target_token_rows"],
                }
            ),
            flush=True,
        )

    for token, rows in pending.items():
        state = states[token]
        last_seen = last_seen_by_token.get(token)
        if last_seen is not None and window_end_ns > 0:
            base.resolve_clock(
                rows,
                state,
                window_end_ns + 1,
            )
        for anchor in rows:
            if anchor.get("kind") == "CONTROL":
                spool.add(anchor)
    spool.close()

    fill_frame = pd.DataFrame(fills)
    audit = {
        "window": window,
        "split": split,
        "target_tokens": len(target_tokens),
        "linked_groups_expected": len(group_by_key),
        "fill_anchors_built": len(fill_frame),
        "linked_trade_rows_seen": len(seen_link_keys),
        "controls_built": int(controls_built),
        "controls_spooled": int(spool.count),
        "raw_rows_scanned": counts["raw_rows"],
        "target_token_rows": counts["target_token_rows"],
        "window_end_ns": int(window_end_ns),
        "control_storage": (
            "disk-backed bounded spool; exact frozen matcher columns only"
        ),
        "clock_outcome_semantics": (
            "last observable reconstructed state at or before horizon; "
            "quiet tokens carry the last state forward through acquisition end"
        ),
    }
    if controls_built != spool.count:
        raise RuntimeError(
            f"control spool mismatch: built={controls_built} "
            f"spooled={spool.count}"
        )
    return fill_frame, spool, audit


def near_fill_mask(
    control_times: np.ndarray,
    fill_times: np.ndarray,
) -> np.ndarray:
    if len(fill_times) == 0:
        return np.zeros(len(control_times), dtype=bool)
    pos = np.searchsorted(fill_times, control_times, side="left")
    left = np.full(len(control_times), np.iinfo(np.int64).max, dtype=np.int64)
    right = left.copy()
    has_left = pos > 0
    has_right = pos < len(fill_times)
    left[has_left] = (
        control_times[has_left] - fill_times[pos[has_left] - 1]
    )
    right[has_right] = (
        fill_times[pos[has_right]] - control_times[has_right]
    )
    distance = np.minimum(np.abs(left), np.abs(right))
    return distance <= base.CONTROL_EXCLUSION_NS


def match_controls_bounded(
    fills: pd.DataFrame,
    spool: ControlSpool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fills.empty:
        return pd.DataFrame(), pd.DataFrame()

    reverse_codes = {
        code: token
        for token, code in spool.token_codes.items()
    }
    work_fills = fills.copy()
    work_fills["_fill_order"] = np.arange(len(work_fills), dtype=np.int64)
    work_fills["_token_code"] = work_fills["token_id"].map(
        spool.token_codes
    ).astype("int32")
    fills_by_code = {
        int(code): group.sort_values("_fill_order", kind="stable")
        for code, group in work_fills.groupby("_token_code", sort=False)
    }

    pair_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []

    for bucket in range(CONTROL_BUCKETS):
        pool = spool.load_bucket(bucket)
        if pool.empty:
            continue
        for code, candidates in pool.groupby("token_code", sort=False):
            code_int = int(code)
            fill_group = fills_by_code.get(code_int)
            if fill_group is None or fill_group.empty:
                continue
            candidates = candidates.sort_values(
                "control_order",
                kind="stable",
            ).copy()
            control_times = candidates["anchor_ts_ns"].to_numpy(
                dtype=np.int64
            )
            fill_times = np.sort(
                fill_group["anchor_ts_ns"].to_numpy(dtype=np.int64)
            )
            candidates = candidates.loc[
                ~near_fill_mask(control_times, fill_times)
            ].copy()
            if candidates.empty:
                continue

            used_orders: set[int] = set()
            token = reverse_codes[code_int]
            for _, fill in fill_group.iterrows():
                eligible = candidates[
                    ~candidates["control_order"].isin(used_orders)
                ]
                if eligible.empty:
                    continue
                f_depth = math.log1p(
                    max(
                        float(
                            fill["bid_depth_2c"]
                            + fill["ask_depth_2c"]
                        ),
                        0.0,
                    )
                )
                score = (
                    ((eligible["mid"] - float(fill["mid"])) / 0.05) ** 2
                    + (
                        (
                            eligible["spread"]
                            - float(fill["spread"])
                        )
                        / 0.02
                    )
                    ** 2
                    + (
                        (eligible["log_depth_2c"] - f_depth)
                        / 2.0
                    )
                    ** 2
                    + (
                        (
                            eligible["activity_60"]
                            - float(fill["activity_60"])
                        )
                        / 25.0
                    )
                    ** 2
                )
                chosen_index = score.idxmin()
                control = eligible.loc[chosen_index]
                order = int(control["control_order"])
                used_orders.add(order)

                selected = control.to_dict()
                selected.update(
                    {
                        "window_id": str(fill["window_id"]),
                        "split": str(fill["split"]),
                        "token_id": token,
                        "q": 1,
                    }
                )
                selected_rows.append(selected)

                rec: dict[str, Any] = {
                    "window_id": str(fill["window_id"]),
                    "split": str(fill["split"]),
                    "group_id": str(fill["group_id"]),
                    "token_id": token,
                    "fill_ts_ns": int(fill["anchor_ts_ns"]),
                    "control_ts_ns": int(control["anchor_ts_ns"]),
                    "match_score": float(score.loc[chosen_index]),
                }
                q = int(fill["q"])
                for horizon in (30, 300):
                    fill_col = f"mid_{horizon}s"
                    control_col = f"mid_{horizon}s"
                    fill_value = fill.get(fill_col)
                    control_value = control.get(control_col)
                    if pd.notna(fill_value) and pd.notna(control_value):
                        fill_move = q * (
                            float(fill_value) - float(fill["mid"])
                        )
                        control_move = q * (
                            float(control_value) - float(control["mid"])
                        )
                        rec[f"fill_signed_move_{horizon}s"] = fill_move
                        rec[f"control_signed_move_{horizon}s"] = control_move
                        rec[
                            f"delta_signed_move_{horizon}s"
                        ] = fill_move - control_move
                        rec[f"fill_abs_move_{horizon}s"] = abs(
                            float(fill_value) - float(fill["mid"])
                        )
                        rec[f"control_abs_move_{horizon}s"] = abs(
                            float(control_value)
                            - float(control["mid"])
                        )
                pair_rows.append(rec)

    pairs = pd.DataFrame(pair_rows)
    selected_controls = pd.DataFrame(selected_rows)
    if not selected_controls.empty:
        selected_controls.sort_values(
            "control_order",
            kind="stable",
            inplace=True,
        )
        selected_controls.reset_index(drop=True, inplace=True)
    return pairs, selected_controls


def locate_freeze() -> Path:
    matches = sorted(
        Path("/kaggle/input").rglob("PRE_HOLDOUT_FREEZE.json")
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one PRE_HOLDOUT_FREEZE.json, found {matches}"
        )
    return matches[0]


def main() -> None:
    freeze = json.loads(
        locate_freeze().read_text(encoding="utf-8")
    )
    if freeze.get("stage") != "PRE_HOLDOUT_FREEZE":
        raise RuntimeError("wrong holdout freeze stage")
    if freeze.get("b0_opened") is not False:
        raise RuntimeError("freeze does not prove B0 remained sealed")
    if freeze.get("b0_authorized") is not True:
        raise RuntimeError("B0 is not authorized by frozen candidate set")
    candidate_ids = [
        str(value)
        for value in freeze.get("candidate_ids", [])
    ]
    if candidate_ids != [
        "005H-C04-ARRIVAL-STATE",
        "005H-C05-DIRECTION-STATE",
    ]:
        raise RuntimeError(
            f"unexpected frozen B0 candidates: {candidate_ids}"
        )
    holdout = freeze.get("final_holdout", {})
    if holdout.get("start_utc") != "2026-09-01T00:00:00Z":
        raise RuntimeError("unexpected B0 start")
    if holdout.get("end_exclusive_utc") != "2026-09-22T00:00:00Z":
        raise RuntimeError("unexpected B0 end")

    root = base.locate_orderbook_root()
    groups, group_audit = base.accepted_groups("B0", "FINAL")
    linked, link_audit = base.scan_linked_trades(
        root,
        groups,
        "baseline_sep",
    )
    join_report = {
        "experiment": "EXPERIMENT-005H",
        "stage": "ONE_SHOT_B0_BUILD_JOIN_AUDIT",
        "identity_rule": "exact transaction_hash + active token_id",
        "prebook_rule": (
            "last reconstructed V3 state strictly before linked trade "
            "in timestamp_received,sequence order"
        ),
        "clock_unit": "nanoseconds",
        "fill_window": "B0",
        "date_scope": "2026-09-01 through 2026-09-21 inclusive",
        "group_audit": group_audit,
        "link_audit": link_audit,
        "candidate_ids": candidate_ids,
        "b0_opened": True,
        "real_sig_orders_sent": False,
    }
    (WORK / "HOLDOUT_JOIN_AUDIT.json").write_text(
        json.dumps(
            join_report,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    control_features = frozen_control_features(freeze)
    fills, spool, replay = process_window_bounded(
        root,
        groups,
        linked,
        "baseline_sep",
        "B0",
        "FINAL",
        control_features,
    )
    fills = base.add_derived(fills)
    fills = base.add_future_flow(fills)
    fills, episodes = base.build_episodes(
        fills,
        threshold_s=10.0,
    )
    episode_sensitivity_frame = base.episode_sensitivity(fills)

    pairs, matched_controls = match_controls_bounded(
        fills,
        spool,
    )
    spool.cleanup()

    fills["log_episode_size"] = np.log1p(
        fills["episode_size"].astype(float)
    )
    fills["log_venue_size"] = np.log1p(
        fills["venue_size"].astype(float)
    )
    fills["log_episode_size_over_touch"] = np.log1p(
        fills["episode_size_over_touch"].astype(float)
    )
    fills["log_venue_size_over_touch"] = np.log1p(
        fills["venue_size_over_touch"].astype(float)
    )

    fills.to_parquet(
        WORK / "HOLDOUT_FILL_EVENTS.parquet",
        index=False,
    )
    episodes.to_parquet(
        WORK / "HOLDOUT_FILL_EPISODES.parquet",
        index=False,
    )
    matched_controls.to_parquet(
        WORK / "HOLDOUT_NONFILL_CONTROLS.parquet",
        index=False,
    )
    pairs.to_csv(
        WORK / "HOLDOUT_MATCHED_CONTROLS.csv",
        index=False,
    )
    episode_sensitivity_frame.to_csv(
        WORK / "HOLDOUT_EPISODE_SENSITIVITY.csv",
        index=False,
    )

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005H",
        "stage": "ONE_SHOT_B0_BUILD",
        "candidate_ids": candidate_ids,
        "fill_events": int(len(fills)),
        "episodes_10s": int(len(episodes)),
        "controls_generated": int(replay["controls_built"]),
        "matched_control_rows_retained": int(
            len(matched_controls)
        ),
        "matched_pairs": int(len(pairs)),
        "replay_audit": replay,
        "join_audit": link_audit,
        "control_storage_policy": (
            "all 5-minute controls generated with frozen semantics; "
            "completed controls spooled disk-backed in bounded memory; "
            "final scorer input retains only the exact without-replacement "
            "matched controls referenced by HOLDOUT_MATCHED_CONTROLS.csv"
        ),
        "date_scope": {
            "start_utc": "2026-09-01T00:00:00Z",
            "end_exclusive_utc": "2026-09-22T00:00:00Z",
        },
        "b0_opened": True,
        "model_scoring_performed": False,
        "technical_retry_after_resource_failure": True,
        "scientific_spec_changed": False,
        "real_sig_orders_sent": False,
    }
    (WORK / "HOLDOUT_BUILD_SUMMARY.json").write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (WORK / "HOLDOUT_BUILD_REPORT.md").write_text(
        "# EXPERIMENT-005H — One-Shot B0 State Build\n\n"
        f"- Frozen candidates: **{len(candidate_ids)}**\n"
        f"- Fill events: **{len(fills):,}**\n"
        f"- 10-second episodes: **{len(episodes):,}**\n"
        f"- Controls generated: **{replay['controls_built']:,}**\n"
        f"- One-to-one matched controls: **{len(pairs):,}**\n"
        "- B0 date scope: **2026-09-01 through 2026-09-21 inclusive**\n"
        "- Technical retry after resource failure: **YES**\n"
        "- Scientific specification changed: **NO**\n"
        "- Model scoring in this kernel: **NO**\n\n"
        "REAL SIG ORDERS SENT: NO\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            summary,
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
