# ruff: noqa
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

OUT = Path("/kaggle/working/r3_fv_001_wave_factor")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"
TARGETS = {"153": -1.0, "154": 1.0}
COUNT_IDS = [str(x) for x in range(2773660, 2773667)]
CORE4_ID = "1178882"
BT_ANY_ID = "2364515"
SOURCE_IDS = set(COUNT_IDS + [CORE4_ID, BT_ANY_ID])
MAX_AGE_S = 86400
BOOTSTRAPS = 2000
RNG_SEED = 20260930


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def idstr(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def locate(name: str, fragment: str) -> Path:
    matches = [p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} under {fragment}, got {matches}")
    return matches[0]


def locate_data004_root() -> Path:
    roots = sorted({
        p.parent
        for p in Path("/kaggle/input").rglob("MANIFEST.json")
        if DATA004_SLUG in str(p.parent)
    })
    if len(roots) != 1:
        raise RuntimeError(f"expected one DATA-004 root, got {roots}")
    return roots[0]


def load_split() -> dict:
    p = locate("R3_SPLIT_MANIFEST.json", PREPARE_FRAGMENT)
    split = json.loads(p.read_text())
    if split.get("split_status") != "FROZEN_BEFORE_PERFORMANCE":
        raise RuntimeError("R3 split not frozen")
    if split.get("final_opened") is not False:
        raise RuntimeError("R3 FINAL already opened")
    return split


def split_name(ts: int, split: dict) -> str | None:
    ds = int(split["dev_start_epoch"])
    fs = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])
    if ts < ds - purge:
        return "TRAIN"
    if ds <= ts < fs - purge:
        return "DEV"
    if ts >= fs:
        raise RuntimeError("FINAL target row reached wave-factor discovery")
    return None


def load_direct(final_start: int) -> pd.DataFrame:
    econ = locate("economic_fills_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    txb = locate("tx_block_DATA003.parquet", BLOCK_GATE_FRAGMENT)
    bts = locate("block_timestamp.parquet", BLOCK_GATE_FRAGMENT)
    gate = json.loads(
        locate("data003_block_gate_report.json", BLOCK_GATE_FRAGMENT).read_text()
    )
    if gate.get("all_hard_gates_pass") is not True:
        raise RuntimeError("DATA-003 block gate failed")
    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        with x as (
          select
            cast(e.timestamp as bigint) ts,
            cast(t.block_number as bigint) block_number,
            cast(e.log_index as bigint) log_index,
            cast(e.sig_market_id as varchar) sig_market_id,
            upper(cast(e.mapping_class as varchar)) mapping_class,
            case
              when upper(cast(e.mapping_direction as varchar))='SAME'
                then cast(e.p_yes as double)
              when upper(cast(e.mapping_direction as varchar))='COMPLEMENT'
                then 1.0-cast(e.p_yes as double)
              else null
            end p
          from read_parquet('{q(econ)}') e
          join read_parquet('{q(txb)}') t
            on lower(cast(e.tx_hash as varchar))=t.tx_hash
          join read_parquet('{q(bts)}') b using(block_number)
          where cast(e.sig_market_id as varchar) in ('153','154')
            and upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
            and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT')
            and cast(e.timestamp as bigint) < {int(final_start)}
        )
        select block_number,max(ts) ts,sig_market_id,
               arg_max(p,log_index) y,arg_max(mapping_class,log_index) mapping_class
        from x
        where p between 0 and 1
        group by 1,3
        order by 1,3
        """
    ).fetchdf()
    con.close()
    df["sig_market_id"] = df["sig_market_id"].map(idstr)
    if (df["ts"] >= final_start).any():
        raise RuntimeError("FINAL direct rows loaded")
    return df


def load_sources(root: Path, final_start: int) -> pd.DataFrame:
    files = sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 parquet")
    sql = ",".join("'" + q(p) + "'" for p in files)
    wanted = ",".join("'" + x + "'" for x in sorted(SOURCE_IDS))
    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        select
          cast(block_number as bigint) block_number,
          cast(log_index as bigint) log_index,
          cast("timestamp" as bigint) ts,
          cast(market_id as varchar) market_id,
          upper(cast(acquisition_tier as varchar)) acquisition_tier,
          case
            when upper(cast(outcome_label as varchar))='YES'
              then cast(price as double)
            when upper(cast(outcome_label as varchar))='NO'
              then 1.0-cast(price as double)
            else null
          end p_yes
        from read_parquet([{sql}],union_by_name=true)
        where upper(cast(order_role as varchar))='TAKER'
          and upper(cast(acquisition_tier as varchar))='P0'
          and cast(price as double) between 0 and 1
          and cast(market_id as varchar) in ({wanted})
          and cast("timestamp" as bigint) < {int(final_start)}
        order by block_number,log_index
        """
    ).fetchdf()
    con.close()
    df["market_id"] = df["market_id"].map(idstr)
    df = df[df["p_yes"].notna()].copy()
    if set(df["market_id"]) - SOURCE_IDS:
        raise RuntimeError("unexpected P0 source ids")
    if set(df["acquisition_tier"]) != {"P0"}:
        raise RuntimeError("non-P0 source entered")
    if df.duplicated(["block_number", "log_index"]).any():
        raise RuntimeError("duplicate DATA-004 block/log rows")
    return df


