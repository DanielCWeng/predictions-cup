"""Chronological train/development/holdout boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChronologicalBoundaries:
    train_end: datetime
    development_end: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("train_end", self.train_end),
            ("development_end", self.development_end),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.development_end <= self.train_end:
            raise ValueError("development_end must be after train_end")
