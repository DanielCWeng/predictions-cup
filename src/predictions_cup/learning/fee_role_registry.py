"""Frozen market/relationship wiring for EXPERIMENT-005A."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Condition:
    event: str
    event_family: str
    regime: str
    condition_id: str
    token_id: str
    market_family: str
    question: str


@dataclass(frozen=True)
class Relationship:
    event: str
    regime: str
    source_condition_id: str
    source_token_id: str
    target_condition_id: str
    target_token_id: str
    relationship_class: str
    semantic_basis: str


_USABLE_COLUMN = {
    "PRE_ELECTION": "pre_election_usable",
    "ACTIVE_RESULTS": "active_results_usable",
}


def load_conditions(
    usability_csv: Path,
    *,
    regimes: tuple[str, ...] = ("PRE_ELECTION", "ACTIVE_RESULTS"),
) -> list[Condition]:
    with usability_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result: list[Condition] = []
    for row in rows:
        for regime in regimes:
            usable = _USABLE_COLUMN.get(regime)
            if usable is None:
                raise ValueError(f"unsupported regime {regime!r}")
            if row.get(usable) != "true":
                continue
            token = row.get("canonical_token_id", "")
            if not token:
                continue
            result.append(
                Condition(
                    event=row["regime_id"],
                    event_family=row["event_family"],
                    regime=regime,
                    condition_id=row["condition_id"],
                    token_id=token,
                    market_family=row["market_family"],
                    question=row["question"],
                )
            )
    regime_rank = {name: index for index, name in enumerate(regimes)}
    return sorted(
        result,
        key=lambda row: (
            row.event,
            regime_rank[row.regime],
            row.market_family,
            row.condition_id,
        ),
    )


def family_pairs(conditions: list[Condition]) -> list[Relationship]:
    """Generate all directed same-family pairs, never selecting by observed performance."""

    groups: dict[tuple[str, str, str], list[Condition]] = {}
    for row in conditions:
        groups.setdefault((row.event, row.regime, row.market_family), []).append(row)
    output: list[Relationship] = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda row: row.condition_id)
        for source in rows:
            for target in rows:
                if source.condition_id == target.condition_id:
                    continue
                output.append(
                    Relationship(
                        event=source.event,
                        regime=source.regime,
                        source_condition_id=source.condition_id,
                        source_token_id=source.token_id,
                        target_condition_id=target.condition_id,
                        target_token_id=target.token_id,
                        relationship_class=f"SAME_FAMILY:{source.market_family}",
                        semantic_basis="frozen market_family metadata",
                    )
                )
    return output


def load_semantic_relationships(
    inventory_csv: Path,
    conditions: list[Condition],
) -> list[Relationship]:
    """Restrict the accepted semantic inventory to pairs usable in each frozen regime."""

    usable = {
        (row.event, row.regime, row.condition_id): row
        for row in conditions
    }
    with inventory_csv.open(newline="", encoding="utf-8") as handle:
        inventory = list(csv.DictReader(handle))
    output: list[Relationship] = []
    for edge in inventory:
        event = edge["regime_id"]
        target_condition = edge["target_condition_id"]
        source_condition = edge["reference_condition_id"]
        for regime in _USABLE_COLUMN:
            source = usable.get((event, regime, source_condition))
            target = usable.get((event, regime, target_condition))
            if source is None or target is None:
                continue
            output.append(
                Relationship(
                    event=event,
                    regime=regime,
                    source_condition_id=source.condition_id,
                    source_token_id=source.token_id,
                    target_condition_id=target.condition_id,
                    target_token_id=target.token_id,
                    relationship_class=edge["relationship_type"],
                    semantic_basis=edge["semantic_basis"],
                )
            )
    return sorted(
        output,
        key=lambda row: (
            row.event,
            row.regime,
            row.source_condition_id,
            row.target_condition_id,
        ),
    )


def participant_stratum(
    *,
    event: str,
    regime: str,
    market_family: str,
    time_block: int,
    role: str,
) -> str:
    """Stable null stratum preserving event/market-family/time/role structure."""

    return "|".join((event, regime, market_family, str(time_block), role))