class Lookup:
    def __init__(self, source: pd.DataFrame):
        self.by_market = {}
        for mid, g in source.groupby("market_id", sort=False):
            z = g.sort_values(["block_number", "log_index"])
            self.by_market[str(mid)] = (
                z["block_number"].to_numpy(np.int64),
                z["ts"].to_numpy(np.int64),
                z["p_yes"].to_numpy(float),
            )

    def before(self, mid: str, block: int, target_ts: int):
        item = self.by_market.get(str(mid))
        if item is None:
            return None
        blocks, ts, p = item
        idx = int(np.searchsorted(blocks, int(block), side="left") - 1)
        if idx < 0:
            return None
        age = int(target_ts) - int(ts[idx])
        if age < 0:
            raise RuntimeError("future source timestamp")
        if age > MAX_AGE_S:
            return None
        return float(p[idx]), int(ts[idx]), int(blocks[idx])


def project_simplex(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, float)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    j = np.arange(1, len(v) + 1)
    keep = u - (cssv - 1.0) / j > 0
    if not np.any(keep):
        return np.full(len(v), 1.0 / len(v))
    rho = int(np.where(keep)[0][-1])
    theta = (cssv[rho] - 1.0) / float(rho + 1)
    return np.maximum(v - theta, 0.0)


def count_state(lookup: Lookup, block: int, ts: int):
    vals = []
    blocks = []
    ages = []
    for mid in COUNT_IDS:
        z = lookup.before(mid, block, ts)
        if z is None:
            return None
        p, sts, sb = z
        vals.append(p)
        blocks.append(sb)
        ages.append(ts - sts)
    qv = project_simplex(np.asarray(vals, float))
    k = np.arange(7, dtype=float)
    mean6 = float(np.dot(k, qv) / 6.0)
    tail3 = float(qv[3:].sum())
    return {
        "COUNT_MEAN6": mean6,
        "COUNT_TAIL3": tail3,
        "count_raw_sum": float(np.sum(vals)),
        "block": int(max(blocks)),
        "age_max_s": int(max(ages)),
    }


def source_state(lookup: Lookup, block: int, ts: int) -> dict | None:
    count = count_state(lookup, block, ts)
    core = lookup.before(CORE4_ID, block, ts)
    bt = lookup.before(BT_ANY_ID, block, ts)
    out = {}

    if count is not None:
        out["COUNT_MEAN6"] = count["COUNT_MEAN6"]
        out["COUNT_TAIL3"] = count["COUNT_TAIL3"]
        out["key__COUNT_MEAN6"] = f"C:{int(count['block'])}"
        out["key__COUNT_TAIL3"] = f"C:{int(count['block'])}"
        out["count_raw_sum"] = count["count_raw_sum"]
        out["count_age_max_s"] = count["age_max_s"]

    if core is not None:
        out["CORE4_BREAK"] = 1.0 - float(core[0])
        out["key__CORE4_BREAK"] = f"CORE:{int(core[2])}"
        out["core_age_s"] = int(ts - core[1])

    if bt is not None:
        out["BT_ANY"] = float(bt[0])
        out["key__BT_ANY"] = f"BT:{int(bt[2])}"
        out["bt_age_s"] = int(ts - bt[1])

    if count is not None and core is not None and bt is not None:
        out["COUNT_PLUS_JOINTS"] = float(
            np.mean([
                out["COUNT_MEAN6"],
                out["CORE4_BREAK"],
                out["BT_ANY"],
            ])
        )
        out["ALL4_EQUAL"] = float(
            np.mean([
                out["COUNT_MEAN6"],
                out["COUNT_TAIL3"],
                out["CORE4_BREAK"],
                out["BT_ANY"],
            ])
        )
        combo_key = (
            f"C:{int(count['block'])}|CORE:{int(core[2])}|BT:{int(bt[2])}"
        )
        out["key__COUNT_PLUS_JOINTS"] = combo_key
        out["key__ALL4_EQUAL"] = combo_key

    return out if any(k in out for k in (
        "COUNT_MEAN6", "COUNT_TAIL3", "CORE4_BREAK", "BT_ANY"
    )) else None


