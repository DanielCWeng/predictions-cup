from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

INPUT_ROOT = Path("/kaggle/input")
manifest_candidates = sorted(INPUT_ROOT.rglob("snapshot_manifest.json"))
if len(manifest_candidates) != 1:
    raise RuntimeError(
        f"expected exactly one 004C-C snapshot manifest under {INPUT_ROOT}, "
        f"found {len(manifest_candidates)}: {manifest_candidates}"
    )
INPUT = manifest_candidates[0].parent
OUTPUT = Path("/kaggle/working/004c_c_crossvenue")
OUTPUT.mkdir(parents=True, exist_ok=True)

BASELINE_FEATURES = [
    "sig_best_bid",
    "sig_best_ask",
    "sig_mid",
    "sig_spread",
    "sig_quote_age_seconds",
    "sig_signed_change_30s",
    "sig_absolute_change_30s",
    "sig_update_count_30s",
    "sig_scalar_trusted",
    "regime_pre_election",
]
PM_FEATURES = [
    "pm_best_bid",
    "pm_best_ask",
    "pm_mid",
    "pm_spread",
    "pm_signed_innovation_5s",
    "pm_absolute_innovation_5s",
    "pm_quote_age_seconds",
    "relative_pm_minus_sig_freshness_seconds",
    "pm_minus_sig_mid_gap",
    "pm_update_count_30s",
    "pm_bid_size",
    "pm_ask_size",
    "pm_depth_age_seconds",
    "pm_depth_available",
]
CHALLENGER_FEATURES = BASELINE_FEATURES + PM_FEATURES


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantiles(values: pd.Series) -> dict[str, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"p05": None, "p50": None, "p95": None}
    return {
        "p05": float(clean.quantile(0.05)),
        "p50": float(clean.quantile(0.50)),
        "p95": float(clean.quantile(0.95)),
    }


def as_utc_ns(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True).astype("datetime64[ns, UTC]")


def logit(values: pd.Series, epsilon: float) -> pd.Series:
    clipped = values.clip(lower=epsilon, upper=1.0 - epsilon)
    return np.log(clipped / (1.0 - clipped))


def top_size(levels: object, price: float) -> float:
    if not isinstance(levels, (list, np.ndarray)):
        return math.nan
    total = 0.0
    found = False
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            level_price = float(level["price"])
            level_size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isclose(level_price, price, abs_tol=1e-12):
            total += level_size
            found = True
    return total if found else math.nan


def previous_asof(
    source: pd.DataFrame,
    lookup_times: pd.Series,
    *,
    time_col: str,
    columns: list[str],
    prefix: str,
) -> pd.DataFrame:
    left = pd.DataFrame({"lookup_at": as_utc_ns(lookup_times)}).sort_values("lookup_at")
    right = source[[time_col] + columns].sort_values(time_col).copy()
    joined = pd.merge_asof(
        left,
        right,
        left_on="lookup_at",
        right_on=time_col,
        direction="backward",
        allow_exact_matches=True,
    )
    rename = {column: f"{prefix}{column}" for column in columns}
    return joined.rename(columns=rename).drop(columns=[time_col])


def first_after(
    source: pd.DataFrame,
    lookup_times: pd.Series,
    *,
    time_col: str,
    columns: list[str],
    prefix: str,
) -> pd.DataFrame:
    left = pd.DataFrame({"lookup_at": as_utc_ns(lookup_times)}).sort_values("lookup_at")
    right = source[[time_col] + columns].sort_values(time_col).copy()
    joined = pd.merge_asof(
        left,
        right,
        left_on="lookup_at",
        right_on=time_col,
        direction="forward",
        allow_exact_matches=True,
    )
    rename = {column: f"{prefix}{column}" for column in columns}
    return joined.rename(columns=rename).drop(columns=[time_col])


def update_counts(times: pd.Series, decisions: pd.Series, window_seconds: int) -> np.ndarray:
    event_ns = as_utc_ns(times).astype("int64").to_numpy()
    decision_ns = as_utc_ns(decisions).astype("int64").to_numpy()
    window_ns = int(window_seconds * 1_000_000_000)
    right = np.searchsorted(event_ns, decision_ns, side="right")
    left = np.searchsorted(event_ns, decision_ns - window_ns, side="right")
    return right - left


