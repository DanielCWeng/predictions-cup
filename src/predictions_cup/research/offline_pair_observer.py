"""Offline-only proposed SIG/PM paired observer; no venue clients or runtime wiring.

This module NEVER authenticates issuer bytes, source rights, actual fills or fees.
It can only validate source-shaped fixture claims.  Default state is disabled.
A future independent operator verifier and separate written research release are
required even for an offline integration.  No network or disk calls occur here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

# Frozen 2026 EXACT/SAME YES-token crosswalk (public registry, main Git blob
# 14823ac7043a451c587c6b7a0d3b684dbf7bb5d6).
# Other-party YES is NEVER interpreted as this market's NO.
_PAIRS = {
    "912": ("223", "0xe0fbb3e61e61b14f5fef15ffdd0e2a0c6cf4336bb5b15877fe3e3c9fdcc6bc7",
            "94033987553817825990709512567031751048276730850154145976615728913102874968691"),
    "913": ("224", "0xf207d480dde3add509a4b1a355d4f3b06195f917de37ff94f7814d9277df0cdd",
            "70720420928389492978842914190430712299346582376255109200751838945374631181994"),
    "959": ("270", "0x66bbf6d55e0296278858b3147689f3df9259374f158f9f028b608baa322a639c",
            "7589880081658059374445882095611024032084645048848237404312287942463647821549"),
    "960": ("271", "0x2c00cb09722a0f0501c5e49131b898ed57bb5c075db4dc2bcbc14908f45b8c48",
            "2142948285506077695096498341598672159464223074468931276310105532825810210121"),
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_MAX_RAW = 65_536
_MAX_OUTPUT = 8_388_608
_MAX_RECORDS = 20_000
_MAX_AGE = timedelta(hours=24)
_MAX_STALENESS = timedelta(seconds=5)
_WHOLE_LOTS = (1, 5, 10, 20)


class OfflineDenied(ValueError):
    """Falsifiable reason for denying a nonadmitted research-only record."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise OfflineDenied(reason)


def _text(value: object, reason: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), reason)
    return str(value)


def _integer(value: object, reason: str, *, low: int = 0) -> int:
    _need(type(value) is int and value >= low, reason)
    return int(value)


def _utc(value: object) -> datetime:
    _need(isinstance(value, str) and value.endswith(("Z", "+00:00")),
          "MISSING_EXPLICIT_UTC")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfflineDenied("INVALID_UTC") from exc
    _need(dt.utcoffset() == timedelta(0), "NON_UTC_CLOCK")
    return dt


def _decimal(value: object, *, price: bool = False) -> Decimal:
    _need(isinstance(value, str), "DECIMAL_STRING_REQUIRED")
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise OfflineDenied("INVALID_DECIMAL") from exc
    _need(d.is_finite() and (Decimal(0) < d < Decimal(1) if price else d > 0),
          "INVALID_POSITIVE_LEVEL")
    return d


def _levels(raw: object, side: str) -> tuple[tuple[Decimal, Decimal], ...]:
    _need(isinstance(raw, list) and 0 < len(raw) <= 100, "INCOMPLETE_FULL_DEPTH")
    parsed: list[tuple[Decimal, Decimal]] = []
    for level in raw:
        _need(isinstance(level, list) and len(level) == 2, "INVALID_LEVEL")
        parsed.append((_decimal(level[0], price=True), _decimal(level[1])))
    prices = [p for p, _ in parsed]
    _need(all((a > b if side == "bids" else a < b)
              for a, b in zip(prices, prices[1:])), "UNSORTED_OR_DUPLICATE_DEPTH")
    return tuple(parsed)


def _book(row: Mapping[str, Any]) -> tuple[
    tuple[tuple[Decimal, Decimal], ...], tuple[tuple[Decimal, Decimal], ...]
]:
    bids = _levels(row.get("bids"), "bids")
    asks = _levels(row.get("asks"), "asks")
    _need(bids[0][0] < asks[0][0], "CROSSED_BOOK")
    return bids, asks