def build_rows(
    direct: pd.DataFrame,
    lookup: Lookup,
    split: dict,
) -> pd.DataFrame:
    rows = []
    for ev in direct.sort_values(["block_number", "sig_market_id"]).itertuples(index=False):
        sp = split_name(int(ev.ts), split)
        if sp is None:
            continue
        st = source_state(lookup, int(ev.block_number), int(ev.ts))
        if st is None:
            continue
        target = idstr(ev.sig_market_id)
        y = float(ev.y) if target == "154" else 1.0 - float(ev.y)
        rows.append(
            {
                "split": sp,
                "sig_market_id": target,
                "block_number": int(ev.block_number),
                "ts": int(ev.ts),
                "oriented_y": y,
                **st,
            }
        )
    return pd.DataFrame(rows)


def episodes_for_target(
    rows: pd.DataFrame,
    target: str,
    factor: str,
) -> pd.DataFrame:
    key_col = f"key__{factor}"
    x = rows[
        (rows["sig_market_id"] == target)
        & rows[factor].notna()
        & rows[key_col].notna()
    ].sort_values(["ts", "block_number"]).copy()
    key = x[key_col].astype(str)
    x["_episode"] = (key != key.shift()).cumsum()
    ep = x.groupby("_episode", as_index=False, sort=False).first()
    ep["next_y"] = ep["oriented_y"].shift(-1)
    ep["next_ts"] = ep["ts"].shift(-1)
    ep["next_split"] = ep["split"].shift(-1)
    bad = ep["next_split"] != ep["split"]
    ep.loc[bad, ["next_y", "next_ts"]] = np.nan
    ep["future_move"] = ep["next_y"] - ep["oriented_y"]
    ep["future_dt_s"] = ep["next_ts"] - ep["ts"]
    ep["target_prev_change"] = ep["oriented_y"].diff()
    return ep


def fit_beta(train: pd.DataFrame, source_change_col: str) -> float | None:
    f = train[[source_change_col, "target_prev_change"]].dropna()
    if len(f) < 20:
        return None
    x = f[source_change_col].to_numpy(float)
    y = f["target_prev_change"].to_numpy(float)
    den = float(np.dot(x, x))
    if den <= 1e-14:
        return None
    return float(np.clip(np.dot(x, y) / den, -5.0, 5.0))


def fit_isotonic(train: pd.DataFrame, level_col: str):
    f = train[[level_col, "oriented_y"]].dropna().drop_duplicates()
    if len(f) < 20 or f[level_col].nunique() < 5:
        return None
    model = IsotonicRegression(
        increasing=True,
        out_of_bounds="clip",
        y_min=0.0,
        y_max=1.0,
    )
    model.fit(f[level_col].to_numpy(float), f["oriented_y"].to_numpy(float))
    return model


