"""Run from PR76 checkout. Models latest-state coalescing; no feed/network use."""
import importlib.util
import sys
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('live005f_fixtures', Path.cwd()/'tests/test_005f_live001.py')
fixtures = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)


@pytest.mark.xfail(strict=True, reason='ASTRA-015 historical fallback only: live ingestion superseded by upstream fix')
def test_coalesced_snapshots_preserve_full_stream_genuine_counts():
    full, sampled = fixtures._provider(), fixtures._provider()
    rows = [(0,.40,.60), (10,.42,.60), (20,.44,.60), (21,.42,.60), (30,.42,.60)]
    for seconds,bid,ask in rows:
        vector_full = full.feature_vector(fixtures._snapshot(seconds,bid,ask))
        # In a burst, state is overwritten at 21 before worker sees the update at 20.
        if seconds != 20:
            vector_sampled = sampled.feature_vector(fixtures._snapshot(seconds,bid,ask))
    assert vector_full is not None and vector_sampled is not None
    assert vector_sampled.values['genuine_60'] == vector_full.values['genuine_60']
    assert vector_sampled.values['genuine_age_s'] == vector_full.values['genuine_age_s']


def test_updated_precoalescing_path_preserves_roundtrip_changes():
    full, updated = fixtures._provider(), fixtures._provider()
    _, _, token = fixtures._identity()
    for seconds,bid,ask in [(0,.40,.60),(10,.42,.60),(20,.44,.60),(21,.42,.60),(30,.42,.60)]:
        snapshot = fixtures._snapshot(seconds,bid,ask)
        vector_full = full.feature_vector(snapshot)
        updated.observe_bbo(scope_id=token,observed_at=snapshot.observed_at,
            observed_monotonic_ns=fixtures.BASE_MONO+seconds*fixtures.NS,
            best_bid=bid,best_ask=ask,source_version='fixture',trusted=True)
    vector_updated = updated.feature_vector(snapshot)
    assert vector_full is not None and vector_updated is not None
    assert dict(vector_updated.values) == dict(vector_full.values)
