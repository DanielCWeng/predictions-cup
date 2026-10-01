from pathlib import Path
import glob, json, math, os, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
OUT = Path(os.environ.get("LIVE_ALPHA_OUTPUT", "/kaggle/working"))
override = os.environ.get("LIVE_ALPHA_INPUT")
if override:
    roots = [Path(override)]
else:
    roots = list(Path("/kaggle/input").glob("sig-live-alpha-20261001-v1*"))
    if not roots:
        embedded = Path("/kaggle/src")
        if list(embedded.glob("trade_features_part*.csv")):
            roots = [embedded]
if not roots or not roots[0].exists():
    raise RuntimeError("live-alpha compact tape unavailable")
ROOT = roots[0]
OUT.mkdir(parents=True, exist_ok=True)

def load_parts(pattern):
    files = sorted(ROOT.glob(pattern))
    if not files:
        raise RuntimeError(f"missing {pattern}")
    return pd.concat([pd.read_csv(p) for p in files], ignore_index=True)

def ci95(x):
    x = pd.Series(x).dropna().astype(float)
    if len(x) < 2:
        return (np.nan, np.nan)
    se = x.std(ddof=1) / math.sqrt(len(x))
    return (x.mean()-1.96*se, x.mean()+1.96*se)

def stat(x):
    x = pd.Series(x).dropna().astype(float)
    lo, hi = ci95(x)
    return len(x), x.mean() if len(x) else np.nan, x.median() if len(x) else np.nan, lo, hi

tr = load_parts("trade_features_part*.csv")
st = load_parts("state_minute_part*.csv")
mapping = pd.read_csv(ROOT / "mapping_exact_same.csv", dtype=str)

tr["executed_at"] = pd.to_datetime(tr["executed_at"], utc=True)
st["at"] = pd.to_datetime(st["at"], utc=True)
numeric_trade = [c for c in tr.columns if c not in {"executed_at","exchange_id","title","token_id","taker_side"}]
numeric_state = [c for c in st.columns if c not in {"at","exchange_id","title","token_id"}]
for c in numeric_trade:
    tr[c] = pd.to_numeric(tr[c], errors="coerce")
for c in numeric_state:
    st[c] = pd.to_numeric(st[c], errors="coerce")
tr["exchange_id"] = tr["exchange_id"].astype(str)
st["exchange_id"] = st["exchange_id"].astype(str)

valid = ((tr["taker_side"].isin(["BUY","SELL"])) &
         (tr["sig_bbo_age_s"] <= 10) &
         (tr["pm_age_s"] <= 65) &
         tr["maker_edge"].notna() &
         (tr["pm_spread"].between(0,.10)))
v = tr[valid].copy().sort_values("executed_at")
if len(v) < 50:
    raise RuntimeError(f"too few matched trades: {len(v)}")
t0, t1 = v["executed_at"].min(), v["executed_at"].max()
cut = t0 + (t1-t0)*.60
v["split"] = np.where(v["executed_at"]<=cut, "DEV", "VAL")
st["split"] = np.where(st["at"]<=cut, "DEV", "VAL")

overall = {
    "start": str(t0), "end": str(t1), "validation_cut": str(cut),
    "matched_trades": int(len(v)), "markets": int(v["exchange_id"].nunique()),
    "notional": float(v["notional"].sum()),
    "maker_edge_c": 100*float(v["maker_edge"].mean()),
    "maker_edge_median_c": 100*float(v["maker_edge"].median()),
    "pre_move_against_maker_c": 100*float(v["pre_move_against_maker"].mean()),
}
for h in (1,5,15,30,60,300):
    overall[f"maker_m{h}_c"] = 100*float(v[f"maker_m{h}"].mean())
    overall[f"maker_m{h}_n"] = int(v[f"maker_m{h}"].notna().sum())

market_rows = []
for (exch,title), g in v.groupby(["exchange_id","title"]):
    d, z = g[g["split"]=="DEV"], g[g["split"]=="VAL"]
    n, mean, med, lo, hi = stat(z["maker_m60"])
    market_rows.append({
        "exchange_id":exch, "title":title, "dev_n":len(d), "val_n":len(z),
        "dev_edge_c":100*d["maker_edge"].mean(), "val_edge_c":100*z["maker_edge"].mean(),
        "val_m60_c":100*mean, "val_m60_ci_lo_c":100*lo,
        "val_m300_c":100*z["maker_m300"].mean(),
        "val_pre_move_against_c":100*z["pre_move_against_maker"].mean(),
        "val_notional":z["notional"].sum(),
        "val_sig_spread_c":100*z["sig_spread"].median(),
        "val_pm_spread_c":100*z["pm_spread"].median(),
    })
