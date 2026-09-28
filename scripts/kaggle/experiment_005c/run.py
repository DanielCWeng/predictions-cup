# ruff: noqa
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

INPUT = Path("/kaggle/input")
WORK = Path("/kaggle/working/005c_joint_panel_prediction")
WORK.mkdir(parents=True, exist_ok=True)

FREEZE_COMMIT = "ea7ed06dd47cb8240cc8a1e5dedabf00d009843f"
PREREG_SHA = "b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5"
MASTER_SEED = 20260928005
FAMILIES = ("US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026")
GRIDS = (5, 15, 30, 60)
HORIZONS = (5, 15, 30, 60, 120)
LAG_DEPTHS = (1, 3)
RIDGE_ALPHAS = (0.1, 1.0, 10.0, 100.0)
RANKS = (1, 2, 4, 8, 16, 32)
MIN_PANEL_MARKETS = 4
MAX_PANEL_MARKETS = 32
MIN_PREFIX_OBS = 250
MIN_PREFIX_BUCKETS = 50
MIN_TRAIN_TARGET = 100
MIN_DEV_TARGET = 30
MIN_HOLD_TARGET = 30
EMBARGO = 120


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one(pattern: str) -> Path:
    matches = sorted(INPUT.rglob(pattern))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"expected exactly one {pattern}, got {matches}")

    # Kaggle may expose the historical fills dataset as an archive rather than loose files.
    # Extract only the requested member, preserving the same source bytes and filenames.
    cache = WORK / "_input_cache"
    cache.mkdir(parents=True, exist_ok=True)
    archived: list[tuple[Path, str]] = []
    for archive_path in sorted(INPUT.rglob("*.zip")):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if Path(name).name == pattern:
                        archived.append((archive_path, name))
        except zipfile.BadZipFile:
            continue
    if len(archived) != 1:
        sample = [str(path.relative_to(INPUT)) for path in sorted(INPUT.rglob("*"))[:80]]
        raise RuntimeError(
            f"expected exactly one {pattern}; loose={matches}, archived={archived}, "
            f"input_sample={sample}"
        )
    archive_path, member = archived[0]
    target = cache / pattern
    if not target.exists():
        with zipfile.ZipFile(archive_path) as archive, archive.open(member) as source:
            with target.open("wb") as output:
                while True:
                    chunk = source.read(1 << 20)
                    if not chunk:
                        break
                    output.write(chunk)
    return target


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


