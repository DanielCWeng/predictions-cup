"""Official-source competition context adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from predictions_cup.observe.contracts import FieldClassification
from predictions_cup.sig.dto import AccountDto
from predictions_cup.sig.realtime_models import (
    TournamentLeaderboardDto,
    TournamentListStatus,
    TournamentPageDto,
)


@dataclass(frozen=True, slots=True)
class ContextField:
    name: str
    value: object | None
    classification: FieldClassification
    source: str
    source_version: str
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CompetitionContextSnapshot:
    tournament_id: str
    observed_at: datetime
    fields: tuple[ContextField, ...]
    raw_tournament: dict[str, object] | None
    raw_account: dict[str, object] | None
    raw_leaderboard: dict[str, object] | None

    def field(self, name: str) -> ContextField:
        for field in self.fields:
            if field.name == name:
                return field
        raise KeyError(name)


class CompetitionContextRest(Protocol):
    async def list_tournaments(
        self, *, status: TournamentListStatus = "any", limit: int = 50, offset: int = 0
    ) -> TournamentPageDto: ...

    async def get_account(self) -> AccountDto: ...

    async def get_tournament_leaderboard(
        self,
        tournament_slug: str,
        *,
        period: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> TournamentLeaderboardDto: ...


class CompetitionContextProvider(Protocol):
    async def snapshot(self) -> CompetitionContextSnapshot: ...


class SigOfficialCompetitionContextProvider:
    """Participant-accessible fields only, using the accepted SIG API contract."""

    def __init__(self, client: CompetitionContextRest, *, tournament_id: str) -> None:
        if not tournament_id.strip():
            raise ValueError("tournament_id must not be blank")
        self._client = client
        self._tournament_id = tournament_id

    async def snapshot(self) -> CompetitionContextSnapshot:
        tournaments = await self._client.list_tournaments(status="any", limit=100, offset=0)
        tournament = next(
            (item for item in tournaments.data if item.id == self._tournament_id),
            None,
        )
        account = await self._client.get_account()
        observed_at = datetime.now(UTC)
        fields: list[ContextField] = []
        raw_leaderboard: dict[str, object] | None = None

        if tournament is None:
            for name in (
                "tournament_name",
                "tournament_status",
                "start_date",
                "end_date",
                "initial_balance",
                "my_balance",
                "joined_at",
                "leaderboard",
                "participant_rank",
                "leaderboard_total",
            ):
                fields.append(
                    ContextField(
                        name,
                        None,
                        FieldClassification.UNAVAILABLE,
                        "SIG participant API",
                        "api-1",
                        "TOURNAMENT_NOT_RETURNED",
                    )
                )
            raw_tournament = None
        else:
            raw_tournament = tournament.model_dump(mode="json", by_alias=True)
            for name, value in (
                ("tournament_name", tournament.name),
                ("tournament_status", tournament.status),
                ("start_date", tournament.start_date),
                ("end_date", tournament.end_date),
                ("initial_balance", tournament.initial_balance),
                ("my_balance", tournament.my_balance),
                ("joined_at", tournament.joined_at),
            ):
                fields.append(
                    ContextField(
                        name,
                        value,
                        FieldClassification.NORMALIZED,
                        "SIG /tournaments",
                        "api-1",
                    )
                )
            leaderboard = await self._client.get_tournament_leaderboard(
                tournament.slug,
                period="all",
                limit=100,
                offset=0,
            )
            raw_leaderboard = leaderboard.model_dump(mode="json", by_alias=True)
            fields.extend(
                (
                    ContextField(
                        "leaderboard",
                        tuple(
                            row.model_dump(mode="json", by_alias=True)
                            for row in leaderboard.leaderboard
                        ),
                        FieldClassification.NORMALIZED,
                        "SIG /tournaments/{slug}/leaderboard",
                        "api-1",
                    ),
                    ContextField(
                        "participant_rank",
                        leaderboard.my_rank,
                        FieldClassification.NORMALIZED,
                        "SIG /tournaments/{slug}/leaderboard",
                        "api-1",
                    ),
                    ContextField(
                        "leaderboard_total",
                        leaderboard.total,
                        FieldClassification.NORMALIZED,
                        "SIG /tournaments/{slug}/leaderboard",
                        "api-1",
                    ),
                )
            )

        fields.extend(
            (
                ContextField(
                    "account_balance",
                    account.balance,
                    FieldClassification.NORMALIZED,
                    "SIG /account",
                    "api-1",
                ),
                ContextField(
                    "super_signal",
                    None,
                    FieldClassification.UNAVAILABLE,
                    "SIG /dmm/trader-analytics/signals",
                    "api-1",
                    "ADMIN_ONLY_PARTICIPANT_KEY_UNSUPPORTED",
                ),
            )
        )
        return CompetitionContextSnapshot(
            tournament_id=self._tournament_id,
            observed_at=observed_at,
            fields=tuple(fields),
            raw_tournament=raw_tournament,
            raw_account=account.model_dump(mode="json", by_alias=True),
            raw_leaderboard=raw_leaderboard,
        )


class ProtocolPersist(Protocol):
    def __call__(self, snapshot: CompetitionContextSnapshot) -> None: ...


class CompetitionContextSampler:
    """Deadline-driven single-flight sampler; no independent polling loop."""

    def __init__(
        self,
        provider: CompetitionContextProvider,
        persist: ProtocolPersist,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._provider = provider
        self._persist = persist
        self._interval = timedelta(seconds=interval_seconds)
        self._next_due: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._failures = 0
        self._last_error: str | None = None

    def maybe_schedule(self, now: datetime, *, force: bool = False) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("context scheduler time must be timezone aware")
        if self._task is not None and not self._task.done():
            return False
        if not force and self._next_due is not None and now < self._next_due:
            return False
        self._next_due = now.astimezone(UTC) + self._interval
        self._task = asyncio.create_task(self._capture_once(), name="observe-context-snapshot")
        return True

    def health(self) -> dict[str, object]:
        return {
            "failures": self._failures,
            "last_error": self._last_error,
            "in_flight": self._task is not None and not self._task.done(),
            "next_due": self._next_due,
        }

    async def aclose(self) -> None:
        if self._task is not None:
            await self._task

    async def _capture_once(self) -> None:
        try:
            snapshot = await self._provider.snapshot()
            self._persist(snapshot)
            self._last_error = None
        except Exception as exc:
            self._failures += 1
            self._last_error = type(exc).__name__


def json_value(value: object | None) -> object | None:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value
