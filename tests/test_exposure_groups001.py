from __future__ import annotations

import hashlib
import json
from pathlib import Path

from predictions_cup.risk.groups import load_exposure_group_provider

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "risk" / "exposure_groups_2026.json"


def test_exposure_group_registry_is_complete_and_loadable() -> None:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    provider = load_exposure_group_provider(REGISTRY)
    assert raw["schema_version"] == "exposure-groups-v1"
    assert raw["coverage"]["total_market_count"] == 237
    assert raw["coverage"]["grouped_market_count"] == 237
    assert len(provider.memberships) == 237
    for item in provider.memberships:
        assert f"tournament:{item.tournament_id}" in item.group_ids


def test_exposure_group_registry_hash_and_provenance_are_deterministic() -> None:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    basis = json.dumps(raw["memberships"], separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(basis.encode()).hexdigest() == raw["content_sha256"]
    assert all(row["provenance"] for row in raw["memberships"])
    assert all(
        all("title" not in p["basis"].lower() for p in row["provenance"])
        for row in raw["memberships"]
    )
