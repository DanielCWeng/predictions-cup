from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/research"))
import pytest
import israel_verified_event as evidence


def test_verified_record_requires_human_review_and_distinct_sources(monkeypatch):
    monkeypatch.setattr(evidence, "known_assets", lambda: {"a"})
    record = evidence.make_record(
        asset_id="a",description="Sample official vote reporting",
        kind="official_count",url="https://www.gov.il/some/results",
        corroborator="https://apnews.com/hub/election-2026",
        published_iso="2026-10-27T20:01:00+00:00",
        reviewer="human_researcher",
        methodology="I manually checked two independently originated updates",
        now_ms=evidence.iso_epoch_ms("2026-10-27T20:01:30Z"),
    )
    assert record["trading_authority"] is False
    assert record["received_epoch_ms"] - record["published_epoch_ms"] == 30_000
    assert record["source_domains"][0] != record["source_domains"][1]


def test_verification_rejects_duplicate_domains_future_and_unmapped(monkeypatch):
    monkeypatch.setattr(evidence, "known_assets", lambda: {"a"})
    vals=dict(asset_id="a",description="Sample official vote reporting",
              kind="official_count",url="https://www.gov.il/a",
              corroborator="https://www.gov.il/b",
              published_iso="2026-10-27T20:01:00Z",
              reviewer="human_researcher",methodology="I manually checked two independent primary feeds",
              now_ms=evidence.iso_epoch_ms("2026-10-27T20:01:30Z"))
    with pytest.raises(ValueError):
        evidence.make_record(**vals)
    vals["corroborator"]="https://apnews.com/story"
    vals["asset_id"]="unmapped"
    with pytest.raises(ValueError):
        evidence.make_record(**vals)
    vals["asset_id"]="a"
    vals["published_iso"]="2026-10-28T20:01:30Z"
    with pytest.raises(ValueError):
        evidence.make_record(**vals)
