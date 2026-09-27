"""Freeze the result-independent EXPERIMENT-004B discovery specification."""

from __future__ import annotations

import json
from pathlib import Path

from predictions_cup.learning.election_market_structure import (
    DISCOVERY_EVENTS,
    EVENT_FAMILY,
    EXPECTED_BASE_COMMIT,
    SEALED_EVENTS,
    frozen_discovery_manifest,
    verify_upstream,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/experiments/experiment_004b"
OUT.mkdir(parents=True, exist_ok=True)
verify_upstream(ROOT)
manifest = frozen_discovery_manifest()
(OUT / "discovery_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
sealed = {
    "experiment_id": "EXPERIMENT-004B",
    "base_commit": EXPECTED_BASE_COMMIT,
    "sealed_events": list(SEALED_EVENTS),
    "sealed_event_families": {e: EVENT_FAMILY[e] for e in SEALED_EVENTS},
    "discovery_events": list(DISCOVERY_EVENTS),
    "empirical_paths_inspected": False,
    "allowed_knowledge": [
        "event identity",
        "event-family identity",
        "market/condition metadata",
        "accepted regime boundaries",
        "coverage/eligibility metadata",
        "hashes",
        "chronology",
        "semantic market relationships",
    ],
    "discovery_spec_sha256": manifest["discovery_spec_sha256"],
}
(OUT / "sealed_holdout_manifest.json").write_text(
    json.dumps(sealed, indent=2, sort_keys=True) + "\n"
)
print(manifest["discovery_spec_sha256"])
