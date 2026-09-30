"""Pure venue timing span derivation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from predictions_cup.observe.contracts import ObservationKind, VenueObservation


class SpanName(StrEnum):
    DECISION_TO_DISPATCH = "decision_to_dispatch"
    DISPATCH_TO_RESPONSE = "dispatch_to_response"
    DISPATCH_TO_ACK = "dispatch_to_ack"
    REQUEST_ENQUEUE_TO_ACK = "request_enqueue_to_ack"
    ACK_TO_FIRST_FILL = "ack_to_first_fill"
    DISPATCH_TO_FILL = "dispatch_to_fill"
    CANCEL_TO_ACK = "cancel_to_ack"
    RECONNECT = "reconnect"
    QUOTE_LIFETIME = "quote_lifetime"


@dataclass(frozen=True, slots=True)
class VenueSpan:
    name: SpanName
    logical_operation_id: str | None
    process_instance_id: str
    start_monotonic_ns: int
    end_monotonic_ns: int
    identity: str | None = None

    @property
    def duration_ns(self) -> int:
        return self.end_monotonic_ns - self.start_monotonic_ns


_RULES: tuple[tuple[SpanName, ObservationKind, tuple[ObservationKind, ...]], ...] = (
    (
        SpanName.DECISION_TO_DISPATCH,
        ObservationKind.DECISION_OBSERVED,
        (ObservationKind.REQUEST_DISPATCHED,),
    ),
    (
        SpanName.DISPATCH_TO_RESPONSE,
        ObservationKind.REQUEST_DISPATCHED,
        (ObservationKind.RESPONSE_RECEIVED,),
    ),
    (SpanName.DISPATCH_TO_ACK, ObservationKind.REQUEST_DISPATCHED, (ObservationKind.ACK,)),
    (
        SpanName.REQUEST_ENQUEUE_TO_ACK,
        ObservationKind.REQUEST_ENQUEUED,
        (ObservationKind.ACK,),
    ),
    (
        SpanName.ACK_TO_FIRST_FILL,
        ObservationKind.ACK,
        (ObservationKind.PARTIAL_FILL, ObservationKind.FILL),
    ),
    (SpanName.DISPATCH_TO_FILL, ObservationKind.REQUEST_DISPATCHED, (ObservationKind.FILL,)),
    (
        SpanName.CANCEL_TO_ACK,
        ObservationKind.CANCEL_REQUESTED,
        (ObservationKind.CANCEL_ACK,),
    ),
)


class VenueSpanCollector:
    """Derive spans only inside one process clock domain."""

    def collect(self, observations: Iterable[VenueObservation]) -> tuple[VenueSpan, ...]:
        rows = tuple(observations)
        groups: dict[tuple[str, str | None], list[VenueObservation]] = {}
        for item in rows:
            key = (item.process_instance_id, item.logical_operation_id)
            groups.setdefault(key, []).append(item)
        spans: list[VenueSpan] = []
        for (process_id, operation_id), group in groups.items():
            ordered = sorted(group, key=lambda item: item.monotonic_ns)
            first: dict[ObservationKind, int] = {}
            for item in ordered:
                first.setdefault(item.kind, item.monotonic_ns)
            for name, start_kind, end_kinds in _RULES:
                start = first.get(start_kind)
                ends = [first[kind] for kind in end_kinds if kind in first]
                if start is None or not ends:
                    continue
                end = min(ends)
                if end < start:
                    continue
                spans.append(VenueSpan(name, operation_id, process_id, start, end))
        quote_starts = tuple(
            item for item in rows if item.kind is ObservationKind.QUOTE_PUBLISHED
        )
        quote_ends = tuple(
            item
            for item in rows
            if item.kind in {ObservationKind.QUOTE_WITHDRAWN, ObservationKind.FILL}
        )
        for start_item in quote_starts:
            start_aliases = _quote_aliases(start_item, allow_quote_key=True)
            if not start_aliases:
                continue
            candidates: list[tuple[int, str]] = []
            for end_item in quote_ends:
                if end_item.process_instance_id != start_item.process_instance_id:
                    continue
                if end_item.monotonic_ns < start_item.monotonic_ns:
                    continue
                end_aliases = _quote_aliases(
                    end_item,
                    allow_quote_key=end_item.kind is ObservationKind.QUOTE_WITHDRAWN,
                )
                common = start_aliases.intersection(end_aliases)
                if not common:
                    continue
                identity = sorted(common, key=_quote_alias_priority)[0]
                candidates.append((end_item.monotonic_ns, identity))
            if not candidates:
                continue
            end_ns, identity = min(candidates, key=lambda item: item[0])
            spans.append(
                VenueSpan(
                    SpanName.QUOTE_LIFETIME,
                    start_item.logical_operation_id,
                    start_item.process_instance_id,
                    start_item.monotonic_ns,
                    end_ns,
                    identity,
                )
            )

        by_process: dict[str, list[VenueObservation]] = {}
        for item in rows:
            by_process.setdefault(item.process_instance_id, []).append(item)
        for process_id, group in by_process.items():
            pending_reconnects: list[int] = []
            for item in sorted(group, key=lambda row: row.monotonic_ns):
                if item.kind is ObservationKind.RECONNECT_STARTED:
                    pending_reconnects.append(item.monotonic_ns)
                    continue
                if (
                    item.kind is ObservationKind.RECONNECT_RESOLVED
                    and pending_reconnects
                ):
                    start = pending_reconnects.pop(0)
                    if item.monotonic_ns >= start:
                        spans.append(
                            VenueSpan(
                                SpanName.RECONNECT,
                                None,
                                process_id,
                                start,
                                item.monotonic_ns,
                            )
                        )
        return tuple(spans)


def _detail_value(observation: VenueObservation, key: str) -> str | None:
    for candidate, value in observation.detail:
        if candidate == key and value:
            return value
    return None


def _quote_aliases(
    observation: VenueObservation,
    *,
    allow_quote_key: bool,
) -> frozenset[str]:
    aliases: set[str] = set()
    if observation.logical_intent_id:
        aliases.add(f"intent:{observation.logical_intent_id}")
    if observation.exchange_order_id:
        aliases.add(f"order:{observation.exchange_order_id}")
    if allow_quote_key:
        quote_key = _detail_value(observation, "quote_key")
        if quote_key is not None:
            aliases.add(f"quote:{quote_key}")
    return frozenset(aliases)


def _quote_alias_priority(value: str) -> int:
    if value.startswith("intent:"):
        return 0
    if value.startswith("order:"):
        return 1
    return 2
