"""Invented synthetic source events only: no venue call, disk or real fill."""

from __future__ import annotations

import json
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from predictions_cup.research.native_paired_source_bridge import (
    SIG_MARKETS,
    SourceBridge,
    SourceRejected,
    reference_only_gaps,
    whole_lot_vwap,
)

PRODUCER = "a" * 40
PM = {"223": "fake-mt-dem", "224": "fake-mt-rep",
      "270": "fake-me-dem", "271": "fake-me-rep"}
BIDS = [{"price": "0.40", "quantity": "11"}, {"price": "0.39", "quantity": "12"}]
ASKS = [{"price": "0.60", "quantity": "11"}, {"price": "0.61", "quantity": "12"}]


def bridge(
    emit: Callable[[dict[str, object]], None] | None = None,
) -> tuple[SourceBridge, list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    sender = records.append if emit is None else emit
    return SourceBridge(emit=sender, producer_sha=PRODUCER,
                        allowed_pm_tokens=PM, max_clock_uncertainty_ms=100), records


def sig(seq: int = 1, *, previous: int = 0,
        books: list[dict[str, object]] | None = None,
        resync: bool = False) -> dict[str, object]:
    return {
        "delivery": {
            "revision": seq, "previousRevision": previous,
            "sourceSequenceFrom": seq, "sourceSequenceThrough": seq,
        },
        "books": [] if books is None else books, "resyncRequired": resync,
    }


def book(*, exchange: str = "912", native_at: str = "2026-10-18T10:00:00Z",
         bids: object = BIDS, asks: object = ASKS) -> dict[str, object]:
    return {"exchangeId": exchange, "asOf": {"sequence": 91, "at": native_at},
            "bids": bids, "asks": asks}


def test_zero_network_default_off_and_four_allowlist() -> None:
    b, records = bridge()
    b.sig_batch(topic_market_id="999", batch={}, received_at="2026-10-18T10:00:01Z",
                connection_epoch=1)
    b.pm_rest_seed(token_id="unlisted", epoch=1, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:01Z")
    assert records == []
    assert len(SIG_MARKETS) == 4
    assert b.records_emitted == 0


def test_sig_every_relevant_batch_including_empty_continuity_and_book() -> None:
    b, records = bridge()
    b.sig_batch(topic_market_id="223", batch=sig(), received_at="2026-10-18T10:00:01Z",
                connection_epoch=1)
    b.sig_batch(topic_market_id="223", batch=sig(2, previous=1, books=[book()]),
                received_at="2026-10-18T10:00:02Z", connection_epoch=1,
                raw_frame=b'{"invented":"bytes"}')
    assert len(records) == 2
    assert records[0]["gap_or_unanchored"] is True
    assert records[0]["full_books"] == []
    assert records[1]["gap_or_unanchored"] is False
    assert cast(list[dict[str, object]], records[1]["full_books"])[0]["asof_sequence"] == 91
    assert records[1]["raw_frame_sha256"] is not None
    assert records[1]["wire_bytes_verified"] is False
    assert all(r["paper_admitted"] is False for r in records)


def test_sig_detects_revision_gap_and_resync_required_without_fake_trust() -> None:
    b, records = bridge()
    b.sig_batch(topic_market_id="223", batch=sig(), received_at="2026-10-18T10:00:01Z",
                connection_epoch=1)
    b.sig_batch(topic_market_id="223", batch=sig(3, previous=2, books=[book()]),
                received_at="2026-10-18T10:00:02Z", connection_epoch=1)
    assert records[-1]["gap_or_unanchored"] is True
    b.sig_batch(topic_market_id="223", batch=sig(4, previous=3, resync=True),
                received_at="2026-10-18T10:00:03Z", connection_epoch=1)
    assert records[-1]["gap_or_unanchored"] is True


def test_sig_epoch_reconnect_is_gap_and_clock_regression_denied() -> None:
    b, records = bridge()
    b.sig_batch(topic_market_id="223", batch=sig(), received_at="2026-10-18T10:00:01Z",
                connection_epoch=1)
    b.sig_batch(topic_market_id="223", batch=sig(2, previous=1),
                received_at="2026-10-18T10:00:02Z", connection_epoch=2)
    assert records[-1]["gap_or_unanchored"] is True
    with pytest.raises(SourceRejected, match="SIG_EPOCH_REGRESSION"):
        b.sig_batch(topic_market_id="223", batch=sig(3, previous=2),
                    received_at="2026-10-18T10:00:03Z", connection_epoch=1)
    with pytest.raises(SourceRejected, match="SIG_RECEIVE_REGRESSION"):
        b.sig_batch(topic_market_id="223", batch=sig(3, previous=2),
                    received_at="2026-10-18T10:00:01Z", connection_epoch=2)


def test_sig_wrong_exchange_no_book_revision_or_future_publisher_rejected() -> None:
    for bad_book, reason in [
        (book(exchange="913"), "SIG_EXCHANGE_MISMATCH"),
        ({"exchangeId": "912", "bids": BIDS, "asks": ASKS}, "SIG_NATIVE_ASOF_MISSING"),
        (book(native_at="2026-10-18T10:02:00Z"), "SOURCE_CLOCK_AFTER_RECEIVE"),
        (book(bids=[]), "FULL_LADDER_UNAVAILABLE"),
        (book(asks=[{"price": "0.30", "quantity": "5"}]), "CROSSED_BOOK"),
    ]:
        b, _ = bridge()
        with pytest.raises(SourceRejected, match=reason):
            b.sig_batch(topic_market_id="223",
                        batch=sig(books=[cast(dict[str, object], bad_book)]),
                        received_at="2026-10-18T10:00:01Z", connection_epoch=1)


def test_pm_seed_epoch_and_local_ordinal_without_global_sequence_claim() -> None:
    b, records = bridge()
    with pytest.raises(SourceRejected, match="PM_UNSEEDED_EPOCH"):
        b.pm_ws_event(epoch=1, ordinal=1, received_at="2026-10-18T10:00:01Z",
                      affected_token_ids=[PM["223"]], reconstructed_books={})
    b.pm_rest_seed(token_id=PM["223"], epoch=1, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:01Z")
    b.pm_ws_event(epoch=1, ordinal=1, received_at="2026-10-18T10:00:02Z",
                  affected_token_ids=[PM["223"]],
                  reconstructed_books={PM["223"]: {"bids": BIDS, "asks": ASKS}})
    assert records[0]["kind"] == "REST_SEED_COMPLETE"
    assert records[-1]["ws_local_ordinal"] == 1
    full_books = cast(dict[str, dict[str, object]], records[-1]["full_books"])
    assert full_books[PM["223"]]["complete_source_claim"] is True
    assert records[-1]["issuer_authenticated"] is False


def test_pm_uninitialised_delta_gap_and_reconnect_are_not_silently_repaired() -> None:
    b, records = bridge()
    b.pm_rest_seed(token_id=PM["223"], epoch=1, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:01Z")
    b.pm_ws_event(epoch=1, ordinal=2, received_at="2026-10-18T10:00:02Z",
                  affected_token_ids=[PM["223"]],
                  reconstructed_books={PM["223"]: {"bids": BIDS, "asks": ASKS}})
    assert records[-1]["gap_or_unanchored"] is True
    assert records[-1]["full_books"] == {}
    assert records[-1]["abstain_tokens"] == [PM["223"]]
    b.pm_rest_seed(token_id=PM["223"], epoch=2, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:03Z")
    b.pm_ws_event(epoch=2, ordinal=1, received_at="2026-10-18T10:00:04Z",
                  affected_token_ids=[PM["223"]],
                  uninitialized_tokens=[PM["223"]],
                  reconstructed_books={})
    assert records[-1]["uninitialized_tokens"] == [PM["223"]]
    b.pm_disconnect(epoch=2, received_at="2026-10-18T10:00:05Z")
    assert records[-1]["kind"] == "DISCONNECT"
    assert not b.pm_seeded


def test_no_seed_inference_from_sparse_parquet_or_missing_reconstruction() -> None:
    b, records = bridge()
    b.pm_rest_seed(token_id=PM["223"], epoch=1, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:01Z")
    b.pm_ws_event(epoch=1, ordinal=1, received_at="2026-10-18T10:00:02Z",
                  affected_token_ids=[PM["223"]], reconstructed_books={})
    assert records[-1]["abstain_tokens"] == [PM["223"]]
    b.pm_ws_event(epoch=1, ordinal=2, received_at="2026-10-18T10:00:03Z",
                  affected_token_ids=[PM["223"]],
                  reconstructed_books={PM["223"]: {"bids": BIDS, "asks": ASKS}})
    assert records[-1]["abstain_tokens"] == [PM["223"]]


def test_sink_exhaustion_irreversibly_poisoned_never_silent_sampling() -> None:
    count = 0

    def bounded(record: dict[str, object]) -> None:
        nonlocal count
        count += len(json.dumps(record))
        if count > 600:
            raise OSError("DISK_OR_WRITER_CAP")

    b, _ = bridge(emit=bounded)
    with pytest.raises(OSError, match="DISK_OR_WRITER_CAP"):
        b.sig_batch(topic_market_id="223", batch=sig(books=[book()]),
                    received_at="2026-10-18T10:00:01Z", connection_epoch=1)
    assert b.poisoned
    with pytest.raises(SourceRejected, match="BRIDGE_POISONED"):
        b.sig_batch(topic_market_id="223", batch=sig(2, previous=1),
                    received_at="2026-10-18T10:00:02Z", connection_epoch=1)


def test_multiple_shares_floor_per_level_and_no_fee_imputation() -> None:
    levels = [["0.40", "1.9"], ["0.39", "4.9"], ["0.38", "15.9"]]
    assert whole_lot_vwap(levels, 1) == Decimal('0.40')
    assert whole_lot_vwap(levels, 5) == (Decimal('0.40') + 4 * Decimal('0.39')) / 5
    assert whole_lot_vwap(levels, 21) is None
    sig_book = {"bids": [["0.40", "10"]], "asks": [["0.60", "10"]]}
    pm_book = {"bids": [["0.62", "10"]], "asks": [["0.64", "10"]]}
    assert reference_only_gaps(sig=sig_book, pm=pm_book, size=5) == (None, None)
    assert reference_only_gaps(sig=sig_book, pm=pm_book, size=5,
                               after_shock_verified=True) == (Decimal('0.02'), Decimal('-0.24'))
    assert reference_only_gaps(sig=sig_book, pm=pm_book, size=20,
                               after_shock_verified=True) == (None, None)



def test_rejected_sig_book_does_not_advance_native_delivery_continuity() -> None:
    b, records = bridge()
    b.sig_batch(topic_market_id="223", batch=sig(1),
                received_at="2026-10-18T10:00:01Z", connection_epoch=1)
    with pytest.raises(SourceRejected, match="SIG_NATIVE_ASOF_MISSING"):
        b.sig_batch(
            topic_market_id="223",
            batch=sig(2, previous=1,
                      books=[{"exchangeId": "912", "bids": BIDS, "asks": ASKS}]),
            received_at="2026-10-18T10:00:02Z", connection_epoch=1,
        )
    assert b.sig_state["223"][1] == 1
    b.sig_batch(topic_market_id="223", batch=sig(3, previous=2),
                received_at="2026-10-18T10:00:03Z", connection_epoch=1)
    assert records[-1]["gap_or_unanchored"] is True


def test_rejected_pm_frame_does_not_advance_ws_ordinal() -> None:
    b, records = bridge()
    b.pm_rest_seed(token_id=PM["223"], epoch=1, bids=BIDS, asks=ASKS,
                   received_at="2026-10-18T10:00:01Z")
    with pytest.raises(SourceRejected, match="RAW_BYTES_CAP"):
        b.pm_ws_event(epoch=1, ordinal=1, received_at="2026-10-18T10:00:02Z",
                      affected_token_ids=[], reconstructed_books={},
                      raw_frame=cast(bytes, "not original bytes"))
    assert b.pm_ordinal == 0
    b.pm_ws_event(epoch=1, ordinal=2, received_at="2026-10-18T10:00:03Z",
                  affected_token_ids=[PM["223"]], reconstructed_books={})
    assert records[-1]["gap_or_unanchored"] is True
    assert records[-1]["abstain_tokens"] == [PM["223"]]


def test_bounded_24hour_equivalent_one_hz_no_buffer_and_measured_synthetic_rate() -> None:
    """86,400 invented 1-Hz deliveries; sinks do not store source book bytes."""
    count = 0
    bytes_total = 0

    def drop_after_accounting(record: dict[str, object]) -> None:
        nonlocal count, bytes_total
        count += 1
        bytes_total += len(json.dumps(record, separators=(",", ":")))

    b, _ = bridge(emit=drop_after_accounting)
    start = datetime(2026, 10, 18, tzinfo=UTC)
    import sys
    import time

    starting_rss_kib = None
    if sys.platform == "linux":
        import resource

        starting_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()

    tick = time.perf_counter()
    for i in range(86_400):
        receive = (start + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        b.sig_batch(topic_market_id="223", batch=sig(i + 1, previous=i),
                    received_at=receive, connection_epoch=1)
    seconds = time.perf_counter() - tick
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if starting_rss_kib is not None:
        import resource

        rss_delta_kib = (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - starting_rss_kib
        )
        assert rss_delta_kib < 32 * 1024
    print(f" SYNTHETIC_24H_1HZ records={count} bytes={bytes_total}"
          f" peak_tracemalloc={peak} elapsed_s={seconds:.3f}"
          f" throughput_records_s={count / seconds:.1f}")
    assert count / seconds > 1000
    assert count == 86_400
    assert b.records_emitted == count
    assert peak < 8_000_000  # bridge: O(4) state, not a storage-capacity claim
    assert bytes_total > 1_000_000  # 8MiB #318 would NOT cover arbitrary 24h