def _vwap(levels: tuple[tuple[Decimal, Decimal], ...], whole: int) -> Decimal | None:
    remaining = Decimal(whole)
    total = Decimal(0)
    for price, qty in levels:
        take = min(remaining, qty)
        total += take * price
        remaining -= take
        if remaining == 0:
            return total / Decimal(whole)
    return None


@dataclass(frozen=True)
class OperatorRelease:
    """Future external verifier's grant; identifiers are NOT verified in this module."""

    release_id: str
    signature_sha256: str
    expires_at: str


@dataclass(frozen=True)
class Screen:
    verdict: str
    exchange_id: str
    whole_shares: int
    yes_reference_gap: str | None
    no_reference_gap: str | None
    native_fill_rate: None = None
    authentic_fees: None = None
    after_cost_pnl: None = None
    source_admitted: bool = False
    paper_admitted: bool = False
    certifies_250ms_survival: bool = False


class OfflinePairObserver:
    """Bounded, non-network in-memory candidate record serializer.

    Construct without a release to remain disabled. A release + independent
    injected verifier can enable ONLY offline synthetic/in-memory append.
    No production integration or trust decision is supplied by this module.
    """

    def __init__(
        self,
        release: OperatorRelease | None = None,
        independent_verify: Callable[[OperatorRelease], bool] | None = None,
    ) -> None:
        self._enabled = False
        if release is not None and independent_verify is not None:
            _need(bool(_HEX64.fullmatch(release.signature_sha256)), "BAD_RELEASE_DIGEST")
            _text(release.release_id, "RELEASE_ID_MISSING")
            _need(_utc(release.expires_at) > datetime.now(timezone.utc),
                  "RELEASE_EXPIRED")
            self._enabled = independent_verify(release) is True
        self._rows: list[bytes] = []
        self._size = 0
        self._start: datetime | None = None
        self._last_at: datetime | None = None
        self._sig: dict[str, dict[str, Any]] = {}
        self._pm: dict[str, dict[str, Any]] = {}
        self._chain = "0" * 64
        self.aborted = False

    @property
    def enabled(self) -> bool:
        return self._enabled and not self.aborted

    def abort(self) -> None:
        self.aborted = True
        self._enabled = False

    def _validate(self, row: Mapping[str, Any]) -> None:
        kind = row.get("kind")
        _need(kind in {"SIG_FULL_BOOK", "PM_FULL_SEED", "PM_BOOK_CHANGE"},
              "UNSUPPORTED_SOURCE_KIND")
        _need(row.get("source") == {
            "SIG_FULL_BOOK": "SIG_REALTIME_NATIVE_FULL_BOOK",
            "PM_FULL_SEED": "PM_REST_SEED_FULL_BOOK",
            "PM_BOOK_CHANGE": "PM_WS_RECONSTRUCTED_FULL_BOOK",
        }[kind], "SOURCE_IS_NOT_NATIVE_FULL_BOOK_OR_SEEDED_PM")
        _need(_HEX40.fullmatch(_text(row.get("producer_git_sha"), "PRODUCER_MISSING"))
              is not None, "PRODUCER_SHA_INVALID")
        _text(row.get("producer_run_id"), "PRODUCER_RUN_MISSING")
        exchange = _text(row.get("exchange_id"), "EXCHANGE_MISSING")
        _need(exchange in _PAIRS and row.get("market_id") == _PAIRS[exchange][0]
              and row.get("condition_id") == _PAIRS[exchange][1]
              and row.get("token_id") == _PAIRS[exchange][2]
              and row.get("outcome") == "YES", "NOT_FROZEN_EXACT_SAME_YES")
        _need(row.get("tournament_id") ==
              "bda92870-621e-47b0-bc3c-3602c5c26f55", "TOURNAMENT_MISMATCH")
        _need(_HEX64.fullmatch(_text(row.get("raw_source_sha256"), "RAW_SHA_MISSING"))
              is not None, "RAW_HASH_INVALID")
        clock_ms = _integer(row.get("clock_uncertainty_ms"), "CLOCK_UNCERTAINTY_UNKNOWN")
        _need(clock_ms <= 1_000, "CLOCK_UNCERTAINTY_TOO_LARGE")
        observed = _utc(row.get("received_at"))
        issuer = _utc(row.get("publisher_at"))
        _need(issuer <= observed + timedelta(milliseconds=clock_ms),
              "SOURCE_FUTURE_OF_OBSERVER")
        _book(row)
        if kind == "SIG_FULL_BOOK":
            _integer(row.get("connection_epoch"), "SIG_EPOCH_MISSING", low=1)
            seq = _integer(row.get("native_asof_sequence"), "NATIVE_ASOF_MISSING", low=1)
            _need(_utc(row.get("native_asof_at")) == issuer,
                  "SIG_NATIVE_ASOF_PUBLISHER_MISMATCH")
            revision = _integer(row.get("delivery_revision"), "REVISION_MISSING", low=1)
            prev_rev = _integer(row.get("previous_revision"), "PREVIOUS_REV_MISSING")
            _integer(row.get("source_sequence_from"), "SRC_SEQ_MISSING")
            _need(_integer(row.get("source_sequence_through"), "SRC_SEQ_MISSING")
                  >= row["source_sequence_from"], "SRC_SEQ_INVERTED")
            _need(row.get("resync_required") is False and row.get("has_full_book") is True,
                  "BOOK_DIRTY_REST_OR_RESYNC_NOT_NATIVE_FULL_BOOK")
            prior = self._sig.get(exchange)
            if prior:
                _need(row["connection_epoch"] == prior["connection_epoch"],
                      "SIG_RECONNECT_NEEDS_NEW_VERIFIED_RUN")
                _need(prev_rev == prior["delivery_revision"]
                      and revision > prev_rev, "SIG_REVISION_GAP_OR_DUPLICATE")
                _need(seq > prior["native_asof_sequence"], "SIG_ASOF_NONMONOTONIC")
                _need(observed > _utc(prior["received_at"]), "SIG_RECEIVE_NONMONOTONIC")
            self._sig[exchange] = dict(row)
        else:
            epoch = _integer(row.get("connection_epoch"), "PM_EPOCH_MISSING", low=1)
            seed = _integer(row.get("seed_epoch"), "PM_SEED_EPOCH_MISSING", low=1)
            ordinal = _integer(row.get("local_receive_ordinal"), "PM_LOCAL_ORDINAL_MISSING")
            _need(_integer(row.get("local_drop_total"), "PM_DROP_COUNTER_MISSING") == 0,
                  "PM_DROPPED_WS_MESSAGES")
            prior_pm = self._pm.get(exchange)
            if kind == "PM_FULL_SEED":
                _need(row.get("seed_complete") is True and ordinal == 0
                      and seed == epoch and prior_pm is None, "INCOMPLETE_OR_REUSED_PM_SEED")
            else:
                _need(prior_pm is not None, "PM_DELTA_BEFORE_SEED")
                _need(row.get("seed_complete") is True
                      and epoch == prior_pm["connection_epoch"]
                      and seed == prior_pm["seed_epoch"]
                      and ordinal == prior_pm["local_receive_ordinal"] + 1,
                      "PM_WS_RECONNECT_DROP_OR_NONCONTIGUOUS_LOCAL_ORDINAL")
                _need(observed > _utc(prior_pm["received_at"]),
                      "PM_RECEIVE_NONMONOTONIC")
                _need(row["bids"] != prior_pm["bids"]
                      or row["asks"] != prior_pm["asks"], "PM_NOT_A_PRICE_BOOK_INNOVATION")
            self._pm[exchange] = dict(row)

    def ingest(self, row: Mapping[str, Any], original_payload: bytes) -> str:
        """Consume a supplied OFFLINE full-state record; never connect to a source."""
        _need(self.enabled, "DEFAULT_DISABLED_OR_ABORTED")
        try:
            _need(isinstance(original_payload, bytes)
                  and 0 < len(original_payload) <= _MAX_RAW, "RAW_BYTES_LIMIT")
            _need(set(row) == {
                "kind", "source", "producer_git_sha", "producer_run_id",
                "exchange_id", "market_id", "condition_id", "token_id",
                "tournament_id", "outcome", "raw_source_sha256",
                "clock_uncertainty_ms", "received_at", "publisher_at",
                "bids", "asks",
            }.union(
                {
                    "connection_epoch", "native_asof_sequence", "native_asof_at",
                    "delivery_revision", "previous_revision", "source_sequence_from",
                    "source_sequence_through", "resync_required", "has_full_book",
                } if row.get("kind") == "SIG_FULL_BOOK" else {
                    "connection_epoch", "seed_epoch", "local_receive_ordinal",
                    "local_drop_total", "seed_complete",
                }
            ), "UNEXPECTED_OR_MISSING_FIELDS_PII_DEFENSE")
            digest = hashlib.sha256(original_payload).hexdigest()
            _need(row.get("raw_source_sha256") == digest, "ORIGINAL_BYTES_HASH_MISMATCH")
            received = _utc(row.get("received_at"))
            if self._start is None:
                self._start = received
            _need(received >= self._start
                  and received - self._start <= _MAX_AGE, "24H_WINDOW_EXCEEDED")
            _need(self._last_at is None or received >= self._last_at,
                  "GLOBAL_RECEIVE_CLOCK_BACKWARDS")
            self._validate(row)
            # Persist only allowlisted numeric metadata, never raw input bytes.
            canonical = json.dumps(dict(row), sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode("ascii")
            chain = hashlib.sha256(bytes.fromhex(self._chain) + canonical).hexdigest()
            line = json.dumps({"record": row, "chain_sha256": chain},
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
            _need(len(self._rows) < _MAX_RECORDS
                  and self._size + len(line) <= _MAX_OUTPUT,
                  "BOUNDED_OUTPUT_EXCEEDED")
            self._rows.append(line)
            self._size += len(line)
            self._chain = chain
            self._last_at = received
            return chain
        except (OfflineDenied, TypeError, ValueError, OverflowError) as exc:
            self.abort()
            if isinstance(exc, OfflineDenied):
                raise
            raise OfflineDenied("INVALID_OFFLINE_RECORD") from exc

    def journal_bytes(self) -> bytes:
        """Caller-owned bounded bytes, no filesystem, uploader or SDK."""
        return b"".join(self._rows)

    def screen(self, exchange_id: str, decision_at: str, whole_shares: int) -> Screen:
        """Descriptive signed PM-bid/ask vs SIG full-depth; NEVER executable alpha."""
        _need(self.enabled, "DEFAULT_DISABLED_OR_ABORTED")
        _need(exchange_id in _PAIRS and whole_shares in _WHOLE_LOTS,
              "INVALID_EXCHANGE_OR_WHOLE_LOT")
        decision = _utc(decision_at)
        sig = self._sig.get(exchange_id)
        pm = self._pm.get(exchange_id)
        if sig is None or pm is None or pm["kind"] != "PM_BOOK_CHANGE":
            return Screen("MISSING_NATIVE_SIG_OR_PM_INNOVATION", exchange_id, whole_shares,
                          None, None)
        sig_at = _utc(sig["received_at"])
        pm_at = _utc(pm["received_at"])
        if (not pm_at < sig_at <= decision
            or decision - pm_at > _MAX_STALENESS
            or decision - sig_at > _MAX_STALENESS):
            return Screen("NONCAUSAL_OR_STALE", exchange_id, whole_shares, None, None)
        sig_bid, sig_ask = _book(sig)
        pm_bid, pm_ask = _book(pm)
        yes_ask = _vwap(sig_ask, whole_shares)
        yes_bid = _vwap(sig_bid, whole_shares)
        if yes_ask is None or yes_bid is None:
            return Screen("WHOLE_DEPTH_INSUFFICIENT", exchange_id, whole_shares,
                          None, None)
        yes_gap = pm_bid[0][0] - yes_ask
        no_gap = yes_bid - pm_ask[0][0]  # NO ask = 1 - same-market YES bid.
        return Screen("REFERENCE_ONLY_2S_5S_NOT_RESTING_PROOF", exchange_id,
                      whole_shares, str(yes_gap), str(no_gap))
