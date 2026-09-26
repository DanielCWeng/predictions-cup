"""DATA-001 historical election regimes and source-routing constants.

The five regimes are preselected by the DATA-001 ticket. Windows are half-open
``[start, end)`` in UTC, so ``end`` is the first instant *outside* the window
(``...T23:59:59Z`` inclusive in ticket wording).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

SCHEMA_VERSION = 1

# PendulumFlow PMXT archive eras (verified against the archive listings).
PMXT_ARCHIVE_BASE_URL = "https://archive.pendulumflow.com/pmxt"
PMXT_V1_FIRST_HOUR = datetime(2026, 2, 21, 18, tzinfo=UTC)
PMXT_V1_LAST_HOUR = datetime(2026, 4, 16, 5, tzinfo=UTC)
# Ticket routing rule: before this hour -> PMXT V1, from this hour -> PMXT V2.
PMXT_V2_FIRST_HOUR = datetime(2026, 4, 13, 19, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Regime:
    regime_id: str
    country: str
    round: str
    family: str
    election_date: date
    window_start: datetime
    window_end: datetime

    def __post_init__(self) -> None:
        for value, label in ((self.window_start, "window_start"), (self.window_end, "window_end")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")

    def contains(self, value: datetime) -> bool:
        return self.window_start <= value < self.window_end


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


REGIMES: tuple[Regime, ...] = (
    Regime(
        regime_id="colombia_first_round",
        country="Colombia",
        round="first_round",
        family="COL_2026",
        election_date=date(2026, 5, 31),
        window_start=_utc(2026, 5, 29),
        window_end=_utc(2026, 6, 3),
    ),
    Regime(
        regime_id="colombia_runoff",
        country="Colombia",
        round="runoff",
        family="COL_2026",
        election_date=date(2026, 6, 21),
        window_start=_utc(2026, 6, 19),
        window_end=_utc(2026, 6, 24),
    ),
    Regime(
        regime_id="peru_first_round",
        country="Peru",
        round="first_round",
        family="PER_2026",
        election_date=date(2026, 4, 12),
        window_start=_utc(2026, 4, 10),
        window_end=_utc(2026, 4, 15),
    ),
    Regime(
        regime_id="peru_runoff",
        country="Peru",
        round="runoff",
        family="PER_2026",
        election_date=date(2026, 6, 7),
        window_start=_utc(2026, 6, 5),
        window_end=_utc(2026, 6, 10),
    ),
    Regime(
        regime_id="hungary_election",
        country="Hungary",
        round="parliamentary",
        family="HUN_2026",
        election_date=date(2026, 4, 12),
        window_start=_utc(2026, 4, 5),
        window_end=_utc(2026, 4, 15),
    ),
)

# Candidate market families per research family, as classified by PolyLeviathan's
# ``market_family`` column. Candidates are then narrowed to tokens with observed rows.
FAMILY_MARKET_FAMILIES: dict[str, frozenset[str]] = {
    "COL_2026": frozenset(
        {
            "PRESIDENTIAL_FIRST_ROUND",
            "PRESIDENTIAL_RUNOFF",
            "PRESIDENTIAL_RESULT",
            "RUNOFF_QUALIFICATION_OR_PAIR",
        }
    ),
    "PER_2026": frozenset(
        {
            "PRESIDENTIAL_FIRST_ROUND",
            "PRESIDENTIAL_RESULT",
            "PRESIDENTIAL_RUNOFF",
            "HOUSE_OR_LOWER_CHAMBER",
            "SENATE_OR_UPPER_CHAMBER",
        }
    ),
    "HUN_2026": frozenset(
        {"SEAT_COUNT_OR_RANK", "GOVERNMENT_FORMATION", "VOTE_SHARE_OR_POPULAR_VOTE"}
    ),
}


def regime_by_id(regime_id: str) -> Regime:
    for regime in REGIMES:
        if regime.regime_id == regime_id:
            return regime
    raise KeyError(f"unknown regime {regime_id!r}")


def pmxt_version_for_hour(hour: datetime) -> str:
    """Ticket routing: V1 before 2026-04-13T19:00Z, V2 from then on."""
    return "PMXT_V2" if hour >= PMXT_V2_FIRST_HOUR else "PMXT_V1"
