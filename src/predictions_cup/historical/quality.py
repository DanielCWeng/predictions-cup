"""Streaming DATA-001 coverage / data-quality accumulation.

Statistics are accumulated one normalized archive hour at a time so a regime never has to be
materialized in memory. Observation intervals are measured between distinct observation
instants (rows sharing a millisecond count once) and are exact at the source's millisecond
resolution (integer counters, not sketches).

"Book evidence" for coverage means depth snapshots plus BBO change rows for a token. A gap is
silence in that stream; it can be a genuinely quiet market or a recorder outage, and the two
cannot be distinguished from the bytes. Regime-wide silence (no token observed at all) is
reported separately because it is the stronger outage signal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

_PRICE = pa.decimal128(24, 10)
_US = 1_000_000


@dataclass(slots=True)
class _TokenStats:
    rows: Counter[str] = field(default_factory=Counter)
    first_at: int | None = None
    last_at: int | None = None
    unique_timestamps: int = 0
    intervals_ms: Counter[int] = field(default_factory=Counter)
    material_gaps: list[tuple[int, int]] = field(default_factory=list)
    first_snapshot_at: int | None = None
    changes_before_first_snapshot: int = 0
    crossed_snapshots: int = 0
    crossed_changes: int = 0
    empty_side_snapshots: int = 0
    empty_side_changes: int = 0
    ambiguous_same_timestamp_groups: int = 0
    source_after_observed: int = 0
    bid_levels: list[int] = field(default_factory=list)
    ask_levels: list[int] = field(default_factory=list)
    rejects: Counter[str] = field(default_factory=Counter)
    duplicates_removed: int = 0
    fills: int = 0
    fills_first_at: int | None = None
    fills_last_at: int | None = None
    fills_inside_book_span: int = 0


class RegimeQuality:
    """Accumulates coverage and quality evidence for one regime."""

    def __init__(self, *, regime_id: str, material_gap: timedelta) -> None:
        self.regime_id = regime_id
        self.material_gap_us = int(material_gap.total_seconds() * _US)
        self.tokens: dict[str, _TokenStats] = {}
        self.pipeline_counts: dict[str, Counter[str]] = {
            "books": Counter(),
            "fills": Counter(),
        }
        self._regime_last_at: int | None = None
        self.regime_gaps: list[tuple[int, int]] = []
        self.regime_max_gap_us = 0

    def _token(self, token: str) -> _TokenStats:
        stats = self.tokens.get(token)
        if stats is None:
            stats = self.tokens[token] = _TokenStats()
        return stats

    # -- books ---------------------------------------------------------------------------

    def add_book_hour(self, tables: dict[str, pa.Table], duplicates: dict[str, int]) -> None:
        snaps = tables["depth_snapshots"]
        changes = tables["book_changes"]
        for stream, table in tables.items():
            if stream == "rejects" or table.num_rows == 0:
                continue
            for token, count in _counts(table["token_id"]).items():
                self._token(token).rows[stream] += count
        for row in tables["rejects"].select(["token_id", "reason"]).to_pylist():
            self._token(row["token_id"] or "<unknown>").rejects[row["reason"]] += 1
        for stream, dropped in duplicates.items():
            if dropped:
                self.pipeline_counts["books"][f"duplicates_removed:{stream}"] += dropped

        self._snapshot_stats(snaps)
        self._change_stats(changes)
        evidence = pa.concat_tables(
            [
                pa.table({"token_id": snaps["token_id"], "t": _us(snaps["recorded_at"])}),
                pa.table({"token_id": changes["token_id"], "t": _us(changes["observed_at"])}),
            ]
        )
        self._interval_stats(evidence)
        for name in ("depth_snapshots", "book_changes", "trades"):
            table = tables[name]
            if table.num_rows == 0:
                continue
            time_field = "recorded_at" if name == "depth_snapshots" else "observed_at"
            late = pc.fill_null(pc.greater(table["source_timestamp"], table[time_field]), False)
            for token, count in _counts(table["token_id"].filter(late)).items():
                self._token(token).source_after_observed += count

    def _snapshot_stats(self, snaps: pa.Table) -> None:
        if snaps.num_rows == 0:
            return
        bid_n = pc.list_value_length(snaps["bids"]).to_pylist()
        ask_n = pc.list_value_length(snaps["asks"]).to_pylist()
        crossed = _crossed(snaps["best_bid"], snaps["best_ask"]).to_pylist()
        times = _us(snaps["recorded_at"]).to_pylist()
        for token, b, a, x, t in zip(
            snaps["token_id"].to_pylist(), bid_n, ask_n, crossed, times, strict=True
        ):
            stats = self._token(token)
            stats.bid_levels.append(b)
            stats.ask_levels.append(a)
            stats.crossed_snapshots += int(bool(x))
            stats.empty_side_snapshots += int(b == 0 or a == 0)
            if stats.first_snapshot_at is None or t < stats.first_snapshot_at:
                stats.first_snapshot_at = t

    def _change_stats(self, changes: pa.Table) -> None:
        if changes.num_rows == 0:
            return
        crossed = _crossed(changes["best_bid"], changes["best_ask"])
        empty = pc.or_(pc.is_null(changes["best_bid"]), pc.is_null(changes["best_ask"]))
        for token, count in _counts(changes["token_id"].filter(crossed)).items():
            self._token(token).crossed_changes += count
        for token, count in _counts(changes["token_id"].filter(empty)).items():
            self._token(token).empty_side_changes += count

        bbo = pc.binary_join_element_wise(
            pc.fill_null(changes["best_bid"], "-"), pc.fill_null(changes["best_ask"], "-"), "|"
        )
        grouped = (
            pa.table(
                {"token_id": changes["token_id"], "t": _us(changes["observed_at"]), "bbo": bbo}
            )
            .group_by(["token_id", "t"])
            .aggregate([("bbo", "count_distinct")])
        )
        ambiguous = grouped.filter(pc.greater(grouped["bbo_count_distinct"], 1))
        for token, count in _counts(ambiguous["token_id"]).items():
            self._token(token).ambiguous_same_timestamp_groups += count

        # Changes before the first depth snapshot cannot extend a known full book.
        known = [(tok, st.first_snapshot_at) for tok, st in self.tokens.items()
                 if st.first_snapshot_at is not None]
        lookup = pa.table(
            {
                "token_id": pa.array([k for k, _ in known], pa.string()),
                "first_snapshot": pa.array([v for _, v in known], pa.int64()),
            }
        )
        joined = pa.table(
            {"token_id": changes["token_id"], "t": _us(changes["observed_at"])}
        ).join(lookup, keys="token_id", join_type="left outer")
        before = pc.or_(
            pc.is_null(joined["first_snapshot"]),
            pc.fill_null(pc.less(joined["t"], joined["first_snapshot"]), False),
        )
        for token, count in _counts(joined["token_id"].filter(before)).items():
            self._token(token).changes_before_first_snapshot += count

    def _interval_stats(self, evidence: pa.Table) -> None:
        if evidence.num_rows == 0:
            return
        # Regime-wide silence uses distinct timestamps across all tokens.
        self._regime_gap(pc.unique(evidence["t"]).sort())

        # Intervals are measured between distinct observation instants per token; rows that
        # share a millisecond are one observation instant.
        evidence = (
            evidence.group_by(["token_id", "t"])
            .aggregate([])
            .sort_by([("token_id", "ascending"), ("t", "ascending")])
        )
        tok = evidence["token_id"].combine_chunks()
        times = evidence["t"].combine_chunks()

        # Carry-over interval from each token's previous batch, then advance its last time.
        bounds = evidence.group_by("token_id").aggregate([("t", "min"), ("t", "max")])
        for row in bounds.to_pylist():
            stats = self._token(row["token_id"])
            first, last = row["t_min"], row["t_max"]
            if stats.last_at is not None:
                self._record_interval(stats, stats.last_at, first)
            if stats.first_at is None:
                stats.first_at = first
            stats.last_at = last

        if len(times) > 1:
            same = pc.equal(tok.slice(1), tok.slice(0, len(tok) - 1))
            nxt = times.slice(1).filter(same)
            prv = times.slice(0, len(times) - 1).filter(same)
            delta = pc.subtract(nxt, prv)
            owner = tok.slice(1).filter(same)
            grouped = (
                pa.table({"token_id": owner, "ms": pc.divide(delta, 1000)})
                .group_by(["token_id", "ms"])
                .aggregate([("ms", "count")])
            )
            for row in grouped.to_pylist():
                self._token(row["token_id"]).intervals_ms[row["ms"]] += row["ms_count"]
            gap = pc.greater(delta, self.material_gap_us)
            for token, a, b in zip(
                owner.filter(gap).to_pylist(),
                prv.filter(gap).to_pylist(),
                nxt.filter(gap).to_pylist(),
                strict=True,
            ):
                self._token(token).material_gaps.append((a, b))

        for token, count in _counts(evidence["token_id"]).items():
            self._token(token).unique_timestamps += count

    def _record_interval(self, stats: _TokenStats, prev: int, t: int) -> None:
        delta = t - prev
        stats.intervals_ms[delta // 1000] += 1
        if delta > self.material_gap_us:
            stats.material_gaps.append((prev, t))

    def _regime_gap(self, unique_sorted: Any) -> None:
        if len(unique_sorted) == 0:
            return
        if self._regime_last_at is not None:
            first = unique_sorted[0].as_py()
            self._regime_candidate(self._regime_last_at, first)
        if len(unique_sorted) > 1:
            prv = unique_sorted.slice(0, len(unique_sorted) - 1)
            nxt = unique_sorted.slice(1)
            delta = pc.subtract(nxt, prv)
            self.regime_max_gap_us = max(self.regime_max_gap_us, pc.max(delta).as_py())
            gap = pc.greater(delta, self.material_gap_us)
            for a, b in zip(prv.filter(gap).to_pylist(), nxt.filter(gap).to_pylist(),
                            strict=True):
                self.regime_gaps.append((a, b))
        self._regime_last_at = unique_sorted[-1].as_py()

    def _regime_candidate(self, prev: int, t: int) -> None:
        delta = t - prev
        self.regime_max_gap_us = max(self.regime_max_gap_us, delta)
        if delta > self.material_gap_us:
            self.regime_gaps.append((prev, t))

    # -- fills ---------------------------------------------------------------------------

    def add_fills(self, fills: pa.Table) -> None:
        times = _us(fills["observed_at"]).to_pylist()
        for token, t in zip(fills["token_id"].to_pylist(), times, strict=True):
            stats = self._token(token)
            stats.fills += 1
            first, last = stats.fills_first_at, stats.fills_last_at
            stats.fills_first_at = t if first is None else min(first, t)
            stats.fills_last_at = t if last is None else max(last, t)
            if stats.first_at is not None and stats.last_at is not None:
                stats.fills_inside_book_span += int(stats.first_at <= t <= stats.last_at)

    # -- reporting -----------------------------------------------------------------------

    def token_report(self, token: str) -> dict[str, Any]:
        s = self._token(token)
        book_rows = s.rows["depth_snapshots"] + s.rows["book_changes"]
        gaps = sorted(s.material_gaps, key=lambda g: g[1] - g[0], reverse=True)
        return {
            "token_id": token,
            "evidence_grades": _grades(s),
            "first_timestamp": _iso(s.first_at),
            "last_timestamp": _iso(s.last_at),
            "row_counts": dict(sorted(s.rows.items())),
            "book_evidence_rows": book_rows,
            "unique_timestamps": s.unique_timestamps,
            "median_observation_interval_ms": _quantile(s.intervals_ms, 0.5),
            "p95_observation_interval_ms": _quantile(s.intervals_ms, 0.95),
            "max_observation_gap_seconds": _max_gap_seconds(s.intervals_ms),
            "material_gap_count": len(s.material_gaps),
            "material_gap_total_seconds": round(
                sum(b - a for a, b in s.material_gaps) / _US, 3
            ),
            "largest_material_gaps": [
                {"start": _iso(a), "end": _iso(b), "seconds": round((b - a) / _US, 3)}
                for a, b in gaps[:3]
            ],
            "first_depth_snapshot_at": _iso(s.first_snapshot_at),
            "book_changes_before_first_snapshot": s.changes_before_first_snapshot,
            "rejected_malformed_rows": dict(sorted(s.rejects.items())),
            "crossed_book_snapshots": s.crossed_snapshots,
            "crossed_bbo_changes": s.crossed_changes,
            "empty_side_snapshots": s.empty_side_snapshots,
            "empty_side_bbo_changes": s.empty_side_changes,
            "ambiguous_same_timestamp_bbo_groups": s.ambiguous_same_timestamp_groups,
            "source_timestamp_after_observed_rows": s.source_after_observed,
            "median_bid_levels": _median_int(s.bid_levels),
            "median_ask_levels": _median_int(s.ask_levels),
            "p95_bid_levels": _quantile_list(s.bid_levels, 0.95),
            "p95_ask_levels": _quantile_list(s.ask_levels, 0.95),
            "fill_count": s.fills,
            "fill_first_timestamp": _iso(s.fills_first_at),
            "fill_last_timestamp": _iso(s.fills_last_at),
            "fills_inside_book_time_span": s.fills_inside_book_span,
            "book_fill_overlap_ratio": (
                None if s.fills == 0 else round(s.fills_inside_book_span / s.fills, 4)
            ),
        }

    def regime_report(self) -> dict[str, Any]:
        gaps = sorted(self.regime_gaps, key=lambda g: g[1] - g[0], reverse=True)
        return {
            "regime_id": self.regime_id,
            "material_gap_threshold_seconds": self.material_gap_us / _US,
            "regime_wide_max_silence_seconds": round(self.regime_max_gap_us / _US, 3),
            "regime_wide_material_silences": len(self.regime_gaps),
            "regime_wide_largest_silences": [
                {"start": _iso(a), "end": _iso(b), "seconds": round((b - a) / _US, 3)}
                for a, b in gaps[:5]
            ],
            "pipeline_counts": {
                name: dict(sorted(counts.items()))
                for name, counts in self.pipeline_counts.items()
            },
        }


def _counts(values: Any) -> dict[str, int]:
    if len(values) == 0:
        return {}
    result: dict[str, int] = {}
    for item in pc.value_counts(values).to_pylist():
        result[item["values"]] = item["counts"]
    return result


def _us(values: Any) -> Any:
    return pc.cast(pc.cast(values, pa.timestamp("us", tz="UTC")), pa.int64())


def _crossed(bid: Any, ask: Any) -> Any:
    both = pc.and_(pc.is_valid(bid), pc.is_valid(ask))
    safe_bid = pc.cast(pc.if_else(both, bid, pa.scalar("0")), _PRICE)
    safe_ask = pc.cast(pc.if_else(both, ask, pa.scalar("1")), _PRICE)
    return pc.and_(both, pc.greater_equal(safe_bid, safe_ask))


def _grades(stats: _TokenStats) -> list[str]:
    grades = []
    if stats.rows["depth_snapshots"]:
        grades.append("BOOK_SNAPSHOT")
    if stats.rows["book_changes"]:
        grades.append("PRICE_ONLY")
    if stats.rows["trades"] or stats.fills:
        grades.append("TRADE_FILL")
    return grades


def _quantile(counter: Counter[int], q: float) -> int | None:
    total = sum(counter.values())
    if total == 0:
        return None
    rank = max(1, int(-(-q * total // 1)))  # nearest-rank
    seen = 0
    for value in sorted(counter):
        seen += counter[value]
        if seen >= rank:
            return value
    return max(counter)


def _max_gap_seconds(counter: Counter[int]) -> float | None:
    return None if not counter else round(max(counter) / 1000, 3)


def _median_int(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _quantile_list(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(-(-q * len(ordered) // 1)))
    return ordered[rank - 1]


def _iso(value: int | None) -> str | None:
    if value is None:
        return None
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)).isoformat()
