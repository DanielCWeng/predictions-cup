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
    CANCEL_TO_CONFIRMATION = "cancel_to_confirmation"
    RECONNECT = "reconnect"
    QUOTE_LIFETIME = "quote_lifetime"


@dataclass(frozen=True, slots=True)
class VenueSpan:
    name: SpanName
    logical_operation_id: str | None
    process_instance_id: str
    start_monotonic_ns: int
    end_monotonic_ns: int

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
        SpanName.CANCEL_TO_CONFIRMATION,
        ObservationKind.CANCEL_REQUESTED,
        (ObservationKind.CANCEL_ACK,),
    ),
    (
        SpanName.QUOTE_LIFETIME,
        ObservationKind.QUOTE_PUBLISHED,
        (ObservationKind.QUOTE_WITHDRAWN, ObservationKind.FILL),
    ),
)


class VenueSpanCollector:
    """Derive spans only inside one process clock domain."""

    def collect(self, observations: Iterable[VenueObservation]) -> tuple[VenueSpan, ...]:
        groups: dict[tuple[str, str | None], list[VenueObservation]] = {}
        for item in observations:
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
        by_process: dict[str, list[VenueObservation]] = {}
        for item in observations:
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
