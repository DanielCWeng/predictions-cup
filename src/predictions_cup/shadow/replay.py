"""Deterministic replay helpers for SHADOW-002."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from predictions_cup.maker.contracts import ExternalQuoteState, MakerMarketSnapshot
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimeOrderState,
    RuntimePortfolio,
    RuntimePosition,
    RuntimeSnapshot,
)
from predictions_cup.shadow.contracts import (
    CandidateDecision,
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
    ShadowCandidate,
    decision_from_output,
    decision_semantic_record,
    failure_output,
)
from predictions_cup.shadow.persistence import read_jsonl_records


@dataclass(frozen=True, slots=True)
class ReplayResult:
    decisions: tuple[CandidateDecision, ...]
    semantic_hash: str


class ShadowReplayRunner:
    """Replay deterministic candidates over an already captured canonical sequence."""

    def __init__(self, candidates: Iterable[ShadowCandidate]) -> None:
        self._candidates = tuple(candidates)
        ids = tuple(candidate.candidate_id for candidate in self._candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("replay candidates must have unique IDs")

    def replay(
        self,
        snapshots: Sequence[CanonicalShadowSnapshot],
        *,
        candidate_ids: set[str] | None = None,
    ) -> ReplayResult:
        selected = (
            set(candidate.candidate_id for candidate in self._candidates)
            if candidate_ids is None
            else set(candidate_ids)
        )
        known = {candidate.candidate_id for candidate in self._candidates}
        unknown = selected - known
        if unknown:
            raise KeyError(f"unknown replay candidates: {sorted(unknown)}")

        decisions: list[CandidateDecision] = []
        previous: int | None = None
        for snapshot in snapshots:
            if previous is not None and snapshot.observed_monotonic_ns < previous:
                raise ValueError("replay snapshots are not in observable-time order")
            previous = snapshot.observed_monotonic_ns
            for candidate in self._candidates:
                if candidate.candidate_id not in selected:
                    continue
                decisions.append(_evaluate_replay(candidate, snapshot))

        semantic_records = [decision_semantic_record(item) for item in decisions]
        raw = json.dumps(
            semantic_records,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ReplayResult(
            decisions=tuple(decisions),
            semantic_hash=hashlib.sha256(raw).hexdigest(),
        )


def load_persisted_snapshots(path: Path) -> tuple[CanonicalShadowSnapshot, ...]:
    snapshots: list[CanonicalShadowSnapshot] = []
    for record in read_jsonl_records(path):
        if record.get("event_type") != "snapshot":
            continue
        snapshots.append(_snapshot_from_record(record))
    return tuple(snapshots)


def _evaluate_replay(
    candidate: ShadowCandidate,
    snapshot: CanonicalShadowSnapshot,
) -> CandidateDecision:
    try:
        output = candidate.evaluate(snapshot)
    except Exception as exc:
        output = failure_output(
            DecisionStatus.EXCEPTION,
            "candidate_exception",
            detail=f"{type(exc).__name__}:{exc}",
        )
    if not isinstance(output, CandidateOutput):
        output = failure_output(
            DecisionStatus.INVALID_OUTPUT,
            "candidate_returned_wrong_type",
            detail=type(output).__name__,
        )
    try:
        return decision_from_output(
            snapshot=snapshot,
            candidate=candidate,
            output=output,
            compute_started_at=0,
            compute_finished_at=0,
        )
    except (TypeError, ValueError) as exc:
        return decision_from_output(
            snapshot=snapshot,
            candidate=candidate,
            output=failure_output(
                DecisionStatus.INVALID_OUTPUT,
                "candidate_output_validation_failed",
                detail=f"{type(exc).__name__}:{exc}",
            ),
            compute_started_at=0,
            compute_finished_at=0,
        )


def _snapshot_from_record(record: dict[str, object]) -> CanonicalShadowSnapshot:
    maker_record = cast(dict[str, Any], record["maker"])
    runtime_record = cast(dict[str, Any], maker_record["runtime"])
    portfolio_record = cast(dict[str, Any], runtime_record["portfolio"])

    markets = tuple(
        RuntimeMarket(
            market_id=str(item["market_id"]),
            status=str(item["status"]),
            exchange_ids=tuple(str(value) for value in item["exchange_ids"]),
            tournament_id=str(item["tournament_id"]),
            mapping_accepted=bool(item["mapping_accepted"]),
            tradeable=bool(item["tradeable"]),
        )
        for item in cast(list[dict[str, Any]], runtime_record["markets"])
    )
    books = tuple(
        RuntimeBook(
            exchange_id=str(item["exchange_id"]),
            market_id=str(item["market_id"]),
            tournament_id=str(item["tournament_id"]),
            bids=tuple(
                RuntimeLevel(
                    price_ticks=int(level["price_ticks"]),
                    quantity=float(level["quantity"]),
                )
                for level in cast(list[dict[str, Any]], item["bids"])
            ),
            asks=tuple(
                RuntimeLevel(
                    price_ticks=int(level["price_ticks"]),
                    quantity=float(level["quantity"]),
                )
                for level in cast(list[dict[str, Any]], item["asks"])
            ),
            trusted_depth=bool(item["trusted_depth"]),
            observed_monotonic_ns=int(item["observed_monotonic_ns"]),
        )
        for item in cast(list[dict[str, Any]], runtime_record["books"])
    )
    positions = tuple(
        RuntimePosition(
            exchange_id=str(item["exchange_id"]),
            market_id=str(item["market_id"]),
            tournament_id=str(item["tournament_id"]),
            gross_exposure=float(item["gross_exposure"]),
            signed_quantity=float(item["signed_quantity"]),
        )
        for item in cast(list[dict[str, Any]], portfolio_record["positions"])
    )
    orders = tuple(
        RuntimeOrderState(
            logical_intent_id=str(item["logical_intent_id"]),
            exchange_id=str(item["exchange_id"]),
            market_id=str(item["market_id"]),
            tournament_id=str(item["tournament_id"]),
            reserved_exposure=float(item["reserved_exposure"]),
            open=bool(item["open"]),
            uncertain=bool(item["uncertain"]),
        )
        for item in cast(list[dict[str, Any]], portfolio_record["orders"])
    )
    runtime = RuntimeSnapshot(
        markets=markets,
        books=books,
        portfolio=RuntimePortfolio(
            positions=positions,
            orders=orders,
            account_trusted=bool(portfolio_record["account_trusted"]),
        ),
        observation_monotonic_ns=int(runtime_record["observation_monotonic_ns"]),
    )

    external_raw = cast(dict[str, dict[str, Any]], maker_record["external_quotes"])
    external = {
        token_id: ExternalQuoteState(
            token_id=str(item["token_id"]),
            best_bid=(
                None if item["best_bid"] is None else float(item["best_bid"])
            ),
            best_ask=(
                None if item["best_ask"] is None else float(item["best_ask"])
            ),
            observed_monotonic_ns=int(item["observed_monotonic_ns"]),
            trusted=bool(item["trusted"]),
            source_version=str(item["source_version"]),
        )
        for token_id, item in external_raw.items()
    }
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=str(maker_record["exchange_id"]),
        market_id=str(maker_record["market_id"]),
        tournament_id=str(maker_record["tournament_id"]),
        now_monotonic_ns=int(maker_record["now_monotonic_ns"]),
        sig_bbo_observed_ns=int(maker_record["sig_bbo_observed_ns"]),
        sig_bbo_trusted=bool(maker_record["sig_bbo_trusted"]),
        sig_depth_observed_ns=(
            None
            if maker_record["sig_depth_observed_ns"] is None
            else int(maker_record["sig_depth_observed_ns"])
        ),
        sig_depth_trusted=bool(maker_record["sig_depth_trusted"]),
        account_observed_ns=int(maker_record["account_observed_ns"]),
        inventory_observed_ns=int(maker_record["inventory_observed_ns"]),
        external_quotes=external,
        volatility=(
            None
            if maker_record["volatility"] is None
            else float(maker_record["volatility"])
        ),
    )
    provenance_pairs = cast(list[list[str]], record["source_provenance"])
    provenance = {str(key): str(value) for key, value in provenance_pairs}
    return CanonicalShadowSnapshot.restore(
        snapshot_id=str(record["snapshot_id"]),
        maker=maker,
        observed_at=datetime.fromisoformat(str(record["observed_at"])),
        mapping_version=str(record["mapping_version"]),
        source_revision=str(record["source_revision"]),
        source_provenance=provenance,
    )
