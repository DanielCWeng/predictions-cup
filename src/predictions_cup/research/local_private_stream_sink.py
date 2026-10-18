
"""Default-OFF, non-network, bounded local-only provenance sink (research only).

Source records and original bytes are caller supplied and NEVER issuer-authenticated
by this sink. No live integration, timer, venue client, collector or uploader.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Callable, Mapping

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_UTC = re.compile(r".*(?:Z|\+00:00)\Z")
_EXCHANGES = frozenset({"912", "913", "959", "960"})
_MARKETS = {"912": "223", "913": "224", "959": "270", "960": "271"}
_KINDS = frozenset({"SIG_BOOK", "SIG_TOPIC_REV", "PM_SEED", "PM_CHANGE"})
_BASE = frozenset({"kind", "exchange_id", "market_id", "producer_git_sha", "producer_run_id",
                   "publisher_utc", "received_utc", "clock_uncertainty_ms", "connection_epoch"})
_SIG = frozenset({"asof_sequence", "native_asof_utc", "revision", "previous_revision",
                  "source_sequence_from", "source_sequence_through"})
_PM = frozenset({"seed_epoch", "local_ordinal", "drop_total", "seed_complete"})
_BOOK = frozenset({"bids", "asks"})
# Frozen EXACT/SAME PM YES-token identities from the reviewed #318 contract.
# A caller-created token string is NOT an alternative source of mapping truth.
BRIDGE_PM_TOKENS = {
    "912": "94033987553817825990709512567031751048276730850154145976615728913102874968691",
    "913": "70720420928389492978842914190430712299346582376255109200751838945374631181994",
    "959": "7589880081658059374445882095611024032084645048848237404312287942463647821549",
    "960": "2142948285506077695096498341598672159464223074468931276310105532825810210121",
}
_BRIDGE_COMMON = frozenset({"schema", "venue", "kind", "producer_sha",
                            "issuer_authenticated", "source_admitted", "paper_admitted"})
_BRIDGE_SIG = frozenset({"market_id", "exchange_id", "tournament_id", "revision",
                        "previous_revision", "source_sequence_from", "source_sequence_through",
                        "connection_epoch", "received_at", "clock_uncertainty_ms",
                        "raw_frame_sha256", "wire_bytes_verified", "gap_or_unanchored",
                        "full_books"})
_BRIDGE_SEED = frozenset({"token_id", "connection_epoch", "received_at",
                         "ws_local_ordinal", "clock_uncertainty_ms",
                         "raw_response_sha256", "wire_bytes_verified", "full_depth"})
_BRIDGE_WS = frozenset({"connection_epoch", "ws_local_ordinal", "received_at",
                       "clock_uncertainty_ms", "raw_frame_sha256", "wire_bytes_verified",
                       "gap_or_unanchored", "affected_tokens", "uninitialized_tokens",
                       "full_books", "abstain_tokens"})
_BRIDGE_DISCONNECT = frozenset({"connection_epoch", "received_at", "ws_local_ordinal",
                               "full_books", "gap_or_unanchored"})
_MAX_ORIGINAL = 65_536
_MAX_LEVELS = 256
_MAX_RECORD = 64 * 1024
_MAX_RUNTIME = timedelta(hours=24)
_MAX_LAG = timedelta(seconds=5)  # research screen, NOT issuer latency SLA


class SinkDenied(RuntimeError):
    """An unrecoverable rejection; client must not restart/replay a damaged run."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SinkDenied(reason)


def _utc(value: object) -> datetime:
    _require(isinstance(value, str) and bool(_UTC.fullmatch(value)), "NON_UTC_CLOCK")
    assert isinstance(value, str)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SinkDenied("NON_UTC_CLOCK") from exc
    _require(dt.utcoffset() == timedelta(0), "NON_UTC_CLOCK")
    return dt


def _integer(value: object, min_value: int = 0, max_value: int = 2**63 - 1) -> int:
    _require(type(value) is int and min_value <= value <= max_value, "INVALID_COUNTER")
    return int(value)