market_validation = pd.DataFrame(market_rows).sort_values(
    ["val_m60_ci_lo_c","val_m60_c","val_n"], ascending=False)

quote_rows = []
tick = .005
for off in (.005,.0075,.01,.0125,.015,.02,.025,.03):
    x = v.copy()
    x["qbid"] = np.floor((x["pm_mid"]-off)/tick + 1e-9)*tick
    x["qask"] = np.ceil((x["pm_mid"]+off)/tick - 1e-9)*tick
    for max_pm_spread in (.01,.02):
      for min_depth in (50,100,200,500):
       for excess in (.005,.01,.015,.02,.03):
        eligible = ((x["pm_spread"]<=max_pm_spread) &
                    (x["pm_bid1_size"].fillna(0)>=min_depth) &
                    (x["pm_ask1_size"].fillna(0)>=min_depth) &
                    (x["pm_mid"]>x["sig_bid"]) & (x["pm_mid"]<x["sig_ask"]) &
                    ((x["sig_spread"]-x["pm_spread"])>=excess))
        sellfill = eligible & (x["taker_side"]=="BUY") & (x["qask"]<=x["sig_ask"]+1e-9) & (x["price"]>=x["qask"]-1e-9)
        buyfill = eligible & (x["taker_side"]=="SELL") & (x["qbid"]>=x["sig_bid"]-1e-9) & (x["price"]<=x["qbid"]+1e-9)
        g = x[sellfill|buyfill].copy()
        if g.empty:
            continue
        g["qpx"] = np.where(g["taker_side"]=="BUY", g["qask"], g["qbid"])
        g["qsign"] = np.where(g["taker_side"]=="BUY", -1.0, 1.0)
        g["qedge"] = g["qsign"]*(g["pm_mid"]-g["qpx"])
        for h in (15,60,300):
            g[f"qm{h}"] = g["qsign"]*(g[f"pm_mid_{h}s"]-g["qpx"])

        row = {"offset_c":off*100, "max_pm_spread_c":max_pm_spread*100,
               "min_depth":min_depth, "min_excess_spread_c":excess*100}
        for split in ("DEV","VAL"):
            z = g[g["split"]==split]
            n, mean, med, lo, hi = stat(z["qm60"])
            row[f"{split.lower()}_n"] = n
            row[f"{split.lower()}_m60_c"] = 100*mean
            row[f"{split.lower()}_m60_ci_lo_c"] = 100*lo
            row[f"{split.lower()}_m300_c"] = 100*z["qm300"].mean()
            row[f"{split.lower()}_edge_c"] = 100*z["qedge"].mean()
            row[f"{split.lower()}_markets"] = z["exchange_id"].nunique()
            row[f"{split.lower()}_pnl50_60"] = (z["qm60"]*z["quantity"].clip(upper=50)).sum()
            row[f"{split.lower()}_max_market_share"] = (
                z["exchange_id"].value_counts(normalize=True).iloc[0] if len(z) else np.nan)
        row["promote_paper"] = bool(
            row.get("dev_n",0)>=10 and row.get("val_n",0)>=10 and
            row.get("dev_m60_c",np.nan)>0 and row.get("val_m60_c",np.nan)>.25 and
            row.get("val_m60_ci_lo_c",np.nan)>0 and
            (pd.isna(row.get("val_m300_c")) or row.get("val_m300_c")>0))
        quote_rows.append(row)
quote_grid = pd.DataFrame(quote_rows)
if len(quote_grid):
    quote_grid = quote_grid.sort_values(
        ["promote_paper","val_m60_ci_lo_c","val_m60_c","val_n"],
        ascending=[False,False,False,False])

s = st[(st["sig_age_s"]<=15) & (st["pm_age_s"]<=65)].copy()
s["direction"] = np.where(s["buy_edge"]>=s["sell_edge"], "BUY_SIG", "SELL_SIG")
s["edge"] = s[["buy_edge","sell_edge"]].max(axis=1)
s["entry"] = np.where(s["direction"]=="BUY_SIG", s["sig_ask"], s["sig_bid"])
s["sign"] = np.where(s["direction"]=="BUY_SIG", 1.0, -1.0)
for h in (60,300):
    s[f"m{h}"] = s["sign"]*(s[f"pm_mid_{h}s"]-s["entry"])