def prepare_depth(depth: pd.DataFrame) -> pd.DataFrame:
    if depth.empty:
        return depth
    depth = depth.copy()
    depth["recorded_at"] = as_utc_ns(depth["recorded_at"])
    depth["best_bid"] = pd.to_numeric(depth["best_bid"], errors="coerce")
    depth["best_ask"] = pd.to_numeric(depth["best_ask"], errors="coerce")
    depth["pm_bid_size"] = [
        top_size(levels, price)
        for levels, price in zip(depth["bids"], depth["best_bid"], strict=True)
    ]
    depth["pm_ask_size"] = [
        top_size(levels, price)
        for levels, price in zip(depth["asks"], depth["best_ask"], strict=True)
    ]
    return depth


def build_exact_rows(
    sig: pd.DataFrame,
    pm_changes: pd.DataFrame,
    depth: pd.DataFrame,
    map_frame: pd.DataFrame,
    prereg: dict[str, object],
) -> pd.DataFrame:
    impl = json.loads(
        (INPUT / "implementation_freeze_001.json").read_text()
    ) if (INPUT / "implementation_freeze_001.json").exists() else {
        "exact": {
            "sig_max_quote_age_seconds": 20,
            "pm_innovation_lookback_seconds": 5,
            "sig_self_history_seconds": 30,
            "intensity_window_seconds": 30,
            "logit_epsilon": 1e-6,
        }
    }
    exact_cfg = impl["exact"]
    sig_age_limit = float(exact_cfg["sig_max_quote_age_seconds"])
    innovation_seconds = int(exact_cfg["pm_innovation_lookback_seconds"])
    self_history_seconds = int(exact_cfg["sig_self_history_seconds"])
    intensity_seconds = int(exact_cfg["intensity_window_seconds"])
    epsilon = float(exact_cfg["logit_epsilon"])
    horizon_seconds = int(prereg["observability"]["primary_fixed_horizon_seconds"])
    entry_latency_ms = int(prereg["execution"]["entry_latency_ms"])
    stress_latency_ms = int(prereg["execution"]["stress_latency_ms"])

    rows: list[pd.DataFrame] = []
    for mapping in map_frame.itertuples(index=False):
        sig_group = sig[sig["exchange_id"] == mapping.sig_exchange_id].copy()
        changes = pm_changes[pm_changes["token_id"] == mapping.pm_token_id].copy()
        if sig_group.empty or changes.empty:
            continue

        sig_group = sig_group.sort_values("rest_observed_at").drop_duplicates(
            subset=["rest_observed_at"], keep="last"
        )
        for column in ["best_bid", "best_ask"]:
            sig_group[column] = pd.to_numeric(sig_group[column], errors="coerce")
        sig_group = sig_group.dropna(subset=["best_bid", "best_ask"])
        sig_group = sig_group[sig_group["best_bid"] <= sig_group["best_ask"]]
        if sig_group.empty:
            continue
        sig_group["sig_mid_source"] = (
            sig_group["best_bid"] + sig_group["best_ask"]
        ) / 2.0

        for column in ["best_bid", "best_ask"]:
            changes[column] = pd.to_numeric(changes[column], errors="coerce")
        changes = changes.dropna(subset=["best_bid", "best_ask"]).sort_values("observed_at")
        changes = changes[changes["best_bid"] <= changes["best_ask"]]
        changes["pm_mid_source"] = (changes["best_bid"] + changes["best_ask"]) / 2.0
        changes = changes[
            changes["pm_mid_source"].ne(changes["pm_mid_source"].shift())
        ].copy()
        if len(changes) < 2:
            continue

        decisions = changes[["observed_at", "best_bid", "best_ask", "pm_mid_source"]].copy()
        decisions = decisions.rename(
            columns={
                "observed_at": "decision_at",
                "best_bid": "pm_best_bid",
                "best_ask": "pm_best_ask",
                "pm_mid_source": "pm_mid",
            }
        ).reset_index(drop=True)
        decisions["pm_spread"] = decisions["pm_best_ask"] - decisions["pm_best_bid"]

        current_sig = previous_asof(
            sig_group,
            decisions["decision_at"],
            time_col="rest_observed_at",
            columns=["best_bid", "best_ask", "sig_mid_source"],
            prefix="current_",
        )
        current_sig = current_sig.rename(
            columns={
                "current_best_bid": "sig_best_bid",
                "current_best_ask": "sig_best_ask",
                "current_sig_mid_source": "sig_mid",
            }
        )
        decisions = pd.concat([decisions, current_sig.drop(columns=["lookup_at"])], axis=1)

        sig_prev = previous_asof(
            sig_group,
            decisions["decision_at"] - pd.to_timedelta(self_history_seconds, unit="s"),
            time_col="rest_observed_at",
            columns=["sig_mid_source"],
            prefix="history_",
        )
        decisions["sig_mid_30s_ago"] = sig_prev["history_sig_mid_source"].to_numpy()

        pm_prev = previous_asof(
            changes.rename(columns={"observed_at": "change_at"}),
            decisions["decision_at"] - pd.to_timedelta(innovation_seconds, unit="s"),
            time_col="change_at",
            columns=["pm_mid_source"],
            prefix="history_",
        )
        decisions["pm_mid_5s_ago"] = pm_prev["history_pm_mid_source"].to_numpy()

        fixed_target = first_after(
            sig_group,
            decisions["decision_at"] + pd.to_timedelta(horizon_seconds, unit="s"),
            time_col="rest_observed_at",
            columns=["best_bid", "best_ask", "sig_mid_source"],
            prefix="target_",
        )
        decisions["target_sig_bid"] = fixed_target["target_best_bid"].to_numpy()
        decisions["target_sig_ask"] = fixed_target["target_best_ask"].to_numpy()
        decisions["target_sig_mid"] = fixed_target["target_sig_mid_source"].to_numpy()

        next_target = first_after(
            sig_group,
            decisions["decision_at"] + pd.to_timedelta(1, unit="ns"),
            time_col="rest_observed_at",
            columns=["sig_mid_source"],
            prefix="next_",
        )
        decisions["next_sig_mid"] = next_target["next_sig_mid_source"].to_numpy()

        entry = previous_asof(
            sig_group,
            decisions["decision_at"] + pd.to_timedelta(entry_latency_ms, unit="ms"),
            time_col="rest_observed_at",
            columns=["best_bid", "best_ask"],
            prefix="entry_",
        )
        decisions["entry_bid"] = entry["entry_best_bid"].to_numpy()
        decisions["entry_ask"] = entry["entry_best_ask"].to_numpy()

        stress = previous_asof(
            sig_group,
            decisions["decision_at"] + pd.to_timedelta(stress_latency_ms, unit="ms"),
            time_col="rest_observed_at",
            columns=["best_bid", "best_ask"],
            prefix="stress_",
        )
        decisions["stress_entry_bid"] = stress["stress_best_bid"].to_numpy()
        decisions["stress_entry_ask"] = stress["stress_best_ask"].to_numpy()

        # Recover the actual as-of timestamp using a merge that retains the right key.
        left = decisions[["decision_at"]].sort_values("decision_at")
        right = sig_group[["rest_observed_at"]].sort_values("rest_observed_at")
        sig_clock = pd.merge_asof(
            left,
            right,
            left_on="decision_at",
            right_on="rest_observed_at",
            direction="backward",
        )
        decisions["sig_quote_age_seconds"] = (
            decisions["decision_at"] - sig_clock["rest_observed_at"]
        ).dt.total_seconds().to_numpy()

        decisions["sig_signed_change_30s"] = (
            decisions["sig_mid"] - decisions["sig_mid_30s_ago"]
        )
        decisions["sig_absolute_change_30s"] = decisions["sig_signed_change_30s"].abs()
        decisions["sig_update_count_30s"] = update_counts(
            sig_group["rest_observed_at"],
            decisions["decision_at"],
            intensity_seconds,
        )
        decisions["pm_update_count_30s"] = update_counts(
            changes["observed_at"],
            decisions["decision_at"],
            intensity_seconds,
        )
        decisions["pm_quote_age_seconds"] = 0.0
        decisions["relative_pm_minus_sig_freshness_seconds"] = (
            -decisions["sig_quote_age_seconds"]
        )
        decisions["pm_minus_sig_mid_gap"] = decisions["pm_mid"] - decisions["sig_mid"]
        decisions["pm_signed_innovation_5s"] = (
            logit(decisions["pm_mid"], epsilon) - logit(decisions["pm_mid_5s_ago"], epsilon)
        )
        decisions["pm_absolute_innovation_5s"] = (
            decisions["pm_signed_innovation_5s"].abs()
        )
        decisions["sig_spread"] = decisions["sig_best_ask"] - decisions["sig_best_bid"]
        decisions["sig_scalar_trusted"] = 1.0
        decisions["regime_pre_election"] = 1.0
        decisions["target_fixed_change"] = decisions["target_sig_mid"] - decisions["sig_mid"]
        decisions["target_next_change"] = decisions["next_sig_mid"] - decisions["sig_mid"]
        decisions["sig_exchange_id"] = mapping.sig_exchange_id
        decisions["pm_token_id"] = mapping.pm_token_id
        decisions["sig_market_id"] = mapping.sig_market_id

        depth_group = depth[depth["token_id"] == mapping.pm_token_id].copy()
        if not depth_group.empty:
            depth_join = pd.merge_asof(
                decisions[["decision_at"]].sort_values("decision_at"),
                depth_group[
                    ["recorded_at", "pm_bid_size", "pm_ask_size"]
                ].sort_values("recorded_at"),
                left_on="decision_at",
                right_on="recorded_at",
                direction="backward",
            )
            decisions["pm_bid_size"] = depth_join["pm_bid_size"].to_numpy()
            decisions["pm_ask_size"] = depth_join["pm_ask_size"].to_numpy()
            decisions["pm_depth_age_seconds"] = (
                decisions["decision_at"] - depth_join["recorded_at"]
            ).dt.total_seconds().to_numpy()
            decisions["pm_depth_available"] = (
                decisions["pm_depth_age_seconds"].le(90)
                & decisions["pm_bid_size"].notna()
                & decisions["pm_ask_size"].notna()
            ).astype(float)
            stale = decisions["pm_depth_available"].eq(0)
            decisions.loc[stale, ["pm_bid_size", "pm_ask_size", "pm_depth_age_seconds"]] = np.nan
        else:
            decisions["pm_bid_size"] = np.nan
            decisions["pm_ask_size"] = np.nan
            decisions["pm_depth_age_seconds"] = np.nan
            decisions["pm_depth_available"] = 0.0

        required = [
            "sig_best_bid",
            "sig_best_ask",
            "sig_mid",
            "sig_quote_age_seconds",
            "sig_mid_30s_ago",
            "pm_mid",
            "pm_mid_5s_ago",
            "target_sig_bid",
            "target_sig_ask",
            "target_sig_mid",
            "next_sig_mid",
            "entry_bid",
            "entry_ask",
            "stress_entry_bid",
            "stress_entry_ask",
        ]
        decisions = decisions.dropna(subset=required)
        decisions = decisions[decisions["sig_quote_age_seconds"] <= sig_age_limit]
        rows.append(decisions)

    if not rows:
        return pd.DataFrame()
    exact_rows = pd.concat(rows, ignore_index=True)
    return exact_rows.sort_values(["decision_at", "sig_exchange_id"]).reset_index(drop=True)