def _levels(value: object, desc: bool) -> list[list[str]]:
    _require(isinstance(value, list) and 0 < len(value) <= _MAX_LEVELS, "INVALID_LADDER")
    assert isinstance(value, list)
    clean: list[list[str]] = []
    previous: Decimal | None = None
    for level in value:
        _require(isinstance(level, list) and len(level) == 2
                 and all(isinstance(v, str) for v in level), "INVALID_LEVEL")
        try:
            p, q = (Decimal(v) for v in level)
        except InvalidOperation as exc:
            raise SinkDenied("INVALID_LEVEL") from exc
        _require(p.is_finite() and q.is_finite() and Decimal(0) < p < Decimal(1)
                 and q > 0 and len(level[0]) <= 22 and len(level[1]) <= 22,
                 "INVALID_LEVEL")
        if previous is not None:
            _require(p < previous if desc else p > previous, "UNSORTED_DEPTH")
        previous = p
        clean.append([str(p), str(q)])
    return clean


@dataclass(frozen=True)
class LocalRunPermit:
    """Operator-provided limits; an injected external authority must approve.

    This is not an authenticated issuer or rights token by itself.
    """

    run_id: str
    producer_git_sha: str
    expires_utc: str
    purge_utc: str
    max_rows: int
    max_bytes: int
    min_disk_free_bytes: int
    min_free_inodes: int


@dataclass(frozen=True)
class ReferenceEconomicRow:
    exchange_id: str
    whole_shares: int
    verdict: str
    sig_yes_bid_vwap: str | None
    sig_yes_ask_vwap: str | None
    pm_yes_bid_vwap: str | None
    pm_yes_ask_vwap: str | None
    yes_reference_gap: str | None
    no_reference_gap: str | None
    arrival_250ms_observed: None = None
    arrival_1s_observed: None = None
    arrival_2s_observed: None = None
    native_order_attempts: None = None
    actual_fills: None = None
    paid_fees: None = None
    terminal_cash: None = None
    real_net: None = None
    source_admitted: bool = False
    paper_admitted: bool = False


def _whole_vwap(levels: list[list[str]], n: int) -> Decimal | None:
    remain = n
    total = Decimal(0)
    for px, qty in levels:
        lots = int(Decimal(qty).to_integral_value(rounding=ROUND_DOWN))
        fill = min(lots, remain)
        total += Decimal(px) * fill
        remain -= fill
        if remain == 0:
            return total / n
    return None