taker_rows = []
for threshold in (.005,.01,.015,.02,.03,.05):
 for max_pm_spread in (.01,.02):
  for min_depth in (50,100,200,500):
    g = s[(s["edge"]>=threshold) &
          (s["pm_spread"]<=max_pm_spread) &
          (s["pm_bid1_size"].fillna(0)>=min_depth) &
          (s["pm_ask1_size"].fillna(0)>=min_depth)].copy()
    if g.empty:
        continue
    row = {"threshold_c":threshold*100, "max_pm_spread_c":max_pm_spread*100,
           "min_depth":min_depth}
    for split in ("DEV","VAL"):
        z = g[g["split"]==split]
        n, mean, med, lo, hi = stat(z["m60"])
        row[f"{split.lower()}_n"] = n
        row[f"{split.lower()}_m60_c"] = 100*mean
        row[f"{split.lower()}_m60_ci_lo_c"] = 100*lo
        row[f"{split.lower()}_m300_c"] = 100*z["m300"].mean()
        row[f"{split.lower()}_edge_c"] = 100*z["edge"].mean()
        row[f"{split.lower()}_markets"] = z["exchange_id"].nunique()
    taker_rows.append(row)
taker_grid = pd.DataFrame(taker_rows)
if len(taker_grid):
    taker_grid = taker_grid.sort_values(["val_m60_ci_lo_c","val_m60_c","val_n"], ascending=False)

slice_specs = {
    "pm_mid": [-.001,.05,.15,.35,.65,.85,.95,1.001],
    "sig_spread": [-.001,.01,.015,.02,.03,.04,.06,.10,1.1],
    "pm_spread": [-.001,.005,.01,.02,.04,.10,1.1],
    "sig_residual_abs": [-.001,.005,.01,.02,.03,.05,.10,1.1],
    "prior60_count": [-.1,0.5,1.5,3.5,7.5,15.5,1e9],
}
v["sig_residual_abs"] = v["sig_residual"].abs()
slice_rows = []
for feature,bins in slice_specs.items():
    labels = pd.cut(v[feature], bins)
    for bucket,g in v.groupby(labels, observed=True):
        d,z = g[g["split"]=="DEV"],g[g["split"]=="VAL"]
        if len(d)<5 or len(z)<5:
            continue
        nd,md,_,lod,_ = stat(d["maker_m60"])
        nv,mv,_,lov,_ = stat(z["maker_m60"])
        slice_rows.append({
            "feature":feature,"bucket":str(bucket),"dev_n":nd,"val_n":nv,
            "dev_m60_c":100*md,"val_m60_c":100*mv,
            "dev_ci_lo_c":100*lod,"val_ci_lo_c":100*lov,
            "val_m300_c":100*z["maker_m300"].mean(),
            "val_edge_c":100*z["maker_edge"].mean(),
            "val_pre_move_against_c":100*z["pre_move_against_maker"].mean(),
            "val_markets":z["exchange_id"].nunique(),
        })
slice_score = pd.DataFrame(slice_rows)
if len(slice_score):
    slice_score = slice_score.sort_values(["val_ci_lo_c","val_m60_c","val_n"], ascending=False)

suspect = market_validation[
    (market_validation["val_pm_spread_c"]<=2) &
    (market_validation["val_edge_c"].abs()>=15)
].copy()

candidates = []
if len(quote_grid):
    for _,r in quote_grid[quote_grid["promote_paper"]].head(20).iterrows():
        candidates.append({
            "family":"PM_ANCHORED_MAKER","status":"PAPER_CANDIDATE",
            "spec":f"offset={r.offset_c:.2f}c pm<={r.max_pm_spread_c:.1f}c depth>={int(r.min_depth)} excess>={r.min_excess_spread_c:.1f}c",
            "dev_n":r.dev_n,"val_n":r.val_n,"val_m60_c":r.val_m60_c,
            "val_ci_lo_c":r.val_m60_ci_lo_c,"val_m300_c":r.val_m300_c,
            "val_markets":r.val_markets,"concentration":r.val_max_market_share,
        })
for _,r in market_validation[
    (market_validation["dev_n"]>=5) & (market_validation["val_n"]>=5) &
    (market_validation["dev_edge_c"]>0) & (market_validation["val_m60_ci_lo_c"]>0)
].head(20).iterrows():
    candidates.append({
        "family":"MARKET_SELECTION","status":"PAPER_CANDIDATE",
        "spec":f"{r.exchange_id} {r.title}","dev_n":r.dev_n,"val_n":r.val_n,
        "val_m60_c":r.val_m60_c,"val_ci_lo_c":r.val_m60_ci_lo_c,
        "val_m300_c":r.val_m300_c,"val_markets":1,"concentration":1.0,
    })
if len(taker_grid):
    for _,r in taker_grid[
        (taker_grid["dev_n"]>=5) & (taker_grid["val_n"]>=5) &
        (taker_grid["dev_m60_c"]>0) & (taker_grid["val_m60_ci_lo_c"]>0)
    ].head(10).iterrows():
        candidates.append({
            "family":"RESIDUAL_TAKER","status":"PAPER_CANDIDATE",
            "spec":f"edge>={r.threshold_c:.1f}c pm<={r.max_pm_spread_c:.1f}c depth>={int(r.min_depth)}",
            "dev_n":r.dev_n,"val_n":r.val_n,"val_m60_c":r.val_m60_c,
            "val_ci_lo_c":r.val_m60_ci_lo_c,"val_m300_c":r.val_m300_c,
            "val_markets":r.val_markets,"concentration":np.nan,
        })
