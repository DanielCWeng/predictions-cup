"""Acquire exact-token PMXT hourly extracts for the DATA-001 regimes.

Each unique archive hour is downloaded once to a temporary file, filtered in small batches for
every family whose regime window covers it, then deleted. Only raw rows for candidate tokens
are kept, with raw columns preserved. A per-(hour, family) JSONL checkpoint makes the stage
resumable; re-running skips completed work.

Filtering is by exact identity only: V2 by ``asset_id`` token, V1 by ``market_id`` condition
(V1 carries the token inside its JSON payload; the normalization stage filters tokens exactly).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from predictions_cup.historical.corpus import _hours, load_candidates
from predictions_cup.historical.regimes import REGIMES, pmxt_version_for_hour
from predictions_cup.historical.sources import pmxt_archive_url

_BATCH_ROWS = 25_000


def plan_hours(only: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Unique source hours -> families whose regime window needs them.

    ``only`` narrows the plan to the given ``YYYY-MM-DDTHH`` hour keys (re-acquiring single
    hours without re-running the whole plan); an hour outside every regime window is an error.
    """
    needed: dict[datetime, set[str]] = {}
    for regime in REGIMES:
        for hour in _hours(regime.window_start, regime.window_end):
            needed.setdefault(hour, set()).add(regime.family)
    if only is not None:
        unknown = only - {f"{hour:%Y-%m-%dT%H}" for hour in needed}
        if unknown:
            raise ValueError(f"hours outside every regime window: {sorted(unknown)}")
        needed = {h: f for h, f in needed.items() if f"{h:%Y-%m-%dT%H}" in only}
    return [
        {
            "hour": f"{hour:%Y-%m-%dT%H}",
            "source_version": pmxt_version_for_hour(hour),
            "families": sorted(families),
        }
        for hour, families in sorted(needed.items())
    ]


def acquire(
    *,
    fills_root: Path,
    output_root: Path,
    scratch: Path,
    worker: int = 0,
    workers: int = 1,
    only_hours: frozenset[str] | None = None,
) -> None:
    pa.set_memory_pool(pa.system_memory_pool())
    output_root.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    progress_path = output_root / f"_acquire_progress_w{worker}.jsonl"
    done = _load_done(output_root)
    filters = {}
    for family in sorted({r.family for r in REGIMES}):
        candidates, _ = load_candidates(fills_root, family)
        filters[family] = {
            "tokens": pa.array(sorted({c.token_id for c in candidates}), pa.string()),
            "conditions": pa.array(sorted({c.condition_id for c in candidates}), pa.string()),
        }
    todo = []
    for item in plan_hours(only_hours):
        families = [f for f in item["families"] if (item["hour"], f) not in done]
        if families:
            todo.append({**item, "families": families})
    for index, item in enumerate(todo):
        if index % workers != worker:
            continue
        url = pmxt_archive_url(item["hour"], item["source_version"])
        record: dict[str, Any] = {"hour": item["hour"], "url": url}
        try:
            local, size = _download(url, scratch)
        except FileNotFoundError:
            record["status"] = "SOURCE_MISSING"
            _append(progress_path, record)
            continue
        try:
            source_sha256 = _sha256(local)
            source_rows = pq.read_metadata(local).num_rows
            per_family = _filter_hour(local, item, filters, output_root)
            record.update(
                status="DONE", source_bytes=size, source_sha256=source_sha256,
                source_rows=source_rows, per_family=per_family,
            )
        finally:
            local.unlink(missing_ok=True)
        _append(progress_path, record)


def _filter_hour(
    local: Path,
    item: dict[str, Any],
    filters: dict[str, dict[str, pa.Array]],
    output_root: Path,
) -> dict[str, Any]:
    source = pq.ParquetFile(local)
    is_v2 = item["source_version"] == "PMXT_V2"
    writers: dict[str, pq.ParquetWriter] = {}
    paths = {
        fam: output_root / fam / f"date={item['hour'][:10]}" / f"hour={item['hour'][11:]}"
        / "events.parquet"
        for fam in item["families"]
    }
    try:
        for batch in source.iter_batches(batch_size=_BATCH_ROWS, use_threads=False):
            for family in item["families"]:
                if is_v2:
                    mask = pc.is_in(batch["asset_id"], value_set=filters[family]["tokens"])
                else:
                    mask = pc.is_in(batch["market_id"], value_set=filters[family]["conditions"])
                part = batch.filter(mask)
                if part.num_rows == 0:
                    continue
                if is_v2:
                    part = part.set_column(
                        part.schema.get_field_index("market"),
                        "market",
                        pc.cast(part["market"], pa.string()),
                    )
                part = pa.Table.from_batches([part])
                part = part.append_column("research_family", pa.array([family] * part.num_rows))
                part = part.append_column(
                    "source_version", pa.array([item["source_version"]] * part.num_rows)
                )
                if family not in writers:
                    paths[family].parent.mkdir(parents=True, exist_ok=True)
                    writers[family] = pq.ParquetWriter(
                        str(paths[family]) + ".unsorted", part.schema
                    )
                writers[family].write_table(part)
    finally:
        for writer in writers.values():
            writer.close()

    stats: dict[str, Any] = {}
    for family in item["families"]:
        if family not in writers:
            stats[family] = {"rows": 0}
            continue
        unsorted = Path(str(paths[family]) + ".unsorted")
        table = pq.ParquetFile(unsorted).read()
        order = pc.sort_indices(table, sort_keys=[("timestamp_received", "ascending")])
        with pq.ParquetWriter(str(paths[family]) + ".tmp", table.schema,
                              compression="zstd") as writer:
            for start in range(0, table.num_rows, 100_000):
                writer.write_table(table.take(order[start:start + 100_000]))
        rows = table.num_rows
        del table, order
        unsorted.unlink()
        os.replace(str(paths[family]) + ".tmp", paths[family])
        stats[family] = {"rows": rows, "sha256": _sha256(paths[family])}
    return stats


def _download(url: str, scratch: Path, attempts: int = 4) -> tuple[Path, int]:
    dest = scratch / url.rsplit("/", 1)[-1]
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                expected = int(response.headers.get("Content-Length", -1))
                with open(str(dest) + ".part", "wb") as handle:
                    shutil.copyfileobj(response, handle, length=8 << 20)
            size = os.path.getsize(str(dest) + ".part")
            if expected >= 0 and size != expected:
                raise OSError(f"size mismatch {size} != {expected}")
            os.replace(str(dest) + ".part", dest)
            return dest, size
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(url) from exc
            error = exc
        except OSError as exc:
            error = exc
        time.sleep(min(60, 5 * 2**attempt))
    raise OSError(f"download failed after {attempts} attempts: {error}")


def _load_done(output_root: Path) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    for path in output_root.glob("_acquire_progress_w*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("status") == "DONE":
                done.update((record["hour"], fam) for fam in record["per_family"])
    return done


def _append(path: Path, record: dict[str, Any]) -> None:
    record["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
