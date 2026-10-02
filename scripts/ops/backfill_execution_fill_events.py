"""Manually backfill known SIG fills into the local execution journal.

The SIG client is read-only. This script is never called by a service; local
journal writes require the explicit --write flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.execution.journal import (
    ExecutionJournal,
    classify_fill_source,
    fill_identity_keys,
)
from predictions_cup.sig.client import RetryPolicy, SigRestClient
from predictions_cup.sig.trading_dto import FillReadDto


@dataclass(frozen=True, slots=True)
class PlacementAttribution:
    logical_operation_id: str
    logical_intent_id: str | None
    strategy_family: str | None
    strategy_id: str | None

    @property
    def fill_source(self) -> str:
        return classify_fill_source(
            logical_operation_id=self.logical_operation_id,
            strategy_family=self.strategy_family,
            strategy_id=self.strategy_id,
        )


@dataclass(frozen=True, slots=True)
class BackfillCandidate:
    fill: FillReadDto
    attribution: PlacementAttribution


def _parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--since must include a timezone")
    return parsed.astimezone(UTC)


def _placement_attributions(
    journal_path: Path,
    *,
    tournament_id: str,
) -> dict[str, PlacementAttribution]:
    if not journal_path.is_file():
        raise FileNotFoundError(f"execution journal does not exist: {journal_path}")
    connection = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT ack.exchange_order_id, ack.logical_operation_id,
                   ack.logical_intent_id, submission.strategy_family,
                   submission.strategy_id
            FROM execution_events AS ack
            JOIN execution_envelopes AS envelope
              ON envelope.logical_operation_id = ack.logical_operation_id
            LEFT JOIN execution_events AS submission
              ON submission.logical_operation_id = ack.logical_operation_id
             AND submission.logical_intent_id = ack.logical_intent_id
             AND submission.event_type = 'SUBMISSION'
            WHERE ack.event_type = 'ACK'
              AND ack.exchange_order_id IS NOT NULL
              AND envelope.tournament_id = ?
              AND envelope.operation_kind IN (
                  'single_placement', 'best_effort_batch', 'atomic_multi_leg'
              )
            ORDER BY ack.event_id
            """,
            (tournament_id,),
        ).fetchall()
    finally:
        connection.close()

    result: dict[str, PlacementAttribution] = {}
    for order_id, operation_id, intent_id, family, strategy_id in rows:
        attribution = PlacementAttribution(
            logical_operation_id=str(operation_id),
            logical_intent_id=None if intent_id is None else str(intent_id),
            strategy_family=None if family is None else str(family),
            strategy_id=None if strategy_id is None else str(strategy_id),
        )
        key = str(order_id)
        previous = result.get(key)
        if previous is not None and previous != attribution:
            raise ValueError(f"SIG order {key} maps to multiple journal placements")
        result[key] = attribution
    return result


async def _read_complete_portfolio_fills(
    rest: SigRestClient,
    *,
    tournament_id: str,
) -> tuple[FillReadDto, ...]:
    rows: list[FillReadDto] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    first_request = True
    while True:
        if not first_request:
            await asyncio.sleep(1.05)
        first_request = False
        page = await rest.list_portfolio_fills(
            tournament_id=tournament_id,
            limit=200,
            cursor=cursor,
        )
        if page.coverage is None or not page.coverage.complete:
            raise RuntimeError("SIG did not report complete portfolio fill coverage")
        rows.extend(page.data)
        if not page.pagination.has_more:
            return tuple(rows)
        next_cursor = page.pagination.next_cursor
        if next_cursor is None or not next_cursor.strip() or next_cursor in seen_cursors:
            raise RuntimeError("SIG portfolio fill pagination did not advance")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def _keys_for_fill(
    fill: FillReadDto,
    *,
    tournament_id: str,
) -> tuple[str, ...]:
    if fill.order_id is None:
        return ()
    return fill_identity_keys(
        tournament_id=tournament_id,
        exchange_id=fill.exchange_id,
        exchange_order_id=str(fill.order_id),
        source_timestamp=fill.filled_at.isoformat(),
        quantity=str(fill.quantity),
        price=None if fill.price is None else str(fill.price),
        fill_id=str(fill.id),
    )


def _existing_fill_keys(journal_path: Path) -> set[str]:
    connection = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "execution_fill_dedupe" in tables:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT dedupe_key FROM execution_fill_dedupe"
                ).fetchall()
            }
        rows = connection.execute(
            """
            SELECT tournament_id, exchange_id, exchange_order_id,
                   source_timestamp, quantity, price, fill_id
            FROM execution_events
            WHERE event_type IN ('REALTIME_FILL', 'AUTHORITATIVE_FILL')
              AND exchange_order_id IS NOT NULL
              AND source_timestamp IS NOT NULL
              AND quantity IS NOT NULL
            """
        ).fetchall()
    finally:
        connection.close()
    keys: set[str] = set()
    for row in rows:
        keys.update(
            fill_identity_keys(
                tournament_id=str(row[0]),
                exchange_id=None if row[1] is None else str(row[1]),
                exchange_order_id=str(row[2]),
                source_timestamp=str(row[3]),
                quantity=str(row[4]),
                price=None if row[5] is None else str(row[5]),
                fill_id=None if row[6] is None else str(row[6]),
            )
        )
    return keys


