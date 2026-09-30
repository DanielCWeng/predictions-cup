"""Adapters from OBSERVE-001 contracts into CAPTURE-001 storage."""

from __future__ import annotations

from typing import Protocol

from predictions_cup.observe.context import CompetitionContextSnapshot
from predictions_cup.observe.contracts import VenueObservation


class CaptureObservationRecorder(Protocol):
    def record_venue_observation(self, observation: VenueObservation) -> None: ...

    def record_competition_context(
        self, snapshot: CompetitionContextSnapshot
    ) -> None: ...


class CaptureObservationSink:
    def __init__(self, recorder: CaptureObservationRecorder) -> None:
        self._recorder = recorder

    def write(self, observation: VenueObservation) -> None:
        self._recorder.record_venue_observation(observation)


def persist_competition_context(
    recorder: CaptureObservationRecorder,
    snapshot: CompetitionContextSnapshot,
) -> None:
    recorder.record_competition_context(snapshot)
