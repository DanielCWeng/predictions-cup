"""Fail-closed examples showing future research modules plugging into MAKE.

These are intentionally not production signals. If accidentally wired into the maker
they fail closed rather than silently creating alpha claims.
"""

from __future__ import annotations

from predictions_cup.maker.contracts import (
    FairValueResult,
    MakerMarketSnapshot,
    PredictiveAdjustment,
    ToxicityEstimate,
)


class EtsFairValueStub:
    provider_id = "ets-stub"
    version = "unimplemented"

    def fair_value(self, snapshot: MakerMarketSnapshot) -> FairValueResult:
        del snapshot
        return FairValueResult(
            value=None,
            uncertainty=0.0,
            confidence=0.0,
            observed_monotonic_ns=0,
            trusted=False,
            source_id=self.provider_id,
            source_version=self.version,
            mapping_class="ETS",
            reason="stub_not_implemented",
        )


class Pred006AdjustmentStub:
    model_id = "pred-006-stub"
    version = "unimplemented"

    def adjust(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> PredictiveAdjustment:
        del fair_value
        return PredictiveAdjustment(
            probability_shift=0.0,
            confidence=0.0,
            observed_monotonic_ns=snapshot.now_monotonic_ns,
            trusted=False,
            model_id=self.model_id,
            version=self.version,
        )


class UpdateHazard005FStub:
    model_id = "005f-update-hazard-stub"
    version = "unimplemented"

    def estimate(
        self,
        snapshot: MakerMarketSnapshot,
        fair_value: FairValueResult,
    ) -> ToxicityEstimate:
        del fair_value
        return ToxicityEstimate(
            update_hazard=0.0,
            adverse_selection=0.0,
            observed_monotonic_ns=snapshot.now_monotonic_ns,
            trusted=False,
            model_id=self.model_id,
            version=self.version,
        )
