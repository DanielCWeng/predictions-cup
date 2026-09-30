"""Bounded non-blocking OBSERVE-001 emission."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from predictions_cup.observe.contracts import ObservationSink, VenueObservation


@dataclass(frozen=True, slots=True)
class EmitterHealth:
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    accepted: int
    dropped: int
    sink_failures: int
    worker_alive: bool


class NullObservationEmitter:
    def emit(self, observation: VenueObservation) -> bool:
        del observation
        return True


class InMemoryObservationSink:
    def __init__(self) -> None:
        self.observations: list[VenueObservation] = []

    def write(self, observation: VenueObservation) -> None:
        self.observations.append(observation)


class CallbackObservationSink:
    def __init__(self, callback: Callable[[VenueObservation], None]) -> None:
        self._callback = callback

    def write(self, observation: VenueObservation) -> None:
        self._callback(observation)


class BoundedObservationEmitter:
    """Non-blocking producer; sink failures are isolated from execution and Risk."""

    def __init__(self, sink: ObservationSink, *, queue_max: int = 65_536) -> None:
        if queue_max <= 0:
            raise ValueError("queue_max must be positive")
        self._sink = sink
        self._queue: queue.Queue[VenueObservation | None] = queue.Queue(maxsize=queue_max)
        self._accepted = 0
        self._dropped = 0
        self._sink_failures = 0
        self._queue_high_water = 0
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="observe-001-writer",
            daemon=True,
        )
        self._thread.start()

    def emit(self, observation: VenueObservation) -> bool:
        if self._closed:
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait(observation)
        except queue.Full:
            self._dropped += 1
            return False
        self._accepted += 1
        self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
        return True

    def health(self) -> EmitterHealth:
        return EmitterHealth(
            queue_depth=self._queue.qsize(),
            queue_capacity=self._queue.maxsize,
            queue_high_water=self._queue_high_water,
            accepted=self._accepted,
            dropped=self._dropped,
            sink_failures=self._sink_failures,
            worker_alive=self._thread.is_alive(),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        while True:
            observation = self._queue.get()
            if observation is None:
                return
            try:
                self._sink.write(observation)
            except Exception:
                self._sink_failures += 1
