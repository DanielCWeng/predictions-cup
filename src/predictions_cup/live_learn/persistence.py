"""Append-only LIVE-LEARN outcome persistence."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter_ns

from predictions_cup.live_learn.contracts import (
    DecisionOutcome,
    outcome_from_record,
    outcome_record,
)


class JsonlOutcomeStore:
    """Append-only, idempotent outcome journal keyed by deterministic outcome_id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._known_ids: set[str] = set()
        self._outcomes: dict[str, DecisionOutcome] = {}
        self._started = False
        self._failures = 0
        self._last_error: str | None = None
        self._write_batches = 0
        self._persisted = 0
        self._write_latency_ns: list[int] = []

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        if self.path.exists():
            loaded = await asyncio.to_thread(self._load)
            for item in loaded:
                self._known_ids.add(item.outcome_id)
                self._outcomes[item.outcome_id] = item
        self._started = True

    async def persist_many(self, outcomes: Sequence[DecisionOutcome]) -> int:
        self._require_started()
        fresh: list[DecisionOutcome] = []
        staged: set[str] = set()
        for outcome in outcomes:
            if outcome.outcome_id in self._known_ids or outcome.outcome_id in staged:
                continue
            staged.add(outcome.outcome_id)
            fresh.append(outcome)
        if not fresh:
            return 0
        started = perf_counter_ns()
        try:
            await asyncio.to_thread(self._append, tuple(fresh))
        except Exception as exc:
            self._failures += 1
            self._last_error = f"{type(exc).__name__}:{exc}"
            raise
        self._write_latency_ns.append(perf_counter_ns() - started)
        self._write_batches += 1
        self._persisted += len(fresh)
        for outcome in fresh:
            self._known_ids.add(outcome.outcome_id)
            self._outcomes[outcome.outcome_id] = outcome
        return len(fresh)

    async def close(self) -> None:
        self._started = False

    def contains(self, outcome_id: str) -> bool:
        return outcome_id in self._known_ids

    def outcomes(self) -> tuple[DecisionOutcome, ...]:
        return tuple(
            sorted(
                self._outcomes.values(),
                key=lambda item: (
                    item.maturity_at,
                    item.decision_id,
                    item.horizon_seconds,
                ),
            )
        )

    @property
    def persisted(self) -> int:
        return self._persisted

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def write_batches(self) -> int:
        return self._write_batches

    @property
    def write_latency_p95_ns(self) -> int | None:
        if not self._write_latency_ns:
            return None
        values = sorted(self._write_latency_ns)
        index = int((len(values) - 1) * 0.95)
        return values[index]

    def _load(self) -> tuple[DecisionOutcome, ...]:
        results: list[DecisionOutcome] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                value = json.loads(stripped)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"invalid LIVE-LEARN outcome at line {line_number}"
                    )
                outcome = outcome_from_record(value)
                results.append(outcome)
        return tuple(results)

    def _append(self, outcomes: Sequence[DecisionOutcome]) -> None:
        lines = "".join(
            json.dumps(
                outcome_record(outcome),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for outcome in outcomes
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
            handle.flush()
            os.fsync(handle.fileno())

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("LIVE-LEARN outcome store is not started")
