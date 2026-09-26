"""Build and validate the DATA-001 historical replay corpus.

Inputs (both local directories, e.g. Kaggle ``/kaggle/input/...`` mounts):

* ``orderbooks``: exact-token PMXT hourly extracts produced by ``acquire`` laid out as
  ``<FAMILY>/date=YYYY-MM-DD/hour=HH/events.parquet`` (raw archive columns preserved);
* ``fills``: the PolyLeviathan export directory containing ``fills_<FAMILY>.parquet`` and
  ``markets_<FAMILY>.parquet``.

Output (outside Git)::

    <output>/schema_version=1/<regime>/books/{depth_snapshots,book_changes,trades,
                                              tick_size_changes,rejects}/date=YYYY-MM-DD/part-0.parquet
    <output>/schema_version=1/<regime>/fills/date=YYYY-MM-DD/part-0.parquet
    <output>/schema_version=1/{corpus_manifest.json,corpus_quality.json,market_identity.csv}

``<regime>/books`` is directly consumable by BUILD-005 ``load_polymarket_capture``.
Canonical content never embeds wall-clock build time, so identical inputs and configuration
produce logically identical output.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from predictions_cup.historical import pmxt
from predictions_cup.historical.fills import FILL_COLUMNS, FILL_SCHEMA, normalize_fills
from predictions_cup.historical.quality import RegimeQuality
from predictions_cup.historical.regimes import (
    FAMILY_MARKET_FAMILIES,
    REGIMES,
    SCHEMA_VERSION,
    Regime,
    pmxt_version_for_hour,
)

BOOK_STREAMS = ("depth_snapshots", "book_changes", "trades", "tick_size_changes", "rejects")
_V2_COLUMNS = [
    "timestamp_received",
    "timestamp",
    "market",
    "event_type",
    "asset_id",
    "bids",
    "asks",
    "price",
    "size",
    "side",
    "best_bid",
    "best_ask",
    "fee_rate_bps",
    "transaction_hash",
    "old_tick_size",
    "new_tick_size",
]
_V1_COLUMNS = ["timestamp_received", "timestamp_created_at", "market_id", "update_type", "data"]
IDENTITY_FIELDS = [
    "regime_id",
    "country",
    "round",
    "market_id",
    "condition_id",
    "token_id",
    "outcome",
    "event_id",
    "question",
    "market_family",
    "source",
    "first_observation_time",
    "last_observation_time",
    "book_available",
    "fills_available",
    "book_evidence_rows",
    "fill_rows",
]

TIMESTAMP_SEMANTICS: dict[str, dict[str, str]] = {
    "PMXT_V2": {
        "observed_at": "HISTORICAL_PROXY_ARCHIVE_RECEIVE_TIME: PMXT V2 timestamp_received (ms), "
        "when the archive's recorder received the message; used as observable time",
        "source_timestamp": "VENUE_EVENT_TIME: PMXT V2 timestamp (ms) from the Polymarket "
        "market channel; retained for diagnosis, never used for ordering",
    },
    "PMXT_V1": {
        "observed_at": "HISTORICAL_PROXY_ARCHIVE_RECEIVE_TIME: PMXT V1 timestamp_received (ms); "
        "payload timestamp equals it to the millisecond (third-party recorder receive time)",
        "source_timestamp": "NULL: PMXT V1 carries no venue event time; timestamp_created_at "
        "is a later archive write time and is not used",
    },
    "POLYLEVIATHAN_FILLS": {
        "source_timestamp": "BLOCK_TIME: on-chain block timestamp (1 s resolution)",
        "observed_at": "BLOCK_TIME_PROXY: equal to block time; no historical receipt time exists",
    },
}
EVIDENCE_GRADES: dict[str, str] = {
    "books/depth_snapshots": "BOOK_SNAPSHOT: full L2 depth as published in a venue book "
    "message; exact state at that observable time",
    "books/book_changes": "PRICE_ONLY: per-level price_change deltas carrying post-change best "
    "bid/ask (L1). Same-millisecond ties have no recoverable order; not exact event replay",
    "books/trades": "TRADE_FILL: venue last_trade_price prints delivered on the market channel",
    "books/tick_size_changes": "venue tick-size change notices (metadata, not replayed)",
    "fills": "TRADE_FILL: on-chain fills (PolyLeviathan export), separate evidence stream",
}
KNOWN_LIMITATIONS = [
    "PendulumFlow classifies PMXT V1/V2 as snapshot grade: millisecond receive timestamps tie "
    "and the original exchange order within a tie is unrecoverable; archive export order is "
    "not stable. Ties are ordered deterministically by row content, which is arbitrary, and "
    "every ambiguous same-millisecond BBO group is counted in corpus_quality.json.",
    "No stream is graded FULL_EVENT_REPLAY. Queue position, cancellation timing, maker fill "
    "probability and passive quote survival cannot be reconstructed.",
    "observed_at is an archive-receive proxy, not a time at which this project possessed the "
    "data; recorder-to-venue latency is unknown.",
    "PMXT V1 missed part of the live market universe (PendulumFlow documentation); selected "
    "markets absent from the archive are listed with book_available=false.",
    "Coverage gaps are silence in the recorded stream and cannot be distinguished from "
    "genuinely quiet markets except by regime-wide silence statistics.",
    "No state before a regime window is carried in; the first depth snapshot for each token "
    "arrives inside the window and BBO changes before it are counted.",
    "BUILD-005 de-duplicates hashed trades on (token_id, transaction_hash); multiple venue "
    "trade prints sharing a transaction collapse to the first when replayed.",
    "Fill price/size are source-supplied binary floats rendered as shortest round-trip text; "
    "fill side is exporter text and is not aggressor direction.",
]


@dataclass(frozen=True, slots=True)
class Candidate:
    family: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    event_id: str
    question: str
    market_family: str


def load_candidates(fills_root: Path, family: str) -> tuple[list[Candidate], list[dict[str, str]]]:
    """Candidate tokens from PolyLeviathan identities, filtered by market family."""
    table = pq.read_table(fills_root / f"markets_{family}.parquet")
    allowed = FAMILY_MARKET_FAMILIES[family]
    candidates: list[Candidate] = []
    excluded: list[dict[str, str]] = []
    for row in table.to_pylist():
        if row["market_family"] not in allowed:
            continue
        cid = row.get("condition_id") or ""
        if len(cid) != 66 or not cid.startswith("0x"):
            excluded.append(
                {
                    "family": family,
                    "market_id": str(row.get("market_id")),
                    "question": str(row.get("question")),
                    "reason": "missing_or_invalid_condition_id_in_identity_file",
                }
            )
            continue
        labels = _outcome_labels(row.get("outcome_labels"))
        for token_field, index in (("yes_token_id", 0), ("no_token_id", 1)):
            token = row.get(token_field)
            if not token:
                continue
            candidates.append(
                Candidate(
                    family=family,
                    market_id=str(row["market_id"]),
                    condition_id=cid,
                    token_id=str(token),
                    outcome=labels[index] if len(labels) > index else token_field[:-9].upper(),
                    event_id=str(row.get("event_id") or ""),
                    question=str(row.get("question") or ""),
                    market_family=str(row["market_family"]),
                )
            )
    candidates.sort(key=lambda c: (c.market_id, c.token_id))
    return candidates, excluded


def build_corpus(
    *,
    orderbooks_root: Path,
    fills_root: Path,
    output_root: Path,
    regimes: tuple[Regime, ...] = REGIMES,
    material_gap: timedelta = timedelta(minutes=5),
    pipeline_commit: str | None = None,
) -> dict[str, Any]:
    root = output_root / f"schema_version={SCHEMA_VERSION}"
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "dataset_id": "DATA-001-historical-replay-corpus",
        "schema_version": SCHEMA_VERSION,
        "pipeline_commit": pipeline_commit,
        "configuration": {
            "material_gap_seconds": material_gap.total_seconds(),
            "family_market_families": {k: sorted(v) for k, v in FAMILY_MARKET_FAMILIES.items()},
            "pmxt_routing": "PMXT_V1 before 2026-04-13T19:00:00Z, PMXT_V2 from then",
        },
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "evidence_grades": EVIDENCE_GRADES,
        "known_limitations": KNOWN_LIMITATIONS,
        "regimes": [],
        "source_files": [],
        "identity_exclusions": [],
        "output_files": [],
    }
    quality_out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "regimes": []}
    identity_rows: list[dict[str, Any]] = []
    seen_sources: dict[str, dict[str, Any]] = {}
    families_done: set[str] = set()

    for regime in regimes:
        candidates, excluded = load_candidates(fills_root, regime.family)
        if regime.family not in families_done:
            manifest["identity_exclusions"].extend(excluded)
            families_done.add(regime.family)
        tokens = frozenset(c.token_id for c in candidates)
        quality = RegimeQuality(regime_id=regime.regime_id, material_gap=material_gap)
        regime_dir = root / regime.regime_id
        missing_hours: list[str] = []
        versions: set[str] = set()

        with _DailyWriters(regime_dir / "books", pmxt.SCHEMAS) as writers:
            for hour in _hours(regime.window_start, regime.window_end):
                path = (
                    orderbooks_root
                    / regime.family
                    / f"date={hour:%Y-%m-%d}"
                    / f"hour={hour:%H}"
                    / "events.parquet"
                )
                if not path.is_file():
                    missing_hours.append(f"{hour:%Y-%m-%dT%H}")
                    continue
                rel = path.relative_to(orderbooks_root).as_posix()
                version = pmxt_version_for_hour(hour)
                if rel not in seen_sources:
                    seen_sources[rel] = _file_record(path, rel, kind="pmxt_extract")
                    seen_sources[rel]["source_version"] = version
                    seen_sources[rel]["hour"] = f"{hour:%Y-%m-%dT%H}"
                versions.add(version)
                columns = _V2_COLUMNS if version == "PMXT_V2" else _V1_COLUMNS
                table = pq.ParquetFile(path).read(columns=columns)
                _check_extract_version(path, version)
                normalized = pmxt.normalize_extract(
                    table,
                    source_version=version,
                    token_ids=tokens,
                    window_start=regime.window_start,
                    window_end=regime.window_end,
                )
                deduped: dict[str, pa.Table] = {}
                dropped: dict[str, int] = {}
                for stream in BOOK_STREAMS:
                    deduped[stream], dropped[stream] = pmxt.sort_and_dedupe(
                        stream, normalized.tables[stream]
                    )
                    writers.write(stream, hour, deduped[stream])
                quality.pipeline_counts["books"].update(normalized.counts)
                quality.add_book_hour(deduped, dropped)

        fill_table = pq.read_table(
            fills_root / f"fills_{regime.family}.parquet", columns=FILL_COLUMNS
        )
        fills_rel = f"fills_{regime.family}.parquet"
        markets_rel = f"markets_{regime.family}.parquet"
        for rel in (fills_rel, markets_rel):
            if rel not in seen_sources:
                seen_sources[rel] = _file_record(fills_root / rel, rel, kind="polyleviathan")
        fills, fill_counts = normalize_fills(
            fill_table,
            token_ids=tokens,
            window_start=regime.window_start,
            window_end=regime.window_end,
        )
        del fill_table
        quality.pipeline_counts["fills"].update(fill_counts)
        with _DailyWriters(regime_dir, {"fills": FILL_SCHEMA}) as writers:
            for day, part in _split_by_day(fills):
                writers.write("fills", day, part)
        quality.add_fills(fills)

        token_reports = []
        regime_identities = []
        for cand in candidates:
            report = quality.token_report(cand.token_id)
            book_rows = report["book_evidence_rows"]
            token_reports.append({**report, "market_id": cand.market_id,
                                  "condition_id": cand.condition_id})
            regime_identities.append(
                {
                    "regime_id": regime.regime_id,
                    "country": regime.country,
                    "round": regime.round,
                    "market_id": cand.market_id,
                    "condition_id": cand.condition_id,
                    "token_id": cand.token_id,
                    "outcome": cand.outcome,
                    "event_id": cand.event_id,
                    "question": cand.question,
                    "market_family": cand.market_family,
                    "source": ";".join(sorted(versions)) if book_rows else "",
                    "first_observation_time": report["first_timestamp"] or "",
                    "last_observation_time": report["last_timestamp"] or "",
                    "book_available": book_rows > 0,
                    "fills_available": report["fill_count"] > 0,
                    "book_evidence_rows": book_rows,
                    "fill_rows": report["fill_count"],
                }
            )
        identity_rows.extend(regime_identities)
        observed = [r for r in regime_identities if r["book_available"]]
        regime_report = quality.regime_report()
        regime_report.update(
            {
                "window_start": regime.window_start.isoformat(),
                "window_end_exclusive": regime.window_end.isoformat(),
                "candidate_markets": len({c.market_id for c in candidates}),
                "candidate_tokens": len(candidates),
                "markets_with_book_evidence": len({r["market_id"] for r in observed}),
                "tokens_with_book_evidence": len(observed),
                "tokens_with_fills": sum(1 for r in regime_identities if r["fills_available"]),
                "missing_source_hours": missing_hours,
                "source_versions": sorted(versions),
            }
        )
        quality_out["regimes"].append({**regime_report, "tokens": token_reports})
        manifest["regimes"].append(
            {
                "regime_id": regime.regime_id,
                "country": regime.country,
                "round": regime.round,
                "family": regime.family,
                "election_date": regime.election_date.isoformat(),
                "window_start": regime.window_start.isoformat(),
                "window_end_exclusive": regime.window_end.isoformat(),
                "initialization_data": "NONE: no rows before window_start are included",
                "source_versions": sorted(versions),
                "missing_source_hours": missing_hours,
                "selected_token_ids": sorted(r["token_id"] for r in observed),
                "pipeline_counts": regime_report["pipeline_counts"],
            }
        )

    manifest["source_files"] = [seen_sources[k] for k in sorted(seen_sources)]
    identity_path = root / "market_identity.csv"
    identity_path.write_bytes(render_identity_csv(identity_rows))
    quality_path = root / "corpus_quality.json"
    quality_path.write_bytes(_json_bytes(quality_out))
    manifest["output_files"] = [
        _file_record(path, path.relative_to(root).as_posix(), kind="corpus", with_rows=True)
        for path in sorted(root.rglob("*.parquet"))
    ]
    manifest["output_files"].extend(
        _file_record(p, p.name, kind="corpus_metadata") for p in (identity_path, quality_path)
    )
    manifest["totals"] = _totals(manifest)
    (root / "corpus_manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def validate_corpus(output_root: Path) -> dict[str, Any]:
    """Re-verify hashes, row counts, schemas and window containment of a produced corpus."""
    root = output_root / f"schema_version={SCHEMA_VERSION}"
    manifest = json.loads((root / "corpus_manifest.json").read_text(encoding="utf-8"))
    windows = {
        r["regime_id"]: (datetime.fromisoformat(r["window_start"]),
                         datetime.fromisoformat(r["window_end_exclusive"]))
        for r in manifest["regimes"]
    }
    problems: list[str] = []
    checked = 0
    for record in manifest["output_files"]:
        path = root / record["path"]
        if not path.is_file():
            problems.append(f"missing {record['path']}")
            continue
        actual = _file_record(path, record["path"], kind=record["kind"],
                              with_rows="rows" in record)
        for key in ("bytes", "sha256", "rows"):
            if key in record and actual.get(key) != record[key]:
                problems.append(f"{record['path']}: {key} mismatch")
        if path.suffix == ".parquet":
            regime_id, family_dir = record["path"].split("/")[:2]
            stream = "fills" if family_dir == "fills" else record["path"].split("/")[2]
            schema = FILL_SCHEMA if stream == "fills" else pmxt.SCHEMAS[stream]
            file_schema = pq.read_schema(path)
            if not file_schema.equals(schema):
                problems.append(f"{record['path']}: schema differs from contract")
            time_field = "observed_at" if stream == "fills" else pmxt.STREAM_TIME_FIELD[stream]
            times = pq.read_table(path, columns=[time_field])[time_field]
            if len(times):
                start, end = windows[regime_id]
                lo, hi = pc.min(times).as_py(), pc.max(times).as_py()
                if lo < start or hi >= end:
                    problems.append(f"{record['path']}: rows outside regime window")
                ordered = pc.all(pc.greater_equal(times.slice(1), times.slice(0, len(times) - 1)))
                if len(times) > 1 and not ordered.as_py():
                    problems.append(f"{record['path']}: not ordered by {time_field}")
        checked += 1
    return {"files_checked": checked, "problems": problems, "ok": not problems}


def render_identity_csv(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=IDENTITY_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: (r["regime_id"], r["market_id"], r["token_id"])):
        writer.writerow({k: _csv_value(row[k]) for k in IDENTITY_FIELDS})
    return buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------------------------


class _DailyWriters:
    """One ZSTD Parquet file per stream per UTC day; one row group per source hour."""

    def __init__(self, root: Path, schemas: dict[str, pa.Schema]) -> None:
        self.root = root
        self.schemas = schemas
        self._open: dict[str, tuple[str, pq.ParquetWriter]] = {}

    def __enter__(self) -> _DailyWriters:
        return self

    def __exit__(self, *exc: object) -> None:
        for _, writer in self._open.values():
            writer.close()
        self._open.clear()

    def write(self, stream: str, hour: datetime, table: pa.Table) -> None:
        if table.num_rows == 0:
            return
        day = f"{hour:%Y-%m-%d}"
        current = self._open.get(stream)
        if current is not None and current[0] != day:
            current[1].close()
            current = None
        if current is None:
            path = self.root / stream / f"date={day}" / "part-0.parquet"
            if stream == "fills":
                path = self.root / "fills" / f"date={day}" / "part-0.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            current = (
                day,
                pq.ParquetWriter(
                    path,
                    self.schemas[stream],
                    compression="zstd",
                    use_dictionary=True,
                    write_statistics=True,
                ),
            )
            self._open[stream] = current
        current[1].write_table(table.cast(self.schemas[stream]))


def _hours(start: datetime, end: datetime) -> Iterator[datetime]:
    hour = start
    while hour < end:
        yield hour
        hour += timedelta(hours=1)


def _split_by_day(table: pa.Table) -> Iterator[tuple[datetime, pa.Table]]:
    if table.num_rows == 0:
        return
    days = pc.floor_temporal(table["observed_at"], unit="day")
    for day in sorted(set(days.to_pylist())):
        yield day, table.filter(pc.equal(days, pa.scalar(day, days.type)))


def _check_extract_version(path: Path, version: str) -> None:
    schema = pq.read_schema(path)
    if "source_version" not in schema.names:
        return
    values = pc.unique(pq.read_table(path, columns=["source_version"])["source_version"])
    if values.to_pylist() not in ([version], []):
        raise ValueError(f"{path}: extract source_version {values.to_pylist()} != {version}")


def _file_record(path: Path, rel: str, *, kind: str, with_rows: bool = False) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    record: dict[str, Any] = {
        "path": rel,
        "kind": kind,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
    if with_rows or path.suffix == ".parquet":
        record["rows"] = pq.ParquetFile(path).metadata.num_rows
    return record


def _totals(manifest: dict[str, Any]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    size = 0
    for record in manifest["output_files"]:
        size += record["bytes"]
        if record["kind"] != "corpus":
            continue
        parts = record["path"].split("/")
        stream = "fills" if parts[1] == "fills" else parts[2]
        totals[stream] = totals.get(stream, 0) + record.get("rows", 0)
    return {"rows_by_stream": dict(sorted(totals.items())), "corpus_bytes": size}


def _outcome_labels(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
