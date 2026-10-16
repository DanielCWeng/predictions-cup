"""Input-only, default-deny schema gate for operator-exported OFFLINE paired depth.

No network clients, venue calls, collector/maker imports, fill estimates or trust promotion.
A structurally valid synthetic tape is SCHEMA_ONLY/PAPER_NOT_ADMITTED: external
issuer identity, use rights, native clock authenticity and real availability
are never attested merely because a self-described JSON field is present.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

_MAX_INPUT_BYTES = 8 * 1024 * 1024
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class SchemaDenied(ValueError):
    """Precise, non-promotable source/interval rejection reason."""


@dataclass(frozen=True)
class GateResult:
    verdict: str
    schema_valid_intervals: int
    rejections: tuple[tuple[str, str], ...]
    source_admitted: bool = False
    paper_admitted: bool = False
    network_requests: int = 0
    external_issuer_authenticated: bool = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SchemaDenied(reason)


def _text(value: Any, reason: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), reason)
    return cast(str, value)


def _sha(value: Any, reason: str, *, git: bool = False) -> str:
    result = _text(value, reason)
    pattern = _HEX_GIT_SHA if git else _HEX_SHA256
    _require(pattern.fullmatch(result) is not None, reason)
    return result


def _object(value: Any, reason: str) -> dict[str, Any]:
    _require(isinstance(value, dict) and all(isinstance(k, str) for k in value), reason)
    return cast(dict[str, Any], value)


def _integer(value: Any, reason: str, *, minimum: int = 1) -> int:
    _require(type(value) is int and value >= minimum, reason)
    return cast(int, value)


def _utc(value: Any) -> datetime:
    _require(
        isinstance(value, str) and (value.endswith("Z") or value.endswith("+00:00")),
        "MIXED_OR_MISSING_UTC_CLOCK",
    )
    try:
        stamp = datetime.fromisoformat(cast(str, value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaDenied("MIXED_OR_MISSING_UTC_CLOCK") from exc
    _require(stamp.utcoffset() == timedelta(0), "MIXED_OR_MISSING_UTC_CLOCK")
    return stamp


def _read(path: Path) -> bytes:
    _require(path.is_file() and not path.is_symlink(), "INPUT_NOT_REGULAR_IMMUTABLE_FILE")
    data = path.read_bytes()
    _require(0 < len(data) <= _MAX_INPUT_BYTES, "INPUT_EMPTY_OR_OVERSIZE")
    return data


def _json_object(data: bytes) -> dict[str, Any]:
    try:
        return _object(json.loads(data.decode("utf-8")), "INVALID_JSON_OBJECT")
    except (UnicodeError, ValueError) as exc:
        raise SchemaDenied("INVALID_JSON_OBJECT") from exc


def _jsonl(data: bytes) -> list[dict[str, Any]]:
    _require(data.endswith(b"\n"), "PARTIAL_ARCHIVE")
    lines = data.splitlines()
    _require(len(lines) > 0 and all(bool(line.strip()) for line in lines), "PARTIAL_ARCHIVE")
    return [_json_object(line) for line in lines]


def _levels(row: dict[str, Any]) -> None:
    _require(row.get("full_depth") is True, "MISSING_FULL_DEPTH")
    sides: list[list[Decimal]] = []
    for side in ("bids", "asks"):
        raw = row.get(side)
        _require(isinstance(raw, list) and bool(raw), "MISSING_FULL_DEPTH")
        prices: list[Decimal] = []
        for entry in cast(list[Any], raw):
            item = _object(entry, "INVALID_BOOK_LEVEL")
            price_raw, size_raw = item.get("price"), item.get("size")
            _require(type(price_raw) is str and type(size_raw) is str, "INVALID_BOOK_LEVEL")
            try:
                price, size = Decimal(price_raw), Decimal(size_raw)
            except InvalidOperation as exc:
                raise SchemaDenied("INVALID_BOOK_LEVEL") from exc
            _require(
                price.is_finite() and size.is_finite()
                and Decimal(0) < price < Decimal(1) and size > 0,
                "INVALID_BOOK_LEVEL",
            )
            prices.append(price)
        _require(
            all(a > b for a, b in zip(prices, prices[1:]))
            if side == "bids"
            else all(a < b for a, b in zip(prices, prices[1:])),
            "UNSORTED_OR_DUPLICATE_DEPTH",
        )
        sides.append(prices)
    _require(sides[0][0] < sides[1][0], "CROSSED_BOOK")


def _row_clock(row: dict[str, Any], manifest: dict[str, Any]) -> tuple[datetime, datetime]:
    _require(row.get("clock_domain") == "UTC", "MIXED_OR_MISSING_UTC_CLOCK")
    source = _utc(row.get("source_at"))
    received = _utc(row.get("received_at"))
    decision = _utc(row.get("decision_at"))
    uncertainty = _integer(row.get("clock_uncertainty_ms"), "MISSING_CLOCK_UNCERTAINTY", minimum=0)
    budget = _integer(manifest.get("max_clock_uncertainty_ms"), "BAD_CLOCK_BUDGET", minimum=0)
    age_limit = _integer(manifest.get("max_age_ms"), "BAD_AGE_LIMIT")
    _require(uncertainty <= budget, "CLOCK_UNCERTAINTY_BREACH")
    slack = timedelta(milliseconds=uncertainty)
    _require(source <= received + slack and received <= decision, "NONCAUSAL_CLOCK_ORDER")
    _require(decision >= source and (decision - source) + slack <= timedelta(milliseconds=age_limit),
             "STALE_OR_UNAVAILABLE_BOOK")
    return received, decision


def _manifest(manifest: dict[str, Any]) -> None:
    _require(manifest.get("schema") == "offline-paired-depth-schema-v1", "WRONG_MANIFEST_SCHEMA")
    _require(manifest.get("archive_complete") is True, "PARTIAL_ARCHIVE")
    _require(manifest.get("source_type") == "OPERATOR_EXPORTED_OFFLINE_BYTES", "BAD_SOURCE_TYPE")
    _text(manifest.get("issuer"), "MISSING_ISSUER")
    _text(manifest.get("permission_ref"), "MISSING_PERMITTED_USE_EVIDENCE")
    _require(manifest.get("permitted_use") == "OFFLINE_SCHEMA_CHECK_ONLY",
             "MISSING_PERMITTED_USE_EVIDENCE")
    for venue in ("sig", "pm"):
        producer = _object(_object(manifest.get("producers"), "MISSING_PRODUCER").get(venue),
                           "MISSING_PRODUCER")
        _text(producer.get("issuer"), "MISSING_PRODUCER_ISSUER")
        _sha(producer.get("producer_git_sha"), "MISSING_PRODUCER_SHA", git=True)
        _text(producer.get("run_id"), "MISSING_PRODUCER_RUN")
        _require(producer.get("source_type") == f"{venue.upper()}_FULL_DEPTH_EXPORT",
                 "BAD_PRODUCER_TYPE")
    digests = _object(manifest.get("file_sha256"), "MISSING_INPUT_DIGEST")
    for file_name in ("mapping", "sig", "pm"):
        _sha(digests.get(file_name), "MISSING_INPUT_DIGEST")
    _integer(manifest.get("interval_count"), "PARTIAL_ARCHIVE")
    _integer(manifest.get("max_age_ms"), "BAD_AGE_LIMIT")
    _integer(manifest.get("max_clock_uncertainty_ms"), "BAD_CLOCK_BUDGET", minimum=0)


def _mapping(value: dict[str, Any]) -> dict[str, Any]:
    _require(value.get("mapping_class") == "EXACT", "INVALID_MAPPING")
    _text(value.get("mapping_version"), "INVALID_MAPPING")
    for name in ("sig_exchange_id", "sig_market_id", "pm_condition_id", "pm_token_id"):
        _text(value.get(name), "INVALID_MAPPING")
    sig_side = value.get("sig_outcome")
    pm_side = value.get("pm_outcome")
    direction = value.get("mapping_direction")
    _require(sig_side in ("YES", "NO") and pm_side in ("YES", "NO"), "INVALID_MAPPING")
    _require(direction in ("SAME", "COMPLEMENT"), "INVALID_MAPPING")
    _require((sig_side == pm_side) == (direction == "SAME"), "MAPPING_ORIENTATION_INVALID")
    return value


def _interval(sig: dict[str, Any], pm: dict[str, Any], mapping: dict[str, Any],
              manifest: dict[str, Any]) -> None:
    _require(sig.get("interval_id") == pm.get("interval_id"), "INTERVAL_ID_MISMATCH")
    for row, producer in ((sig, "sig"), (pm, "pm")):
        expected_run = _object(manifest["producers"], "MISSING_PRODUCER")[producer]["run_id"]
        _require(row.get("run_id") == expected_run, "PRODUCER_RUN_MISMATCH")
        _levels(row)
    for field, expected in (
        ("exchange_id", mapping["sig_exchange_id"]),
        ("market_id", mapping["sig_market_id"]),
        ("outcome", mapping["sig_outcome"]),
    ):
        _require(sig.get(field) == expected, "SIG_MAPPING_OR_SIDE_MISMATCH")
    for field, expected in (
        ("token_id", mapping["pm_token_id"]),
        ("condition_id", mapping["pm_condition_id"]),
        ("outcome", mapping["pm_outcome"]),
    ):
        _require(pm.get(field) == expected, "PM_MAPPING_OR_SIDE_MISMATCH")

    _require(sig.get("publisher_clock_attested") is True
             and pm.get("publisher_clock_attested") is True,
             "PUBLISHER_CLOCK_UNATTESTED")
    _integer(sig.get("source_sequence"), "MISSING_SIG_NATIVE_REVISION")
    _integer(pm.get("ws_sequence"), "MISSING_PM_WS_SEQUENCE")
    sig_received, sig_decision = _row_clock(sig, manifest)
    pm_received, pm_decision = _row_clock(pm, manifest)
    _require(sig_decision == pm_decision and sig_decision >= max(sig_received, pm_received),
             "NO_SAME_CLOCK_ASOF_OVERLAP")
    _require(pm.get("seed_status") == "COMPLETE" and pm.get("book_initialized") is True,
             "PM_SEED_NOT_COMPLETE")
    seed = _integer(pm.get("seed_generation"), "PM_SEED_EPOCH_MISMATCH")
    connection_seed = _integer(pm.get("connection_seed_generation"), "PM_SEED_EPOCH_MISMATCH")
    subscription = _integer(pm.get("subscription_epoch"), "PM_SEED_EPOCH_MISMATCH")
    connection_subscription = _integer(pm.get("connection_subscription_epoch"),
                                       "PM_SEED_EPOCH_MISMATCH")
    _require(seed == connection_seed and subscription == connection_subscription,
             "PM_SEED_EPOCH_MISMATCH")
    _require(pm.get("ws_contiguous") is True and pm.get("reconnect_gap") is False,
             "PM_WS_CONTINUITY_LOST")
    _require(_utc(pm.get("last_full_book_at")) <= pm_received, "PM_FULL_SEED_IN_FUTURE")
    _sha(pm.get("book_hash"), "PM_BOOK_HASH_MISSING")


def validate_offline_tape(manifest_path: Path, mapping_path: Path, sig_path: Path, pm_path: Path,
                          *, expected_manifest_sha256: str) -> GateResult:
    """Validate only immutable byte/schema shape; NEVER grant PAPER/research admission."""
    try:
        expected = _sha(expected_manifest_sha256, "MANIFEST_UNPINNED")
        manifest_data = _read(manifest_path)
        _require(hashlib.sha256(manifest_data).hexdigest() == expected, "MANIFEST_HASH_MISMATCH")
        manifest = _json_object(manifest_data)
        _manifest(manifest)
        files = {"mapping": _read(mapping_path), "sig": _read(sig_path), "pm": _read(pm_path)}
        digests = _object(manifest["file_sha256"], "MISSING_INPUT_DIGEST")
        for name, data in files.items():
            _require(hashlib.sha256(data).hexdigest() == digests[name],
                     f"{name.upper()}_INPUT_HASH_MISMATCH")
        mapping = _mapping(_json_object(files["mapping"]))
        sig_rows, pm_rows = _jsonl(files["sig"]), _jsonl(files["pm"])
        _require(len(sig_rows) == len(pm_rows) == manifest["interval_count"], "PARTIAL_ARCHIVE")
        seen: set[str] = set()
        rejection: list[tuple[str, str]] = []
        for sig, pm in zip(sig_rows, pm_rows):
            label = sig.get("interval_id")
            identity = label if isinstance(label, str) and label else "INVALID_INTERVAL"
            try:
                _require(identity not in seen and identity != "INVALID_INTERVAL",
                         "DUPLICATE_OR_MISSING_INTERVAL")
                seen.add(identity)
                _interval(sig, pm, mapping, manifest)
            except SchemaDenied as exc:
                rejection.append((identity, str(exc)))
        if rejection:
            return GateResult("PAIRED_DEPTH_NOT_CERTIFIED", 0, tuple(rejection))
        return GateResult("SCHEMA_ONLY/PAPER_NOT_ADMITTED", len(sig_rows), ())
    except (SchemaDenied, OSError, UnicodeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, SchemaDenied) else "INVALID_OR_INACCESSIBLE_INPUT"
        return GateResult("PAIRED_DEPTH_NOT_CERTIFIED", 0, (("ALL", reason),))
