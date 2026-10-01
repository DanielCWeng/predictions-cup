# ruff: noqa: E501,I001
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


REQUIRED = {
    "event_type",
    "timestamp_received",
    "market",
    "asset_id",
    "best_bid",
    "best_ask",
    "bids",
    "asks",
    "price",
    "size",
    "side",
    "old_tick_size",
    "new_tick_size",
    "window_id",
    "source_version",
}

STATE_COLUMNS = [
    "bid_last",
    "ask_last",
    "last_quote_ts",
    "last_trade_ts",
    "bid_depth1",
    "ask_depth1",
    "bid_depth5",
    "ask_depth5",
    "depth_total5",
    "imbalance1",
    "imbalance5",
    "depth_concentration",
    "book_entropy",
    "book_shape_distance",
    "wall_ratio",
    "last_book_ts",
    "last_depth_update_ts",
    "depth_trusted",
]

COUNT_COLUMNS = [
    "quote_events",
    "bbo_changes",
    "price_change_count",
    "pc_buy_size_proxy",
    "pc_sell_size_proxy",
    "pc_zero_count",
    "trade_count",
    "trade_volume",
    "signed_trade_volume",
    "book_count",
    "depth_update_count",
    "level_delete_count",
    "visible_add_size",
    "visible_remove_size",
    "level_flow_signed",
    "ofi_top_event",
    "ofi_abs_event",
    "bbo_up_count",
    "bbo_down_count",
    "tick_change_count",
]


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def token_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def level_pairs(raw: Any, descending: bool) -> list[tuple[float, float]]:
    if raw is None:
        return []
    if isinstance(raw, np.ndarray):
        raw = raw.tolist()
    out: list[tuple[float, float]] = []
    try:
        items = list(raw)
    except TypeError:
        return []
    for item in items:
        try:
            if isinstance(item, dict):
                price = float(item["price"])
                size = float(item["size"])
            elif hasattr(item, "as_py"):
                v = item.as_py()
                price = float(v["price"])
                size = float(v["size"])
            else:
                price = float(item[0])
                size = float(item[1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(price) and math.isfinite(size) and size >= 0):
            continue
        out.append((price, size))
    out.sort(key=lambda x: x[0], reverse=descending)
    return out


def entropy(values: list[float]) -> float:
    total = float(sum(values))
    if total <= 0:
        return np.nan
    p = np.asarray(values, dtype=float) / total
    p = p[p > 0]
    if len(p) <= 1:
        return 0.0
    return float(-(p * np.log(p)).sum() / math.log(len(p)))


def book_features(row: pd.Series) -> pd.Series:
    bids = level_pairs(row["bids"], descending=True)
    asks = level_pairs(row["asks"], descending=False)
    if not bids or not asks:
        return pd.Series(
            {
                "bid_depth1": np.nan,
                "ask_depth1": np.nan,
                "bid_depth5": np.nan,
                "ask_depth5": np.nan,
                "depth_total5": np.nan,
                "imbalance1": np.nan,
                "imbalance5": np.nan,
                "depth_concentration": np.nan,
                "book_entropy": np.nan,
                "book_shape_distance": np.nan,
                "wall_ratio": np.nan,
            }
        )
    b5 = bids[:5]
    a5 = asks[:5]
    b1 = b5[0][1]
    a1 = a5[0][1]
    bd = sum(x[1] for x in b5)
    ad = sum(x[1] for x in a5)
    total = bd + ad
    denom1 = b1 + a1
    imbalance1 = (b1 - a1) / denom1 if denom1 > 0 else np.nan
    imbalance5 = (bd - ad) / total if total > 0 else np.nan
    top_total = b1 + a1
    concentration = top_total / total if total > 0 else np.nan
    ent = entropy([x[1] for x in b5] + [x[1] for x in a5])
    best_bid = b5[0][0]
    best_ask = a5[0][0]
    bdist = sum(s * (best_bid - p) for p, s in b5) / bd if bd > 0 else np.nan
    adist = sum(s * (p - best_ask) for p, s in a5) / ad if ad > 0 else np.nan
    shape = np.nanmean([bdist, adist])
    sizes = [x[1] for x in b5] + [x[1] for x in a5]
    mean_size = float(np.mean(sizes)) if sizes else np.nan
    wall = float(max(sizes) / mean_size) if mean_size and mean_size > 0 else np.nan
    return pd.Series(
        {
            "bid_depth1": b1,
            "ask_depth1": a1,
            "bid_depth5": bd,
            "ask_depth5": ad,
            "depth_total5": total,
            "imbalance1": imbalance1,
            "imbalance5": imbalance5,
            "depth_concentration": concentration,
            "book_entropy": ent,
            "book_shape_distance": shape,
            "wall_ratio": wall,
        }
    )


def map_book_features(
    bids_map: dict[float, float],
    asks_map: dict[float, float],
) -> dict[str, float]:
    bids = sorted(bids_map.items(), reverse=True)[:5]
    asks = sorted(asks_map.items())[:5]
    if not bids or not asks:
        return {
            "bid_depth1": np.nan,
            "ask_depth1": np.nan,
            "bid_depth5": np.nan,
            "ask_depth5": np.nan,
            "depth_total5": np.nan,
            "imbalance1": np.nan,
            "imbalance5": np.nan,
            "depth_concentration": np.nan,
            "book_entropy": np.nan,
            "book_shape_distance": np.nan,
            "wall_ratio": np.nan,
        }
    b1 = bids[0][1]
    a1 = asks[0][1]
    bd = sum(size for _, size in bids)
    ad = sum(size for _, size in asks)
    total = bd + ad
    denom1 = b1 + a1
    imbalance1 = (b1 - a1) / denom1 if denom1 > 0 else np.nan
    imbalance5 = (bd - ad) / total if total > 0 else np.nan
    concentration = (b1 + a1) / total if total > 0 else np.nan
    ent = entropy([size for _, size in bids] + [size for _, size in asks])
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    bdist = sum(size * (best_bid - price) for price, size in bids) / bd if bd > 0 else np.nan
    adist = sum(size * (price - best_ask) for price, size in asks) / ad if ad > 0 else np.nan
    shape = np.nanmean([bdist, adist])
    sizes = [size for _, size in bids] + [size for _, size in asks]
    mean_size = float(np.mean(sizes)) if sizes else np.nan
    wall = float(max(sizes) / mean_size) if mean_size and mean_size > 0 else np.nan
    return {
        "bid_depth1": b1,
        "ask_depth1": a1,
        "bid_depth5": bd,
        "ask_depth5": ad,
        "depth_total5": total,
        "imbalance1": imbalance1,
        "imbalance5": imbalance5,
        "depth_concentration": concentration,
        "book_entropy": ent,
        "book_shape_distance": shape,
        "wall_ratio": wall,
    }


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ofi_event(
    old_bid: float | None,
    old_ask: float | None,
    old_bid_size: float,
    old_ask_size: float,
    new_bid: float | None,
    new_ask: float | None,
    new_bid_size: float,
    new_ask_size: float,
) -> float:
    if old_bid is None or old_ask is None or new_bid is None or new_ask is None:
        return 0.0
    bid_term = (new_bid_size if new_bid >= old_bid else 0.0) - (
        old_bid_size if new_bid <= old_bid else 0.0
    )
    ask_term = -(new_ask_size if new_ask <= old_ask else 0.0) + (
        old_ask_size if new_ask >= old_ask else 0.0
    )
    return float(bid_term + ask_term)


def reconstruct_visible_book(
    df: pd.DataFrame,
    carry: dict[Any, dict[str, Any]],
) -> pd.DataFrame:
    events = df[df["event_type"].isin(["book", "price_change"])].copy()
    if events.empty:
        return pd.DataFrame()
    if "sequence" in events:
        events["_seq"] = pd.to_numeric(events["sequence"], errors="coerce").fillna(-1).astype("int64")
        events.sort_values(["timestamp_received", "_seq"], inplace=True, kind="stable")
    else:
        events.sort_values(["timestamp_received"], inplace=True, kind="stable")

    agg: dict[tuple[Any, pd.Timestamp], dict[str, Any]] = {}
    last_minute: dict[Any, pd.Timestamp] = {}

    def rec_for(asset: Any, minute: pd.Timestamp) -> dict[str, Any]:
        key = (asset, minute)
        if key not in agg:
            agg[key] = {
                "asset_id": asset,
                "minute": minute,
                "book_count": 0.0,
                "depth_update_count": 0.0,
                "level_delete_count": 0.0,
                "visible_add_size": 0.0,
                "visible_remove_size": 0.0,
                "level_flow_signed": 0.0,
                "ofi_top_event": 0.0,
                "ofi_abs_event": 0.0,
                "bbo_up_count": 0.0,
                "bbo_down_count": 0.0,
            }
        return agg[key]

    def finalise(asset: Any, minute: pd.Timestamp) -> None:
        state = carry.get(asset, {})
        bids_map = state.get("_bid_book")
        asks_map = state.get("_ask_book")
        if not isinstance(bids_map, dict) or not isinstance(asks_map, dict) or not bids_map or not asks_map:
            return
        rec = rec_for(asset, minute)
        rec.update(map_book_features(bids_map, asks_map))
        rec["last_book_ts"] = state.get("_last_book_ts")
        rec["last_depth_update_ts"] = state.get("_last_depth_update_ts")
        rec["depth_trusted"] = 1.0

    for row in events.itertuples(index=False):
        asset = row.asset_id
        minute = row.minute
        previous_minute = last_minute.get(asset)
        if previous_minute is not None and minute != previous_minute:
            finalise(asset, previous_minute)
        last_minute[asset] = minute
        rec = rec_for(asset, minute)
        state = carry.setdefault(asset, {})

        if str(row.event_type) == "book":
            bid_pairs = level_pairs(row.bids, descending=True)
            ask_pairs = level_pairs(row.asks, descending=False)
            if not bid_pairs or not ask_pairs:
                continue
            state["_bid_book"] = {price: size for price, size in bid_pairs if size > 0}
            state["_ask_book"] = {price: size for price, size in ask_pairs if size > 0}
            state["_best_bid"] = max(state["_bid_book"])
            state["_best_ask"] = min(state["_ask_book"])
            state["_last_book_ts"] = row.timestamp_received
            state["_last_depth_update_ts"] = row.timestamp_received
            rec["book_count"] += 1.0
            continue

        bids_map = state.get("_bid_book")
        asks_map = state.get("_ask_book")
        if not isinstance(bids_map, dict) or not isinstance(asks_map, dict) or not bids_map or not asks_map:
            continue

        price = _safe_float(row.price)
        size = _safe_float(row.size)
        if price is None or size is None:
            continue
        side = str(row.side).upper()
        if side not in {"BUY", "SELL"}:
            continue

        old_bid = _safe_float(state.get("_best_bid"))
        old_ask = _safe_float(state.get("_best_ask"))
        old_bid_size = bids_map.get(old_bid, 0.0) if old_bid is not None else 0.0
        old_ask_size = asks_map.get(old_ask, 0.0) if old_ask is not None else 0.0
        old_mid = (old_bid + old_ask) / 2.0 if old_bid is not None and old_ask is not None else None

        target = bids_map if side == "BUY" else asks_map
        old_level_size = float(target.get(price, 0.0))
        delta = size - old_level_size
        if size <= 0:
            target.pop(price, None)
            rec["level_delete_count"] += 1.0
        else:
            target[price] = size

        reported_bid = _safe_float(row.best_bid)
        reported_ask = _safe_float(row.best_ask)
        new_bid = reported_bid if reported_bid is not None else (max(bids_map) if bids_map else None)
        new_ask = reported_ask if reported_ask is not None else (min(asks_map) if asks_map else None)
        state["_best_bid"] = new_bid
        state["_best_ask"] = new_ask
        state["_last_depth_update_ts"] = row.timestamp_received

        new_bid_size = bids_map.get(new_bid, 0.0) if new_bid is not None else 0.0
        new_ask_size = asks_map.get(new_ask, 0.0) if new_ask is not None else 0.0
        event_ofi = _ofi_event(
            old_bid,
            old_ask,
            old_bid_size,
            old_ask_size,
            new_bid,
            new_ask,
            new_bid_size,
            new_ask_size,
        )
        rec["depth_update_count"] += 1.0
        rec["visible_add_size"] += max(delta, 0.0)
        rec["visible_remove_size"] += max(-delta, 0.0)
        rec["level_flow_signed"] += delta if side == "BUY" else -delta
        rec["ofi_top_event"] += event_ofi
        rec["ofi_abs_event"] += abs(event_ofi)

        if old_mid is not None and new_bid is not None and new_ask is not None:
            new_mid = (new_bid + new_ask) / 2.0
            if new_mid > old_mid:
                rec["bbo_up_count"] += 1.0
            elif new_mid < old_mid:
                rec["bbo_down_count"] += 1.0

    for asset, minute in last_minute.items():
        finalise(asset, minute)

    if not agg:
        return pd.DataFrame()
    return pd.DataFrame(list(agg.values()))


def aggregate_hour(path: Path, carry: dict[Any, dict[str, Any]], tail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[Any, dict[str, Any]], pd.DataFrame, dict[str, int]]:
    pf = pq.ParquetFile(path)
    missing = REQUIRED - set(pf.schema_arrow.names)
    if missing:
        raise RuntimeError(f"V3 worker missing columns {sorted(missing)} in {path}")

    cols = sorted(REQUIRED)
    df = pf.read(columns=cols, use_threads=True).to_pandas()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame(), carry, tail, {}

    df["timestamp_received"] = pd.to_datetime(df["timestamp_received"], utc=True)
    df["minute"] = df["timestamp_received"].dt.floor("min")
    df["event_type"] = df["event_type"].astype(str)
    asset_market = (
        df.loc[df["asset_id"].notna() & df["market"].notna(), ["asset_id", "market"]]
        .drop_duplicates("asset_id", keep="last")
        .set_index("asset_id")["market"]
        .to_dict()
    )
    counts = {str(k): int(v) for k, v in df["event_type"].value_counts(dropna=False).items()}

    quote = df[df["best_bid"].notna() & df["best_ask"].notna()].copy()
    quote["best_bid"] = quote["best_bid"].astype(float)
    quote["best_ask"] = quote["best_ask"].astype(float)
    quote = quote[(quote["best_bid"] >= 0) & (quote["best_ask"] <= 1) & (quote["best_ask"] >= quote["best_bid"])]
    quote.sort_values(["asset_id", "timestamp_received"], inplace=True)
    quote["mid"] = (quote["best_bid"] + quote["best_ask"]) / 2.0
    quote["spread_calc"] = quote["best_ask"] - quote["best_bid"]
    quote["prev_mid_event"] = quote.groupby("asset_id", sort=False)["mid"].shift(1)
    quote["bbo_changed"] = (quote["mid"] != quote["prev_mid_event"]).astype(int)

    if quote.empty:
        qagg = pd.DataFrame()
    else:
        qagg = (
            quote.groupby(["asset_id", "minute"], sort=False)
            .agg(
                quote_events=("event_type", "size"),
                bbo_changes=("bbo_changed", "sum"),
                bid_last=("best_bid", "last"),
                ask_last=("best_ask", "last"),
                mid_first=("mid", "first"),
                mid_last=("mid", "last"),
                mid_min=("mid", "min"),
                mid_max=("mid", "max"),
                spread_last=("spread_calc", "last"),
                last_quote_ts=("timestamp_received", "max"),
            )
            .reset_index()
        )

    pc = df[df["event_type"].eq("price_change")].copy()
    if pc.empty:
        pcagg = pd.DataFrame()
    else:
        pc["size_f"] = pd.to_numeric(pc["size"], errors="coerce").fillna(0.0).astype(float)
        pc["buy_size"] = np.where(pc["side"].astype(str).eq("BUY"), pc["size_f"], 0.0)
        pc["sell_size"] = np.where(pc["side"].astype(str).eq("SELL"), pc["size_f"], 0.0)
        pc["zero_size"] = pc["size_f"].eq(0.0).astype(int)
        pcagg = (
            pc.groupby(["asset_id", "minute"], sort=False)
            .agg(
                price_change_count=("event_type", "size"),
                pc_buy_size_proxy=("buy_size", "sum"),
                pc_sell_size_proxy=("sell_size", "sum"),
                pc_zero_count=("zero_size", "sum"),
            )
            .reset_index()
        )

    tr = df[df["event_type"].eq("last_trade_price")].copy()
    if tr.empty:
        tragg = pd.DataFrame()
        hazard_trade = pd.DataFrame()
    else:
        tr["size_f"] = pd.to_numeric(tr["size"], errors="coerce").fillna(0.0).astype(float)
        tr["price_f"] = pd.to_numeric(tr["price"], errors="coerce").astype(float)
        tr["signed_volume"] = np.where(
            tr["side"].astype(str).eq("BUY"),
            tr["size_f"],
            np.where(tr["side"].astype(str).eq("SELL"), -tr["size_f"], 0.0),
        )
        tr.sort_values(["asset_id", "timestamp_received"], inplace=True)
        tr["interarrival_s"] = (
            tr.groupby("asset_id", sort=False)["timestamp_received"].diff().dt.total_seconds()
        )
        tragg = (
            tr.groupby(["asset_id", "minute"], sort=False)
            .agg(
                trade_count=("event_type", "size"),
                trade_volume=("size_f", "sum"),
                signed_trade_volume=("signed_volume", "sum"),
                trade_price_last=("price_f", "last"),
                last_trade_ts=("timestamp_received", "max"),
            )
            .reset_index()
        )
        hazard_trade = tr.loc[
            tr["interarrival_s"].notna(),
            ["asset_id", "timestamp_received", "interarrival_s", "price_f", "size_f", "side"],
        ].copy()
        hazard_trade["kind"] = "TRADE"

    bagg = reconstruct_visible_book(df, carry)

    ticks = df[df["event_type"].eq("tick_size_change")]
    if ticks.empty:
        tickagg = pd.DataFrame()
    else:
        tickagg = (
            ticks.groupby(["asset_id", "minute"], sort=False)
            .size()
            .rename("tick_change_count")
            .reset_index()
        )

    hazard_quote = pd.DataFrame()
    if not quote.empty:
        hazard_quote = quote[["asset_id", "timestamp_received", "mid", "spread_calc"]].copy()
        hazard_quote["interarrival_s"] = (
            hazard_quote.groupby("asset_id", sort=False)["timestamp_received"].diff().dt.total_seconds()
        )
        hazard_quote = hazard_quote[hazard_quote["interarrival_s"].notna()]
        hazard_quote["kind"] = "QUOTE"

    tokens = set(carry)
    tokens.update(df["asset_id"].dropna().unique().tolist())
    if not tokens:
        return pd.DataFrame(), pd.DataFrame(), carry, tail, counts
    start = df["minute"].min()
    end = df["minute"].max()
    minutes = pd.date_range(start, end, freq="1min", tz="UTC")
    grid = pd.MultiIndex.from_product(
        [list(tokens), minutes],
        names=["asset_id", "minute"],
    ).to_frame(index=False)

    panel = grid
    for part in (qagg, pcagg, tragg, bagg, tickagg):
        if not part.empty:
            panel = panel.merge(part, on=["asset_id", "minute"], how="left")

    for col in COUNT_COLUMNS:
        if col not in panel:
            panel[col] = 0.0
        panel[col] = panel[col].fillna(0.0)

    for col in STATE_COLUMNS:
        if col not in panel:
            panel[col] = pd.NaT if col.endswith("_ts") else np.nan

    panel.sort_values(["asset_id", "minute"], inplace=True)

    numeric_state = [c for c in STATE_COLUMNS if not c.endswith("_ts")]
    time_state = [c for c in STATE_COLUMNS if c.endswith("_ts")]
    for col in numeric_state + time_state:
        panel[col] = panel.groupby("asset_id", sort=False)[col].ffill()
        lead = panel["asset_id"].map(lambda x, key=col: carry.get(x, {}).get(key))
        panel[col] = panel[col].where(panel[col].notna(), lead)

    for col in numeric_state + COUNT_COLUMNS:
        panel[col] = pd.to_numeric(panel[col], errors="coerce").astype(float)
    for col in time_state:
        panel[col] = pd.to_datetime(panel[col], utc=True, errors="coerce")

    market_map = {token: state.get("market_id") for token, state in carry.items()}
    market_map.update(asset_market)
    panel["market_id"] = panel["asset_id"].map(market_map)

    panel["mid"] = (panel["bid_last"] + panel["ask_last"]) / 2.0
    panel["spread"] = panel["ask_last"] - panel["bid_last"]
    panel["relative_spread"] = panel["spread"] / panel["mid"].clip(lower=0.001)
    panel["mid"] = pd.to_numeric(panel["mid"], errors="coerce").astype(float)
    clipped_mid = panel["mid"].clip(lower=0.001, upper=0.999).astype(float)
    panel["logit_mid"] = np.log(clipped_mid / (1.0 - clipped_mid))

    minute_end = panel["minute"] + pd.Timedelta(minutes=1)
    panel["age_quote_s"] = (minute_end - panel["last_quote_ts"]).dt.total_seconds()
    panel["age_trade_s"] = (minute_end - panel["last_trade_ts"]).dt.total_seconds()
    panel["age_book_s"] = (minute_end - panel["last_book_ts"]).dt.total_seconds()
    panel["age_depth_update_s"] = (minute_end - panel["last_depth_update_ts"]).dt.total_seconds()

    panel["pressure_proxy"] = (
        panel["pc_buy_size_proxy"] - panel["pc_sell_size_proxy"]
    ) / (panel["pc_buy_size_proxy"] + panel["pc_sell_size_proxy"] + 1e-9)

    if not tail.empty:
        calc = pd.concat([tail, panel], ignore_index=True, sort=False)
    else:
        calc = panel.copy()
    calc.sort_values(["asset_id", "minute"], inplace=True)
    g = calc.groupby("asset_id", sort=False)
    calc["ret_1m"] = g["mid"].diff(1)
    calc["ret_5m"] = g["mid"].diff(5)
    calc["spread_delta_1m"] = g["spread"].diff(1)
    calc["depth_delta_1m"] = g["depth_total5"].diff(1)
    calc["observed_step"] = calc["ret_1m"].abs()
    calc["rv_5m"] = (
        g["ret_1m"]
        .rolling(window=5, min_periods=3)
        .std()
        .reset_index(level=0, drop=True)
    )
    calc["withdrawal_1m"] = (-calc["depth_delta_1m"]).clip(lower=0)
    calc["replenishment_1m"] = calc["depth_delta_1m"].clip(lower=0)

    depth_denom = calc["bid_depth1"] + calc["ask_depth1"]
    calc["microprice"] = (
        calc["ask_last"] * calc["bid_depth1"]
        + calc["bid_last"] * calc["ask_depth1"]
    ) / depth_denom.replace(0, np.nan)
    calc["microprice_dev"] = calc["microprice"] - calc["mid"]

    prev_bid = g["bid_last"].shift(1)
    prev_ask = g["ask_last"].shift(1)
    prev_bsz = g["bid_depth1"].shift(1)
    prev_asz = g["ask_depth1"].shift(1)
    bid_term = np.where(
        calc["bid_last"] >= prev_bid,
        calc["bid_depth1"].fillna(0),
        0,
    ) - np.where(calc["bid_last"] <= prev_bid, prev_bsz.fillna(0), 0)
    ask_term = -np.where(
        calc["ask_last"] <= prev_ask,
        calc["ask_depth1"].fillna(0),
        0,
    ) + np.where(calc["ask_last"] >= prev_ask, prev_asz.fillna(0), 0)
    calc["ofi_top_proxy"] = bid_term + ask_term
    calc["ofi_depth_norm"] = calc["ofi_top_proxy"] / calc["depth_total5"].replace(0, np.nan)
    calc["ofi_top_event_norm"] = calc["ofi_top_event"] / calc["depth_total5"].replace(0, np.nan)
    calc["level_flow_norm"] = calc["level_flow_signed"] / calc["depth_total5"].replace(0, np.nan)
    calc["visible_turnover_norm"] = (
        calc["visible_add_size"] + calc["visible_remove_size"]
    ) / calc["depth_total5"].replace(0, np.nan)
    calc["replenishment_ratio_event"] = calc["visible_add_size"] / (
        calc["visible_remove_size"] + 1e-9
    )
    calc["visible_add_share"] = calc["visible_add_size"] / (
        calc["visible_add_size"] + calc["visible_remove_size"] + 1e-9
    )
    calc["visible_net_flow"] = (
        calc["visible_add_size"] - calc["visible_remove_size"]
    ) / (calc["visible_add_size"] + calc["visible_remove_size"] + 1e-9)

    current_mask = calc["minute"].between(start, end)
    panel = calc.loc[current_mask].copy()
    gp = panel.groupby("asset_id", sort=False)
    panel["future_ret_1m"] = gp["mid"].shift(-1) - panel["mid"]
    panel["future_ret_5m"] = gp["mid"].shift(-5) - panel["mid"]
    panel["future_abs_5m"] = panel["future_ret_5m"].abs()
    panel["future_quote_events_1m"] = gp["quote_events"].shift(-1)
    panel["future_trade_count_1m"] = gp["trade_count"].shift(-1)
    panel["continuation_5m"] = np.sign(panel["ret_5m"]) * np.sign(panel["future_ret_5m"])

    common = (
        panel.groupby("minute", sort=False)
        .agg(
            common_median_spread=("spread", "median"),
            common_median_depth=("depth_total5", "median"),
            common_quote_events=("quote_events", "mean"),
            common_trade_count=("trade_count", "mean"),
            common_rv_5m=("rv_5m", "median"),
        )
        .reset_index()
    )
    panel = panel.merge(common, on="minute", how="left")

    for token, last in panel.groupby("asset_id", sort=False).tail(1).set_index("asset_id").iterrows():
        state = carry.setdefault(token, {})
        if token in market_map and market_map[token] is not None:
            state["market_id"] = market_map[token]
        for col in STATE_COLUMNS:
            value = last.get(col)
            if pd.notna(value):
                state[col] = value

    tail_cols = [
        "asset_id",
        "minute",
        "mid",
        "spread",
        "bid_last",
        "ask_last",
        "bid_depth1",
        "ask_depth1",
        "depth_total5",
    ]
    new_tail = calc.groupby("asset_id", sort=False).tail(6)[tail_cols].copy()

    hazard = pd.concat([hazard_quote, hazard_trade], ignore_index=True, sort=False)
    return panel, hazard, carry, new_tail, counts


def stratified_sample(panel: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    if panel.empty:
        return panel
    depth = panel[panel["depth_trusted"].fillna(0).gt(0)]
    n_depth = min(len(depth), int(n * 0.35))
    a = depth.sample(n=n_depth, random_state=seed) if n_depth else depth.iloc[:0]

    remaining = panel.drop(index=a.index, errors="ignore")
    active = remaining[(remaining["quote_events"] + remaining["trade_count"]) > 0]
    n_active = min(len(active), int(n * 0.4))
    b = active.sample(n=n_active, random_state=seed + 1) if n_active else active.iloc[:0]

    remaining = remaining.drop(index=b.index, errors="ignore")
    n_other = min(len(remaining), n - len(a) - len(b))
    c = remaining.sample(n=n_other, random_state=seed + 2) if n_other else remaining.iloc[:0]
    return pd.concat([a, b, c], ignore_index=True)


def summarize_market(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    return (
        panel.groupby("asset_id", sort=False)
        .agg(
            minutes=("minute", "size"),
            active_minutes=("quote_events", lambda x: int((x > 0).sum())),
            quote_events=("quote_events", "sum"),
            trade_count=("trade_count", "sum"),
            trade_volume=("trade_volume", "sum"),
            mean_spread=("spread", "mean"),
            median_spread=("spread", "median"),
            mean_depth=("depth_total5", "mean"),
            median_depth=("depth_total5", "median"),
            mean_abs_ret_1m=("ret_1m", lambda x: float(x.abs().mean())),
            mean_age_quote_s=("age_quote_s", "mean"),
            mean_imbalance5=("imbalance5", "mean"),
        )
        .reset_index()
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    dataset = str(spec["dataset"])
    files = [str(x) for x in spec["files"]]
    worker_id = str(spec["worker_id"])
    sample_per_hour = int(spec["sample_per_hour"])
    seed = int(spec["seed"])
    surface = str(spec["surface"])

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    temp = out / "_downloads"
    temp.mkdir(parents=True, exist_ok=True)

    carry: dict[Any, dict[str, Any]] = {}
    tail = pd.DataFrame()
    panel_samples: list[pd.DataFrame] = []
    hazard_samples: list[pd.DataFrame] = []
    hour_rows: list[dict[str, Any]] = []
    market_parts: list[pd.DataFrame] = []
    event_counts: Counter[str] = Counter()
    failures: list[dict[str, str]] = []

    for i, file_name in enumerate(files):
        file_dir = temp / f"{i:04d}"
        file_dir.mkdir(parents=True, exist_ok=True)
        try:
            run(
                [
                    "kaggle",
                    "datasets",
                    "download",
                    dataset,
                    "-f",
                    file_name,
                    "-p",
                    str(file_dir),
                    "--unzip",
                ]
            )
            parquets = sorted(file_dir.rglob("*.parquet"))
            if len(parquets) != 1:
                raise RuntimeError(f"expected one parquet, found {len(parquets)}")
            path = parquets[0]
            panel, hazard, carry, tail, counts = aggregate_hour(path, carry, tail)
            event_counts.update(counts)
            if not panel.empty:
                samp = stratified_sample(panel, sample_per_hour, seed + i * 17)
                samp["source_file"] = file_name
                samp["worker_id"] = worker_id
                samp["surface"] = surface
                panel_samples.append(samp)
                market_parts.append(summarize_market(panel))
            if not hazard.empty:
                hs = hazard.sample(
                    n=min(len(hazard), 1000),
                    random_state=seed + i * 29,
                )
                hs["source_file"] = file_name
                hs["worker_id"] = worker_id
                hs["surface"] = surface
                hazard_samples.append(hs)
            hour_rows.append(
                {
                    "file": file_name,
                    "rows": int(pq.ParquetFile(path).metadata.num_rows),
                    "tokens": int(panel["asset_id"].nunique()) if not panel.empty else 0,
                    "panel_rows": int(len(panel)),
                    "sample_rows": int(len(panel_samples[-1])) if panel_samples else 0,
                    **{f"event_{k}": int(v) for k, v in counts.items()},
                }
            )
            print(
                f"005I_WORKER worker={worker_id} {i + 1}/{len(files)} "
                f"rows={hour_rows[-1]['rows']} panel={hour_rows[-1]['panel_rows']}",
                flush=True,
            )
        except Exception as exc:
            failures.append({"file": file_name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"005I_WORKER_ERROR file={file_name} error={exc}", flush=True)
        finally:
            shutil.rmtree(file_dir, ignore_errors=True)

    panel_out = pd.concat(panel_samples, ignore_index=True, sort=False) if panel_samples else pd.DataFrame()
    hazard_out = pd.concat(hazard_samples, ignore_index=True, sort=False) if hazard_samples else pd.DataFrame()
    hours_out = pd.DataFrame(hour_rows)
    markets_out = pd.concat(market_parts, ignore_index=True, sort=False) if market_parts else pd.DataFrame()
    if not markets_out.empty:
        markets_out = (
            markets_out.groupby("asset_id", sort=False)
            .agg(
                minutes=("minutes", "sum"),
                active_minutes=("active_minutes", "sum"),
                quote_events=("quote_events", "sum"),
                trade_count=("trade_count", "sum"),
                trade_volume=("trade_volume", "sum"),
                mean_spread=("mean_spread", "mean"),
                median_spread=("median_spread", "median"),
                mean_depth=("mean_depth", "mean"),
                median_depth=("median_depth", "median"),
                mean_abs_ret_1m=("mean_abs_ret_1m", "mean"),
                mean_age_quote_s=("mean_age_quote_s", "mean"),
                mean_imbalance5=("mean_imbalance5", "mean"),
            )
            .reset_index()
        )

    def stringify_token_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if not frame.empty and "asset_id" in frame:
            frame = frame.copy()
            frame["asset_id"] = frame["asset_id"].map(token_text)
        if not frame.empty and "market_id" in frame:
            frame = frame.copy()
            frame["market_id"] = frame["market_id"].map(token_text)
        return frame

    panel_out = stringify_token_frame(panel_out)
    hazard_out = stringify_token_frame(hazard_out)
    markets_out = stringify_token_frame(markets_out)

    panel_out.to_csv(out / "PANEL_SAMPLE.csv.gz", index=False, compression="gzip")
    hazard_out.to_csv(out / "HAZARD_SAMPLE.csv.gz", index=False, compression="gzip")
    hours_out.to_csv(out / "HOUR_SUMMARY.csv", index=False)
    markets_out.to_csv(out / "MARKET_SUMMARY.csv.gz", index=False, compression="gzip")

    numeric = panel_out.select_dtypes(include=[np.number]) if not panel_out.empty else pd.DataFrame()
    quantiles: dict[str, dict[str, float | None]] = {}
    for col in numeric.columns:
        values = numeric[col].replace([np.inf, -np.inf], np.nan).dropna()
        if values.empty:
            continue
        q = values.quantile([0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
        quantiles[col] = {str(k): float(v) for k, v in q.items()}

    summary = {
        "schema_version": 1,
        "experiment": "EXPERIMENT-005I",
        "worker_id": worker_id,
        "surface": surface,
        "files_requested": len(files),
        "files_completed": len(hour_rows),
        "files_failed": len(failures),
        "failures": failures,
        "panel_sample_rows": int(len(panel_out)),
        "hazard_sample_rows": int(len(hazard_out)),
        "markets": int(panel_out["asset_id"].nunique()) if not panel_out.empty else 0,
        "event_type_counts": dict(event_counts),
        "quantiles": quantiles,
        "holdout_read": surface.upper() == "HOLDOUT",
        "make_modified": False,
        "real_sig_orders_sent": False,
    }
    (out / "WORKER_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
