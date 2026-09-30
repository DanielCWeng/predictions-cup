"""Exact competition-period 005F state provider over observable PM BBOs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from predictions_cup.mapping.models import (
    MappingDirection,
    MappingDocument,
    MappingStatus,
)
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.shadow.frozen_runtime import (
    Hazard005FBboObservation,
    Hazard005FFeatureVector,
    IncrementalHazard005FState,
)


class Accepted005FScopeResolver:
    """Resolve SIG exchange identity to the accepted PM research token."""

    version = "005f-accepted-direct-mapping-v1"

    def __init__(self, mapping: MappingDocument) -> None:
        exchange_to_token: dict[str, str] = {}
        for record in mapping.records:
            direct = record.direct_polymarket
            if (
                record.status is not MappingStatus.VERIFIED
                or direct is None
                or record.mapping_direction
                not in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
            ):
                continue
            previous = exchange_to_token.setdefault(
                record.sig_exchange_id,
                direct.mapped_token_id,
            )
            if previous != direct.mapped_token_id:
                raise ValueError(
                    "005F accepted mapping resolves one exchange to multiple tokens"
                )
        self._exchange_to_token = exchange_to_token
        self._accepted_tokens = frozenset(exchange_to_token.values())

    def resolve_exchange(self, exchange_id: str) -> str | None:
        return self._exchange_to_token.get(exchange_id)

    def accepts_scope(self, scope_id: str) -> bool:
        return scope_id in self._accepted_tokens

    def resolve_snapshot(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> str | None:
        return self.resolve_exchange(snapshot.exchange_id)


@dataclass(slots=True)
class _PendingBboGroup:
    observed_at: datetime
    timestamp_ns: int
    observed_monotonic_ns: int
    states: set[tuple[float, float]] = field(default_factory=set)
    source_versions: set[str] = field(default_factory=set)
    invalid: bool = False
    raw_count: int = 0


class Live005FStateProvider:
    """Feed exact observable PM BBO changes into accepted frozen 005F state logic."""

    provider_id = "005f-live-exact-observable-state"
    version = "005f-live-001-v1"

    def __init__(
        self,
        *,
        mapping: MappingDocument,
        grid_origin: datetime | None,
    ) -> None:
        self._resolver = Accepted005FScopeResolver(mapping)
        self._grid_origin_ns = (
            None
            if grid_origin is None
            else IncrementalHazard005FState.datetime_ns(grid_origin)
        )
        self._state = IncrementalHazard005FState(
            grid_origin_ns=self._grid_origin_ns,
            scope_resolver=self._resolver.resolve_snapshot,
        )
        self._last_observed_monotonic_ns: dict[str, int] = {}
        self._pending: dict[str, _PendingBboGroup] = {}
        self._raw_observations = 0
        self._accepted_observations = 0
        self._missing_event_time = 0
        self._unmapped_snapshots = 0

    @property
    def grid_origin_ns(self) -> int | None:
        return self._grid_origin_ns

    @property
    def accepted_observations(self) -> int:
        return self._accepted_observations

    @property
    def missing_event_time(self) -> int:
        return self._missing_event_time

    @property
    def unmapped_snapshots(self) -> int:
        return self._unmapped_snapshots

    def scope_for_exchange(self, exchange_id: str) -> str | None:
        return self._resolver.resolve_exchange(exchange_id)

    def observe_bbo(
        self,
        *,
        scope_id: str,
        observed_at: datetime,
        observed_monotonic_ns: int,
        best_bid: float | None,
        best_ask: float | None,
        source_version: str,
        trusted: bool,
    ) -> bool:
        """Ingest one accepted PM BBO boundary before MAKE/SHADOW coalescing."""
        if not self._resolver.accepts_scope(scope_id):
            return False
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("005F PM observed_at must be timezone-aware")
        if observed_monotonic_ns < 0:
            raise ValueError("005F observed_monotonic_ns must be non-negative")
        if not source_version.strip():
            raise ValueError("005F source_version must not be blank")

        timestamp_ns = IncrementalHazard005FState.datetime_ns(observed_at)
        previous_monotonic = self._last_observed_monotonic_ns.get(scope_id)
        if (
            previous_monotonic is not None
            and observed_monotonic_ns < previous_monotonic
        ):
            raise ValueError("005F observable PM chronology must be monotonic")

        pending = self._pending.get(scope_id)
        if pending is not None and timestamp_ns < pending.timestamp_ns:
            raise ValueError("005F BBO observations must be time ordered per scope")
        if pending is not None and timestamp_ns > pending.timestamp_ns:
            self._flush_pending(scope_id)
            pending = None
        if pending is None:
            pending = _PendingBboGroup(
                observed_at=observed_at,
                timestamp_ns=timestamp_ns,
                observed_monotonic_ns=observed_monotonic_ns,
            )
            self._pending[scope_id] = pending

        pending.raw_count += 1
        pending.observed_monotonic_ns = max(
            pending.observed_monotonic_ns,
            observed_monotonic_ns,
        )
        pending.source_versions.add(source_version)
        valid_state = (
            trusted
            and best_bid is not None
            and best_ask is not None
            and math.isfinite(best_bid)
            and math.isfinite(best_ask)
            and 0.0 <= best_bid <= best_ask <= 1.0
        )
        if valid_state:
            assert best_bid is not None and best_ask is not None
            pending.states.add((best_bid, best_ask))
        else:
            pending.invalid = True

        self._last_observed_monotonic_ns[scope_id] = observed_monotonic_ns
        self._raw_observations += 1
        return True

    def _flush_pending(self, scope_id: str) -> None:
        pending = self._pending.pop(scope_id, None)
        if pending is None:
            return
        unambiguous = not pending.invalid and len(pending.states) == 1
        if unambiguous:
            best_bid, best_ask = next(iter(pending.states))
        else:
            best_bid = None
            best_ask = None
        source_version = (
            next(iter(pending.source_versions))
            if len(pending.source_versions) == 1
            else "mixed-source-version"
        )
        self._state.observe(
            Hazard005FBboObservation(
                scope_id=scope_id,
                timestamp_ns=pending.timestamp_ns,
                observed_monotonic_ns=pending.observed_monotonic_ns,
                best_bid=best_bid,
                best_ask=best_ask,
                source_version=source_version,
                ambiguous=not unambiguous,
            )
        )
        self._accepted_observations += 1

    def feature_vector(
        self,
        snapshot: CanonicalShadowSnapshot,
    ) -> Hazard005FFeatureVector | None:
        scope_id = self._resolver.resolve_snapshot(snapshot)
        if scope_id is None:
            self._unmapped_snapshots += 1
            return None

        # Replay/backward-compatible fallback: live production feeds every accepted
        # PM BBO at the websocket/order-book boundary before coalescing. Persisted
        # SHADOW-only replays may still have only the latest external quote.
        quote = snapshot.maker.external_quotes.get(scope_id)
        if quote is not None:
            previous = self._last_observed_monotonic_ns.get(scope_id)
            if previous is None or quote.observed_monotonic_ns > previous:
                if quote.observed_at is None:
                    self._missing_event_time += 1
                else:
                    self.observe_bbo(
                        scope_id=scope_id,
                        observed_at=quote.observed_at,
                        observed_monotonic_ns=quote.observed_monotonic_ns,
                        best_bid=quote.best_bid,
                        best_ask=quote.best_ask,
                        source_version=quote.source_version,
                        trusted=quote.trusted,
                    )

        pending = self._pending.get(scope_id)
        snapshot_ns = IncrementalHazard005FState.datetime_ns(snapshot.observed_at)
        if pending is not None and pending.timestamp_ns < snapshot_ns:
            self._flush_pending(scope_id)

        return self._state.feature_vector(snapshot)

    def status(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "version": self.version,
            "grid_origin_ns": self._grid_origin_ns,
            "raw_observations": self._raw_observations,
            "accepted_observations": self._accepted_observations,
            "pending_equal_time_groups": len(self._pending),
            "missing_event_time": self._missing_event_time,
            "unmapped_snapshots": self._unmapped_snapshots,
            "mapping_semantics": (
                "VERIFIED direct SAME/COMPLEMENT only; SIG market IDs are never "
                "used as PM research-token IDs."
            ),
        }
