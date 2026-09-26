"""Normalize PolyLeviathan on-chain fill exports into a separate DATA-001 fill stream.

Fills are ``TRADE_FILL`` evidence and never share a stream with book observations. They are
not written in the BUILD-007 ``trades`` schema: that contract de-duplicates on
``(token_id, transaction_hash)``, which would silently collapse distinct on-chain fills that
share a transaction. Fills keep the source's own reliable identity
``(transaction_hash, log_index, token_id)``.

Semantics carried through unchanged (never reinterpreted):

* ``source_side`` is the exporter's raw ``side`` text; it is *not* treated as aggressor
  direction.
* ``is_exchange_taker`` only records that the taker address is an exchange contract.
* The source supplies ``price`` / ``size_shares`` / ``value_usd`` as binary floats. They are
  rendered with Arrow's shortest round-trip decimal text; no further float arithmetic occurs.
* The only timestamp is block time at 1-second resolution (``source_timestamp``).
  ``observed_at`` repeats it as an explicit ``BLOCK_TIME_PROXY``: no historical receipt time
  exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from predictions_cup.historical.pmxt import GRADE_TRADE_FILL, UTC_US

FILL_SOURCE_VERSION = "POLYLEVIATHAN_FILLS"
FILL_SCHEMA = pa.schema(
    [
        ("fill_id", pa.string()),
        ("token_id", pa.string()),
        ("market_id", pa.string()),
        ("polymarket_market_id", pa.string()),
        ("event_id", pa.string()),
        ("outcome", pa.string()),
        ("source_timestamp", UTC_US),
        ("observed_at", UTC_US),
        ("price", pa.string()),
        ("size", pa.string()),
        ("value_usd", pa.string()),
        ("source_side", pa.string()),
        ("maker_address", pa.string()),
        ("taker_address", pa.string()),
        ("is_exchange_taker", pa.bool_()),
        ("transaction_hash", pa.string()),
        ("log_index", pa.int64()),
        ("source_version", pa.string()),
        ("evidence_grade", pa.string()),
    ]
)
FILL_COLUMNS = [
    "timestamp",
    "side",
    "price",
    "size_shares",
    "value_usd",
    "token_id",
    "condition_id",
    "maker_address",
    "taker_address",
    "tx_hash",
    "log_index",
    "is_exchange_taker",
    "event_id",
    "market_id",
    "outcome_side",
]


def normalize_fills(
    table: pa.Table,
    *,
    token_ids: frozenset[str],
    window_start: datetime,
    window_end: datetime,
) -> tuple[pa.Table, dict[str, int]]:
    """Filter exact tokens + window, validate, de-duplicate on the source's reliable key."""
    counts: dict[str, int] = {"raw_rows": table.num_rows}
    table = table.filter(pc.is_in(table["token_id"], value_set=pa.array(sorted(token_ids))))
    counts["rows_matching_tokens"] = table.num_rows
    block_time = pc.cast(pc.multiply(pc.cast(table["timestamp"], pa.int64()), 1_000_000), UTC_US)
    in_window = pc.and_(
        pc.greater_equal(block_time, pa.scalar(window_start, UTC_US)),
        pc.less(block_time, pa.scalar(window_end, UTC_US)),
    )
    counts["rows_outside_window"] = table.num_rows - pc.sum(in_window).as_py()
    table = table.filter(in_window)
    block_time = block_time.filter(in_window)
    counts["rows_inside_window"] = table.num_rows

    price = _float_text(table["price"])
    size = _float_text(table["size_shares"])
    n = table.num_rows
    fills = pa.table(
        {
            "fill_id": pc.binary_join_element_wise(
                table["tx_hash"], pc.cast(table["log_index"], pa.string()), table["token_id"], ":"
            ),
            "token_id": table["token_id"],
            "market_id": table["condition_id"],
            "polymarket_market_id": table["market_id"],
            "event_id": table["event_id"],
            "outcome": table["outcome_side"],
            "source_timestamp": block_time,
            "observed_at": block_time,
            "price": price,
            "size": size,
            "value_usd": _float_text(table["value_usd"]),
            "source_side": table["side"],
            "maker_address": table["maker_address"],
            "taker_address": table["taker_address"],
            "is_exchange_taker": table["is_exchange_taker"],
            "transaction_hash": table["tx_hash"],
            "log_index": pc.cast(table["log_index"], pa.int64()),
            "source_version": pa.array([FILL_SOURCE_VERSION] * n, pa.string()),
            "evidence_grade": pa.array([GRADE_TRADE_FILL] * n, pa.string()),
        },
        schema=FILL_SCHEMA,
    )
    price_bad = pc.or_(
        pc.less(table["price"], 0.0), pc.greater(table["price"], 1.0)
    )
    size_bad = pc.less(table["size_shares"], 0.0)
    key_bad = pc.or_(pc.is_null(table["tx_hash"]), pc.is_null(table["log_index"]))
    bad = pc.fill_null(pc.or_(pc.or_(price_bad, size_bad), key_bad), True)
    counts["rejected_malformed"] = pc.sum(bad).as_py() or 0
    fills = fills.filter(pc.invert(bad))

    order = pc.sort_indices(
        fills,
        sort_keys=[
            ("observed_at", "ascending"),
            ("token_id", "ascending"),
            ("transaction_hash", "ascending"),
            ("log_index", "ascending"),
        ],
    )
    fills = fills.take(order)
    unique = pc.unique(fills["fill_id"])
    counts["duplicates_removed"] = 0
    if len(unique) != fills.num_rows:
        # Keep the first occurrence of each reliable key; content already source-deduplicated.
        first = pc.index_in(unique, value_set=fills["fill_id"])
        fills = fills.take(pc.sort_indices(first))
        counts["duplicates_removed"] = n - counts["rejected_malformed"] - fills.num_rows
    counts["final_rows"] = fills.num_rows
    return fills, counts


def _float_text(values: Any) -> Any:
    """Shortest round-trip text for source-supplied binary floats."""
    return pc.cast(values, pa.string())
