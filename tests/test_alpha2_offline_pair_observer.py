"""Invented OFFLINE records only; no venue, filesystem, host, cloud or trade path."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from predictions_cup.research import offline_pair_observer as ob

_SHA = "a" * 40
_SIG = "2026-10-18T00:00:02Z"
_PM_SEED = "2026-10-18T00:00:00Z"
_PM_CHANGE = "2026-10-18T00:00:01Z"
_TOURNAMENT = "bda92870-621e-47b0-bc3c-3602c5c26f55"
_MAP = {
    "912": ("223", "0xe0fbb3e61e61b14f5fef15ffdd0e2a0c6cf4336bb5b15877fe3e3c9fdcc6bc7f",
            "94033987553817825990709512567031751048276730850154145976615728913102874968691"),
    "913": ("224", "0xf207d480dde3add509a4b1a355d4f3b06195f917de37ff94f7814d9277df0cdd",
            "70720420928389492978842914190430712299346582376255109200751838945374631181994"),
    "959": ("270", "0x66bbf6d55e0296278858b3147689f3df9259374f158f9f028b608baa322a639c",
            "7589880081658059374445882095611024032084645048848237404312287942463647821549"),
    "960": ("271", "0x2c00cb09722a0f0501c5e49131b898ed57bb5c075db4dc2bcbc14908f45b8c48",
            "2142948285506077695096498341598672159464223074468931276310105532825810210121"),
}


def observer(*, approved: bool = True) -> ob.OfflinePairObserver:
    # Test-only fake verifier is NOT a real operator authorisation.
    return ob.OfflinePairObserver(
        ob.OperatorRelease("FAKE-TEST-NOT-A-PERMIT", "0" * 64, "2027-01-01T00:00:00Z"),
        lambda release: approved and release.release_id.startswith("FAKE-TEST"),
    )


def book(
    kind: str, *, exchange: str = "912", received: str | None = None
) -> dict[str, Any]:
    m, condition, token = _MAP[exchange]
    when = received or (
        _PM_SEED if kind == "PM_FULL_SEED"
        else _PM_CHANGE if kind == "PM_BOOK_CHANGE"
        else "2026-10-18T00:00:03Z"
    )
    publisher = (
        _PM_SEED if kind == "PM_FULL_SEED"
        else _PM_CHANGE if kind == "PM_BOOK_CHANGE"
        else _SIG
    )
    row: dict[str, Any] = {
        "kind": kind,
        "source": {
            "SIG_FULL_BOOK": "SIG_REALTIME_NATIVE_FULL_BOOK",
            "PM_FULL_SEED": "PM_REST_SEED_FULL_BOOK",
            "PM_BOOK_CHANGE": "PM_WS_RECONSTRUCTED_FULL_BOOK",
        }[kind],
        "producer_git_sha": _SHA,
        "producer_run_id": "synthetic-run",
        "exchange_id": exchange,
        "market_id": m,
        "condition_id": condition,
        "token_id": token,
        "tournament_id": _TOURNAMENT,
        "outcome": "YES",
        "raw_source_sha256": "",
        "clock_uncertainty_ms": 0,
        "received_at": when,
        "publisher_at": publisher,
        "bids": [["0.42", "25"]],
        "asks": [["0.66", "25"]],
    }
    if kind == "SIG_FULL_BOOK":
        row.update({
            "connection_epoch": 1,
            "native_asof_sequence": 4,
            "native_asof_at": _SIG,
            "delivery_revision": 7,
            "previous_revision": 6,
            "source_sequence_from": 10,
            "source_sequence_through": 11,
            "resync_required": False,
            "has_full_book": True,
        })
        row["bids"] = [["0.40", "25"]]
        row["asks"] = [["0.60", "25"]]
    else:
        row.update({
            "connection_epoch": 1,
            "seed_epoch": 1,
            "local_receive_ordinal": 0 if kind == "PM_FULL_SEED" else 1,
            "local_drop_total": 0,
            "seed_complete": True,
        })
        if kind == "PM_BOOK_CHANGE":
            row["bids"] = [["0.65", "25"]]
            row["asks"] = [["0.67", "25"]]
    return row


def append(
    o: ob.OfflinePairObserver, row: dict[str, Any], payload: bytes | None = None
) -> str:
    # The non-identity source payload varies across fabricated events; reuse
    # across two distinct events is prohibited by the validator.
    if payload is None:
        payload = json.dumps(row, sort_keys=True).encode("ascii")
    row["raw_source_sha256"] = hashlib.sha256(payload).hexdigest()
    return o.ingest(row, payload)


def full_pair(o: ob.OfflinePairObserver, exchange: str = "912") -> None:
    append(o, book("PM_FULL_SEED", exchange=exchange))
    append(o, book("PM_BOOK_CHANGE", exchange=exchange))
    append(o, book("SIG_FULL_BOOK", exchange=exchange))


def assert_nulls(s: ob.Screen) -> None:
    assert s.native_fill_rate is None
    assert s.authentic_fees is None
    assert s.after_cost_pnl is None
    assert s.source_admitted is False
    assert s.paper_admitted is False
    assert s.certifies_250ms_survival is False


def test_default_disabled_and_release_must_be_verified() -> None:
    assert not ob.OfflinePairObserver().enabled
    with pytest.raises(ob.OfflineDenied, match="DEFAULT_DISABLED"):
        append(ob.OfflinePairObserver(), book("PM_FULL_SEED"))
    x = observer(approved=False)
    assert not x.enabled


def test_valid_offline_sequence_frozen_four_contracts_are_only_descriptive() -> None:
    for exchange in ("912", "913", "959", "960"):
        o = observer()
        full_pair(o, exchange)
        s = o.screen(exchange, "2026-10-18T00:00:04Z", 5)
        assert s.verdict == "REFERENCE_ONLY_2S_5S_NOT_RESTING_PROOF"
        assert s.yes_reference_gap == "0.05"
        assert s.no_reference_gap == "-0.27"
        assert_nulls(s)
        assert len(o.journal_bytes().splitlines()) == 3


def test_tiny_and_large_whole_lot_ladder_and_insufficient_depth() -> None:
    o = observer()
    full_pair(o)
    for n in (1, 5, 10, 20):
        assert_nulls(o.screen("912", "2026-10-18T00:00:04Z", n))
    assert o.screen("912", "2026-10-18T00:00:04Z", 20).yes_reference_gap == "0.05"
    with pytest.raises(ob.OfflineDenied, match="WHOLE_LOT"):
        o.screen("912", "2026-10-18T00:00:04Z", 2)
    z = observer()
    append(z, book("PM_FULL_SEED"))
    append(z, book("PM_BOOK_CHANGE"))
    a = book("SIG_FULL_BOOK")
    a["asks"] = [["0.60", "3"]]
    append(z, a)
    assert z.screen("912", "2026-10-18T00:00:04Z", 5).verdict == "WHOLE_DEPTH_INSUFFICIENT"


def test_no_unseeded_pm_delta() -> None:
    o = observer()
    with pytest.raises(ob.OfflineDenied, match="PM_DELTA_BEFORE_SEED"):
        append(o, book("PM_BOOK_CHANGE"))
    assert o.aborted


def test_reconnect_and_local_loss_do_not_imply_global_exchange_sequence() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    changed = book("PM_BOOK_CHANGE")
    changed["connection_epoch"] = 2
    with pytest.raises(ob.OfflineDenied, match="PM_WS_RECONNECT_DROP"):
        append(o, changed)
    assert o.aborted

    z = observer()
    append(z, book("PM_FULL_SEED"))
    dropped = book("PM_BOOK_CHANGE")
    dropped["local_drop_total"] = 1
    with pytest.raises(ob.OfflineDenied, match="PM_DROPPED_WS_MESSAGES"):
        append(z, dropped)


def test_pm_duplicate_ordinal_and_unchanged_rebuild_fail() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    append(o, book("PM_BOOK_CHANGE"))
    duplicate = book("PM_BOOK_CHANGE", received="2026-10-18T00:00:02Z")
    with pytest.raises(ob.OfflineDenied, match="PM_WS_RECONNECT_DROP"):
        append(o, duplicate)

    z = observer()
    append(z, book("PM_FULL_SEED"))
    same = book("PM_BOOK_CHANGE")
    same["bids"] = [["0.42", "25"]]
    same["asks"] = [["0.66", "25"]]
    with pytest.raises(ob.OfflineDenied, match="PM_NOT_A_PRICE"):
        append(z, same)


def test_sig_bookdirty_rest_mirror_or_resync_rejected() -> None:
    for change in (
        {"source": "SIG_REST_AUTHORITATIVE"},
        {"has_full_book": False},
        {"resync_required": True},
    ):
        o = observer()
        row = book("SIG_FULL_BOOK")
        row.update(change)
        with pytest.raises(ob.OfflineDenied, match="(SOURCE_IS_NOT|BOOK_DIRTY)"):
            append(o, row)


def test_sig_native_asof_revision_gap_duplicate_and_reconnect_rejected() -> None:
    for change, marker in (
        ({"native_asof_sequence": 4, "delivery_revision": 8, "previous_revision": 7},
         "SIG_ASOF_NONMONOTONIC"),
        ({"native_asof_sequence": 5, "delivery_revision": 9, "previous_revision": 8},
         "SIG_REVISION_GAP"),
        ({"connection_epoch": 2, "native_asof_sequence": 5,
          "delivery_revision": 8, "previous_revision": 7},
         "SIG_RECONNECT"),
    ):
        o = observer()
        append(o, book("SIG_FULL_BOOK"))
        next_row = book("SIG_FULL_BOOK", received="2026-10-18T00:00:05Z")
        next_row["native_asof_at"] = "2026-10-18T00:00:04Z"
        next_row["publisher_at"] = "2026-10-18T00:00:04Z"
        next_row.update(change)
        with pytest.raises(ob.OfflineDenied, match=marker):
            append(o, next_row)


def test_wrong_market_outcome_and_party_yes_substitution_denied() -> None:
    for change in ({"market_id": "224"}, {"outcome": "NO"},
                   {"condition_id": _MAP["913"][1]},
                   {"token_id": _MAP["913"][2]}):
        o = observer()
        row = book("PM_FULL_SEED")
        row.update(change)
        with pytest.raises(ob.OfflineDenied, match="FROZEN_EXACT_SAME"):
            append(o, row)


def test_missing_clock_uncertainty_and_future_source_denied() -> None:
    o = observer()
    row = book("PM_FULL_SEED")
    row["clock_uncertainty_ms"] = None
    with pytest.raises(ob.OfflineDenied, match="CLOCK_UNCERTAINTY_UNKNOWN"):
        append(o, row)
    z = observer()
    future = book("PM_FULL_SEED")
    future["publisher_at"] = "2026-10-18T00:00:09Z"
    with pytest.raises(ob.OfflineDenied, match="SOURCE_FUTURE"):
        append(z, future)


def test_stale_or_after_decision_data_not_used_to_make_a_screen() -> None:
    o = observer()
    full_pair(o)
    assert o.screen("912", "2026-10-18T00:00:01Z", 1).verdict == "NONCAUSAL_OR_STALE"
    assert o.screen("912", "2026-10-18T00:00:12Z", 1).verdict == "NONCAUSAL_OR_STALE"


def test_hash_of_actual_original_supplied_bytes_required() -> None:
    o = observer()
    row = book("PM_FULL_SEED")
    row["raw_source_sha256"] = "f" * 64
    with pytest.raises(ob.OfflineDenied, match="ORIGINAL_BYTES_HASH_MISMATCH"):
        o.ingest(row, b"DIFFERENT")
    assert o.aborted


def test_bounded_output_and_abort_are_irreversible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ob, "_MAX_OUTPUT", 80)
    o = observer()
    with pytest.raises(ob.OfflineDenied, match="BOUNDED_OUTPUT_EXCEEDED"):
        append(o, book("PM_FULL_SEED"))
    assert o.aborted
    with pytest.raises(ob.OfflineDenied, match="DEFAULT_DISABLED"):
        append(o, book("PM_FULL_SEED"))


def test_24_hour_limit_enforced_even_with_valid_row_shape() -> None:
    o = observer()
    append(o, book("SIG_FULL_BOOK"))
    too_late = book("SIG_FULL_BOOK", received="2026-10-19T00:00:04Z")
    with pytest.raises(ob.OfflineDenied, match="24H_WINDOW"):
        append(o, too_late)


def test_duplicate_claimed_original_bytes_abort_instead_of_double_spend() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"), b"UNIQUE-BUT-REUSED")
    with pytest.raises(ob.OfflineDenied, match="DUPLICATE_ORIGINAL_PAYLOAD"):
        append(o, book("SIG_FULL_BOOK"), b"UNIQUE-BUT-REUSED")
    assert o.aborted


def test_deterministic_chain_does_not_emit_original_raw_payload_or_secrets() -> None:
    a, b = observer(), observer()
    sample = b"SECRET-SHOULD-NOT-BE-WRITTEN"
    aa = append(a, book("PM_FULL_SEED"), sample)
    bb = append(b, book("PM_FULL_SEED"), sample)
    assert aa == bb
    assert a.journal_bytes() == b.journal_bytes()
    assert sample not in a.journal_bytes()
    assert b"raw_source_sha256" in a.journal_bytes()


def test_unexpected_credentials_or_pii_field_fails_closed() -> None:
    o = observer()
    row = book("PM_FULL_SEED")
    row["access_token"] = "NOT_REAL"
    with pytest.raises(ob.OfflineDenied, match="UNEXPECTED_OR_MISSING_FIELDS"):
        append(o, row)


def test_ladders_sorted_and_uncrossed() -> None:
    for change in (
        {"bids": [["0.40", "1"], ["0.41", "1"]]},
        {"asks": [["0.60", "1"], ["0.60", "1"]]},
        {"bids": [["0.80", "1"]]},
    ):
        o = observer()
        row = book("SIG_FULL_BOOK")
        row.update(change)
        with pytest.raises(ob.OfflineDenied, match="(UNSORTED|CROSSED)"):
            append(o, row)


def test_synthetic_raw_book_cannot_be_promoted_to_real_pnl() -> None:
    o = observer()
    full_pair(o)
    s = o.screen("912", "2026-10-18T00:00:04Z", 1)
    assert s.yes_reference_gap == "0.05"
    assert_nulls(s)
    assert s.verdict != "EXECUTABLE" and not s.certifies_250ms_survival


@pytest.mark.parametrize(
    ("whole", "available"),
    [
        (1, (("0.50", "0.5"), ("0.90", "0.5"))),
        (5, (("0.50", "2.5"), ("0.90", "2.5"))),
        (10, (("0.50", "5.5"), ("0.90", "4.5"))),
        (20, (("0.50", "10.5"), ("0.90", "9.5"))),
    ],
)
def test_fractional_levels_must_not_pool_to_whole_ioc(
    whole: int, available: tuple[tuple[str, str], ...]
) -> None:
    # Old code incorrectly combined separate fractions to make entire shares.
    levels = tuple((Decimal(p), Decimal(q)) for p, q in available)
    assert ob._vwap(levels, whole) is None


def test_integer_ladders_vwap_and_actual_sig_screening() -> None:
    levels = ((Decimal("0.50"), Decimal("2")),
              (Decimal("0.90"), Decimal("3")))
    assert ob._vwap(levels, 5) == Decimal("0.74")
    o = observer()
    append(o, book("PM_FULL_SEED"))
    append(o, book("PM_BOOK_CHANGE"))
    sig = book("SIG_FULL_BOOK")
    sig["asks"] = [["0.50", "0.5"], ["0.90", "0.5"]]
    append(o, sig)
    assert o.screen("912", "2026-10-18T00:00:04Z", 1).verdict == "WHOLE_DEPTH_INSUFFICIENT"


def test_pm_unsized_high_best_bid_cannot_create_yes_edge() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    pm = book("PM_BOOK_CHANGE")
    pm["bids"] = [["0.95", "0.1"], ["0.20", "10"]]
    pm["asks"] = [["0.99", "25"]]
    append(o, pm)
    sig = book("SIG_FULL_BOOK")
    sig["asks"] = [["0.60", "5"]]
    append(o, sig)
    s = o.screen("912", "2026-10-18T00:00:04Z", 5)
    assert s.yes_reference_gap == "-0.40"
    assert s.yes_reference_gap != "0.35"
    assert_nulls(s)


def test_pm_unsized_cheap_best_ask_cannot_create_no_edge() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    pm = book("PM_BOOK_CHANGE")
    pm["bids"] = [["0.40", "25"]]
    pm["asks"] = [["0.45", "0.1"], ["0.80", "20"]]
    append(o, pm)
    sig = book("SIG_FULL_BOOK")
    sig["bids"] = [["0.70", "25"]]
    sig["asks"] = [["0.90", "25"]]
    append(o, sig)
    s = o.screen("912", "2026-10-18T00:00:04Z", 5)
    assert s.no_reference_gap == "-0.10"
    assert s.no_reference_gap != "0.25"
    assert_nulls(s)


def test_pm_insufficient_sized_ladder_nulls_both_reference_gaps() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    pm = book("PM_BOOK_CHANGE")
    pm["bids"] = [["0.65", "0.5"]]
    append(o, pm)
    append(o, book("SIG_FULL_BOOK"))
    s = o.screen("912", "2026-10-18T00:00:04Z", 1)
    assert s.verdict == "WHOLE_DEPTH_INSUFFICIENT"
    assert s.yes_reference_gap is None and s.no_reference_gap is None
    assert_nulls(s)


def test_post_ingest_nested_caller_mutation_cannot_change_journal_or_gap() -> None:
    o = observer()
    seed, change, sig = book("PM_FULL_SEED"), book("PM_BOOK_CHANGE"), book("SIG_FULL_BOOK")
    append(o, seed)
    append(o, change)
    append(o, sig)
    original_journal = o.journal_bytes()
    original_screen = o.screen("912", "2026-10-18T00:00:04Z", 5)
    for row in (seed, change, sig):
        row["bids"][0][0] = "0.01"
        row["bids"][0][1] = "9000"
        row["asks"][0][0] = "0.99"
        row["asks"][0][1] = "0.01"
        row["received_at"] = "2026-12-30T00:00:00Z"
    assert o.journal_bytes() == original_journal
    assert o.screen("912", "2026-10-18T00:00:04Z", 5) == original_screen
    assert_nulls(original_screen)


def test_uncertainty_overlap_source_and_receive_returns_null() -> None:
    for venue in ("PM", "SIG"):
        o = observer()
        append(o, book("PM_FULL_SEED"))
        pm = book("PM_BOOK_CHANGE")
        sig = book("SIG_FULL_BOOK")
        if venue == "PM":
            pm["clock_uncertainty_ms"] = 1000
        else:
            sig["clock_uncertainty_ms"] = 1000
        append(o, pm)
        append(o, sig)
        s = o.screen("912", "2026-10-18T00:00:04Z", 5)
        assert s.verdict == "NONCAUSAL_OR_STALE"
        assert s.yes_reference_gap is None and s.no_reference_gap is None
        assert_nulls(s)


def test_clock_uncertain_sig_receive_after_decision_abstains() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    append(o, book("PM_BOOK_CHANGE"))
    sig = book("SIG_FULL_BOOK")
    sig["clock_uncertainty_ms"] = 250
    append(o, sig)
    s = o.screen("912", "2026-10-18T00:00:03Z", 1)
    assert s.verdict == "NONCAUSAL_OR_STALE"
    assert_nulls(s)


def test_sig_original_source_sequence_gap_rejected_even_when_revision_is_contiguous() -> None:
    o = observer()
    append(o, book("SIG_FULL_BOOK"))
    sig = book("SIG_FULL_BOOK", received="2026-10-18T00:00:05Z")
    sig.update({
        "publisher_at": "2026-10-18T00:00:04Z",
        "native_asof_at": "2026-10-18T00:00:04Z",
        "native_asof_sequence": 5,
        "delivery_revision": 8, "previous_revision": 7,
        "source_sequence_from": 14, "source_sequence_through": 15,
    })
    with pytest.raises(ob.OfflineDenied, match="SIG_SOURCE_SEQUENCE_GAP"):
        append(o, sig)
    assert o.aborted


def test_sig_contiguous_original_source_ranges_remain_schema_only() -> None:
    o = observer()
    full_pair(o)
    sig = book("SIG_FULL_BOOK", received="2026-10-18T00:00:05Z")
    sig.update({
        "publisher_at": "2026-10-18T00:00:04Z",
        "native_asof_at": "2026-10-18T00:00:04Z",
        "native_asof_sequence": 5,
        "delivery_revision": 8, "previous_revision": 7,
        "source_sequence_from": 12, "source_sequence_through": 13,
    })
    append(o, sig)
    s = o.screen("912", "2026-10-18T00:00:06Z", 5)
    assert s.verdict == "REFERENCE_ONLY_2S_5S_NOT_RESTING_PROOF"
    assert_nulls(s)


def test_release_expiry_rechecked_on_each_ingest_and_screen() -> None:
    o = observer()
    o._release_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ob.OfflineDenied, match="RELEASE_EXPIRED"):
        append(o, book("PM_FULL_SEED"))
    assert o.aborted
    z = observer()
    full_pair(z)
    z._release_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ob.OfflineDenied, match="RELEASE_EXPIRED"):
        z.screen("912", "2026-10-18T00:00:04Z", 1)
    assert z.aborted


def test_monotonic_24_hour_offline_runtime_guard_is_not_collector_release() -> None:
    o = observer()
    o._runtime_started_at = time.monotonic() - 86_401
    with pytest.raises(ob.OfflineDenied, match="OFFLINE_RUNTIME_EXPIRED"):
        append(o, book("PM_FULL_SEED"))
    assert o.aborted


@pytest.mark.parametrize(
    "run_id",
    [
        "me@example.com", "secret-token", "api_key_credentials",
        "a" * 33, "CLIENT_TOKEN_123", "my password",
        "../../private", "owner/desk",
    ],
)
def test_untrusted_producer_run_metadata_injection_denied(run_id: str) -> None:
    o = observer()
    row = book("PM_FULL_SEED")
    row["producer_run_id"] = run_id
    with pytest.raises(ob.OfflineDenied, match="(PRODUCER_RUN_INVALID|SENSITIVE_METADATA_DENIED)"):
        append(o, row)
    assert o.aborted

# Focused 18-Oct publisher source-clock replay regressions. Do not weaken
# the 19 original or the earlier independently accepted adversarial tests.


def test_pm_one_hour_backdated_publisher_change_denied_on_ingest() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    replay = book("PM_BOOK_CHANGE")
    # Exact Red Team #207 synthetic proof: fresh receipt and ordinal but
    # publisher from ONE HOUR before the complete PM seed.
    replay["publisher_at"] = "2026-10-17T23:00:00Z"
    with pytest.raises(
        ob.OfflineDenied, match="SOURCE_PUBLISHER_RECEIPT_STALE_OR_UNATTESTED"
    ):
        append(o, replay)
    assert o.aborted
    assert len(o.journal_bytes().splitlines()) == 1


def test_pm_publisher_regression_with_recent_receipts_and_valid_epoch_denied() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    append(o, book("PM_BOOK_CHANGE"))
    replay = book("PM_BOOK_CHANGE", received="2026-10-18T00:00:02Z")
    replay.update({
        "local_receive_ordinal": 2,
        "publisher_at": "2026-10-18T00:00:00.500Z",
        "bids": [["0.63", "25"]],
        "asks": [["0.68", "25"]],
    })
    with pytest.raises(ob.OfflineDenied, match="PM_SOURCE_CLOCK_NONMONOTONIC"):
        append(o, replay)
    assert o.aborted


@pytest.mark.parametrize(
    ("previous_uncertainty", "next_uncertainty", "second_publisher"),
    [
        (0, 0, "2026-10-18T00:00:00Z"),  # equal source timestamps
        (600, 600, "2026-10-18T00:00:01Z"),  # overlapping ±UTC intervals
    ],
)
def test_pm_seed_source_equal_or_uncertain_overlap_denied(
    previous_uncertainty: int,
    next_uncertainty: int,
    second_publisher: str,
) -> None:
    o = observer()
    seed = book("PM_FULL_SEED")
    seed["clock_uncertainty_ms"] = previous_uncertainty
    append(o, seed)
    change = book("PM_BOOK_CHANGE")
    change["clock_uncertainty_ms"] = next_uncertainty
    change["publisher_at"] = second_publisher
    with pytest.raises(ob.OfflineDenied, match="PM_SOURCE_CLOCK_NONMONOTONIC"):
        append(o, change)
    assert o.aborted


def test_sig_increasing_native_revision_with_older_native_asof_denied() -> None:
    o = observer()
    append(o, book("SIG_FULL_BOOK"))
    prior = book("SIG_FULL_BOOK", received="2026-10-18T00:00:04Z")
    prior.update({
        "native_asof_sequence": 5,
        "native_asof_at": "2026-10-18T00:00:01Z",
        "publisher_at": "2026-10-18T00:00:01Z",
        "delivery_revision": 8,
        "previous_revision": 7,
        "source_sequence_from": 12,
        "source_sequence_through": 13,
    })
    with pytest.raises(ob.OfflineDenied, match="SIG_SOURCE_CLOCK_NONMONOTONIC"):
        append(o, prior)
    assert o.aborted


def test_sig_native_source_uncertain_overlap_rejected_despite_adjacency() -> None:
    o = observer()
    first = book("SIG_FULL_BOOK")
    first["clock_uncertainty_ms"] = 750
    append(o, first)
    following = book("SIG_FULL_BOOK", received="2026-10-18T00:00:04Z")
    following.update({
        "clock_uncertainty_ms": 750,
        "publisher_at": "2026-10-18T00:00:03Z",
        "native_asof_at": "2026-10-18T00:00:03Z",
        "native_asof_sequence": 5,
        "delivery_revision": 8,
        "previous_revision": 7,
        "source_sequence_from": 12,
        "source_sequence_through": 13,
    })
    with pytest.raises(ob.OfflineDenied, match="SIG_SOURCE_CLOCK_NONMONOTONIC"):
        append(o, following)
    assert o.aborted


@pytest.mark.parametrize("venue", ["PM", "SIG"])
def test_source_old_despite_local_receipt_fresh_denied_at_ingest(
    venue: str,
) -> None:
    o = observer()
    if venue == "PM":
        append(o, book("PM_FULL_SEED"))
        delayed = book("PM_BOOK_CHANGE", received="2026-10-18T00:00:08Z")
        delayed["publisher_at"] = "2026-10-18T00:00:01Z"
    else:
        delayed = book("SIG_FULL_BOOK", received="2026-10-18T00:00:10Z")
        delayed["publisher_at"] = "2026-10-18T00:00:02Z"
    with pytest.raises(
        ob.OfflineDenied, match="SOURCE_PUBLISHER_RECEIPT_STALE_OR_UNATTESTED"
    ):
        append(o, delayed)
    assert o.aborted


def test_source_publisher_age_at_decision_nulls_newly_received_book() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    delayed_pm = book("PM_BOOK_CHANGE", received="2026-10-18T00:00:05Z")
    delayed_pm["publisher_at"] = "2026-10-18T00:00:01Z"
    append(o, delayed_pm)  # source→receipt exactly four seconds, permitted
    sig = book("SIG_FULL_BOOK", received="2026-10-18T00:00:06Z")
    sig["publisher_at"] = "2026-10-18T00:00:05.500Z"
    sig["native_asof_at"] = sig["publisher_at"]
    append(o, sig)
    result = o.screen("912", "2026-10-18T00:00:07Z", 5)
    assert result.verdict == "NONCAUSAL_OR_STALE"
    assert result.yes_reference_gap is None and result.no_reference_gap is None
    assert_nulls(result)


def test_ordered_two_pm_changes_and_native_sig_remain_reference_only() -> None:
    o = observer()
    append(o, book("PM_FULL_SEED"))
    append(o, book("PM_BOOK_CHANGE"))
    new_pm = book("PM_BOOK_CHANGE", received="2026-10-18T00:00:02Z")
    new_pm.update({
        "local_receive_ordinal": 2,
        "publisher_at": "2026-10-18T00:00:02Z",
        "bids": [["0.64", "25"]],
        "asks": [["0.68", "25"]],
    })
    append(o, new_pm)
    sig = book("SIG_FULL_BOOK", received="2026-10-18T00:00:04Z")
    sig["publisher_at"] = "2026-10-18T00:00:03Z"
    sig["native_asof_at"] = sig["publisher_at"]
    append(o, sig)
    result = o.screen("912", "2026-10-18T00:00:05Z", 5)
    assert result.verdict == "REFERENCE_ONLY_2S_5S_NOT_RESTING_PROOF"
    assert result.yes_reference_gap == "0.04"
    assert result.no_reference_gap == "-0.28"
    assert_nulls(result)


def test_unknown_or_ambiguous_source_clock_cannot_make_reference_gap() -> None:
    o = observer()
    seed = book("PM_FULL_SEED")
    # Keep the seed→change clock intervals strictly ordered, then separately
    # make the cross-venue PM-vs-SIG source clock intervals ambiguous.
    seed["publisher_at"] = "2026-10-17T23:59:59Z"
    append(o, seed)
    pm = book("PM_BOOK_CHANGE")
    pm["clock_uncertainty_ms"] = 1000
    append(o, pm)
    sig = book("SIG_FULL_BOOK")
    sig["clock_uncertainty_ms"] = 1000
    append(o, sig)
    result = o.screen("912", "2026-10-18T00:00:04Z", 1)
    assert result.verdict == "NONCAUSAL_OR_STALE"
    assert result.yes_reference_gap is None and result.no_reference_gap is None
    assert_nulls(result)
