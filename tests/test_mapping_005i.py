"""Review regression: pairs only from canonical hash-attested EXACT registry."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from predictions_cup.replay.mapping_005i import load_accepted_005i_pairs

ROOT = Path(__file__).resolve().parents[1]


def test_actual_accepted_registry_generates_single_binary_sig_exchange_pairs() -> None:
    pairs = load_accepted_005i_pairs(ROOT)
    assert len(pairs) == 140
    assert len({p.sig_exchange_id for p in pairs}) == len(pairs)
    assert all(p.mapping_direction in ("SAME", "COMPLEMENT") for p in pairs)
    assert all(p.sig_market_id and p.pm_yes for p in pairs)
    # Even where two political party outcomes live within the same race,
    # there is no invented second SIG exchange for buying NO on the first.
    first = pairs[0]
    assert load_accepted_005i_pairs(ROOT, sig_exchange_ids=(first.sig_exchange_id,)) == (
        first,
    )


def test_near_unmapped_and_unknown_sibling_exchanges_rejected() -> None:
    with pytest.raises(ValueError, match="no VERIFIED accepted EXACT"):
        load_accepted_005i_pairs(ROOT, sig_exchange_ids=("840",))
    with pytest.raises(ValueError, match="no VERIFIED accepted EXACT"):
        load_accepted_005i_pairs(ROOT, sig_exchange_ids=("999999999",))
    with pytest.raises(ValueError, match="duplicate"):
        p = load_accepted_005i_pairs(ROOT)[0]
        load_accepted_005i_pairs(ROOT, sig_exchange_ids=(p.sig_exchange_id,) * 2)


def test_crosswalk_mutation_invalidates_acceptance_digest(tmp_path: Path) -> None:
    dst = tmp_path / "data" / "mappings"
    dst.mkdir(parents=True)
    base = ROOT / "data" / "mappings"
    for name in ("sig_polymarket_2026.json", "sig_polymarket_2026_acceptance.json"):
        (dst / name).write_bytes((base / name).read_bytes())
    registry = dst / "sig_polymarket_2026.json"
    original = registry.read_bytes()
    registry.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_accepted_005i_pairs(tmp_path)


def test_self_attested_acceptance_does_not_create_new_exact_exchange(tmp_path: Path) -> None:
    # The CLI does not accept arbitrary JSON pair arrays or 'ACCEPTED_EXACT'
    # strings. This synthetic evidence can't produce a new identity.
    dst = tmp_path / "data" / "mappings"
    dst.mkdir(parents=True)
    (dst / "sig_polymarket_2026.json").write_text('{"records":[]}')
    (dst / "sig_polymarket_2026_acceptance.json").write_text(json.dumps({
        "all_records_verified": True,
        "artifact_sha256": {"crosswalk_json": "forged"},
        "clob_smoke": {"passed": True, "mapped_token_ids": [],
                       "mapped_token_count": 0, "books_returned": 0},
    }))
    with pytest.raises(ValueError, match="digest mismatch"):
        load_accepted_005i_pairs(tmp_path)
