"""Read-only frozen 005I reversal versus original native SIG books.

PM periodic depth is NOT a proven REST-seeded/continuous WS feed. Every
quote return is NONEXECUTABLE_PROXY, never a trade, observed fill or net PnL.
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from predictions_cup.replay.economic_005i import _marginal_buy_limit, _outcome_levels, _walk
from predictions_cup.replay.mapping_005i import load_accepted_005i_pairs
from predictions_cup.replay.model import BookLevel, InstrumentView, ReplaySource

# Independently pinned four accepted EXACT SAME YES tokens from #318/#319.
PINNED = {
    "912": "94033987553817825990709512567031751048276730850154145976615728913102874968691",
    "913": "70720420928389492978842914190430712299346582376255109200751838945374631181994",
    "959": "7589880081658059374445882095611024032084645048848237404312287942463647821549",
    "960": "2142948285506077695096498341598672159464223074468931276310105532825810210121",
}
SIZES = (1, 5, 10, 20)
FRESH = timedelta(seconds=5)
EXIT_WAIT = timedelta(seconds=10)


def utc(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        raise ValueError("explicit UTC required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timedelta(0):
        raise ValueError("non-UTC timestamp")
    return result


def ladder(raw: object, *, bid: bool) -> tuple[BookLevel, ...]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not 0 < len(raw) <= 4096:
        raise ValueError("incomplete depth")
    out: list[BookLevel] = []
    prev: Decimal | None = None
    for item in raw:
        if isinstance(item, dict):
            price, qty = item.get("price"), item.get("quantity", item.get("size"))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            price, qty = item
        else:
            raise ValueError("malformed level")
        try:
            p, q = Decimal(str(price)), Decimal(str(qty))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("malformed number") from exc
        if not p.is_finite() or not q.is_finite() or not 0 < p < 1 or q <= 0:
            raise ValueError("invalid price/quantity")
        if prev is not None and (p >= prev if bid else p <= prev):
            raise ValueError("unordered/duplicate level")
        out.append(BookLevel(p, q))
        prev = p
    return tuple(out)


@dataclass(frozen=True)
class Native:
    exchange: str
    received: datetime
    publisher: datetime
    sequence: int
    session: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]


@dataclass(frozen=True)
class Signal:
    exchange: str
    end: datetime
    side: str


def native_rows(rows: Iterable[dict[str, Any]], counts: Counter[str]
                ) -> dict[str, list[Native]]:
    result: dict[str, list[Native]] = defaultdict(list)
    prior: dict[str, Native] = {}
    for row in sorted(rows, key=lambda r: utc(r["local_receive_at"])):
        counts["sig_raw_rows"] += 1
        try:
            received = utc(row["local_receive_at"])
            frame = json.loads(row["raw_json"])
            books = frame["books"]
            if not isinstance(books, list):
                raise ValueError("books not list")
            for raw in books:
                exchange = str(raw.get("exchangeId"))
                if exchange not in PINNED:
                    continue
                try:
                    source = utc(raw["asOf"]["at"])
                    seq = raw["asOf"]["sequence"]
                    if type(seq) is not int or seq < 1:
                        raise ValueError("missing sequence")
                    if not timedelta(0) <= received - source <= FRESH:
                        raise ValueError("publisher lag/future")
                    bids = ladder(raw["bids"], bid=True)
                    asks = ladder(raw["asks"], bid=False)
                    if bids[0].price >= asks[0].price:
                        raise ValueError("crossed book")
                    session = str(row.get("session_id") or "") + ":" + str(
                        row.get("connection_epoch") or ""
                    )
                    book = Native(exchange, received, source, seq, session, bids, asks)
                    previous = prior.get(exchange)
                    if previous is not None and (
                        received <= previous.received
                        or (session == previous.session and (
                            seq <= previous.sequence or source <= previous.publisher
                        ))
                    ):
                        counts["sig_native_regression"] += 1
                        continue
                    if previous is not None and session != previous.session:
                        counts["sig_epoch_change_unproven"] += 1
                    prior[exchange] = book
                    result[exchange].append(book)
                    counts["sig_two_sided_" + exchange] += 1
                except (KeyError, ValueError, TypeError):
                    counts["sig_invalid_native"] += 1
        except (KeyError, ValueError, TypeError):
            counts["sig_invalid_raw"] += 1
    return result


def completed_reversal(rows: Iterable[dict[str, Any]],
                       counts: Counter[str]) -> list[Signal]:
    """Six completed minutes; PM receipt-freshness proxy, not original WS parity."""
    ex_by_token = {token: ex for ex, token in PINNED.items()}
    minutes: dict[str, dict[datetime, tuple[datetime, Decimal]]] = defaultdict(dict)
    for row in rows:
        counts["pm_rows"] += 1
        token = str(row.get("token_id"))
        if token not in ex_by_token:
            continue
        counts["pm_exact_token_rows"] += 1
        try:
            at = utc(row["recorded_at"])
            bids = ladder(row["bids"], bid=True)
            asks = ladder(row["asks"], bid=False)
            if bids[0].price >= asks[0].price or row.get("book_valid") is False:
                raise ValueError("invalid PM book")
            end = at.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if end - at > FRESH:
                counts["pm_stale_at_minute_end"] += 1
                continue
            earlier = minutes[token].get(end)
            if earlier is None or at > earlier[0]:
                minutes[token][end] = (at, (bids[0].price + asks[0].price) / 2)
        except (ValueError, TypeError, KeyError):
            counts["pm_invalid"] += 1
    signals = []
    for token, history in minutes.items():
        last: datetime | None = None
        for end in sorted(history):
            counts["pm_closed_minute_candidates"] += 1
            six = [end - timedelta(minutes=i) for i in range(5, -1, -1)]
            if any(minute not in history for minute in six):
                counts["missing_six_minutes"] += 1
                continue
            ret = history[end][1] - history[six[0]][1]
            if ret == 0:
                counts["flat_5m"] += 1
                continue
            counts["n_signed_before_purge"] += 1
            if last is not None and end - last < timedelta(minutes=5):
                counts["overlap_purged"] += 1
                continue
            last = end
            signals.append(Signal(ex_by_token[token], end,
                                  "YES" if ret < 0 else "NO"))
    counts["n_signed_signal"] = len(signals)
    return sorted(signals, key=lambda s: (s.end, s.exchange))


def levels(book: Native, side: str, *, buy: bool) -> tuple[BookLevel, ...]:
    view = InstrumentView(
        source=ReplaySource.SIG, instrument_id=book.exchange, market_id=None,
        best_bid=book.bids[0].price, best_ask=book.asks[0].price,
        bids=book.bids, asks=book.asks, last_trade=None,
        quote_observed_at=book.received, last_event_observed_at=book.received,
        trusted=False, book_valid=True, source_available=True,
    )
    # Reuse #209's SAME-exchange YES/NO complement. Floor each level, not total.
    return tuple(BookLevel(x.price, x.quantity.to_integral_value(rounding=ROUND_FLOOR))
                 for x in _outcome_levels(view, side=side, buy=buy) if x.quantity >= 1)


def quote(book: Native, side: str, size: int, *, buy: bool
          ) -> Decimal | None:
    depth = levels(book, side, buy=buy)
    qty = Decimal(size)
    cost = _walk(depth, qty, buy=buy, haircut=Decimal(1))
    if cost is None:
        return None
    if buy and _marginal_buy_limit(depth, qty, haircut=Decimal(1)) is None:
        return None
    return cost / qty


def after(books: list[Native], ticks: list[datetime], at: datetime,
          wait: timedelta) -> Native | None:
    i = bisect.bisect_left(ticks, at)
    return books[i] if i < len(ticks) and ticks[i] - at <= wait else None


def replay(signals: Iterable[Signal], source: dict[str, list[Native]], *,
           latency_ms: int = 0, placebo: bool = False) -> dict[str, Any]:
    if not 0 <= latency_ms <= 2000:
        raise ValueError("0 <= latency_ms <= 2000 required")
    signals = tuple(signals)
    series = {key: sorted(value, key=lambda b: b.received)
              for key, value in source.items()}
    ticks = {key: [book.received for book in books]
             for key, books in series.items()}
    counts: Counter[str] = Counter()
    outcomes: dict[int, list[Decimal]] = defaultdict(list)
    wrong: dict[int, list[Decimal]] = defaultdict(list)
    latency: dict[int, list[Decimal]] = defaultdict(list)
    rows: list[dict[str, object]] = []
    for signal in signals:
        counts["n_signal"] += 1
        books, times = series.get(signal.exchange, []), ticks.get(signal.exchange, [])
        entry = after(books, times, signal.end + timedelta(milliseconds=latency_ms), FRESH)
        if entry is None:
            counts["n_entry_unknown"] += 1
            continue
        counts["n_sig_entry"] += 1
        exit_book = after(books, times, entry.received + timedelta(minutes=5), EXIT_WAIT)
        if exit_book is None:
            counts["n_exit_unknown"] += 1
        else:
            counts["n_sig_exit"] += 1
        reference = after(books, times, signal.end, FRESH)
        opposite = "NO" if signal.side == "YES" else "YES"
        for size in SIZES:
            ask = quote(entry, signal.side, size, buy=True)
            if ask is None:
                counts[f"n_{size}_entry_depth_unknown"] += 1
                continue
            counts[f"n_{size}_entry_feasible"] += 1
            bid = quote(exit_book, signal.side, size, buy=False) if exit_book else None
            if bid is None:
                counts[f"n_{size}_exit_depth_unknown"] += 1
                continue
            counts[f"n_{size}_feasible"] += 1
            outcomes[size].append(bid - ask)
            flip_ask = quote(entry, opposite, size, buy=True)
            flip_bid = quote(exit_book, opposite, size, buy=False)
            if flip_ask is not None and flip_bid is not None:
                wrong[size].append(flip_bid - flip_ask)
            if reference is not None and reference.received < entry.received:
                early = quote(reference, signal.side, size, buy=True)
                if early is not None:
                    latency[size].append(ask - early)
            if size == 1:
                rows.append({"exchange": signal.exchange, "side": signal.side,
                             "feature_end": signal.end.isoformat(),
                             "entry_receipt": entry.received.isoformat(),
                             "entry_publisher": entry.publisher.isoformat(),
                             "exit_receipt": exit_book.received.isoformat(),
                             "ask": str(ask), "bid": str(bid),
                             "zero_fee_quote_markout": str(bid - ask)})
    def describe(items: list[Decimal]) -> dict[str, object]:
        return {"n": len(items),
                "mean": str(sum(items) / len(items)) if items else None,
                "worst": str(min(items)) if items else None,
                "best": str(max(items)) if items else None}
    shifted = None
    if not placebo:
        shifted_output = replay(
            (Signal(x.exchange, x.end + timedelta(minutes=10), x.side)
             for x in signals), source, latency_ms=latency_ms, placebo=True,
        )
        shifted = {n: shifted_output["sizes"][n]["ask_to_bid_proxy"]
                   for n in (str(size) for size in SIZES)}
    return {
        "status": "NONEXECUTABLE_PROXY_PM_ORIGINAL_SEED_WS_UNPROVEN",
        "sig_fees": 0, "assumed_receipt_delay_ms": latency_ms,
        "counts": dict(sorted(counts.items())),
        "sizes": {
            str(size): {
                "ask_to_bid_proxy": describe(outcomes[size]),
                "opposite_side_placebo": describe(wrong[size]),
                "observed_arrival_ask_deterioration": describe(latency[size]),
            } for size in SIZES
        },
        "time_shift_10m_placebo": shifted,
        "row_examples_size_1": rows[:10],
        "source_admitted_pairs": 0,
        "real_fill_count": None, "fill_rate": None,
        "real_net_pnl": None, "true_executable_opportunity_count": None,
        "adverse_zero_salvage_bound": None,
        "claim_boundary": "Native quotes are real raw book claims but sequence "
                          "continuity, PM seed/WS, queue, fill and surviving "
                          "available liquidity are not certified.",
    }


def archived(root: Path, kind: str, start: datetime, end: datetime
             ) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    files = []
    for path in (root / kind).rglob("*.parquet"):
        try:
            stamp = datetime.strptime(path.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if start - timedelta(minutes=7) <= stamp <= end:
            files.append(path)
    if len(files) > 150:
        raise ValueError(f"{kind}: >150 shards; narrow UTC window")
    rows: list[dict[str, Any]] = []
    for file in sorted(files):
        parquet = pq.ParquetFile(file)
        wanted = ("local_receive_at", "raw_json", "session_id", "connection_epoch") if (
            kind == "raw_events"
        ) else ("recorded_at", "token_id", "bids", "asks", "book_valid")
        columns = [c for c in wanted if c in parquet.schema_arrow.names]
        required = {"local_receive_at", "raw_json"} if kind == "raw_events" else (
            {"recorded_at", "token_id", "bids", "asks"}
        )
        if not required.issubset(columns):
            raise ValueError(f"{kind}: required Parquet columns absent")
        for batch in parquet.iter_batches(columns=columns, batch_size=256):
            for row in batch.to_pylist():
                at = utc(row["local_receive_at"] if kind == "raw_events" else row["recorded_at"])
                if start - timedelta(minutes=6) <= at < end:
                    rows.append(row)
                if len(rows) > 240_000:
                    raise ValueError("240k row budget exceeded; narrow UTC window")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline 005I SIG native quote-only replay")
    parser.add_argument("--sig-root", required=True, type=Path)
    parser.add_argument("--pm-root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=utc)
    parser.add_argument("--end", required=True, type=utc)
    parser.add_argument("--latency-ms", type=int, default=0)
    args = parser.parse_args()
    if args.start >= args.end or args.start < utc("2026-10-12T00:00:00Z"):
        parser.error("invalid prospective date range")
    pairs = load_accepted_005i_pairs(
        Path(__file__).resolve().parents[3], sig_exchange_ids=tuple(PINNED)
    )
    if any(p.pm_yes != PINNED[p.sig_exchange_id] or p.mapping_direction != "SAME"
           for p in pairs):
        raise ValueError("frozen SHA-verified EXACT SAME YES mapping mismatch")
    sig_rows = archived(args.sig_root, "raw_events", args.start, args.end)
    pm_rows = archived(args.pm_root, "depth_snapshots", args.start, args.end)
    counts: Counter[str] = Counter()
    sig = native_rows(sig_rows, counts)
    signals = [s for s in completed_reversal(pm_rows, counts)
               if args.start <= s.end < args.end]
    output = replay(signals, sig, latency_ms=args.latency_ms)
    output["input_counts"] = dict(sorted(counts.items()))
    output["UTC_window"] = [args.start.isoformat(), args.end.isoformat()]
    output["source"] = ["existing SIG raw_events/*.parquet",
                        "uncertified PM depth_snapshots/*.parquet"]
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
