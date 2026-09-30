"""Future market and execution evidence providers for LIVE-LEARN-001."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from predictions_cup.live_learn.contracts import (
    ExecutionEvidence,
    FillEvidence,
    MarketEvidence,
)
from predictions_cup.runtime.models import SIG_TICK
from predictions_cup.shadow.contracts import CandidateDecision, CanonicalShadowSnapshot


@dataclass(frozen=True, slots=True)
class ObservableMarketState:
    snapshot_id: str
    exchange_id: str
    market_id: str
    observed_at: datetime
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None
    trusted: bool
    source_observed_monotonic_ns: int
    snapshot_observed_monotonic_ns: int
    mapping_version: str

    @classmethod
    def from_snapshot(cls, snapshot: CanonicalShadowSnapshot) -> ObservableMarketState:
        book = snapshot.maker.runtime.book(snapshot.exchange_id)
        best_bid: float | None = None
        best_ask: float | None = None
        midpoint: float | None = None
        if book is not None and book.bids and book.asks:
            bid_ticks = max(level.price_ticks for level in book.bids)
            ask_ticks = min(level.price_ticks for level in book.asks)
            if bid_ticks <= ask_ticks:
                best_bid = bid_ticks * float(SIG_TICK)
                best_ask = ask_ticks * float(SIG_TICK)
                midpoint = (best_bid + best_ask) * 0.5
        return cls(
            snapshot_id=snapshot.snapshot_id,
            exchange_id=snapshot.exchange_id,
            market_id=snapshot.market_id,
            observed_at=snapshot.observed_at.astimezone(UTC),
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=midpoint,
            trusted=snapshot.maker.sig_bbo_trusted,
            source_observed_monotonic_ns=snapshot.maker.sig_bbo_observed_ns,
            snapshot_observed_monotonic_ns=snapshot.observed_monotonic_ns,
            mapping_version=snapshot.mapping_version,
        )

    @classmethod
    def from_shadow_record(cls, record: Mapping[str, object]) -> ObservableMarketState:
        maker = cast(Mapping[str, Any], record["maker"])
        runtime = cast(Mapping[str, Any], maker["runtime"])
        exchange_id = str(maker["exchange_id"])
        best_bid: float | None = None
        best_ask: float | None = None
        midpoint: float | None = None
        books = cast(list[Mapping[str, Any]], runtime.get("books", []))
        book = next(
            (item for item in books if str(item.get("exchange_id")) == exchange_id),
            None,
        )
        if book is not None:
            bids = cast(list[Mapping[str, Any]], book.get("bids", []))
            asks = cast(list[Mapping[str, Any]], book.get("asks", []))
            if bids and asks:
                bid_ticks = max(int(item["price_ticks"]) for item in bids)
                ask_ticks = min(int(item["price_ticks"]) for item in asks)
                if bid_ticks <= ask_ticks:
                    best_bid = bid_ticks * float(SIG_TICK)
                    best_ask = ask_ticks * float(SIG_TICK)
                    midpoint = (best_bid + best_ask) * 0.5
        return cls(
            snapshot_id=str(record["snapshot_id"]),
            exchange_id=exchange_id,
            market_id=str(maker["market_id"]),
            observed_at=datetime.fromisoformat(str(record["observed_at"])).astimezone(UTC),
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=midpoint,
            trusted=bool(maker["sig_bbo_trusted"]),
            source_observed_monotonic_ns=int(maker["sig_bbo_observed_ns"]),
            snapshot_observed_monotonic_ns=int(record["observed_monotonic_ns"]),
            mapping_version=str(record["mapping_version"]),
        )

    def evidence(self) -> MarketEvidence:
        freshness = max(
            0.0,
            (
                self.snapshot_observed_monotonic_ns
                - self.source_observed_monotonic_ns
            )
            / 1e9,
        )
        return MarketEvidence(
            snapshot_id=self.snapshot_id,
            exchange_id=self.exchange_id,
            market_id=self.market_id,
            observed_at=self.observed_at,
            source_id=f"sig-bbo:{self.snapshot_id}:{self.exchange_id}",
            source_observed_at=self.observed_at,
            source_monotonic_ns=self.source_observed_monotonic_ns,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            midpoint=self.midpoint,
            trusted=self.trusted,
            freshness_seconds=freshness,
        )


class ExecutionEvidenceProvider(Protocol):
    provider_id: str
    version: str

    async def evidence_for(
        self,
        decision: CandidateDecision,
        *,
        maturity_at: datetime,
    ) -> ExecutionEvidence: ...


class NullExecutionEvidenceProvider:
    provider_id = "none"
    version = "v1"

    async def evidence_for(
        self,
        decision: CandidateDecision,
        *,
        maturity_at: datetime,
    ) -> ExecutionEvidence:
        del decision, maturity_at
        return ExecutionEvidence(
            supported=False,
            reason="execution_evidence_provider_unavailable",
            planned_quantity=0.0,
        )


class JournalExecutionEvidenceProvider:
    """Read BUILD-009's append-only journal without adding execution semantics.

    Immediate fills recorded by the LIVE sink and authoritative fills recovered
    from SIG are eligible. Passive trade-through is never interpreted as a fill.
    """

    provider_id = "build009-execution-journal"
    version = "live-learn-001-v1"

    def __init__(self, path: Path) -> None:
        self._path = path

    async def evidence_for(
        self,
        decision: CandidateDecision,
        *,
        maturity_at: datetime,
    ) -> ExecutionEvidence:
        import asyncio

        return await asyncio.to_thread(self._read, decision, maturity_at)

    def _read(
        self,
        decision: CandidateDecision,
        maturity_at: datetime,
    ) -> ExecutionEvidence:
        if not self._path.exists():
            return ExecutionEvidence(
                supported=False,
                reason="execution_journal_unavailable",
                planned_quantity=0.0,
            )
        try:
            connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        except sqlite3.Error:
            return ExecutionEvidence(
                supported=False,
                reason="execution_journal_unreadable",
                planned_quantity=0.0,
            )
        try:
            operation_rows = connection.execute(
                """
                SELECT DISTINCT e.logical_operation_id, x.payload_json, x.sink_mode
                FROM execution_events AS e
                JOIN execution_envelopes AS x
                  ON x.logical_operation_id = e.logical_operation_id
                WHERE e.event_type = 'SUBMISSION'
                  AND e.strategy_id = ?
                  AND e.exchange_id = ?
                  AND e.tournament_id = ?
                  AND (
                    e.decision_monotonic_ns = ?
                    OR e.decision_observation_ns = ?
                  )
                """,
                (
                    decision.candidate_id,
                    decision.exchange_id,
                    decision.tournament_id,
                    decision.monotonic_time,
                    decision.monotonic_time,
                ),
            ).fetchall()
            if not operation_rows:
                return ExecutionEvidence(
                    supported=False,
                    reason="no_execution_plan_for_decision",
                    planned_quantity=0.0,
                )

            planned = 0.0
            fills: list[FillEvidence] = []
            sources: list[str] = []
            modes: set[str] = set()
            for operation_id_raw, payload_json_raw, mode_raw in operation_rows:
                operation_id = str(operation_id_raw)
                payload_json = str(payload_json_raw)
                modes.add(str(mode_raw))
                plans = _payload_legs(payload_json, decision.exchange_id)
                planned += sum(float(item["quantity"]) for item in plans)
                action = _single_action(plans)
                if action is None:
                    return ExecutionEvidence(
                        supported=False,
                        reason="mixed_side_execution_requires_intent_fill_join",
                        planned_quantity=0.0,
                    )
                sources.append(f"build009:operation:{operation_id}")
                rows = connection.execute(
                    """
                    SELECT event_id, event_type, observed_monotonic_ns,
                           source_timestamp, exchange_order_id, fill_id,
                           quantity, price
                    FROM execution_events
                    WHERE logical_operation_id = ?
                      AND exchange_id = ?
                      AND event_type IN ('FILL_SUMMARY', 'AUTHORITATIVE_FILL')
                    ORDER BY event_id
                    """,
                    (operation_id, decision.exchange_id),
                ).fetchall()
                authoritative = [row for row in rows if str(row[1]) == "AUTHORITATIVE_FILL"]
                selected = authoritative if authoritative else rows
                for row in selected:
                    filled_at = _parse_time(row[3])
                    if filled_at is not None and filled_at > maturity_at:
                        continue
                    observed_ns = int(row[2])
                    if filled_at is None:
                        horizon_ns = int(
                            (maturity_at - decision.observed_at).total_seconds() * 1e9
                        )
                        if observed_ns > decision.monotonic_time + horizon_ns:
                            continue
                    quantity = _positive_number(row[6])
                    if quantity is None:
                        continue
                    price = _probability(row[7])
                    event_id = int(row[0])
                    fill_id = None if row[5] is None else str(row[5])
                    evidence_id = (
                        f"sig-fill:{fill_id}"
                        if fill_id is not None
                        else f"build009-event:{event_id}"
                    )
                    fills.append(
                        FillEvidence(
                            evidence_id=evidence_id,
                            logical_operation_id=operation_id,
                            exchange_order_id=(
                                None if row[4] is None else str(row[4])
                            ),
                            exchange_id=decision.exchange_id,
                            action=action,
                            quantity=quantity,
                            price=price,
                            filled_at=filled_at,
                            observed_monotonic_ns=observed_ns,
                        )
                    )
                    sources.append(evidence_id)

            if planned <= 0.0:
                return ExecutionEvidence(
                    supported=False,
                    reason="execution_plan_has_no_matching_leg",
                    planned_quantity=0.0,
                )
            mode = next(iter(modes)) if len(modes) == 1 else "MIXED"
            return ExecutionEvidence(
                supported=True,
                reason=None,
                planned_quantity=planned,
                fills=tuple(fills),
                evidence_source_ids=tuple(dict.fromkeys(sources)),
                execution_mode=mode,
            )
        except sqlite3.Error:
            return ExecutionEvidence(
                supported=False,
                reason="execution_journal_query_failed",
                planned_quantity=0.0,
            )
        finally:
            connection.close()


def _payload_legs(payload_json: str, exchange_id: str) -> tuple[dict[str, object], ...]:
    raw = json.loads(payload_json)
    if not isinstance(raw, dict):
        return ()
    if "orders" in raw:
        values = raw["orders"]
    elif "legs" in raw:
        values = raw["legs"]
    else:
        values = [raw]
    if not isinstance(values, list):
        return ()
    result: list[dict[str, object]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        if str(item.get("exchangeId")) != exchange_id:
            continue
        quantity = item.get("quantity")
        action = item.get("action")
        if (
            isinstance(quantity, int)
            and quantity > 0
            and action in {"buy", "sell"}
        ):
            result.append(
                {
                    "quantity": quantity,
                    "action": str(action),
                }
            )
    return tuple(result)


def _single_action(plans: tuple[dict[str, object], ...]) -> str | None:
    actions = {str(item["action"]) for item in plans}
    if actions == {"buy"}:
        return "buy"
    if actions == {"sell"}:
        return "sell"
    return None


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _positive_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = abs(float(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def _probability(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return parsed
