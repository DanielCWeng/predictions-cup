from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from predictions_cup.learning.historical_alpha import (
    FAMILY_IDS,
    FillSeries,
    QuoteSeries,
    Relationship,
    _apply_global_fdr,
    _collapse_quote_batches,
    _fit_ols,
    _participant_feature,
    _predict_ols,
    _svd_factors,
    build_family_harnesses,
    load_relationships,
    mechanical_loo_price_relationships,
)


def test_same_time_quote_batches_are_order_invariant_and_ambiguous() -> None:
    t = np.asarray([1, 1, 2], dtype=np.int64)
    bids = np.asarray([0.40, 0.41, 0.42])
    asks = np.asarray([0.60, 0.59, 0.58])

    left = _collapse_quote_batches(t, bids, asks)
    right = _collapse_quote_batches(t, bids[[1, 0, 2]], asks[[1, 0, 2]])

    np.testing.assert_array_equal(left[0], np.asarray([1, 2]))
    np.testing.assert_array_equal(left[0], right[0])
    assert np.isnan(left[1][0]) and np.isnan(left[2][0])
    assert np.isnan(right[1][0]) and np.isnan(right[2][0])
    assert left[1][1] == right[1][1] == pytest.approx(0.42)


def test_identical_same_time_quote_batch_remains_valid() -> None:
    t = np.asarray([1, 1], dtype=np.int64)
    bids = np.asarray([0.40, 0.40])
    asks = np.asarray([0.60, 0.60])
    out = _collapse_quote_batches(t, bids, asks)
    np.testing.assert_array_equal(out[0], np.asarray([1]))
    np.testing.assert_allclose(out[1], np.asarray([0.40]))
    np.testing.assert_allclose(out[2], np.asarray([0.60]))


def test_quote_sample_never_borrows_future_state() -> None:
    seconds = 1_000_000_000
    series = QuoteSeries(
        times_ns=np.asarray([10 * seconds, 20 * seconds], dtype=np.int64),
        bid=np.asarray([0.40, 0.50]),
        ask=np.asarray([0.60, 0.70]),
        midpoint=np.asarray([0.50, 0.60]),
        logit_mid=np.asarray([0.0, np.log(1.5)]),
    )
    query = np.asarray([5 * seconds, 15 * seconds], dtype=np.int64)
    logits, bids, _, valid = series.sample(query, freshness_ns=100 * seconds)

    assert not valid[0]
    assert np.isnan(logits[0])
    assert valid[1]
    assert bids[1] == pytest.approx(0.40)
    assert logits[1] == pytest.approx(0.0)


def test_ols_fit_is_train_only() -> None:
    x_train = np.asarray([[0.0], [1.0], [2.0], [3.0]] * 10)
    y_train = 2.0 * x_train[:, 0] + 1.0
    fit = _fit_ols(x_train, y_train)
    x_holdout = np.asarray([[4.0], [5.0]])
    first = _predict_ols(x_holdout, fit)

    wildly_different_holdout_labels = np.asarray([1e9, -1e9])
    del wildly_different_holdout_labels
    second = _predict_ols(x_holdout, fit)

    np.testing.assert_allclose(first, second)
    np.testing.assert_allclose(first, np.asarray([9.0, 11.0]), atol=1e-10)


