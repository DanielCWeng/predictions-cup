from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from predictions_cup.learning.structural_redistribution import (
    bh_adjust,
    project_capped_simplex,
    project_nonincreasing,
    project_simplex,
    structural_residual,
)

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data/experiments/experiment_004c_b"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_projection_simplex_and_capped_simplex() -> None:
    p = np.array([0.7, 0.6, 0.2])
    q = project_simplex(p)
    assert np.isclose(q.sum(), 1.0)
    assert np.all(q >= 0)
    assert np.allclose(project_capped_simplex(np.array([0.2, 0.3])), [0.2, 0.3])
    capped = project_capped_simplex(p)
    assert np.isclose(capped.sum(), 1.0)
    assert np.allclose(structural_residual(p, "MUTUALLY_EXCLUSIVE_NONEXHAUSTIVE"), p - capped)


def test_nonincreasing_isotonic_projection() -> None:
    x = np.array([0.8, 0.5, 0.6, 0.2])
    q = project_nonincreasing(x)
    assert np.all(q[:-1] >= q[1:] - 1e-12)
    assert np.all((q >= 0) & (q <= 1))
    assert np.allclose(project_nonincreasing(np.array([0.9, 0.7, 0.4])), [0.9, 0.7, 0.4])


def test_bh_counts_unavailable_cells_in_family_size() -> None:
    rows = [
        {"hypothesis_id": "a", "fdr_family": "x", "p_value": 0.001},
        {"hypothesis_id": "b", "fdr_family": "x", "p_value": None},
        {"hypothesis_id": "c", "fdr_family": "x", "p_value": 0.02},
    ]
    out = bh_adjust(rows)
    assert all(row["fdr_family_size"] == 3 for row in out)
    assert np.isclose(out[0]["q_value"], 0.003)
    assert out[1]["q_value"] is None


def test_frozen_scientific_artifacts_match_marker() -> None:
    marker = json.loads((EXP / "FREEZE_COMMIT_MARKER.json").read_text())
    expected = {
        "family_registry_sha256": EXP / "family_registry.json",
        "preregistration_sha256": EXP / "preregistration.json",
        "prior_exposure_manifest_sha256": EXP / "prior_exposure_manifest.json",
        "family_validation_report_sha256": EXP / "family_validation_report.md",
        "scientific_doc_sha256": (
            ROOT / "docs/experiments/EXPERIMENT_004C_B_STRUCTURAL_REDISTRIBUTION.md"
        ),
    }
    for key, path in expected.items():
        assert _sha(path) == marker[key]