def bootstrap(frame: pd.DataFrame, col: str, seed: int) -> dict:
    f = frame[["ts", col]].dropna().copy()
    if f.empty:
        return {"status": "NO_ROWS"}
    f["day"] = pd.to_datetime(f["ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    g = f.groupby("day")[col].agg(["sum", "count"])
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
        "lower_95": float(np.quantile(draws, 0.025)),
        "median": float(np.quantile(draws, 0.5)),
        "upper_95": float(np.quantile(draws, 0.975)),
    }


def eval_signal(
    ep: pd.DataFrame,
    signal_col: str,
    split_name: str,
    threshold: float,
    seed: int,
) -> dict:
    f = ep[
        (ep["split"] == split_name)
        & ep["future_move"].notna()
        & ep["future_dt_s"].notna()
        & (ep["future_dt_s"] > 0)
        & (ep["future_dt_s"] <= MAX_AGE_S)
        & ep[signal_col].notna()
        & (np.abs(ep[signal_col]) >= threshold)
    ].copy()
    if len(f) < 1:
        return {"status": "NO_ROWS", "rows": 0}
    f["sign"] = np.sign(f[signal_col])
    f = f[f["sign"] != 0].copy()
    if f.empty:
        return {"status": "NO_ACTIVE_SIGNAL", "rows": 0}
    f["signed_move"] = f["sign"] * f["future_move"]
    f["reversal_signed_move"] = np.sign(-f["target_prev_change"]) * f["future_move"]
    f["increment_vs_reversal"] = f["signed_move"] - f["reversal_signed_move"]
    nz = f["future_move"] != 0
    return {
        "status": "OK",
        "rows": int(len(f)),
        "nonzero_rows": int(nz.sum()),
        "mean_signed_move": float(f["signed_move"].mean()),
        "direction_accuracy_nonzero": (
            float(np.mean(f.loc[nz, "sign"] == np.sign(f.loc[nz, "future_move"])))
            if nz.any() else None
        ),
        "mean_reversal_signed_move": float(f["reversal_signed_move"].mean()),
        "increment_vs_reversal": float(f["increment_vs_reversal"].mean()),
        "signed_move_bootstrap_day": bootstrap(f, "signed_move", seed),
        "increment_bootstrap_day": bootstrap(f, "increment_vs_reversal", seed + 10000),
    }


def threshold(train: pd.DataFrame, signal: str, quantile: float) -> float:
    f = train[
        train["future_move"].notna()
        & (train["future_dt_s"] > 0)
        & (train["future_dt_s"] <= MAX_AGE_S)
        & train[signal].notna()
    ]
    if f.empty:
        return float("inf")
    return float(np.quantile(np.abs(f[signal]), quantile))


def build_signals(ep: pd.DataFrame, level_col: str) -> tuple[pd.DataFrame, dict]:
    x = ep.copy()
    x["source_change"] = x[level_col].diff()
    train = x[x["split"] == "TRAIN"].copy()
    beta = fit_beta(train, "source_change")
    if beta is not None:
        x["unabsorbed"] = beta * x["source_change"] - x["target_prev_change"]
    else:
        x["unabsorbed"] = np.nan
    iso = fit_isotonic(train, level_col)
    if iso is not None:
        x["iso_level"] = iso.predict(x[level_col].to_numpy(float))
        x["iso_residual"] = x["iso_level"] - x["oriented_y"]
    else:
        x["iso_level"] = np.nan
        x["iso_residual"] = np.nan
    x["source_change_stale"] = x["source_change"].shift(1)
    return x, {
        "beta_train": beta,
        "isotonic_fit": iso is not None,
        "train_rows": int(len(train)),
    }


def evaluate(ep: pd.DataFrame, level_col: str, seed: int) -> dict:
    x, fit = build_signals(ep, level_col)
    signals = ["source_change", "unabsorbed", "iso_residual", "source_change_stale"]
    out = {"fit": fit, "signals": {}}
    tr = x[x["split"] == "TRAIN"]
    for si, signal in enumerate(signals):
        out["signals"][signal] = {}
        for qv in (0.0, 0.5, 0.75):
            th = threshold(tr, signal, qv)
            key = f"train_abs_q{int(qv*100):02d}"
            out["signals"][signal][key] = {
                "threshold": th,
                "TRAIN": eval_signal(x, signal, "TRAIN", th, seed + si * 100 + int(qv*10)),
                "DEV": eval_signal(x, signal, "DEV", th, seed + 5000 + si * 100 + int(qv*10)),
            }
    return out, x


def main() -> None:
    split = load_split()
    final_start = int(split["final_start_epoch"])
    root = locate_data004_root()
    manifest = json.loads((root / "MANIFEST.json").read_text())
    quality = json.loads((root / "QUALITY.json").read_text())
    if manifest.get("status") != "ACCEPTED_V2" or quality.get("all_gates_pass") is not True:
        raise RuntimeError("DATA-004 package not accepted")
    direct = load_direct(final_start)
    source = load_sources(root, final_start)
    lookup = Lookup(source)
    rows = build_rows(direct, lookup, split)
    if rows.empty:
        raise RuntimeError("no wave-factor target rows")

    factors = [
        "COUNT_MEAN6",
        "COUNT_TAIL3",
        "CORE4_BREAK",
        "BT_ANY",
        "COUNT_PLUS_JOINTS",
        "ALL4_EQUAL",
    ]
    results = {}
    episode_frames = []
    compact_rows = []

    for ti, target in enumerate(("153", "154")):
        results[target] = {}
        for fi, factor in enumerate(factors):
            base_ep = episodes_for_target(rows, target, factor)
            res, ep = evaluate(base_ep, factor, RNG_SEED + ti * 1000 + fi * 100)
            results[target][factor] = res
            z = ep.copy()
            z["factor"] = factor
            episode_frames.append(z)
            for signal, gates in res["signals"].items():
                for gate, vals in gates.items():
                    dv = vals["DEV"]
                    tr = vals["TRAIN"]
                    compact_rows.append(
                        {
                            "target": target,
                            "factor": factor,
                            "signal": signal,
                            "gate": gate,
                            "beta_train": res["fit"]["beta_train"],
                            "isotonic_fit": res["fit"]["isotonic_fit"],
                            "threshold": vals["threshold"],
                            "train_rows": tr.get("rows", 0),
                            "train_signed_move": tr.get("mean_signed_move"),
                            "train_increment_vs_reversal": tr.get("increment_vs_reversal"),
                            "dev_rows": dv.get("rows", 0),
                            "dev_signed_move": dv.get("mean_signed_move"),
                            "dev_increment_vs_reversal": dv.get("increment_vs_reversal"),
                            "dev_direction_accuracy_nonzero": dv.get("direction_accuracy_nonzero"),
                            "dev_bootstrap_lower": dv.get("signed_move_bootstrap_day", {}).get("lower_95"),
                            "dev_increment_bootstrap_lower": dv.get("increment_bootstrap_day", {}).get("lower_95"),
                        }
                    )

    compact = pd.DataFrame(compact_rows)
    compact.to_csv(OUT / "WAVE_FACTOR_RESULTS.csv", index=False)
    if episode_frames:
        pd.concat(episode_frames, ignore_index=True).to_csv(
            OUT / "WAVE_FACTOR_EPISODES.csv", index=False
        )

    result = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001M-WAVE",
        "stage": "TRAIN_DEV_DISCOVERY",
        "binding": {
            "target": "DATA-003",
            "source": "DATA-004_P0",
            "source_market_ids": sorted(SOURCE_IDS),
            "data001_used": False,
        },
        "final_opened": False,
        "final_rows_accessed": 0,
        "max_source_age_seconds": MAX_AGE_S,
        "factor_definitions": {
            "COUNT_MEAN6": "E[min(K,6)]/6 from projected 0..5,6+ Kamala-state Republican-win count",
            "COUNT_TAIL3": "P(K>=3) from same count surface",
            "CORE4_BREAK": "1-P(Democrats win all core four Senate races)",
            "BT_ANY": "P(Republicans win any Biden-Trump Senate/Governor election)",
            "COUNT_PLUS_JOINTS": "equal mean of COUNT_MEAN6, CORE4_BREAK, BT_ANY",
            "ALL4_EQUAL": "equal mean of COUNT_MEAN6, COUNT_TAIL3, CORE4_BREAK, BT_ANY",
        },
        "target_orientation": {
            "153": "1 - Democratic Senate probability",
            "154": "Republican Senate probability",
        },
        "interpretation": (
            "Dynamic Republican-wave information-transfer test. Factor levels are not "
            "asserted equal to Senate-control probabilities. Primary interest is whether "
            "TRAIN-fitted absorption residuals predict the next distinct source-state "
            "episode and add value beyond target-only reversal."
        ),
        "results": results,
    }
    (OUT / "WAVE_FACTOR_RESULTS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )

    viable = compact[
        (compact["train_rows"] >= 20)
        & (compact["dev_rows"] >= 5)
        & (compact["train_signed_move"].fillna(-1) > 0)
        & (compact["dev_signed_move"].fillna(-1) > 0)
        & (compact["train_increment_vs_reversal"].fillna(-1) > 0)
        & (compact["dev_increment_vs_reversal"].fillna(-1) > 0)
    ].copy()
    handoff = [
        "# R3-FV-001M Wave-Factor Handoff",
        "",
        "**R3 FINAL accessed:** NO",
        "",
        "This lane uses DATA-003 targets and DATA-004 P0 sources only. DATA-001 is not used.",
        "",
        "The method treats three untouched P0 structural objects as Republican-wave sensors, "
        "not as direct fair values. Repeated target updates under the same source state are "
        "collapsed into episodes. Absorption beta and isotonic calibration are fit on TRAIN only.",
        "",
        "A candidate must be positive in TRAIN and DEV and add signed movement versus the "
        "target-only reversal baseline on the same episodes. This remains discovery evidence.",
        "",
        f"Candidate rows meeting the broad four-sign screen: {len(viable)}",
        "",
        viable.to_csv(index=False),
    ]
    (OUT / "WAVE_FACTOR_HANDOFF.md").write_text("\n".join(handoff))

    print(
        "R3M_WAVE_RESULT="
        + json.dumps(
            {
                "status": "COMPLETE",
                "data001_used": False,
                "final_opened": False,
                "target_rows": int(len(rows)),
                "candidate_rows": int(len(viable)),
                "targets": sorted(results),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
