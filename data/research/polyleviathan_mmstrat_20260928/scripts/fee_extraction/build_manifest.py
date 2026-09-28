"""Build data_002_manifest.json and data_002_quality.json for the DATA-002 Kaggle package."""
import json, hashlib, datetime
from pathlib import Path
import polars as pl

ROOT = Path(__file__).parent.parent / "sig-cup-data-002-polymarket-fees" / "schema_version=1"

KIND = {
    "matched_fills": "matched_fills",
    "unmatched": "unmatched",
    "rebates": "rebates",
    "regimes": "regimes",
    "docs": "docs",
}

def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def row_count(p: Path):
    if p.suffix == ".parquet":
        return pl.scan_parquet(str(p)).select(pl.len()).collect().item()
    if p.suffix == ".csv":
        return pl.scan_csv(str(p)).select(pl.len()).collect().item()
    return None

files = []
for sub in ["matched_fills", "unmatched", "rebates", "regimes", "docs"]:
    for p in sorted((ROOT / sub).iterdir()):
        rel = f"schema_version=1/{sub}/{p.name}"
        files.append({
            "path": rel,
            "bytes": p.stat().st_size,
            "rows": row_count(p),
            "sha256": sha256_of(p),
            "kind": KIND[sub],
        })

stats = json.loads((ROOT / "docs" / "STATS.json").read_text())
families = ["US_2024", "CAN_2025", "COL_2026", "HUN_2026", "PER_2026"]

time_coverage = {
    fam: {"first": stats[fam]["first"], "last": stats[fam]["last"]}
    for fam in families
}

manifest = {
    "dataset_id": "sig-cup-data-002-polymarket-fees",
    "schema_version": 1,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "source_commit": None,
    "pipeline_commit": None,
    "families": families,
    "time_coverage": time_coverage,
    "fee_regime_dates": {
        "fees_introduced_utc": "2026-01-05",
        "v2_migration_utc": "2026-04-28",
        "sports_fee_rate_change_utc": "2026-07-10",
    },
    "files": files,
}

quality = {
    "fee_transfer_records": stats["legs_total"],
    "matched_fee_transfers": stats["legs_attributed"],
    "unattributed_fee_transfers": stats["legs_unattributed"],
    "reconciliation_check": {
        "matched_plus_unattributed": stats["legs_attributed"] + stats["legs_unattributed"],
        "total": stats["legs_total"],
        "status": "PASS" if stats["legs_attributed"] + stats["legs_unattributed"] == stats["legs_total"] else "FAIL",
    },
    "family_fee_totals_usd_equiv": {
        fam: {
            "fee_charged_usd_equiv": stats[fam]["fee_charged_usd_equiv"],
            "fee_refunded_usd_equiv": stats[fam]["fee_refunded_usd_equiv"],
        }
        for fam in families
    },
    "rebates_scoped_wallets": {
        "payouts": stats.get("rebate_payouts_all"),
        "usd": stats.get("rebate_usd_all"),
        "note": "rebate_payouts_all/rebate_usd_all in STATS.json are ALL distributor payouts pre-wallet-scoping; "
                "the shipped rebates/maker_rebate_payouts_scoped_wallets.parquet is filtered to wallets appearing "
                "in these fills (97,124 rows / $2,230,607.84 per STATS.md) - see docs/STATS.md for the scoped figure.",
    },
}

(ROOT / "data_002_manifest.json").write_text(json.dumps(manifest, indent=2))
(ROOT / "data_002_quality.json").write_text(json.dumps(quality, indent=2))
print("manifest files:", len(files))
print(json.dumps(quality, indent=2))
