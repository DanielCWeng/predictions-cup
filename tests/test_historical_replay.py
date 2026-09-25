import datetime
import decimal
import pathlib

import pytest

import predictions_cup.replay as replay

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "historical_snapshots.jsonl"


def test_historical_snapshot_fixture_is_build_005_compatible() -> None:
    batch = replay.load_historical_snapshot_jsonl(FIXTURE)

    assert batch.timestamp_semantics == "SOURCE_TIME_PROXY"
    assert batch.latency_model == "NONE_INVENTED"
    assert len(batch.events) == 3
    assert all(event.source is replay.ReplaySource.POLYMARKET for event in batch.events)
    assert all(event.event_type is replay.ReplayEventType.DEPTH_SNAPSHOT for event in batch.events)
    assert all(event.observed_at == event.source_at for event in batch.events)

    first = batch.events[0]
    assert isinstance(first.payload, replay.QuotePayload)
    assert first.payload.best_bid == decimal.Decimal("0.41")
    assert first.payload.best_ask == decimal.Decimal("0.43")
    assert first.payload.bids[0].quantity == decimal.Decimal("120")
    assert first.observed_at == datetime.datetime.fromtimestamp(1772308800, tz=datetime.UTC)


def test_historical_snapshot_rejects_crossed_books(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "crossed.jsonl"
    path.write_text(
        '{"minute_ts":1,"asset_id":"a","condition_id":"c",'
        '"bids":[["0.6","1"]],"asks":[["0.5","1"]]}\n',
        encoding="utf-8",
    )
    with pytest.raises(replay.HistoricalSnapshotError, match="crossed snapshot"):
        replay.load_historical_snapshot_jsonl(path)


def test_historical_snapshot_rejects_duplicate_asset_minute(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "dup.jsonl"
    row = (
        '{"minute_ts":1,"asset_id":"a","condition_id":"c",'
        '"bids":[["0.4","1"]],"asks":[["0.5","1"]]}\n'
    )
    path.write_text(row + row, encoding="utf-8")
    with pytest.raises(replay.HistoricalSnapshotError, match="duplicate asset/minute"):
        replay.load_historical_snapshot_jsonl(path)


def test_historical_snapshot_rejects_binary_float_prices(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "float.jsonl"
    path.write_text(
        '{"minute_ts":1,"asset_id":"a","condition_id":"c",'
        '"bids":[[0.4,"1"]],"asks":[["0.5","1"]]}\n',
        encoding="utf-8",
    )
    with pytest.raises(replay.HistoricalSnapshotError, match="binary float"):
        replay.load_historical_snapshot_jsonl(path)
