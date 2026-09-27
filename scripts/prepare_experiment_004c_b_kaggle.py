[Reading 111 lines from start (total: 111 lines, 0 remaining)]

"""Build the self-contained EXPERIMENT-004C-B Kaggle kernel from the immutable freeze."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data/experiments/experiment_004c_b"
K = ROOT / "scripts/kaggle/experiment_004c_b"
REGISTRY = EXP / "family_registry.json"
PREREG = EXP / "preregistration.json"
MARKER = EXP / "FREEZE_COMMIT_MARKER.json"
PRIOR = EXP / "prior_exposure_manifest.json"
DOC = ROOT / "docs/experiments/EXPERIMENT_004C_B_STRUCTURAL_REDISTRIBUTION.md"
MATRIX = ROOT / "data/experiments/experiment_004a2/condition_usability_matrix.csv"
REGIMES = ROOT / "data/experiments/experiment_004a/regime_definitions.json"
EVENTS = ("hungary_election", "peru_first_round", "colombia_first_round", "peru_runoff", "colombia_runoff")
PRIMARY = ("PRE_ELECTION", "ACTIVE_RESULTS")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    frozen = {
        "family_registry_sha256": REGISTRY,
        "preregistration_sha256": PREREG,
        "prior_exposure_manifest_sha256": PRIOR,
        "scientific_doc_sha256": DOC,
    }
    for key, path in frozen.items():
        actual = sha(path)
        if actual != marker[key]:
            raise SystemExit(f"frozen artifact drift: {key}: {actual} != {marker[key]}")

    universe = {event: {regime: [] for regime in PRIMARY} for event in EVENTS}
    with MATRIX.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fields = {"PRE_ELECTION": "pre_election_usable", "ACTIVE_RESULTS": "active_results_usable"}
    for row in rows:
        event = row["regime_id"]
        if event not in universe or row["canonical_outcome"] != "Yes" or not row["canonical_token_id"]:
            continue
        for regime, field in fields.items():
            if row[field].lower() != "true":
                continue
            universe[event][regime].append(
                {
                    "condition_id": row["condition_id"],
                    "market_id": row["market_id"],
                    "market_family": row["market_family"],
                    "question": row["question"],
                    "canonical_token_id": row["canonical_token_id"],
                }
            )
    for event in EVENTS:
        for regime in PRIMARY:
            dedup = {}
            for row in universe[event][regime]:
                dedup[row["condition_id"]] = row
            universe[event][regime] = [dedup[key] for key in sorted(dedup)]

    windows = {event: {} for event in EVENTS}
    definitions = json.loads(REGIMES.read_text(encoding="utf-8"))
    for event in definitions["events"]:
        event_id = event["regime_id"]
        if event_id not in windows:
            continue
        for regime in event["regimes"]:
            if regime["name"] in PRIMARY:
                windows[event_id][regime["name"]] = [regime["start_utc"], regime["end_utc"]]

    embedded = {
        "registry": json.loads(REGISTRY.read_text(encoding="utf-8")),
        "preregistration": json.loads(PREREG.read_text(encoding="utf-8")),
        "freeze_marker": marker,
        "prior_exposure": json.loads(PRIOR.read_text(encoding="utf-8")),
        "universe": universe,
        "windows": windows,
        "source_hashes": {
            "condition_usability_matrix_sha256": sha(MATRIX),
            "regime_definitions_sha256": sha(REGIMES),
        },
    }
    body = (K / "run_body.py.txt").read_text(encoding="utf-8")
    future = "from __future__ import annotations\n"
    if not body.startswith(future):
        raise SystemExit("run_body.py.txt must start with future import")
    run = future + "EMBEDDED=" + repr(embedded) + "\n" + body[len(future) :]
    (K / "run.py").write_text(run, encoding="utf-8")
    print(
        json.dumps(
            {
                "run_sha256": sha(K / "run.py"),
                "run_bytes": (K / "run.py").stat().st_size,
                "families": len(embedded["registry"]["families"]),
                "universe_counts": {
                    f"{e}|{r}": len(universe[e][r]) for e in EVENTS for r in PRIMARY
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

[executed on device: ip-172-31-73-211.ec2.internal (c5706598-02ec-4925-9d6e-43e31c32d097)]