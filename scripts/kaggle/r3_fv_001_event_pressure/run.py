# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/r3_fv_001_event_pressure")
OUT.mkdir(parents=True, exist_ok=True)
RNG_SEED = 20260930
BOOTSTRAPS = 2000


def locate(name: str) -> Path:
    matches = list(Path("/kaggle/input").rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name}, got {matches}")
    return matches[0]


def source_files() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    hp = locate("HOUSE_COUNT_EVENTS.csv")
    sp = locate("SENATE_COUNT_EVENTS.csv")
    rp = locate("SEAT_COUNT_RESULTS.json")
    house = pd.read_csv(hp)
    senate = pd.read_csv(sp)
    result = json.loads(rp.read_text())
    if result.get("final_opened") is not False:
        raise RuntimeError("parent seat-count output says FINAL opened")
    if result.get("final_rows_accessed") != 0:
        raise RuntimeError("parent seat-count output accessed FINAL rows")
    for name, df in (("house", house), ("senate", senate)):
        bad = set(df["split"].dropna().astype(str)) - {"TRAIN", "DEV"}
        if bad:
            raise RuntimeError(f"{name} has unexpected split labels {bad}")
    return house, senate, result


def consecutive_episode_first(df: pd.DataFrame) -> pd.DataFrame:
    x = df.sort_values(["ts", "block_number"]).copy()
    x["_episode"] = (
        x["source_latest_block"].astype(str)
        != x["source_latest_block"].astype(str).shift()
    ).cumsum()
    ep = x.groupby("_episode", as_index=False, sort=False).first()
    ep["next_y_episode"] = ep["y"].shift(-1)
    ep["next_ts_episode"] = ep["ts"].shift(-1)
    ep["next_split_episode"] = ep["split"].shift(-1)
    bad = ep["next_split_episode"] != ep["split"]
    ep.loc[bad, ["next_y_episode", "next_ts_episode"]] = np.nan
    ep["episode_dy"] = ep["next_y_episode"] - ep["y"]
    ep["episode_dt_s"] = ep["next_ts_episode"] - ep["ts"]
    ep["target_prev_change"] = ep["y"].diff()
    return ep


def house_episodes(house: pd.DataFrame) -> pd.DataFrame:
    ep = consecutive_episode_first(house)
    ep["fv_qp"] = ep["qp_maxent"]
    ep["fv_kl"] = ep["kl_maxent"]
    ep["fv_mean"] = 0.5 * (ep["fv_qp"] + ep["fv_kl"])
    ep["source_change"] = ep["fv_mean"].diff()
    ep["unabsorbed_pressure"] = ep["source_change"] - ep["target_prev_change"]
    ep["level_residual"] = ep["fv_mean"] - ep["y"]
    ep["method_disagreement"] = np.abs(ep["fv_qp"] - ep["fv_kl"])
    # Wider union is deliberately conservative: only signal a violation when
    # target is outside both reconciliation variants.
    ep["bound_lower"] = np.minimum(ep["qp_lower"], ep["kl_lower"])
    ep["bound_upper"] = np.maximum(ep["qp_upper"], ep["kl_upper"])
    ep["bound_width"] = ep["bound_upper"] - ep["bound_lower"]
    ep["interval_violation"] = np.where(
        ep["y"] < ep["bound_lower"],
        ep["bound_lower"] - ep["y"],
        np.where(
            ep["y"] > ep["bound_upper"],
            ep["bound_upper"] - ep["y"],
            0.0,
        ),
    )
    ep["family"] = "HOUSE"
    return ep


