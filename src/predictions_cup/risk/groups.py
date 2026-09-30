"""Configured correlated-event grouping for RISK-002."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from predictions_cup.risk.capital import ExposureGroupProvider, MarketExposureGroup


@dataclass(frozen=True, slots=True)
class StaticExposureGroupProvider(ExposureGroupProvider):
    version: str
    memberships: tuple[MarketExposureGroup, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("exposure-group version must not be blank")
        identities = tuple(
            (item.market_id, item.tournament_id) for item in self.memberships
        )
        if len(identities) != len(set(identities)):
            raise ValueError("exposure-group memberships must be unique by market/tournament")

    def groups_for(self, market_id: str, tournament_id: str) -> tuple[str, ...]:
        for item in self.memberships:
            if item.market_id == market_id and item.tournament_id == tournament_id:
                return item.group_ids
        return ()

    def for_tournament(self, tournament_id: str) -> tuple[MarketExposureGroup, ...]:
        return tuple(
            item for item in self.memberships if item.tournament_id == tournament_id
        )


def load_exposure_group_provider(path: Path) -> StaticExposureGroupProvider:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("exposure-group document must be an object")
    version = raw.get("version")
    rows = raw.get("memberships")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("exposure-group document requires version")
    if not isinstance(rows, list):
        raise ValueError("exposure-group document requires memberships array")

    memberships: list[MarketExposureGroup] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("exposure-group membership must be an object")
        market_id = row.get("market_id")
        tournament_id = row.get("tournament_id")
        group_ids = row.get("group_ids")
        if not isinstance(market_id, str) or not isinstance(tournament_id, str):
            raise ValueError("exposure-group membership requires market/tournament")
        if not isinstance(group_ids, list) or not group_ids:
            raise ValueError("exposure-group membership requires non-empty group_ids")
        if any(not isinstance(value, str) for value in group_ids):
            raise ValueError("exposure-group ids must be strings")
        memberships.append(
            MarketExposureGroup(
                market_id=market_id,
                tournament_id=tournament_id,
                group_ids=tuple(group_ids),
            )
        )
    return StaticExposureGroupProvider(
        version=version,
        memberships=tuple(memberships),
    )
