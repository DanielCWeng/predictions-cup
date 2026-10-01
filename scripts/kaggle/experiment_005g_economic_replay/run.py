from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

INPUT = Path("/kaggle/input/sig-cup-data003-orderbooks")
HERE = Path(__file__).resolve().parent
OUT = Path("/kaggle/working/005g_economic_replay_v1")
OUT.mkdir(parents=True, exist_ok=True)

EXPERIMENT = "EXPERIMENT-005G"
PROTOCOL = "005G_ECONOMIC_REPLAY_V1"
TRAIN_DATE = "2026-09-06"
DEV_DATE = "2026-09-07"
HOLDOUT_DATE = "2026-09-08"
GRID_SECONDS = 30
FORWARD_SECONDS = 60
TRANSITION_SECONDS = 300
MAX_STATE_AGE_SECONDS = 300
MIN_SUPPORT_FILLS = 50
SEED = 20261001005

FEATURES = [
    "state_dwell_s",
    "genuine_age_s",
    "spread_x_distance",
    "distance_from_0_5",
    "volatility_x_liquidity",
    "price_change_age_s",
]
POLICY_QUANTILES = [0.50, 0.65, 0.80, 0.90]
WIDTH_EXTRAS = [0.005, 0.01]
SIZE_MULTIPLIERS = [0.25, 0.50]
REFRESH_SECONDS = [15, 30]

