"""Scientific-control tests for EXPERIMENT-004A event-time evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from predictions_cup.historical import event_time
from predictions_cup.historical.event_time import (
    Window,
    _book_state_covers,
    _classification,
    _coverage_hours,
    _dt,
    _pre_usable,
    _write_json,
    scan_corpus,
    verify_data001,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "data" / "experiments" / "experiment_004a"
T0 = datetime(2026, 4, 12, 12, tzinfo=UTC)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_frozen_timeline_local_to_utc_conversion() -> None:
    payload = json.loads((PACKAGE / "event_timeline.json").read_text(encoding="utf-8"))
    for event in payload["events"]:
        for anchor in event["anchors"].values():
            if anchor is None:
                continue
            local = datetime.fromisoformat(anchor["local_timestamp"])
            expected = _dt(anchor["utc_timestamp"])
            assert local.astimezone(UTC) == expected
        for anchor in event.get("additional_anchors", []):
            local = datetime.fromisoformat(anchor["local_timestamp"])
            assert local.astimezone(UTC) == _dt(anchor["utc_timestamp"])


def test_regime_boundaries_are_ordered_contiguous_and_not_midnight_bins() -> None:
    payload = json.loads((PACKAGE / "regime_definitions.json").read_text(encoding="utf-8"))
    for event in payload["events"]:
        enabled = [r for r in event["regimes"] if r.get("enabled", True)]
        previous_end: datetime | None = None
        for regime in enabled:
            start = _dt(regime["start_utc"])
            end = _dt(regime["end_utc"])
            assert start is not None and end is not None
            assert start <= end
            if start == end:
                assert regime.get("zero_length") is True
            if previous_end is not None:
                assert start == previous_end
            previous_end = end

    # Economic boundaries must not collapse into date<election_date / UTC-midnight bins.
    timeline = json.loads((PACKAGE / "event_timeline.json").read_text(encoding="utf-8"))
    for event in timeline["events"]:
        poll = _dt(event["anchors"]["poll_close"]["utc_timestamp"])
        assert poll is not None
        assert poll.time() != datetime.min.time()


def test_event_family_grouping_and_market_specific_anchor_are_explicit() -> None:
    payload = json.loads((PACKAGE / "event_timeline.json").read_text(encoding="utf-8"))
    families = {e["regime_id"]: e["event_family"] for e in payload["events"]}
    assert families == event_time.EVENT_FAMILY
    assert len(set(families.values())) == 3

    peru = next(e for e in payload["events"] if e["regime_id"] == "peru_first_round")
    anchors = {a["anchor_type"]: a for a in peru["additional_anchors"]}
    assert anchors["SUPPLEMENTAL_POLL_CLOSE"]["utc_timestamp"] == "2026-04-13T23:00:00Z"


def test_coverage_hours_never_reports_negative_duration() -> None:
    assert _coverage_hours(T0, T0 + timedelta(hours=2)) == 2.0
    assert _coverage_hours(T0 + timedelta(hours=2), T0) == 0.0
    assert _coverage_hours(None, T0) is None


def test_no_backward_fill_before_first_depth_initialization() -> None:
    first_depth = T0 + timedelta(minutes=10)
    last_book = T0 + timedelta(hours=2)
    assert not _book_state_covers(first_depth, last_book, T0)
    assert _book_state_covers(first_depth, last_book, first_depth)
    assert _book_state_covers(first_depth, last_book, last_book)


def test_fills_only_market_cannot_become_book_usable() -> None:
    usable, reasons = _pre_usable(
        None,
        None,
        T0 + timedelta(hours=12),
        pre_obs=10_000,
        source_ok=True,
    )
    assert not usable
    assert "NO_INITIALIZED_DEPTH" in reasons
    assert "NO_PRE_ELECTION_BOOK_THROUGH_POLL_CLOSE" in reasons


@pytest.mark.parametrize(
    ("pre", "night", "expected"),
    [
        (True, True, "BOTH"),
        (True, False, "PRE_ELECTION_ONLY"),
        (False, True, "ELECTION_NIGHT_ONLY"),
        (False, False, "NEITHER"),
    ],
)
def test_usability_classification_is_deterministic(
    pre: bool, night: bool, expected: str
) -> None:
    assert _classification(pre, night) == expected
    assert _classification(pre, night) == expected


def test_source_version_provenance_and_window_gap_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "historical_replay_corpus" / "schema_version=1" / "synthetic"
    for stream in ("book_changes", "depth_snapshots", "trades"):
        (root / "books" / stream / "date=2026-04-12").mkdir(parents=True)
    (root / "fills" / "date=2026-04-12").mkdir(parents=True)

    bbo = pa.table(
        {
            "token_id": ["111", "111", "111"],
            "observed_at": [
                T0,
                T0 + timedelta(minutes=5),
                T0 + timedelta(minutes=20),
            ],
            "source_version": ["PMXT_V1", "PMXT_V1", "PMXT_V2"],
        }
    )
    pq.write_table(
        bbo,
        root / "books" / "book_changes" / "date=2026-04-12" / "part-0.parquet",
    )
    depth = pa.table(
        {
            "token_id": ["111"],
            "recorded_at": [T0],
            "source_version": ["PMXT_V1"],
        }
    )
    pq.write_table(
        depth,
        root / "books" / "depth_snapshots" / "date=2026-04-12" / "part-0.parquet",
    )
    trade = pa.table(
        {
            "token_id": ["111"],
            "observed_at": [T0 + timedelta(minutes=2)],
            "source_version": ["PMXT_V1"],
        }
    )
    pq.write_table(
        trade,
        root / "books" / "trades" / "date=2026-04-12" / "part-0.parquet",
    )
    fill = pa.table(
        {
            "token_id": ["111"],
            "observed_at": [T0 + timedelta(minutes=3)],
            "source_version": ["POLYLEVIATHAN_FILL_EXPORT"],
        }
    )
    pq.write_table(fill, root / "fills" / "date=2026-04-12" / "part-0.parquet")

    windows = {
        "synthetic": [
            Window("PRE_ELECTION", T0, T0 + timedelta(hours=1)),
        ]
    }
    result = scan_corpus(tmp_path / "historical_replay_corpus", windows, batch_size=2)
    stats = result.windows[("synthetic", "PRE_ELECTION", "book_changes", "111")]
    assert stats.count == 3
    assert stats.max_gap_us == 15 * 60 * 1_000_000
    assert result.source_versions[("synthetic", "book_changes", "111")] == {
        "PMXT_V1",
        "PMXT_V2",
    }
    assert result.source_versions[("synthetic", "fills", "111")] == {
        "POLYLEVIATHAN_FILL_EXPORT"
    }


def test_accepted_v1_v2_splice_semantics_are_preserved() -> None:
    quality = json.loads(
        (ROOT / "data" / "manifests" / "historical" / "data_001_corpus_quality.json")
        .read_text(encoding="utf-8")
    )
    regimes = {r["regime_id"]: r for r in quality["regimes"]}
    for regime_id in ("peru_first_round", "hungary_election"):
        splice = regimes[regime_id]["source_splices"]
        assert len(splice) == 1
        assert splice[0]["hour"] == "2026-04-13T19"
        assert splice[0]["primary_version"] == "PMXT_V1"
        assert splice[0]["supplement_version"] == "PMXT_V2"
        assert (
            splice[0]["supplement_only_first_observed_at"]
            == "2026-04-13T19:42:26.600000+00:00"
        )


def test_manifest_verification_checks_hash_size_and_every_manifest_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_parent = tmp_path / "historical_replay_corpus"
    schema = corpus_parent / "schema_version=1"
    repo = tmp_path / "repo-manifests"
    schema.mkdir(parents=True)
    repo.mkdir()

    payload = b"raw accepted evidence\n"
    evidence = schema / "synthetic.bin"
    evidence.write_bytes(payload)
    manifest = {
        "dataset_id": "synthetic-DATA-001",
        "pipeline_commit": "abc123",
        "totals": {"rows": 1},
        "output_files": [
            {
                "path": "synthetic.bin",
                "bytes": len(payload),
                "sha256": _sha(payload),
            }
        ],
    }
    core = {
        "corpus_manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        "corpus_quality.json": b'{"regimes":[]}\n',
        "market_identity.csv": b"regime_id,market_id,token_id\n",
    }
    repo_names = {
        "corpus_manifest.json": "data_001_corpus_manifest.json",
        "corpus_quality.json": "data_001_corpus_quality.json",
        "market_identity.csv": "data_001_market_identity.csv",
    }
    expected: dict[str, str] = {}
    for name, data in core.items():
        (schema / name).write_bytes(data)
        (repo / repo_names[name]).write_bytes(data)
        expected[name] = _sha(data)
    monkeypatch.setattr(event_time, "ACCEPTED_SHA256", expected)

    report = verify_data001(corpus_parent, repo)
    assert report["ok"] is True
    assert report["files_checked"] == 1

    evidence.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="verification failed"):
        verify_data001(corpus_parent, repo)


def test_output_serialization_is_deterministic(tmp_path: Path) -> None:
    payload = {"b": [2, 1], "a": {"z": True}}
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    _write_json(one, payload)
    _write_json(two, payload)
    assert one.read_bytes() == two.read_bytes()
