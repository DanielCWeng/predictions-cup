from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq

from predictions_cup.external.polymarket.models import (
    BookChangeEvent,
    BookLevel,
    BookSnapshot,
    TradeEvent,
    hashed_trade_event_id,
)
from predictions_cup.external.polymarket.parquet_storage import PolymarketResearchStorage


def snapshot(observed_at: datetime, *, token_id: str = "token-1") -> BookSnapshot:
    return BookSnapshot(
        market_id="0xmarket",
        token_id=token_id,
        source_timestamp=observed_at - timedelta(milliseconds=10),
        observed_at=observed_at,
        bids=(BookLevel(Decimal("0.45"), Decimal("10")),),
        asks=(BookLevel(Decimal("0.46"), Decimal("12")),),
        last_trade_price=Decimal("0.455"),
    )


def _files(root: Path, stream: str) -> list[Path]:
    return sorted((root / stream).rglob("*.parquet"))


def test_parquet_storage_writes_zstd_atomic_shards_with_distinct_timestamps(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research"
    storage = PolymarketResearchStorage(root, shard_seconds=60)
    storage.initialize()

    observed = datetime(2026, 9, 25, 12, 0, 0, 250000, tzinfo=UTC)
    sampled = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)
    assert storage.append_observations((snapshot(observed),), sampled.isoformat()) == 1
    assert storage.append_snapshots((snapshot(observed),), sampled.isoformat()) == 1
    assert storage.flush_all() == 2

    panel_file = _files(root, "observations")[0]
    depth_file = _files(root, "depth_snapshots")[0]
    panel = pq.read_table(panel_file).to_pylist()[0]
    depth = pq.read_table(depth_file).to_pylist()[0]

    assert panel["source_timestamp"] == observed - timedelta(milliseconds=10)
    assert panel["state_observed_at"] == observed
    assert panel["observed_at"] == sampled
    assert depth["state_observed_at"] == observed
    assert depth["recorded_at"] == sampled
    assert depth["bids"] == [{"price": "0.45", "size": "10"}]
    assert depth["asks"] == [{"price": "0.46", "size": "12"}]

    metadata = pq.ParquetFile(panel_file).metadata
    assert metadata.row_group(0).column(0).compression == "ZSTD"
    assert not list(root.rglob("*.tmp"))


def test_parquet_storage_rotates_short_immutable_shards(tmp_path: Path) -> None:
    root = tmp_path / "research"
    storage = PolymarketResearchStorage(root, shard_seconds=60)
    storage.initialize()
    first = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)
    second = first + timedelta(minutes=1)

    storage.append_observations((snapshot(first),), first.isoformat())
    storage.append_observations((snapshot(second),), second.isoformat())
    storage.flush_all()

    files = _files(root, "observations")
    assert len(files) == 2
    assert sum(pq.ParquetFile(path).metadata.num_rows for path in files) == 2


def test_sparse_stream_bucket_is_published_when_clock_advances(tmp_path: Path) -> None:
    root = tmp_path / "research"
    storage = PolymarketResearchStorage(root, shard_seconds=60)
    storage.initialize()
    observed = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)

    storage.append_trade(
        TradeEvent(
            market_id="0xmarket",
            token_id="token-1",
            price=Decimal("0.455"),
            size=Decimal("1"),
            side="BUY",
            source_timestamp=observed,
            observed_at=observed,
            transaction_hash="tx-sparse",
            fee_rate_bps=None,
        )
    )
    assert _files(root, "trades") == []

    assert storage.flush_due(observed + timedelta(seconds=60)) == 1
    files = _files(root, "trades")
    assert len(files) == 1
    assert pq.ParquetFile(files[0]).metadata.num_rows == 1


def test_parquet_event_streams_preserve_source_and_observed_time(tmp_path: Path) -> None:
    root = tmp_path / "research"
    storage = PolymarketResearchStorage(root)
    storage.initialize()
    observed = datetime(2026, 9, 25, 12, 0, 1, tzinfo=UTC)
    source = observed - timedelta(milliseconds=50)

    storage.append_book_changes(
        (
            BookChangeEvent(
                market_id="0xmarket",
                token_id="token-1",
                side="BUY",
                price=Decimal("0.45"),
                size=Decimal("10"),
                source_timestamp=source,
                observed_at=observed,
                best_bid=Decimal("0.45"),
                best_ask=Decimal("0.46"),
                book_hash="hash-1",
            ),
        )
    )
    storage.append_trade(
        TradeEvent(
            market_id="0xmarket",
            token_id="token-1",
            price=Decimal("0.455"),
            size=Decimal("2"),
            side="BUY",
            source_timestamp=source,
            observed_at=observed,
            transaction_hash="tx-1",
            fee_rate_bps=None,
        )
    )
    storage.flush_all()

    change = pq.read_table(_files(root, "book_changes")[0]).to_pylist()[0]
    trade = pq.read_table(_files(root, "trades")[0]).to_pylist()[0]
    assert change["source_timestamp"] == source
    assert change["observed_at"] == observed
    assert trade["source_timestamp"] == source
    assert trade["observed_at"] == observed
    assert trade["event_id"] == hashed_trade_event_id("token-1", "tx-1")
