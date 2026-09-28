from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pytest

from predictions_cup.learning.fee_role_data import (
    append_role_classification,
    append_signed_yes_pressure,
    enrich_fills_with_roles,
    load_infrastructure_addresses,
    role_counts,
)


def _fills() -> pa.Table:
    return pa.table(
        {
            "fill_id": ["a", "b"],
            "token_id": ["yes", "no"],
            "market_id": ["cond", "cond"],
            "event_id": ["event", "event"],
            "outcome": ["Yes", "No"],
            "observed_at": pa.array(
                [
                    datetime(2026, 5, 1, 12, tzinfo=UTC),
                    datetime(2026, 5, 1, 12, tzinfo=UTC),
                ],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "price": ["0.6", "0.4"],
            "value_usd": ["6", "4"],
            "source_side": ["buy", "buy"],
            "maker_address": ["0xowner", "0xmaker"],
            "taker_address": ["0xexchange", "0xowner"],
            "transaction_hash": ["0xtx1", "0xtx2"],
            "log_index": pa.array([1, 2], pa.int64()),
        }
    )


def _fees() -> pa.Table:
    return pa.table(
        {
            "tx_hash": ["0xtx1", "0xtx2"],
            "log_index": pa.array([1, 2], pa.int64()),
            "token_id": ["yes", "no"],
            "participant_address": ["0xowner", "0xmaker"],
            "timestamp": pa.array([1777636800, 1777636800], pa.int64()),
            "order_is_match_taker_order": [True, False],
            "fee_evidence": ["fee_charged", "no_fee_leg_observed"],
            "fee_net_usd_equiv": [0.02, 0.0],
            "flag_attribution_ambiguous": [False, False],
            "flag_multiple_fee_records": [False, False],
            "flag_fee_on_fee_disabled_market": [False, False],
            "flag_fee_on_non_taker_order": [False, False],
            "outcome_side": ["YES", "NO"],
            "participant_side": ["buy", "buy"],
            "price": [0.6, 0.4],
        }
    )


def test_exact_role_join_classification_and_signed_flow() -> None:
    joined, audit = enrich_fills_with_roles(
        _fills(),
        _fees(),
        infrastructure_addresses=frozenset(),
    )
    assert audit.matched_rows == 2
    assert audit.unmatched_rows == 0
    assert audit.outcome_disagreements == 0
    assert audit.price_disagreements == 0

    classified = append_role_classification(joined)
    assert role_counts(classified) == {
        "TAKER_HIGH_CONFIDENCE": 1,
        "MAKER_HIGH_CONFIDENCE": 1,
    }
    signed = append_signed_yes_pressure(classified)
    assert signed["signed_yes_pressure"].to_pylist() == [6.0, None]


def test_join_fails_on_duplicate_data002_key() -> None:
    duplicate = pa.concat_tables([_fees(), _fees().slice(0, 1)])
    with pytest.raises(ValueError, match="non-unique"):
        enrich_fills_with_roles(
            _fills(),
            duplicate,
            infrastructure_addresses=frozenset(),
        )


def test_join_audits_outcome_and_price_disagreement() -> None:
    fees = _fees()
    outcome_index = fees.schema.get_field_index("outcome_side")
    price_index = fees.schema.get_field_index("price")
    fees = fees.set_column(outcome_index, "outcome_side", pa.array(["NO", "NO"]))
    fees = fees.set_column(price_index, "price", pa.array([0.55, 0.4]))
    _, audit = enrich_fills_with_roles(
        _fills(),
        fees,
        infrastructure_addresses=frozenset(),
    )
    assert audit.outcome_disagreements == 1
    assert audit.price_disagreements == 1


def test_infrastructure_identity_is_counted_not_silently_removed() -> None:
    _, audit = enrich_fills_with_roles(
        _fills(),
        _fees(),
        infrastructure_addresses=frozenset({"0xowner"}),
    )
    assert audit.participant_infrastructure_rows == 1


def test_unmatched_fill_becomes_unknown() -> None:
    joined, audit = enrich_fills_with_roles(
        _fills(),
        _fees().slice(0, 1),
        infrastructure_addresses=frozenset(),
    )
    assert audit.unmatched_rows == 1
    classified = append_role_classification(joined)
    assert role_counts(classified)["UNKNOWN"] == 1


def test_load_infrastructure_addresses_normalizes_case(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text('{"infra_addresses":["0xAbC"],"exchange_addresses":[]}')
    assert load_infrastructure_addresses(path) == frozenset({"0xabc"})