class PrivateStreamingSink:
    """Synchronous O(1)-queue append-only file sink; no source/network access.

    Requires a caller-provided non-existing private directory, future external
    permit verifier and explicit constructor call. Never starts automatically.
    """

    def __init__(
        self, root: Path, permit: LocalRunPermit | None = None,
        verify: Callable[[LocalRunPermit], bool] | None = None,
    ) -> None:
        self.enabled = False
        self.aborted = False
        self._file: Any = None
        self._rows = 0
        self._bytes = 0
        self._chain = "0" * 64
        self._records_sha = hashlib.sha256()
        self._last_payload_hash: dict[tuple[str, str], str] = {}
        self._last_receipt: datetime | None = None
        self._sig: dict[str, dict[str, Any]] = {}
        self._pm: dict[str, dict[str, Any]] = {}
        self._start_monotonic = 0.0
        self._abort_reason: str | None = None
        # ABI mode cannot be mixed in one immutable manifest. Bridge has no
        # issuer-verified wire bytes or authenticated native source metadata.
        self._abi_mode: str | None = None
        self._bridge_sig: dict[str, tuple[int, int, int, datetime]] = {}
        self._bridge_last_native: dict[str, tuple[int, datetime, int]] = {}
        self._bridge_pm_epoch: int | None = None
        self._bridge_pm_ordinal = 0
        self._bridge_pm_seeded: set[str] = set()
        self._bridge_unanchored = 0
        self._bridge_nonbook = 0
        self._bridge_unchanged_ws = 0
        self.root = root
        self.permit = permit
        if permit is None or verify is None:
            return
        _require(verify(permit) is True, "PERMIT_NOT_INDEPENDENTLY_VERIFIED")
        _require(bool(_ID.fullmatch(permit.run_id))
                 and bool(_HEX40.fullmatch(permit.producer_git_sha)),
                 "BAD_PRODUCER_OR_RUN")
        now = datetime.now(UTC)
        _require(now < _utc(permit.expires_utc) <= now + _MAX_RUNTIME,
                 "PERMIT_EXPIRY_INVALID")
        _require(_utc(permit.expires_utc) < _utc(permit.purge_utc)
                 <= _utc(permit.expires_utc) + timedelta(hours=72),
                 "PURGE_DEADLINE_INVALID")
        _integer(permit.max_rows, 1, 2_000_000)
        _integer(permit.max_bytes, 1024, 2 * 1024**3)
        _integer(permit.min_disk_free_bytes, 1)
        _integer(permit.min_free_inodes, 1)
        _require(root.is_absolute() and not root.exists(), "OUTPUT_MUST_BE_NEW_ABSOLUTE")
        _require(root.parent.is_dir() and not root.parent.is_symlink(), "UNSAFE_PARENT")
        _require(root.parent.stat().st_mode & 0o022 == 0, "PARENT_WRITABLE_BY_OTHERS")
        old_mask = os.umask(0o077)
        try:
            root.mkdir(mode=0o700)
            fd = os.open(root / "records.jsonl",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self._file = os.fdopen(fd, "wb", buffering=0)
        finally:
            os.umask(old_mask)
        self._start_monotonic = time.monotonic()
        self.enabled = True

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def written_bytes(self) -> int:
        return self._bytes

    def _active(self) -> None:
        _require(self.enabled and not self.aborted and self.permit is not None,
                 "DEFAULT_OFF_OR_ABORTED")
        _require(time.monotonic() - self._start_monotonic < 86400,
                 "24H_RUNTIME_EXPIRED")
        _require(datetime.now(UTC) < _utc(self.permit.expires_utc), "PERMIT_EXPIRED")

    def _validate(self, item: Mapping[str, Any]) -> dict[str, Any]:
        kind = item.get("kind")
        _require(kind in _KINDS, "UNKNOWN_EVENT_KIND")
        expected = _BASE | (_SIG if kind in ("SIG_BOOK", "SIG_TOPIC_REV") else _PM)
        if kind in ("SIG_BOOK", "PM_SEED", "PM_CHANGE"):
            expected = expected | _BOOK
        _require(set(item.keys()) == expected, "UNEXPECTED_FIELD_OR_PII")
        ex = item["exchange_id"]
        _require(type(ex) is str and ex in _EXCHANGES and item["market_id"] == _MARKETS[ex],
                 "NOT_ALLOWLISTED_EXACT_MARKET")
        assert self.permit is not None
        _require(item["producer_git_sha"] == self.permit.producer_git_sha
                 and item["producer_run_id"] == self.permit.run_id,
                 "PRODUCER_NOT_PINNED")
        epoch = _integer(item["connection_epoch"], 1)
        uncertainty = _integer(item["clock_uncertainty_ms"], 0, 1000)
        publisher, received = _utc(item["publisher_utc"]), _utc(item["received_utc"])
        u = timedelta(milliseconds=uncertainty)
        _require(publisher - u <= received and received - (publisher - u) <= _MAX_LAG,
                 "CLOCK_SOURCE_STALE_OR_FUTURE")
        _require(self._last_receipt is None or received >= self._last_receipt,
                 "GLOBAL_CLOCK_REGRESSION")
        clean = dict(item)
        if kind in ("SIG_BOOK", "PM_SEED", "PM_CHANGE"):
            bids, asks = _levels(item["bids"], True), _levels(item["asks"], False)
            _require(Decimal(bids[0][0]) < Decimal(asks[0][0]), "CROSSED_BOOK")
            clean["bids"], clean["asks"] = bids, asks
        if kind in ("SIG_BOOK", "SIG_TOPIC_REV"):
            seq = _integer(item["asof_sequence"], 1)
            _require(_utc(item["native_asof_utc"]) == publisher, "NATIVE_ASOF_MISMATCH")
            rev = _integer(item["revision"], 1)
            previous_rev = _integer(item["previous_revision"])
            start = _integer(item["source_sequence_from"])
            end = _integer(item["source_sequence_through"])
            _require(end >= start and rev > previous_rev, "SIG_RANGE_INVALID")
            prior = self._sig.get(ex)
            if prior is not None:
                prior_source, prior_u = _utc(prior["publisher_utc"]), timedelta(
                    milliseconds=prior["clock_uncertainty_ms"])
                _require(epoch == prior["connection_epoch"] and previous_rev == prior["revision"]
                         and seq > prior["asof_sequence"]
                         and start == prior["source_sequence_through"] + 1
                         and prior_source + prior_u < publisher - u,
                         "SIG_GAP_RECONNECT_OR_CLOCK_REGRESSION")
            self._sig[ex] = clean
        else:
            seed = _integer(item["seed_epoch"], 1)
            ordinal = _integer(item["local_ordinal"])
            _require(_integer(item["drop_total"]) == 0 and item["seed_complete"] is True,
                     "PM_GAP_OR_UNSEEDED")
            prior_pm = self._pm.get(ex)
            if kind == "PM_SEED":
                _require(prior_pm is None and ordinal == 0 and seed == epoch,
                         "PM_SEED_REPLAY_OR_MISSING")
            else:
                _require(prior_pm is not None, "PM_CHANGE_BEFORE_SEED")
                assert prior_pm is not None
                prior_source, prior_u = _utc(prior_pm["publisher_utc"]), timedelta(
                    milliseconds=prior_pm["clock_uncertainty_ms"])
                _require(epoch == prior_pm["connection_epoch"]
                         and seed == prior_pm["seed_epoch"]
                         and ordinal == prior_pm["local_ordinal"] + 1
                         and prior_source + prior_u < publisher - u,
                         "PM_GAP_RECONNECT_OR_CLOCK_REGRESSION")
            self._pm[ex] = clean
        return clean

    def reference_economic_rows(self, exchange_id: str) -> tuple[ReferenceEconomicRow, ...]:
        """Never produces arrival proof, verified fees or real net economics."""
        self._active()
        _require(exchange_id in _EXCHANGES, "NOT_ALLOWLISTED_EXACT_MARKET")
        sig = self._sig.get(exchange_id)
        pm = self._pm.get(exchange_id)
        eligible = False
        if sig and pm and sig["kind"] == "SIG_BOOK" and pm["kind"] == "PM_CHANGE":
            sig_src, pm_src = _utc(sig["publisher_utc"]), _utc(pm["publisher_utc"])
            sig_rx, pm_rx = _utc(sig["received_utc"]), _utc(pm["received_utc"])
            sig_u = timedelta(milliseconds=sig["clock_uncertainty_ms"])
            pm_u = timedelta(milliseconds=pm["clock_uncertainty_ms"])
            eligible = (pm_src + pm_u < sig_src - sig_u
                        and pm_rx + pm_u < sig_rx - sig_u
                        and sig_rx - pm_rx <= _MAX_LAG
                        and sig_src - pm_src <= _MAX_LAG)
        result: list[ReferenceEconomicRow] = []
        for n in (1, 5, 10, 20):
            values: tuple[Decimal | None, ...] = (None, None, None, None)
            if eligible and sig and pm:
                values = (_whole_vwap(sig["bids"], n), _whole_vwap(sig["asks"], n),
                          _whole_vwap(pm["bids"], n), _whole_vwap(pm["asks"], n))
            if all(v is not None for v in values):
                sig_bid, sig_ask, pm_bid, pm_ask = values
                assert sig_bid is not None and sig_ask is not None
                assert pm_bid is not None and pm_ask is not None
                result.append(ReferenceEconomicRow(exchange_id, n,
                    "REFERENCE_ONLY_NOT_RESTING_PROOF", str(sig_bid), str(sig_ask),
                    str(pm_bid), str(pm_ask), str(pm_bid - sig_ask),
                    str(sig_bid - pm_ask)))
            else:
                result.append(ReferenceEconomicRow(exchange_id, n,
                    "SOURCE_OR_WHOLE_DEPTH_UNKNOWN", None, None, None, None,
                    None, None))
        return tuple(result)

    def _resources(self, next_size: int) -> None:
        assert self.permit is not None
        _require(self._rows < self.permit.max_rows
                 and self._bytes + next_size <= self.permit.max_bytes,
                 "ROW_OR_BYTE_CAP")
        stat = os.statvfs(self.root)
        _require(stat.f_bavail * stat.f_frsize - next_size >= self.permit.min_disk_free_bytes
                 and stat.f_favail >= self.permit.min_free_inodes,
                 "DISK_OR_INODE_GUARD")

    def append(self, row: Mapping[str, Any], original_payload: bytes) -> str:
        """Synchronous bounded durable append. No buffering queue or dropped sample."""
        try:
            self._active()
            _require(self._abi_mode in (None, "legacy"), "MIXED_ABI_DENIED")
            _require(type(original_payload) is bytes
                     and 0 < len(original_payload) <= _MAX_ORIGINAL, "RAW_BYTES_LIMIT")
            clean = self._validate(row)
            digest = hashlib.sha256(original_payload).hexdigest()
            key = (str(clean["kind"]), str(clean["exchange_id"]))
            _require(self._last_payload_hash.get(key) != digest, "DUPLICATE_SOURCE_PAYLOAD")
            clean["original_sha256"] = digest
            encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode("ascii")
            chain = hashlib.sha256(bytes.fromhex(self._chain) + encoded).hexdigest()
            line = json.dumps({"row": clean, "chain_sha256": chain},
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
            _require(len(line) <= _MAX_RECORD, "ROW_OVERSIZE")
            self._resources(len(line))
            _require(self._file.write(line) == len(line), "PARTIAL_FILE_WRITE")
            os.fsync(self._file.fileno())
            self._rows += 1
            self._bytes += len(line)
            self._chain = chain
            self._records_sha.update(line)
            self._last_payload_hash[key] = digest
            self._last_receipt = _utc(clean["received_utc"])
            self._abi_mode = "legacy"
            return chain
        except (SinkDenied, OSError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, SinkDenied) else "IO_OR_INVALID_RECORD"
            self.abort(reason)
            raise SinkDenied(reason) from exc

    @staticmethod
    def _bridge_digest(value: object) -> None:
        # Digests from PR319 are unverified claims (NULL when wire bytes absent).
        # This sink never regenerates a plausible issuer hash from decoded JSON.
        _require(value is None or (type(value) is str and
                 bool(_HEX64.fullmatch(value))), "UNVERIFIED_RAW_DIGEST_FORMAT")

    @staticmethod
    def _bridge_full_book(value: object) -> dict[str, object]:
        _require(type(value) is dict and set(value) ==
                 {"bids", "asks", "complete_source_claim"} and
                 value["complete_source_claim"] is True, "BAD_FULL_BOOK_SHAPE")
        bids = _levels(value["bids"], True)
        asks = _levels(value["asks"], False)
        _require(Decimal(bids[0][0]) < Decimal(asks[0][0]), "CROSSED_BOOK")
        return {"bids": bids, "asks": asks, "complete_source_claim": True}

    @staticmethod
    def _bridge_token_list(value: object) -> list[str]:
        _require(type(value) is list and len(value) <= 4 and
                 all(type(token) is str and token in BRIDGE_PM_TOKENS.values()
                     for token in value) and len(value) == len(set(value)),
                 "NONFROZEN_TOKEN_OR_DUPLICATE")
        return list(value)

    def _validate_bridge(
        self, row: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, object], str | None]:
        """Fail-closed adapter for exact PR319 emit(dict), with O(4) state.

        State change is returned and applied only AFTER the synchronous fsync.
        Neither native publisher authenticity nor PM global WS sequencing exists.
        """
        _require(type(row) is dict, "BRIDGE_DICT_REQUIRED")
        venue, kind = row.get("venue"), row.get("kind")
        names = ("SIG", "BATCH"), ("PM", "REST_SEED_COMPLETE"), ("PM", "WS_EVENT"), (
            "PM", "DISCONNECT")
        _require((venue, kind) in names and row.get("schema") ==
                 "alpha2-source-bridge-v1", "BRIDGE_SCHEMA_MISMATCH")
        fields = (_BRIDGE_SIG if venue == "SIG" else
                  _BRIDGE_SEED if kind == "REST_SEED_COMPLETE" else
                  _BRIDGE_WS if kind == "WS_EVENT" else _BRIDGE_DISCONNECT)
        _require(set(row) == _BRIDGE_COMMON | fields, "UNEXPECTED_FIELD_OR_PII")
        assert self.permit is not None
        _require(row["producer_sha"] == self.permit.producer_git_sha and
                 type(row["producer_sha"]) is str, "PRODUCER_NOT_PINNED")
        _require(row["issuer_authenticated"] is False and
                 row["source_admitted"] is False and row["paper_admitted"] is False,
                 "UNSUPPORTED_TRUST_PROMOTION")
        received = _utc(row["received_at"])
        _require(self._last_receipt is None or received >= self._last_receipt,
                 "GLOBAL_CLOCK_REGRESSION")
        epoch = _integer(row["connection_epoch"], 1)
        clean = dict(row)
        terminal: str | None = None
        if venue == "SIG":
            exchange = row["exchange_id"]
            _require(type(exchange) is str and exchange in _EXCHANGES and
                     row["market_id"] == _MARKETS[exchange] and
                     row["tournament_id"] ==
                     "bda92870-621e-47b0-bc3c-3602c5c26f55",
                     "NOT_ALLOWLISTED_EXACT_MARKET")
            uncertainty = _integer(row["clock_uncertainty_ms"], 0, 1000)
            _require(row["wire_bytes_verified"] is False, "WIRE_VERIFICATION_UNPROVEN")
            self._bridge_digest(row["raw_frame_sha256"])
            revision = _integer(row["revision"], 0)
            previous_revision = _integer(row["previous_revision"], 0)
            first = _integer(row["source_sequence_from"], 0)
            through = _integer(row["source_sequence_through"], 0)
            _require(first <= through and revision > previous_revision,
                     "SIG_RANGE_INVALID")
            prior = self._bridge_sig.get(exchange)
            gap = row["gap_or_unanchored"]
            _require(type(gap) is bool, "GAP_MARKER_INVALID")
            if prior is None:
                # PR319 labels first delivery unanchored by design. Retain it,
                # but source_admitted remains FALSE and no arrival is proven.
                _require(gap is True, "SIG_FIRST_DELIVERY_NOT_ANCHORED")
            else:
                old_epoch, old_rev, old_end, old_receive = prior
                _require(not gap and epoch == old_epoch and
                         previous_revision == old_rev and revision > old_rev and
                         first >= old_end and received >= old_receive,
                         "SIG_SOURCE_GAP_OR_RECONNECT")
            books = row["full_books"]
            _require(type(books) is list and len(books) <= 16, "SIG_BOOK_BATCH_CAP")
            normalized: list[dict[str, object]] = []
            last = self._bridge_last_native.get(exchange)
            for book in books:
                _require(type(book) is dict and set(book) ==
                         {"asof_at", "asof_sequence", "full_depth"},
                         "SIG_NATIVE_SHAPE_INVALID")
                native_at = _utc(book["asof_at"])
                native_seq = _integer(book["asof_sequence"], 0)
                u = timedelta(milliseconds=uncertainty)
                _require(native_at - u <= received and
                         received - (native_at - u) <= _MAX_LAG,
                         "SIG_NATIVE_SOURCE_CLOCK_STALE_OR_FUTURE")
                if last is not None:
                    last_seq, last_native_at, last_uncertainty = last
                    _require(native_seq > last_seq and
                             last_native_at + timedelta(milliseconds=last_uncertainty)
                             < native_at - u, "SIG_NATIVE_ASOF_REGRESSION")
                normalized.append({"asof_at": book["asof_at"],
                                   "asof_sequence": native_seq,
                                   "full_depth": self._bridge_full_book(book["full_depth"])})
                last = native_seq, native_at, uncertainty
            clean["full_books"] = normalized
            update: tuple[str, object] = ("SIG", (str(exchange),
                         (epoch, revision, through, received), last))
        else:
            # PM source bridge exports receive-ordered snapshots, NOT issuer
            # publisher_at or a venue-global WS sequence. NEVER create them.
            if kind == "REST_SEED_COMPLETE":
                _integer(row["clock_uncertainty_ms"], 0, 1000)
                _require(row["wire_bytes_verified"] is False,
                         "WIRE_VERIFICATION_UNPROVEN")
                self._bridge_digest(row["raw_response_sha256"])
                token = row["token_id"]
                _require(type(token) is str and token in BRIDGE_PM_TOKENS.values(),
                         "NONFROZEN_TOKEN_OR_DUPLICATE")
                _require(_integer(row["ws_local_ordinal"]) == 0 and
                         (self._bridge_pm_epoch is None or
                          self._bridge_pm_epoch == epoch) and
                         self._bridge_pm_ordinal == 0 and
                         token not in self._bridge_pm_seeded,
                         "PM_SEED_EPOCH_OR_ORDINAL_INVALID")
                clean["full_depth"] = self._bridge_full_book(row["full_depth"])
                update = "PM_SEED", (epoch, str(token))
            elif kind == "WS_EVENT":
                _integer(row["clock_uncertainty_ms"], 0, 1000)
                _require(row["wire_bytes_verified"] is False,
                         "WIRE_VERIFICATION_UNPROVEN")
                self._bridge_digest(row["raw_frame_sha256"])
                ordinal = _integer(row["ws_local_ordinal"], 1)
                _require(self._bridge_pm_epoch == epoch and
                         ordinal == self._bridge_pm_ordinal + 1 and
                         row["gap_or_unanchored"] is False,
                         "PM_WS_ORDINAL_GAP_OR_RECONNECT")
                affected = self._bridge_token_list(row["affected_tokens"])
                uninitialized = self._bridge_token_list(row["uninitialized_tokens"])
                abstain = self._bridge_token_list(row["abstain_tokens"])
                books = row["full_books"]
                _require(type(books) is dict and len(books) <= 4 and
                         all(type(t) is str and t in BRIDGE_PM_TOKENS.values()
                             and t in affected for t in books),
                         "PM_SOURCE_BOOK_TOKEN_INVALID")
                _require(not uninitialized and not abstain and
                         all(t in self._bridge_pm_seeded for t in affected) and
                         all(t in books for t in affected),
                         "PM_UNSEEDED_OR_MISSING_BOOK")
                clean["affected_tokens"] = affected
                clean["uninitialized_tokens"] = uninitialized
                clean["abstain_tokens"] = abstain
                clean["full_books"] = {
                    token: self._bridge_full_book(book) for token, book in books.items()
                }
                update = "PM_WS", ordinal
            else:
                _require(row["gap_or_unanchored"] is True and
                         row["full_books"] == {} and
                         self._bridge_pm_epoch == epoch and
                         _integer(row["ws_local_ordinal"]) == self._bridge_pm_ordinal,
                         "PM_DISCONNECT_SHAPE_INVALID")
                update = "PM_DISCONNECT", epoch
                terminal = "PM_DISCONNECT_ABORT"
        return clean, update, terminal

    def append_bridge(self, record: dict[str, object]) -> None:
        """PR319 SourceBridge(emit=sink.append_bridge); no original wire required.

        Persist *every* allowed event including no-book SIG and unchanged WS.
        Never label local canonical SHA as issuer SHA. Errors propagate to
        SourceBridge._emit and irrevocably poison its event stream.
        """
        try:
            self._active()
            _require(self._abi_mode in (None, "bridge"), "MIXED_ABI_DENIED")
            clean, update, terminal = self._validate_bridge(record)
            # Computed locally, explicitly NOT original wire provenance.
            encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode("ascii")
            clean["local_canonical_sha256"] = hashlib.sha256(encoded).hexdigest()
            encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode("ascii")
            chain = hashlib.sha256(bytes.fromhex(self._chain) + encoded).hexdigest()
            line = json.dumps({"row": clean, "chain_sha256": chain},
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
            _require(len(line) <= _MAX_RECORD, "ROW_OVERSIZE")
            self._resources(len(line))
            _require(self._file.write(line) == len(line), "PARTIAL_FILE_WRITE")
            os.fsync(self._file.fileno())
            self._rows += 1
            self._bytes += len(line)
            self._chain = chain
            self._records_sha.update(line)
            self._last_receipt = _utc(clean["received_at"])
            self._abi_mode = "bridge"
            tag, state = update
            if tag == "SIG":
                ex, sig_state, last_native = state
                self._bridge_sig[ex] = sig_state
                if last_native is not None:
                    self._bridge_last_native[ex] = last_native
                if clean["gap_or_unanchored"]:
                    self._bridge_unanchored += 1
                if not clean["full_books"]:
                    self._bridge_nonbook += 1
            elif tag == "PM_SEED":
                epoch, token = state
                self._bridge_pm_epoch = epoch
                self._bridge_pm_seeded.add(token)
            elif tag == "PM_WS":
                self._bridge_pm_ordinal = state
                if not clean["affected_tokens"]:
                    self._bridge_unchanged_ws += 1
            elif tag == "PM_DISCONNECT":
                self.abort(terminal or "PM_DISCONNECT_ABORT")
                raise SinkDenied("PM_DISCONNECT_ABORT")
        except (SinkDenied, OSError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, SinkDenied) else "BRIDGE_IO_OR_INVALID_RECORD"
            self.abort(reason)
            raise SinkDenied(reason) from exc

    def abort(self, reason: str = "OPERATOR_STOP") -> None:
        self.aborted = True
        self.enabled = False
        self._abort_reason = reason
        if self._file is not None and not self._file.closed:
            self._file.close()
        self._seal()

    def close(self) -> None:
        self.enabled = False
        if self._file is not None and not self._file.closed:
            self._file.close()
        self._seal()

    def _seal(self) -> None:
        if self.permit is None or not self.root.exists() or (self.root / "receipt.json").exists():
            return
        summary = {
            "schema": "local-private-stream-v1", "run_id": self.permit.run_id,
            "producer_git_sha": self.permit.producer_git_sha,
            "rows": self._rows, "bytes": self._bytes,
            "rejected": int(self._abort_reason is not None),
            "gap_abort": int(self._abort_reason is not None and "GAP" in self._abort_reason),
            "last_chain_sha256": self._chain,
            "records_sha256": self._records_sha.hexdigest(),
            "abort_reason": self._abort_reason, "purge_utc": self.permit.purge_utc,
            "source_admitted": False, "paper_admitted": False,
            "real_net": None, "egress": False,
            "abi_mode": self._abi_mode,
            "bridge_unanchored": self._bridge_unanchored,
            "bridge_nonbook": self._bridge_nonbook,
            "bridge_unchanged_ws": self._bridge_unchanged_ws,
            "issuer_authenticated": False,
        }
        data = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        old_mask = os.umask(0o077)
        try:
            fd = os.open(self.root / "receipt.json",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        finally:
            os.umask(old_mask)

    def purge_after_deadline(self, now: datetime | None = None) -> bool:
        """Explicit caller-invoked purge, NOT a scheduler or proven retention enforcement."""
        _require(self.permit is not None and not self.enabled, "CLOSE_BEFORE_PURGE")
        _require((now or datetime.now(UTC)) >= _utc(self.permit.purge_utc),
                 "PURGE_NOT_DUE")
        shutil.rmtree(self.root)
        return True


def verify_sealed_archive(root: Path) -> bool:
    """Synthetic/offline read verification; bounded line-by-line integrity, not issuer proof."""
    receipt_path = root / "receipt.json"
    source_path = root / "records.jsonl"
    _require(receipt_path.is_file() and source_path.is_file(), "UNSEALED_OR_MISSING")
    _require(not receipt_path.is_symlink() and not source_path.is_symlink(),
             "SYMLINK_ARCHIVE")
    _require(receipt_path.stat().st_size <= 4096, "INVALID_RECEIPT_SIZE")
    try:
        meta = json.loads(receipt_path.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise SinkDenied("INVALID_RECEIPT_JSON") from exc
    _require(meta.get("schema") == "local-private-stream-v1", "WRONG_ARCHIVE_SCHEMA")
    chain = "0" * 64
    digest = hashlib.sha256()
    count = 0
    size = 0
    with source_path.open("rb") as file:
        for line in file:
            _require(0 < len(line) <= _MAX_RECORD and line.endswith(b"\n"),
                     "TRUNCATED_OR_OVERSIZED_RECORD")
            try:
                object_ = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise SinkDenied("INVALID_ARCHIVE_LINE") from exc
            _require(type(object_) is dict and set(object_) == {"row", "chain_sha256"},
                     "INVALID_ARCHIVE_LINE")
            canonical = json.dumps(object_["row"], sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=True,
                                   allow_nan=False).encode("ascii")
            chain = hashlib.sha256(bytes.fromhex(chain) + canonical).hexdigest()
            _require(object_["chain_sha256"] == chain, "CORRUPT_CHAIN")
            digest.update(line)
            count += 1
            size += len(line)
    _require(count == meta.get("rows") and size == meta.get("bytes")
             and chain == meta.get("last_chain_sha256")
             and digest.hexdigest() == meta.get("records_sha256"),
             "TRUNCATED_OR_CORRUPT_ARCHIVE")
    return True