def senate_episodes(senate: pd.DataFrame) -> pd.DataFrame:
    ep = consecutive_episode_first(senate)
    for threshold in ("t50", "t51"):
        ep[f"fv_{threshold}"] = 0.5 * (
            ep[f"qp_{threshold}"] + ep[f"kl_{threshold}"]
        )
        ep[f"source_change_{threshold}"] = ep[f"fv_{threshold}"].diff()
        ep[f"unabsorbed_pressure_{threshold}"] = (
            ep[f"source_change_{threshold}"] - ep["target_prev_change"]
        )
        ep[f"level_residual_{threshold}"] = ep[f"fv_{threshold}"] - ep["y"]

    ep["method_disagreement_t50"] = np.abs(ep["qp_t50"] - ep["kl_t50"])
    ep["method_disagreement_t51"] = np.abs(ep["qp_t51"] - ep["kl_t51"])

    # Treat unresolved T50/T51 semantics as an interval rather than selecting
    # the better DEV threshold. This is the safest semantics-agnostic object.
    ep["semantic_lower"] = np.minimum(ep["qp_t51"], ep["kl_t51"])
    ep["semantic_upper"] = np.maximum(ep["qp_t50"], ep["kl_t50"])
    ep["semantic_width"] = ep["semantic_upper"] - ep["semantic_lower"]
    ep["semantic_interval_violation"] = np.where(
        ep["y"] < ep["semantic_lower"],
        ep["semantic_lower"] - ep["y"],
        np.where(
            ep["y"] > ep["semantic_upper"],
            ep["semantic_upper"] - ep["y"],
            0.0,
        ),
    )
    ep["family"] = "SENATE"
    return ep


