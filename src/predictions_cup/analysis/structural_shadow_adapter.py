"""SHADOW-002 adapter for structural certificates; never sends orders."""

from __future__ import annotations

from typing import Protocol

from predictions_cup.analysis.structural_certificates import (
    StructuralCertificate,
    StructuralStatus,
)
from predictions_cup.shadow.contracts import (
    CandidateOutput,
    CanonicalShadowSnapshot,
    DecisionStatus,
)


class StructuralCertificateProvider(Protocol):
    def certificate_for(
        self,
        market_id: str,
    ) -> StructuralCertificate | None: ...


class StructuralCertificateCandidate:
    """Publish current structural evidence onto the existing SHADOW candidate bus."""

    candidate_id = "structural-certificate"
    candidate_version = "struct-scan-001-v1"
    strategy_family = "STRUCT"

    def __init__(self, provider: StructuralCertificateProvider) -> None:
        self._provider = provider

    def evaluate(self, snapshot: CanonicalShadowSnapshot) -> CandidateOutput:
        certificate = self._provider.certificate_for(snapshot.market_id)
        if certificate is None:
            return CandidateOutput(
                status=DecisionStatus.NOT_READY,
                abstain_reason="STRUCTURAL_CERTIFICATE_UNAVAILABLE",
                quality_flags=("SHADOW_ONLY",),
            )

        payload = {
            "certificate_id": certificate.certificate_id,
            "relationship_id": certificate.relationship_id,
            "event_group_id": certificate.event_group_id,
            "relationship_type": certificate.relationship_type,
            "certificate_status": certificate.certificate_status.value,
            "net_edge": certificate.net_edge,
            "available_size": certificate.available_size,
            "fee_rate": certificate.fee_rate,
            "fees": certificate.fees,
            "slippage_assumption": certificate.slippage_assumption,
            "slippage_cost": certificate.slippage_cost,
            "minimum_net_edge_per_bundle": certificate.minimum_net_edge_per_bundle,
            "semantic_proof_version": certificate.semantic_proof_version,
            "semantic_proof_hash": certificate.semantic_proof_hash,
            "mapping_hash": certificate.mapping_hash,
            "execution_authority": "SHADOW_ONLY",
        }
        if snapshot.market_id not in certificate.leg_ids:
            return CandidateOutput(
                status=DecisionStatus.ABSTAIN,
                abstain_reason="SNAPSHOT_MARKET_NOT_CERTIFICATE_LEG",
                quality_flags=("SHADOW_ONLY",),
                candidate_payload=payload,
            )
        if (
            certificate.certificate_status
            is not StructuralStatus.EXECUTABLE_VIOLATION
        ):
            return CandidateOutput(
                status=DecisionStatus.ABSTAIN,
                abstain_reason=certificate.certificate_status.value,
                quality_flags=("SHADOW_ONLY",),
                candidate_payload=payload,
            )
        return CandidateOutput(
            status=DecisionStatus.OK,
            score=certificate.net_edge,
            action_intent="STRUCTURAL_CERTIFICATE_OBSERVED",
            quality_flags=("SHADOW_ONLY",),
            candidate_payload=payload,
        )
