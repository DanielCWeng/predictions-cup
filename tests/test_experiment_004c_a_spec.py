from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "data" / "experiments" / "experiment_004c" / "a"


def _csv(name: str) -> list[dict[str, str]]:
    with (A / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_004c_a_preregistration_primary_is_frozen_30s() -> None:
    spec = json.loads((A / "preregistration.json").read_text(encoding="utf-8"))
    assert spec["primary_horizon_seconds"] == 30
    assert spec["panel_resolution_seconds"] == 30
    assert spec["regime_pooling"] is False
    assert spec["source_information_in_baseline"] is False
    assert spec["nulls"]["draws_per_null"] == 250
    assert spec["nulls"]["minimum_valid_draws"] == 250
    assert spec["models"]["hyperparameter_search"] == "NONE"


def test_004c_a_pair_registry_is_metadata_only_and_frozen() -> None:
    rows = _csv("pair_registry.csv")
    assert len(rows) == 38
    assert all(row["source_condition_id"] != row["target_condition_id"] for row in rows)
    assert all(
        row["evidence_scope"]
        in {"DISCOVERY", "004B_SEALED_PRIOR_EXPERIMENT_EXPOSED"}
        for row in rows
    )
    assert not any("effect" in key.lower() for key in rows[0])


def test_004c_a_challenge_labels_are_not_globally_pristine() -> None:
    manifest = json.loads((A / "prior_exposure_manifest.json").read_text(encoding="utf-8"))
    assert manifest["globally_pristine_holdout_claim"] is False
    assert {
        item["event"] for item in manifest["challenge_events"]
    } == {"colombia_first_round", "peru_runoff", "colombia_runoff"}


def test_004c_a_universe_never_duplicates_conditions_within_event_regime() -> None:
    rows = _csv("universe.csv")
    keys = [(r["event"], r["regime"], r["condition_id"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert all(r["canonical_token_id"] for r in rows)
