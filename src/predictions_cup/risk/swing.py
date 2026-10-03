"""Default-off national election swing risk control."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from predictions_cup.execution.models import RuntimeOrderIntent
from predictions_cup.external.polymarket.orderbook import OrderBookStore
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)
from predictions_cup.runtime.models import RuntimeSnapshot

LOGIT_PER_SWING_POINT = 0.10
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SwingMappedMarket:
    exchange_id: str
    market_id: str
    tournament_id: str
    race_id: str
    chamber: str
    party_sign: int
    pm_condition_id: str
    pm_yes_token_id: str
    sig_yes_is_pm_yes: bool


@dataclass(frozen=True, slots=True)
class SwingCrosswalk:
    verified: bool
    markets: tuple[SwingMappedMarket, ...] = ()

    def market_for(self, exchange_id: str) -> SwingMappedMarket | None:
        return next(
            (market for market in self.markets if market.exchange_id == exchange_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class SwingPmMark:
    pm_condition_id: str
    midpoint: float
    observed_at: datetime


class SwingPmMarkProvider(Protocol):
    def mark_for(self, token_id: str) -> SwingPmMark | None: ...


class PolymarketOrderBookSwingMarkProvider:
    """Expose current in-memory PM YES-token midpoints to the pure risk layer."""

    def __init__(self, books: OrderBookStore) -> None:
        self._books = books

    def mark_for(self, token_id: str) -> SwingPmMark | None:
        book = self._books.snapshot(token_id, depth=1)
        if book is None or book.midpoint is None:
            return None
        return SwingPmMark(
            pm_condition_id=book.market_id,
            midpoint=float(book.midpoint),
            observed_at=book.observed_at,
        )


@dataclass(frozen=True, slots=True)
class SwingRiskControl:
    shock_points: float
    max_loss: float
    max_pm_mark_age_ns: int
    crosswalk: SwingCrosswalk
    mark_provider: SwingPmMarkProvider | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.shock_points) or self.shock_points <= 0.0:
            raise ValueError("swing shock_points must be finite and positive")
        if not math.isfinite(self.max_loss) or self.max_loss <= 0.0:
            raise ValueError("swing max_loss must be finite and positive")
        if self.max_pm_mark_age_ns <= 0:
            raise ValueError("swing max PM mark age must be positive")


@dataclass(frozen=True, slots=True)
class SwingRiskDiagnostics:
    positive_swing_loss: float
    negative_swing_loss: float
    net_derivative_per_point: float
    house_derivative_per_point: float
    senate_derivative_per_point: float
    governor_derivative_per_point: float


@dataclass(frozen=True, slots=True)
class _Exposure:
    exchange_id: str
    market_id: str
    tournament_id: str
    signed_quantity: float
    optional_fill: bool
    unknown_direction: bool = False


def load_swing_crosswalk(
    mapping: MappingDocument,
    *,
    mapping_path: Path,
) -> SwingCrosswalk:
    """Load swing identities only from a byte-accepted MAPPING-001 artifact."""
    failure_clause = _swing_crosswalk_verification_failure(mapping, mapping_path)
    if failure_clause is not None:
        _LOG.warning(
            "swing crosswalk verification failed mapping_path=%s clause=%s",
            mapping_path,
            failure_clause,
        )
        return SwingCrosswalk(verified=False)

    markets = tuple(
        market
        for record in mapping.records
        if (market := _swing_market(record)) is not None
    )
    return SwingCrosswalk(verified=True, markets=markets)


def _swing_crosswalk_verification_failure(
    mapping: MappingDocument,
    mapping_path: Path,
) -> str | None:
    acceptance_path = mapping_path.with_name(f"{mapping_path.stem}_acceptance.json")
    try:
        mapping_bytes = mapping_path.read_bytes()
    except Exception as exc:
        return f"crosswalk_json_read_error:{type(exc).__name__}"

    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"acceptance_json_read_error:{type(exc).__name__}"

    if not isinstance(acceptance, dict):
        return "acceptance_document_not_object"
    if acceptance.get("all_records_verified") is not True:
        return "all_records_verified_false"

    artifact_sha256 = acceptance.get("artifact_sha256")
    if not isinstance(artifact_sha256, dict):
        return "artifact_sha256_missing"
    if artifact_sha256.get("crosswalk_json") != hashlib.sha256(mapping_bytes).hexdigest():
        return "artifact_sha256.crosswalk_json_mismatch"

    smoke = acceptance.get("clob_smoke")
    if not isinstance(smoke, dict):
        return "clob_smoke_missing"
    if smoke.get("passed") is not True:
        return "clob_smoke.passed_false"

    try:
        accepted_document = MappingDocument.model_validate(
            json.loads(mapping_bytes.decode("utf-8"))
        )
    except Exception as exc:
        return f"accepted_mapping_invalid:{type(exc).__name__}"

    if not all(record.status is MappingStatus.VERIFIED for record in accepted_document.records):
        return "accepted_records_not_all_verified"
    if accepted_document.tournament_id != mapping.tournament_id:
        return "runtime_mapping_tournament_id_mismatch"
    if accepted_document.schema_version != mapping.schema_version:
        return "runtime_mapping_schema_version_mismatch"

    accepted_records = {
        record.sig_exchange_id: record.model_dump(mode="json")
        for record in accepted_document.normalized().records
    }
    runtime_records = mapping.normalized().records
    if any(
        accepted_records.get(record.sig_exchange_id) != record.model_dump(mode="json")
        for record in runtime_records
    ):
        return "runtime_mapping_not_accepted_subset"
    return None


def _swing_market(record: MarketMapping) -> SwingMappedMarket | None:
    if (
        record.status is not MappingStatus.VERIFIED
        or record.mapping_class not in {MappingClass.EXACT, MappingClass.NEAR}
        or record.mapping_direction not in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
        or record.sig_outcome_label.casefold() != "yes"
        or record.direct_polymarket is None
    ):
        return None

    party = _party_and_target(record.sig_market_title)
    if party is None:
        return None
    party_sign, target = party
    chamber = _chamber(target)
    direct = record.direct_polymarket
    if chamber is None or direct.event_id is None:
        return None
    yes_tokens = tuple(
        token_id
        for outcome, token_id in zip(direct.outcomes, direct.token_ids, strict=True)
        if outcome.casefold() == "yes"
    )
    if len(yes_tokens) != 1 or direct.mapped_outcome.casefold() not in {"yes", "no"}:
        return None

    return SwingMappedMarket(
        exchange_id=record.sig_exchange_id,
        market_id=record.sig_market_id,
        tournament_id=record.sig_tournament_id,
        race_id=f"pm-event:{direct.event_id}",
        chamber=chamber,
        party_sign=party_sign,
        pm_condition_id=direct.condition_id,
        pm_yes_token_id=yes_tokens[0],
        sig_yes_is_pm_yes=(
            (record.mapping_direction is MappingDirection.SAME)
            == (direct.mapped_outcome.casefold() == "yes")
        ),
    )


_PARTY_TITLE = re.compile(
    r"^Will the (Democratic|Republican) Party win (?:the )?(.+)\?$",
    re.IGNORECASE,
)


def _party_and_target(title: str) -> tuple[int, str] | None:
    match = _PARTY_TITLE.fullmatch(title.strip())
    if match is None:
        return None
    party = match.group(1).casefold()
    return (1 if party == "democratic" else -1), match.group(2).strip()


def _chamber(target: str) -> str | None:
    words = set(re.findall(r"[a-z]+", target.casefold()))
    matches = [
        chamber
        for word, chamber in (
            ("house", "House"),
            ("senate", "Senate"),
            ("governor", "Governor"),
        )
        if word in words
    ]
    return matches[0] if len(matches) == 1 else None


def evaluate_swing_cap(
    control: SwingRiskControl,
    snapshot: RuntimeSnapshot,
    intents: tuple[RuntimeOrderIntent, ...],
) -> tuple[str | None, SwingRiskDiagnostics | None]:
    """Return a denial reason and diagnostics for one projected risk decision."""
    if _candidate_is_reducing(snapshot, intents):
        return None, None
    if not control.crosswalk.verified:
        return "swing_mapping_unverified", None
    if snapshot.portfolio.account_proxy_uncertainty > 0.0:
        return "swing_exposure_unmapped", None
    if control.mark_provider is None:
        return "swing_pm_mark_missing", None

    positions, pending, candidates = _collect_exposures(snapshot, intents)
    active = positions + pending + candidates
    mapped_by_exchange: dict[str, SwingMappedMarket] = {}
    probabilities: dict[str, float] = {}
    now = datetime.now(UTC)
    for exposure in active:
        mapped = mapped_by_exchange.get(exposure.exchange_id)
        if mapped is None:
            mapped = control.crosswalk.market_for(exposure.exchange_id)
            if (
                mapped is None
                or mapped.market_id != exposure.market_id
                or mapped.tournament_id != exposure.tournament_id
            ):
                return "swing_market_unmapped", None
            mapped_by_exchange[exposure.exchange_id] = mapped

            mark = control.mark_provider.mark_for(mapped.pm_yes_token_id)
            if mark is None or mark.pm_condition_id != mapped.pm_condition_id:
                return "swing_pm_mark_missing", None
            if (
                mark.observed_at.tzinfo is None
                or mark.observed_at.utcoffset() is None
                or not math.isfinite(mark.midpoint)
                or not 0.0 < mark.midpoint < 1.0
            ):
                return "swing_pm_mark_invalid", None
            age_ns = int((now - mark.observed_at.astimezone(UTC)).total_seconds() * 1_000_000_000)
            if age_ns < 0 or age_ns > control.max_pm_mark_age_ns:
                return "swing_pm_mark_stale", None
            probabilities[exposure.exchange_id] = (
                mark.midpoint if mapped.sig_yes_is_pm_yes else 1.0 - mark.midpoint
            )

    up_pnl = _scenario_pnl(
        positions,
        pending,
        candidates,
        mapped_by_exchange,
        probabilities,
        control.shock_points,
    )
    down_pnl = _scenario_pnl(
        positions,
        pending,
        candidates,
        mapped_by_exchange,
        probabilities,
        -control.shock_points,
    )
    baseline_up_pnl = _scenario_pnl(
        positions,
        pending,
        [],
        mapped_by_exchange,
        probabilities,
        control.shock_points,
    )
    baseline_down_pnl = _scenario_pnl(
        positions,
        pending,
        [],
        mapped_by_exchange,
        probabilities,
        -control.shock_points,
    )
    baseline_worst_case_loss = max(
        0.0,
        -baseline_up_pnl,
        -baseline_down_pnl,
    )
    diagnostics = _diagnostics(
        positions + pending + candidates,
        mapped_by_exchange,
        probabilities,
        control,
        up_pnl=up_pnl,
        down_pnl=down_pnl,
    )
    projected_worst_case_loss = max(
        diagnostics.positive_swing_loss,
        diagnostics.negative_swing_loss,
    )
    if (
        projected_worst_case_loss > control.max_loss
        and projected_worst_case_loss > baseline_worst_case_loss
    ):
        return "swing_loss_limit", diagnostics
    return None, diagnostics


def _candidate_is_reducing(
    snapshot: RuntimeSnapshot,
    intents: tuple[RuntimeOrderIntent, ...],
) -> bool:
    deltas: dict[tuple[str, str], float] = {}
    for intent in intents:
        direction = _signed_yes_direction(intent.outcome_side.value, intent.action.value)
        identity = (intent.exchange_id, intent.tournament_id)
        deltas[identity] = deltas.get(identity, 0.0) + direction * intent.quantity
    return bool(deltas) and all(
        snapshot.portfolio.is_inventory_reducing(
            exchange_id=exchange_id,
            tournament_id=tournament_id,
            signed_delta=delta,
        )
        for (exchange_id, tournament_id), delta in deltas.items()
        if delta != 0.0
    ) and all(delta != 0.0 for delta in deltas.values())


def _signed_yes_direction(side: str, action: str) -> float:
    if side == "yes":
        return 1.0 if action == "buy" else -1.0
    return -1.0 if action == "buy" else 1.0


def _collect_exposures(
    snapshot: RuntimeSnapshot,
    intents: tuple[RuntimeOrderIntent, ...],
) -> tuple[list[_Exposure], list[_Exposure], list[_Exposure]]:
    positions = [
        _Exposure(
            exchange_id=position.exchange_id,
            market_id=position.market_id,
            tournament_id=position.tournament_id,
            signed_quantity=(
                math.copysign(
                    max(abs(position.signed_quantity), position.gross_exposure),
                    position.signed_quantity,
                )
                if position.signed_quantity != 0.0
                else position.gross_exposure
            ),
            optional_fill=False,
            unknown_direction=position.signed_quantity == 0.0 and position.gross_exposure > 0.0,
        )
        for position in snapshot.portfolio.positions
        if position.signed_quantity != 0.0 or position.gross_exposure > 0.0
    ]
    pending = [
        _Exposure(
            exchange_id=order.exchange_id,
            market_id=order.market_id,
            tournament_id=order.tournament_id,
            signed_quantity=(
                math.copysign(
                    max(abs(order.signed_quantity), order.reserved_exposure),
                    order.signed_quantity,
                )
                if order.signed_quantity != 0.0
                else order.reserved_exposure
            ),
            optional_fill=True,
            unknown_direction=order.signed_quantity == 0.0 and order.reserved_exposure > 0.0,
        )
        for order in snapshot.portfolio.orders
        if (order.open or order.uncertain)
        and (order.signed_quantity != 0.0 or order.reserved_exposure > 0.0)
    ]
    candidates = [
        _Exposure(
            exchange_id=intent.exchange_id,
            market_id=intent.market_id,
            tournament_id=intent.tournament_id,
            signed_quantity=_signed_yes_direction(
                intent.outcome_side.value,
                intent.action.value,
            )
            * intent.quantity,
            optional_fill=False,
        )
        for intent in intents
    ]
    return positions, pending, candidates


def _scenario_pnl(
    positions: list[_Exposure],
    pending: list[_Exposure],
    candidates: list[_Exposure],
    mappings: dict[str, SwingMappedMarket],
    probabilities: dict[str, float],
    swing_points: float,
) -> float:
    pnl = 0.0
    for exposure in positions + pending + candidates:
        mapped = mappings[exposure.exchange_id]
        p = probabilities[exposure.exchange_id]
        shifted = _logistic(
            math.log(p / (1.0 - p))
            + LOGIT_PER_SWING_POINT * swing_points * mapped.party_sign
        )
        per_share = shifted - p
        if exposure.unknown_direction:
            pnl -= abs(per_share) * abs(exposure.signed_quantity)
        else:
            contribution = exposure.signed_quantity * per_share
            if exposure.optional_fill and contribution > 0.0:
                continue
            pnl += contribution
    return pnl


def _diagnostics(
    exposures: list[_Exposure],
    mappings: dict[str, SwingMappedMarket],
    probabilities: dict[str, float],
    control: SwingRiskControl,
    *,
    up_pnl: float,
    down_pnl: float,
) -> SwingRiskDiagnostics:
    derivative_by_chamber: dict[str, list[float]] = {
        "House": [0.0, 0.0],
        "Senate": [0.0, 0.0],
        "Governor": [0.0, 0.0],
    }
    for exposure in exposures:
        mapped = mappings[exposure.exchange_id]
        p = probabilities[exposure.exchange_id]
        coefficient = (
            mapped.party_sign
            * LOGIT_PER_SWING_POINT
            * p
            * (1.0 - p)
        )
        values = derivative_by_chamber.setdefault(mapped.chamber, [0.0, 0.0])
        if exposure.unknown_direction:
            values[1] += abs(coefficient * exposure.signed_quantity)
        else:
            values[0] += coefficient * exposure.signed_quantity

    subtotals = {
        chamber: _largest_magnitude_endpoint(known, uncertain)
        for chamber, (known, uncertain) in derivative_by_chamber.items()
    }
    derivative = sum(subtotals.values())
    return SwingRiskDiagnostics(
        positive_swing_loss=max(0.0, -up_pnl),
        negative_swing_loss=max(0.0, -down_pnl),
        net_derivative_per_point=derivative,
        house_derivative_per_point=subtotals["House"],
        senate_derivative_per_point=subtotals["Senate"],
        governor_derivative_per_point=subtotals["Governor"],
    )


def _largest_magnitude_endpoint(known: float, uncertain: float) -> float:
    lower = known - uncertain
    upper = known + uncertain
    return lower if abs(lower) > abs(upper) else upper


def _logistic(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)
