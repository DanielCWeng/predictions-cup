"""Canonical accepted SIG↔Polymarket fair-value provider for MAKE-001."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from predictions_cup.maker.contracts import FairValueResult, MakerMarketSnapshot
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import (
    MappingClass,
    MappingDirection,
    MappingDocument,
    MappingStatus,
    MarketMapping,
)


@dataclass(frozen=True, slots=True)
class _Probability:
    value: float
    uncertainty: float
    observed_monotonic_ns: int


class DirectPolymarketFairValueProvider:
    """Pure provider over a startup-loaded canonical mapping document.

    EXACT and NEAR mappings use the aligned mapped token directly.
    DERIVED is intentionally narrow: only mappings whose canonical notes explicitly
    identify a mutually-exclusive partition/union are summed. Other DERIVED shapes
    fail closed rather than guessing a formula.
    """

    provider_id = "direct-polymarket"
    version = "make-001-v1"

    def __init__(
        self,
        document: MappingDocument,
    ) -> None:
        self._records = {
            record.sig_exchange_id: record
            for record in document.records
        }
        if len(self._records) != len(document.records):
            raise ValueError("mapping document contains duplicate SIG exchanges")

    @classmethod
    def from_path(
        cls,
        path: Path,
    ) -> DirectPolymarketFairValueProvider:
        """Startup helper. File I/O never occurs in fair_value()."""
        return cls(load_document(path))

    def fair_value(
        self,
        snapshot: MakerMarketSnapshot,
    ) -> FairValueResult:
        record = self._records.get(snapshot.exchange_id)
        if record is None:
            return self._unavailable("missing_mapping")
        if record.sig_market_id != snapshot.market_id:
            return self._unavailable("mapping_market_mismatch", record)
        if record.sig_tournament_id != snapshot.tournament_id:
            return self._unavailable("mapping_tournament_mismatch", record)
        if record.status is not MappingStatus.VERIFIED:
            return self._unavailable("mapping_not_verified", record)
        if record.mapping_class in {MappingClass.NO_TRADE, MappingClass.MODEL_ONLY}:
            return self._unavailable("mapping_not_directly_tradeable", record)

        if record.mapping_class in {MappingClass.EXACT, MappingClass.NEAR}:
            assert record.direct_polymarket is not None
            probability = self._token_probability(
                token_id=record.direct_polymarket.mapped_token_id,
                snapshot=snapshot,
            )
            if probability is None:
                return self._unavailable("polymarket_source_unusable", record)
            value = probability.value
            if record.mapping_direction is MappingDirection.COMPLEMENT:
                value = 1.0 - value
            elif record.mapping_direction is not MappingDirection.SAME:
                return self._unavailable("unsupported_direct_direction", record)
            return self._available(
                value=value,
                uncertainty=probability.uncertainty,
                observed_ns=probability.observed_monotonic_ns,
                record=record,
            )

        if record.mapping_class is MappingClass.DERIVED:
            if not self._is_explicit_partition_sum(record):
                return self._unavailable("derived_formula_not_explicit", record)
            parts: list[_Probability] = []
            for component in record.polymarket_components:
                probability = self._token_probability(
                    token_id=component.mapped_token_id,
                    snapshot=snapshot,
                )
                if probability is None:
                    return self._unavailable("derived_component_unusable", record)
                parts.append(probability)
            value = sum(part.value for part in parts)
            uncertainty = sum(part.uncertainty for part in parts)
            if not math.isfinite(value) or value < -1e-9 or value > 1.0 + 1e-9:
                return self._unavailable("derived_probability_out_of_bounds", record)
            return self._available(
                value=min(1.0, max(0.0, value)),
                uncertainty=min(1.0, uncertainty),
                observed_ns=min(part.observed_monotonic_ns for part in parts),
                record=record,
            )

        return self._unavailable("unsupported_mapping_class", record)

    def _token_probability(
        self,
        *,
        token_id: str,
        snapshot: MakerMarketSnapshot,
    ) -> _Probability | None:
        quote = snapshot.external_quotes.get(token_id)
        if quote is None or not quote.trusted:
            return None
        age = snapshot.now_monotonic_ns - quote.observed_monotonic_ns
        if age < 0:
            return None
        bid = quote.best_bid
        ask = quote.best_ask
        if bid is None or ask is None:
            return None
        if not (math.isfinite(bid) and math.isfinite(ask)):
            return None
        if not (0.0 <= bid <= ask <= 1.0):
            return None
        return _Probability(
            value=(bid + ask) * 0.5,
            uncertainty=(ask - bid) * 0.5,
            observed_monotonic_ns=quote.observed_monotonic_ns,
        )

    @staticmethod
    def _is_explicit_partition_sum(record: MarketMapping) -> bool:
        notes = " ".join(
            value.lower()
            for value in (record.semantic_notes, record.resolution_notes)
            if value is not None
        )
        return (
            ("union/sum" in notes or "sum of" in notes)
            and ("mutually exclusive" in notes or "partition" in notes)
        )

    def _available(
        self,
        *,
        value: float,
        uncertainty: float,
        observed_ns: int,
        record: MarketMapping,
    ) -> FairValueResult:
        return FairValueResult(
            value=value,
            uncertainty=max(0.0, uncertainty),
            confidence=float(record.mapping_confidence),
            observed_monotonic_ns=observed_ns,
            trusted=True,
            source_id=self.provider_id,
            source_version=self.version,
            mapping_class=record.mapping_class.value,
            reason="ok",
        )

    def _unavailable(
        self,
        reason: str,
        record: MarketMapping | None = None,
    ) -> FairValueResult:
        return FairValueResult(
            value=None,
            uncertainty=0.0,
            confidence=0.0,
            observed_monotonic_ns=0,
            trusted=False,
            source_id=self.provider_id,
            source_version=self.version,
            mapping_class="UNKNOWN" if record is None else record.mapping_class.value,
            reason=reason,
        )
