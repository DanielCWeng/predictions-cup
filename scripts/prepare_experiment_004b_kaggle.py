"""Generate the self-contained 004B Kaggle kernel from frozen repository metadata."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K = ROOT / "scripts/kaggle/experiment_004b"
OUT = ROOT / "data/experiments/experiment_004b"
with (OUT / "market_universe.csv").open(newline="", encoding="utf-8") as h:
    universe_rows = list(csv.DictReader(h))
universe = {
    e: {r: [] for r in ("PRE_ELECTION", "ACTIVE_RESULTS")}
    for e in ("hungary_election", "peru_first_round")
}
for row in universe_rows:
    if row["admitted"] == "true":
        universe[row["event"]][row["regime"]].append(
            {
                k: row[k]
                for k in (
                    "condition_id",
                    "market_id",
                    "market_family",
                    "question",
                    "canonical_token_id",
                )
            }
        )
with (OUT / "structural_edges.csv").open(newline="", encoding="utf-8") as h:
    all_edges = list(csv.DictReader(h))
edges = [r for r in all_edges if r["verification_status"] == "SEMANTIC_BUT_NON_MECHANICAL"]
edge_counts = {
    "semantic_non_mechanical": len(edges),
    "unverified": sum(r["verification_status"] == "UNVERIFIED" for r in all_edges),
    "verified_mechanical": sum(r["verification_status"] == "MECHANICAL" for r in all_edges),
}
defs = json.loads((ROOT / "data/experiments/experiment_004a/regime_definitions.json").read_text())
windows = {}
for event in defs["events"]:
    if event["regime_id"] not in universe:
        continue
    windows[event["regime_id"]] = {
        r["name"]: [r["start_utc"], r["end_utc"]]
        for r in event["regimes"]
        if r["name"] in ("PRE_ELECTION", "ACTIVE_RESULTS")
    }
meta = {
    "universe": universe,
    "windows": windows,
    "structural_edges": edges,
    "structural_edge_counts": edge_counts,
}
body = (K / "run_body.py.txt").read_text()
future = "from __future__ import annotations\n"
assert body.startswith(future)
body = body[len(future) :]
prefix = future + "EMBEDDED=" + repr(meta) + "\n"
(K / "run.py").write_text(prefix + body)
print(
    json.dumps(
        {
            "embedded_markets": sum(len(v) for e in universe.values() for v in e.values()),
            "embedded_edges": len(edges),
            "run_bytes": (K / "run.py").stat().st_size,
        },
        sort_keys=True,
    )
)
