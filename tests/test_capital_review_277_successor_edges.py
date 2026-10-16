"""Additional independent, synthetic-only corrected-#277 adversarial boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from predictions_cup.research.offline_paired_tape_gate import validate_offline_tape
from test_capital_review_277_adversarial import _replace_archive, _two_intervals
from test_offline_paired_tape_gate import _sample


def _deny(result: Any) -> None:
    assert result.verdict == "PAIRED_DEPTH_NOT_CERTIFIED"
    assert result.schema_valid_intervals == 0
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.external_issuer_authenticated is False
    assert result.network_requests == 0


def test_claimed_midrun_new_pm_epoch_does_not_launder_sequence_reset(tmp_path: Path) -> None:
    paths, _ = _sample(tmp_path)
    _two_intervals(paths, sig_next_sequence=52, pm_next_sequence=7)
    pm_rows = [json.loads(x) for x in paths[3].read_text().splitlines()]
    # A self-asserted in-band epoch is not a new externally pinned producer run.
    pm_rows[1]["seed_generation"] = 4
    pm_rows[1]["connection_seed_generation"] = 4
    pm_rows[1]["subscription_epoch"] = 8
    pm_rows[1]["connection_subscription_epoch"] = 8
    digest = _replace_archive(paths, pm_rows=pm_rows)
    _deny(validate_offline_tape(*paths, expected_manifest_sha256=digest))


@pytest.mark.parametrize("surface", ["manifest", "mapping"])
def test_hash_pinned_nonstandard_infinity_outside_jsonl_is_denied(
    tmp_path: Path, surface: str,
) -> None:
    paths, _ = _sample(tmp_path)
    manifest = json.loads(paths[0].read_text())
    if surface == "manifest":
        manifest["extra_health"] = float("inf")
    else:
        mapping = json.loads(paths[1].read_text())
        mapping["extra_health"] = float("-inf")
        mapping_bytes = json.dumps(mapping, sort_keys=True).encode()
        paths[1].write_bytes(mapping_bytes)
        manifest["file_sha256"]["mapping"] = hashlib.sha256(mapping_bytes).hexdigest()
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    paths[0].write_bytes(manifest_bytes)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    _deny(validate_offline_tape(*paths, expected_manifest_sha256=digest))


def test_monotonic_two_interval_fixture_remains_schema_only_without_trust(tmp_path: Path) -> None:
    paths, _ = _sample(tmp_path)
    digest = _two_intervals(paths, sig_next_sequence=52, pm_next_sequence=9)
    result = validate_offline_tape(*paths, expected_manifest_sha256=digest)
    assert result.verdict == "SCHEMA_ONLY/PAPER_NOT_ADMITTED"
    assert result.schema_valid_intervals == 2
    assert result.source_admitted is False
    assert result.paper_admitted is False
    assert result.external_issuer_authenticated is False
    assert result.network_requests == 0
