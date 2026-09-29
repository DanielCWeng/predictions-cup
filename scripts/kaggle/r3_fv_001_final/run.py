# ruff: noqa
from __future__ import annotations

import ast
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

OUT = Path("/kaggle/working/r3_fv_001_final")
OUT.mkdir(parents=True, exist_ok=True)

DATA004_SLUG = "sig-cup-data-004-ets-p0p1-fills"
BLOCK_GATE_FRAGMENT = "005b-data003-block-gate"
PREPARE_FRAGMENT = "r3-fv-001-prepare-v2"\nDISCOVERY_FRAGMENT = "r3-fv-001-discovery"
HALF_LIFE_SECONDS = 21600.0
BOOTSTRAPS = 400
RNG_SEED = 20260929


def q(path: Path) -> str:
    return str(path).replace("'", "''")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def idstr(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def parse_jsonish(value) -> list[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    if isinstance(value, (list, tuple, set)):
        return [idstr(x) for x in value if idstr(x)]
    s = str(value).strip()
    if not s:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            obj = loader(s)
            if isinstance(obj, (list, tuple, set)):
                return [idstr(x) for x in obj if idstr(x)]
            if obj is None:
                return []
            return [idstr(obj)]
        except Exception:
            continue
    return []


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass"}


def locate(name: str, fragment: str) -> Path:
    matches = [p for p in Path("/kaggle/input").rglob(name) if fragment in str(p)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} under {fragment}, got {matches}")
    return matches[0]


def locate_data004_root() -> Path:
    matches = []
    for p in Path("/kaggle/input").rglob("MANIFEST.json"):
        if DATA004_SLUG in str(p.parent):
            matches.append(p.parent)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise RuntimeError(f"expected one DATA-004 root, got {matches}")
    return matches[0]


def graph_bundle(root: Path) -> Path:
    mounted = root / "full_frozen_universe"
    if mounted.is_dir():
        return mounted
    matches = list(root.rglob("ETS_MARKET_GRAPH.csv"))
    if len(matches) == 1:
        return matches[0].parent
    raise RuntimeError("DATA-004 semantic graph is not mounted")


def load_split() -> dict:
    path = locate("R3_SPLIT_MANIFEST.json", PREPARE_FRAGMENT)
    split = json.loads(path.read_text())
    if split.get("final_opened") is not False:
        raise RuntimeError("prepare split says FINAL was opened")
    if split.get("performance_or_future_target_metrics_accessed") is not False:
        raise RuntimeError("prepare split is not outcome-blind")
    if split.get("split_status") != "FROZEN_BEFORE_PERFORMANCE":
        raise RuntimeError("prepare split is not frozen")
    return split


def load_direct_events(final_start: int) -> pd.DataFrame:
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
        with rows as (
            select
                cast(e.timestamp as bigint) ts,
                cast(t.block_number as bigint) block_number,
                cast(e.log_index as bigint) log_index,
                cast(e.sig_market_id as varchar) sig_market_id,
                upper(cast(e.mapping_class as varchar)) mapping_class,
                upper(cast(e.mapping_direction as varchar)) mapping_direction,
                case
                    when upper(cast(e.mapping_direction as varchar))='SAME'
                        then cast(e.p_yes as double)
                    when upper(cast(e.mapping_direction as varchar))='COMPLEMENT'
                        then 1.0-cast(e.p_yes as double)
                    else null
                end aligned_p
            from read_parquet('{q(econ)}') e
            join read_parquet('{q(txb)}') t
              on lower(cast(e.tx_hash as varchar))=t.tx_hash
            join read_parquet('{q(bts)}') b using(block_number)
            where upper(cast(e.mapping_class as varchar)) in ('EXACT','NEAR')
              and upper(cast(e.mapping_direction as varchar)) in ('SAME','COMPLEMENT')
        )
        select
            block_number,
            max(ts) ts,
            sig_market_id,
            arg_max(aligned_p,log_index) p_target,
            arg_max(mapping_class,log_index) mapping_class
        from rows
        where aligned_p between 0 and 1
        group by 1,3
        order by 1,3
        """
    ).fetchdf()
    con.close()
    if df.empty:
        raise RuntimeError("no eligible direct events")
    df["sig_market_id"] = df["sig_market_id"].map(idstr)
    return df


def load_source_rows(root: Path, final_start: int) -> pd.DataFrame:
    files = sorted(root.rglob("fills/date=*/part-*.parquet"))
    if not files:
        raise RuntimeError("no DATA-004 fill parquet files")
    sql = ",".join("'" + q(p) + "'" for p in files)
    con = duckdb.connect()
    con.execute("pragma threads=4")
    df = con.execute(
        f"""
        select
            cast(block_number as bigint) block_number,
            cast(log_index as bigint) log_index,
            cast("timestamp" as bigint) ts,
            cast(market_id as varchar) market_id,
            case
                when upper(cast(outcome_label as varchar))='YES'
                    then cast(price as double)
                when upper(cast(outcome_label as varchar))='NO'
                    then 1.0-cast(price as double)
                else null
            end p_yes
        from read_parquet([{sql}], union_by_name=true)
        where upper(cast(order_role as varchar))='TAKER'
          and cast(price as double) between 0 and 1
        order by block_number,log_index
        """
    ).fetchdf()
    con.close()
    df["market_id"] = df["market_id"].map(idstr)
    if df.duplicated(["block_number", "log_index"]).any():
        raise RuntimeError("DATA-004 TAKER chronology has duplicate block/log keys")
    return df


def build_semantic_maps(root: Path, source_market_ids: set[str]):
    bundle = graph_bundle(root)
    anchors = pd.read_csv(bundle / "ETS_SIG_ANCHOR_GRAPH.csv", low_memory=False)
    graph = pd.read_csv(bundle / "ETS_MARKET_GRAPH.csv", low_memory=False)
    family = pd.read_csv(
        locate("DIRECT_INFORMATION_FAMILY_SEED.csv", PREPARE_FRAGMENT),
        low_memory=False,
    )

    exchange_to_target = {
        idstr(r.sig_exchange_id): idstr(r.sig_market_id)
        for r in anchors.itertuples(index=False)
    }
    anchor_by_target = {}
    for r in anchors.itertuples(index=False):
        target = idstr(r.sig_market_id)
        anchor_by_target[target] = {
            "mapping_class": str(r.existing_direct_mapping_class),
            "adds": str(r.adds_beyond_direct),
            "direct_ids": set(parse_jsonish(r.existing_direct_pm_market_ids_json)),
            "exact_ids": set(parse_jsonish(r.v2_exact_structural_pm_ids_json)),
            "model_ids": set(parse_jsonish(r.v2_model_dependent_pm_ids_json)),
        }

    seed_by_target = {}
    for r in family.itertuples(index=False):
        target = idstr(r.sig_market_id)
        seed_by_target[target] = {
            "p1_ids": set(parse_jsonish(r.p1_direct_winner_ids_json)),
            "direct_ids": set(parse_jsonish(r.direct_market_ids_json)),
            "adds": str(r.adds_beyond_direct),
        }

    links = defaultdict(lambda: defaultdict(lambda: {"rels": set(), "maths": set()}))
    relation_classes: set[str] = set()
    math_classes: set[str] = set()
    for r in graph.itertuples(index=False):
        exchange_id = idstr(getattr(r, "sig_exchange_id", ""))
        target = exchange_to_target.get(exchange_id)
        if not target:
            continue
        math_class = str(getattr(r, "mathematical_class", "")).strip()
        relation = str(getattr(r, "relationship_class", "")).strip()
        if not relation or not math_class or math_class == "REJECTED":
            continue
        if not truthy(getattr(r, "in_v2_universe", False)):
            continue
        for market_id in parse_jsonish(getattr(r, "polymarket_market_ids_json", "")):
            if market_id not in source_market_ids:
                continue
            links[target][market_id]["rels"].add(relation)
            links[target][market_id]["maths"].add(math_class)
            relation_classes.add(relation)
            math_classes.add(math_class)

    modes = {}
    family_audit = {}
    independent_semantics = {"dependence-info", "independent-constraint"}
    for target, meta in anchor_by_target.items():
        all_ids = set(links.get(target, {}))
        seed = seed_by_target.get(target, {"p1_ids": set(), "direct_ids": set(), "adds": meta["adds"]})
        direct_ids = set(meta["direct_ids"]) | set(seed["direct_ids"])
        p1_ids = set(seed["p1_ids"])
        exact_equiv_ids = {
            mid
            for mid, lm in links.get(target, {}).items()
            if "EXACT_EQUIVALENT" in lm["rels"]
        }
        exact_ids = set(meta["exact_ids"])

        loo_price = all_ids - direct_ids
        if meta["adds"] in independent_semantics:
            loo_semantic = all_ids - direct_ids - p1_ids - exact_equiv_ids
        else:
            loo_semantic = set()
        loo_strict = (
            all_ids - direct_ids - p1_ids - exact_ids
            if meta["adds"] in independent_semantics
            else set()
        )
        modes[target] = {
            "LOO_PRICE": loo_price,
            "LOO_FAMILY_SEMANTIC": loo_semantic,
            "LOO_FAMILY_STRICT": loo_strict,
        }
        family_audit[target] = {
            "adds_beyond_direct": meta["adds"],
            "all_linked_sources": len(all_ids),
            "direct_mapping_ids": len(direct_ids & all_ids),
            "p1_direct_winner_ids": len(p1_ids & all_ids),
            "exact_equivalent_ids": len(exact_equiv_ids),
            "exact_structural_ids": len(exact_ids & all_ids),
            "loo_price_sources": len(loo_price),
            "loo_family_semantic_sources": len(loo_semantic),
            "loo_family_strict_sources": len(loo_strict),
        }

    return (
        links,
        modes,
        anchor_by_target,
        sorted(relation_classes),
        sorted(math_classes),
        family_audit,
    )


def safe_name(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def weighted_mean(values: list[tuple[float, float]]) -> float:
    if not values:
        return float("nan")
    p = np.asarray([x[0] for x in values], dtype=float)
    age = np.asarray([max(0.0, x[1]) for x in values], dtype=float)
    w = np.exp2(-age / HALF_LIFE_SECONDS)
    if float(w.sum()) <= 0:
        return float(np.mean(p))
    return float(np.average(p, weights=w))


def make_features(
    target: str,
    candidate_ids: set[str],
    state: dict[str, tuple[float, int, int]],
    target_ts: int,
    links,
    relation_classes: list[str],
    math_classes: list[str],
    anchor_meta: dict,
) -> dict[str, float]:
    available = []
    rel_values = defaultdict(list)
    math_values = defaultdict(list)
    for market_id in candidate_ids:
        st = state.get(market_id)
        if st is None:
            continue
        p, source_ts, _ = st
        age = max(0.0, float(target_ts - source_ts))
        available.append((float(p), age))
        for rel in links[target][market_id]["rels"]:
            rel_values[rel].append((float(p), age))
        for math_class in links[target][market_id]["maths"]:
            math_values[math_class].append((float(p), age))

    feat: dict[str, float] = {
        "f__candidate_sources": float(len(candidate_ids)),
        "f__available_sources": float(len(available)),
        "f__available_fraction": (
            float(len(available) / len(candidate_ids)) if candidate_ids else 0.0
        ),
        "f__mapping_exact": float(anchor_meta.get("mapping_class") == "EXACT"),
        "f__mapping_near": float(anchor_meta.get("mapping_class") == "NEAR"),
        "f__adds_dependence": float(anchor_meta.get("adds") == "dependence-info"),
        "f__adds_independent": float(anchor_meta.get("adds") == "independent-constraint"),
    }
    if available:
        p = np.asarray([x[0] for x in available], dtype=float)
        age = np.asarray([x[1] for x in available], dtype=float)
        feat.update(
            {
                "f__all_mean": float(np.mean(p)),
                "f__all_wmean": weighted_mean(available),
                "f__all_min": float(np.min(p)),
                "f__all_max": float(np.max(p)),
                "f__all_std": float(np.std(p)),
                "f__all_range": float(np.max(p) - np.min(p)),
                "f__age_min_hours": float(np.min(age) / 3600.0),
                "f__age_median_hours": float(np.median(age) / 3600.0),
            }
        )
    else:
        for key in (
            "f__all_mean",
            "f__all_wmean",
            "f__all_min",
            "f__all_max",
            "f__all_std",
            "f__all_range",
            "f__age_min_hours",
            "f__age_median_hours",
        ):
            feat[key] = float("nan")

    for rel in relation_classes:
        vals = rel_values.get(rel, [])
        key = safe_name(rel)
        feat[f"f__rel_{key}_count"] = float(len(vals))
        feat[f"f__rel_{key}_wmean"] = weighted_mean(vals)
        feat[f"f__rel_{key}_sum"] = (
            float(np.sum([x[0] for x in vals])) if vals else float("nan")
        )

    for math_class in math_classes:
        vals = math_values.get(math_class, [])
        key = safe_name(math_class)
        feat[f"f__math_{key}_count"] = float(len(vals))
        feat[f"f__math_{key}_wmean"] = weighted_mean(vals)

    return feat


def build_event_frames(
    direct: pd.DataFrame,
    source: pd.DataFrame,
    split: dict,
    links,
    modes,
    anchor_by_target,
    relation_classes,
    math_classes,
) -> dict[str, pd.DataFrame]:
    dev_start = int(split["dev_start_epoch"])
    final_start = int(split["final_start_epoch"])
    purge = int(split["selection_rule"]["boundary_purge_seconds"])

    source_rows = list(
        source[["block_number", "log_index", "ts", "market_id", "p_yes"]]
        .itertuples(index=False, name=None)
    )
    src_i = 0
    state: dict[str, tuple[float, int, int]] = {}
    previous_direct: dict[str, float] = {}
    rows = defaultdict(list)

    for ev in direct.itertuples(index=False):
        block = int(ev.block_number)
        while src_i < len(source_rows) and int(source_rows[src_i][0]) < block:
            sb, _, sts, market_id, p_yes = source_rows[src_i]
            state[idstr(market_id)] = (float(p_yes), int(sts), int(sb))
            src_i += 1

        target = idstr(ev.sig_market_id)
        current = float(ev.p_target)
        prev = previous_direct.get(target)
        ts = int(ev.ts)

        if prev is not None:
            split_name = None
            if ts < dev_start - purge:
                split_name = "TRAIN"
            elif dev_start <= ts < final_start - purge:
                split_name = "DEV"
            elif ts >= final_start:
                split_name = "FINAL"

            if split_name is not None and target in anchor_by_target:
                for mode_name, by_target in (
                    ("LOO_PRICE", modes[target]["LOO_PRICE"]),
                    ("LOO_FAMILY_SEMANTIC", modes[target]["LOO_FAMILY_SEMANTIC"]),
                    ("LOO_FAMILY_STRICT", modes[target]["LOO_FAMILY_STRICT"]),
                ):
                    feat = make_features(
                        target,
                        by_target,
                        state,
                        ts,
                        links,
                        relation_classes,
                        math_classes,
                        anchor_by_target[target],
                    )
                    if feat["f__available_sources"] <= 0:
                        continue
                    rows[mode_name].append(
                        {
                            "split": split_name,
                            "sig_market_id": target,
                            "block_number": block,
                            "ts": ts,
                            "mapping_class": str(ev.mapping_class),
                            "prev": prev,
                            "y": current,
                            **feat,
                        }
                    )
        previous_direct[target] = current

    frames = {name: pd.DataFrame(items) for name, items in rows.items()}
    for name in ("LOO_PRICE", "LOO_FAMILY_SEMANTIC", "LOO_FAMILY_STRICT"):
        frames.setdefault(name, pd.DataFrame())
    return frames


def inner_train_validation(train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    blocks = np.sort(train["block_number"].unique())
    if len(blocks) < 20:
        raise RuntimeError("insufficient TRAIN blocks for inner validation")
    cutoff = blocks[max(1, int(math.floor(0.8 * len(blocks)))) - 1]
    inner = train[train["block_number"] <= cutoff].copy()
    val = train[train["block_number"] > cutoff].copy()
    if len(inner) < 500 or len(val) < 100:
        ordered = train.sort_values(["block_number", "sig_market_id"])
        cut = max(500, int(0.8 * len(ordered)))
        inner = ordered.iloc[:cut].copy()
        val = ordered.iloc[cut:].copy()
    return inner, val


def usable_feature_columns(train: pd.DataFrame, include_prev: bool) -> list[str]:
    cols = [c for c in train.columns if c.startswith("f__")]
    cols = [c for c in cols if train[c].notna().any()]
    if include_prev:
        cols = ["prev", *cols]
    return cols



def load_selection() -> dict:
    path = locate("SELECTION_DECISION.json", DISCOVERY_FRAGMENT)
    decision = json.loads(path.read_text())
    if decision.get("final_opened") is not False:
        raise RuntimeError("discovery decision says FINAL was already opened")
    if decision.get("decision") != "FREEZE_FOR_FINAL":
        raise RuntimeError(
            "FINAL evaluator may run only after FREEZE_FOR_FINAL discovery decision"
        )
    champion = decision.get("champion")
    if not champion:
        raise RuntimeError("FREEZE_FOR_FINAL decision is missing champion")
    candidates = [champion, *decision.get("challengers", [])]
    if len(candidates) > 3:
        raise RuntimeError("more than three frozen FINAL candidates")
    for candidate in candidates:
        if candidate.get("mode") not in {
            "LOO_FAMILY_SEMANTIC",
            "LOO_FAMILY_STRICT",
        }:
            raise RuntimeError(
                f"non-LOO-FAMILY candidate attempted FINAL: {candidate}"
            )
    return decision


def build_exact_model(model_id: str, params: dict):
    if model_id == "source_ridge" or model_id == "blend_ridge":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=float(params["alpha"]))),
            ]
        )
    if model_id == "source_hgb" or model_id == "blend_hgb":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        learning_rate=float(params["learning_rate"]),
                        max_leaf_nodes=int(params["max_leaf_nodes"]),
                        l2_regularization=float(params["l2_regularization"]),
                        max_iter=140,
                        random_state=RNG_SEED,
                    ),
                ),
            ]
        )
    raise RuntimeError(f"unknown frozen model_id {model_id}")


def usable_feature_columns(frame: pd.DataFrame, include_prev: bool) -> list[str]:
    cols = [col for col in frame.columns if col.startswith("f__")]
    cols = [col for col in cols if frame[col].notna().any()]
    if include_prev:
        cols = ["prev", *cols]
    return cols


def cluster_bootstrap(improvement: np.ndarray, markets: np.ndarray, seed: int) -> dict:
    groups = {}
    for market in np.unique(markets):
        values = improvement[markets == market]
        groups[str(market)] = (float(values.sum()), int(len(values)))
    keys = sorted(groups)
    sums = np.asarray([groups[key][0] for key in keys], dtype=float)
    counts = np.asarray([groups[key][1] for key in keys], dtype=float)
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAPS, dtype=float)
    for i in range(BOOTSTRAPS):
        idx = rng.integers(0, len(keys), size=len(keys))
        draws[i] = float(sums[idx].sum() / counts[idx].sum())
    return {
        "resamples": BOOTSTRAPS,
        "lower_95": float(np.quantile(draws, 0.025)),
        "median": float(np.quantile(draws, 0.5)),
        "upper_95": float(np.quantile(draws, 0.975)),
    }


def rank_corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3:
        return None
    sa = pd.Series(a).rank(method="average")
    sb = pd.Series(b).rank(method="average")
    value = sa.corr(sb)
    return None if pd.isna(value) else float(value)


def evaluate_final(
    mode: str,
    model_id: str,
    frame: pd.DataFrame,
    params: dict,
    seed: int,
) -> tuple[dict, pd.DataFrame]:
    prefinal = frame[frame["split"].isin(["TRAIN", "DEV"])].copy()
    final = frame[frame["split"] == "FINAL"].copy()
    if len(prefinal) < 500 or len(final) < 100:
        raise RuntimeError(
            f"insufficient frozen support for {mode}/{model_id}: "
            f"prefinal={len(prefinal)} final={len(final)}"
        )

    include_prev = model_id.startswith("blend_")
    cols = usable_feature_columns(prefinal, include_prev)
    model = build_exact_model(model_id, params)
    model.fit(prefinal[cols], prefinal["y"])
    pred = np.clip(model.predict(final[cols]), 0.0, 1.0)

    y = final["y"].to_numpy(dtype=float)
    prev = final["prev"].to_numpy(dtype=float)
    base_err = np.abs(y - prev)
    model_err = np.abs(y - pred)
    improvement = base_err - model_err
    base_mae = float(np.mean(base_err))
    mae = float(np.mean(model_err))
    abs_improvement = base_mae - mae
    rel_improvement = abs_improvement / base_mae if base_mae > 0 else 0.0
    real_delta = y - prev
    pred_delta = pred - prev

    directional = {}
    for threshold in (0.005, 0.01):
        mask = (np.abs(pred_delta) >= threshold) & (np.abs(real_delta) > 1e-12)
        n = int(mask.sum())
        directional[str(threshold)] = {
            "events": n,
            "accuracy": (
                float(
                    np.mean(
                        np.sign(pred_delta[mask]) == np.sign(real_delta[mask])
                    )
                )
                if n
                else None
            ),
        }

    boot = cluster_bootstrap(
        improvement,
        final["sig_market_id"].astype(str).to_numpy(),
        seed,
    )
    confirmed = bool(
        abs_improvement > 0
        and rel_improvement >= 0.005
        and boot["lower_95"] > 0
    )

    by_mapping = {}
    for mapping_class, group in final.groupby("mapping_class"):
        idx = group.index.to_numpy()
        loc = final.index.get_indexer(idx)
        by_mapping[str(mapping_class)] = {
            "events": int(len(group)),
            "markets": int(group["sig_market_id"].nunique()),
            "baseline_mae": float(np.mean(base_err[loc])),
            "model_mae": float(np.mean(model_err[loc])),
        }

    result = {
        "mode": mode,
        "model_id": model_id,
        "frozen_params": params,
        "feature_count": int(len(cols)),
        "prefinal_refit_events": int(len(prefinal)),
        "prefinal_refit_markets": int(prefinal["sig_market_id"].nunique()),
        "final_events": int(len(final)),
        "final_sig_markets": int(final["sig_market_id"].nunique()),
        "baseline_persistence_mae": base_mae,
        "model_mae": mae,
        "absolute_mae_improvement": abs_improvement,
        "relative_mae_improvement": rel_improvement,
        "baseline_rmse": float(np.sqrt(np.mean((y - prev) ** 2))),
        "model_rmse": float(np.sqrt(np.mean((y - pred) ** 2))),
        "residual_spearman": rank_corr(pred_delta, real_delta),
        "directional_accuracy": directional,
        "cluster_bootstrap_mae_improvement": boot,
        "by_mapping_class": by_mapping,
        "final_confirmation_rule_pass": confirmed,
    }

    pred_frame = final[
        ["sig_market_id", "block_number", "ts", "mapping_class", "prev", "y"]
    ].copy()
    pred_frame["mode"] = mode
    pred_frame["model_id"] = model_id
    pred_frame["pred"] = pred
    pred_frame["baseline_abs_error"] = base_err
    pred_frame["model_abs_error"] = model_err
    pred_frame["improvement"] = improvement
    return result, pred_frame


def main() -> None:
    split = load_split()
    decision = load_selection()
    final_start = int(split["final_start_epoch"])

    root = locate_data004_root()
    direct = load_direct_events(final_start)
    source = load_source_rows(root, final_start)
    (
        links,
        modes,
        anchor_by_target,
        relation_classes,
        math_classes,
        family_audit,
    ) = build_semantic_maps(root, set(source["market_id"].astype(str)))

    frames = build_event_frames(
        direct,
        source,
        split,
        links,
        modes,
        anchor_by_target,
        relation_classes,
        math_classes,
    )

    candidates = [decision["champion"], *decision.get("challengers", [])]
    results = []
    predictions = []
    for index, candidate in enumerate(candidates):
        mode = candidate["mode"]
        model_id = candidate["model_id"]
        params = candidate["selected_inner_train_params"]
        result, pred_frame = evaluate_final(
            mode,
            model_id,
            frames[mode],
            params,
            RNG_SEED + 1000 + index,
        )
        result["role"] = "CHAMPION" if index == 0 else "CHALLENGER"
        results.append(result)
        predictions.append(pred_frame)

    champion = results[0]
    overall = (
        "CONFIRMED"
        if champion["final_confirmation_rule_pass"]
        else "NOT_FRESHLY_CONFIRMED"
    )

    payload = {
        "schema_version": 1,
        "experiment_id": "R3-FV-001",
        "stage": "ONE_SHOT_FINAL",
        "final_start_utc": split["final_start_utc"],
        "discovery_decision": decision,
        "result": overall,
        "champion": champion,
        "challengers": results[1:],
        "posthoc_replacement_allowed": False,
        "historical_execution_claim": (
            "None. This is a fair-value/repricing study using fills as observations, "
            "not proof of executable historical arbitrage or maker P&L."
        ),
    }
    out_path = OUT / "FINAL_RESULTS.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if predictions:
        pred = pd.concat(predictions, ignore_index=True)
        pred.sort_values(
            ["mode", "model_id", "block_number", "sig_market_id"]
        ).to_csv(OUT / "FINAL_PREDICTIONS.csv", index=False)

    compact = {
        "result": overall,
        "champion_mode": champion["mode"],
        "champion_model_id": champion["model_id"],
        "champion_final_events": champion["final_events"],
        "champion_relative_mae_improvement": champion[
            "relative_mae_improvement"
        ],
        "champion_bootstrap_lower_95": champion[
            "cluster_bootstrap_mae_improvement"
        ]["lower_95"],
        "final_results_sha256": sha256(out_path),
    }
    print("R3_FINAL_RESULT=" + json.dumps(compact, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
