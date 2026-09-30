from __future__ import annotations

import json
from pathlib import Path

import pytest

from predictions_cup.external.polymarket.supervised_universe import (
    accepted_mapping_token_ids,
    combined_supervised_ids,
    shadow_token_ids,
)


def test_combined_supervised_ids_union_mapping_and_shadow() -> None:
    mapping = {
        "records": [
            {
                "mapping_class": "EXACT",
                "direct_polymarket": {"mapped_token_id": "100"},
                "polymarket_components": [],
            },
            {
                "mapping_class": "DERIVED",
                "direct_polymarket": None,
                "polymarket_components": [
                    {"mapped_token_id": "200"},
                    {"mapped_token_id": "300"},
                ],
            },
            {
                "mapping_class": "NEAR",
                "direct_polymarket": {"mapped_token_id": "999"},
                "polymarket_components": [],
            },
        ]
    }
    shadow = {
        "schema_version": 1,
        "sources": [
            {
                "market_id": "42",
                "capture_token_id": "400",
                "outcome_label": "Yes",
            }
        ],
    }

    assert accepted_mapping_token_ids(mapping) == {"100", "200", "300"}
    assert shadow_token_ids(shadow) == {"400"}
    assert combined_supervised_ids(mapping, shadow_spec=shadow) == (
        "100",
        "200",
        "300",
        "400",
    )


def test_shadow_spec_rejects_non_yes_and_duplicate_tokens() -> None:
    with pytest.raises(ValueError, match="YES"):
        shadow_token_ids(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "market_id": "42",
                        "capture_token_id": "400",
                        "outcome_label": "No",
                    }
                ],
            }
        )

    with pytest.raises(ValueError, match="duplicate"):
        shadow_token_ids(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "market_id": "42",
                        "capture_token_id": "400",
                        "outcome_label": "Yes",
                    },
                    {
                        "market_id": "43",
                        "capture_token_id": "400",
                        "outcome_label": "Yes",
                    },
                ],
            }
        )


def test_live_shadow_spec_is_explicit_and_disjoint_from_accepted_mapping() -> None:
    root = Path(__file__).resolve().parents[1]
    mapping = json.loads(
        (root / "data/mappings/sig_polymarket_2026.json").read_text(encoding="utf-8")
    )
    shadow = json.loads(
        (root / "data/capture/r3_live_shadow_polymarket_ids.json").read_text(
            encoding="utf-8"
        )
    )
    mapping_tokens = accepted_mapping_token_ids(mapping)
    shadow_tokens = shadow_token_ids(shadow)

    assert len(shadow_tokens) == 13
    assert mapping_tokens.isdisjoint(shadow_tokens)
    assert len(combined_supervised_ids(mapping, shadow_spec=shadow)) == (
        len(mapping_tokens) + 13
    )
