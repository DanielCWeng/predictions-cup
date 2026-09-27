"""Deterministic observable-time replay runner."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

from predictions_cup.replay.model import ReplayEvent, ReplayState


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    """Ephemeral view after all events observable at this exact timestamp are applied."""

    observed_at: datetime
    events: tuple[ReplayEvent, ...]
    state: ReplayState


FrameHandler = Callable[[ReplayFrame], None]
BeforeFrameHandler = Callable[[datetime, ReplayState], None]


class ReplayRunner:
    def __init__(self, events: Iterable[ReplayEvent]) -> None:
        self.events = tuple(sorted(events, key=lambda event: event.sort_key))

    def run(
        self,
        on_frame: FrameHandler,
        *,
        before_frame: BeforeFrameHandler | None = None,
    ) -> ReplayState:
        state = ReplayState()
        for observed_at, group_iter in groupby(
            self.events, key=lambda event: event.observed_at
        ):
            if before_frame is not None:
                before_frame(observed_at, state)
            group = tuple(group_iter)
            for event in group:
                state.apply(event)
            on_frame(ReplayFrame(observed_at=observed_at, events=group, state=state))
        return state
