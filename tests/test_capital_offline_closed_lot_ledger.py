"""Inert invented-only ledger contract tests; never call native clients."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from predictions_cup.research.offline_closed_lot_ledger import (
    AccountFrame,
    CapitalSlice,
    MakerAttempt,
    NativeEntryFill,
    NativeLotClosure,
    OfflineBundle,
    OriginalDocument,
    audit_offline,
)

D = Decimal
START = datetime(2026, 10, 1, tzinfo=UTC)


def doc(key: str, at: datetime) -> OriginalDocument:
    content = f"FICTIONAL:{key}".encode()
    return OriginalDocument(
        key=key, issuer_reference=f"FAKE-ISSUER:{key}",
        raw_bytes=content, original_sha256=hashlib.sha256(content).hexdigest(),
        issuer_rule_version="FAKE-CONTRACT-1", source_namespace_reference="INVENTED",
        permitted_use_reference="TEST-ONLY", independent_custodian_reference="FAKE-CUSTODIAN",
        issuer_at=at, received_at=at + timedelta(minutes=1), revision=f"FAKE:{key}",
    )


def example() -> OfflineBundle:
    """Two COMPLETE fictitious YES/NO original-side lots, never native evidence."""
    enter = START
    exit_at = START + timedelta(hours=1)
    asof = START + timedelta(days=1)
    docs = (doc("entry", enter), doc("closed", exit_at), doc("account", asof))
    attempts = (
        MakerAttempt("att-a", "order-a", "T-1", "E-1", "M-YES", "YES", D("2"),
                     "MAKER", "FAKE-ROLE", "TERMINAL", "FAKE-TERMINAL", D("0"), "entry"),
        MakerAttempt("att-b", "order-b", "T-1", "E-1", "M-NO", "NO", D("3"),
                     "MAKER", "FAKE-ROLE", "TERMINAL", "FAKE-TERMINAL", D("0"), "entry"),
    )
    fills = (
        NativeEntryFill("fill-a", "att-a", "order-a", "lot-a", D("2"), D(".4"),
                        D(".8"), D(".02"), D("0"), "SUSQ", "entry"),
        # NO's paid entry basis is *directly* .3*3=.9; do NOT complement it to .7.
        NativeEntryFill("fill-b", "att-b", "order-b", "lot-b", D("3"), D(".3"),
                        D(".9"), D(".03"), D("0"), "SUSQ", "entry"),
    )
    attempts = tuple(
        replace(a, settlement_contract_reference="FAKE-CONTRACT-1",
                trading_cutoff_at_utc=START + timedelta(days=1))
        for a in attempts
    )
    fills = (replace(fills[0], signed_yes_whole_shares=D("2")),
             replace(fills[1], signed_yes_whole_shares=D("-3")))
    closures = (
        NativeLotClosure("close-a", "lot-a", D("2"), "SETTLEMENT", D("2"),
                         D(".02"), D("0"), "SUSQ", "FAKE-CONTRACT-1",
                         "FAKE-FINAL", True, "closed"),
        NativeLotClosure("close-b", "lot-b", D("3"), "SETTLEMENT", D("3"),
                         D(".03"), D("0"), "SUSQ", "FAKE-CONTRACT-1",
                         "FAKE-FINAL", True, "closed"),
    )
    account = AccountFrame(
        "account", asof, D("100"), D("5"), D("8"), D("-3"), D("110"),
        D("8"), D("0"), D("100"),
    )
    slices = (
        CapitalSlice("entry", START, START + timedelta(hours=12),
                     D("10"), D("2"), D("100"), False),
        CapitalSlice("closed", START + timedelta(hours=12), asof,
                     D("8"), D("0"), D("100"), False),
    )
    return OfflineBundle("SYNTHETIC", docs, attempts, fills, closures, account,
                         slices, (START, asof), "INVENTED-NAMESPACE-NOT-PROOF")


def test_invented_yes_no_closures_fees_and_double_entry_remain_unadmitted() -> None:
    r = audit_offline(example())
    assert r.verdict == "SCHEMA_ONLY" and r.reasons == ()
    # YES 2-.8-.02-.02=1.16; NO 3-.9-.03-.03=2.04 => 3.20.
    assert r.descriptive_closed_lot_net_susq == D("3.20")
    # 12 gross+reserved shares for half-day plus 8 for next full half-day => 10.
    assert r.gross_share_days == D("10.0")
    assert r.account_identity_pass is True
    assert r.maker_closed_lots_descriptive == 2
    assert r.eligible_attempts_descriptive == 2
    assert r.realised_maker_net_susq is None
    assert r.independently_verified_maker_denominator is None
    assert r.real_alpha is None
    assert r.source_admitted is False and r.paper_admitted is False
    # Account realised=8, invented closed maker arithmetic=3.2; never relabel.
    account = example().account
    assert account is not None
    assert account.account_realised_susq == D("8")


def test_partial_exit_uncertain_void_censors_lot_and_total_net() -> None:
    b = example()
    a = replace(b.attempts[1], terminal_state="UNCERTAIN", native_terminal_reference=None,
                reserved_gross_shares=D("2"))
    v = replace(b.closures[1], whole_shares=D("1"), kind="VOID",
                final=False, settlement_rule_version=None, issuer_terminal_reference=None)
    r = audit_offline(replace(b, attempts=(b.attempts[0], a),
                              closures=(b.closures[0], v)))
    assert r.verdict == "INCOMPLETE"
    assert "UNCERTAIN_OR_UNATTESTED_TERMINAL_STATE" in r.reasons
    assert "OPEN_OR_PARTIALLY_CLOSED_OR_OVERFILLED_LOT" in r.reasons
    assert "MISSING_VOID_RECOUNT_SETTLEMENT_ORIGINAL_RULE" in r.reasons
    assert r.descriptive_closed_lot_net_susq is None and r.realised_maker_net_susq is None


def test_cross_tournament_raw_namespace_and_same_tournament_collision_both_block() -> None:
    b = example()
    second = replace(b.attempts[1], tournament="T-2",
                     native_order_id=b.attempts[0].native_order_id)
    second_fill = replace(b.fills[1], key=b.fills[0].key,
                          native_order_id=b.fills[0].native_order_id)
    cross = audit_offline(replace(b, attempts=(b.attempts[0], second),
                                  fills=(b.fills[0], second_fill),
                                  native_namespace_policy_version=None))
    assert cross.verdict == "REJECTED"
    assert "NATIVE_ORDER_COLLISION_CROSS_TOURNAMENT_SCOPE_UNKNOWN" in cross.reasons
    assert "DUPLICATE_NATIVE_FILL_OR_LOT" in cross.reasons
    assert "ORIGINAL_NATIVE_ORDER_FILL_NAMESPACE_UNVERIFIED" in cross.reasons
    assert cross.source_admitted is False and cross.descriptive_closed_lot_net_susq is None
    same = audit_offline(replace(b, attempts=(b.attempts[0], replace(second, tournament="T-1")),
                                 fills=(b.fills[0], second_fill)))
    assert same.verdict == "REJECTED"
    assert "NATIVE_ORDER_COLLISION_SAME_TOURNAMENT" in same.reasons


def test_negative_fee_or_rebate_and_absent_paid_fee_are_not_assumed_zero() -> None:
    b = example()
    for replacement in (
        replace(b.fills[0], paid_fee_susq=D("-.01")),
        replace(b.fills[0], rebate_susq=None),
        replace(b.fills[0], paid_fee_susq=None),
    ):
        r = audit_offline(replace(b, fills=(replacement, b.fills[1])))
        assert r.verdict != "SCHEMA_ONLY"
        assert r.descriptive_closed_lot_net_susq is None
        assert r.source_admitted is False


def test_bad_source_sha_account_clock_gross_and_shareday_gap_fail_closed() -> None:
    b = example()
    bad_doc = replace(b.sources[0], raw_bytes=b"tampered")
    bad_account = replace(b.account, end_equity_susq=D("111")) if b.account else None
    gap = replace(b.capital_slices[1], start_utc=START + timedelta(hours=13))
    r = audit_offline(replace(b, sources=(bad_doc, *b.sources[1:]),
                              account=bad_account,
                              capital_slices=(b.capital_slices[0], gap)))
    assert r.verdict == "REJECTED"
    assert "SOURCE_BYTES_SHA256_MISMATCH" in r.reasons
    assert "ACCOUNT_DOUBLE_ENTRY_MISMATCH" in r.reasons
    assert "GAP_OVERLAP_OR_INVALID_GROSS_SHARE_DAY_CLOCK" in r.reasons
    assert r.gross_share_days is None
    assert r.descriptive_closed_lot_net_susq is None
    assert r.account_identity_pass is False


def test_negative_rebate_or_missing_exit_cash_or_stale_account_never_promotes() -> None:
    b = example()
    for c in (
        replace(b.closures[0], rebate_susq=D("-0.01")),
        replace(b.closures[0], original_cash_received=None),
        replace(b.closures[0], final=False),
    ):
        r = audit_offline(replace(b, closures=(c, b.closures[1])))
        assert r.descriptive_closed_lot_net_susq is None
        assert not r.source_admitted and not r.paper_admitted
    if b.account is not None:
        stale = audit_offline(replace(b, account=replace(
            b.account, asof_utc=b.account.asof_utc - timedelta(seconds=1))))
        assert stale.verdict == "REJECTED"
        assert "ACCOUNT_ASOF_NOT_ISSUER_SNAPSHOT_CLOCK" in stale.reasons


def test_unverified_original_caller_strings_do_not_authenticate_source() -> None:
    b = example()
    claimed = replace(b, kind="UNVERIFIED_ORIGINAL",
                      native_namespace_policy_version="CLAIMED-VERSION")
    r = audit_offline(claimed)
    assert r.verdict == "INCOMPLETE"
    assert "EXTERNAL_ISSUER_CUSTODIAN_RIGHTS_AUTHENTICATION_REQUIRED" in r.reasons
    assert r.eligible_attempts_descriptive is None
    assert r.realised_maker_net_susq is None
    assert r.descriptive_closed_lot_net_susq is None
    assert r.source_admitted is False and r.paper_admitted is False
    agg = audit_offline(replace(b, kind="AGGREGATE"))
    assert "AGGREGATE_IS_NOT_ORIGINAL_LOT_EVIDENCE" in agg.reasons
    assert agg.realised_maker_net_susq is None


def test_signed_no_basis_cutoff_and_multi_leg_orphan_are_fail_closed() -> None:
    b = example()
    bad_sign = audit_offline(replace(
        b, fills=(b.fills[0], replace(b.fills[1], signed_yes_whole_shares=D("3")))
    ))
    assert bad_sign.verdict == "REJECTED"
    assert "WRONG_YES_NO_SIGN_OR_COMPLEMENT" in bad_sign.reasons
    assert bad_sign.descriptive_closed_lot_net_susq is None

    expired = audit_offline(replace(
        b, attempts=(replace(b.attempts[0],
                             trading_cutoff_at_utc=START - timedelta(seconds=1)),
                     b.attempts[1]),
    ))
    assert expired.verdict == "REJECTED"
    assert "ORDER_AFTER_TRADING_CUTOFF" in expired.reasons

    orphan = audit_offline(replace(
        b, attempts=(replace(b.attempts[0], multi_leg_group_id="FAKE-ATOMIC",
                             multi_leg_expected_legs=2), b.attempts[1]),
    ))
    assert orphan.verdict == "INCOMPLETE"
    assert "MULTI_LEG_ORPHAN_OR_INCOMPLETE_GROUP" in orphan.reasons
    assert orphan.realised_maker_net_susq is None and not orphan.source_admitted

    complete_group = audit_offline(replace(
        b, attempts=tuple(replace(a, multi_leg_group_id="FAKE-ATOMIC",
                                  multi_leg_expected_legs=2) for a in b.attempts),
    ))
    assert complete_group.verdict == "SCHEMA_ONLY"
    assert complete_group.source_admitted is False and complete_group.real_alpha is None
