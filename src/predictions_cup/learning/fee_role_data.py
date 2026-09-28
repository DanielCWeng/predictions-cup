"""DATA-001 + DATA-002 role-enrichment primitives for EXPERIMENT-005A."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from predictions_cup.learning.fee_role_structure import (
    RoleClass,
    RoleEvidence,
    canonical_yes_pressure_sign,
    classify_role,
)

FEE_FILES = {
    "COL_2026": "fees_COL_2026.parquet",
    "HUN_2026": "fees_HUN_2026.parquet",
    "PER_2026": "fees_PER_2026.parquet",
    "CAN_2025": "fees_CAN_2025.parquet",
    "US_2024": "fees_US_2024.parquet",
}

ROLE_JOIN_LEFT = ("transaction_hash", "log_index", "token_id", "maker_address")
ROLE_JOIN_RIGHT = ("tx_hash", "log_index", "token_id", "participant_address")

ROLE_EVIDENCE_COLUMNS = (
    "family",
    "event_id",
    "market_id",
    "condition_id",
    "token_id",
    "timestamp",
    "tx_hash",
    "log_index",
    "participant_address",
    "counterparty_address",
    "order_is_match_taker_order",
    "exchange_if_taker_is_exchange",
    "participant_side",
    "outcome_side",
    "price",
    "size_shares",
    "value_usd",
    "fee_evidence",
    "fee_charged_usd_equiv",
    "fee_refunded_usd_equiv",
    "fee_net_usd_equiv",
    "n_charge_legs",
    "n_refund_legs",
    "fee_leg_refs",
    "attribution_rules",
    "flag_multiple_fee_records",
    "flag_attribution_ambiguous",
    "flag_fee_on_fee_disabled_market",
    "flag_fee_on_non_taker_order",
    "fees_enabled",
    "fee_taker_only",
    "fee_rate",
    "fee_rebate_rate",
    "fee_category",
    "custody_scan",
)


@dataclass(frozen=True)
class JoinAudit:
    left_rows: int
    right_rows: int
    joined_rows: int
    matched_rows: int
    unmatched_rows: int
    duplicate_right_keys: int
    outcome_disagreements: int
    price_disagreements: int
    participant_infrastructure_rows: int

    def as_dict(self) -> dict[str, int]:
        return {
            "left_rows": self.left_rows,
            "right_rows": self.right_rows,
            "joined_rows": self.joined_rows,
            "matched_rows": self.matched_rows,
            "unmatched_rows": self.unmatched_rows,
            "duplicate_right_keys": self.duplicate_right_keys,
            "outcome_disagreements": self.outcome_disagreements,
            "price_disagreements": self.price_disagreements,
            "participant_infrastructure_rows": self.participant_infrastructure_rows,
        }


def utc_seconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.astimezone(UTC).timestamp())


def load_infrastructure_addresses(path: Path) -> frozenset[str]:
    payload = json.loads(path.read_text())
    raw = payload.get("infra_addresses")
    if not isinstance(raw, list):
        raise ValueError("infra registry lacks infra_addresses list")
    return frozenset(str(address).lower() for address in raw)


def load_fee_family(
    root: Path,
    family: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    columns: Iterable[str] = ROLE_EVIDENCE_COLUMNS,
) -> pa.Table:
    try:
        filename = FEE_FILES[family]
    except KeyError as exc:
        raise ValueError(f"unsupported fee family {family!r}") from exc
    path = root / "matched_fills" / filename
    filters: list[tuple[str, str, int]] = []
    if start is not None:
        filters.append(("timestamp", ">=", utc_seconds(start)))
    if end is not None:
        filters.append(("timestamp", "<", utc_seconds(end)))
    return pq.read_table(path, columns=list(columns), filters=filters or None)


def _parquet_files(root: Path) -> list[str]:
    return [str(path) for path in sorted(root.rglob("*.parquet"))]


def load_event_fills(
    corpus_root: Path,
    event: str,
    *,
    start: datetime,
    end: datetime,
    columns: Iterable[str] | None = None,
) -> pa.Table:
    files = _parquet_files(corpus_root / event / "fills")
    if not files:
        raise FileNotFoundError(f"no DATA-001 fills for {event}")
    dataset = pads.dataset(files, format="parquet")
    expr = (pads.field("observed_at") >= pa.scalar(start, pa.timestamp("us", tz="UTC"))) & (
        pads.field("observed_at") < pa.scalar(end, pa.timestamp("us", tz="UTC"))
    )
    return dataset.to_table(columns=None if columns is None else list(columns), filter=expr)


def _right_key_duplicates(table: pa.Table) -> int:
    grouped = table.group_by(list(ROLE_JOIN_RIGHT)).aggregate([("tx_hash", "count")])
    return int(pc.sum(pc.greater(grouped["tx_hash_count"], 1)).as_py() or 0)


def _rename_role_columns(table: pa.Table) -> pa.Table:
    key_set = set(ROLE_JOIN_RIGHT)
    names = [
        name if name in key_set else f"role_{name}"
        for name in table.column_names
    ]
    return table.rename_columns(names)


def _normalize_join_key_types(table: pa.Table, keys: tuple[str, ...]) -> pa.Table:
    out = table
    for name in keys:
        index = out.schema.get_field_index(name)
        if index < 0:
            continue
        target = pa.int64() if name == "log_index" else pa.string()
        if out.schema.field(index).type != target:
            out = out.set_column(index, name, pc.cast(out[name], target))
    return out


def enrich_fills_with_roles(
    fills: pa.Table,
    fee_rows: pa.Table,
    *,
    infrastructure_addresses: frozenset[str],
) -> tuple[pa.Table, JoinAudit]:
    """Join DATA-002 evidence onto exact DATA-001 signed-order-owner fill rows."""

    missing_left = [name for name in ROLE_JOIN_LEFT if name not in fills.column_names]
    missing_right = [name for name in ROLE_JOIN_RIGHT if name not in fee_rows.column_names]
    if missing_left or missing_right:
        raise ValueError(f"join columns missing left={missing_left} right={missing_right}")

    fills = _normalize_join_key_types(fills, ROLE_JOIN_LEFT)
    fee_rows = _normalize_join_key_types(fee_rows, ROLE_JOIN_RIGHT)
    duplicate_right = _right_key_duplicates(fee_rows)
    if duplicate_right:
        raise ValueError(f"DATA-002 role join key is non-unique: {duplicate_right} duplicate keys")

    right = _rename_role_columns(fee_rows)
    joined = fills.join(
        right,
        keys=list(ROLE_JOIN_LEFT),
        right_keys=list(ROLE_JOIN_RIGHT),
        join_type="left outer",
    )

    evidence_col = "role_fee_evidence"
    matched = pc.is_valid(joined[evidence_col])
    matched_rows = int(pc.sum(matched).as_py() or 0)

    outcome_disagreements = 0
    if "outcome" in joined.column_names and "role_outcome_side" in joined.column_names:
        compare_mask = pc.and_(matched, pc.is_valid(joined["outcome"]))
        mismatch = pc.and_(
            compare_mask,
            pc.not_equal(pc.utf8_upper(joined["outcome"]), joined["role_outcome_side"]),
        )
        outcome_disagreements = int(pc.sum(mismatch).as_py() or 0)

    price_disagreements = 0
    if "price" in joined.column_names and "role_price" in joined.column_names:
        left_price = pc.cast(joined["price"], pa.float64())
        right_price = pc.cast(joined["role_price"], pa.float64())
        mismatch = pc.and_(
            matched,
            pc.greater(pc.abs(pc.subtract(left_price, right_price)), 1e-9),
        )
        price_disagreements = int(pc.sum(mismatch).as_py() or 0)

    maker = joined["maker_address"].to_pylist()
    infra_rows = sum(
        value is not None and str(value).lower() in infrastructure_addresses
        for value in maker
    )

    audit = JoinAudit(
        left_rows=fills.num_rows,
        right_rows=fee_rows.num_rows,
        joined_rows=joined.num_rows,
        matched_rows=matched_rows,
        unmatched_rows=fills.num_rows - matched_rows,
        duplicate_right_keys=duplicate_right,
        outcome_disagreements=outcome_disagreements,
        price_disagreements=price_disagreements,
        participant_infrastructure_rows=infra_rows,
    )
    return joined, audit


def append_role_classification(table: pa.Table) -> pa.Table:
    required = (
        "role_timestamp",
        "role_order_is_match_taker_order",
        "role_fee_evidence",
        "role_fee_net_usd_equiv",
        "role_flag_attribution_ambiguous",
        "role_flag_multiple_fee_records",
        "role_flag_fee_on_fee_disabled_market",
        "role_flag_fee_on_non_taker_order",
    )
    missing = [name for name in required if name not in table.column_names]
    if missing:
        raise ValueError(f"role evidence missing: {missing}")

    values: list[str] = []
    sources: list[str] = []
    reasons: list[str] = []
    rows = table.select(required).to_pylist()
    for row in rows:
        if row["role_timestamp"] is None:
            values.append(RoleClass.UNKNOWN.value)
            sources.append("NONE")
            reasons.append("no DATA-002 join")
            continue
        decision = classify_role(
            RoleEvidence(
                timestamp=datetime.fromtimestamp(row["role_timestamp"], tz=UTC),
                order_is_match_taker_order=row["role_order_is_match_taker_order"],
                fee_evidence=row["role_fee_evidence"],
                fee_net_usd_equiv=(
                    None
                    if row["role_fee_net_usd_equiv"] is None
                    else Decimal(str(row["role_fee_net_usd_equiv"]))
                ),
                flag_attribution_ambiguous=bool(row["role_flag_attribution_ambiguous"]),
                flag_multiple_fee_records=bool(row["role_flag_multiple_fee_records"]),
                flag_fee_on_fee_disabled_market=bool(
                    row["role_flag_fee_on_fee_disabled_market"]
                ),
                flag_fee_on_non_taker_order=bool(row["role_flag_fee_on_non_taker_order"]),
            )
        )
        values.append(decision.role_class.value)
        sources.append(decision.evidence_source.value)
        reasons.append(decision.reason)

    return (
        table.append_column("role_class", pa.array(values, pa.string()))
        .append_column("role_evidence_source", pa.array(sources, pa.string()))
        .append_column("role_reason", pa.array(reasons, pa.string()))
    )


def append_signed_yes_pressure(
    table: pa.Table,
    *,
    amount_column: str = "value_usd",
    high_confidence_only: bool = True,
) -> pa.Table:
    required = ("role_class", "role_outcome_side", "role_participant_side", amount_column)
    missing = [name for name in required if name not in table.column_names]
    if missing:
        raise ValueError(f"signed-flow columns missing: {missing}")

    role = table["role_class"].to_pylist()
    outcome = table["role_outcome_side"].to_pylist()
    side = table["role_participant_side"].to_pylist()
    amounts = pc.cast(table[amount_column], pa.float64()).to_pylist()

    signed: list[float | None] = []
    for role_class, outcome_side, participant_side, amount in zip(
        role, outcome, side, amounts, strict=True
    ):
        allowed = role_class == RoleClass.TAKER_HIGH_CONFIDENCE.value
        if not high_confidence_only:
            allowed = allowed or role_class == RoleClass.TAKER_SUPPORTIVE.value
        if not allowed or outcome_side is None or participant_side is None or amount is None:
            signed.append(None)
            continue
        signed.append(
            float(amount) * canonical_yes_pressure_sign(str(outcome_side), str(participant_side))
        )
    return table.append_column("signed_yes_pressure", pa.array(signed, pa.float64()))


def role_counts(table: pa.Table) -> dict[str, int]:
    if "role_class" not in table.column_names:
        raise ValueError("role_class column missing")
    counts = pc.value_counts(table["role_class"]).to_pylist()
    return {str(row["values"]): int(row["counts"]) for row in counts}
