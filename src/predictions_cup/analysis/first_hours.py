"""Generate an immediate first-hours forensic report from launch capture artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from predictions_cup.analysis.cross_venue import analyze_direct_cross_venue
from predictions_cup.analysis.live_diag import analyze_live_diagnostics
from predictions_cup.analysis.microstructure import analyze_sig_microstructure
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDirection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CAPTURE-001 first-hours forensic report")
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="SIG research root or launch root",
    )
    parser.add_argument("--output", type=Path, required=True, help="Report output directory")
    parser.add_argument("--polymarket-root", type=Path)
    parser.add_argument("--execution-journal", type=Path)
    parser.add_argument(
        "--live-learn-outcomes",
        type=Path,
        help="LIVE-LEARN append-only outcome journal for actual maker economics",
    )
    parser.add_argument(
        "--shadow-journal",
        type=Path,
        help="SHADOW-002 append-only event journal for canonical inventory history",
    )
    parser.add_argument(
        "--inventory-limit",
        type=float,
        help="Optional explicit per-market inventory limit for recycling diagnostics",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Accepted mapping JSON for direct cross-venue diagnostics",
    )
    return parser.parse_args()


def _files(root: Path, stream: str) -> tuple[Path, ...]:
    path = root / stream
    if not path.exists():
        return ()
    return tuple(sorted(path.rglob("*.parquet")))


def _scanner(root: Path, stream: str, columns: tuple[str, ...]) -> Any | None:
    files = _files(root, stream)
    if not files:
        return None
    dataset = ds.dataset([str(path) for path in files], format="parquet")
    available = [column for column in columns if column in dataset.schema.names]
    return dataset.scanner(columns=available, batch_size=65_536)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p50": None, "p90": None, "p99": None}
    array = np.asarray(values, dtype=float)
    p10, p50, p90, p99 = np.percentile(array, [10, 50, 90, 99])
    return {
        "p10": float(p10),
        "p50": float(p50),
        "p90": float(p90),
        "p99": float(p99),
    }


def _analyse_sig(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, int]], dict[str, float]]:
    counts: Counter[str] = Counter()
    exchange_counts: dict[str, Counter[str]] = defaultdict(Counter)
    spreads: list[float] = []
    trade_sizes: list[float] = []
    bbo_rows = 0
    bbo_available = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    latest_mid: dict[str, tuple[datetime, float]] = {}

    scanner = _scanner(
        root,
        "normalized_events",
        (
            "event_type",
            "exchange_id",
            "observed_at",
            "price",
            "quantity",
            "best_bid",
            "best_ask",
            "spread",
            "trust_state",
            "evidence_label",
        ),
    )
    if scanner is not None:
        for batch in scanner.to_batches():
            data = batch.to_pylist()
            for row in data:
                event_type = str(row.get("event_type") or "UNKNOWN")
                counts[event_type] += 1
                exchange_id = row.get("exchange_id")
                if isinstance(exchange_id, str):
                    exchange_counts[exchange_id][event_type] += 1
                observed = row.get("observed_at")
                if isinstance(observed, datetime):
                    first_at = observed if first_at is None or observed < first_at else first_at
                    last_at = observed if last_at is None or observed > last_at else last_at
                if event_type == "BBO_SNAPSHOT":
                    bbo_rows += 1
                    bid = _optional_float(row.get("best_bid"))
                    ask = _optional_float(row.get("best_ask"))
                    spread = _optional_float(row.get("spread"))
                    if bid is not None and ask is not None:
                        bbo_available += 1
                        if isinstance(exchange_id, str) and isinstance(observed, datetime):
                            latest_mid[exchange_id] = (observed, (bid + ask) / 2.0)
                    if spread is not None:
                        spreads.append(spread)
                elif event_type == "TRADE":
                    size = _optional_float(row.get("quantity"))
                    if size is not None:
                        trade_sizes.append(size)

    raw_total = 0
    raw_invalid = 0
    revision_rows: list[tuple[datetime, int, int]] = []
    scanner = _scanner(
        root,
        "raw_events",
        (
            "local_receive_at",
            "revision",
            "previous_revision",
            "validation_error",
        ),
    )
    if scanner is not None:
        for batch in scanner.to_batches():
            for row in batch.to_pylist():
                raw_total += 1
                if row.get("validation_error") is not None:
                    raw_invalid += 1
                at = row.get("local_receive_at")
                revision = row.get("revision")
                previous = row.get("previous_revision")
                if (
                    isinstance(at, datetime)
                    and isinstance(revision, int)
                    and isinstance(previous, int)
                ):
                    revision_rows.append((at, revision, previous))
    revision_rows.sort(key=lambda item: item[0])
    revision_gaps = 0
    duplicate_revisions = 0
    last_revision: int | None = None
    for _, revision, previous in revision_rows:
        if last_revision == revision:
            duplicate_revisions += 1
            continue
        if last_revision is not None and previous != last_revision:
            revision_gaps += 1
        last_revision = revision

    liquidity: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    scanner = _scanner(root, "liquidity_events", ("change_type", "evidence_label"))
    if scanner is not None:
        for batch in scanner.to_batches():
            for row in batch.to_pylist():
                liquidity[str(row.get("change_type") or "UNKNOWN")] += 1
                evidence[str(row.get("evidence_label") or "UNKNOWN")] += 1

    return (
        {
            "capture_window": {"start": _iso(first_at), "end": _iso(last_at)},
            "event_counts": dict(sorted(counts.items())),
            "raw_batches": raw_total,
            "raw_validation_failures": raw_invalid,
            "revision_gaps_detected_from_capture": revision_gaps,
            "duplicate_revisions": duplicate_revisions,
            "bbo_rows": bbo_rows,
            "bbo_availability_rate": (
                None if bbo_rows == 0 else bbo_available / bbo_rows
            ),
            "spread": _percentiles(spreads),
            "trade_size": _percentiles(trade_sizes),
            "liquidity_change_counts": dict(sorted(liquidity.items())),
            "liquidity_evidence_counts": dict(sorted(evidence.items())),
        },
        {key: dict(value) for key, value in exchange_counts.items()},
        {key: value[1] for key, value in latest_mid.items()},
    )


def _analyse_polymarket(root: Path | None) -> tuple[dict[str, Any], dict[str, float]]:
    if root is None or not root.exists():
        return {"available": False}, {}

    observations = 0
    available = 0
    spreads: list[float] = []
    latest_mid: dict[str, tuple[datetime, float]] = {}
    scanner = _scanner(
        root,
        "observations",
        ("token_id", "observed_at", "best_bid", "best_ask", "spread", "book_valid"),
    )
    if scanner is not None:
        for batch in scanner.to_batches():
            for row in batch.to_pylist():
                observations += 1
                if row.get("book_valid") is False:
                    continue
                bid = _optional_float(row.get("best_bid"))
                ask = _optional_float(row.get("best_ask"))
                spread = _optional_float(row.get("spread"))
                if bid is not None and ask is not None:
                    available += 1
                    token_id = row.get("token_id")
                    observed = row.get("observed_at")
                    if isinstance(token_id, str) and isinstance(observed, datetime):
                        latest_mid[token_id] = (observed, (bid + ask) / 2.0)
                if spread is not None:
                    spreads.append(spread)

    trades = 0
    trade_sizes: list[float] = []
    scanner = _scanner(root, "trades", ("size",))
    if scanner is not None:
        for batch in scanner.to_batches():
            trades += batch.num_rows
            for value in batch.column(0).to_pylist() if batch.num_columns else ():
                size = _optional_float(value)
                if size is not None:
                    trade_sizes.append(size)

    changes = sum(
        batch.num_rows
        for scanner in [_scanner(root, "book_changes", ("token_id",))]
        if scanner is not None
        for batch in scanner.to_batches()
    )
    depth = sum(
        batch.num_rows
        for scanner in [_scanner(root, "depth_snapshots", ("token_id",))]
        if scanner is not None
        for batch in scanner.to_batches()
    )
    return (
        {
            "available": True,
            "observations": observations,
            "bbo_availability_rate": None if observations == 0 else available / observations,
            "spread": _percentiles(spreads),
            "trades": trades,
            "trade_size": _percentiles(trade_sizes),
            "book_changes": changes,
            "depth_snapshots": depth,
        },
        {key: value[1] for key, value in latest_mid.items()},
    )


def _delta_ms(start: int | None, end: int | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start) / 1_000_000.0


def _analyse_execution(
    path: Path | None,
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    if path is None or not path.exists():
        return {"available": False}, []
    connection = sqlite3.connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(execution_events)").fetchall()
        }
        if not columns:
            return {"available": False}, []
        event_counts = {
            str(event_type): int(count)
            for event_type, count in connection.execute(
                "SELECT event_type, COUNT(*) FROM execution_events GROUP BY event_type"
            ).fetchall()
        }
        has_decision = {
            "decision_monotonic_ns",
            "decision_observation_ns",
        } <= columns
        if has_decision:
            raw_rows = connection.execute(
                """
                SELECT logical_operation_id, event_type, observed_monotonic_ns,
                       decision_observation_ns, decision_monotonic_ns
                FROM execution_events
                ORDER BY event_id
                """
            ).fetchall()
        else:
            raw_rows = [
                (operation_id, event_type, observed_ns, None, None)
                for operation_id, event_type, observed_ns in connection.execute(
                    """
                    SELECT logical_operation_id, event_type, observed_monotonic_ns
                    FROM execution_events
                    ORDER BY event_id
                    """
                ).fetchall()
            ]

        events_by_operation: dict[str, dict[str, int]] = defaultdict(dict)
        decision_by_operation: dict[str, tuple[int | None, int | None]] = {}
        for operation_id, event_type, observed_ns, decision_observed, decision_ns in raw_rows:
            operation = str(operation_id)
            event = str(event_type)
            observed = int(observed_ns)
            existing = events_by_operation[operation].get(event)
            if existing is None or observed < existing:
                events_by_operation[operation][event] = observed
            if event == "SUBMISSION":
                current = decision_by_operation.get(operation)
                candidate = (
                    None if decision_observed is None else int(decision_observed),
                    None if decision_ns is None else int(decision_ns),
                )
                if current is None or current == (None, None):
                    decision_by_operation[operation] = candidate

        operation_rows: list[dict[str, object]] = []
        for operation, events in sorted(events_by_operation.items()):
            submission = events.get("SUBMISSION")
            if submission is None:
                continue
            decision_observed, decision = decision_by_operation.get(
                operation,
                (None, None),
            )
            dispatch = events.get("NETWORK_DISPATCH")
            ack = events.get("ACK")
            fill_candidates = [
                value
                for name in ("REALTIME_FILL", "FILL")
                if (value := events.get(name)) is not None
            ]
            fill = min(fill_candidates) if fill_candidates else None
            operation_rows.append(
                {
                    "logical_operation_id": operation,
                    "observation_to_decision_ms": _delta_ms(
                        decision_observed,
                        decision,
                    ),
                    "decision_to_submission_ms": _delta_ms(decision, submission),
                    "submission_to_dispatch_ms": _delta_ms(submission, dispatch),
                    "dispatch_to_ack_ms": _delta_ms(dispatch, ack),
                    "dispatch_to_fill_ms": _delta_ms(dispatch, fill),
                    "observation_to_ack_ms": _delta_ms(decision_observed, ack),
                    "observation_to_fill_ms": _delta_ms(decision_observed, fill),
                }
            )

        def metric(name: str) -> dict[str, float | None]:
            values = [
                float(str(value))
                for row in operation_rows
                if (value := row.get(name)) is not None
            ]
            return _percentiles(values)

        return (
            {
                "available": True,
                "event_counts": event_counts,
                "operations_with_submission": len(operation_rows),
                "observation_to_decision_ms": metric("observation_to_decision_ms"),
                "decision_to_submission_ms": metric("decision_to_submission_ms"),
                "submission_to_dispatch_ms": metric("submission_to_dispatch_ms"),
                "dispatch_to_ack_ms": metric("dispatch_to_ack_ms"),
                "dispatch_to_fill_ms": metric("dispatch_to_fill_ms"),
                "observation_to_ack_ms": metric("observation_to_ack_ms"),
                "observation_to_fill_ms": metric("observation_to_fill_ms"),
            },
            operation_rows,
        )
    finally:
        connection.close()


def _cross_venue(
    mapping_path: Path | None,
    sig_mid: dict[str, float],
    pm_mid: dict[str, float],
) -> list[dict[str, object]]:
    if mapping_path is None or not mapping_path.exists():
        return []
    document = load_document(mapping_path)
    rows: list[dict[str, object]] = []
    for record in document.records:
        direct = record.direct_polymarket
        if direct is None or direct.mapped_token_id is None:
            continue
        sig_value = sig_mid.get(record.sig_exchange_id)
        pm_raw_value = pm_mid.get(direct.mapped_token_id)
        if sig_value is None or pm_raw_value is None:
            continue
        if record.mapping_direction is MappingDirection.COMPLEMENT:
            pm_aligned_value = 1.0 - pm_raw_value
        else:
            pm_aligned_value = pm_raw_value
        rows.append(
            {
                "sig_exchange_id": record.sig_exchange_id,
                "polymarket_token_id": direct.mapped_token_id,
                "mapping_class": record.mapping_class.value,
                "mapping_direction": (
                    None
                    if record.mapping_direction is None
                    else record.mapping_direction.value
                ),
                "sig_mid": sig_value,
                "polymarket_raw_mid": pm_raw_value,
                "polymarket_aligned_mid": pm_aligned_value,
                "sig_minus_polymarket": sig_value - pm_aligned_value,
            }
        )
    rows.sort(
        key=lambda item: abs(float(str(item["sig_minus_polymarket"]))),
        reverse=True,
    )
    return rows


def _write_activity(path: Path, counts: dict[str, dict[str, int]]) -> None:
    event_types = sorted({event for values in counts.values() for event in values})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["exchange_id", *event_types, "total"])
        for exchange_id, values in sorted(
            counts.items(), key=lambda item: sum(item[1].values()), reverse=True
        ):
            writer.writerow(
                [
                    exchange_id,
                    *(values.get(event_type, 0) for event_type in event_types),
                    sum(values.values()),
                ]
            )


def _write_rows(
    path: Path,
    rows: list[dict[str, object]],
    *,
    fieldnames: list[str] | None = None,
) -> None:
    if not rows and fieldnames is None:
        path.write_text("", encoding="utf-8")
        return
    selected_fields = fieldnames or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=selected_fields)
        writer.writeheader()
        writer.writerows(rows)


def _report(summary: dict[str, Any]) -> str:
    sig = summary["sig"]
    pm = summary["polymarket"]
    execution = summary["execution"]
    microstructure = summary["sig_microstructure"]
    cross_response = summary["cross_venue_response"]
    cross = summary["cross_venue_latest"]
    lines = [
        "# CAPTURE-001 First-Hours Forensics",
        "",
        "## Capture integrity",
        "",
        (
            f"- SIG capture window: {sig['capture_window']['start']} "
            f"to {sig['capture_window']['end']}."
        ),
        (
            f"- Raw SIG batches: {sig['raw_batches']:,}; "
            f"validation failures: {sig['raw_validation_failures']:,}."
        ),
        (
            "- Captured revision gaps: "
            f"{sig['revision_gaps_detected_from_capture']:,}; "
            f"duplicate revisions: {sig['duplicate_revisions']:,}."
        ),
        "",
        "## SIG market structure",
        "",
        f"- BBO rows: {sig['bbo_rows']:,}; availability rate: {sig['bbo_availability_rate']}.",
        f"- Spread percentiles: {sig['spread']}.",
        f"- Trade-size percentiles: {sig['trade_size']}.",
        f"- Event counts: {sig['event_counts']}.",
        f"- Liquidity transitions: {sig['liquidity_change_counts']}.",
        f"- Evidence labels: {sig['liquidity_evidence_counts']}.",
        f"- Economic BBO lifetime: {microstructure.get('economic_bbo_lifetime_seconds')}.",
        f"- Trade-arrival interval: {microstructure.get('trade_arrival_interval_seconds')}.",
        f"- Absolute midpoint moves: {microstructure.get('absolute_midpoint_move')}.",
        (
            "- Jump counts |mid move| >= 0.05 / 0.10: "
            f"{microstructure.get('jump_count_abs_0_05')} / "
            f"{microstructure.get('jump_count_abs_0_10')}."
        ),
        f"- Aggressor classification: {microstructure.get('aggressor_classification')}.",
        f"- Tracked bid depth: {microstructure.get('tracked_bid_depth')}.",
        f"- Tracked ask depth: {microstructure.get('tracked_ask_depth')}.",
        "",
        "## Markout semantics",
        "",
        f"- {microstructure.get('markout_rule')}.",
        "- Full horizon tables are written to markouts.csv.",
        "",
        "## Polymarket synchronization",
        "",
        f"- Summary: {pm}.",
        "",
        "## Own execution / shadow audit",
        "",
        f"- Summary: {execution}.",
        "",
        "## Cross-venue response diagnostics",
        "",
        f"- Summary: {cross_response}.",
        (
            "- Response lag means nearest subsequent economic BBO change within the "
            "declared window; it is descriptive and not causal evidence."
        ),
        "",
        "## Direct mapped cross-venue latest marks",
        "",
        f"- Comparable direct mappings at report time: {len(cross):,}.",
    ]
    for row in cross[:20]:
        lines.append(
            (
                "- SIG {sig_exchange_id}: SIG {sig_mid:.4f}, "
                "PM aligned {polymarket_aligned_mid:.4f} "
                "({mapping_direction}), difference "
                "{sig_minus_polymarket:+.4f}."
            ).format(**row)
        )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- SIG bookDirty is an observed invalidation, not an order-level delta.",
            "- Aggregate depth reductions are labelled ambiguous unless stronger evidence exists.",
            (
                "- The supplied SIG market feed does not expose persistent participant, "
                "maker, taker, aggressor, or order-lifecycle identity for anonymous activity."
            ),
            (
                "- Passive queue position is not reconstructed. Shadow passive fills "
                "require an explicit separate simulation rule."
            ),
            (
                "- These summaries are descriptive first-hours diagnostics, not a "
                "confirmed 005F transfer or a validated trading edge."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run(
    *,
    input_root: Path,
    output_root: Path,
    polymarket_root: Path | None,
    execution_journal: Path | None,
    mapping_path: Path | None,
    live_learn_outcomes: Path | None = None,
    shadow_journal: Path | None = None,
    inventory_limit: float | None = None,
) -> dict[str, Any]:
    sig_root = input_root / "sig" if (input_root / "sig").exists() else input_root
    pm_root = polymarket_root
    if pm_root is None and (input_root / "polymarket").exists():
        pm_root = input_root / "polymarket"
    journal = execution_journal
    if journal is None and (input_root / "execution_journal.sqlite3").exists():
        journal = input_root / "execution_journal.sqlite3"

    sig, activity, sig_mid = _analyse_sig(sig_root)
    (
        sig_microstructure,
        microstructure_rows,
        markout_rows,
        depth_rows,
        bucket_rows,
    ) = analyze_sig_microstructure(sig_root)
    pm, pm_mid = _analyse_polymarket(pm_root)
    execution, execution_rows = _analyse_execution(journal)
    latency_ms = 100.0
    latency_source = "fixed_sensitivity"
    latency_summary = execution.get("observation_to_ack_ms")
    if isinstance(latency_summary, dict):
        measured_p50 = _optional_float(latency_summary.get("p50"))
        if measured_p50 is not None:
            latency_ms = measured_p50
            latency_source = "measured_execution_observation_to_ack_p50"

    outcomes_path = live_learn_outcomes
    if outcomes_path is None:
        captured_outcomes = input_root / "live_learn" / "outcomes.jsonl"
        if captured_outcomes.exists():
            outcomes_path = captured_outcomes

    shadow_path = shadow_journal
    if shadow_path is None:
        captured_shadow = input_root / "shadow_002" / "events.jsonl"
        if captured_shadow.exists():
            shadow_path = captured_shadow

    economic_intelligence = analyze_live_diagnostics(
        sig_root=sig_root,
        polymarket_root=pm_root,
        mapping_path=mapping_path,
        output_root=output_root / "economic_intelligence",
        live_learn_outcomes=outcomes_path,
        shadow_journal=shadow_path,
        inventory_limit=inventory_limit,
        latency_ms=latency_ms,
        latency_assumption_source=latency_source,
    )
    cross_response, cross_response_rows = analyze_direct_cross_venue(
        sig_root=sig_root,
        polymarket_root=pm_root,
        mapping_path=mapping_path,
    )
    cross = _cross_venue(mapping_path, sig_mid, pm_mid)
    summary = {
        "schema_version": "capture-001-first-hours-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "sig": sig,
        "sig_microstructure": sig_microstructure,
        "polymarket": pm,
        "execution": execution,
        "cross_venue_response": cross_response,
        "cross_venue_latest": cross,
        "economic_intelligence": economic_intelligence,
    }

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "report.md").write_text(_report(summary), encoding="utf-8")
    _write_activity(output_root / "market_activity.csv", activity)
    _write_rows(output_root / "market_microstructure.csv", microstructure_rows)
    _write_rows(output_root / "markouts.csv", markout_rows)
    _write_rows(
        output_root / "depth_summary.csv",
        depth_rows,
        fieldnames=[
            "exchange_id",
            "depth_snapshots",
            "bid_depth_p50",
            "ask_depth_p50",
            "bid_top_level_share_p50",
            "ask_top_level_share_p50",
        ],
    )
    _write_rows(output_root / "activity_15m.csv", bucket_rows)
    _write_rows(output_root / "cross_venue_diagnostics.csv", cross_response_rows)
    _write_rows(output_root / "cross_venue_latest.csv", cross)
    _write_rows(output_root / "execution_latency.csv", execution_rows)
    return summary


def main() -> int:
    args = parse_args()
    summary = run(
        input_root=args.input,
        output_root=args.output,
        polymarket_root=args.polymarket_root,
        execution_journal=args.execution_journal,
        mapping_path=args.mapping,
        live_learn_outcomes=args.live_learn_outcomes,
        shadow_journal=args.shadow_journal,
        inventory_limit=args.inventory_limit,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
