"""Hostile certificate invariants, pinned PR #75."""
from datetime import UTC, datetime
import pytest
from predictions_cup.analysis import structural_certificates as s
T = datetime(2026,10,1,tzinfo=UTC)
def book(name):
    return s.ExecutableBook(name,T,(s.BookLevel(.39,10),),(s.BookLevel(.4,10),))
def relation(a='yes', b='no'):
    return s.complement_relationship(relationship_id='r',yes_instrument_id=a,
        no_instrument_id=b,semantic_proof_version='v',semantic_proof_hash='h',mapping_hash='m')

@pytest.mark.xfail(strict=True,reason="ASTRA-006: same instrument allowed as its own complement")
def test_same_token_cannot_be_its_own_complement():
    cert=s.evaluate_relationship(relation('x','x'),{'x':book('x')},observed_at=T)
    # Actual terminal payoff is 0 or 20 shares; worst-case profit is -8, not +2.
    assert cert.certificate_status != s.StructuralStatus.EXECUTABLE_VIOLATION

@pytest.mark.xfail(strict=True,reason="ASTRA-006: book dictionary keys not bound to identity")
def test_wrong_instrument_under_valid_key_rejected():
    cert=s.evaluate_relationship(relation(),{'yes':book('wrong'),'no':book('no')},observed_at=T)
    assert cert.certificate_status == s.StructuralStatus.MAPPING_INVALID