def clustered_bootstrap(frame: pd.DataFrame, value_col: str, seed: int) -> dict:
    f = frame[["ts", value_col]].dropna().copy()
    if f.empty:
        return {"status": "NO_ROWS"}
    f["day"] = pd.to_datetime(f["ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    g = f.groupby("day")[value_col].agg(["sum", "count"])
    if len(g) < 2:
        return {"status": "INSUFFICIENT_DAY_CLUSTERS", "clusters": int(len(g))}
    sums = g["sum"].to_numpy(float)
    counts = g["count"].to_numpy(float)
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAPS)
    for i in range(BOOTSTRAPS):
        idx = rng.integers(0, len(g), size=len(g))
        draws[i] = sums[idx].sum() / counts[idx].sum()
    return {
        "status": "OK",
        "clusters": int(len(g)),
        "resamples": BOOTSTRAPS,
        "lower_95": float(np.quantile(draws, 0.025)),
        "median": float(np.quantile(draws, 0.5)),
        "upper_95": float(np.quantile(draws, 0.975)),
    }


def eval_signal(
    ep: pd.DataFrame,
    signal_col: str,
    split_name: str,
    max_horizon_s: int,
    abs_threshold: float,
    seed: int,
) -> dict:
    f = ep[
        (ep["split"] == split_name)
        & ep["episode_dy"].notna()
        & ep["episode_dt_s"].notna()
        & (ep["episode_dt_s"] > 0)
        & (ep["episode_dt_s"] <= max_horizon_s)
        & ep[signal_col].notna()
    ].copy()
    f = f[np.abs(f[signal_col]) >= float(abs_threshold)].copy()
    if f.empty:
        return {"status": "NO_ROWS", "rows": 0}
    f["signal_sign"] = np.sign(f[signal_col])
    f = f[f["signal_sign"] != 0].copy()
    if f.empty:
        return {"status": "NO_ACTIVE_SIGNALS", "rows": 0}

    f["signed_move"] = f["signal_sign"] * f["episode_dy"]
    f["reversal_sign"] = np.sign(-f["target_prev_change"])
    f["reversal_signed_move"] = f["reversal_sign"] * f["episode_dy"]
    f["increment_vs_reversal"] = f["signed_move"] - f["reversal_signed_move"]
    nz = f["episode_dy"] != 0

    return {
        "status": "OK",
        "rows": int(len(f)),
        "nonzero_outcome_rows": int(nz.sum()),
        "mean_signed_move": float(f["signed_move"].mean()),
        "median_signed_move": float(f["signed_move"].median()),
        "mean_absolute_target_move": float(np.abs(f["episode_dy"]).mean()),
        "direction_accuracy_nonzero": (
            float(
                np.mean(
                    f.loc[nz, "signal_sign"]
                    == np.sign(f.loc[nz, "episode_dy"])
                )
            )
            if nz.any()
            else None
        ),
        "mean_episode_seconds": float(f["episode_dt_s"].mean()),
        "mean_target_reversal_signed_move": float(
            f["reversal_signed_move"].mean()
        ),
        "incremental_mean_vs_target_reversal": float(
            f["increment_vs_reversal"].mean()
        ),
        "signed_move_bootstrap_day": clustered_bootstrap(
            f, "signed_move", seed
        ),
        "increment_vs_reversal_bootstrap_day": clustered_bootstrap(
            f, "increment_vs_reversal", seed + 10000
        ),
    }


def threshold_from_train(
    ep: pd.DataFrame,
    signal_col: str,
    q: float,
    max_horizon_s: int,
) -> float:
    f = ep[
        (ep["split"] == "TRAIN")
        & ep["episode_dy"].notna()
        & (ep["episode_dt_s"] > 0)
        & (ep["episode_dt_s"] <= max_horizon_s)
        & ep[signal_col].notna()
    ]
    if f.empty:
        return float("inf")
    return float(np.quantile(np.abs(f[signal_col]), q))


def evaluate_family(
    ep: pd.DataFrame,
    family: str,
    signals: list[str],
) -> dict:
    result = {}
    for horizon_h in (24, 6, 72):
        max_s = horizon_h * 3600
        horizon_key = f"{horizon_h}h"
        result[horizon_key] = {}
        for si, signal in enumerate(signals):
            result[horizon_key][signal] = {}
            for q in (0.0, 0.5, 0.75):
                threshold = threshold_from_train(ep, signal, q, max_s)
                key = f"train_abs_q{int(q*100):02d}"
                result[horizon_key][signal][key] = {
                    "train_abs_threshold": threshold,
                    "TRAIN": eval_signal(
                        ep,
                        signal,
                        "TRAIN",
                        max_s,
                        threshold,
                        RNG_SEED + si * 100 + horizon_h + int(q * 10),
                    ),
                    "DEV": eval_signal(
                        ep,
                        signal,
                        "DEV",
                        max_s,
                        threshold,
                        RNG_SEED + 5000 + si * 100 + horizon_h + int(q * 10),
                    ),
                }
    return result


def summarize_primary(results: dict) -> list[dict]:
    rows = []
    for family, block in results.items():
        primary = block["24h"]
        for signal, thresholds in primary.items():
            for gate, item in thresholds.items():
                tr = item["TRAIN"]
                dv = item["DEV"]
                rows.append(
                    {
                        "family": family,
                        "signal": signal,
                        "gate": gate,
                        "threshold": item["train_abs_threshold"],
                        "train_rows": tr.get("rows", 0),
                        "train_signed_move": tr.get("mean_signed_move"),
                        "train_increment_vs_reversal": tr.get(
                            "incremental_mean_vs_target_reversal"
                        ),
                        "dev_rows": dv.get("rows", 0),
                        "dev_signed_move": dv.get("mean_signed_move"),
                        "dev_increment_vs_reversal": dv.get(
                            "incremental_mean_vs_target_reversal"
                        ),
                        "dev_direction_accuracy_nonzero": dv.get(
                            "direction_accuracy_nonzero"
                        ),
                        "dev_day_bootstrap_lower": (
                            dv.get("signed_move_bootstrap_day", {}).get("lower_95")
                        ),
                        "dev_increment_bootstrap_lower": (
                            dv.get(
                                "increment_vs_reversal_bootstrap_day", {}
                            ).get("lower_95")
                        ),
                    }
                )
    return rows


def main() -> None:
    house, senate, parent = source_files()
    he = house_episodes(house)
    se = senate_episodes(senate)

    signals = {
        "HOUSE": [
            "source_change",
            "unabsorbed_pressure",
            "level_residual",
            "interval_violation",
        ],
        "SENATE": [
            "source_change_t50",
            "unabsorbed_pressure_t50",
            "level_residual_t50",
            "source_change_t51",
            "unabsorbed_pressure_t51",
            "level_residual_t51",
            "semantic_interval_violation",
        ],
    }
    results = {
        "HOUSE": evaluate_family(he, "HOUSE", signals["HOUSE"]),
        "SENATE": evaluate_family(se, "SENATE", signals["SENATE"]),
    }
    summary_rows = summarize_primary(results)
    summary = pd.DataFrame(summary_rows).sort_values(
        ["family", "signal", "gate"]
    )

    output = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001M-EVENT",
        "status": "POST_HOC_TRAIN_DEV_EXPLORATION_ONLY",
        "data_binding": {
            "target": "DATA-003 via frozen parent seat-count outputs",
            "source": "DATA-004 P0 via frozen parent seat-count outputs",
            "data001_used": False,
        },
        "final_opened": False,
        "final_rows_accessed": 0,
        "parent_final_opened": parent["final_opened"],
        "unit": "distinct source_latest_block episode",
        "primary_max_episode_horizon_hours": 24,
        "sensitivity_horizons_hours": [6, 72],
        "house_episode_counts": {
            "TRAIN": int((he["split"] == "TRAIN").sum()),
            "DEV": int((he["split"] == "DEV").sum()),
        },
        "senate_episode_counts": {
            "TRAIN": int((se["split"] == "TRAIN").sum()),
            "DEV": int((se["split"] == "DEV").sum()),
        },
        "interpretation_rule": (
            "A structural method is interesting only if it has positive TRAIN "
            "and DEV signed move and, more importantly, adds positive signed "
            "move versus the target-only reversal baseline. Small DEV episode "
            "counts remain exploratory regardless of point estimates."
        ),
        "senate_semantics_rule": (
            "T50 and T51 remain separate frozen variants. DEV performance "
            "cannot choose the settlement interpretation."
        ),
        "results": results,
    }

    (OUT / "EVENT_PRESSURE_RESULTS.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n"
    )
    summary.to_csv(OUT / "EVENT_PRESSURE_RESULTS.csv", index=False)

    keep_house = [
        "family", "split", "source_latest_block", "block_number", "ts", "y",
        "next_y_episode", "episode_dy", "episode_dt_s", "target_prev_change",
        "fv_mean", "source_change", "unabsorbed_pressure", "level_residual",
        "bound_lower", "bound_upper", "interval_violation",
        "method_disagreement",
    ]
    he[keep_house].to_csv(OUT / "EVENT_EPISODES_HOUSE.csv", index=False)

    keep_senate = [
        "family", "split", "source_latest_block", "block_number", "ts", "y",
        "next_y_episode", "episode_dy", "episode_dt_s", "target_prev_change",
        "fv_t50", "fv_t51", "source_change_t50", "source_change_t51",
        "unabsorbed_pressure_t50", "unabsorbed_pressure_t51",
        "level_residual_t50", "level_residual_t51",
        "semantic_lower", "semantic_upper", "semantic_interval_violation",
        "method_disagreement_t50", "method_disagreement_t51",
    ]
    se[keep_senate].to_csv(OUT / "EVENT_EPISODES_SENATE.csv", index=False)

    # Compact evidence-based handoff, deliberately avoiding promotion language.
    candidates = summary[
        (summary["train_signed_move"].fillna(-1) > 0)
        & (summary["dev_signed_move"].fillna(-1) > 0)
    ].copy()
    handoff = [
        "# R3-FV-001M Event-Pressure Handoff",
        "",
        "**R3 FINAL accessed:** NO",
        "",
        "This is post-hoc TRAIN/DEV exploration, not confirmation.",
        "",
        "The unit is one distinct structural source-state episode. Repeated target "
        "updates against the same source state are intentionally collapsed.",
        "",
        "A continuous structural fair value was not assumed. The tested mechanism "
        "is whether a source-surface impulse, residual, or interval violation "
        "predicts the next target move before the structural state changes again.",
        "",
        f"House episodes: TRAIN={int((he['split']=='TRAIN').sum())}, "
        f"DEV={int((he['split']=='DEV').sum())}.",
        f"Senate episodes: TRAIN={int((se['split']=='TRAIN').sum())}, "
        f"DEV={int((se['split']=='DEV').sum())}.",
        "",
        "Any apparent signal must beat the target-only reversal baseline on the "
        "same episodes. Senate T50/T51 semantics remain unresolved and are not "
        "selected using DEV.",
        "",
        "Rows with positive TRAIN and DEV signed moves:",
        "",
        candidates.to_csv(index=False),
    ]
    (OUT / "EVENT_PRESSURE_HANDOFF.md").write_text("\n".join(handoff))

    print(
        "R3M_EVENT_RESULT="
        + json.dumps(
            {
                "status": "COMPLETE",
                "final_opened": False,
                "data001_used": False,
                "house_train_episodes": int((he["split"] == "TRAIN").sum()),
                "house_dev_episodes": int((he["split"] == "DEV").sum()),
                "senate_train_episodes": int((se["split"] == "TRAIN").sum()),
                "senate_dev_episodes": int((se["split"] == "DEV").sum()),
                "positive_train_dev_rows": int(len(candidates)),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
