"""Generate the strict Polymarket capture universe used at launch.

The production recorder remains fail-closed.  This helper keeps the accepted
SIG<->Polymarket mapping universe separate from small, explicit research-only
shadow sources, then emits their deterministic union for runtime.env.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAPPING_CLASSES = {"EXACT", "DERIVED"}


def accepted_mapping_token_ids(document: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("mapping document requires records[]")
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("mapping record must be an object")
        if str(record.get("mapping_class") or "").upper() not in MAPPING_CLASSES:
            continue
        legs: list[Mapping[str, Any]] = []
        direct = record.get("direct_polymarket")
        if isinstance(direct, Mapping):
            legs.append(direct)
        components = record.get("polymarket_components") or []
        if not isinstance(components, list):
            raise ValueError("polymarket_components must be a list")
        legs.extend(leg for leg in components if isinstance(leg, Mapping))
        for leg in legs:
            token = str(leg.get("mapped_token_id") or "").strip()
            if token:
                tokens.add(token)
    if not tokens:
        raise ValueError("accepted mapping resolved zero capture tokens")
    return tokens


def shadow_token_ids(spec: Mapping[str, Any]) -> set[str]:
    if int(spec.get("schema_version") or 0) != 1:
        raise ValueError("unsupported shadow capture schema_version")
    sources = spec.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("shadow capture spec requires non-empty sources[]")
    tokens: set[str] = set()
    for row in sources:
        if not isinstance(row, Mapping):
            raise ValueError("shadow source must be an object")
        if str(row.get("outcome_label") or "").upper() != "YES":
            raise ValueError("shadow capture is restricted to explicit YES tokens")
        token = str(row.get("capture_token_id") or "").strip()
        market_id = str(row.get("market_id") or "").strip()
        if not token.isdigit() or not market_id.isdigit():
            raise ValueError("shadow market/token identities must be decimal strings")
        if token in tokens:
            raise ValueError(f"duplicate shadow capture token: {token}")
        tokens.add(token)
    return tokens


def combined_supervised_ids(
    mapping_document: Mapping[str, Any],
    *,
    shadow_spec: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    tokens = accepted_mapping_token_ids(mapping_document)
    if shadow_spec is not None:
        tokens.update(shadow_token_ids(shadow_spec))
    return tuple(sorted(tokens))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print strict supervised Polymarket IDs for runtime.env"
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path("data/mappings/sig_polymarket_2026.json"),
    )
    parser.add_argument(
        "--shadow-spec",
        type=Path,
        default=Path("data/capture/r3_live_shadow_polymarket_ids.json"),
    )
    parser.add_argument(
        "--no-shadow",
        action="store_true",
        help="Emit only accepted EXACT+DERIVED mapped tokens.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    shadow = None
    if not args.no_shadow:
        shadow = json.loads(args.shadow_spec.read_text(encoding="utf-8"))
    ids = combined_supervised_ids(mapping, shadow_spec=shadow)
    mapping_count = len(accepted_mapping_token_ids(mapping))
    shadow_count = 0 if shadow is None else len(shadow_token_ids(shadow))
    print(
        f"mapping_tokens={mapping_count} shadow_tokens={shadow_count} "
        f"supervised_ids={len(ids)}",
        file=sys.stderr,
    )
    print(",".join(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
