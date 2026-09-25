"""Canonical SIG ↔ Polymarket mapping contracts for MAPPING-001."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from predictions_cup.models.common import (
    CanonicalModel,
    Identifier,
    NonBlankStr,
    Probability,
)


class MappingClass(StrEnum):
    EXACT = "EXACT"
    NEAR = "NEAR"
    DERIVED = "DERIVED"
    MODEL_ONLY = "MODEL_ONLY"
    NO_TRADE = "NO_TRADE"


class MappingDirection(StrEnum):
    SAME = "SAME"
    COMPLEMENT = "COMPLEMENT"
    DERIVED = "DERIVED"


class MappingStatus(StrEnum):
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"


class PolymarketContractIdentity(CanonicalModel):
    """One Polymarket market plus the exact aligned outcome/token used by a mapping."""

    market_id: Identifier
    condition_id: Identifier
    event_id: Identifier | None = None
    slug: NonBlankStr | None = None
    question: NonBlankStr
    outcomes: tuple[NonBlankStr, ...] = Field(min_length=1)
    token_ids: tuple[Identifier, ...] = Field(min_length=1)
    mapped_outcome: NonBlankStr
    mapped_token_id: Identifier

    @model_validator(mode="after")
    def validate_token_alignment(self) -> Self:
        if len(self.outcomes) != len(self.token_ids):
            raise ValueError("Polymarket outcomes and token_ids must remain aligned")
        pairs = tuple(zip(self.outcomes, self.token_ids, strict=True))
        if pairs.count((self.mapped_outcome, self.mapped_token_id)) != 1:
            raise ValueError("mapped Polymarket outcome/token pair is not uniquely aligned")
        if len(self.token_ids) != len(set(self.token_ids)):
            raise ValueError("Polymarket token_ids must be unique within a market")
        return self


class MarketMapping(CanonicalModel):
    """Canonical mapping record for exactly one SIG exchange."""

    sig_tournament_id: Identifier
    sig_market_id: Identifier
    sig_market_title: NonBlankStr
    sig_exchange_id: Identifier
    sig_outcome_label: NonBlankStr

    mapping_class: MappingClass
    mapping_direction: MappingDirection | None = None
    mapping_confidence: Probability
    status: MappingStatus

    direct_polymarket: PolymarketContractIdentity | None = None
    polymarket_components: tuple[PolymarketContractIdentity, ...] = ()
    candidate_polymarket_market_ids: tuple[Identifier, ...] = ()

    semantic_notes: NonBlankStr | None = None
    resolution_notes: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_mapping_shape(self) -> Self:
        direct_classes = {MappingClass.EXACT, MappingClass.NEAR}
        if self.mapping_class in direct_classes:
            if self.direct_polymarket is None:
                raise ValueError("EXACT/NEAR mapping requires direct Polymarket identity")
            if self.polymarket_components:
                raise ValueError("EXACT/NEAR mapping cannot also contain derived components")
            if self.mapping_direction not in {
                MappingDirection.SAME,
                MappingDirection.COMPLEMENT,
            }:
                raise ValueError("EXACT/NEAR mapping requires SAME or COMPLEMENT direction")
            if self.status is MappingStatus.UNRESOLVED:
                raise ValueError("direct mapping cannot be marked UNRESOLVED")
        elif self.mapping_class is MappingClass.DERIVED:
            if self.direct_polymarket is not None:
                raise ValueError("DERIVED mapping cannot claim a direct Polymarket identity")
            if len(self.polymarket_components) < 2:
                raise ValueError("DERIVED mapping requires at least two Polymarket components")
            if self.mapping_direction is not MappingDirection.DERIVED:
                raise ValueError("DERIVED mapping requires DERIVED direction")
            if self.status is MappingStatus.UNRESOLVED:
                raise ValueError("DERIVED mapping cannot be marked UNRESOLVED")
        else:
            if self.direct_polymarket is not None or self.polymarket_components:
                raise ValueError("MODEL_ONLY/NO_TRADE mapping cannot claim Polymarket tokens")
            if self.mapping_direction is not None:
                raise ValueError("MODEL_ONLY/NO_TRADE mapping cannot claim a direction")

        if self.status is MappingStatus.UNRESOLVED and self.mapping_class is not MappingClass.NO_TRADE:
            raise ValueError("UNRESOLVED records must fail closed as NO_TRADE")
        if len(self.candidate_polymarket_market_ids) != len(
            set(self.candidate_polymarket_market_ids)
        ):
            raise ValueError("candidate Polymarket market IDs must be unique")
        return self


class MappingDocument(CanonicalModel):
    """Deterministic canonical crosswalk document."""

    schema_version: Literal[1] = 1
    tournament_id: Identifier
    records: tuple[MarketMapping, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_exchange_coverage(self) -> Self:
        exchange_ids = tuple(record.sig_exchange_id for record in self.records)
        if len(exchange_ids) != len(set(exchange_ids)):
            raise ValueError("each SIG exchange must appear exactly once")
        if any(record.sig_tournament_id != self.tournament_id for record in self.records):
            raise ValueError("mapping records must use the document tournament_id")
        return self

    def normalized(self) -> MappingDocument:
        records = tuple(
            sorted(
                self.records,
                key=lambda record: (
                    record.sig_market_id,
                    record.sig_exchange_id,
                    record.sig_outcome_label,
                ),
            )
        )
        return self.model_copy(update={"records": records})

    def mapping_for_sig_exchange(self, exchange_id: str) -> MarketMapping:
        matches = tuple(record for record in self.records if record.sig_exchange_id == exchange_id)
        if len(matches) != 1:
            raise KeyError(f"SIG exchange {exchange_id!r} is not mapped exactly once")
        return matches[0]


class PolymarketOverrideLeg(CanonicalModel):
    market_id: Identifier
    outcome: NonBlankStr


class MappingOverride(CanonicalModel):
    """Explicit reviewer-owned decision; candidate matching never creates one."""

    sig_market_id: Identifier
    sig_exchange_id: Identifier
    sig_outcome_label: NonBlankStr
    mapping_class: MappingClass
    mapping_direction: MappingDirection | None = None
    mapping_confidence: Probability
    status: MappingStatus
    polymarket_legs: tuple[PolymarketOverrideLeg, ...] = ()
    semantic_notes: NonBlankStr | None = None
    resolution_notes: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_override_shape(self) -> Self:
        if self.status is MappingStatus.UNRESOLVED:
            raise ValueError("explicit overrides cannot be UNRESOLVED")
        if self.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}:
            if len(self.polymarket_legs) != 1:
                raise ValueError("EXACT/NEAR override requires exactly one Polymarket leg")
            if self.mapping_direction not in {
                MappingDirection.SAME,
                MappingDirection.COMPLEMENT,
            }:
                raise ValueError("EXACT/NEAR override requires SAME or COMPLEMENT direction")
        elif self.mapping_class is MappingClass.DERIVED:
            if len(self.polymarket_legs) < 2:
                raise ValueError("DERIVED override requires at least two Polymarket legs")
            if self.mapping_direction is not MappingDirection.DERIVED:
                raise ValueError("DERIVED override requires DERIVED direction")
        elif self.polymarket_legs or self.mapping_direction is not None:
            raise ValueError("MODEL_ONLY/NO_TRADE override cannot claim Polymarket tokens")
        return self


class MappingOverrideDocument(CanonicalModel):
    schema_version: Literal[1] = 1
    tournament_id: Identifier
    records: tuple[MappingOverride, ...] = ()

    @model_validator(mode="after")
    def validate_unique_exchange_ids(self) -> Self:
        exchange_ids = tuple(record.sig_exchange_id for record in self.records)
        if len(exchange_ids) != len(set(exchange_ids)):
            raise ValueError("mapping overrides must have unique SIG exchange IDs")
        return self
