"""Default-OFF, event-driven research provenance bridge.

No venue, runtime, writer or maker imports. The caller MUST install the optional
source callback explicitly and supply an independently approved bounded sink.
Records describe decoded/source claims, NEVER issuer authentication or fills.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Iterator, cast

SIG_TOURNAMENT: Final = "bda92870-621e-47b0-bc3c-3602c5c26f55"
SIG_MARKETS: Final = {"223": "912", "224": "913", "270": "959", "271": "960"}
MAX_SOURCE_BYTES: Final = 1_048_576
MAX_LADDER_LEVELS: Final = 4096
# Offline research freshness screen, NOT an issuer SLA or proof of source authenticity.
MAX_SOURCE_LAG: Final = timedelta(seconds=5)
# Independently pinned EXACT/SAME market, condition and YES-token orientation from
# frozen PR #318's _PAIRS and the accepted mapping blob 14823ac7043a451c587c6b7a0d3b684dbf7bb5d6.
# A different candidate's YES is NEVER same-market NO.
PM_EXACT_SAME_YES: Final = {
    "223": ("912", "0xe0fbb3e61e61b14f5fef15ffdd0e2a0c6cf4336bb5b15877fe3e3c9fdcc6bc7f",
            "94033987553817825990709512567031751048276730850154145976615728913102874968691"),
    "224": ("913", "0xf207d480dde3add509a4b1a355d4f3b06195f917de37ff94f7814d9277df0cdd",
            "70720420928389492978842914190430712299346582376255109200751838945374631181994"),
    "270": ("959", "0x66bbf6d55e0296278858b3147689f3df9259374f158f9f028b608baa322a639c",
            "7589880081658059374445882095611024032084645048848237404312287942463647821549"),
    "271": ("960", "0x2c00cb09722a0f0501c5e49131b898ed57bb5c075db4dc2bcbc14908f45b8c48",
            "2142948285506077695096498341598672159464223074468931276310105532825810210121"),
}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")

Sink = Callable[[dict[str, object]], None]


class SourceRejected(ValueError):
    """Fail-closed producer observation; bridge counts and poisons the attempt."""


def _require(test: bool, reason: str) -> None:
    if not test:
        raise SourceRejected(reason)


def _utc(value: str) -> datetime:
    _require(isinstance(value, str) and value.endswith(("Z", "+00:00")), "UTC_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceRejected("INVALID_UTC") from exc
    _require(result.utcoffset() == timedelta(0), "UTC_REQUIRED")
    return result


def _int(value: object, reason: str, *, min_value: int = 0) -> int:
    _require(type(value) is int and value >= min_value, reason)
    return cast(int, value)


def _text(value: object, reason: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), reason)
    return cast(str, value)


def _digest(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    _require(type(raw) is bytes and len(raw) <= MAX_SOURCE_BYTES, "RAW_BYTES_CAP")
    return hashlib.sha256(raw).hexdigest()


def _decimal(value: object, *, price: bool) -> Decimal:
    _require(isinstance(value, (str, int, float)) and not isinstance(value, bool),
             "LEVEL_NUMBER_MISSING")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise SourceRejected("LEVEL_NUMBER_INVALID") from exc
    _require(number.is_finite() and number > 0, "LEVEL_NUMBER_INVALID")
    if price:
        _require(number < 1, "LEVEL_PRICE_OUT_OF_RANGE")
    return number


def _levels(raw: object, side: str) -> list[list[str]]:
    _require(isinstance(raw, (tuple, list)) and 0 < len(raw) <= MAX_LADDER_LEVELS,
             "FULL_LADDER_UNAVAILABLE")
    result: list[list[str]] = []
    previous: Decimal | None = None
    for item in cast(Sequence[object], raw):
        if isinstance(item, Mapping):
            price = _decimal(item.get("price"), price=True)
            qty = _decimal(item.get("quantity", item.get("size")), price=False)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            price = _decimal(item[0], price=True)
            qty = _decimal(item[1], price=False)
        else:
            raise SourceRejected("LEVEL_SHAPE_INVALID")
        if previous is not None:
            _require(price < previous if side == "bids" else price > previous,
                     "LADDER_ORDER_OR_DUPLICATE")
        previous = price
        result.append([str(price), str(qty)])
    return result


def _book(bids: object, asks: object) -> dict[str, object]:
    b = _levels(bids, "bids")
    a = _levels(asks, "asks")
    _require(Decimal(b[0][0]) < Decimal(a[0][0]), "CROSSED_BOOK")
    return {"bids": b, "asks": a, "complete_source_claim": True}


class SourceBridge:
    """O(4-market + 4-token) state; emits each transition once, synchronously.

    A sink exception irreversibly poisons the bridge; never silently drops.
    A callback is an explicit research seam, NOT an auto-started collector.
    """

    def __init__(self, *, emit: Sink, producer_sha: str,
                 allowed_pm_tokens: Mapping[str, str],
                 max_clock_uncertainty_ms: int) -> None:
        _require(callable(emit), "SINK_REQUIRED")
        _require(isinstance(producer_sha, str) and SHA_RE.fullmatch(producer_sha) is not None,
                 "PRODUCER_SHA_REQUIRED")
        _require(set(allowed_pm_tokens) == set(SIG_MARKETS), "FOUR_EXACT_MARKETS_REQUIRED")
        values = list(allowed_pm_tokens.values())
        _require(len(set(values)) == 4 and all(isinstance(v, str) and v for v in values),
                 "UNIQUE_PM_TOKEN_REQUIRED")
        _require(all(SIG_MARKETS[m] == PM_EXACT_SAME_YES[m][0]
                     and allowed_pm_tokens[m] == PM_EXACT_SAME_YES[m][2]
                     for m in SIG_MARKETS), "PM_EXACT_SAME_YES_TOKEN_REQUIRED")
        self.emit = emit
        self.producer_sha = producer_sha
        self.tokens = dict(allowed_pm_tokens)
        self.pm_tokens = frozenset(values)
        self.uncertainty = _int(max_clock_uncertainty_ms, "CLOCK_BUDGET")
        _require(self.uncertainty <= 1000, "CLOCK_BUDGET_TOO_LARGE")
        self.sig_state: dict[str, tuple[int, int, int, datetime]] = {}
        # Independent native publisher state: epoch, asOf.sequence, asOf.at.
        # A no-book delivery MUST NOT move this clock or native sequence.
        self.sig_native_state: dict[str, tuple[int, int, datetime]] = {}
        self.pm_epoch: int | None = None
        self.pm_ordinal = 0
        self.pm_seeded: set[str] = set()
        self.pm_last_receive: datetime | None = None
        self.pm_disconnected = False
        self.poisoned = False
        self.records_emitted = 0
        self.attempts_total = 0
        self.rejected_total = 0
        self.lost_lower_bound = 0
        self.gap_events_total = 0
        self.last_failure_reason: str | None = None

    @contextmanager
    def _attempt(self) -> Iterator[None]:
        """Every attempted handler call is accounted; no post-reject continuation."""
        self.attempts_total += 1
        if self.poisoned:
            self.rejected_total += 1
            self.lost_lower_bound += 1
            self.last_failure_reason = "BRIDGE_POISONED"
            raise SourceRejected("BRIDGE_POISONED")
        try:
            yield
        except Exception as exc:
            self.poisoned = True
            self.rejected_total += 1
            self.lost_lower_bound += 1
            reason = str(exc) if isinstance(exc, SourceRejected) else ""
            self.last_failure_reason = (
                reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reason)
                else "SINK_OR_HANDLER_EXCEPTION"
            )
            raise

    def health_snapshot(self) -> dict[str, object]:
        """Privacy-safe, caller-readable loss LOWER BOUND; never invent missing frames."""
        return {
            "attempts_total": self.attempts_total,
            "records_emitted": self.records_emitted,
            "rejected_total": self.rejected_total,
            "lost_lower_bound": self.lost_lower_bound,
            "gap_events_total": self.gap_events_total,
            "poisoned": self.poisoned,
            "last_failure_reason": self.last_failure_reason,
            "last_good_sig": {
                market: {
                    "connection_epoch": state[0],
                    "revision": state[1],
                    "source_sequence_through": state[2],
                    "native_asof_sequence": (
                        self.sig_native_state[market][1]
                        if market in self.sig_native_state
                        and self.sig_native_state[market][0] == state[0] else None
                    ),
                }
                for market, state in self.sig_state.items()
            },
            "last_good_pm_epoch": self.pm_epoch,
            "last_good_pm_ordinal": self.pm_ordinal,
        }

    def _emit(self, record: dict[str, object]) -> None:
        _require(not self.poisoned, "BRIDGE_POISONED")
        record["schema"] = "alpha2-source-bridge-v1"
        record["producer_sha"] = self.producer_sha
        record["issuer_authenticated"] = False
        record["source_admitted"] = False
        record["paper_admitted"] = False
        try:
            self.emit(record)
        except Exception:
            self.poisoned = True
            raise
        self.records_emitted += 1

    def _clock(self, source_at: str | None, received_at: str) -> None:
        receive = _utc(received_at)
        if source_at is not None:
            source = _utc(source_at)
            u = timedelta(milliseconds=self.uncertainty)
            _require(source <= receive + u, "SOURCE_CLOCK_AFTER_RECEIVE")
            _require(receive - (source - u) <= MAX_SOURCE_LAG,
                     "SOURCE_PUBLISHER_RECEIPT_STALE_OR_UNATTESTED")

    def _pm_receipt(self, received_at: str, *, reset_epoch: bool = False) -> datetime:
        received = _utc(received_at)
        prior = None if reset_epoch else self.pm_last_receive
        if prior is not None:
            u = timedelta(milliseconds=self.uncertainty)
            _require(prior + u < received - u,
                     "PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS")
        return received

    def sig_batch(self, *, topic_market_id: str, batch: Mapping[str, Any],
                  received_at: str, connection_epoch: int,
                  raw_frame: bytes | None = None) -> None:
        """Call on EVERY existing decoded batch of each target market topic.

        Do not invoke only on books: delivery-only batches preserve continuity.
        'raw_frame=None' is honest for decoded callbacks lacking original bytes.
        """
        with self._attempt():
            _require(not self.poisoned, "BRIDGE_POISONED")
            if topic_market_id not in SIG_MARKETS:
                return
            epoch = _int(connection_epoch, "SIG_EPOCH_MISSING", min_value=1)
            delivery = batch.get("delivery")
            _require(isinstance(delivery, Mapping), "SIG_DELIVERY_MISSING")
            delivery = cast(Mapping[str, Any], delivery)
            rev = _int(delivery.get("revision"), "SIG_REVISION_MISSING")
            prev = _int(delivery.get("previousRevision"), "SIG_PREVIOUS_MISSING")
            first = _int(delivery.get("sourceSequenceFrom"), "SIG_SOURCE_FROM_MISSING")
            through = _int(delivery.get("sourceSequenceThrough"), "SIG_SOURCE_THROUGH_MISSING")
            _require(first <= through, "SIG_SOURCE_RANGE_INVALID")
            self._clock(None, received_at)
            previous = self.sig_state.get(topic_market_id)
            gap = bool(batch.get("resyncRequired"))
            if previous is not None:
                old_epoch, old_rev, old_through, old_receive = previous
                if epoch == old_epoch:
                    _require(_utc(received_at) >= old_receive, "SIG_RECEIVE_REGRESSION")
                    if rev <= old_rev or prev != old_rev or first < old_through:
                        gap = True
                elif epoch < old_epoch:
                    raise SourceRejected("SIG_EPOCH_REGRESSION")
                else:
                    gap = True  # reconnect is not a continuous native book proof
            else:
                gap = True  # first packet has no proved start-of-stream chain
            raw_digest = _digest(raw_frame)
            base: dict[str, object] = {
                "venue": "SIG", "kind": "BATCH", "market_id": topic_market_id,
                "exchange_id": SIG_MARKETS[topic_market_id],
                "tournament_id": SIG_TOURNAMENT,
                "revision": rev, "previous_revision": prev,
                "source_sequence_from": first, "source_sequence_through": through,
                "connection_epoch": epoch, "received_at": received_at,
                "clock_uncertainty_ms": self.uncertainty,
                "raw_frame_sha256": raw_digest, "wire_bytes_verified": False,
                "gap_or_unanchored": gap, "full_books": [],
            }
            native_books = batch.get("books", ())
            native_candidate = self.sig_native_state.get(topic_market_id)
            if native_candidate is not None and native_candidate[0] != epoch:
                # Epoch boundary discards comparison, never invents continuity.
                native_candidate = None
            _require(isinstance(native_books, (tuple, list)), "SIG_BOOKS_INVALID")
            for item in native_books:
                _require(isinstance(item, Mapping), "SIG_BOOK_INVALID")
                item = cast(Mapping[str, Any], item)
                exchange = str(item.get("exchangeId", ""))
                _require(exchange == SIG_MARKETS[topic_market_id], "SIG_EXCHANGE_MISMATCH")
                asof = item.get("asOf")
                _require(isinstance(asof, Mapping), "SIG_NATIVE_ASOF_MISSING")
                asof = cast(Mapping[str, Any], asof)
                native_at = _text(asof.get("at"), "SIG_NATIVE_CLOCK_MISSING")
                self._clock(native_at, received_at)
                native_seq = _int(asof.get("sequence"), "SIG_NATIVE_SEQUENCE_MISSING")
                publisher = _utc(native_at)
                if native_candidate is not None:
                    _require(native_seq > native_candidate[1],
                             "SIG_NATIVE_SEQUENCE_REGRESSION")
                    u = timedelta(milliseconds=self.uncertainty)
                    _require(native_candidate[2] + u < publisher - u,
                             "SIG_NATIVE_PUBLISHER_NONMONOTONIC_OR_AMBIGUOUS")
                native_candidate = (epoch, native_seq, publisher)
                books = base["full_books"]
                assert isinstance(books, list)
                books.append({
                    "asof_at": native_at, "asof_sequence": native_seq,
                    "full_depth": _book(item.get("bids"), item.get("asks")),
                })
            self._emit(base)
            # Commit local continuity only AFTER all original levels and sink acceptance.
            self.sig_state[topic_market_id] = (epoch, rev, through, _utc(received_at))
            if native_candidate is not None:
                self.sig_native_state[topic_market_id] = native_candidate
            if gap:
                self.gap_events_total += 1

    def pm_rest_seed(self, *, token_id: str, epoch: int,
                     bids: object, asks: object, received_at: str,
                     raw_response: bytes | None = None) -> None:
        """Caller invokes only AFTER token's REST seed actually completed."""
        with self._attempt():
            if token_id not in self.pm_tokens:
                return
            eid = _int(epoch, "PM_EPOCH_MISSING", min_value=1)
            new_epoch = self.pm_epoch != eid
            if new_epoch:
                _require(self.pm_epoch is None or eid > self.pm_epoch,
                         "PM_EPOCH_REGRESSION")
            else:
                _require(not self.pm_disconnected, "PM_RECONNECT_REQUIRES_NEW_EPOCH")
                _require(token_id not in self.pm_seeded, "PM_DUPLICATE_SEED")
            self._clock(None, received_at)
            receive = self._pm_receipt(received_at, reset_epoch=new_epoch)
            full = _book(bids, asks)
            self._emit({
                "venue": "PM", "kind": "REST_SEED_COMPLETE", "token_id": token_id,
                "connection_epoch": eid, "received_at": received_at,
                "ws_local_ordinal": 0, "clock_uncertainty_ms": self.uncertainty,
                "raw_response_sha256": _digest(raw_response),
                "wire_bytes_verified": False, "full_depth": full,
            })
            if new_epoch:
                self.pm_epoch = eid
                self.pm_ordinal = 0
                self.pm_seeded.clear()
            self.pm_seeded.add(token_id)
            self.pm_last_receive = receive
            self.pm_disconnected = False

    def pm_ws_event(self, *, epoch: int, ordinal: int, received_at: str,
                    affected_token_ids: Sequence[str],
                    reconstructed_books: Mapping[str, Mapping[str, object]],
                    uninitialized_tokens: Sequence[str] = (),
                    raw_frame: bytes | None = None) -> None:
        """One record per EVERY WS event ordinal, even no-change/unknown events.

        Ordinals are LOCAL counters from raw ordered handler invocation, never
        described as issuer/global PM WS sequence. No sparse-Parquet inference.
        """
        with self._attempt():
            _require(not self.poisoned, "BRIDGE_POISONED")
            eid = _int(epoch, "PM_EPOCH_MISSING", min_value=1)
            number = _int(ordinal, "PM_LOCAL_ORDINAL_MISSING", min_value=1)
            _require(eid == self.pm_epoch and not self.pm_disconnected,
                     "PM_UNSEEDED_EPOCH")
            self._clock(None, received_at)
            receive = self._pm_receipt(received_at)
            gap = number != self.pm_ordinal + 1
            _require(number > self.pm_ordinal, "PM_LOCAL_ORDINAL_REGRESSION")
            seeded = set(self.pm_seeded)
            if gap:
                seeded.clear()
            uninitialized = set(uninitialized_tokens).intersection(self.pm_tokens)
            seeded.difference_update(uninitialized)
            affected = set(affected_token_ids).intersection(self.pm_tokens)
            snapshots: dict[str, object] = {}
            for token in sorted(affected):
                if token not in seeded:
                    continue
                item = reconstructed_books.get(token)
                if not isinstance(item, Mapping):
                    seeded.discard(token)
                    continue
                try:
                    snapshots[token] = _book(item.get("bids"), item.get("asks"))
                except SourceRejected:
                    seeded.discard(token)
            self._emit({
                "venue": "PM", "kind": "WS_EVENT", "connection_epoch": eid,
                "ws_local_ordinal": number, "received_at": received_at,
                "clock_uncertainty_ms": self.uncertainty,
                "raw_frame_sha256": _digest(raw_frame), "wire_bytes_verified": False,
                "gap_or_unanchored": gap, "affected_tokens": sorted(affected),
                "uninitialized_tokens": sorted(uninitialized),
                "full_books": snapshots,
                "abstain_tokens": sorted(affected.difference(snapshots)),
            })
            if gap:
                self.gap_events_total += 1
                self.lost_lower_bound += number - self.pm_ordinal - 1
            self.pm_seeded = seeded
            self.pm_ordinal = number
            self.pm_last_receive = receive

    def pm_disconnect(self, *, epoch: int, received_at: str) -> None:
        with self._attempt():
            _require(epoch == self.pm_epoch, "PM_EPOCH_MISMATCH")
            self._clock(None, received_at)
            receive = self._pm_receipt(received_at)
            self._emit({"venue": "PM", "kind": "DISCONNECT", "connection_epoch": epoch,
                        "received_at": received_at, "ws_local_ordinal": self.pm_ordinal,
                        "full_books": {}, "gap_or_unanchored": True})
            self.pm_seeded.clear()
            self.pm_disconnected = True
            self.pm_last_receive = receive
            self.gap_events_total += 1


