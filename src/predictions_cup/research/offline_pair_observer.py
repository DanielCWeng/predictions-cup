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
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, cast

# Frozen 2026 EXACT/SAME YES-token crosswalk (public registry, main Git blob
# 14823ac7043a451c587c6b7a0d3b684dbf7bb5d6).
# Other-party YES is NEVER interpreted as this market's NO.
_PAIRS = {
    "912": ("223", "0xe0fbb3e61e61b14f5fef15ffdd0e2a0c6cf4336bb5b15877fe3e3c9fdcc6bc7f",
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
_SAFE_RUN_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_SAFE_RELEASE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SENSITIVE_METADATA = re.compile(
    r"secret|password|token|api.?key|private|credential|bearer|email|@", re.I
)
_MAX_RAW = 65_536
_MAX_OUTPUT = 8_388_608
_MAX_RECORDS = 20_000
_MAX_AGE = timedelta(hours=24)
_MAX_STALENESS = timedelta(seconds=5)
# Research-only age screen, NOT an independently attested venue latency SLA.
_MAX_SOURCE_LAG = _MAX_STALENESS
_WHOLE_LOTS = (1, 5, 10, 20)


class OfflineDenied(ValueError):
    """Falsifiable reason for denying a nonadmitted research-only record."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise OfflineDenied(reason)


def _text(value: object, reason: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()), reason)
    return str(value)


def _safe_id(value: object, pattern: re.Pattern[str], reason: str) -> str:
    ident = _text(value, reason)
    _need(pattern.fullmatch(ident) is not None, reason)
    _need(_SENSITIVE_METADATA.search(ident) is None, "SENSITIVE_METADATA_DENIED")
    return ident


def _integer(value: object, reason: str, *, low: int = 0) -> int:
    _need(type(value) is int and value >= low, reason)
    return cast(int, value)


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
        d = Decimal(cast(str, value))
    except InvalidOperation as exc:
        raise OfflineDenied("INVALID_DECIMAL") from exc
    _need(d.is_finite() and (Decimal(0) < d < Decimal(1) if price else d > 0),
          "INVALID_POSITIVE_LEVEL")
    return d


def _levels(raw: object, side: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, list) or not 0 < len(raw) <= 100:
        raise OfflineDenied("INCOMPLETE_FULL_DEPTH")
    parsed: list[tuple[Decimal, Decimal]] = []
    for level in raw:
        _need(isinstance(level, list) and len(level) == 2, "INVALID_LEVEL")
        parsed.append((_decimal(level[0], price=True), _decimal(level[1])))
    prices = [p for p, _ in parsed]
    _need(all((a > b if side == "bids" else a < b)
              for a, b in pairwise(prices)), "UNSORTED_OR_DUPLICATE_DEPTH")
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
        # Floor at EACH venue level. Pooling fractional displays invents
        # whole-share IOC capacity, even when aggregate decimals sum to n.
        available = qty.to_integral_value(rounding=ROUND_DOWN)
        take = min(remaining, available)
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
        self._release_expires_at: datetime | None = None
        self._runtime_started_at: float | None = None
        if release is not None and independent_verify is not None:
            _need(bool(_HEX64.fullmatch(release.signature_sha256)), "BAD_RELEASE_DIGEST")
            _safe_id(release.release_id, _SAFE_RELEASE_ID, "RELEASE_ID_INVALID")
            expiry = _utc(release.expires_at)
            _need(expiry > datetime.now(UTC), "RELEASE_EXPIRED")
            self._enabled = independent_verify(release) is True
            if self._enabled:
                self._release_expires_at = expiry
                self._runtime_started_at = time.monotonic()
        self._rows: list[bytes] = []
        self._size = 0
        self._start: datetime | None = None
        self._last_at: datetime | None = None
        self._sig: dict[str, dict[str, Any]] = {}
        self._pm: dict[str, dict[str, Any]] = {}
        self._chain = "0" * 64
        self._seen_raw_digests: set[str] = set()
        self.aborted = False

    @property
    def enabled(self) -> bool:
        return self._enabled and not self.aborted

    def abort(self) -> None:
        self.aborted = True
        self._enabled = False

    def _require_active(self) -> None:
        _need(self.enabled, "DEFAULT_DISABLED_OR_ABORTED")
        if (self._release_expires_at is not None
                and datetime.now(UTC) >= self._release_expires_at):
            self.abort()
            raise OfflineDenied("RELEASE_EXPIRED")
        if (self._runtime_started_at is not None
                and time.monotonic() - self._runtime_started_at >= _MAX_AGE.total_seconds()):
            self.abort()
            raise OfflineDenied("OFFLINE_RUNTIME_EXPIRED")

    def _validate(self, row: Mapping[str, Any]) -> dict[str, Any]:
        kind = row.get("kind")
        _need(kind in {"SIG_FULL_BOOK", "PM_FULL_SEED", "PM_BOOK_CHANGE"},
              "UNSUPPORTED_SOURCE_KIND")
        _need(row.get("source") == {
            "SIG_FULL_BOOK": "SIG_REALTIME_NATIVE_FULL_BOOK",
            "PM_FULL_SEED": "PM_REST_SEED_FULL_BOOK",
            "PM_BOOK_CHANGE": "PM_WS_RECONSTRUCTED_FULL_BOOK",
        }[cast(str, kind)], "SOURCE_IS_NOT_NATIVE_FULL_BOOK_OR_SEEDED_PM")
        _need(_HEX40.fullmatch(_text(row.get("producer_git_sha"), "PRODUCER_MISSING"))
              is not None, "PRODUCER_SHA_INVALID")
        _safe_id(row.get("producer_run_id"), _SAFE_RUN_ID, "PRODUCER_RUN_INVALID")
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
        uncertainty = timedelta(milliseconds=clock_ms)
        # Old publisher clocks cannot be refreshed by a newer local receipt.
        # These bounds are conservative research screening, not venue SLA proof.
        _need(
            observed - (issuer - uncertainty) <= _MAX_SOURCE_LAG,
            "SOURCE_PUBLISHER_RECEIPT_STALE_OR_UNATTESTED",
        )
        bids, asks = _book(row)
        # Deep-normalize the ONLY nested metadata into immutable values.
        snapshot = dict(row)
        snapshot["bids"], snapshot["asks"] = bids, asks
        if kind == "SIG_FULL_BOOK":
            _integer(row.get("connection_epoch"), "SIG_EPOCH_MISSING", low=1)
            seq = _integer(row.get("native_asof_sequence"), "NATIVE_ASOF_MISSING", low=1)
            _need(_utc(row.get("native_asof_at")) == issuer,
                  "SIG_NATIVE_ASOF_PUBLISHER_MISMATCH")
            revision = _integer(row.get("delivery_revision"), "REVISION_MISSING", low=1)
            prev_rev = _integer(row.get("previous_revision"), "PREVIOUS_REV_MISSING")
            _need(prev_rev < revision, "INVALID_REVISION_ORDER")
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
                _need(
                    row["source_sequence_from"] == prior["source_sequence_through"] + 1,
                    "SIG_SOURCE_SEQUENCE_GAP_OR_UNVERIFIED_RANGE",
                )
                prior_uncertainty = timedelta(
                    milliseconds=prior["clock_uncertainty_ms"]
                )
                _need(
                    _utc(prior["publisher_at"]) + prior_uncertainty
                    < issuer - uncertainty,
                    "SIG_SOURCE_CLOCK_NONMONOTONIC_OR_AMBIGUOUS",
                )
            self._sig[exchange] = snapshot
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
                if prior_pm is None:
                    raise OfflineDenied("PM_DELTA_BEFORE_SEED")
                _need(row.get("seed_complete") is True
                      and epoch == prior_pm["connection_epoch"]
                      and seed == prior_pm["seed_epoch"]
                      and ordinal == prior_pm["local_receive_ordinal"] + 1,
                      "PM_WS_RECONNECT_DROP_OR_NONCONTIGUOUS_LOCAL_ORDINAL")
                _need(observed > _utc(prior_pm["received_at"]),
                      "PM_RECEIVE_NONMONOTONIC")
                _need(bids != prior_pm["bids"] or asks != prior_pm["asks"],
                      "PM_NOT_A_PRICE_BOOK_INNOVATION")
                prior_uncertainty = timedelta(
                    milliseconds=prior_pm["clock_uncertainty_ms"]
                )
                _need(
                    _utc(prior_pm["publisher_at"]) + prior_uncertainty
                    < issuer - uncertainty,
                    "PM_SOURCE_CLOCK_NONMONOTONIC_OR_AMBIGUOUS",
                )
            self._pm[exchange] = snapshot
        return snapshot

    def ingest(self, row: Mapping[str, Any], original_payload: bytes) -> str:
        """Consume a supplied OFFLINE full-state record; never connect to a source."""
        self._require_active()
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
            _need(digest not in self._seen_raw_digests, "DUPLICATE_ORIGINAL_PAYLOAD")
            received = _utc(row.get("received_at"))
            if self._start is None:
                self._start = received
            _need(received >= self._start
                  and received - self._start < _MAX_AGE, "24H_WINDOW_EXCEEDED")
            _need(self._last_at is None or received >= self._last_at,
                  "GLOBAL_RECEIVE_CLOCK_BACKWARDS")
            snapshot = self._validate(row)
            # Hash and journal the SAME validated books used for later screens.
            serialized = dict(snapshot)
            serialized["bids"] = [[str(p), str(q)] for p, q in snapshot["bids"]]
            serialized["asks"] = [[str(p), str(q)] for p, q in snapshot["asks"]]
            canonical = json.dumps(
                serialized, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii")
            chain = hashlib.sha256(bytes.fromhex(self._chain) + canonical).hexdigest()
            line = json.dumps(
                {"record": serialized, "chain_sha256": chain},
                sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii") + b"\n"
            _need(len(self._rows) < _MAX_RECORDS
                  and self._size + len(line) <= _MAX_OUTPUT,
                  "BOUNDED_OUTPUT_EXCEEDED")
            self._rows.append(line)
            self._seen_raw_digests.add(digest)
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
        self._require_active()
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
        sig_u = timedelta(milliseconds=sig["clock_uncertainty_ms"])
        pm_u = timedelta(milliseconds=pm["clock_uncertainty_ms"])
        sig_source = _utc(sig["publisher_at"])
        pm_source = _utc(pm["publisher_at"])
        # Overlapping source OR receipt uncertainty intervals cannot establish
        # PM shock before SIG arrived book before offline decision.
        if (not (
            pm_at + pm_u < sig_at - sig_u
            and pm_source + pm_u < sig_source - sig_u
            and sig_at + sig_u <= decision
            and sig_source + sig_u <= decision
        )
            or decision - (pm_at - pm_u) > _MAX_STALENESS
            or decision - (sig_at - sig_u) > _MAX_STALENESS
            or pm_at - (pm_source - pm_u) > _MAX_SOURCE_LAG
            or sig_at - (sig_source - sig_u) > _MAX_SOURCE_LAG
            or decision - (pm_source - pm_u) > _MAX_STALENESS
            or decision - (sig_source - sig_u) > _MAX_STALENESS):
            return Screen("NONCAUSAL_OR_STALE", exchange_id, whole_shares, None, None)
        sig_bid, sig_ask = sig["bids"], sig["asks"]
        pm_bid, pm_ask = pm["bids"], pm["asks"]
        yes_ask = _vwap(sig_ask, whole_shares)
        yes_bid = _vwap(sig_bid, whole_shares)
        sized_pm_bid = _vwap(pm_bid, whole_shares)
        sized_pm_ask = _vwap(pm_ask, whole_shares)
        if any(x is None for x in (yes_ask, yes_bid, sized_pm_bid, sized_pm_ask)):
            return Screen("WHOLE_DEPTH_INSUFFICIENT", exchange_id, whole_shares,
                          None, None)
        assert yes_ask is not None and yes_bid is not None
        assert sized_pm_bid is not None and sized_pm_ask is not None
        yes_gap = sized_pm_bid - yes_ask
        no_gap = yes_bid - sized_pm_ask  # Same-market NO ask = 1 - YES bid.
        return Screen("REFERENCE_ONLY_2S_5S_NOT_RESTING_PROOF", exchange_id,
                      whole_shares, str(yes_gap), str(no_gap))
