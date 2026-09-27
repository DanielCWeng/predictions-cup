"""EXPERIMENT-004A event-time coverage and regime validation.

This module is intentionally descriptive.  It freezes externally sourced election
boundaries, verifies accepted DATA-001 bytes, scans only coverage/provenance columns,
and produces deterministic evidence artefacts.  It does not compute strategy returns,
signals, parameters, or any alpha-selection statistic.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

SCHEMA_VERSION = 1
EXPERIMENT_ID = "EXPERIMENT-004A"
PACKAGE_VERSION = "004A-event-time-v1"

ACCEPTED_SHA256 = {
    "corpus_manifest.json": "e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4",
    "corpus_quality.json": "354801b67b9c32ae82d419f8a6198b7fd814923e8c424ac8716862b47907ccb5",
    "market_identity.csv": "e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72",
}

EVENT_FAMILY = {
    "colombia_first_round": "COL_2026",
    "colombia_runoff": "COL_2026",
    "peru_first_round": "PER_2026",
    "peru_runoff": "PER_2026",
    "hungary_election": "HUN_2026",
}

CHECKPOINT_HOURS = (-24, -12, -6, -3, -1, 0, 0.25, 0.5, 1, 3, 6, 12, 24)

PRE_MIN_LEAD_HOURS = 6.0
NIGHT_MIN_SPAN_HOURS = 3.0
LATE_MIN_SPAN_HOURS = 6.0
MIN_BOOK_OBSERVATIONS = 20
MAX_REGIME_WIDE_SOURCE_SILENCE_SECONDS = 1800.0


@dataclass(frozen=True, slots=True)
class Window:
    name: str
    start: datetime | None
    end: datetime | None
    enabled: bool = True

    @property
    def zero_length(self) -> bool:
        return (
            self.enabled
            and self.start is not None
            and self.end is not None
            and self.start == self.end
        )


@dataclass(slots=True)
class StreamStats:
    count: int = 0
    first_us: int | None = None
    last_us: int | None = None
    max_gap_us: int = 0
    _last_us: int | None = None

    def merge(self, *, count: int, first_us: int, last_us: int, max_gap_us: int) -> None:
        if self._last_us is not None and first_us >= self._last_us:
            self.max_gap_us = max(self.max_gap_us, first_us - self._last_us)
        self.count += count
        self.first_us = first_us if self.first_us is None else min(self.first_us, first_us)
        self.last_us = last_us if self.last_us is None else max(self.last_us, last_us)
        self.max_gap_us = max(self.max_gap_us, max_gap_us)
        self._last_us = last_us if self._last_us is None else max(self._last_us, last_us)


@dataclass(slots=True)
class ScanResult:
    overall: dict[tuple[str, ...], StreamStats] = field(default_factory=dict)
    windows: dict[tuple[str, ...], StreamStats] = field(default_factory=dict)
    source_versions: dict[tuple[str, ...], set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive timestamp: {value}")
    return parsed.astimezone(UTC)


def _iso_us(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC).isoformat().replace("+00:00", "Z")


def _to_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _coverage_hours(a: datetime | None, b: datetime | None) -> float | None:
    """Non-negative observed span between two ordered coverage anchors."""
    if a is None or b is None:
        return None
    return round(max(0.0, (b - a).total_seconds() / 3600.0), 6)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_package_sha(timeline_path: Path, definitions_path: Path) -> str:
    h = hashlib.sha256()
    for path in (timeline_path, definitions_path):
        h.update(path.read_bytes())
        h.update(b"\n")
    return h.hexdigest()


def verify_data001(corpus_parent: Path, repo_manifest_dir: Path) -> dict[str, Any]:
    schema_root = corpus_parent / f"schema_version={SCHEMA_VERSION}"
    if not schema_root.is_dir():
        raise FileNotFoundError(f"missing DATA-001 schema root: {schema_root}")

    mapping = {
        "corpus_manifest.json": repo_manifest_dir / "data_001_corpus_manifest.json",
        "corpus_quality.json": repo_manifest_dir / "data_001_corpus_quality.json",
        "market_identity.csv": repo_manifest_dir / "data_001_market_identity.csv",
    }
    checked: dict[str, dict[str, str]] = {}
    for corpus_name, repo_path in mapping.items():
        corpus_path = schema_root / corpus_name
        corpus_sha = _sha256(corpus_path)
        repo_sha = _sha256(repo_path)
        expected = ACCEPTED_SHA256[corpus_name]
        if corpus_sha != expected or repo_sha != expected:
            raise ValueError(
                f"DATA-001 hash mismatch for {corpus_name}: "
                f"corpus={corpus_sha} repo={repo_sha} expected={expected}"
            )
        checked[corpus_name] = {
            "sha256": expected,
            "corpus_path": str(corpus_path),
            "repo_path": str(repo_path),
        }

    manifest = json.loads((schema_root / "corpus_manifest.json").read_text(encoding="utf-8"))
    output_files = manifest.get("output_files", [])
    problems: list[str] = []
    for item in output_files:
        rel = item["path"]
        path = schema_root / rel
        if not path.is_file():
            problems.append(f"missing:{rel}")
            continue
        if path.stat().st_size != int(item["bytes"]):
            problems.append(f"size:{rel}")
            continue
        if _sha256(path) != item["sha256"]:
            problems.append(f"sha256:{rel}")
    if problems:
        raise ValueError(f"DATA-001 file verification failed: {problems[:10]}")
    return {
        "ok": True,
        "files_checked": len(output_files),
        "accepted_core_files": checked,
        "dataset_id": manifest.get("dataset_id"),
        "pipeline_commit": manifest.get("pipeline_commit"),
        "totals": manifest.get("totals"),
    }


def _load_identity(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(rows, key=lambda r: (r["regime_id"], r["market_id"], r["token_id"]))


def _load_quality(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["regime_id"]: item for item in payload["regimes"]}


def _load_windows(path: Path) -> dict[str, list[Window]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[Window]] = {}
    for event in payload["events"]:
        windows = []
        for item in event["regimes"]:
            enabled = bool(item.get("enabled", True))
            windows.append(
                Window(
                    name=item["name"],
                    start=_dt(item.get("start_utc")),
                    end=_dt(item.get("end_utc")),
                    enabled=enabled,
                )
            )
        result[event["regime_id"]] = windows
    return result


def _stream_files(schema_root: Path, regime_id: str, stream: str) -> list[Path]:
    base = schema_root / regime_id / ("fills" if stream == "fills" else "books") / stream
    if stream == "fills":
        base = schema_root / regime_id / "fills"
    return sorted(base.glob("date=*/part-*.parquet"))


def _batch_gap_rows(tokens: pa.Array, times_us: pa.Array) -> dict[str, tuple[int, int, int, int]]:
    if len(tokens) == 0:
        return {}
    table = pa.table({"token_id": tokens, "t": times_us}).sort_by(
        [("token_id", "ascending"), ("t", "ascending")]
    )
    grouped = table.group_by("token_id").aggregate(
        [("t", "count"), ("t", "min"), ("t", "max")]
    )
    max_gap: dict[str, int] = {}
    tok = table["token_id"].combine_chunks()
    times = table["t"].combine_chunks()
    if len(table) > 1:
        same = pc.equal(tok.slice(1), tok.slice(0, len(tok) - 1))
        owners = tok.slice(1).filter(same)
        deltas = pc.subtract(times.slice(1), times.slice(0, len(times) - 1)).filter(same)
        if len(owners):
            gaps = (
                pa.table({"token_id": owners, "gap": deltas})
                .group_by("token_id")
                .aggregate([("gap", "max")])
            )
            max_gap = {r["token_id"]: int(r["gap_max"]) for r in gaps.to_pylist()}
    return {
        row["token_id"]: (
            int(row["t_count"]),
            int(row["t_min"]),
            int(row["t_max"]),
            max_gap.get(row["token_id"], 0),
        )
        for row in grouped.to_pylist()
    }


def _add_batch(
    destination: dict[tuple[str, ...], StreamStats],
    key_prefix: tuple[str, ...],
    tokens: pa.Array,
    times: pa.Array,
) -> None:
    if len(tokens) == 0:
        return
    times_us = pc.cast(pc.cast(times, pa.timestamp("us", tz="UTC")), pa.int64())
    for token, (count, first_us, last_us, gap_us) in _batch_gap_rows(tokens, times_us).items():
        key = (*key_prefix, token)
        stats = destination.setdefault(key, StreamStats())
        stats.merge(count=count, first_us=first_us, last_us=last_us, max_gap_us=gap_us)


def scan_corpus(
    corpus_parent: Path,
    windows_by_regime: dict[str, list[Window]],
    *,
    batch_size: int = 262_144,
) -> ScanResult:
    schema_root = corpus_parent / f"schema_version={SCHEMA_VERSION}"
    result = ScanResult()
    stream_time = {
        "book_changes": "observed_at",
        "depth_snapshots": "recorded_at",
        "trades": "observed_at",
        "fills": "observed_at",
    }
    for regime_id in sorted(windows_by_regime):
        windows = windows_by_regime[regime_id]
        for stream, time_col in stream_time.items():
            for path in _stream_files(schema_root, regime_id, stream):
                parquet = pq.ParquetFile(path)
                columns = ["token_id", time_col, "source_version"]
                for batch in parquet.iter_batches(batch_size=batch_size, columns=columns):
                    token_arr = batch.column(0)
                    time_arr = batch.column(1)
                    source_arr = batch.column(2)
                    _add_batch(result.overall, (regime_id, stream), token_arr, time_arr)

                    versions = pa.table(
                        {"token_id": token_arr, "source_version": source_arr}
                    ).group_by(["token_id", "source_version"]).aggregate([])
                    for row in versions.to_pylist():
                        if row["source_version"]:
                            result.source_versions[
                                (regime_id, stream, row["token_id"])
                            ].add(row["source_version"])

                    ints = pc.cast(pc.cast(time_arr, pa.timestamp("us", tz="UTC")), pa.int64())
                    for window in windows:
                        if not window.enabled or window.start is None or window.end is None:
                            continue
                        if window.start == window.end:
                            continue
                        lo = _to_us(window.start)
                        hi = _to_us(window.end)
                        mask = pc.and_(
                            pc.greater_equal(ints, pa.scalar(lo, pa.int64())),
                            pc.less(ints, pa.scalar(hi, pa.int64())),
                        )
                        if pc.sum(pc.cast(mask, pa.int64())).as_py() == 0:
                            continue
                        _add_batch(
                            result.windows,
                            (regime_id, window.name, stream),
                            token_arr.filter(mask),
                            time_arr.filter(mask),
                        )
    return result


def _quality_token_index(
    quality: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for regime_id, item in quality.items():
        for token in item["tokens"]:
            index[(regime_id, token["token_id"])] = token
    return index


def _stream_stat(
    scan: ScanResult,
    regime_id: str,
    stream: str,
    token_id: str,
    window: str | None = None,
) -> StreamStats:
    if window is None:
        return scan.overall.get((regime_id, stream, token_id), StreamStats())
    return scan.windows.get((regime_id, window, stream, token_id), StreamStats())


def _book_window_counts(
    scan: ScanResult,
    regime_id: str,
    window: str,
    token_id: str,
) -> tuple[int, int]:
    bbo = _stream_stat(scan, regime_id, "book_changes", token_id, window).count
    depth = _stream_stat(scan, regime_id, "depth_snapshots", token_id, window).count
    return bbo, depth


def _window_by_name(windows: list[Window], name: str) -> Window:
    return next(w for w in windows if w.name == name)


def _source_silence_ok(regime_quality: dict[str, Any]) -> bool:
    value = regime_quality.get("regime_wide_max_silence_seconds")
    return value is None or float(value) <= MAX_REGIME_WIDE_SOURCE_SILENCE_SECONDS


def _pre_usable(
    first_depth: datetime | None,
    last_book: datetime | None,
    poll_close: datetime,
    pre_obs: int,
    source_ok: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if first_depth is None:
        reasons.append("NO_INITIALIZED_DEPTH")
    elif first_depth > poll_close - timedelta(hours=PRE_MIN_LEAD_HOURS):
        reasons.append("INSUFFICIENT_PRE_ELECTION_LEAD")
    if last_book is None or last_book < poll_close:
        reasons.append("NO_PRE_ELECTION_BOOK_THROUGH_POLL_CLOSE")
    if pre_obs < MIN_BOOK_OBSERVATIONS:
        reasons.append("INSUFFICIENT_PRE_ELECTION_OBSERVATIONS")
    if not source_ok:
        reasons.append("MATERIAL_EVENT_WINDOW_GAP")
    return not reasons, reasons


def _night_usable(
    first_depth: datetime | None,
    last_book: datetime | None,
    active: Window,
    corpus_end: datetime,
    active_obs: int,
    source_ok: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if active.start is None or active.end is None or active.start >= corpus_end:
        return False, ["RESULT_WINDOW_OUTSIDE_DATASET"]
    if first_depth is None:
        reasons.append("NO_INITIALIZED_DEPTH")
    elif first_depth > active.start:
        reasons.append("FIRST_SNAPSHOT_AFTER_RESULT_ONSET")
    target_end = min(active.end, active.start + timedelta(hours=NIGHT_MIN_SPAN_HOURS), corpus_end)
    if last_book is None or last_book < target_end:
        reasons.append("NO_ACTIVE_RESULTS_COVERAGE")
    if active_obs < MIN_BOOK_OBSERVATIONS:
        reasons.append("INSUFFICIENT_ACTIVE_RESULTS_OBSERVATIONS")
    if not source_ok:
        reasons.append("MATERIAL_EVENT_WINDOW_GAP")
    return not reasons, reasons


def _late_usable(
    first_depth: datetime | None,
    last_book: datetime | None,
    late: Window,
    corpus_end: datetime,
    late_obs: int,
    source_ok: bool,
) -> tuple[bool, list[str]]:
    if late.start is None or late.end is None or late.start >= corpus_end:
        return False, ["LATE_COUNT_OUTSIDE_DATASET"]
    reasons: list[str] = []
    if first_depth is None or first_depth > late.start:
        reasons.append("NO_INITIALIZED_DEPTH_AT_LATE_COUNT")
    target_end = min(late.end, late.start + timedelta(hours=LATE_MIN_SPAN_HOURS), corpus_end)
    if last_book is None or last_book < target_end:
        reasons.append("INSUFFICIENT_LATE_COUNT_COVERAGE")
    if late_obs < MIN_BOOK_OBSERVATIONS:
        reasons.append("INSUFFICIENT_LATE_COUNT_OBSERVATIONS")
    if not source_ok:
        reasons.append("MATERIAL_EVENT_WINDOW_GAP")
    return not reasons, reasons


def _classification(pre: bool, night: bool) -> str:
    if pre and night:
        return "BOTH"
    if pre:
        return "PRE_ELECTION_ONLY"
    if night:
        return "ELECTION_NIGHT_ONLY"
    return "NEITHER"


def _checkpoint_name(offset: float) -> str:
    if offset == 0:
        return "poll_close"
    sign = "minus" if offset < 0 else "plus"
    value = abs(offset)
    if value < 1:
        return f"{sign}_{int(value * 60)}m"
    return f"{sign}_{int(value)}h"


def _book_state_covers(
    first_depth: datetime | None,
    last_book: datetime | None,
    when: datetime,
) -> bool:
    return (
        first_depth is not None
        and last_book is not None
        and first_depth <= when <= last_book
    )


def build_outputs(
    *,
    repo_root: Path,
    corpus_parent: Path,
    output_dir: Path,
    batch_size: int = 262_144,
) -> dict[str, Any]:
    manifest_dir = repo_root / "data" / "manifests" / "historical"
    timeline_path = output_dir / "event_timeline.json"
    definitions_path = output_dir / "regime_definitions.json"
    verification = verify_data001(corpus_parent, manifest_dir)
    package_sha = _canonical_package_sha(timeline_path, definitions_path)

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    timeline_by_regime = {e["regime_id"]: e for e in timeline["events"]}
    windows_by_regime = _load_windows(definitions_path)
    identity = _load_identity(manifest_dir / "data_001_market_identity.csv")
    quality = _load_quality(manifest_dir / "data_001_corpus_quality.json")
    quality_tokens = _quality_token_index(quality)

    scan = scan_corpus(corpus_parent, windows_by_regime, batch_size=batch_size)

    market_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    usability_rows: list[dict[str, Any]] = []

    for row in identity:
        regime_id = row["regime_id"]
        token_id = row["token_id"]
        q = quality_tokens[(regime_id, token_id)]
        event = timeline_by_regime[regime_id]
        windows = windows_by_regime[regime_id]
        anchors = event["anchors"]
        poll_close = _dt(anchors["poll_close"]["utc_timestamp"])
        result_start = _dt(anchors["first_meaningful_results"]["utc_timestamp"])
        corpus_start = _dt(event["corpus_window_start_utc"])
        corpus_end = _dt(event["corpus_window_end_utc"])
        assert poll_close and result_start and corpus_start and corpus_end

        first_book = _dt(q["first_timestamp"])
        last_book = _dt(q["last_timestamp"])
        first_depth = _dt(q["first_depth_snapshot_at"])
        depth_stats = _stream_stat(scan, regime_id, "depth_snapshots", token_id)
        trade_stats = _stream_stat(scan, regime_id, "trades", token_id)
        last_depth = _dt(_iso_us(depth_stats.last_us))
        first_fill = _dt(q["fill_first_timestamp"])
        last_fill = _dt(q["fill_last_timestamp"])

        book_sources: set[str] = set()
        for stream in ("book_changes", "depth_snapshots", "trades"):
            book_sources.update(scan.source_versions.get((regime_id, stream, token_id), set()))
        fill_sources = scan.source_versions.get((regime_id, "fills", token_id), set())
        if not book_sources and q["book_evidence_rows"]:
            book_sources.update(quality[regime_id].get("source_versions", []))
        source_versions = sorted(book_sources | fill_sources)

        row_counts = q.get("row_counts", {})
        market: dict[str, Any] = {
            "regime_id": regime_id,
            "event_id": event["event_id"],
            "event_family": event["event_family"],
            "market_id": row["market_id"],
            "condition_id": row["condition_id"],
            "token_id": token_id,
            "outcome": row["outcome"],
            "question": row["question"],
            "market_family": row["market_family"],
            "source_versions": ";".join(source_versions),
            "book_source_versions": ";".join(sorted(book_sources)),
            "poll_close_utc": anchors["poll_close"]["utc_timestamp"],
            "first_meaningful_results_utc": anchors["first_meaningful_results"]["utc_timestamp"],
            "active_results_end_utc": anchors["active_results_end"]["utc_timestamp"],
            "corpus_window_start_utc": event["corpus_window_start_utc"],
            "corpus_window_end_utc": event["corpus_window_end_utc"],
            "fill_source_versions": ";".join(sorted(fill_sources)),
            "first_book_observation": q["first_timestamp"],
            "last_book_observation": q["last_timestamp"],
            "first_depth_snapshot": q["first_depth_snapshot_at"],
            "last_depth_snapshot": _iso_us(depth_stats.last_us),
            "first_trade_print": _iso_us(trade_stats.first_us),
            "last_trade_print": _iso_us(trade_stats.last_us),
            "first_fill": q["fill_first_timestamp"],
            "last_fill": q["fill_last_timestamp"],
            "book_available": str(bool(q["book_evidence_rows"])).lower(),
            "fills_available": str(bool(q["fill_count"])).lower(),
            "depth_snapshot_count": int(row_counts.get("depth_snapshots", 0)),
            "bbo_change_count": int(row_counts.get("book_changes", 0)),
            "venue_trade_print_count": int(row_counts.get("trades", 0)),
            "fill_count": int(q["fill_count"]),
            "distinct_observation_instants": int(q["unique_timestamps"]),
            "hours_book_before_poll_close": _coverage_hours(first_book, poll_close),
            "hours_book_after_poll_close": _coverage_hours(poll_close, last_book),
            "hours_depth_before_poll_close": _coverage_hours(first_depth, poll_close),
            "hours_depth_after_poll_close": _coverage_hours(poll_close, last_depth),
            "hours_fills_before_poll_close": _coverage_hours(first_fill, poll_close),
            "hours_fills_after_poll_close": _coverage_hours(poll_close, last_fill),
            "book_state_at_first_meaningful_results": _book_state_covers(
                first_depth, last_book, result_start
            ),
            "max_book_observation_gap_seconds": q["max_observation_gap_seconds"],
            "material_gap_count": int(q["material_gap_count"]),
            "bbo_before_first_snapshot_count": int(q["book_changes_before_first_snapshot"]),
            "crossed_snapshot_count": int(q["crossed_book_snapshots"]),
            "crossed_bbo_count": int(q["crossed_bbo_changes"]),
            "empty_side_snapshot_count": int(q["empty_side_snapshots"]),
            "empty_side_bbo_count": int(q["empty_side_bbo_changes"]),
            "same_millisecond_ambiguity_count": int(
                q["ambiguous_same_timestamp_bbo_groups"]
            ),
            "book_fill_overlap_ratio": q["book_fill_overlap_ratio"],
            "evidence_grades_json": json.dumps(
                q.get("evidence_grades", []), sort_keys=True, separators=(",", ":")
            ),
            "largest_material_gaps_json": json.dumps(
                q.get("largest_material_gaps", []), sort_keys=True, separators=(",", ":")
            ),
        }
        for offset in CHECKPOINT_HOURS:
            when = poll_close + timedelta(hours=offset)
            market[f"book_state_{_checkpoint_name(offset)}"] = _book_state_covers(
                first_depth, last_book, when
            )
        market_rows.append(market)

        for window in windows:
            bbo = _stream_stat(scan, regime_id, "book_changes", token_id, window.name)
            depth = _stream_stat(scan, regime_id, "depth_snapshots", token_id, window.name)
            trade = _stream_stat(scan, regime_id, "trades", token_id, window.name)
            fills = _stream_stat(scan, regime_id, "fills", token_id, window.name)
            first_candidates = [x for x in (bbo.first_us, depth.first_us) if x is not None]
            last_candidates = [x for x in (bbo.last_us, depth.last_us) if x is not None]
            first_us = min(first_candidates) if first_candidates else None
            last_us = max(last_candidates) if last_candidates else None
            basis = "BBO_CHANGE_STREAM" if bbo.count else "DEPTH_SNAPSHOT_STREAM"
            max_gap = bbo.max_gap_us if bbo.count else depth.max_gap_us
            observed_hours = (
                None
                if first_us is None or last_us is None
                else round((last_us - first_us) / 3_600_000_000, 6)
            )
            overlap_start = max(window.start, corpus_start) if window.start else None
            overlap_end = min(window.end, corpus_end) if window.end else None
            dataset_overlap_hours = (
                None
                if overlap_start is None or overlap_end is None or overlap_end <= overlap_start
                else round((overlap_end - overlap_start).total_seconds() / 3600.0, 6)
            )
            observed_fraction = (
                None
                if observed_hours is None or dataset_overlap_hours is None
                else round(min(1.0, observed_hours / dataset_overlap_hours), 8)
            )
            window_rows.append(
                {
                    "regime_id": regime_id,
                    "event_family": event["event_family"],
                    "market_id": row["market_id"],
                    "condition_id": row["condition_id"],
                    "token_id": token_id,
                    "market": row["question"],
                    "regime": window.name,
                    "start": None if window.start is None else window.start.isoformat(),
                    "end": None if window.end is None else window.end.isoformat(),
                    "observed_duration_hours": observed_hours,
                    "dataset_overlap_duration_hours": dataset_overlap_hours,
                    "observed_span_fraction": observed_fraction,
                    "bbo_count": bbo.count,
                    "snapshot_count": depth.count,
                    "trade_count": trade.count,
                    "fill_count": fills.count,
                    "bbo_per_observed_hour": _rate_per_hour(bbo.count, observed_hours),
                    "snapshots_per_observed_hour": _rate_per_hour(depth.count, observed_hours),
                    "trades_per_observed_hour": _rate_per_hour(trade.count, observed_hours),
                    "fills_per_observed_hour": _rate_per_hour(fills.count, observed_hours),
                    "max_gap_seconds": None if max_gap == 0 else round(max_gap / 1_000_000, 6),
                    "max_gap_basis": basis,
                    "usable": False,
                    "reason": "NOT_A_PREDICTIVE_REGIME"
                    if window.name in {"ELECTION_DAY_PRE_RESULTS", "POST_RESOLUTION_DIAGNOSTIC"}
                    else "",
                }
            )

        pre = _window_by_name(windows, "PRE_ELECTION")
        active = _window_by_name(windows, "ACTIVE_RESULTS")
        late = _window_by_name(windows, "LATE_COUNT")
        pre_bbo, pre_depth = _book_window_counts(scan, regime_id, pre.name, token_id)
        active_bbo, active_depth = _book_window_counts(scan, regime_id, active.name, token_id)
        late_bbo, late_depth = _book_window_counts(scan, regime_id, late.name, token_id)
        source_ok = _source_silence_ok(quality[regime_id])
        pre_ok, pre_reasons = _pre_usable(
            first_depth, last_book, poll_close, pre_bbo + pre_depth, source_ok
        )
        night_ok, night_reasons = _night_usable(
            first_depth,
            last_book,
            active,
            corpus_end,
            active_bbo + active_depth,
            source_ok,
        )
        late_ok, late_reasons = _late_usable(
            first_depth,
            last_book,
            late,
            corpus_end,
            late_bbo + late_depth,
            source_ok,
        )
        classification = _classification(pre_ok, night_ok)
        reason_codes = sorted(set(pre_reasons + night_reasons))
        if not q["book_evidence_rows"] and q["fill_count"]:
            reason_codes.append("FILLS_ONLY")
        if q["book_evidence_rows"] and int(row_counts.get("depth_snapshots", 0)) < 10:
            reason_codes.append("INSUFFICIENT_DEPTH")
        if (
            regime_id in {"peru_first_round", "hungary_election"}
            and first_book is not None
            and first_book >= datetime(2026, 4, 13, 19, 42, 26, 600000, tzinfo=UTC)
        ):
            reason_codes.append("V2_ONLY_LATE_START")
        if quality[regime_id].get("source_splices"):
            reason_codes.append("PMXT_V1_V2_SPLICE_PRESENT")
        descriptive_only = classification == "NEITHER" and bool(
            q["book_evidence_rows"] or q["fill_count"]
        )
        usability_rows.append(
            {
                "regime_id": regime_id,
                "event_family": event["event_family"],
                "condition_id": row["condition_id"],
                "token_id": token_id,
                "market/question": row["question"],
                "pre_election_usable": pre_ok,
                "election_night_usable": night_ok,
                "late_count_usable": late_ok,
                "classification": classification,
                "descriptive_only": descriptive_only,
                "reason_codes": ";".join(sorted(set(reason_codes))),
                "late_count_reason_codes": ";".join(sorted(set(late_reasons))),
            }
        )

    # Populate predictive usability on event-window rows from the deterministic matrix.
    usable_index = {
        (r["regime_id"], r["token_id"]): r
        for r in usability_rows
    }
    for item in window_rows:
        u = usable_index[(item["regime_id"], item["token_id"])]
        if item["regime"] == "PRE_ELECTION":
            item["usable"] = u["pre_election_usable"]
            item["reason"] = "" if item["usable"] else u["reason_codes"]
        elif item["regime"] == "ACTIVE_RESULTS":
            item["usable"] = u["election_night_usable"]
            item["reason"] = "" if item["usable"] else u["reason_codes"]
        elif item["regime"] == "LATE_COUNT":
            item["usable"] = u["late_count_usable"]
            item["reason"] = "" if item["usable"] else u["late_count_reason_codes"]

    from predictions_cup.historical.event_reaction import scan_snapshot_reactions

    reaction_index = scan_snapshot_reactions(corpus_parent, timeline)
    reaction_rows: list[dict[str, Any]] = []
    for identity_row in identity:
        key = (identity_row["regime_id"], identity_row["token_id"])
        diagnostic = reaction_index.get(key)
        if diagnostic is None:
            continue
        reaction_rows.append(
            {
                "regime_id": identity_row["regime_id"],
                "event_family": EVENT_FAMILY[identity_row["regime_id"]],
                "market_id": identity_row["market_id"],
                "condition_id": identity_row["condition_id"],
                "token_id": identity_row["token_id"],
                "outcome": identity_row["outcome"],
                "question": identity_row["question"],
                **diagnostic,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "market_coverage.csv", market_rows)
    _write_csv(output_dir / "event_window_coverage.csv", window_rows)
    _write_csv(output_dir / "usability_matrix.csv", usability_rows)
    _write_csv(output_dir / "reaction_diagnostics.csv", reaction_rows)
    _write_csv(output_dir / "event_sources.csv", _event_source_rows(timeline))

    classification_counts = Counter(r["classification"] for r in usability_rows)
    descriptive_only_count = sum(bool(r["descriptive_only"]) for r in usability_rows)
    by_election: dict[str, Counter[str]] = defaultdict(Counter)
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for u, m in zip(usability_rows, market_rows, strict=True):
        by_election[u["regime_id"]][u["classification"]] += 1
        by_family[u["event_family"]][u["classification"]] += 1
        by_source[m["book_source_versions"] or "<none>"][u["classification"]] += 1

    summary = {
        "experiment_id": EXPERIMENT_ID,
        "package_version": PACKAGE_VERSION,
        "regime_package_sha256": package_sha,
        "data001_verification": verification,
        "coverage_policy": {
            "pre_min_lead_hours": PRE_MIN_LEAD_HOURS,
            "night_min_span_hours": NIGHT_MIN_SPAN_HOURS,
            "late_min_span_hours": LATE_MIN_SPAN_HOURS,
            "min_book_observations": MIN_BOOK_OBSERVATIONS,
            "max_regime_wide_source_silence_seconds": MAX_REGIME_WIDE_SOURCE_SILENCE_SECONDS,
            "gap_rule": (
                "Token-level update silence is reported but is not treated as recorder loss "
                "because unchanged book state can legitimately persist. Only regime-wide source "
                "silence over "
                "the threshold hard-fails usability."
            ),
        },
        "markets_tokens_total": len(usability_rows),
        "markets_with_book": sum(m["book_available"] == "true" for m in market_rows),
        "markets_with_fills": sum(m["fills_available"] == "true" for m in market_rows),
        "classification_counts": dict(sorted(classification_counts.items())),
        "descriptive_only_count": descriptive_only_count,
        "reaction_diagnostic_tokens": len(reaction_rows),
        "usable_by_election": {k: dict(sorted(v.items())) for k, v in sorted(by_election.items())},
        "usable_by_event_family": {
            k: dict(sorted(v.items())) for k, v in sorted(by_family.items())
        },
        "usable_by_source_version": {
            k: dict(sorted(v.items())) for k, v in sorted(by_source.items())
        },
        "source_version_limitations": {
            rid: {
                "source_versions": quality[rid].get("source_versions", []),
                "source_splices": quality[rid].get("source_splices", []),
                "missing_source_hours": quality[rid].get("missing_source_hours", []),
                "regime_wide_max_silence_seconds": quality[rid].get(
                    "regime_wide_max_silence_seconds"
                ),
                "regime_wide_material_silences": quality[rid].get(
                    "regime_wide_material_silences", 0
                ),
                "regime_wide_largest_silences": quality[rid].get(
                    "regime_wide_largest_silences", []
                ),
            }
            for rid in sorted(quality)
        },
        "event_family_independence_warning": (
            "The five rounds are not five independent election families: Colombia first "
            "round/runoff share COL_2026; Peru first round/runoff share PER_2026; "
            "Hungary is HUN_2026."
        ),
        "alpha_searched": False,
    }
    _write_json(output_dir / "coverage_summary.json", summary)
    _write_report(repo_root / "docs" / "experiments" / "EXPERIMENT_004A_EVENT_TIME_COVERAGE.md",
                  timeline, summary, usability_rows, market_rows)
    return summary


def _event_source_rows(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in timeline["events"]:
        for anchor_type, anchor in event["anchors"].items():
            if anchor is None:
                continue
            rows.append(
                {
                    "event_id": event["event_id"],
                    "regime_id": event["regime_id"],
                    "event_family": event["event_family"],
                    "country": event["country"],
                    "round": event["round"],
                    "anchor_type": anchor_type,
                    "local_timestamp": anchor["local_timestamp"],
                    "utc_timestamp": anchor["utc_timestamp"],
                    "timezone": event["timezone"],
                    "source_name": anchor["source_name"],
                    "source_url": anchor["source_url"],
                    "source_type": anchor["source_type"],
                    "precision": anchor["precision"],
                    "evidence_note": anchor["evidence_note"],
                    "confidence": anchor["confidence"],
                }
            )
        for anchor in event.get("additional_anchors", []):
            rows.append(
                {
                    "event_id": event["event_id"],
                    "regime_id": event["regime_id"],
                    "event_family": event["event_family"],
                    "country": event["country"],
                    "round": event["round"],
                    "anchor_type": anchor["anchor_type"],
                    "local_timestamp": anchor["local_timestamp"],
                    "utc_timestamp": anchor["utc_timestamp"],
                    "timezone": event["timezone"],
                    "source_name": anchor["source_name"],
                    "source_url": anchor["source_url"],
                    "source_type": anchor["source_type"],
                    "precision": anchor["precision"],
                    "evidence_note": anchor["evidence_note"],
                    "confidence": anchor["confidence"],
                }
            )
    return sorted(
        rows,
        key=lambda row: (row["regime_id"], row["utc_timestamp"], row["anchor_type"]),
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(row.get(k)) for k in fields})


def _rate_per_hour(count: int, observed_hours: float | None) -> float | None:
    if observed_hours is None or observed_hours <= 0:
        return None
    return round(count / observed_hours, 6)


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(
    path: Path,
    timeline: dict[str, Any],
    summary: dict[str, Any],
    usability: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> None:
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usability:
        by_regime[row["regime_id"]].append(row)
    market_by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        market_by_regime[row["regime_id"]].append(row)

    lines = [
        "# EXPERIMENT-004A — Event-Time Coverage & Regime Validation",
        "",
        "> Evidence validation only. No alpha search, parameter optimisation, strategy simulation, "
        "execution simulation, or P&L is performed by this package.",
        "",
        f"- Regime package: `{summary['package_version']}`",
        f"- Regime package SHA-256: `{summary['regime_package_sha256']}`",
        f"- DATA-001 verification: `{summary['data001_verification']['files_checked']} files / OK`",
        "- Historical venue: Polymarket only. No historical SIG observations are claimed.",
        "",
        "## Frozen coverage-sufficiency rule",
        "",
        f"PRE_ELECTION requires an initialized depth state at least {PRE_MIN_LEAD_HOURS:g}h before "
        f"poll close, book coverage through poll close, and at least {MIN_BOOK_OBSERVATIONS} book "
        "observations in the pre-election window. ACTIVE_RESULTS requires initialization no later "
        f"than result onset and coverage for the full active window when it is shorter than "
        f"{NIGHT_MIN_SPAN_HOURS:g}h, otherwise at least the first {NIGHT_MIN_SPAN_HOURS:g}h. "
        "Token-level silence is descriptive because unchanged state can persist; a regime-wide "
        f"source silence longer than {MAX_REGIME_WIDE_SOURCE_SILENCE_SECONDS/60:g} minutes "
        "hard-fails usability.",
        "",
        "The rule is fixed independently of all later alpha outcomes.",
        "",
    ]
    names = {
        "colombia_first_round": "Colombia first round",
        "colombia_runoff": "Colombia runoff",
        "peru_first_round": "Peru first round",
        "peru_runoff": "Peru runoff",
        "hungary_election": "Hungary parliamentary election",
    }
    event_index = {e["regime_id"]: e for e in timeline["events"]}
    for regime_id in [
        "colombia_first_round", "colombia_runoff", "peru_first_round",
        "peru_runoff", "hungary_election"
    ]:
        event = event_index[regime_id]
        anchors = event["anchors"]
        rows = by_regime[regime_id]
        counts = Counter(r["classification"] for r in rows)
        books = market_by_regime[regime_id]
        lines.extend([
            f"## {names[regime_id]}",
            "",
            "| Anchor | Local | UTC | Evidence |",
            "|---|---|---|---|",
            f"| Poll close | {anchors['poll_close']['local_timestamp']} | "
            f"{anchors['poll_close']['utc_timestamp']} | {anchors['poll_close']['source_name']} |",
            "| First meaningful results | "
            f"{anchors['first_meaningful_results']['local_timestamp']} | "
            f"{anchors['first_meaningful_results']['utc_timestamp']} | "
            f"{anchors['first_meaningful_results']['source_name']} |",
            f"| ACTIVE_RESULTS end | {anchors['active_results_end']['local_timestamp']} | "
            f"{anchors['active_results_end']['utc_timestamp']} | "
            f"{anchors['active_results_end']['source_name']} |",
            f"| LATE_COUNT end | {anchors['late_count_end']['local_timestamp']} | "
            f"{anchors['late_count_end']['utc_timestamp']} | "
            f"{anchors['late_count_end']['source_name']} |",
            "",
            f"DATA-001 window: `{event['corpus_window_start_utc']}` → "
            f"`{event['corpus_window_end_utc']}`. Candidate tokens: {len(rows)}; "
            f"tokens with book evidence: {sum(r['book_available']=='true' for r in books)}.",
            "",
            f"Usability: BOTH {counts['BOTH']}; PRE_ELECTION_ONLY {counts['PRE_ELECTION_ONLY']}; "
            f"ELECTION_NIGHT_ONLY {counts['ELECTION_NIGHT_ONLY']}; NEITHER {counts['NEITHER']}.",
            "",
        ])
        if event.get("additional_anchors"):
            for extra in event["additional_anchors"]:
                lines.append(
                    f"Special anchor: **{extra['anchor_type']}** at {extra['local_timestamp']} "
                    f"({extra['utc_timestamp']}). {extra['evidence_note']}"
                )
            lines.append("")
        lines.extend([
            "Sources:",
            f"- {anchors['poll_close']['source_name']}: {anchors['poll_close']['source_url']}",
            f"- {anchors['first_meaningful_results']['source_name']}: "
            f"{anchors['first_meaningful_results']['source_url']}",
            f"- {anchors['late_count_end']['source_name']}: "
            f"{anchors['late_count_end']['source_url']}",
            "",
        ])

    lines.extend([
        "## DATA-001 source/version limitations",
        "",
        "- Colombia first round, Colombia runoff and Peru runoff use PMXT V2 in the "
        "accepted corpus.",
        "- Peru first round and Hungary cross the accepted PMXT V1-preferred / V2-only splice. "
        "V2-only market state begins only at its real first observation; no backward fill "
        "is allowed.",
        "- Fills-only evidence never becomes book-usable. BBO changes before the first "
        "depth snapshot do not initialize a full book.",
        "- Crossed books, empty sides, same-millisecond ambiguity, material token gaps and "
        "fill/book overlap remain reported evidence; this task does not repair or "
        "interpolate them.",
        "",
        "## Independence for downstream validation",
        "",
        "COL_2026 first round/runoff and PER_2026 first round/runoff are dependent event-family "
        "members, not independent elections. HUN_2026 is the third family. EXPERIMENT-004A.2 must "
        "therefore use whole-event/family holdouts rather than treating five rounds as five "
        "independent samples.",
        "",
        "## Machine-readable artefacts",
        "",
        "- `data/experiments/experiment_004a/event_timeline.json`",
        "- `data/experiments/experiment_004a/regime_definitions.json`",
        "- `data/experiments/experiment_004a/market_coverage.csv`",
        "- `data/experiments/experiment_004a/event_window_coverage.csv`",
        "- `data/experiments/experiment_004a/usability_matrix.csv`",
        "- `data/experiments/experiment_004a/coverage_summary.json`",
        "- `data/experiments/experiment_004a/reaction_diagnostics.csv`",
        "- `data/experiments/experiment_004a/event_sources.csv`",
        "",
        "Reaction diagnostics use exact DATA-001 depth snapshots and the already-frozen "
        "factual result clock. Thresholds refer to movement toward the token's last observed "
        "DATA-001 side; they are descriptive and are not treated as contract settlement or "
        "used to define regime boundaries.",
        "",
        "Regime boundaries are immutable for downstream alpha work. A factual correction "
        "requires a new package version and explicit downstream-impact note.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--corpus", type=Path, required=True,
                        help="DATA-001 historical_replay_corpus parent directory")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/experiments/experiment_004a"),
    )
    parser.add_argument("--batch-size", type=int, default=262_144)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    output = args.output
    if not output.is_absolute():
        output = repo_root / output
    summary = build_outputs(
        repo_root=repo_root,
        corpus_parent=args.corpus.resolve(),
        output_dir=output,
        batch_size=args.batch_size,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