TOKEN_PAYLOAD = json.loads((HERE / "token_ids.json").read_text(encoding="utf-8"))
TOKEN_IDS = [str(x) for x in TOKEN_PAYLOAD["token_ids"]]
TOKEN_LOOKUP = {
    int(token).to_bytes(32, byteorder="big", signed=False): token
    for token in TOKEN_IDS
}
TOKEN_BYTES = pa.array(list(TOKEN_LOOKUP), type=pa.binary())


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_json(name: str, value: Any) -> None:
    (OUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values.astype(float), 1e-6, 1 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def hour_path(date: str, hour: int) -> Path:
    return INPUT / "baseline_sep" / f"date={date}" / f"hour={hour:02d}" / "events.parquet"


@dataclass
class DayData:
    states: pd.DataFrame
    bbo: pd.DataFrame
    trades: pd.DataFrame
    audit: dict[str, Any]


def _tokenize(series: pd.Series) -> pd.Series:
    return series.map(TOKEN_LOOKUP)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def load_day(date: str) -> DayData:
    state_parts: list[pd.DataFrame] = []
    bbo_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    rows_raw = 0
    rows_selected = 0
    files = []

    wanted = [
        "event_type",
        "timestamp_received",
        "asset_id",
        "best_bid",
        "best_ask",
        "price",
        "size",
        "fee_rate_bps",
    ]
    event_values = pa.array(["price_change", "best_bid_ask", "last_trade_price"])

    for hour in range(24):
        path = hour_path(date, hour)
        if not path.is_file():
            raise FileNotFoundError(path)
        pf = pq.ParquetFile(path)
        rows_raw += int(pf.metadata.num_rows)
        table = pq.read_table(path, columns=wanted, use_threads=True)
        event_type = pc.cast(table["event_type"], pa.string())
        event_mask = pc.fill_null(pc.is_in(event_type, value_set=event_values), False)
        table = table.filter(event_mask)
        token_mask = pc.fill_null(
            pc.is_in(table["asset_id"], value_set=TOKEN_BYTES),
            False,
        )
        table = table.filter(token_mask)
        rows_selected += table.num_rows
        files.append(str(path.relative_to(INPUT)))

        if table.num_rows == 0:
            continue

        frame = table.to_pandas()
        frame["token_id"] = _tokenize(frame["asset_id"])
        frame["time"] = pd.to_datetime(frame["timestamp_received"], utc=True)
        frame["event_type"] = frame["event_type"].astype(str)

        states = frame[frame["event_type"] == "price_change"].copy()
        if not states.empty:
            states["best_bid"] = _numeric(states["best_bid"])
            states["best_ask"] = _numeric(states["best_ask"])
            states = states[
                states["token_id"].notna()
                & states["best_bid"].notna()
                & states["best_ask"].notna()
                & (states["best_bid"] >= 0.0)
                & (states["best_ask"] <= 1.0)
                & (states["best_bid"] < states["best_ask"])
            ].copy()
            if not states.empty:
                states["bucket"] = states["time"].dt.floor(f"{GRID_SECONDS}s")
                states = (
                    states.sort_values(["token_id", "time"])
                    .groupby(["token_id", "bucket"], sort=False, as_index=False)
                    .tail(1)
                )
                states = states[
                    ["token_id", "time", "best_bid", "best_ask", "bucket"]
                ].rename(columns={"time": "state_time"})
                state_parts.append(states)

        bbo = frame[frame["event_type"] == "best_bid_ask"].copy()
        if not bbo.empty:
            bbo["best_bid"] = _numeric(bbo["best_bid"])
            bbo["best_ask"] = _numeric(bbo["best_ask"])
            bbo = bbo[
                bbo["token_id"].notna()
                & bbo["best_bid"].notna()
                & bbo["best_ask"].notna()
                & (bbo["best_bid"] >= 0.0)
                & (bbo["best_ask"] <= 1.0)
                & (bbo["best_bid"] < bbo["best_ask"])
            ].copy()
            if not bbo.empty:
                bbo["spread"] = bbo["best_ask"] - bbo["best_bid"]
                bbo_parts.append(
                    bbo[["token_id", "time", "best_bid", "best_ask", "spread"]]
                    .rename(columns={"time": "bbo_time"})
                )

        trades = frame[frame["event_type"] == "last_trade_price"].copy()
        if not trades.empty:
            trades["price"] = _numeric(trades["price"])
            trades["size"] = _numeric(trades["size"]).fillna(0.0)
            trades["fee_rate_bps"] = _numeric(trades["fee_rate_bps"]).fillna(0.0)
            trades = trades[
                trades["token_id"].notna()
                & trades["price"].notna()
                & (trades["price"] >= 0.0)
                & (trades["price"] <= 1.0)
            ].copy()
            if not trades.empty:
                trade_parts.append(
                    trades[["token_id", "time", "price", "size", "fee_rate_bps"]]
                    .rename(columns={"time": "trade_time"})
                )

        print(
            f"LOAD_DAY date={date} hour={hour:02d} selected={table.num_rows}",
            flush=True,
        )

    states = (
        pd.concat(state_parts, ignore_index=True)
        if state_parts
        else pd.DataFrame(columns=["token_id", "state_time", "best_bid", "best_ask", "bucket"])
    )
    bbo = (
        pd.concat(bbo_parts, ignore_index=True)
        if bbo_parts
        else pd.DataFrame(columns=["token_id", "bbo_time", "best_bid", "best_ask", "spread"])
    )
    trades = (
        pd.concat(trade_parts, ignore_index=True)
        if trade_parts
        else pd.DataFrame(columns=["token_id", "trade_time", "price", "size", "fee_rate_bps"])
    )
    states = states.sort_values(["token_id", "state_time"]).reset_index(drop=True)
    bbo = bbo.sort_values(["token_id", "bbo_time"]).reset_index(drop=True)
    trades = trades.sort_values(["token_id", "trade_time"]).reset_index(drop=True)

    return DayData(
        states=states,
        bbo=bbo,
        trades=trades,
        audit={
            "date": date,
            "files": files,
            "raw_rows": rows_raw,
            "selected_rows": rows_selected,
            "state_rows_30s": int(len(states)),
            "bbo_rows": int(len(bbo)),
            "trade_rows": int(len(trades)),
            "state_tokens": int(states["token_id"].nunique()) if not states.empty else 0,
            "bbo_tokens": int(bbo["token_id"].nunique()) if not bbo.empty else 0,
            "trade_tokens": int(trades["token_id"].nunique()) if not trades.empty else 0,
        },
    )


def train_spread_thresholds(bbo: pd.DataFrame) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    if bbo.empty:
        raise RuntimeError("TRAIN has no native BBO observations")
    global_q = tuple(float(x) for x in bbo["spread"].quantile([1 / 3, 2 / 3]).tolist())
    thresholds: dict[str, tuple[float, float]] = {}
    for token, part in bbo.groupby("token_id", sort=False):
        if len(part) < 10:
            thresholds[str(token)] = global_q
            continue
        q1, q2 = (float(x) for x in part["spread"].quantile([1 / 3, 2 / 3]).tolist())
        thresholds[str(token)] = global_q if (not np.isfinite(q1 + q2) or q1 >= q2) else (q1, q2)
    return thresholds, global_q


def _spread_states(spreads: np.ndarray, q1: float, q2: float) -> np.ndarray:
    return np.select([spreads <= q1, spreads <= q2], [0, 1], default=2).astype(np.int8)


def build_panel(
    day: DayData,
    *,
    date: str,
    thresholds: dict[str, tuple[float, float]],
    global_thresholds: tuple[float, float],
) -> pd.DataFrame:
    start = pd.Timestamp(f"{date}T00:00:00Z")
    end = start + pd.Timedelta(days=1)
    grid = pd.date_range(
        start + pd.Timedelta(seconds=GRID_SECONDS),
        end - pd.Timedelta(seconds=TRANSITION_SECONDS),
        freq=f"{GRID_SECONDS}s",
        tz="UTC",
    )
    parts: list[pd.DataFrame] = []

    bbo_groups = {str(k): v for k, v in day.bbo.groupby("token_id", sort=False)}
    for n, (token, states) in enumerate(day.states.groupby("token_id", sort=False), 1):
        token = str(token)
        states = states.sort_values("state_time").copy()
        right = states[["state_time", "best_bid", "best_ask"]].drop_duplicates("state_time")
        frame = pd.DataFrame({"time": grid})
        frame = pd.merge_asof(
            frame,
            right,
            left_on="time",
            right_on="state_time",
            direction="backward",
            tolerance=pd.Timedelta(seconds=MAX_STATE_AGE_SECONDS),
        )
        frame["token_id"] = token
        frame["state_age_s"] = (frame["time"] - frame["state_time"]).dt.total_seconds()
        frame["genuine_age_s"] = frame["state_age_s"]
        frame["price_change_age_s"] = frame["state_age_s"]
        frame["midpoint"] = (frame["best_bid"] + frame["best_ask"]) / 2.0
        frame["spread"] = frame["best_ask"] - frame["best_bid"]
        frame["distance_from_0_5"] = (frame["midpoint"] - 0.5).abs()
        frame["spread_x_distance"] = frame["spread"] * frame["distance_from_0_5"]

        lp = pd.Series(logit(frame["midpoint"].to_numpy(float)), index=frame.index)
        ret = lp.diff()
        rv = np.sqrt(ret.pow(2).rolling(2, min_periods=1).sum())
        frame["volatility_x_liquidity"] = rv * frame["spread"]

        bbo = bbo_groups.get(token)
        if bbo is None or bbo.empty:
            frame["state_dwell_s"] = np.nan
            frame["bbo_update_h60"] = np.nan
            frame["state_transition_h300"] = np.nan
        else:
            bbo = bbo.sort_values("bbo_time")
            bbo_ns = bbo["bbo_time"].astype("int64").to_numpy()
            query_ns = frame["time"].astype("int64").to_numpy()
            q1, q2 = thresholds.get(token, global_thresholds)
            s = _spread_states(bbo["spread"].to_numpy(float), q1, q2)
            trans = np.r_[True, s[1:] != s[:-1]]
            trans_ns = bbo_ns[trans]

            last_bbo_idx = np.searchsorted(bbo_ns, query_ns, side="right") - 1
            current_ok = last_bbo_idx >= 0
            bbo_age = np.full(len(frame), np.nan)
            bbo_age[current_ok] = (query_ns[current_ok] - bbo_ns[last_bbo_idx[current_ok]]) / 1e9

            last_trans_idx = np.searchsorted(trans_ns, query_ns, side="right") - 1
            dwell = np.full(len(frame), np.nan)
            ok_trans = last_trans_idx >= 0
            dwell[ok_trans] = (query_ns[ok_trans] - trans_ns[last_trans_idx[ok_trans]]) / 1e9
            dwell[bbo_age > MAX_STATE_AGE_SECONDS] = np.nan
            frame["state_dwell_s"] = dwell

            right60 = np.searchsorted(bbo_ns, query_ns + FORWARD_SECONDS * 1_000_000_000, side="right")
            left60 = np.searchsorted(bbo_ns, query_ns, side="right")
            update = (right60 > left60).astype(float)
            update[bbo_age > MAX_STATE_AGE_SECONDS] = np.nan
            frame["bbo_update_h60"] = update

            right300 = np.searchsorted(trans_ns, query_ns + TRANSITION_SECONDS * 1_000_000_000, side="right")
            left300 = np.searchsorted(trans_ns, query_ns, side="right")
            transition = (right300 > left300).astype(float)
            transition[bbo_age > MAX_STATE_AGE_SECONDS] = np.nan
            frame["state_transition_h300"] = transition

        st_ns = states["state_time"].astype("int64").to_numpy()
        mids = ((states["best_bid"] + states["best_ask"]) / 2.0).to_numpy(float)
        query_future = frame["time"].astype("int64").to_numpy() + FORWARD_SECONDS * 1_000_000_000
        idx = np.searchsorted(st_ns, query_future, side="right") - 1
        future = np.full(len(frame), np.nan)
        future_age = np.full(len(frame), np.nan)
        good = idx >= 0
        future[good] = mids[idx[good]]
        future_age[good] = (query_future[good] - st_ns[idx[good]]) / 1e9
        future[future_age > MAX_STATE_AGE_SECONDS] = np.nan
        frame["future_mid_60"] = future

        required = FEATURES + [
            "best_bid",
            "best_ask",
            "midpoint",
            "future_mid_60",
            "bbo_update_h60",
            "state_transition_h300",
        ]
        keep = np.ones(len(frame), dtype=bool)
        for col in required:
            keep &= np.isfinite(pd.to_numeric(frame[col], errors="coerce").to_numpy(float))
        frame = frame.loc[keep].copy()
        if not frame.empty:
            parts.append(frame)

        if n % 100 == 0:
            print(f"BUILD_PANEL date={date} tokens={n}", flush=True)

    panel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if panel.empty:
        raise RuntimeError(f"empty economic panel for {date}")
    panel = panel.sort_values(["time", "token_id"]).reset_index(drop=True)
    return panel


@dataclass
class FrozenLogit:
    scaler_mean: list[float]
    scaler_scale: list[float]
    coefficients: list[float]
    intercept: float
    target: str

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame[FEATURES].to_numpy(float)
        mean = np.asarray(self.scaler_mean)
        scale = np.asarray(self.scaler_scale)
        scale = np.where(scale == 0.0, 1.0, scale)
        z = (x - mean) / scale
        score = z @ np.asarray(self.coefficients) + self.intercept
        score = np.clip(score, -35, 35)
        return 1.0 / (1.0 + np.exp(-score))

    def record(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "features": FEATURES,
            "scaler_mean": self.scaler_mean,
            "scaler_scale": self.scaler_scale,
            "coefficients": self.coefficients,
            "intercept": self.intercept,
        }


def fit_logit(panel: pd.DataFrame, target: str) -> FrozenLogit:
    data = panel[FEATURES + [target]].dropna().copy()
    if len(data) > 350_000:
        data = data.sample(350_000, random_state=SEED)
    y = data[target].astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        raise RuntimeError(f"one-class TRAIN target: {target}")
    x = data[FEATURES].to_numpy(float)
    scaler = StandardScaler().fit(x)
    z = scaler.transform(x)
    model = LogisticRegression(
        max_iter=400,
        class_weight="balanced",
        random_state=SEED,
        solver="lbfgs",
    ).fit(z, y)
    return FrozenLogit(
        scaler_mean=[float(v) for v in scaler.mean_],
        scaler_scale=[float(v) for v in scaler.scale_],
        coefficients=[float(v) for v in model.coef_[0]],
        intercept=float(model.intercept_[0]),
        target=target,
    )


def add_hazard(panel: pd.DataFrame, update_model: FrozenLogit, transition_model: FrozenLogit) -> pd.DataFrame:
    out = panel.copy()
    out["p_update_h60"] = update_model.predict(out)
    out["p_transition_h300"] = transition_model.predict(out)
    out["hazard"] = np.maximum(out["p_update_h60"], out["p_transition_h300"])
    return out


def trade_groups(trades: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for token, part in trades.groupby("token_id", sort=False):
        part = part.sort_values("trade_time")
        out[str(token)] = (
            part["trade_time"].astype("int64").to_numpy(),
            part["price"].to_numpy(float),
            part["fee_rate_bps"].fillna(0.0).to_numpy(float),
        )
    return out


def unit_economics(
    panel: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    extra_width: float = 0.0,
    quote_lifetime_seconds: int = 60,
) -> pd.DataFrame:
    n = len(panel)
    gross = np.zeros(n)
    fees = np.zeros(n)
    adverse = np.zeros(n)
    spread_capture = np.zeros(n)
    fills = np.zeros(n)
    inventory_delta = np.zeros(n)
    turnover = np.zeros(n)
    groups = trade_groups(trades)

    panel_groups = panel.groupby("token_id", sort=False).groups
    times_all = panel["time"].astype("int64").to_numpy()
    bid_all = panel["best_bid"].to_numpy(float)
    ask_all = panel["best_ask"].to_numpy(float)
    mid_all = panel["midpoint"].to_numpy(float)
    future_all = panel["future_mid_60"].to_numpy(float)

    for token, indices in panel_groups.items():
        trade = groups.get(str(token))
        if trade is None:
            continue
        tt, tp, tf = trade
        for idx in np.asarray(indices, dtype=int):
            start = times_all[idx]
            end = start + quote_lifetime_seconds * 1_000_000_000
            lo = np.searchsorted(tt, start, side="right")
            hi = np.searchsorted(tt, end, side="right")
            if hi <= lo:
                continue
            prices = tp[lo:hi]
            fee_bps = tf[lo:hi]
            bid = max(0.0, bid_all[idx] - extra_width)
            ask = min(1.0, ask_all[idx] + extra_width)
            future = future_all[idx]
            mid = mid_all[idx]

            bid_hits = np.flatnonzero(prices <= bid)
            if len(bid_hits):
                k = int(bid_hits[0])
                fee = max(0.0, fee_bps[k]) * bid / 10000.0
                pnl = future - bid
                gross[idx] += pnl
                fees[idx] += fee
                adverse[idx] += max(0.0, -pnl)
                spread_capture[idx] += max(0.0, mid - bid)
                fills[idx] += 1.0
                inventory_delta[idx] += 1.0
                turnover[idx] += bid

            ask_hits = np.flatnonzero(prices >= ask)
            if len(ask_hits):
                k = int(ask_hits[0])
                fee = max(0.0, fee_bps[k]) * ask / 10000.0
                pnl = ask - future
                gross[idx] += pnl
                fees[idx] += fee
                adverse[idx] += max(0.0, -pnl)
                spread_capture[idx] += max(0.0, ask - mid)
                fills[idx] += 1.0
                inventory_delta[idx] -= 1.0
                turnover[idx] += ask

    return pd.DataFrame(
        {
            "gross": gross,
            "fees": fees,
            "net": gross - fees,
            "adverse": adverse,
            "spread_capture": spread_capture,
            "fills": fills,
            "inventory_delta": inventory_delta,
            "turnover": turnover,
        },
        index=panel.index,
    )


def aggregate(
    panel: pd.DataFrame,
    economics: pd.DataFrame,
    *,
    active: np.ndarray | None = None,
    scale: np.ndarray | None = None,
    intervention: np.ndarray | None = None,
) -> dict[str, Any]:
    n = len(panel)
    if active is None:
        active = np.ones(n, dtype=bool)
    if scale is None:
        scale = np.ones(n)
    scale = np.asarray(scale, dtype=float)
    active = np.asarray(active, dtype=bool)
    mult = scale * active.astype(float)

    work = economics.copy()
    for col in ["gross", "fees", "net", "adverse", "spread_capture", "fills", "inventory_delta", "turnover"]:
        work[col] = work[col].to_numpy(float) * mult

    fill_qty = float(work["fills"].sum())
    gross = float(work["gross"].sum())
    fees = float(work["fees"].sum())
    net = float(work["net"].sum())
    adverse_total = float(work["adverse"].sum())
    quoted_sides = float(active.sum() * 2)

    inv_frame = pd.DataFrame(
        {
            "token_id": panel["token_id"].to_numpy(),
            "time": panel["time"].to_numpy(),
            "delta": work["inventory_delta"].to_numpy(float),
        }
    ).sort_values(["token_id", "time"])
    inv_frame["inventory"] = inv_frame.groupby("token_id", sort=False)["delta"].cumsum()
    inv_abs = inv_frame["inventory"].abs()

    pnl_time = pd.DataFrame(
        {"time": panel["time"].to_numpy(), "net": work["net"].to_numpy(float)}
    ).groupby("time", sort=True)["net"].sum()
    equity = pnl_time.cumsum()
    drawdown = equity.cummax() - equity

    result = {
        "net_markout_pnl_after_fee_proxy": net,
        "gross_markout_pnl_proxy": gross,
        "fees_proxy": fees,
        "adverse_selection_per_filled_side": adverse_total / fill_qty if fill_qty > 0 else 0.0,
        "realised_spread_markout_per_filled_side": gross / fill_qty if fill_qty > 0 else 0.0,
        "spread_capture_at_entry_per_filled_side": float(work["spread_capture"].sum()) / fill_qty if fill_qty > 0 else 0.0,
        "filled_sides": fill_qty,
        "fill_rate": fill_qty / quoted_sides if quoted_sides > 0 else 0.0,
        "turnover_proxy": float(work["turnover"].sum()),
        "inventory_abs_mean": float(inv_abs.mean()) if len(inv_abs) else 0.0,
        "inventory_abs_max": float(inv_abs.max()) if len(inv_abs) else 0.0,
        "max_drawdown_proxy": float(drawdown.max()) if len(drawdown) else 0.0,
        "quoted_decisions": int(active.sum()),
        "intervention_rate": float(np.mean(intervention)) if intervention is not None and len(intervention) else 0.0,
        "support_status": "SUPPORTED" if fill_qty >= MIN_SUPPORT_FILLS else "SUPPORT_LIMITED",
    }
    return result


def apply_policy(
    panel: pd.DataFrame,
    variants: dict[str, pd.DataFrame],
    policy: dict[str, Any],
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    hazard = panel["hazard"].to_numpy(float)
    threshold = float(policy.get("hazard_threshold", math.inf))
    high = hazard >= threshold
    active = np.ones(len(panel), dtype=bool)
    scale = np.ones(len(panel))
    chosen = variants["base"].copy()

    if policy.get("wait", False):
        active[high] = False
    if "size_multiplier" in policy:
        scale[high] *= float(policy["size_multiplier"])

    variant_key = "base"
    extra = float(policy.get("extra_width", 0.0))
    refresh = int(policy.get("refresh_seconds", 60))
    if extra > 0.0 and refresh < 60:
        variant_key = f"w{extra:.3f}_h{refresh}"
    elif extra > 0.0:
        variant_key = f"w{extra:.3f}_h60"
    elif refresh < 60:
        variant_key = f"w0.000_h{refresh}"

    alt = variants.get(variant_key)
    if alt is not None and variant_key != "base":
        for col in chosen.columns:
            arr = chosen[col].to_numpy(float)
            other = alt[col].to_numpy(float)
            arr[high] = other[high]
            chosen[col] = arr

    return chosen, active, scale


def evaluate_policy(
    panel: pd.DataFrame,
    variants: dict[str, pd.DataFrame],
    policy: dict[str, Any],
) -> dict[str, Any]:
    econ, active, scale = apply_policy(panel, variants, policy)
    high = panel["hazard"].to_numpy(float) >= float(policy.get("hazard_threshold", math.inf))
    return aggregate(panel, econ, active=active, scale=scale, intervention=high)


def policy_passes(metrics: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        metrics["net_markout_pnl_after_fee_proxy"] > baseline["net_markout_pnl_after_fee_proxy"]
        and metrics["adverse_selection_per_filled_side"] <= baseline["adverse_selection_per_filled_side"] + 1e-12
        and metrics["max_drawdown_proxy"] <= baseline["max_drawdown_proxy"] + 1e-12
    )


def make_variants(panel: pd.DataFrame, trades: pd.DataFrame, combinations: list[tuple[float, int]]) -> dict[str, pd.DataFrame]:
    variants = {"base": unit_economics(panel, trades, extra_width=0.0, quote_lifetime_seconds=60)}
    for extra, horizon in combinations:
        key = f"w{extra:.3f}_h{horizon}"
        if key not in variants:
            variants[key] = unit_economics(
                panel,
                trades,
                extra_width=extra,
                quote_lifetime_seconds=horizon,
            )
    return variants


def candidate_sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
    m = item["metrics"]
    return (
        float(m["net_markout_pnl_after_fee_proxy"]),
        -float(m["turnover_proxy"]),
        -float(m["intervention_rate"]),
    )


def select_dev_policy(panel: pd.DataFrame, trades: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    combinations = [(0.0, 15), (0.0, 30)]
    combinations += [(w, 60) for w in WIDTH_EXTRAS]
    combinations += [(w, h) for w in WIDTH_EXTRAS for h in REFRESH_SECONDS]
    variants = make_variants(panel, trades, combinations)
    baseline = aggregate(panel, variants["base"])
    thresholds = {
        f"q{int(q * 100)}": float(panel["hazard"].quantile(q))
        for q in POLICY_QUANTILES
    }

    families: dict[str, list[dict[str, Any]]] = {
        "WIDTH_ONLY": [],
        "SIZE_ONLY": [],
        "WAIT_ONLY": [],
        "REFRESH_ONLY": [],
    }
    for qname, threshold in thresholds.items():
        for extra in WIDTH_EXTRAS:
            policy = {
                "family": "WIDTH_ONLY",
                "hazard_quantile": qname,
                "hazard_threshold": threshold,
                "extra_width": extra,
            }
            metrics = evaluate_policy(panel, variants, policy)
            families["WIDTH_ONLY"].append({"policy": policy, "metrics": metrics, "passes": policy_passes(metrics, baseline)})

        for mult in SIZE_MULTIPLIERS:
            policy = {
                "family": "SIZE_ONLY",
                "hazard_quantile": qname,
                "hazard_threshold": threshold,
                "size_multiplier": mult,
            }
            metrics = evaluate_policy(panel, variants, policy)
            families["SIZE_ONLY"].append({"policy": policy, "metrics": metrics, "passes": policy_passes(metrics, baseline)})

        policy = {
            "family": "WAIT_ONLY",
            "hazard_quantile": qname,
            "hazard_threshold": threshold,
            "wait": True,
        }
        metrics = evaluate_policy(panel, variants, policy)
        families["WAIT_ONLY"].append({"policy": policy, "metrics": metrics, "passes": policy_passes(metrics, baseline)})

        for horizon in REFRESH_SECONDS:
            policy = {
                "family": "REFRESH_ONLY",
                "hazard_quantile": qname,
                "hazard_threshold": threshold,
                "refresh_seconds": horizon,
            }
            metrics = evaluate_policy(panel, variants, policy)
            families["REFRESH_ONLY"].append({"policy": policy, "metrics": metrics, "passes": policy_passes(metrics, baseline)})

    winners: dict[str, dict[str, Any]] = {}
    for family, rows in families.items():
        passing = [row for row in rows if row["passes"]]
        if passing:
            winners[family] = max(passing, key=candidate_sort_key)

    combined_policy: dict[str, Any] | None = None
    combined_result: dict[str, Any] | None = None
    if winners:
        thresholds_used = [
            float(row["policy"]["hazard_threshold"])
            for row in winners.values()
        ]
        combined_policy = {
            "family": "COMBINED",
            "hazard_threshold": max(thresholds_used),
            "components": sorted(winners),
        }
        if "WIDTH_ONLY" in winners:
            combined_policy["extra_width"] = winners["WIDTH_ONLY"]["policy"]["extra_width"]
        if "SIZE_ONLY" in winners:
            combined_policy["size_multiplier"] = winners["SIZE_ONLY"]["policy"]["size_multiplier"]
        if "WAIT_ONLY" in winners:
            combined_policy["wait"] = True
        if "REFRESH_ONLY" in winners:
            combined_policy["refresh_seconds"] = winners["REFRESH_ONLY"]["policy"]["refresh_seconds"]
        metrics = evaluate_policy(panel, variants, combined_policy)
        combined_result = {
            "policy": combined_policy,
            "metrics": metrics,
            "passes": policy_passes(metrics, baseline),
        }

    pool = list(winners.values())
    if combined_result is not None and combined_result["passes"]:
        pool.append(combined_result)
    champion = max(pool, key=candidate_sort_key) if pool else None

    serial_families = {
        family: rows for family, rows in families.items()
    }
    dev_results = {
        "baseline": baseline,
        "thresholds": thresholds,
        "families": serial_families,
        "family_winners": winners,
        "combined": combined_result,
        "champion": champion,
    }
    freeze = {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "protocol_id": PROTOCOL,
        "status": "FROZEN_BEFORE_HOLDOUT_READ",
        "frozen_at": now_iso(),
        "train_date": TRAIN_DATE,
        "dev_date": DEV_DATE,
        "holdout_date": HOLDOUT_DATE,
        "features": FEATURES,
        "policy_quantiles": POLICY_QUANTILES,
        "family_winners": {
            family: row["policy"] for family, row in winners.items()
        },
        "combined_policy": None if combined_result is None else combined_result["policy"],
        "champion_policy": None if champion is None else champion["policy"],
        "holdout_read": False,
        "post_holdout_tuning_allowed": False,
        "real_sig_orders_sent": False,
    }
    return dev_results, freeze, {"variants_needed": combinations}


def holdout_concentration(
    panel: pd.DataFrame,
    baseline_econ: pd.DataFrame,
    champion_econ: pd.DataFrame,
    champion_active: np.ndarray,
    champion_scale: np.ndarray,
) -> dict[str, Any]:
    base = baseline_econ["net"].to_numpy(float)
    champ = champion_econ["net"].to_numpy(float) * champion_scale * champion_active.astype(float)
    diff = champ - base
    detail = pd.DataFrame(
        {
            "token_id": panel["token_id"].to_numpy(),
            "time": panel["time"].to_numpy(),
            "diff": diff,
        }
    )
    by_token = detail.groupby("token_id")["diff"].sum().sort_values(ascending=False)
    positive = by_token[by_token > 0]
    token_share = float(positive.iloc[0] / positive.sum()) if len(positive) and positive.sum() > 0 else 1.0

    detail["block"] = pd.to_datetime(detail["time"], utc=True).dt.hour // 6
    by_block = detail.groupby("block")["diff"].sum().sort_values(ascending=False)
    positive_b = by_block[by_block > 0]
    block_share = float(positive_b.iloc[0] / positive_b.sum()) if len(positive_b) and positive_b.sum() > 0 else 1.0

    return {
        "max_positive_token_contribution_share": token_share,
        "max_positive_6h_block_contribution_share": block_share,
        "not_single_token_concentrated": token_share < 0.50,
        "not_single_time_block_concentrated": block_share < 0.75,
    }


def main() -> None:
    print("005G ECONOMIC REPLAY: loading TRAIN only", flush=True)
    train = load_day(TRAIN_DATE)
    thresholds, global_thresholds = train_spread_thresholds(train.bbo)
    train_panel = build_panel(
        train,
        date=TRAIN_DATE,
        thresholds=thresholds,
        global_thresholds=global_thresholds,
    )
    update_model = fit_logit(train_panel, "bbo_update_h60")
    transition_model = fit_logit(train_panel, "state_transition_h300")
    train_panel = add_hazard(train_panel, update_model, transition_model)

    model_record = {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "protocol_id": PROTOCOL,
        "trained_only_on": TRAIN_DATE,
        "features": FEATURES,
        "update_model": update_model.record(),
        "transition_model": transition_model.record(),
        "hazard_combiner": "max",
    }
    write_json("ECONOMIC_HAZARD_MODEL.json", model_record)

    print("005G ECONOMIC REPLAY: loading DEV only", flush=True)
    dev = load_day(DEV_DATE)
    dev_panel = build_panel(
        dev,
        date=DEV_DATE,
        thresholds=thresholds,
        global_thresholds=global_thresholds,
    )
    dev_panel = add_hazard(dev_panel, update_model, transition_model)
    dev_results, policy_freeze, aux = select_dev_policy(dev_panel, dev.trades)
    write_json("ECONOMIC_DEV_RESULTS.json", dev_results)
    write_json("ECONOMIC_POLICY_FREEZE.json", policy_freeze)

    # HOLDOUT is intentionally not opened until the frozen policy file exists.
    if not (OUT / "ECONOMIC_POLICY_FREEZE.json").is_file():
        raise RuntimeError("policy freeze was not durably written before HOLDOUT read")

    print("005G ECONOMIC REPLAY: policy frozen; opening HOLDOUT now", flush=True)
    holdout = load_day(HOLDOUT_DATE)
    holdout_panel = build_panel(
        holdout,
        date=HOLDOUT_DATE,
        thresholds=thresholds,
        global_thresholds=global_thresholds,
    )
    holdout_panel = add_hazard(holdout_panel, update_model, transition_model)

    policies: dict[str, dict[str, Any]] = {
        family: row["policy"]
        for family, row in dev_results["family_winners"].items()
    }
    if dev_results["combined"] is not None and dev_results["combined"]["passes"]:
        policies["COMBINED"] = dev_results["combined"]["policy"]
    champion = dev_results["champion"]

    combinations = [(0.0, 15), (0.0, 30)]
    combinations += [(w, 60) for w in WIDTH_EXTRAS]
    combinations += [(w, h) for w in WIDTH_EXTRAS for h in REFRESH_SECONDS]
    hold_variants = make_variants(holdout_panel, holdout.trades, combinations)
    baseline_metrics = aggregate(holdout_panel, hold_variants["base"])

    policy_results: dict[str, Any] = {}
    for name, policy in policies.items():
        policy_results[name] = {
            "policy": policy,
            "metrics": evaluate_policy(holdout_panel, hold_variants, policy),
        }

    champion_name = None
    champion_metrics = None
    concentration = None
    promotion = "NO_DEV_CHAMPION"
    promotion_reasons: list[str] = []
    if champion is not None:
        champ_policy = champion["policy"]
        champion_name = str(champ_policy["family"])
        champ_econ, champ_active, champ_scale = apply_policy(
            holdout_panel,
            hold_variants,
            champ_policy,
        )
        champion_metrics = aggregate(
            holdout_panel,
            champ_econ,
            active=champ_active,
            scale=champ_scale,
            intervention=holdout_panel["hazard"].to_numpy(float)
            >= float(champ_policy["hazard_threshold"]),
        )
        concentration = holdout_concentration(
            holdout_panel,
            hold_variants["base"],
            champ_econ,
            champ_active,
            champ_scale,
        )

        gates = {
            "net_pnl_proxy_positive_vs_baseline": champion_metrics["net_markout_pnl_after_fee_proxy"] > baseline_metrics["net_markout_pnl_after_fee_proxy"],
            "adverse_selection_non_worse": champion_metrics["adverse_selection_per_filled_side"] <= baseline_metrics["adverse_selection_per_filled_side"] + 1e-12,
            "max_drawdown_non_worse": champion_metrics["max_drawdown_proxy"] <= baseline_metrics["max_drawdown_proxy"] + 1e-12,
            "minimum_fill_support": champion_metrics["filled_sides"] >= MIN_SUPPORT_FILLS,
            "not_single_token_concentrated": concentration["not_single_token_concentrated"],
            "not_single_time_block_concentrated": concentration["not_single_time_block_concentrated"],
        }
        promotion_reasons = [key for key, value in gates.items() if not value]
        promotion = "PASS_PROXY_ECONOMIC_GATE" if all(gates.values()) else "FAIL_OR_SUPPORT_LIMITED"
    else:
        gates = {}

    result = {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "protocol_id": PROTOCOL,
        "completed_at": now_iso(),
        "economic_label": "HISTORICAL_PASSIVE_EXECUTION_PROXY_NOT_LIVE_SIG_PNL",
        "windows": {
            "TRAIN": TRAIN_DATE,
            "DEV": DEV_DATE,
            "HOLDOUT": HOLDOUT_DATE,
        },
        "holdout_read_only_after_policy_freeze": True,
        "post_holdout_tuning": False,
        "baseline": baseline_metrics,
        "policies": policy_results,
        "dev_champion_family": champion_name,
        "dev_champion_policy": None if champion is None else champion["policy"],
        "holdout_champion_metrics": champion_metrics,
        "concentration": concentration,
        "promotion_gates": gates,
        "promotion": promotion,
        "promotion_failures": promotion_reasons,
        "real_sig_orders_sent": False,
        "make_modified": False,
    }
    write_json("ECONOMIC_HOLDOUT_RESULTS.json", result)

    input_audit = {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "protocol_id": PROTOCOL,
        "dataset": "polyleviathan/sig-cup-data003-orderbooks",
        "dataset_version": 1,
        "token_count_frozen": len(TOKEN_IDS),
        "train": train.audit,
        "dev": dev.audit,
        "holdout": holdout.audit,
        "panel_rows": {
            "train": int(len(train_panel)),
            "dev": int(len(dev_panel)),
            "holdout": int(len(holdout_panel)),
        },
        "holdout_opened_after_freeze": True,
        "real_sig_orders_sent": False,
    }
    write_json("ECONOMIC_INPUT_AUDIT.json", input_audit)

    report = [
        "# EXPERIMENT-005G Economic Replay V1",
        "",
        f"- Completed: {result['completed_at']}",
        "- Evidence type: historical passive-execution proxy; **not live SIG P&L**",
        f"- TRAIN: {TRAIN_DATE}; DEV: {DEV_DATE}; sealed economic HOLDOUT: {HOLDOUT_DATE}",
        f"- Frozen mapped tokens: {len(TOKEN_IDS)}",
        f"- DEV champion: {champion_name or 'NONE'}",
        f"- HOLDOUT promotion: {promotion}",
        "",
        "## Holdout",
        "",
        f"- Baseline net 60s markout P&L after fee proxy: {baseline_metrics['net_markout_pnl_after_fee_proxy']:.8f}",
        f"- Baseline filled sides: {baseline_metrics['filled_sides']:.0f}",
    ]
    if champion_metrics is not None:
        report.extend(
            [
                f"- Champion net 60s markout P&L after fee proxy: {champion_metrics['net_markout_pnl_after_fee_proxy']:.8f}",
                f"- Champion filled sides: {champion_metrics['filled_sides']:.0f}",
                f"- Champion adverse selection / filled side: {champion_metrics['adverse_selection_per_filled_side']:.8f}",
                f"- Champion max drawdown proxy: {champion_metrics['max_drawdown_proxy']:.8f}",
                "",
                "## Gate failures",
                "",
                *([f"- {x}" for x in promotion_reasons] if promotion_reasons else ["- none"]),
            ]
        )
    report.extend(
        [
            "",
            "## Boundary",
            "",
            "This run does not modify MAKE, send SIG orders, or claim that Polymarket historical markout proxy equals competition P&L.",
        ]
    )
    (OUT / "FINAL_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"promotion": promotion, "dev_champion": champion_name}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
