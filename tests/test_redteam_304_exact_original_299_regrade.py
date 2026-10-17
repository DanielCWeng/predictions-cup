"""Independent original Red Team #299 immutable regression and extra invented identity checks.

Research-only offline tests, exact frozen #299 witness bytes, no real source admission.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from predictions_cup.research.journal_maker_adapter import (
    FillDedupeRow,
    normalise_journal,
)
from test_redteam_296_identity_and_proof import _env, _ev

FROZEN_299_TEST_GIT_BLOB = "91265db2daa2222eb5643d77885421bef25c23fe"


def test_original_redteam_299_byte_identical_strict_file() -> None:
    path = Path(__file__).with_name("test_redteam_296_identity_and_proof.py")
    original = path.read_bytes()
    header = b"blob " + str(len(original)).encode() + b"\x00"
    assert hashlib.sha1(header + original).hexdigest() == FROZEN_299_TEST_GIT_BLOB


def test_distinct_native_orders_cannot_share_same_original_native_fill_id() -> None:
    # Two original operations with different native ACK orders, but one
    # claimed issuer fill ID. Repeated native fill ID is NOT two economic fills.
    envs = (_env("op-one"), _env("op-two"))
    events = (
        _ev(1, "op-one", "SUBMISSION"),
        _ev(2, "op-one", "ACK", native_order="native-A"),
        _ev(3, "op-one", "REALTIME_FILL",
            native_order="native-A", native_fill="shared-issuer-fill"),
        _ev(4, "op-two", "SUBMISSION"),
        _ev(5, "op-two", "ACK", native_order="native-B"),
        _ev(6, "op-two", "AUTHORITATIVE_FILL",
            native_order="native-B", native_fill="shared-issuer-fill"),
    )
    result = normalise_journal(envs, events, ())
    assert result.candidates == ()
    assert any("CONFLICTING_NATIVE_FILL_ALIAS" in x for x in result.rejected_rows)
    assert result.locked_bundle is None and result.realised_net is None
    assert result.source_admitted is False and result.paper_admitted is False


def test_fake_fill_dedupe_alias_to_ack_with_spoofed_fill_id_atomic_reject() -> None:
    env = _env("op-one")
    events = (
        _ev(1, "op-one", "SUBMISSION"),
        _ev(2, "op-one", "ACK",
            native_order="native-A", native_fill="fake-fill"),
    )
    result = normalise_journal(
        (env,), events, (FillDedupeRow("faked-alias", 2),),
    )
    assert result.candidates == ()
    assert result.rejected_rows == ("INVALID_ALIAS_REFERENCE",)
    assert result.realised_net is None and result.source_admitted is False