def design_matrices(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    contract_ids: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    train_numeric = train[features].astype(float).copy()
    test_numeric = test[features].astype(float).copy()
    medians = train_numeric.median(axis=0, skipna=True).fillna(0.0)
    train_numeric = train_numeric.fillna(medians)
    test_numeric = test_numeric.fillna(medians)

    q25 = train_numeric.quantile(0.25)
    q75 = train_numeric.quantile(0.75)
    scale = (q75 - q25).replace(0.0, 1.0).fillna(1.0)
    center = train_numeric.median()
    x_train = ((train_numeric - center) / scale).to_numpy(dtype=float)
    x_test = ((test_numeric - center) / scale).to_numpy(dtype=float)

    for contract in contract_ids[:-1]:
        x_train = np.column_stack(
            (x_train, (train["sig_exchange_id"] == contract).astype(float).to_numpy())
        )
        x_test = np.column_stack(
            (x_test, (test["sig_exchange_id"] == contract).astype(float).to_numpy())
        )
    return x_train, x_test


def ridge_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    features: list[str],
    contract_ids: list[str],
    alpha: float,
) -> np.ndarray:
    x_train, x_test = design_matrices(train, test, features, contract_ids)
    y_train = train["target_fixed_change"].to_numpy(dtype=float)
    train_design = np.column_stack((np.ones(len(x_train)), x_train))
    test_design = np.column_stack((np.ones(len(x_test)), x_test))
    penalty = np.eye(train_design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    beta = np.linalg.pinv(train_design.T @ train_design + penalty) @ train_design.T @ y_train
    return np.asarray(test_design @ beta, dtype=float)


def build_holdout_predictions(
    rows: pd.DataFrame,
    prereg: dict[str, object],
    contract_ids: list[str],
) -> pd.DataFrame:
    cfg = prereg["historical_discovery"]["walk_forward"]
    start = rows["decision_at"].min()
    snapshot_end = rows["decision_at"].max()
    training = pd.Timedelta(minutes=int(cfg["training_minutes"]))
    development = pd.Timedelta(minutes=int(cfg["development_minutes"]))
    holdout = pd.Timedelta(minutes=int(cfg["holdout_minutes"]))
    step = pd.Timedelta(minutes=int(cfg["step_minutes"]))
    embargo = pd.Timedelta(seconds=int(cfg["embargo_seconds"]))
    alpha = float(prereg["model"]["ridge_alpha"])

    outputs: list[pd.DataFrame] = []
    fold = 0
    while True:
        train_end = start + training + step * fold
        hold_start = train_end + development
        hold_end = hold_start + holdout
        if hold_end > snapshot_end:
            break
        fit = rows[
            (rows["decision_at"] < hold_start - embargo)
        ].copy()
        test = rows[
            (rows["decision_at"] >= hold_start)
            & (rows["decision_at"] < hold_end)
        ].copy()
        if len(fit) < 100 or test.empty:
            fold += 1
            continue
        test["baseline_prediction"] = ridge_predict(
            fit,
            test,
            features=BASELINE_FEATURES,
            contract_ids=contract_ids,
            alpha=alpha,
        )
        test["challenger_prediction"] = ridge_predict(
            fit,
            test,
            features=CHALLENGER_FEATURES,
            contract_ids=contract_ids,
            alpha=alpha,
        )
        test["fold"] = fold
        test["absolute_error_improvement"] = (
            (test["target_fixed_change"] - test["baseline_prediction"]).abs()
            - (test["target_fixed_change"] - test["challenger_prediction"]).abs()
        )
        test["direction_correct_baseline"] = (
            np.sign(test["baseline_prediction"]) == np.sign(test["target_fixed_change"])
        )
        test["direction_correct_challenger"] = (
            np.sign(test["challenger_prediction"]) == np.sign(test["target_fixed_change"])
        )
        direction = np.sign(test["challenger_prediction"])
        test["markout_250ms"] = np.where(
            direction > 0,
            test["target_sig_bid"] - test["entry_ask"],
            np.where(direction < 0, test["entry_bid"] - test["target_sig_ask"], 0.0),
        )
        test["markout_1s"] = np.where(
            direction > 0,
            test["target_sig_bid"] - test["stress_entry_ask"],
            np.where(direction < 0, test["stress_entry_bid"] - test["target_sig_ask"], 0.0),
        )
        outputs.append(test)
        fold += 1

    if not outputs:
        return pd.DataFrame()
    return pd.concat(outputs, ignore_index=True)


manifest = json.loads((INPUT / "snapshot_manifest.json").read_text())
prereg = json.loads((INPUT / "preregistration.json").read_text())
mapping = json.loads((INPUT / "sig_polymarket_2026.json").read_text())

assert sha256(INPUT / "sig_polymarket_2026.json") == manifest["mapping_sha256"]
assert sha256(INPUT / "preregistration.json") == manifest["preregistration_sha256"]
if "implementation_freeze_sha256" in manifest:
    assert (
        sha256(INPUT / "implementation_freeze_001.json")
        == manifest["implementation_freeze_sha256"]
    )
for name, metadata in manifest["files"].items():
    assert sha256(INPUT / name) == metadata["sha256"]

exact = [
    record
    for record in mapping["records"]
    if record["mapping_class"] == "EXACT"
    and record["status"] == "VERIFIED"
    and record["mapping_direction"] == "SAME"
]
assert len(exact) == 140
map_frame = pd.DataFrame(
    {
        "sig_exchange_id": [str(record["sig_exchange_id"]) for record in exact],
        "pm_token_id": [str(record["direct_polymarket"]["mapped_token_id"]) for record in exact],
        "sig_market_id": [str(record["sig_market_id"]) for record in exact],
    }
)
contract_ids = sorted(map_frame["sig_exchange_id"].tolist())

sig = pd.read_parquet(INPUT / "sig_prices.parquet")
pm_obs = pd.read_parquet(INPUT / "pm_observations.parquet")
pm_changes = pd.read_parquet(INPUT / "pm_book_changes.parquet")
depth = pd.read_parquet(INPUT / "pm_depth_snapshots.parquet")

sig["exchange_id"] = sig["exchange_id"].astype(str)
pm_obs["token_id"] = pm_obs["token_id"].astype(str)
pm_changes["token_id"] = pm_changes["token_id"].astype(str)
depth["token_id"] = depth["token_id"].astype(str)
sig["rest_observed_at"] = as_utc_ns(sig["rest_observed_at"])
pm_obs["observed_at"] = as_utc_ns(pm_obs["observed_at"])
pm_changes["observed_at"] = as_utc_ns(pm_changes["observed_at"])
depth = prepare_depth(depth)

sig = sig.merge(map_frame, left_on="exchange_id", right_on="sig_exchange_id", how="inner")
pm_obs = pm_obs.merge(map_frame, left_on="token_id", right_on="pm_token_id", how="inner")
pm_changes = pm_changes.merge(map_frame, left_on="token_id", right_on="pm_token_id", how="inner")

sig_ids = set(sig["exchange_id"].unique())
pm_obs_ids = set(pm_obs["token_id"].unique())
pm_change_ids = set(pm_changes["token_id"].unique())

sig_cadences: list[float] = []
for _, group in sig.sort_values("rest_observed_at").groupby("exchange_id"):
    delta = group["rest_observed_at"].drop_duplicates().sort_values().diff().dt.total_seconds()
    sig_cadences.extend(delta.dropna().tolist())

pm_cadences: list[float] = []
for _, group in pm_obs.sort_values("observed_at").groupby("token_id"):
    delta = group["observed_at"].drop_duplicates().sort_values().diff().dt.total_seconds()
    pm_cadences.extend(delta.dropna().tolist())

exact_rows = build_exact_rows(sig, pm_changes, depth, map_frame, prereg)
exact_rows.to_parquet(OUTPUT / "exact_asof_rows.parquet", index=False, compression="zstd")

coverage = map_frame.copy()
coverage["sig_observed"] = coverage["sig_exchange_id"].isin(sig_ids)
coverage["pm_panel_observed"] = coverage["pm_token_id"].isin(pm_obs_ids)
coverage["pm_change_observed"] = coverage["pm_token_id"].isin(pm_change_ids)
if not exact_rows.empty:
    decision_counts = exact_rows.groupby("sig_exchange_id").size().rename("evaluable_decisions")
    coverage = coverage.merge(decision_counts, on="sig_exchange_id", how="left")
else:
    coverage["evaluable_decisions"] = 0
coverage["evaluable_decisions"] = coverage["evaluable_decisions"].fillna(0).astype(int)
coverage.to_csv(OUTPUT / "coverage_exact.csv", index=False)

snapshot_start = pd.Timestamp(manifest["snapshot_start_utc"])
snapshot_cutoff = pd.Timestamp(manifest["snapshot_cutoff_utc"])
duration_minutes = (snapshot_cutoff - snapshot_start).total_seconds() / 60.0
required_minutes = (
    prereg["historical_discovery"]["walk_forward"]["training_minutes"]
    + prereg["historical_discovery"]["walk_forward"]["development_minutes"]
    + prereg["historical_discovery"]["walk_forward"]["holdout_minutes"]
)
min_contracts = prereg["exact_statistics"]["minimum_forward_coverage_for_promotion"][
    "valid_exact_contracts"
]
min_decisions = prereg["exact_statistics"]["minimum_forward_coverage_for_promotion"][
    "valid_oos_decisions"
]
joint_contracts = int(
    (coverage["sig_observed"] & coverage["pm_panel_observed"]).sum()
)

sufficient = bool(
    duration_minutes >= required_minutes
    and joint_contracts >= min_contracts
    and len(exact_rows) >= min_decisions
)

audit = {
    "schema_version": 2,
    "experiment_id": "EXPERIMENT-004C-C",
    "namespace": "004c_c_crossvenue",
    "run_kind": "PHASE0_FORWARD_SUFFICIENCY_AUDIT",
    "source_snapshot_manifest": manifest,
    "exact_frozen_contracts": 140,
    "sig_exact_contracts_observed": int(coverage["sig_observed"].sum()),
    "pm_exact_tokens_panel_observed": int(coverage["pm_panel_observed"].sum()),
    "pm_exact_tokens_with_book_changes": int(coverage["pm_change_observed"].sum()),
    "joint_exact_contracts": joint_contracts,
    "evaluable_asof_decisions": int(len(exact_rows)),
    "snapshot_duration_minutes": duration_minutes,
    "minimum_walk_forward_minutes": required_minutes,
    "sig_observed_cadence_seconds": quantiles(pd.Series(sig_cadences, dtype=float)),
    "pm_panel_observed_cadence_seconds": quantiles(pd.Series(pm_cadences, dtype=float)),
    "sig_quote_age_at_pm_innovation_seconds": quantiles(
        exact_rows["sig_quote_age_seconds"] if not exact_rows.empty else pd.Series(dtype=float)
    ),
    "causal_clock": "local_observed_at",
    "source_timestamps_used_for_ordering": False,
    "predictive_model_fit": sufficient,
    "status": (
        "READY_FOR_FROZEN_WALK_FORWARD"
        if sufficient
        else "INCONCLUSIVE_INSUFFICIENT_FORWARD_WINDOW"
    ),
}
(OUTPUT / "phase0_audit.json").write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")

if sufficient:
    predictions = build_holdout_predictions(exact_rows, prereg, contract_ids)
    predictions.to_parquet(
        OUTPUT / "exact_holdout_predictions.parquet",
        index=False,
        compression="zstd",
    )
    if predictions.empty:
        audit["predictive_model_fit"] = False
        audit["status"] = "INCONCLUSIVE_NO_VALID_HOLDOUT_FOLD"
    else:
        contract = predictions.groupby("sig_exchange_id").agg(
            oos_rows=("absolute_error_improvement", "size"),
            mean_ae_improvement=("absolute_error_improvement", "mean"),
            mean_markout_250ms=("markout_250ms", "mean"),
            mean_markout_1s=("markout_1s", "mean"),
            baseline_direction_accuracy=("direction_correct_baseline", "mean"),
            challenger_direction_accuracy=("direction_correct_challenger", "mean"),
        )
        contract.to_csv(OUTPUT / "exact_contract_summary_unadjusted.csv")
        aggregate = {
            "oos_rows": int(len(predictions)),
            "oos_contracts": int(predictions["sig_exchange_id"].nunique()),
            "equal_contract_mean_ae_improvement": float(
                contract["mean_ae_improvement"].mean()
            ),
            "equal_contract_mean_markout_250ms": float(
                contract["mean_markout_250ms"].mean()
            ),
            "equal_contract_mean_markout_1s": float(
                contract["mean_markout_1s"].mean()
            ),
        }
        (OUTPUT / "exact_aggregate_unadjusted.json").write_text(
            json.dumps(aggregate, sort_keys=True, indent=2) + "\n"
        )

(OUTPUT / "phase0_audit.json").write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")
print(json.dumps(audit, sort_keys=True, indent=2))