def load_code() -> dict[str, Any]:
    manifest_path = one("code_manifest.json")
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest["freeze_commit"] != FREEZE_COMMIT:
        raise RuntimeError("wrong 005C freeze commit")
    if manifest["preregistration_sha256"] != PREREG_SHA:
        raise RuntimeError("wrong 005C preregistration hash")
    if manifest["runner_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("005C runner hash mismatch")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"code bundle mismatch: {name}")
    sys.path.insert(0, str(root / "predictions_cup_005c.bundle"))
    return manifest


CODE_MANIFEST = load_code()

from predictions_cup.learning.joint_panel import (  # noqa: E402
    effective_number,
    fit_masked_multioutput,
    fit_pca,
    future_target,
    logit_price,
    market_grid_features,
    masked_mse,
    per_target_mse,
    predictive_ic,
    singular_spectrum,
    source_target_concentration,
    truncate_model,
)


@dataclass
class MarketSeries:
    condition_id: str
    event_id: str
    market_family: str
    times: np.ndarray
    probability: np.ndarray
    logit: np.ndarray
    activity_value: float


@dataclass
class Panel:
    panel_id: str
    family: str
    scope: str
    scope_value: str
    markets: tuple[str, ...]


@dataclass
class FeatureCube:
    returns: np.ndarray
    masks: np.ndarray
    ages: np.ndarray


@dataclass
class PreparedFeatures:
    train_x: np.ndarray
    other_x: np.ndarray
    train_r: np.ndarray
    other_r: np.ndarray
    train_m: np.ndarray
    other_m: np.ndarray
    train_a: np.ndarray
    other_a: np.ndarray
    source_groups: np.ndarray
    return_source_groups: np.ndarray
    scaling: dict[str, Any]


def reconstruction_family(
    family: str,
) -> tuple[dict[str, MarketSeries], pd.DataFrame, dict[str, Any]]:
    fills_path = one(f"fills_{family}.parquet")
    markets_path = one(f"markets_{family}.parquet")
    raw_columns = [
        "timestamp", "price", "size_shares", "value_usd", "token_id",
        "condition_id", "maker_address", "tx_hash", "log_index",
        "is_exchange_taker", "outcome_side",
    ]
    table = pq.read_table(fills_path, columns=raw_columns)
    raw_rows = table.num_rows

    active = table.filter(pc.equal(table["is_exchange_taker"], True))
    passive = table.filter(pc.equal(table["is_exchange_taker"], False))

    active_nonnull = active.filter(pc.is_valid(active["maker_address"]))
    active_unique = active_nonnull.group_by(
        ["tx_hash", "condition_id", "maker_address"]
    ).aggregate([("timestamp", "count")])
    owner_counts = active_unique.group_by(
        ["tx_hash", "condition_id"]
    ).aggregate([("maker_address", "count")])
    passive_groups = passive.group_by(
        ["tx_hash", "condition_id"]
    ).aggregate([("value_usd", "sum")])
    group_audit = passive_groups.join(
        owner_counts,
        keys=["tx_hash", "condition_id"],
        join_type="left outer",
    )
    owners = pc.fill_null(group_audit["maker_address_count"], 0)
    valid_group = pc.equal(owners, 1)
    valid_count = int(pc.sum(pc.cast(valid_group, pa.int64())).as_py() or 0)
    total_passive_value = float(
        pc.sum(pc.fill_null(group_audit["value_usd_sum"], 0.0)).as_py() or 0.0
    )
    valid_passive_value = float(
        pc.sum(
            pc.fill_null(group_audit.filter(valid_group)["value_usd_sum"], 0.0)
        ).as_py()
        or 0.0
    )
    retained_fraction = (
        valid_passive_value / total_passive_value
        if total_passive_value > 0
        else 0.0
    )
    invalid_groups = group_audit.num_rows - valid_count
    if retained_fraction < 0.99:
        raise RuntimeError(
            f"{family}: reconstruction audit retained only "
            f"{retained_fraction:.6f} passive notional"
        )
    if invalid_groups:
        valid_keys = group_audit.filter(valid_group).select(
            ["tx_hash", "condition_id"]
        )
        passive = passive.join(
            valid_keys,
            keys=["tx_hash", "condition_id"],
            join_type="inner",
        )

    outcome = pc.utf8_upper(passive["outcome_side"])
    is_yes = pc.equal(outcome, "YES")
    is_no = pc.equal(outcome, "NO")
    binary = pc.or_(is_yes, is_no)
    price = pc.cast(passive["price"], pa.float64())
    size = pc.cast(passive["size_shares"], pa.float64())
    finite = pc.and_(pc.is_finite(price), pc.is_finite(size))
    valid_numeric = pc.and_(
        finite,
        pc.and_(
            pc.greater(size, 0.0),
            pc.and_(
                pc.greater_equal(price, 0.0),
                pc.less_equal(price, 1.0),
            ),
        ),
    )
    keep = pc.and_(binary, valid_numeric)
    keep_count = int(pc.sum(pc.cast(keep, pa.int64())).as_py() or 0)
    rejected = passive.num_rows - keep_count
    econ = passive.filter(keep)

    outcome = pc.utf8_upper(econ["outcome_side"])
    price = pc.cast(econ["price"], pa.float64())
    size = pc.cast(econ["size_shares"], pa.float64())
    yes_price = pc.if_else(
        pc.equal(outcome, "YES"),
        price,
        pc.subtract(1.0, price),
    )
    weighted = pc.multiply(yes_price, size)
    economic = pa.table(
        {
            "condition_id": econ["condition_id"],
            "timestamp": pc.cast(econ["timestamp"], pa.int64()),
            "weighted_price": weighted,
            "size": size,
            "value_usd": pc.cast(econ["value_usd"], pa.float64()),
        }
    )
    agg = economic.group_by(["condition_id", "timestamp"]).aggregate(
        [
            ("weighted_price", "sum"),
            ("size", "sum"),
            ("value_usd", "sum"),
            ("timestamp", "count"),
        ]
    )
    agg = agg.append_column(
        "yes_price",
        pc.divide(agg["weighted_price_sum"], agg["size_sum"]),
    )
    agg = agg.select(
        [
            "condition_id", "timestamp", "yes_price",
            "size_sum", "value_usd_sum", "timestamp_count",
        ]
    )
    order = pc.sort_indices(
        agg,
        sort_keys=[("condition_id", "ascending"), ("timestamp", "ascending")],
    )
    agg = agg.take(order)

    meta = pq.read_table(
        markets_path,
        columns=["condition_id", "event_id", "market_family", "question"],
    ).to_pandas()
    meta["condition_id"] = meta["condition_id"].astype(str)
    meta["event_id"] = meta["event_id"].fillna("").astype(str)
    meta["market_family"] = meta["market_family"].fillna("").astype(str)
    meta = meta.drop_duplicates("condition_id", keep="first")
    meta_by_cond = meta.set_index("condition_id")

    df = agg.to_pandas(categories=["condition_id"])
    series: dict[str, MarketSeries] = {}
    clipped_low = 0
    clipped_high = 0
    for condition, sub in df.groupby(
        "condition_id",
        observed=True,
        sort=False,
    ):
        cid = str(condition)
        if cid not in meta_by_cond.index:
            continue
        times = sub["timestamp"].to_numpy(dtype=np.int64, copy=True)
        probability = sub["yes_price"].to_numpy(dtype=np.float64, copy=True)
        clipped_low += int((probability < 1e-4).sum())
        clipped_high += int((probability > 1.0 - 1e-4).sum())
        row = meta_by_cond.loc[cid]
        series[cid] = MarketSeries(
            condition_id=cid,
            event_id=str(row["event_id"]),
            market_family=str(row["market_family"]),
            times=times,
            probability=probability,
            logit=logit_price(probability, epsilon=1e-4),
            activity_value=float(sub["value_usd_sum"].sum()),
        )

    audit = {
        "family": family,
        "raw_orderfilled_rows": raw_rows,
        "active_rows": active.num_rows,
        "passive_rows_after_role_audit": passive.num_rows,
        "transaction_condition_groups_with_passive": group_audit.num_rows,
        "invalid_role_structure_groups": invalid_groups,
        "valid_passive_notional_fraction": retained_fraction,
        "rejected_nonbinary_or_numeric_passive_rows": rejected,
        "economic_condition_second_observations": agg.num_rows,
        "conditions_with_economic_observations": len(series),
        "logit_clipped_low": clipped_low,
        "logit_clipped_high": clipped_high,
        "source_identity_uniqueness": (
            "inherited from frozen DATA-001 source-export audit: "
            "0 duplicate (tx_hash,log_index,token_id) keys"
        ),
    }
    print("RECON", json.dumps(audit, sort_keys=True))
    del table, active, passive, economic, agg, df
    return series, meta, audit


def panel_id(
    family: str,
    scope: str,
    scope_value: str,
    markets: tuple[str, ...],
) -> str:
    raw = f"{family}|{scope}|{scope_value}|{'|'.join(markets)}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{family}_{scope}_{digest}"


def select_panels(
    family: str,
    series: dict[str, MarketSeries],
    meta: pd.DataFrame,
) -> tuple[list[Panel], list[dict[str, Any]]]:
    candidates: list[tuple[str, str, list[str]]] = []
    all_meta = sorted(set(meta["condition_id"].astype(str)))
    candidates.append(("family", family, all_meta))
    events = meta[meta["event_id"].astype(str) != ""]
    for event_id, sub in events.groupby("event_id"):
        conditions = sorted(set(sub["condition_id"].astype(str)))
        if len(conditions) >= MIN_PANEL_MARKETS:
            candidates.append(("event", str(event_id), conditions))

    panels: list[Panel] = []
    coverage: list[dict[str, Any]] = []
    seen_sets: set[tuple[str, ...]] = set()
    for scope, value, conditions in candidates:
        observed = [
            cid
            for cid in conditions
            if cid in series and len(series[cid].times)
        ]
        if not observed:
            for cid in conditions:
                coverage.append(
                    {
                        "family": family,
                        "scope": scope,
                        "scope_value": value,
                        "panel_id": "",
                        "condition_id": cid,
                        "selected": False,
                        "reason": "no_economic_observations",
                        "prefix_observations": 0,
                        "prefix_5min_buckets": 0,
                    }
                )
            continue

        start = min(int(series[cid].times[0]) for cid in observed)
        end = max(int(series[cid].times[-1]) for cid in observed)
        prefix_end = start + int(0.60 * max(1, end - start))
        support: dict[str, tuple[int, int]] = {}
        eligible: list[str] = []
        for cid in conditions:
            if cid not in series:
                support[cid] = (0, 0)
                continue
            t = series[cid].times
            prefix = t[t < prefix_end]
            count = len(prefix)
            buckets = len(np.unique(prefix // 300)) if count else 0
            support[cid] = (count, buckets)
            if count >= MIN_PREFIX_OBS and buckets >= MIN_PREFIX_BUCKETS:
                eligible.append(cid)

        eligible.sort(key=lambda cid: (-support[cid][0], cid))
        selected = tuple(eligible[:MAX_PANEL_MARKETS])
        pid = (
            panel_id(family, scope, value, selected)
            if len(selected) >= MIN_PANEL_MARKETS
            else ""
        )
        selected_set = set(selected)
        for cid in conditions:
            count, buckets = support.get(cid, (0, 0))
            if cid in selected_set:
                reason = "selected"
            elif cid not in series:
                reason = "no_economic_observations"
            elif count < MIN_PREFIX_OBS:
                reason = "insufficient_prefix_observations"
            elif buckets < MIN_PREFIX_BUCKETS:
                reason = "insufficient_prefix_5min_buckets"
            elif cid in eligible[MAX_PANEL_MARKETS:]:
                reason = "panel_cap_32_lower_support"
            else:
                reason = "not_selected"
            coverage.append(
                {
                    "family": family,
                    "scope": scope,
                    "scope_value": value,
                    "panel_id": pid,
                    "condition_id": cid,
                    "selected": cid in selected_set,
                    "reason": reason,
                    "prefix_observations": count,
                    "prefix_5min_buckets": buckets,
                }
            )

        if len(selected) < MIN_PANEL_MARKETS or selected in seen_sets:
            continue
        seen_sets.add(selected)
        panels.append(Panel(pid, family, scope, value, selected))

    print(f"PANELS {family}: {len(panels)} eligible")
    return panels, coverage


def age_cap(grid: int) -> int:
    return min(300, 10 * grid)


def decision_times(
    panel: Panel,
    series: dict[str, MarketSeries],
    grid: int,
) -> np.ndarray:
    buckets = [
        ((series[cid].times // grid) + 1) * grid
        for cid in panel.markets
    ]
    q = np.unique(np.concatenate(buckets))
    available = np.zeros((len(q), len(panel.markets)), dtype=bool)
    observed = np.zeros((len(q), len(panel.markets)), dtype=bool)
    for j, cid in enumerate(panel.markets):
        mg = market_grid_features(
            series[cid].times,
            series[cid].logit,
            q,
            grid_seconds=grid,
            lag_depth=1,
            age_cap_seconds=age_cap(grid),
        )
        available[:, j] = mg.current_available
        observed[:, j] = mg.observed[:, 0] > 0.5

    keep = (
        available.sum(axis=1)
        >= max(3, math.ceil(0.50 * len(panel.markets)))
    ) & (observed.sum(axis=1) >= 3)
    return q[keep]


def split_masks(
    q: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[int, int]]:
    n = len(q)
    if n < 20:
        empty = np.zeros(n, dtype=bool)
        return empty, empty.copy(), empty.copy(), (0, 0)
    i1 = min(n - 1, max(1, int(0.60 * n)))
    i2 = min(n - 1, max(2, int(0.80 * n)))
    b1 = int(q[i1])
    b2 = int(q[i2])
    train = q < (b1 - EMBARGO)
    dev = (q >= (b1 + EMBARGO)) & (q < (b2 - EMBARGO))
    hold = q >= (b2 + EMBARGO)
    return train, dev, hold, (b1, b2)


def future_mask(
    times: np.ndarray,
    decisions: np.ndarray,
    horizon: int,
    cap: int,
) -> np.ndarray:
    q = np.asarray(decisions, dtype=np.int64)
    fq = q + horizon
    base_idx = np.searchsorted(times, q, side="left") - 1
    future_idx = np.searchsorted(times, fq, side="left") - 1
    left = np.searchsorted(times, q, side="left")
    right = np.searchsorted(times, fq, side="left")
    valid = (base_idx >= 0) & (future_idx >= 0) & (right > left)
    out = np.zeros(len(q), dtype=bool)
    if valid.any():
        bi = base_idx[valid]
        fi = future_idx[valid]
        out[valid] = (
            (q[valid] - times[bi] <= cap)
            & (fq[valid] - times[fi] <= cap)
        )
    return out


def target_matrix(
    panel: Panel,
    series: dict[str, MarketSeries],
    decisions: np.ndarray,
    horizon: int,
    coordinate: str,
    grid: int,
) -> np.ndarray:
    y = np.full(
        (len(decisions), len(panel.markets)),
        np.nan,
        dtype=np.float64,
    )
    cap = age_cap(grid)
    for j, cid in enumerate(panel.markets):
        values = (
            series[cid].logit
            if coordinate == "logit"
            else series[cid].probability
        )
        y[:, j] = future_target(
            series[cid].times,
            values,
            decisions,
            horizon_seconds=horizon,
            age_cap_seconds=cap,
        )
    return y


def raw_cube(
    panel: Panel,
    series: dict[str, MarketSeries],
    decisions: np.ndarray,
    grid: int,
    lag: int,
    coordinate: str,
) -> FeatureCube:
    n = len(decisions)
    m = len(panel.markets)
    returns = np.full((n, m, lag), np.nan, dtype=np.float64)
    masks = np.zeros((n, m, lag), dtype=np.float64)
    ages = np.ones((n, m, lag), dtype=np.float64)
    for j, cid in enumerate(panel.markets):
        values = (
            series[cid].logit
            if coordinate == "logit"
            else series[cid].probability
        )
        mg = market_grid_features(
            series[cid].times,
            values,
            decisions,
            grid_seconds=grid,
            lag_depth=lag,
            age_cap_seconds=age_cap(grid),
        )
        returns[:, j, :] = mg.returns
        masks[:, j, :] = mg.observed
        ages[:, j, :] = mg.age
    return FeatureCube(returns, masks, ages)


def prepare_features(
    train: FeatureCube,
    other: FeatureCube,
) -> PreparedFeatures:
    n_train, m, lag = train.returns.shape
    n_other = other.returns.shape[0]
    tr = np.zeros_like(train.returns)
    ot = np.zeros_like(other.returns)
    means = np.zeros((m, lag), dtype=np.float64)
    scales = np.ones((m, lag), dtype=np.float64)

    for j in range(m):
        for l in range(lag):
            mask = train.masks[:, j, l] > 0.5
            values = train.returns[mask, j, l]
            mean = float(values.mean()) if len(values) else 0.0
            scale = float(values.std()) if len(values) else 1.0
            if not np.isfinite(scale) or scale <= 1e-12:
                scale = 1.0
            means[j, l] = mean
            scales[j, l] = scale

            train_mask = train.masks[:, j, l] > 0.5
            other_mask = other.masks[:, j, l] > 0.5
            tr[train_mask, j, l] = (
                train.returns[train_mask, j, l] - mean
            ) / scale
            ot[other_mask, j, l] = (
                other.returns[other_mask, j, l] - mean
            ) / scale

    tr_flat = tr.reshape(n_train, m * lag)
    ot_flat = ot.reshape(n_other, m * lag)
    tm = train.masks.reshape(n_train, m * lag)
    om = other.masks.reshape(n_other, m * lag)
    ta = train.ages.reshape(n_train, m * lag)
    oa = other.ages.reshape(n_other, m * lag)

    train_x = np.concatenate([tr_flat, tm, ta], axis=1)
    other_x = np.concatenate([ot_flat, om, oa], axis=1)
    groups = np.repeat(np.arange(m, dtype=np.int64), lag)
    source_groups = np.concatenate([groups, groups, groups])

    return PreparedFeatures(
        train_x=train_x,
        other_x=other_x,
        train_r=tr,
        other_r=ot,
        train_m=train.masks,
        other_m=other.masks,
        train_a=train.ages,
        other_a=other.ages,
        source_groups=source_groups,
        return_source_groups=groups,
        scaling={
            "return_mean": means.tolist(),
            "return_scale": scales.tolist(),
        },
    )


def common_features(
    prepared: PreparedFeatures,
    target: int,
    *,
    train: bool,
) -> np.ndarray:
    r = prepared.train_r if train else prepared.other_r
    mask = prepared.train_m if train else prepared.other_m
    age = prepared.train_a if train else prepared.other_a
    n, m, lag = r.shape
    cols: list[np.ndarray] = []
    others = np.arange(m) != target

    for l in range(lag):
        mk = mask[:, others, l]
        count = mk.sum(axis=1)
        total = (r[:, others, l] * mk).sum(axis=1)
        mean = np.divide(
            total,
            count,
            out=np.zeros(n),
            where=count > 0,
        )
        frac = count / max(1, m - 1)
        age_total = (age[:, others, l] * mk).sum(axis=1)
        mean_age = np.divide(
            age_total,
            count,
            out=np.ones(n),
            where=count > 0,
        )
        cols.extend([mean, frac, mean_age])

    return np.column_stack(cols)


def baseline_predictions(
    prepared: PreparedFeatures,
    y_train: np.ndarray,
    target_indices: list[int],
    *,
    kind: str,
    alpha: float,
) -> np.ndarray:
    pred = np.full(
        (prepared.other_x.shape[0], len(target_indices)),
        np.nan,
        dtype=np.float64,
    )
    for out_col, target in enumerate(target_indices):
        own_train = np.concatenate(
            [
                prepared.train_r[:, target, :],
                prepared.train_m[:, target, :],
                prepared.train_a[:, target, :],
            ],
            axis=1,
        )
        own_other = np.concatenate(
            [
                prepared.other_r[:, target, :],
                prepared.other_m[:, target, :],
                prepared.other_a[:, target, :],
            ],
            axis=1,
        )
        if kind == "B2":
            own_train = np.concatenate(
                [
                    own_train,
                    common_features(prepared, target, train=True),
                ],
                axis=1,
            )
            own_other = np.concatenate(
                [
                    own_other,
                    common_features(prepared, target, train=False),
                ],
                axis=1,
            )

        model = fit_masked_multioutput(
            own_train,
            y_train[:, out_col : out_col + 1],
            alpha=alpha,
            minimum_support=MIN_TRAIN_TARGET,
        )
        pred[:, out_col] = model.predict(own_other)[:, 0]

    return pred


def metric_summary(
    y: np.ndarray,
    pred: np.ndarray,
    baseline: np.ndarray | None = None,
) -> dict[str, Any]:
    mse = masked_mse(y, pred)
    per = per_target_mse(y, pred)
    cross_ic, temporal_ic = predictive_ic(y, pred)
    mask = np.isfinite(y) & np.isfinite(pred)
    sign_mask = mask & (np.abs(y) > 1e-15)
    sign_accuracy = (
        float(
            np.mean(
                np.sign(y[sign_mask])
                == np.sign(pred[sign_mask])
            )
        )
        if sign_mask.any()
        else float("nan")
    )

    valid_y = y[np.isfinite(y)]
    if len(valid_y):
        sst = float(np.sum((valid_y - valid_y.mean()) ** 2))
    else:
        sst = float("nan")
    if mask.any():
        sse = float(np.sum((y[mask] - pred[mask]) ** 2))
    else:
        sse = float("nan")
    explained = (
        1.0 - sse / sst
        if np.isfinite(sst) and sst > 0
        else float("nan")
    )

    out: dict[str, Any] = {
        "mse": mse,
        "cross_sectional_ic": cross_ic,
        "median_time_series_ic": temporal_ic,
        "sign_accuracy": sign_accuracy,
        "explained_predictive_variance": explained,
        "per_target_mse": per,
    }

    if baseline is not None:
        base_mse = masked_mse(y, baseline)
        base_per = per_target_mse(y, baseline)
        pooled_imp = (
            (base_mse - mse) / base_mse
            if np.isfinite(base_mse) and base_mse > 0
            else float("nan")
        )
        valid = (
            np.isfinite(base_per)
            & np.isfinite(per)
            & (base_per > 0)
        )
        improvements = np.full(len(per), np.nan)
        improvements[valid] = (
            base_per[valid] - per[valid]
        ) / base_per[valid]
        helped = valid & (per < base_per)
        out.update(
            {
                "baseline_mse": base_mse,
                "pooled_mse_improvement": pooled_imp,
                "median_target_mse_improvement": (
                    float(np.nanmedian(improvements))
                    if valid.any()
                    else float("nan")
                ),
                "fraction_targets_helped": (
                    float(helped.sum() / valid.sum())
                    if valid.any()
                    else float("nan")
                ),
                "per_target_improvements": improvements,
                "gain_effective_targets": effective_number(
                    np.maximum(
                        np.nan_to_num(improvements, nan=0.0),
                        0.0,
                    )
                ),
            }
        )

    return out


def block_wins(
    y: np.ndarray,
    pred: np.ndarray,
    baseline: np.ndarray,
) -> int:
    wins = 0
    for idx in np.array_split(np.arange(len(y)), 5):
        if len(idx) == 0:
            continue
        candidate = masked_mse(y[idx], pred[idx])
        base = masked_mse(y[idx], baseline[idx])
        if (
            np.isfinite(candidate)
            and np.isfinite(base)
            and candidate < base
        ):
            wins += 1
    return wins


def candidate_row(
    panel: Panel,
    grid: int,
    horizon: int,
    model: str,
    lag: int,
    alpha: float | None,
    rank: int | None,
    y_dev: np.ndarray,
    pred: np.ndarray,
    b2_pred: np.ndarray,
) -> dict[str, Any]:
    metrics = metric_summary(y_dev, pred, b2_pred)
    wins = block_wins(y_dev, pred, b2_pred)
    passes = (
        float(
            metrics.get(
                "pooled_mse_improvement",
                float("nan"),
            )
        )
        > 0.0
        and float(
            metrics.get(
                "median_target_mse_improvement",
                float("nan"),
            )
        )
        > 0.0
        and float(
            metrics.get(
                "fraction_targets_helped",
                0.0,
            )
        )
        >= 0.50
        and wins >= 3
    )
    return {
        "family": panel.family,
        "panel_id": panel.panel_id,
        "panel_scope": panel.scope,
        "grid_seconds": grid,
        "horizon_seconds": horizon,
        "model": model,
        "lag_depth": lag,
        "alpha": alpha,
        "rank": rank,
        "dev_mse": metrics["mse"],
        "b2_dev_mse": metrics["baseline_mse"],
        "pooled_mse_improvement": (
            metrics["pooled_mse_improvement"]
        ),
        "median_target_mse_improvement": (
            metrics["median_target_mse_improvement"]
        ),
        "fraction_targets_helped": (
            metrics["fraction_targets_helped"]
        ),
        "cross_sectional_ic": metrics["cross_sectional_ic"],
        "median_time_series_ic": metrics["median_time_series_ic"],
        "sign_accuracy": metrics["sign_accuracy"],
        "explained_predictive_variance": (
            metrics["explained_predictive_variance"]
        ),
        "positive_dev_blocks": wins,
        "passes_dev_requirements": passes,
    }


def best_baseline(
    panel: Panel,
    series: dict[str, MarketSeries],
    train_q: np.ndarray,
    dev_q: np.ndarray,
    y_train: np.ndarray,
    y_dev: np.ndarray,
    target_indices: list[int],
    grid: int,
    kind: str,
) -> tuple[dict[str, Any], np.ndarray]:
    best: dict[str, Any] | None = None
    best_pred: np.ndarray | None = None

    for lag in LAG_DEPTHS:
        raw_train = raw_cube(
            panel,
            series,
            train_q,
            grid,
            lag,
            "logit",
        )
        raw_dev = raw_cube(
            panel,
            series,
            dev_q,
            grid,
            lag,
            "logit",
        )
        prepared = prepare_features(raw_train, raw_dev)

        for alpha in RIDGE_ALPHAS:
            pred = baseline_predictions(
                prepared,
                y_train,
                target_indices,
                kind=kind,
                alpha=alpha,
            )
            mse = masked_mse(y_dev, pred)
            row = {
                "kind": kind,
                "lag_depth": lag,
                "alpha": alpha,
                "dev_mse": mse,
            }
            if (
                best is None
                or (
                    np.isfinite(mse)
                    and mse < float(best["dev_mse"])
                )
            ):
                best = row
                best_pred = pred

    if best is None or best_pred is None:
        raise RuntimeError(f"no {kind} baseline")
    return best, best_pred


def fit_candidate(
    model_name: str,
    prepared: PreparedFeatures,
    y_train: np.ndarray,
    *,
    alpha: float | None,
    rank: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if model_name in {"M1", "M2", "M4"}:
        base = fit_masked_multioutput(
            prepared.train_x,
            y_train,
            alpha=(
                0.0
                if model_name == "M1"
                else float(alpha)
            ),
            minimum_support=MIN_TRAIN_TARGET,
        )
        model = (
            truncate_model(base, int(rank))
            if model_name == "M4"
            else base
        )
        return (
            model.predict(prepared.other_x),
            model.coef,
            prepared.source_groups,
            model.intercept,
        )

    if model_name == "M3":
        xr_train = prepared.train_r.reshape(
            prepared.train_r.shape[0],
            -1,
        )
        xr_other = prepared.other_r.reshape(
            prepared.other_r.shape[0],
            -1,
        )
        pca = fit_pca(xr_train, int(rank))
        z_train = pca.transform(xr_train)
        z_other = pca.transform(xr_other)
        model = fit_masked_multioutput(
            z_train,
            y_train,
            alpha=0.0,
            minimum_support=MIN_TRAIN_TARGET,
        )
        effective_coef = pca.components.T @ model.coef
        effective_intercept = (
            model.intercept
            - pca.mean @ effective_coef
        )
        return (
            model.predict(z_other),
            effective_coef,
            prepared.return_source_groups,
            effective_intercept,
        )

    raise ValueError(model_name)


def selection_spectrum(
    prepared: PreparedFeatures,
    y_train: np.ndarray,
    coef: np.ndarray,
) -> dict[str, Any]:
    xr = prepared.train_r.reshape(
        prepared.train_r.shape[0],
        -1,
    )
    if xr.shape[0] > 1:
        covariance = np.cov(xr, rowvar=False)
    else:
        covariance = np.zeros(
            (xr.shape[1], xr.shape[1])
        )

    means = np.nanmean(y_train, axis=0)
    centered = np.where(
        np.isfinite(y_train),
        y_train - means,
        0.0,
    )
    cross = xr.T @ centered / max(1, len(xr))

    return {
        "return_covariance": singular_spectrum(covariance),
        "predictive_cross_covariance": singular_spectrum(cross),
        "fitted_predictive_matrix": singular_spectrum(coef),
    }


def cell_stage1(
    panel: Panel,
    series: dict[str, MarketSeries],
    grid: int,
    horizon: int,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    if horizon < grid or horizon % grid:
        return None, [], None

    q = decision_times(panel, series, grid)
    train_mask, dev_mask, hold_mask, boundaries = split_masks(q)
    train_q = q[train_mask]
    dev_q = q[dev_mask]
    hold_q = q[hold_mask]
    if min(len(train_q), len(dev_q), len(hold_q)) < 30:
        return None, [], None

    y_train_full = target_matrix(
        panel,
        series,
        train_q,
        horizon,
        "logit",
        grid,
    )
    y_dev_full = target_matrix(
        panel,
        series,
        dev_q,
        horizon,
        "logit",
        grid,
    )

    hold_support = np.zeros(
        len(panel.markets),
        dtype=np.int64,
    )
    for j, cid in enumerate(panel.markets):
        hold_support[j] = int(
            future_mask(
                series[cid].times,
                hold_q,
                horizon,
                age_cap(grid),
            ).sum()
        )

    train_support = np.isfinite(y_train_full).sum(axis=0)
    dev_support = np.isfinite(y_dev_full).sum(axis=0)
    targets = [
        j
        for j in range(len(panel.markets))
        if train_support[j] >= MIN_TRAIN_TARGET
        and dev_support[j] >= MIN_DEV_TARGET
        and hold_support[j] >= MIN_HOLD_TARGET
    ]
    if len(targets) < 3:
        return None, [], None

    y_train = y_train_full[:, targets]
    y_dev = y_dev_full[:, targets]

    b1_cfg, b1_pred = best_baseline(
        panel,
        series,
        train_q,
        dev_q,
        y_train,
        y_dev,
        targets,
        grid,
        "B1",
    )
    b2_cfg, b2_pred = best_baseline(
        panel,
        series,
        train_q,
        dev_q,
        y_train,
        y_dev,
        targets,
        grid,
        "B2",
    )
    baseline_summary = {
        "B0_dev_mse": masked_mse(
            y_dev,
            np.zeros_like(y_dev),
        ),
        "B1": b1_cfg,
        "B2": b2_cfg,
        "B1_dev_mse": masked_mse(y_dev, b1_pred),
        "B2_dev_mse": masked_mse(y_dev, b2_pred),
    }

    rows: list[dict[str, Any]] = []
    feature_cache: dict[int, PreparedFeatures] = {}

    for lag in LAG_DEPTHS:
        raw_train = raw_cube(
            panel,
            series,
            train_q,
            grid,
            lag,
            "logit",
        )
        raw_dev = raw_cube(
            panel,
            series,
            dev_q,
            grid,
            lag,
            "logit",
        )
        prepared = prepare_features(raw_train, raw_dev)
        feature_cache[lag] = prepared

        p = prepared.train_x.shape[1]
        if len(train_q) >= 2 * (p + 1):
            pred, _, _, _ = fit_candidate(
                "M1",
                prepared,
                y_train,
                alpha=None,
                rank=None,
            )
            rows.append(
                candidate_row(
                    panel,
                    grid,
                    horizon,
                    "M1",
                    lag,
                    None,
                    None,
                    y_dev,
                    pred,
                    b2_pred,
                )
            )

        for alpha in RIDGE_ALPHAS:
            pred, _, _, _ = fit_candidate(
                "M2",
                prepared,
                y_train,
                alpha=alpha,
                rank=None,
            )
            rows.append(
                candidate_row(
                    panel,
                    grid,
                    horizon,
                    "M2",
                    lag,
                    alpha,
                    None,
                    y_dev,
                    pred,
                    b2_pred,
                )
            )

            base_model = fit_masked_multioutput(
                prepared.train_x,
                y_train,
                alpha=alpha,
                minimum_support=MIN_TRAIN_TARGET,
            )
            for rank in RANKS:
                k = min(
                    rank,
                    base_model.coef.shape[0],
                    base_model.coef.shape[1],
                )
                if k < 1:
                    continue
                model = truncate_model(base_model, k)
                pred_rr = model.predict(
                    prepared.other_x
                )
                rows.append(
                    candidate_row(
                        panel,
                        grid,
                        horizon,
                        "M4",
                        lag,
                        alpha,
                        k,
                        y_dev,
                        pred_rr,
                        b2_pred,
                    )
                )

        xr_train = prepared.train_r.reshape(
            prepared.train_r.shape[0],
            -1,
        )
        max_rank = min(xr_train.shape)
        for rank in RANKS:
            k = min(rank, max_rank, len(targets))
            if k < 1:
                continue
            pred, _, _, _ = fit_candidate(
                "M3",
                prepared,
                y_train,
                alpha=None,
                rank=k,
            )
            rows.append(
                candidate_row(
                    panel,
                    grid,
                    horizon,
                    "M3",
                    lag,
                    None,
                    k,
                    y_dev,
                    pred,
                    b2_pred,
                )
            )

    promoted = [
        row
        for row in rows
        if row["model"] in {"M2", "M3", "M4"}
        and row["passes_dev_requirements"]
    ]

    if not promoted:
        return (
            {
                "family": panel.family,
                "panel_id": panel.panel_id,
                "panel_scope": panel.scope,
                "grid_seconds": grid,
                "horizon_seconds": horizon,
                "selection": None,
                "target_ids": [
                    panel.markets[j]
                    for j in targets
                ],
                "panel_markets": list(panel.markets),
                "boundaries": boundaries,
                "baseline": baseline_summary,
            },
            rows,
            None,
        )

    promoted.sort(
        key=lambda row: (
            row["dev_mse"],
            -row["median_target_mse_improvement"],
            -row["cross_sectional_ic"],
        )
    )
    winner = promoted[0]
    lag = int(winner["lag_depth"])
    prepared = feature_cache[lag]
    _, coef, groups, _ = fit_candidate(
        str(winner["model"]),
        prepared,
        y_train,
        alpha=(
            None
            if winner["alpha"] in (None, "")
            else float(winner["alpha"])
        ),
        rank=(
            None
            if winner["rank"] in (None, "")
            else int(winner["rank"])
        ),
    )

    spectrum = selection_spectrum(
        prepared,
        y_train,
        coef,
    )
    concentration = source_target_concentration(
        coef,
        source_groups=groups,
    )

    selection = {
        "family": panel.family,
        "panel_id": panel.panel_id,
        "panel_scope": panel.scope,
        "panel_scope_value": panel.scope_value,
        "grid_seconds": grid,
        "horizon_seconds": horizon,
        "model": winner["model"],
        "lag_depth": lag,
        "alpha": winner["alpha"],
        "rank": winner["rank"],
        "panel_markets": list(panel.markets),
        "target_ids": [
            panel.markets[j]
            for j in targets
        ],
        "target_panel_indices": targets,
        "scaling": (
            "TRAIN observed-return mean/std; "
            "missing standardized returns=0 "
            "with explicit masks/ages"
        ),
        "evaluation_metrics": [
            "vector MSE",
            "per-market MSE",
            "cross-sectional IC",
            "time-series IC",
            "sign accuracy",
            "explained predictive variance",
            "market breadth",
            "5-block stability",
        ],
        "baseline": baseline_summary,
        "dev_metrics": winner,
        "train_concentration": concentration,
        "split_boundaries": {
            "train_dev": boundaries[0],
            "dev_holdout": boundaries[1],
        },
    }
    return selection, rows, spectrum


def model_for_holdout(
    selection: dict[str, Any],
    panel: Panel,
    series: dict[str, MarketSeries],
    coordinate: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    grid = int(selection["grid_seconds"])
    horizon = int(selection["horizon_seconds"])
    lag = int(selection["lag_depth"])

    q = decision_times(panel, series, grid)
    train_mask, _, hold_mask, _ = split_masks(q)
    train_q = q[train_mask]
    hold_q = q[hold_mask]
    target_indices = [
        int(x)
        for x in selection["target_panel_indices"]
    ]

    y_train_full = target_matrix(
        panel,
        series,
        train_q,
        horizon,
        coordinate,
        grid,
    )
    y_hold_full = target_matrix(
        panel,
        series,
        hold_q,
        horizon,
        coordinate,
        grid,
    )
    y_train = y_train_full[:, target_indices]
    y_hold = y_hold_full[:, target_indices]

    raw_train = raw_cube(
        panel,
        series,
        train_q,
        grid,
        lag,
        coordinate,
    )
    raw_hold = raw_cube(
        panel,
        series,
        hold_q,
        grid,
        lag,
        coordinate,
    )
    prepared = prepare_features(
        raw_train,
        raw_hold,
    )
    pred, coef, groups, intercept = fit_candidate(
        str(selection["model"]),
        prepared,
        y_train,
        alpha=(
            None
            if selection["alpha"] in (None, "")
            else float(selection["alpha"])
        ),
        rank=(
            None
            if selection["rank"] in (None, "")
            else int(selection["rank"])
        ),
    )

    b2_lag = int(
        selection["baseline"]["B2"]["lag_depth"]
    )
    b2_alpha = float(
        selection["baseline"]["B2"]["alpha"]
    )
    b2_train = raw_cube(
        panel,
        series,
        train_q,
        grid,
        b2_lag,
        coordinate,
    )
    b2_hold = raw_cube(
        panel,
        series,
        hold_q,
        grid,
        b2_lag,
        coordinate,
    )
    b2_prepared = prepare_features(
        b2_train,
        b2_hold,
    )
    b2_pred = baseline_predictions(
        b2_prepared,
        y_train,
        target_indices,
        kind="B2",
        alpha=b2_alpha,
    )

    metrics = metric_summary(
        y_hold,
        pred,
        b2_pred,
    )
    metrics["positive_holdout_blocks"] = block_wins(
        y_hold,
        pred,
        b2_pred,
    )
    metrics["concentration"] = (
        source_target_concentration(
            coef,
            source_groups=groups,
        )
    )

    state = {
        "prepared": prepared,
        "y_train": y_train,
        "y_hold": y_hold,
        "pred": pred,
        "b2_pred": b2_pred,
        "coef": coef,
        "groups": groups,
        "intercept": intercept,
        "train_q": train_q,
        "hold_q": hold_q,
        "target_indices": target_indices,
    }
    return metrics, state


def block_bootstrap(
    loss_diff: np.ndarray,
    component: str,
) -> dict[str, Any]:
    values = np.asarray(
        loss_diff,
        dtype=np.float64,
    )
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return {
            "mean": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "n": len(values),
        }

    block = max(
        10,
        int(round(math.sqrt(len(values)))),
    )
    block = min(block, len(values))
    needed = math.ceil(len(values) / block)
    seed = int.from_bytes(
        hashlib.sha256(
            f"{MASTER_SEED}|{component}".encode()
        ).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)
    draws = np.empty(1000, dtype=np.float64)
    max_start = len(values) - block

    for draw in range(1000):
        starts = rng.integers(
            0,
            max_start + 1,
            size=needed,
        )
        sample = np.concatenate(
            [
                values[start : start + block]
                for start in starts
            ]
        )[: len(values)]
        draws[draw] = float(sample.mean())

    return {
        "mean": float(values.mean()),
        "ci_low": float(
            np.quantile(draws, 0.025)
        ),
        "ci_high": float(
            np.quantile(draws, 0.975)
        ),
        "n": len(values),
        "block_rows": block,
        "draws": 1000,
    }


def per_time_loss_diff(
    y: np.ndarray,
    pred: np.ndarray,
    baseline: np.ndarray,
) -> np.ndarray:
    out = np.full(
        len(y),
        np.nan,
        dtype=np.float64,
    )
    for i in range(len(y)):
        mask = (
            np.isfinite(y[i])
            & np.isfinite(pred[i])
            & np.isfinite(baseline[i])
        )
        if mask.any():
            base = np.mean(
                (y[i, mask] - baseline[i, mask]) ** 2
            )
            candidate = np.mean(
                (y[i, mask] - pred[i, mask]) ** 2
            )
            out[i] = base - candidate
    return out


def holdout_ablations(
    selection: dict[str, Any],
    panel: Panel,
    series: dict[str, MarketSeries],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    prepared: PreparedFeatures = state["prepared"]
    y_train: np.ndarray = state["y_train"]
    y_hold: np.ndarray = state["y_hold"]
    base_pred: np.ndarray = state["b2_pred"]
    eff: np.ndarray = state["coef"]
    intercept: np.ndarray = state["intercept"]
    target_indices: list[int] = state["target_indices"]

    if selection["model"] == "M3":
        x_train_direct = prepared.train_r.reshape(
            prepared.train_r.shape[0],
            -1,
        )
        x_hold_direct = prepared.other_r.reshape(
            prepared.other_r.shape[0],
            -1,
        )
        direct_groups = prepared.return_source_groups
    else:
        x_train_direct = prepared.train_x
        x_hold_direct = prepared.other_x
        direct_groups = prepared.source_groups

    rows: list[dict[str, Any]] = []

    def record(
        name: str,
        pred: np.ndarray,
        detail: str,
    ) -> None:
        summary = metric_summary(
            y_hold,
            pred,
            base_pred,
        )
        rows.append(
            {
                "family": panel.family,
                "panel_id": panel.panel_id,
                "grid_seconds": (
                    selection["grid_seconds"]
                ),
                "horizon_seconds": (
                    selection["horizon_seconds"]
                ),
                "model": selection["model"],
                "ablation": name,
                "detail": detail,
                "holdout_mse": summary["mse"],
                "pooled_mse_improvement_vs_b2": (
                    summary.get(
                        "pooled_mse_improvement"
                    )
                ),
                "median_target_mse_improvement_vs_b2": (
                    summary.get(
                        "median_target_mse_improvement"
                    )
                ),
                "fraction_targets_helped": (
                    summary.get(
                        "fraction_targets_helped"
                    )
                ),
            }
        )

    own = eff.copy()
    for out_col, target in enumerate(target_indices):
        own[
            direct_groups == target,
            out_col,
        ] = 0.0
    record(
        "remove_own_history",
        x_hold_direct @ own + intercept,
        "zero own-market source coefficients",
    )

    source_norms: list[tuple[float, int]] = []
    for source in np.unique(direct_groups):
        source_norms.append(
            (
                float(
                    np.linalg.norm(
                        eff[direct_groups == source]
                    )
                ),
                int(source),
            )
        )
    source_norms.sort(reverse=True)
    for _, source in source_norms[:3]:
        removed = eff.copy()
        removed[direct_groups == source, :] = 0.0
        record(
            "remove_strongest_source",
            x_hold_direct @ removed + intercept,
            panel.markets[source],
        )

    activity: list[tuple[float, int]] = []
    for source in np.unique(direct_groups):
        activity.append(
            (
                float(
                    prepared.train_m[
                        :, int(source), :
                    ].sum()
                ),
                int(source),
            )
        )
    activity.sort(reverse=True)
    if activity:
        source = activity[0][1]
        removed = eff.copy()
        removed[direct_groups == source, :] = 0.0
        record(
            "remove_highest_activity_source",
            x_hold_direct @ removed + intercept,
            panel.markets[source],
        )

    if panel.scope == "family":
        event_groups: dict[str, list[int]] = {}
        for j, cid in enumerate(panel.markets):
            event_groups.setdefault(
                series[cid].event_id,
                [],
            ).append(j)
        candidates = [
            (len(value), key, value)
            for key, value in event_groups.items()
            if key and len(value) < len(panel.markets)
        ]
        if candidates:
            _, event_id, market_ids = max(
                candidates
            )
            removed = eff.copy()
            removed[
                np.isin(
                    direct_groups,
                    market_ids,
                ),
                :,
            ] = 0.0
            record(
                "remove_event_subgroup",
                x_hold_direct @ removed + intercept,
                event_id,
            )

    centered = (
        x_train_direct
        - x_train_direct.mean(axis=0)
    )
    _, _, vt = np.linalg.svd(
        centered,
        full_matrices=False,
    )
    direction = vt[0]
    train_resid = (
        x_train_direct
        - np.outer(
            x_train_direct @ direction,
            direction,
        )
    )
    hold_resid = (
        x_hold_direct
        - np.outer(
            x_hold_direct @ direction,
            direction,
        )
    )

    if selection["model"] == "M3":
        pca = fit_pca(
            train_resid,
            int(selection["rank"]),
        )
        z_train = pca.transform(train_resid)
        z_hold = pca.transform(hold_resid)
        model = fit_masked_multioutput(
            z_train,
            y_train,
            alpha=0.0,
            minimum_support=MIN_TRAIN_TARGET,
        )
        common_pred = model.predict(z_hold)
    else:
        model = fit_masked_multioutput(
            train_resid,
            y_train,
            alpha=float(selection["alpha"]),
            minimum_support=MIN_TRAIN_TARGET,
        )
        if selection["model"] == "M4":
            model = truncate_model(
                model,
                int(selection["rank"]),
            )
        common_pred = model.predict(hold_resid)

    record(
        "remove_common_factor",
        common_pred,
        "leading TRAIN predictor direction projected out",
    )

    train_shift = max(
        1,
        len(x_train_direct) // 3,
    )
    hold_shift = max(
        1,
        len(x_hold_direct) // 3,
    )
    x_train_roll = np.roll(
        x_train_direct,
        train_shift,
        axis=0,
    )
    x_hold_roll = np.roll(
        x_hold_direct,
        hold_shift,
        axis=0,
    )

    if selection["model"] == "M3":
        pca = fit_pca(
            x_train_roll,
            int(selection["rank"]),
        )
        z_train = pca.transform(x_train_roll)
        z_hold = pca.transform(x_hold_roll)
        null_model = fit_masked_multioutput(
            z_train,
            y_train,
            alpha=0.0,
            minimum_support=MIN_TRAIN_TARGET,
        )
        null_pred = null_model.predict(z_hold)
    else:
        null_model = fit_masked_multioutput(
            x_train_roll,
            y_train,
            alpha=float(selection["alpha"]),
            minimum_support=MIN_TRAIN_TARGET,
        )
        if selection["model"] == "M4":
            null_model = truncate_model(
                null_model,
                int(selection["rank"]),
            )
        null_pred = null_model.predict(x_hold_roll)

    record(
        "circular_block_shift",
        null_pred,
        (
            f"row shifts train={train_shift}, "
            f"hold={hold_shift}"
        ),
    )

    # Delayed-panel placebo: shift predictor clock by one target horizon.
    grid = int(selection["grid_seconds"])
    horizon = int(selection["horizon_seconds"])
    lag = int(selection["lag_depth"])
    train_q = state["train_q"] - horizon
    hold_q = state["hold_q"] - horizon
    delayed_train = raw_cube(
        panel,
        series,
        train_q,
        grid,
        lag,
        "logit",
    )
    delayed_hold = raw_cube(
        panel,
        series,
        hold_q,
        grid,
        lag,
        "logit",
    )
    delayed = prepare_features(
        delayed_train,
        delayed_hold,
    )
    delayed_pred, _, _, _ = fit_candidate(
        str(selection["model"]),
        delayed,
        y_train,
        alpha=(
            None
            if selection["alpha"] in (None, "")
            else float(selection["alpha"])
        ),
        rank=(
            None
            if selection["rank"] in (None, "")
            else int(selection["rank"])
        ),
    )
    record(
        "delayed_panel_placebo",
        delayed_pred,
        f"predictor panel delayed by {horizon}s",
    )

    return rows


def final_label(
    selections: list[dict[str, Any]],
    holdout_rows: list[dict[str, Any]],
    no_selection_cells: list[dict[str, Any]],
) -> str:
    if not selections:
        any_common = any(
            cell["baseline"]["B2_dev_mse"]
            < cell["baseline"]["B1_dev_mse"]
            for cell in no_selection_cells
            if cell.get("baseline")
        )
        return (
            "COMMON-STATE ONLY"
            if any_common
            else "NO JOINT PREDICTIVE STRUCTURE"
        )

    strong = [
        row
        for row in holdout_rows
        if row["pooled_mse_improvement_vs_b2"] > 0
        and row["median_target_mse_improvement_vs_b2"] > 0
        and row["fraction_targets_helped"] >= 0.50
        and row["positive_holdout_blocks"] >= 3
    ]
    if not strong:
        positive = [
            row
            for row in holdout_rows
            if row["pooled_mse_improvement_vs_b2"] > 0
        ]
        return (
            "WEAK / CONCENTRATED"
            if positive
            else "COMMON-STATE ONLY"
        )

    dense = [
        row
        for row in strong
        if row["effective_source_markets"] >= 3.0
    ]
    if not dense:
        return "WEAK / CONCENTRATED"
    if any(
        row["panel_scope"] == "family"
        for row in dense
    ):
        return "WITHIN-FAMILY JOINT CANDIDATE"
    return "REGIME-SPECIFIC JOINT CANDIDATE"


def main() -> None:
    reconstruction_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    family_series: dict[
        str,
        dict[str, MarketSeries],
    ] = {}
    panels: list[Panel] = []

    for family in FAMILIES:
        series, meta, audit = reconstruction_family(
            family
        )
        family_series[family] = series
        reconstruction_rows.append(audit)
        selected, coverage = select_panels(
            family,
            series,
            meta,
        )
        panels.extend(selected)
        coverage_rows.extend(coverage)

    write_csv(
        WORK / "reconstruction_audit.csv",
        reconstruction_rows,
    )
    write_csv(
        WORK / "panel_coverage.csv",
        coverage_rows,
    )

    dev_rows: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    no_selection_cells: list[dict[str, Any]] = []
    spectra: dict[str, Any] = {}

    for p_index, panel in enumerate(
        panels,
        start=1,
    ):
        print(
            f"DEV PANEL {p_index}/{len(panels)} "
            f"{panel.panel_id} "
            f"markets={len(panel.markets)}"
        )
        series = family_series[panel.family]
        for grid in GRIDS:
            for horizon in HORIZONS:
                if (
                    horizon < grid
                    or horizon % grid
                ):
                    continue
                selection, rows, spectrum = cell_stage1(
                    panel,
                    series,
                    grid,
                    horizon,
                )
                dev_rows.extend(rows)
                if selection is None:
                    continue
                if selection.get("selection", "promoted") is None:
                    no_selection_cells.append(selection)
                else:
                    selections.append(selection)
                    spectra[
                        (
                            f"{panel.panel_id}|"
                            f"g{grid}|h{horizon}"
                        )
                    ] = spectrum

    write_csv(
        WORK / "dev_model_candidates.csv",
        dev_rows,
    )
    (
        WORK / "train_singular_spectra.json"
    ).write_text(
        json.dumps(
            spectra,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    freeze = {
        "experiment_id": "EXPERIMENT-005C",
        "freeze_commit": FREEZE_COMMIT,
        "preregistration_sha256": PREREG_SHA,
        "implementation_commit": (
            CODE_MANIFEST["implementation_commit"]
        ),
        "status": "IMMUTABLE_PRE_HOLDOUT_SELECTION",
        "selection_count": len(selections),
        "selections": selections,
        "no_selection_cells": no_selection_cells,
    }
    freeze_path = WORK / "PRE_HOLDOUT_FREEZE.json"
    freeze_path.write_text(
        json.dumps(
            freeze,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    freeze_sha = sha256(freeze_path)
    print(
        "PRE_HOLDOUT_FREEZE "
        f"sha256={freeze_sha} "
        f"selections={len(selections)}"
    )

    # HOLDOUT starts only after the immutable file exists and is hashed.
    holdout_rows: list[dict[str, Any]] = []
    ablation_rows: list[dict[str, Any]] = []
    robustness_rows: list[dict[str, Any]] = []
    panel_lookup = {
        panel.panel_id: panel
        for panel in panels
    }

    for index, selection in enumerate(
        selections,
        start=1,
    ):
        panel = panel_lookup[
            selection["panel_id"]
        ]
        series = family_series[panel.family]
        print(
            f"HOLDOUT {index}/{len(selections)} "
            f"{panel.panel_id} "
            f"g={selection['grid_seconds']} "
            f"h={selection['horizon_seconds']}"
        )

        metrics, state = model_for_holdout(
            selection,
            panel,
            series,
            "logit",
        )
        loss_diff = per_time_loss_diff(
            state["y_hold"],
            state["pred"],
            state["b2_pred"],
        )
        uncertainty = block_bootstrap(
            loss_diff,
            (
                f"{panel.panel_id}|"
                f"{selection['grid_seconds']}|"
                f"{selection['horizon_seconds']}"
            ),
        )
        concentration = metrics["concentration"]
        holdout_rows.append(
            {
                "family": panel.family,
                "panel_id": panel.panel_id,
                "panel_scope": panel.scope,
                "grid_seconds": (
                    selection["grid_seconds"]
                ),
                "horizon_seconds": (
                    selection["horizon_seconds"]
                ),
                "model": selection["model"],
                "lag_depth": selection["lag_depth"],
                "alpha": selection["alpha"],
                "rank": selection["rank"],
                "holdout_mse": metrics["mse"],
                "b2_holdout_mse": (
                    metrics["baseline_mse"]
                ),
                "pooled_mse_improvement_vs_b2": (
                    metrics["pooled_mse_improvement"]
                ),
                "median_target_mse_improvement_vs_b2": (
                    metrics[
                        "median_target_mse_improvement"
                    ]
                ),
                "fraction_targets_helped": (
                    metrics["fraction_targets_helped"]
                ),
                "cross_sectional_ic": (
                    metrics["cross_sectional_ic"]
                ),
                "median_time_series_ic": (
                    metrics["median_time_series_ic"]
                ),
                "sign_accuracy": (
                    metrics["sign_accuracy"]
                ),
                "explained_predictive_variance": (
                    metrics[
                        "explained_predictive_variance"
                    ]
                ),
                "positive_holdout_blocks": (
                    metrics["positive_holdout_blocks"]
                ),
                "effective_source_markets": (
                    concentration[
                        "effective_source_markets"
                    ]
                ),
                "effective_target_markets": (
                    concentration[
                        "effective_target_markets"
                    ]
                ),
                "max_source_share": (
                    concentration["max_source_share"]
                ),
                "bootstrap_loss_advantage_mean": (
                    uncertainty["mean"]
                ),
                "bootstrap_ci_low": (
                    uncertainty["ci_low"]
                ),
                "bootstrap_ci_high": (
                    uncertainty["ci_high"]
                ),
                "bootstrap_n_times": uncertainty["n"],
                "bootstrap_block_rows": (
                    uncertainty.get("block_rows")
                ),
            }
        )

        ablation_rows.extend(
            holdout_ablations(
                selection,
                panel,
                series,
                state,
            )
        )

        robust_metrics, _ = model_for_holdout(
            selection,
            panel,
            series,
            "probability",
        )
        robustness_rows.append(
            {
                "family": panel.family,
                "panel_id": panel.panel_id,
                "grid_seconds": (
                    selection["grid_seconds"]
                ),
                "horizon_seconds": (
                    selection["horizon_seconds"]
                ),
                "model": selection["model"],
                "coordinate": "probability",
                "pooled_mse_improvement_vs_b2": (
                    robust_metrics[
                        "pooled_mse_improvement"
                    ]
                ),
                "median_target_mse_improvement_vs_b2": (
                    robust_metrics[
                        "median_target_mse_improvement"
                    ]
                ),
                "fraction_targets_helped": (
                    robust_metrics[
                        "fraction_targets_helped"
                    ]
                ),
                "cross_sectional_ic": (
                    robust_metrics[
                        "cross_sectional_ic"
                    ]
                ),
                "positive_holdout_blocks": (
                    robust_metrics[
                        "positive_holdout_blocks"
                    ]
                ),
            }
        )

    write_csv(
        WORK / "holdout_results.csv",
        holdout_rows,
    )
    write_csv(
        WORK / "ablation_null_results.csv",
        ablation_rows,
    )
    write_csv(
        WORK / "probability_coordinate_robustness.csv",
        robustness_rows,
    )

    label = final_label(
        selections,
        holdout_rows,
        no_selection_cells,
    )
    manifest = {
        "experiment_id": "EXPERIMENT-005C",
        "freeze_commit": FREEZE_COMMIT,
        "preregistration_sha256": PREREG_SHA,
        "implementation_commit": (
            CODE_MANIFEST["implementation_commit"]
        ),
        "code_manifest_sha256": sha256(
            one("code_manifest.json")
        ),
        "pre_holdout_freeze_sha256": freeze_sha,
        "families": list(FAMILIES),
        "eligible_panels": len(panels),
        "dev_candidate_rows": len(dev_rows),
        "dev_promoted_selections": len(selections),
        "holdout_rows": len(holdout_rows),
        "final_research_label": label,
        "outputs": {},
    }

    for path in sorted(WORK.iterdir()):
        if (
            path.is_file()
            and path.name != "run_manifest.json"
        ):
            manifest["outputs"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }

    (
        WORK / "run_manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    best = None
    if holdout_rows:
        best = sorted(
            holdout_rows,
            key=lambda row: (
                -float(
                    row[
                        "pooled_mse_improvement_vs_b2"
                    ]
                ),
                -float(
                    row[
                        "fraction_targets_helped"
                    ]
                ),
            ),
        )[0]

    lines = [
        "# EXPERIMENT-005C — MASTER HANDOFF",
        "",
        f"Disposition: {label}",
        (
            "Frozen design: "
            f"{FREEZE_COMMIT} / "
            f"prereg {PREREG_SHA}"
        ),
        (
            "Implementation: "
            f"{CODE_MANIFEST['implementation_commit']}"
        ),
        (
            "Pre-HOLDOUT freeze SHA-256: "
            f"{freeze_sha}"
        ),
        "",
        "## Reconstruction",
        "",
    ]
    for row in reconstruction_rows:
        lines.append(
            f"- {row['family']}: "
            f"{row['economic_condition_second_observations']:,} "
            "economic condition-second observations; "
            "valid passive-notional fraction "
            f"{row['valid_passive_notional_fraction']:.6f}; "
            "invalid role-structure groups "
            f"{row['invalid_role_structure_groups']}."
        )

    lines.extend(
        [
            "",
            "## Search / selection",
            "",
            (
                "- Eligible metadata/coverage-defined panels: "
                f"{len(panels)}."
            ),
            (
                "- DEV model rows evaluated: "
                f"{len(dev_rows)}."
            ),
            (
                "- Frozen DEV-promoted joint selections: "
                f"{len(selections)}."
            ),
            (
                "- HOLDOUT evaluations: "
                f"{len(holdout_rows)}."
            ),
            "",
            "## Headline",
            "",
        ]
    )

    if best is None:
        lines.append(
            "No joint model passed the frozen DEV gate, "
            "so no joint model was promoted into HOLDOUT."
        )
    else:
        lines.append(
            "Strongest HOLDOUT row by pooled MSE improvement: "
            f"{best['model']} on {best['panel_id']} at "
            f"grid {best['grid_seconds']}s / horizon "
            f"{best['horizon_seconds']}s; pooled improvement "
            f"vs B2 {best['pooled_mse_improvement_vs_b2']:.6f}, "
            "median target improvement "
            f"{best['median_target_mse_improvement_vs_b2']:.6f}, "
            "target breadth "
            f"{best['fraction_targets_helped']:.3f}, "
            "effective sources "
            f"{best['effective_source_markets']:.2f}."
        )

    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            (
                "- B2 is the frozen own-history + "
                "leave-target-out common-state baseline."
            ),
            (
                "- Missing/quiet returns are never treated "
                "as observed zero; zero imputation occurs "
                "only after an explicit mask is carried."
            ),
            (
                "- HOLDOUT was not scored until "
                "PRE_HOLDOUT_FREEZE.json existed and "
                "had been hashed."
            ),
            (
                "- No fee, participant, wallet, "
                "book-microstructure, execution or P&L "
                "variables enter 005C."
            ),
            (
                "- Probability-space robustness, source "
                "concentration, ablations and block "
                "uncertainty are separate outputs."
            ),
            "",
            "## Outputs",
            "",
            "- reconstruction_audit.csv",
            "- panel_coverage.csv",
            "- dev_model_candidates.csv",
            "- train_singular_spectra.json",
            "- PRE_HOLDOUT_FREEZE.json",
            "- holdout_results.csv",
            "- probability_coordinate_robustness.csv",
            "- ablation_null_results.csv",
            "- run_manifest.json",
            "",
        ]
    )
    (
        WORK / "MASTER_HANDOFF_005C.md"
    ).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
