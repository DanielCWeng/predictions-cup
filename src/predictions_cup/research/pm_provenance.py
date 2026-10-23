"""Research-only Polymarket book provenance / orphan token attribution.

The input is a separately captured ordered websocket transcript, not the
current Parquet depth rows (which do not preserve missing-token or reconnect
provenance). No networking and no attempt to invent lost liquidity.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from predictions_cup.external.polymarket.models import PayloadError
from predictions_cup.external.polymarket.orderbook import OrderBookStore, event_type
from predictions_cup.research.paired_depth import InvalidInput, _time, load_specs


@dataclass
class PmProvenance:
    subscribed: frozenset[str]
    opposite: frozenset[str]
    books: OrderBookStore = field(default_factory=OrderBookStore)
    counters: Counter[str] = field(default_factory=Counter)
    epoch: int = 0
    connected: bool = False
    seeded: set[str] = field(default_factory=set)
    last_recv: datetime | None = None
    last_source: dict[str, datetime] = field(default_factory=dict)

    def boundary(self, kind: str, at: datetime) -> dict[str, Any]:
        if kind not in ("CONNECTED", "DISCONNECTED", "API_ERROR", "SOURCE_GAP"):
            raise InvalidInput("invalid WS lifecycle boundary")
        self.epoch += 1
        self.connected = kind == "CONNECTED"
        self.seeded.clear()
        self.books.invalidate_all()
        self.last_source.clear()
        self.counters[f"boundary_{kind}"] += 1
        return {
            "event": "BOUNDARY", "epoch": self.epoch, "received_at": at.isoformat(),
            "status": kind, "cleared_books": True, "trusted": False,
        }

    def _category(self, token: str) -> str:
        if token in self.subscribed:
            return "SUBSCRIBED_UNSEEDED"
        if token in self.opposite:
            return "UNSUBSCRIBED_OPPOSITE"
        return "OUTSIDE_ACCEPTED_TOKEN_SET"

    def _snapshot(self, token: str, received: datetime, kind: str) -> dict[str, Any]:
        book = self.books.snapshot(token, 200)
        if book is None:
            return {"event": "REJECTED", "token_id": token, "reason": "MISSING_FULL_SEED",
                    "epoch": self.epoch, "received_at": received.isoformat(),
                    "trusted": False}
        source = book.source_timestamp
        source_ok = source is not None and source <= received
        two_sided = bool(book.bids and book.asks)
        uncrossed = bool(two_sided and book.bids[0].price < book.asks[0].price)
        available = self.connected and token in self.seeded and uncrossed and source_ok
        # Local websocket order alone cannot prove that a source frame was not
        # silently dropped: CLOB price_change does not expose a token sequence.
        return {
            "event": "PM_BOOK_STATE", "venue": "PM", "instrument_id": token,
            "market_id": book.market_id, "kind": kind,
            "exchange_at": source.isoformat() if source else None,
            "received_at": received.isoformat(),
            "bids": [{"price": str(x.price), "size": str(x.size)} for x in book.bids],
            "asks": [{"price": str(x.price), "size": str(x.size)} for x in book.asks],
            "connection_epoch": self.epoch,
            "complete": token in self.seeded,
            "connected": self.connected,
            "seeded_from_full_book": token in self.seeded,
            "continuity": False, "trusted": False,
            "local_continuous_since_seed": available,
            "source_sequence_available": False, "source_sequence_verified": False,
            "reason": ("UNPROVEN_SOURCE_SEQUENCE" if available else
                       "CROSSED_OR_ZERO_DEPTH" if not uncrossed else
                       "MISSING_SOURCE_TIMESTAMP" if not source_ok else
                       "UNSEEDED_OR_DISCONNECTED"),
        }

    def receive(self, payload: dict[str, Any], received: datetime) -> list[dict[str, Any]]:
        if not self.connected:
            self.counters["messages_while_disconnected"] += 1
            return [{"event": "REJECTED", "reason": "WEBSOCKET_DISCONNECTED",
                     "received_at": received.isoformat(), "trusted": False}]
        if self.last_recv is not None and received < self.last_recv:
            self.counters["out_of_order_receipt"] += 1
            self.boundary("SOURCE_GAP", received)
            return [{"event": "REJECTED", "reason": "OUT_OF_ORDER_RECEIPT",
                     "received_at": received.isoformat(), "trusted": False}]
        self.last_recv = received
        kind = event_type(payload)
        body = payload.get("payload", payload) if payload.get("topic") == "market" else payload
        if not isinstance(body, dict):
            return [{"event": "REJECTED", "reason": "INVALID_MARKET_ENVELOPE",
                     "received_at": received.isoformat(), "trusted": False}]
        if kind == "book":
            token = str(body.get("asset_id") or body.get("tokenId") or "")
            if token not in self.subscribed:
                self.counters["full_seed_"+self._category(token)] += 1
                return []
            if not isinstance(body.get("bids"), list) or not isinstance(body.get("asks"), list):
                self.counters["incomplete_full_snapshot"] += 1
                self.seeded.discard(token)
                self.books.invalidate(frozenset((token,)))
                return [{"event": "REJECTED", "token_id": token,
                         "reason": "INCOMPLETE_FULL_SNAPSHOT",
                         "received_at": received.isoformat(), "trusted": False}]
            try:
                self.books.apply_full_snapshot(payload, received)
                self.seeded.add(token)
                self.counters["full_seed_subscribed"] += 1
            except (PayloadError, ValueError, KeyError, TypeError):
                self.counters["invalid_full_seed"] += 1
                self.seeded.discard(token)
                self.books.invalidate(frozenset((token,)))
                return [{"event": "REJECTED", "token_id": token,
                         "reason": "INVALID_FULL_SNAPSHOT",
                         "received_at": received.isoformat(), "trusted": False}]
            return [self._snapshot(token, received, "FULL_SNAPSHOT")]
        if kind == "price_change":
            entries = body.get("price_changes") or body.get("priceChanges")
            if not isinstance(entries, list):
                self.counters["malformed_price_change"] += 1
                return [{"event": "REJECTED", "reason": "MALFORMED_PRICE_CHANGE",
                         "received_at": received.isoformat(), "trusted": False}]
            accepted: list[dict[str, Any]] = []
            for raw in entries:
                if not isinstance(raw, dict):
                    self.counters["malformed_entry"] += 1
                    continue
                token = str(raw.get("asset_id") or raw.get("tokenId") or "")
                if token not in self.seeded or token not in self.subscribed:
                    category = self._category(token)
                    self.counters["ignored_delta_"+category] += 1
                    accepted.append({
                        "event": "REJECTED", "token_id": token,
                        "reason": "UNINITIALIZED_DELTA_" + category,
                        "received_at": received.isoformat(), "epoch": self.epoch,
                        "trusted": False,
                    })
                    continue
                # Separate singleton delta to isolate a bad entry from all
                # other tokens; never mark a failed partial as a full book.
                one = dict(body)
                one["price_changes"] = [raw]
                one.pop("priceChanges", None)
                try:
                    self.books.apply_price_change(one, received)
                except (PayloadError, ValueError, TypeError, KeyError):
                    self.counters["invalid_seeded_delta"] += 1
                    self.seeded.discard(token)
                    self.books.invalidate(frozenset((token,)))
                    accepted.append({"event": "REJECTED", "token_id": token,
                                     "reason": "INVALID_DELTA_INVALIDATED",
                                     "received_at": received.isoformat(),
                                     "trusted": False})
                    continue
                self.counters["applied_delta_subscribed"] += 1
                accepted.append(self._snapshot(token, received, "PRICE_CHANGE"))
            return accepted
        self.counters["event_"+str(kind)] += 1
        return []

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "epoch": self.epoch,
            "connected": self.connected,
            "subscribed_tokens": len(self.subscribed),
            "subscribed_seeded": len(self.seeded),
            "counters": dict(self.counters),
            "trust": "NOT_CERTIFIED_NO_SOURCE_SEQUENCE",
            "reason": "Cannot infer missed websocket frames from depth Parquet or book hashes",
        }


def main() -> int:
    p = argparse.ArgumentParser(description="PM WS RAW transcript forensic replay; NO network")
    p.add_argument("--input", type=Path, required=True,
                   help="JSONL raw WS MESSAGE and BOUNDARY records with received_at")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mapping", type=Path, required=True)
    p.add_argument("--acceptance", type=Path, required=True)
    p.add_argument("--exchange-ids", required=True)
    a = p.parse_args()
    if a.output.exists() or a.output.resolve() == a.input.resolve():
        raise InvalidInput("output must be a NEW independent file")
    specs = load_specs(a.mapping, a.acceptance, a.exchange_ids.split(","))
    mapping = json.loads(a.mapping.read_text())
    chosen = set(specs)
    opposite: set[str] = set()
    for r in mapping["records"]:
        if r["sig_exchange_id"] in chosen:
            record = r["direct_polymarket"]
            opposite.update(set(record["token_ids"]) - {record["mapped_token_id"]})
    audit = PmProvenance(frozenset(s.token for s in specs.values()), frozenset(opposite))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.input.open(encoding="utf-8") as source, a.output.open("x", encoding="utf-8") as dest:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                at = _time(row["received_at"])
                if row["event"] == "BOUNDARY":
                    results = [audit.boundary(row["status"], at)]
                elif row["event"] == "MESSAGE":
                    results = audit.receive(row["payload"], at)
                else:
                    raise InvalidInput("unknown transcript event")
            except (KeyError, TypeError, json.JSONDecodeError, InvalidInput) as exc:
                audit.counters["malformed_record"] += 1
                results = [{"event": "REJECTED", "input_line": number,
                            "reason": type(exc).__name__, "trusted": False}]
            for r in results:
                dest.write(json.dumps(r, sort_keys=True) + "\n")
        dest.write(json.dumps({"event": "SCORECARD", **audit.report()}, sort_keys=True) + "\n")
    print(json.dumps(audit.report(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