def _write_candidate(
    journal: ExecutionJournal,
    candidate: BackfillCandidate,
    *,
    observed_monotonic_ns: int,
) -> bool:
    fill = candidate.fill
    attribution = candidate.attribution
    if fill.order_id is None:
        return False
    source = attribution.fill_source
    return journal.record_fill_event_once(
        logical_operation_id=attribution.logical_operation_id,
        logical_intent_id=attribution.logical_intent_id,
        event_type="AUTHORITATIVE_FILL",
        observed_monotonic_ns=observed_monotonic_ns,
        source_timestamp=fill.filled_at.isoformat(),
        exchange_id=fill.exchange_id,
        exchange_order_id=str(fill.order_id),
        fill_id=str(fill.id),
        quantity=str(fill.quantity),
        price=None if fill.price is None else str(fill.price),
        strategy_family=source,
        strategy_id=attribution.strategy_id,
        detail_json=json.dumps(
            {
                "fill_source": source,
                "origin_strategy_family": attribution.strategy_family,
                "sig_fill_id": str(fill.id),
            },
            separators=(",", ":"),
        ),
    )


async def _run(args: argparse.Namespace, settings: AppSettings) -> int:
    if settings.sig_read_credential is None:
        raise RuntimeError("PREDICTIONS_CUP_SIG_READ_CREDENTIAL is required")
    if settings.tournament_id is None or not settings.tournament_id.strip():
        raise RuntimeError("PREDICTIONS_CUP_TOURNAMENT_ID is required")
    tournament_id = settings.tournament_id
    attributions = _placement_attributions(
        args.journal,
        tournament_id=tournament_id,
    )
    since = _parse_since(args.since)

    retry_policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=1.0,
        max_delay_seconds=4.0,
        jitter_ratio=0.0,
    )
    async with SigRestClient(
        settings,
        timeout_seconds=args.timeout_seconds,
        retry_policy=retry_policy,
    ) as rest:
        fills = await _read_complete_portfolio_fills(rest, tournament_id=tournament_id)

    candidates: list[BackfillCandidate] = []
    unmatched = 0
    out_of_scope = 0
    for fill in fills:
        if since is not None and fill.filled_at.astimezone(UTC) < since:
            out_of_scope += 1
            continue
        if fill.order_id is None:
            unmatched += 1
            continue
        attribution = attributions.get(str(fill.order_id))
        if attribution is None:
            unmatched += 1
            continue
        candidates.append(BackfillCandidate(fill=fill, attribution=attribution))

    inserted = 0
    already_present = 0
    journal: ExecutionJournal | None = None
    known_keys = set() if args.write else _existing_fill_keys(args.journal)
    try:
        if args.write:
            journal = ExecutionJournal(args.journal)
        for candidate in candidates:
            fill = candidate.fill
            keys = _keys_for_fill(fill, tournament_id=tournament_id)
            if args.write:
                assert journal is not None
                recorded = _write_candidate(
                    journal,
                    candidate,
                    observed_monotonic_ns=monotonic_ns(),
                )
            else:
                recorded = not any(key in known_keys for key in keys)
                if recorded:
                    known_keys.update(keys)
            if recorded:
                inserted += 1
            else:
                already_present += 1
    finally:
        if journal is not None:
            journal.close()

    source_counts = {"MAKE": 0, "TAKE": 0}
    source_shares = {"MAKE": 0.0, "TAKE": 0.0}
    for candidate in candidates:
        source = candidate.attribution.fill_source
        source_counts[source] += 1
        source_shares[source] += abs(float(candidate.fill.quantity))
    mode = "WRITE" if args.write else "DRY_RUN"
    print(
        json.dumps(
            {
                "mode": mode,
                "tournament_id": tournament_id,
                "since": None if since is None else since.isoformat(),
                "sig_complete_fills": len(fills),
                "matched_fills": len(candidates),
                "unmatched_fills": unmatched,
                "out_of_scope_fills": out_of_scope,
                "new_fills": inserted,
                "already_journaled": already_present,
                "make_fill_count": source_counts["MAKE"],
                "make_shares": source_shares["MAKE"],
                "take_fill_count": source_counts["TAKE"],
                "take_shares": source_shares["TAKE"],
            },
            sort_keys=True,
        )
    )
    return 0 if unmatched == 0 else 2


def main(argv: Sequence[str] | None = None) -> int:
    os.environ.pop("PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL", None)
    settings = load_settings(use_dotenv=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--journal",
        type=Path,
        required=True,
        help="path to a local copied execution journal",
    )
    parser.add_argument("--since", help="optional ISO timestamp with timezone")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write missing fills to the local journal (default is read-only dry run)",
    )
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        return asyncio.run(_run(args, settings))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"backfill failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