def whole_lot_vwap(levels: Sequence[Sequence[str]], size: int) -> Decimal | None:
    """Observable ladder screen only; per-level integer floor; NULL if short."""
    _int(size, "WHOLE_SIZE_REQUIRED", min_value=1)
    remaining = size
    cost = Decimal(0)
    for level in levels:
        price = _decimal(level[0], price=True)
        quantity = int(_decimal(level[1], price=False))
        take = min(remaining, quantity)
        cost += price * take
        remaining -= take
        if remaining == 0:
            return cost / Decimal(size)
    return None


def reference_only_gaps(
    *, sig: Mapping[str, object], pm: Mapping[str, object],
    size: int, after_shock_verified: bool = False,
) -> tuple[Decimal | None, Decimal | None]:
    """Bid/ask-reference YES/NO differences, NEVER real profits or admission."""
    _require(size in (1, 5, 10, 20), "FROZEN_SIZE_REQUIRED")
    if not after_shock_verified:
        return None, None
    sig_bids = sig.get("bids")
    sig_asks = sig.get("asks")
    pm_bids = pm.get("bids")
    pm_asks = pm.get("asks")
    if not all(isinstance(x, list) for x in (sig_bids, sig_asks, pm_bids, pm_asks)):
        return None, None
    sig_ask = whole_lot_vwap(cast(list[list[str]], sig_asks), size)
    sig_bid = whole_lot_vwap(cast(list[list[str]], sig_bids), size)
    pm_bid = whole_lot_vwap(cast(list[list[str]], pm_bids), size)
    pm_ask = whole_lot_vwap(cast(list[list[str]], pm_asks), size)
    yes = pm_bid - sig_ask if pm_bid is not None and sig_ask is not None else None
    no = sig_bid - pm_ask if sig_bid is not None and pm_ask is not None else None
    return yes, no
