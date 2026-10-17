"""Synthetic-only Phase 0 historical maker Parquet ZSTD proof.

No external/archive/host/Kaggle/network reads or writes. Fixture identities
are deliberately invented hashes, never customer/venue identifiers.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

CAP = 256 * 1024 * 1024  # Self-imposed planning threshold; NOT a Kaggle limit.
START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 10, 7, tzinfo=UTC)
D = pa.decimal128(24, 8)
T = pa.timestamp("us", tz="UTC")
CLOCKS = [pa.field("issuer_at", T), pa.field("received_at", T),
          pa.field("exported_at", T)]
SPECS: dict[str, pa.Schema] = {
    "venue_fills": pa.schema([
        pa.field("fill_key", pa.string()), pa.field("order_key", pa.string()),
        pa.field("fill_qty", D), pa.field("fill_price", D),
        pa.field("actual_fee", D), *CLOCKS,
    ]),
    "order_lifecycle": pa.schema([
        pa.field("event_key", pa.string()), pa.field("order_key", pa.string()),
        pa.field("status", pa.string()), pa.field("requested_qty", D),
        pa.field("cumulative_fill_qty", D), pa.field("censored", pa.bool_()), *CLOCKS,
    ]),
    "inventory_cashflows": pa.schema([
        pa.field("cashflow_key", pa.string()), pa.field("order_key", pa.string()),
        pa.field("cash_delta", D), pa.field("actual_exit_cash", D), *CLOCKS,
    ]),
    "quote_markouts": pa.schema([
        pa.field("quote_key", pa.string()), pa.field("order_key", pa.string()),
        pa.field("quote_price", D), pa.field("observed_markout", D), *CLOCKS,
    ]),
}
ID_FIELDS = {"order_key", "fill_key", "event_key", "cashflow_key", "quote_key"}
FORBIDDEN = re.compile(r"password|secret|api.?key|private.?key|username|account|email|salt",
                       re.IGNORECASE)


def _key(value: str) -> str:
    return hashlib.sha256(("INVENTED-FIXTURE-" + value).encode()).hexdigest()


def _clock(value: object) -> datetime:
    if not isinstance(value, str) or not (
        value.endswith("Z") or value.endswith("+00:00")
    ):
        raise ValueError("CLOCK_MUST_BE_EXPLICIT_UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("INVALID_UTC_CLOCK") from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("MIXED_CLOCK_DOMAIN")
    return parsed


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("DECIMAL_STRING_REQUIRED_NO_FLOAT")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("INVALID_DECIMAL") from exc
    exponent = result.as_tuple().exponent
    if not result.is_finite() or not isinstance(exponent, int) or exponent < -8:
        raise ValueError("NONFINITE_OR_PRECISION_LOSS")
    return result


def _safe_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("TEXT_REQUIRED")
    if (
        value[0] in "=+-@" or value.startswith(("/", "\\", "~/"))
        or "../" in value or "\\.." in value or "://" in value
        or "@" in value or FORBIDDEN.search(value)
    ):
        raise ValueError("PII_SECRET_FORMULA_OR_PATH")
    return value


def _validate(table: str, rows: list[dict[str, object]]) -> pa.Table:
    if table not in SPECS or not rows:
        raise ValueError("UNKNOWN_OR_EMPTY_TABLE")
    schema = SPECS[table]
    fields = {field.name for field in schema}
    validated: list[dict[str, object]] = []
    for row in rows:
        if set(row) != fields or any(FORBIDDEN.search(k) for k in row):
            raise ValueError("WRONG_SCHEMA_OR_PRIVATE_FIELD")
        item: dict[str, object] = {}
        for field in schema:
            value = row[field.name]
            if field.name in ("issuer_at", "received_at", "exported_at"):
                item[field.name] = _clock(value)
            elif pa.types.is_decimal(field.type):
                item[field.name] = _decimal(value)
            elif pa.types.is_boolean(field.type):
                if type(value) is not bool:
                    raise ValueError("BOOLEAN_REQUIRED")
                item[field.name] = value
            elif field.name in ID_FIELDS:
                if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise ValueError("MISSING_ANONYMOUS_JOIN_KEY")
                item[field.name] = value
            else:
                item[field.name] = _safe_text(value)
        issuer = item["issuer_at"]
        received = item["received_at"]
        exported = item["exported_at"]
        assert isinstance(issuer, datetime)
        assert isinstance(received, datetime)
        assert isinstance(exported, datetime)
        if not (START <= issuer < END and issuer <= received <= exported):
            raise ValueError("OUT_OF_COHORT_OR_NONCAUSAL_CLOCK")
        validated.append(item)
    validated.sort(key=lambda r: (r["issuer_at"], next(
        str(r[f]) for f in sorted(ID_FIELDS & set(r))
    )))
    return pa.Table.from_pylist(validated, schema=schema)


def _row(clock: str, order: str, **kwargs: object) -> dict[str, object]:
    received = clock.replace("Z", ".100000Z")
    return {"order_key": _key(order), **kwargs, "issuer_at": clock,
            "received_at": received, "exported_at": "2026-10-17T10:00:00Z"}


def _fixtures() -> dict[str, list[dict[str, object]]]:
    return {
        "venue_fills": [
            _row("2026-10-02T10:00:01Z", "A", fill_key=_key("fill-1"),
                 fill_qty="1.25000000", fill_price="0.51000000", actual_fee="0.02000000"),
            _row("2026-10-02T10:00:02Z", "A", fill_key=_key("fill-2"),
                 fill_qty="0.75000000", fill_price="0.52000000", actual_fee=None),
        ],
        "order_lifecycle": [
            _row("2026-10-02T10:00:00Z", "A", event_key=_key("ack-A"),
                 status="ACK", requested_qty="2.00000000",
                 cumulative_fill_qty="0.00000000", censored=False),
            _row("2026-10-02T10:00:01Z", "A", event_key=_key("partial-A"),
                 status="PARTIAL_FILL", requested_qty="2.00000000",
                 cumulative_fill_qty="1.25000000", censored=False),
            _row("2026-10-02T10:00:03Z", "B", event_key=_key("no-fill-B"),
                 status="CANCELLED_ZERO_FILL", requested_qty="3.00000000",
                 cumulative_fill_qty="0.00000000", censored=False),
            _row("2026-10-02T10:00:04Z", "C", event_key=_key("censored-C"),
                 status="CENSORED_NO_ACK", requested_qty="4.00000000",
                 cumulative_fill_qty=None, censored=True),
        ],
        "inventory_cashflows": [
            _row("2026-10-02T10:00:01Z", "A", cashflow_key=_key("cash-1"),
                 cash_delta="-0.63750000", actual_exit_cash=None),
            _row("2026-10-02T10:00:02Z", "A", cashflow_key=_key("cash-2"),
                 cash_delta="-0.39000000", actual_exit_cash=None),
        ],
        "quote_markouts": [
            _row("2026-10-02T10:00:01Z", "A", quote_key=_key("quote-1"),
                 quote_price="0.51000000", observed_markout=None),
        ],
    }


def _canonical_input(rows: list[dict[str, object]]) -> bytes:
    return (json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode()


def _parquet_bytes(table: pa.Table, path: Path) -> bytes:
    pq.write_table(table, path, compression="zstd", compression_level=3,
                   use_dictionary=False, write_statistics=False, row_group_size=1024)
    return path.read_bytes()


def _compile_synthetic(tmp_path: Path) -> dict[str, Any]:
    fixtures = _fixtures()
    reports: dict[str, dict[str, Any]] = {}
    total_size = 0
    joined: set[str] = {str(row["order_key"]) for row in fixtures["order_lifecycle"]}
    for name in ("venue_fills", "order_lifecycle", "inventory_cashflows", "quote_markouts"):
        rows = fixtures[name]
        table = _validate(name, rows)
        source = _canonical_input(rows)
        artifact = tmp_path / (name + ".parquet")
        first = _parquet_bytes(table, artifact)
        second = _parquet_bytes(table, tmp_path / (name + "-repeat.parquet"))
        assert first == second, "PARQUET_NONDETERMINISTIC"
        roundtrip = pq.read_table(artifact)
        assert roundtrip.schema == SPECS[name]
        assert roundtrip.to_pylist() == table.to_pylist()
        assert roundtrip.num_rows == len(rows)
        assert roundtrip["issuer_at"].type == T
        total_size += len(first)
        if name == "order_lifecycle":
            joined = set(table["order_key"].to_pylist())
            assert len(joined) == 3  # ACK/PARTIAL, ZERO_FILL and CENSORED kept.
            assert {"PARTIAL_FILL", "CANCELLED_ZERO_FILL", "CENSORED_NO_ACK"} <= set(
                table["status"].to_pylist()
            )
            assert table["cumulative_fill_qty"].null_count == 1
        else:
            assert set(table["order_key"].to_pylist()) <= joined
        reports[name] = {
            "source_kind": "SYNTHETIC_ONLY",
            "input_sha256": hashlib.sha256(source).hexdigest(),
            "input_bytes": len(source), "parquet_sha256": hashlib.sha256(first).hexdigest(),
            "parquet_bytes": len(first), "rows": roundtrip.num_rows,
            "schema_sha256": hashlib.sha256(str(table.schema).encode()).hexdigest(),
        }
    if total_size > CAP:
        raise ValueError("SELF_IMPOSED_256MIB_LIMIT")
    return {"schema_version": "historical-maker-phase0-synthetic-v1",
            "source_rights_verified": False, "source_original_bytes_available": False,
            "private_kaggle_acl_verified": False, "upload_authorised": False,
            "start_inclusive": START.isoformat(), "end_exclusive": END.isoformat(),
            "planning_cap_bytes": CAP, "synthetic_parquet_bytes": total_size,
            "tables": reports}


def _transfer_preflight(proof: dict[str, Any], *, rights: bool, acl: bool,
                        owner_release: bool, source_original: bool) -> None:
    if (not rights or not acl or not owner_release or not source_original
            or not proof["source_original_bytes_available"]):
        raise PermissionError("ORIGINAL_RIGHTS_ARCHIVE_AND_PRIVATE_ACL_NOT_VERIFIED")
    if proof["synthetic_parquet_bytes"] > CAP:
        raise ValueError("SELF_IMPOSED_256MIB_LIMIT")
    raise PermissionError("TEST_ONLY_NEVER_UPLOAD")


def test_local_four_table_zstd_hashes_and_denominators(tmp_path: Path) -> None:
    manifest = _compile_synthetic(tmp_path)
    assert list(manifest["tables"]) == [
        "venue_fills", "order_lifecycle", "inventory_cashflows", "quote_markouts"
    ]
    assert [v["rows"] for v in manifest["tables"].values()] == [2, 4, 2, 1]
    for proof in manifest["tables"].values():
        assert len(proof["input_sha256"]) == len(proof["parquet_sha256"]) == 64
        assert proof["parquet_bytes"] > 0
    assert not manifest["upload_authorised"]


def test_missing_fee_and_exit_never_imputed_as_zero() -> None:
    data = _fixtures()
    assert data["venue_fills"][1]["actual_fee"] is None
    assert data["inventory_cashflows"][0]["actual_exit_cash"] is None
    assert data["order_lifecycle"][-1]["cumulative_fill_qty"] is None
    assert data["order_lifecycle"][-2]["cumulative_fill_qty"] == "0.00000000"


@pytest.mark.parametrize("bad", [
    "2026-10-02T10:00:01", "2026-10-02T11:00:01+01:00",
    "2026-10-07T00:00:00Z", "2026-09-30T23:59:59Z",
])
def test_invalid_utc_or_out_of_cohort_clock_rejected(bad: str) -> None:
    rows = _fixtures()["venue_fills"]
    rows[0]["issuer_at"] = bad
    with pytest.raises(ValueError):
        _validate("venue_fills", rows)


@pytest.mark.parametrize("bad", [0.51, "NaN", "Infinity", "0.123456789"])
def test_float_nonfinite_or_precision_loss_rejected(bad: object) -> None:
    rows = _fixtures()["venue_fills"]
    rows[0]["fill_price"] = bad
    with pytest.raises(ValueError):
        _validate("venue_fills", rows)


@pytest.mark.parametrize(("field", "value"), [
    ("username", "fake-name"), ("api_key", "synthetic-string"),
    ("email", "nobody@example.com"), ("unapproved_path", "/home/user/private"),
])
def test_private_or_unauthorised_extra_fields_rejected(field: str, value: str) -> None:
    rows = _fixtures()["venue_fills"]
    rows[0][field] = value
    with pytest.raises(ValueError, match="WRONG_SCHEMA_OR_PRIVATE_FIELD"):
        _validate("venue_fills", rows)


@pytest.mark.parametrize("bad", ["=HYPERLINK(1)", "+SUM(2)", "@formula", "../secrets"])
def test_formula_or_path_strings_rejected(bad: str) -> None:
    rows = _fixtures()["order_lifecycle"]
    rows[0]["status"] = bad
    with pytest.raises(ValueError, match="PII_SECRET_FORMULA_OR_PATH"):
        _validate("order_lifecycle", rows)


def test_private_transfer_denied_even_for_perfect_synthetic_manifest(tmp_path: Path) -> None:
    manifest = _compile_synthetic(tmp_path)
    for opts in [(False, False, False, False), (True, True, True, True)]:
        with pytest.raises(PermissionError):
            _transfer_preflight(manifest, rights=opts[0], acl=opts[1],
                                owner_release=opts[2], source_original=opts[3])


def test_no_network_even_in_synthetic_parquet_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("NO_NETWORK_IN_PHASE0")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    assert _compile_synthetic(tmp_path)["upload_authorised"] is False
