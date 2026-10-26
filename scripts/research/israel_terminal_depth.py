"""Compact election-night L2 microstructure recorder. Observation-only.

L2 quantity decreases reflect *net displayed-liquidity removal*, not verified
cancellations: executions, replaces and updates can cause the same observation.
Uses in-memory state from book snapshots plus level deltas, and sparse periodic
records to stay bounded. All quantities are Polymarket shares, not SIG shares.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
DEPTH_TAPE = ROOT / "israel_rehearsal_depth.jsonl"
MAX_DEPTH_BYTES = 25_000_000
CLOSE = datetime.fromisoformat("2026-10-27T20:00:00+00:00").timestamp()
SIZES = (50, 250, 1000)


def terminal_window(epoch: float) -> str:
    seconds_left = CLOSE - epoch
    if 0 <= seconds_left <= 900:
        return "FINAL_15M"
    if 0 <= seconds_left <= 3600:
        return "FINAL_1H"
    if 0 <= seconds_left <= 21600:
        return "FINAL_6H"
    if seconds_left < 0:
        return "AFTER_CLOSE"
    return "BASELINE"


def sample_interval(epoch: float) -> float:
    window = terminal_window(epoch)
    return {
        "BASELINE": 30.0,
        "FINAL_6H": 15.0,
        "FINAL_1H": 5.0,
        "FINAL_15M": 1.0,
        "AFTER_CLOSE": 2.0,
    }[window]


def write_record(record: dict) -> None:
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    if DEPTH_TAPE.exists() and DEPTH_TAPE.stat().st_size + len(encoded.encode()) > MAX_DEPTH_BYTES:
        DEPTH_TAPE.replace(DEPTH_TAPE.with_suffix(".jsonl.1"))
    with DEPTH_TAPE.open("a", encoding="utf-8") as f:
        f.write(encoded)


def sane_levels(levels: object) -> dict[float, float]:
    values = {}
    if not isinstance(levels, list):
        return values
    for item in levels:
        if not isinstance(item, dict):
            continue
        try:
            price = round(float(item["price"]), 6)
            size = float(item["size"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if 0 < price < 1 and 0 < size < 1e10:
            values[price] = size
    return values


def sweep(levels: list[tuple[float, float]], quantity: int) -> dict:
    remaining, total = float(quantity), 0.0
    last = None
    for price, available in levels:
        taken = min(remaining, available)
        total += taken * price
        remaining -= taken
        if taken:
            last = price
        if remaining <= 1e-9:
            return {"vwap": round(total / quantity, 6), "worst": last}
    return {"vwap": None, "worst": None}


class DepthMonitor:
    def __init__(self, tokens: set[str]):
        self.tokens = set(tokens)
        self.books = {}
        self.ages = {}
        self.last_sample = {}
        self.counters = defaultdict(lambda: defaultdict(float))
        self.last_trade_price = {}

    def on_book(self, token: str, event: dict) -> None:
        if token not in self.tokens:
            return
        bids = sane_levels(event.get("bids", []))
        asks = sane_levels(event.get("asks", []))
        if not bids or not asks or max(bids) >= min(asks):
            self.books.pop(token, None)
            return
        self.books[token] = {"bids": bids, "asks": asks}
        self.ages[token] = time.time()
        self.counters[token]["book_events"] += 1

    def on_delta(self, token: str, update: dict) -> None:
        if token not in self.books:
            return
        side = update.get("side")
        side_key = "bids" if side == "BUY" else "asks" if side == "SELL" else None
        if side_key is None:
            return
        try:
            price = round(float(update["price"]), 6)
            size = float(update["size"])
        except (KeyError, ValueError, TypeError):
            return
        if not (0 < price < 1 and 0 <= size < 1e10):
            return
        book = self.books[token][side_key]
        old = book.get(price, 0.0)
        if size > old:
            self.counters[token]["display_added_qty"] += size - old
            self.counters[token]["display_added_events"] += 1
        elif size < old:
            self.counters[token]["display_removed_qty"] += old - size
            self.counters[token]["display_removed_events"] += 1
        if size:
            book[price] = size
        else:
            book.pop(price, None)
        self.ages[token] = time.time()
        self.counters[token]["level_change_events"] += 1

    def on_trade(self, token: str, event: dict) -> None:
        if token not in self.tokens:
            return
        try:
            qty, price = float(event.get("size", 0)), float(event.get("price", 0))
        except (TypeError, ValueError):
            return
        if 0 < qty < 1e10 and 0 < price < 1:
            self.counters[token]["trade_count"] += 1
            self.counters[token]["trade_shares"] += qty
            self.last_trade_price[token] = price

    def flush(self, *, now_epoch: float | None = None, force: bool = False) -> int:
        now_epoch = time.time() if now_epoch is None else now_epoch
        interval = sample_interval(now_epoch)
        wrote = 0
        for token in self.tokens:
            if token not in self.books:
                continue
            if not force and now_epoch - self.last_sample.get(token, 0.0) < interval:
                continue
            book = self.books[token]
            bids = sorted(book["bids"].items(), reverse=True)
            asks = sorted(book["asks"].items())
            if not bids or not asks or bids[0][0] >= asks[0][0]:
                self.counters[token]["invalid_books"] += 1
                continue
            bb, ba = bids[0][0], asks[0][0]
            buy_depth = {str(q): sweep(asks, q) for q in SIZES}
            sell_depth = {str(q): sweep(bids, q) for q in SIZES}
            rec = {
                "kind": "depth_sample",
                "received_epoch_ms": int(now_epoch * 1000),
                "asset_id": token,
                "window": terminal_window(now_epoch),
                "bid": bb, "ask": ba, "spread": round(ba - bb, 6),
                "best_bid_size": round(bids[0][1], 4),
                "best_ask_size": round(asks[0][1], 4),
                "book_age_ms": max(0, round((now_epoch - self.ages[token]) * 1000)),
                "bid_levels": len(bids), "ask_levels": len(asks),
                "buy_sweep": buy_depth, "sell_sweep": sell_depth,
                "top_bid_levels": [(p, round(q, 4)) for p, q in bids[:5]],
                "top_ask_levels": [(p, round(q, 4)) for p, q in asks[:5]],
                "counts_since_previous": {
                    k: round(v, 4) for k, v in self.counters[token].items()
                },
                "last_trade_price": self.last_trade_price.get(token),
                "cancellation_count_confirmed": None,
                "note": "Net displayed liquidity removals are NOT individually proven cancellations",
            }
            write_record(rec)
            self.last_sample[token] = now_epoch
            self.counters[token].clear()
            wrote += 1
        return wrote
