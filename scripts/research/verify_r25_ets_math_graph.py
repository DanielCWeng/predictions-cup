#!/usr/bin/env python3
"""Offline reproducibility audit for the frozen R2.5 ETS mathematical graph."""

import collections
import csv
import hashlib
import json
import pathlib
import typing

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data/research/r25_ets_math_graph"
SOURCE = ROOT / "data/research/r25_ets_v2_market_review"
MANIFEST = GRAPH / "ETS_REPRODUCIBILITY_MANIFEST.json"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_hashes(manifest: dict[str, typing.Any]) -> dict[str, str]:
    observed: dict[str, str] = {}
    for name, spec in manifest["exact_outputs"].items():
        path = GRAPH / name
        digest = sha256(path)
        observed[name] = digest
        require(digest == spec["sha256"], f"SHA-256 drift: {name}")
        if "rows" in spec and path.suffix == ".csv":
            require(len(csv_rows(path)) == spec["rows"], f"row-count drift: {name}")
    return observed


def verify_source_and_gamma(manifest: dict[str, typing.Any]) -> dict[str, int]:
    source_rows = csv_rows(SOURCE / "ETS_V2_MARKETS_FOR_REVIEW.csv")
    audit_rows = csv_rows(GRAPH / "ETS_GAMMA_SEMANTIC_AUDIT.csv")
    source_spec = manifest["source_universe"]
    gamma_spec = manifest["gamma_semantic_snapshot"]

    require(len(source_rows) == source_spec["market_count"], "source market count drift")
    require(len(audit_rows) == gamma_spec["rows"], "Gamma snapshot row count drift")

    source_by_id = {row["market_id"]: row for row in source_rows}
    audit_by_id = {row["market_id"]: row for row in audit_rows}
    require(len(source_by_id) == len(source_rows), "duplicate source market_id")
    require(len(audit_by_id) == len(audit_rows), "duplicate Gamma market_id")
    require(source_by_id.keys() == audit_by_id.keys(), "Gamma/source market-set drift")

    condition_ids = {row["condition_id"] for row in source_rows}
    tokens = {
        token
        for row in source_rows
        for token in (row["token_yes"], row["token_no"])
        if token
    }
    require(len(condition_ids) == source_spec["condition_count"], "condition count drift")
    require(len(tokens) == source_spec["token_count"], "token count drift")

    for market_id, source_row in source_by_id.items():
        audit_row = audit_by_id[market_id]
        require(source_row["question"] == audit_row["question"], f"question drift: {market_id}")
        require(
            source_row["condition_id"] == audit_row["condition_id"],
            f"condition drift: {market_id}",
        )
        audit_tokens = set(json.loads(audit_row["token_ids_json"]))
        source_tokens = {source_row["token_yes"], source_row["token_no"]}
        require(audit_tokens == source_tokens, f"token drift: {market_id}")
        require(audit_row["description_hash_match"] == "true", f"description drift: {market_id}")
        require(
            audit_row["description_sha256"] == audit_row["frozen_description_sha256"],
            f"description hash drift: {market_id}",
        )

    direct = sum(row["lookup_endpoint"] == "/markets?id=" for row in audit_rows)
    fallback = len(audit_rows) - direct
    events = {row["event_id"] for row in audit_rows}
    require(direct == gamma_spec["direct_market_lookups"], "direct lookup count drift")
    require(fallback == gamma_spec["event_fallback_lookups"], "fallback lookup count drift")
    require(len(events) == gamma_spec["unique_events"], "Gamma event count drift")

    by_event: dict[str, set[tuple[str, str, str]]] = {}
    for row in audit_rows:
        signature = (
            row["event_slug"],
            row["event_title"],
            row["event_market_count_gamma"],
        )
        by_event.setdefault(row["event_id"], set()).add(signature)
    require(all(len(values) == 1 for values in by_event.values()), "event metadata drift")

    return {
        "markets": len(source_rows),
        "conditions": len(condition_ids),
        "tokens": len(tokens),
        "events": len(events),
        "direct_market_lookups": direct,
        "event_fallback_lookups": fallback,
    }


def verify_acquisition(manifest: dict[str, typing.Any]) -> dict[str, int]:
    rows = csv_rows(GRAPH / "ETS_FILL_ACQUISITION.csv")
    require(len(rows) == 1279, "fill-acquisition row count drift")
    require("market_id" in rows[0], "fill-acquisition market_id missing")

    tier_key = next(
        (
            key
            for key in ("acquisition_class", "acquisition_tier", "priority")
            if key in rows[0]
        ),
        None,
    )
    require(tier_key is not None, "fill-acquisition tier column missing")
    counts = collections.Counter(row[tier_key] for row in rows)
    require(dict(counts) == manifest["acquisition_class_counts"], "acquisition tier drift")
    require(len({row["market_id"] for row in rows}) == len(rows), "duplicate acquisition market")
    return dict(counts)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = {
        "status": "PASS",
        "source_gamma": verify_source_and_gamma(manifest),
        "acquisition_class_counts": verify_acquisition(manifest),
        "verified_sha256": verify_hashes(manifest),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