def test_participant_feature_excludes_same_block_second() -> None:
    second = 1_000_000_000
    fills = FillSeries(
        times_ns=np.asarray([9 * second, 10 * second], dtype=np.int64),
        value_usd=np.asarray([10.0, 100.0]),
        maker=np.asarray(["old", "same"], dtype=object),
        taker=np.asarray(["old-t", "same-t"], dtype=object),
        exchange_taker=np.asarray([False, False]),
    )
    scores = {
        ("maker", "old"): 1.0,
        ("taker", "old-t"): 1.0,
        ("maker", "same"): 10.0,
        ("taker", "same-t"): 10.0,
    }
    times = np.asarray([10 * second + second // 2, 11 * second], dtype=np.int64)
    own_move = np.asarray([1.0, 1.0])
    feature = _participant_feature(
        fills=fills,
        times_ns=times,
        own_move=own_move,
        scores=scores,
        window_seconds=60,
    )

    assert feature[0] == pytest.approx(1.0)
    assert feature[1] > feature[0]


def test_lowrank_svd_sign_convention_is_deterministic() -> None:
    train = np.asarray(
        [[1.0, 3.0, 2.0], [2.0, 2.0, 3.0], [3.0, 1.0, 4.0], [4.0, 0.0, 5.0]]
    )
    first = _svd_factors(train, rank=1)
    second = _svd_factors(train.copy(), rank=1)
    np.testing.assert_allclose(first[2], second[2])
    loading = first[2][0]
    pivot = int(np.argmax(np.abs(loading)))
    assert loading[pivot] > 0


def test_relationship_inventory_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "inventory.csv"
    path.write_text("regime_id,target_condition_id\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_relationships(path, "0" * 64)


def test_mechanical_loo_excludes_target_and_marks_leakage() -> None:
    primary = (
        Relationship(
            regime_id="r",
            target_condition_id="c1",
            target_token_id="t1",
            target_market_family="F",
            reference_condition_id="c9",
            reference_token_id="t9",
            reference_market_family="G",
            leakage_class="INDIRECT",
            semantic_basis="manual",
        ),
    )
    identity = [
        {
            "regime_id": "r",
            "condition_id": "c1",
            "token_id": "t1",
            "outcome": "Yes",
            "book_available": "true",
            "event_id": "e",
            "market_family": "F",
            "book_evidence_rows": "100",
        },
        {
            "regime_id": "r",
            "condition_id": "c2",
            "token_id": "t2",
            "outcome": "Yes",
            "book_available": "true",
            "event_id": "e",
            "market_family": "F",
            "book_evidence_rows": "90",
        },
    ]
    rows = mechanical_loo_price_relationships(identity, primary, "r")
    assert rows
    assert all(row.reference_token_id != row.target_token_id for row in rows)
    assert all(row.leakage_class == "MECHANICAL_SIBLING" for row in rows)


def test_partial_fdr_keeps_full_preregistered_denominator() -> None:
    prereg_path = Path("data/experiments/experiment_003/preregistration.json")
    prereg = json.loads(prereg_path.read_text())
    horizons = [1, 5, 30, 60, 300]
    primary: dict[str, dict[int, dict[str, object]]] = {
        family: {h: {"raw_p_value": None} for h in horizons}
        for family in FAMILY_IDS
    }
    primary["leadlag"][1]["raw_p_value"] = 0.001

    result = _apply_global_fdr(primary, prereg)

    assert len(result) == 25
    assert all(row["family_size"] == 25 for row in result.values())
    assert result["LEADLAG-001@1s"]["raw_p_value"] == pytest.approx(0.001)
    assert result["LOWRANK-001@300s"]["raw_p_value"] is None


def test_preregistration_is_deterministic_json() -> None:
    path = Path("data/experiments/experiment_003/preregistration.json")
    payload = json.loads(path.read_text())
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    assert encoded == path.read_bytes()
    assert hashlib.sha256(encoded).hexdigest()

def test_build008_family_run_ids_bind_preregistration_and_family() -> None:
    prereg_path = Path("data/experiments/experiment_003/preregistration.json")
    prereg = json.loads(prereg_path.read_text())
    relationships = load_relationships(
        Path(prereg["relationship_inventory"]["path"]),
        prereg["relationship_inventory"]["sha256"],
    )
    digest = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
    harnesses = build_family_harnesses(
        prereg=prereg,
        relationships=relationships,
        code_revision="a" * 40,
        preregistration_sha256=digest,
    )

    run_ids = {family: harness.identity.run_id for family, harness in harnesses.items()}
    assert set(run_ids) == set(FAMILY_IDS)
    assert len(set(run_ids.values())) == len(FAMILY_IDS)
    assert all(
        not harness.spec.disposition_policy.require_execution_stress
        for harness in harnesses.values()
    )

    changed = build_family_harnesses(
        prereg=prereg,
        relationships=relationships,
        code_revision="a" * 40,
        preregistration_sha256="b" * 64,
    )
    assert changed["leadlag"].identity.run_id != harnesses["leadlag"].identity.run_id