candidate_df = pd.DataFrame(candidates)

v.to_csv(OUT/"trade_features_scored.csv", index=False)
market_validation.to_csv(OUT/"market_validation.csv", index=False)
quote_grid.to_csv(OUT/"quote_grid.csv", index=False)
taker_grid.to_csv(OUT/"taker_residual_grid.csv", index=False)
slice_score.to_csv(OUT/"state_slices.csv", index=False)
candidate_df.to_csv(OUT/"alpha_candidates.csv", index=False)
suspect.to_csv(OUT/"mapping_outlier_checks.csv", index=False)
(OUT/"diagnostics.json").write_text(json.dumps(overall, indent=2, default=str)+"\n")

def fmt(v):
    if pd.isna(v): return ""
    if isinstance(v,(float,np.floating)): return f"{v:.3f}"
    return str(v)

def md(df, cols, n=12):
    if df is None or df.empty: return "_None._"
    x=df[cols].head(n)
    lines=["| "+" | ".join(cols)+" |","| "+" | ".join(["---"]*len(cols))+" |"]
    for _,r in x.iterrows():
        lines.append("| "+" | ".join(fmt(r[c]) for c in cols)+" |")
    return "\n".join(lines)

report=["# LIVE-ALPHA-001 — live tape alpha sweep","",
        "## Scope","",
        f"- Canonical EXACT/SAME mappings: **{len(mapping)}**.",
        f"- Matched SIG trades: **{len(v):,}** across **{v.exchange_id.nunique()} markets**.",
        f"- Tape: **{t0} → {t1}**; chronological DEV/VAL cut: **{cut}**.",
        "- Polymarket is the external fair-value anchor. Lead-lag is not a promotable strategy in this run.",
        "- Passive fills are optimistic: first in queue and any trade through our quote fills us.",
        "- Intraday SIG leaderboard marking remains unresolved; economics are against PM fair value.",""]

report += ["## Aggregate observed maker economics","",
           f"- Entry edge: **{overall['maker_edge_c']:.2f}c mean / {overall['maker_edge_median_c']:.2f}c median**.",
           f"- Maker markout 15s / 60s / 300s: **{overall['maker_m15_c']:.2f}c / {overall['maker_m60_c']:.2f}c / {overall['maker_m300_c']:.2f}c**.",
           f"- PM move against maker in prior 5s: **{overall['pre_move_against_maker_c']:.3f}c**.","",
           "## Validated PM-anchored maker rules","",
           md(quote_grid,["offset_c","max_pm_spread_c","min_depth","min_excess_spread_c",
                          "dev_n","val_n","val_m60_c","val_m60_ci_lo_c","val_m300_c",
                          "val_markets","val_max_market_share"],15),"",
           "## Market selection","",
           md(market_validation,["exchange_id","title","dev_n","val_n","val_edge_c",
                                 "val_m60_c","val_m60_ci_lo_c","val_m300_c",
                                 "val_pre_move_against_c","val_notional"],15),"",
           "## State-regime slices","",
           md(slice_score,["feature","bucket","dev_n","val_n","dev_m60_c","val_m60_c",
                           "val_ci_lo_c","val_m300_c","val_markets"],15),"",
           "## Secondary residual taker rules","",
           md(taker_grid,["threshold_c","max_pm_spread_c","min_depth","dev_n","val_n",
                          "dev_m60_c","val_m60_c","val_m60_ci_lo_c","val_m300_c","val_markets"],12),"",
           "## Mapping / semantics outliers","",
           md(suspect,["exchange_id","title","dev_n","val_n","val_edge_c",
                       "val_m60_c","val_m300_c","val_sig_spread_c","val_pm_spread_c"],20),"",
           "## Candidate list","",
           md(candidate_df,["family","status","spec","dev_n","val_n","val_m60_c",
                            "val_ci_lo_c","val_m300_c","val_markets","concentration"],25),"",
           "## Discipline","",
           "- PAPER_CANDIDATE is not LIVE approval.",
           "- Prefer multi-market rules with positive holdout lower confidence bounds.",
           "- Single-market winners are concentration risks even if economics are strong.",
           "- Queue position, fees, inventory recycling, and SIG account marking must be measured before sizing.",
           "- Re-run on a later tape before increasing size.",""]
(OUT/"FINAL_REPORT.md").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report[:18]))
print("outputs:", sorted(p.name for p in OUT.iterdir()))