"""Pure descriptive PM -> SIG timing joins. No causal claim is made here."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EconomicChange:
    venue: str
    instrument_id: str
    observed_at: datetime
    value: float
    source: str
    revision: int | None = None


@dataclass(frozen=True, slots=True)
class CrossVenueMapping:
    polymarket_token_id: str
    sig_exchange_id: str
    direction: str
    mapping_class: str
    mapping_version: str


@dataclass(frozen=True, slots=True)
class CrossVenueResponse:
    polymarket_token_id: str
    sig_exchange_id: str
    pm_observed_at: datetime
    sig_observed_at: datetime
    elapsed_seconds: float
    pm_delta: float
    sig_delta: float
    same_direction: bool
    mapping_direction: str
    mapping_class: str
    mapping_version: str
    interpretation: str = "observed subsequent response; not causal lead-lag evidence"


def _economic_changes(
    values: tuple[EconomicChange, ...],
) -> tuple[tuple[datetime, float], ...]:
    if len(values) < 2:
        return ()
    result: list[tuple[datetime, float]] = []
    previous = values[0]
    for current in values[1:]:
        delta = current.value - previous.value
        if delta != 0.0:
            result.append((current.observed_at, delta))
            previous = current
    return tuple(result)


def join_pm_to_sig(
    *,
    mapping: CrossVenueMapping,
    pm: tuple[EconomicChange, ...],
    sig: tuple[EconomicChange, ...],
    max_delay_seconds: float = 60.0,
) -> tuple[CrossVenueResponse, ...]:
    """Join each PM economic move to the next observed mapped SIG economic move."""
    if max_delay_seconds <= 0:
        raise ValueError("max_delay_seconds must be positive")
    if mapping.direction not in {"SAME", "COMPLEMENT"}:
        return ()
    pm_ordered = tuple(sorted(pm, key=lambda item: item.observed_at))
    sig_ordered = tuple(sorted(sig, key=lambda item: item.observed_at))
    if mapping.direction == "COMPLEMENT":
        pm_ordered = tuple(
            EconomicChange(
                venue=item.venue,
                instrument_id=item.instrument_id,
                observed_at=item.observed_at,
                value=1.0 - item.value,
                source=item.source,
                revision=item.revision,
            )
            for item in pm_ordered
        )
    pm_changes = _economic_changes(pm_ordered)
    sig_changes = _economic_changes(sig_ordered)
    sig_times = [item[0] for item in sig_changes]
    rows: list[CrossVenueResponse] = []
    for pm_at, pm_delta in pm_changes:
        index = bisect.bisect_left(sig_times, pm_at)
        if index >= len(sig_changes):
            continue
        sig_at, sig_delta = sig_changes[index]
        elapsed = (sig_at - pm_at).total_seconds()
        if elapsed < 0 or elapsed > max_delay_seconds:
            continue
        rows.append(
            CrossVenueResponse(
                polymarket_token_id=mapping.polymarket_token_id,
                sig_exchange_id=mapping.sig_exchange_id,
                pm_observed_at=pm_at,
                sig_observed_at=sig_at,
                elapsed_seconds=elapsed,
                pm_delta=pm_delta,
                sig_delta=sig_delta,
                same_direction=pm_delta * sig_delta > 0,
                mapping_direction=mapping.direction,
                mapping_class=mapping.mapping_class,
                mapping_version=mapping.mapping_version,
            )
        )
    return tuple(rows)
