#!/usr/bin/env python3
"""Freeze DATA-004 P0/P1 identities, Gamma outcome alignment, and baseline links."""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LANE = Path("/home/ubuntu/campaigns/data004_20260929")
GRAPH_DIR = ROOT / "data/research/r25_ets_math_graph"
ACQUISITION_PATH = GRAPH_DIR / "ETS_FILL_ACQUISITION.csv"
MARKET_GRAPH_PATH = GRAPH_DIR / "ETS_MARKET_GRAPH.csv"
ANCHOR_GRAPH_PATH = GRAPH_DIR / "ETS_SIG_ANCHOR_GRAPH.csv"
MAPPING_PATH = ROOT / "data/mappings/sig_polymarket_2026.json"
DATA003_MANIFEST_PATH = ROOT / "data/manifests/fills/data_003_manifest.json"
GAMMA_BASE = "https://gamma-api.polymarket.com"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_json_array(value: str | None) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    return parsed


def fetch_json(path: str, *, retries: int = 5) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(f"{GAMMA_BASE}{path}", timeout=(10, 45))
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError(f"Gamma returned {type(body).__name__}, expected object")
            return body
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 20))
    assert last_error is not None
    raise RuntimeError(f"Gamma request failed for {path}: {type(last_error).__name__}: {last_error}")


def get_market(market_id: str) -> tuple[str, dict[str, Any]]:
    return market_id, fetch_json(f"/markets/{market_id}")


def get_event(event_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        return event_id, fetch_json(f"/events/{event_id}"), None
    except RuntimeError as exc:
        return event_id, None, str(exc)


def graph_market_ids(row: dict[str, str]) -> set[str]:
    ids = set(map(str, parse_json_array(row.get("polymarket_market_ids_json"))))
    if row.get("target_type") == "PM_MARKET" and row.get("target_id"):
        ids.add(str(row["target_id"]))
    return ids


def source_identity(source: dict[str, Any]) -> dict[str, Any]:
    outcomes = [str(x) for x in source.get("outcomes", [])]
    tokens = [str(x) for x in source.get("token_ids", [])]
    if len(outcomes) != len(tokens) or not tokens:
        raise ValueError(f"baseline source has invalid outcome/token alignment: {source.get('market_id')}")
    return {
        "market_id": str(source["market_id"]),
        "condition_id": str(source["condition_id"]),
        "event_id": str(source.get("event_id") or ""),
        "question": source.get("question"),
        "slug": source.get("slug"),
        "outcomes": outcomes,
        "token_ids": tokens,
        "mapped_outcome": source.get("mapped_outcome"),
        "mapped_token_id": source.get("mapped_token_id"),
    }


def build_baseline_plan(
    selected: list[dict[str, str]],
    anchor_rows: dict[str, dict[str, str]],
) -> dict[str, Any]:
    mapping_payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    mapping_records = mapping_payload["records"]
    usable = [row for row in mapping_records if row["mapping_class"] in {"EXACT", "DERIVED", "NEAR"}]
    if len(usable) != 231:
        raise ValueError(f"accepted direct/derived/near mapping rows expected 231, got {len(usable)}")
    by_exchange = {str(row["sig_exchange_id"]): row for row in usable}
    if len(by_exchange) != 231:
        raise ValueError("SIG exchange IDs are not unique in the accepted mapping")
    if set(anchor_rows) != set(by_exchange):
        raise ValueError("ETS SIG anchor graph and accepted mapping exchange IDs do not agree")

    data004_by_exchange: dict[str, dict[str, list[str]]] = {}
    for row in selected:
        for exchange_id in parse_json_array(row.get("sig_exchange_ids_json")):
            slot = data004_by_exchange.setdefault(str(exchange_id), {"p0": [], "p1": []})
            slot["p0" if row["priority"] == "P0" else "p1"].append(str(row["market_id"]))

    anchor_plans: list[dict[str, Any]] = []
    source_market_ids: set[str] = set()
    source_conditions: set[str] = set()
    source_tokens: set[str] = set()
    for exchange_id in sorted(by_exchange, key=lambda value: int(value)):
        mapping = by_exchange[exchange_id]
        mapping_class = mapping["mapping_class"]
        source_rows = (
            [source_identity(item) for item in mapping.get("polymarket_components", [])]
            if mapping_class == "DERIVED"
            else [source_identity(mapping["direct_polymarket"]) ]
        )
        for source in source_rows:
            source_market_ids.add(source["market_id"])
            source_conditions.add(source["condition_id"])
            source_tokens.update(source["token_ids"])
        v2 = data004_by_exchange.get(exchange_id, {"p0": [], "p1": []})
        anchor_plans.append({
            "sig_exchange_id": exchange_id,
            "sig_market_id": str(mapping["sig_market_id"]),
            "sig_outcome_label": mapping.get("sig_outcome_label"),
            "sig_question": mapping.get("sig_market_title"),
            "mapping_class": mapping_class,
            "mapping_direction": mapping.get("mapping_direction"),
            "mapping_status": mapping.get("status"),
            "semantic_notes": mapping.get("semantic_notes"),
            "resolution_notes": mapping.get("resolution_notes"),
            "baseline_source_markets": source_rows,
            "data004_p0_market_ids": sorted(set(v2["p0"]), key=int),
            "data004_p1_market_ids": sorted(set(v2["p1"]), key=int),
            "ets_anchor_graph_row": anchor_rows[exchange_id],
            "pairing_rule": (
                "Join DATA-003 history by accepted baseline condition_id/token_id and the SIG exchange mapping; "
                "join DATA-004 by the frozen sig_exchange_ids_json edge. Preserve SAME/COMPLEMENT or DERIVED "
                "semantics from the accepted mapping; compare only under the graph's stated relationship/equation."
            ),
        })

    selected_market_ids = {str(row["market_id"]) for row in selected}
    selected_cids = {str(row["condition_id"]) for row in selected}
    selected_tokens = {
        str(token)
        for row in selected
        for token in (row["token_yes"], row["token_no"])
    }
    overlap = {
        "market_ids": sorted(source_market_ids & selected_market_ids, key=int),
        "condition_ids": sorted(source_conditions & selected_cids),
        "token_ids": sorted(source_tokens & selected_tokens),
    }
    if any(overlap.values()):
        raise ValueError(f"accepted baseline history overlaps DATA-004 P0/P1 scope: {overlap}")

    return {
        "schema_version": 1,
        "baseline_dataset": "DATA-003",
        "baseline_manifest_path": "data/manifests/fills/data_003_manifest.json",
        "baseline_manifest_sha256": sha256_file(DATA003_MANIFEST_PATH),
        "accepted_mapping_path": "data/mappings/sig_polymarket_2026.json",
        "accepted_mapping_sha256": sha256_file(MAPPING_PATH),
        "source_scope": {
            "mapping_records": len(usable),
            "classes": {name: sum(row["mapping_class"] == name for row in usable) for name in ("EXACT", "DERIVED", "NEAR")},
            "unique_market_ids": len(source_market_ids),
            "unique_condition_ids": len(source_conditions),
            "unique_token_ids": len(source_tokens),
            "outside_data004_p0p1": not any(overlap.values()),
            "overlap": overlap,
        },
        "pairing_method": (
            "Use DATA-003 as the accepted historical fill baseline, keyed by condition_id/token_id and attached to "
            "SIG exchange IDs through the accepted MAPPING-001 crosswalk. Pair DATA-004 markets through their "
            "frozen SIG anchor graph edges, retaining relationship_class, mathematical_class, equations/bounds, "
            "and SAME/COMPLEMENT/DERIVED direction. No baseline histories were acquired in DATA-004."
        ),
        "anchors": anchor_plans,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", type=Path, default=DEFAULT_LANE)
    args = parser.parse_args()
    lane = args.lane.resolve()
    lane.mkdir(parents=True, exist_ok=True)

    with ACQUISITION_PATH.open(encoding="utf-8-sig", newline="") as handle:
        acquisition_rows = list(csv.DictReader(handle))
    selected = [row for row in acquisition_rows if row.get("acquisition_class") in {"FILLS_P0", "FILLS_P1"}]
    if len(selected) != 298:
        raise ValueError(f"P0+P1 acquisition scope expected 298 rows, got {len(selected)}")
    counts = {tier: sum(row["acquisition_class"] == tier for row in selected) for tier in ("FILLS_P0", "FILLS_P1")}
    if counts != {"FILLS_P0": 210, "FILLS_P1": 88}:
        raise ValueError(f"expected 210 P0 + 88 P1, got {counts}")
    for label, values in (
        ("market", [row["market_id"] for row in selected]),
        ("condition", [row["condition_id"] for row in selected]),
        ("token", [token for row in selected for token in (row["token_yes"], row["token_no"])]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {label} IDs in P0+P1 universe")
    if any(not row.get(field) for row in selected for field in ("market_id", "condition_id", "token_yes", "token_no")):
        raise ValueError("P0+P1 universe contains blank market/CID/token identity")

    with MARKET_GRAPH_PATH.open(encoding="utf-8-sig", newline="") as handle:
        market_graph = list(csv.DictReader(handle))
    graph_by_market: dict[str, list[dict[str, str]]] = {str(row["market_id"]): [] for row in selected}
    selected_ids = set(graph_by_market)
    for edge in market_graph:
        for market_id in graph_market_ids(edge) & selected_ids:
            graph_by_market[market_id].append(edge)
    missing_edges = [market_id for market_id, edges in graph_by_market.items() if not edges]
    if missing_edges:
        raise ValueError(f"selected markets missing ETS_MARKET_GRAPH links: {missing_edges}")

    with ANCHOR_GRAPH_PATH.open(encoding="utf-8-sig", newline="") as handle:
        anchor_graph = list(csv.DictReader(handle))
    anchor_rows = {str(row["sig_exchange_id"]): row for row in anchor_graph}
    selected_anchor_ids = {
        str(anchor)
        for row in selected
        for anchor in parse_json_array(row.get("sig_exchange_ids_json"))
    }
    missing_anchors = sorted(selected_anchor_ids - set(anchor_rows), key=int)
    if missing_anchors:
        raise ValueError(f"selected SIG anchor references absent from ETS_SIG_ANCHOR_GRAPH: {missing_anchors}")

    market_ids = [str(row["market_id"]) for row in selected]
    raw_market_by_id: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for market_id, body in pool.map(get_market, market_ids):
            raw_market_by_id[market_id] = body

    event_ids = sorted({str(row["event_id"]) for row in selected if row.get("event_id")})
    raw_event_by_id: dict[str, dict[str, Any]] = {}
    event_errors: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for event_id, body, error in pool.map(get_event, event_ids):
            if body is not None:
                raw_event_by_id[event_id] = body
            else:
                event_errors.append({"event_id": event_id, "error": error or "unknown error"})

    universe_markets: list[dict[str, Any]] = []
    token_rows: list[dict[str, Any]] = []
    for row in selected:
        market_id = str(row["market_id"])
        gamma = raw_market_by_id[market_id]
        if str(gamma.get("id")) != market_id:
            raise ValueError(f"Gamma market ID mismatch for requested market {market_id}")
        if str(gamma.get("conditionId") or "").lower() != str(row["condition_id"]).lower():
            raise ValueError(f"Gamma CID mismatch for market {market_id}")
        outcomes = parse_json_array(gamma.get("outcomes")) if isinstance(gamma.get("outcomes"), str) else list(gamma.get("outcomes") or [])
        clob_tokens = parse_json_array(gamma.get("clobTokenIds")) if isinstance(gamma.get("clobTokenIds"), str) else list(gamma.get("clobTokenIds") or [])
        if len(outcomes) != len(clob_tokens) or not outcomes:
            raise ValueError(f"Gamma outcome/token arrays do not align for market {market_id}")
        outcome_map = {str(label).strip().casefold(): str(token) for label, token in zip(outcomes, clob_tokens, strict=True)}
        if len(outcome_map) != len(outcomes):
            raise ValueError(f"Gamma outcomes are not unique for market {market_id}")
        expected = {"yes": str(row["token_yes"]), "no": str(row["token_no"])}
        if outcome_map != expected:
            raise ValueError(
                f"Gamma token/outcome alignment differs from frozen acquisition row for {market_id}: "
                f"Gamma={outcome_map}, frozen={expected}"
            )
        for outcome_label, token_id in zip(outcomes, clob_tokens, strict=True):
            token_rows.append({
                "market_id": market_id,
                "condition_id": str(row["condition_id"]),
                "token_id": str(token_id),
                "outcome_label": str(outcome_label),
                "market_question": str(gamma.get("question") or row.get("question") or ""),
                "event_id": str(row.get("event_id") or ""),
                "acquisition_class": row["acquisition_class"],
                "priority": row["priority"],
            })

        event_id = str(row.get("event_id") or "")
        event_raw = raw_event_by_id.get(event_id)
        market_record = {
            **row,
            "market_id": market_id,
            "condition_id": str(row["condition_id"]),
            "gamma_market": gamma,
            "gamma_event": event_raw,
            "gamma_event_fetch_error": next((item["error"] for item in event_errors if item["event_id"] == event_id), None),
            "gamma_created_at": gamma.get("createdAt"),
            "gamma_start_date": gamma.get("startDate"),
            "gamma_end_date": gamma.get("endDate"),
            "gamma_outcomes": [str(value) for value in outcomes],
            "gamma_token_ids": [str(value) for value in clob_tokens],
            "gamma_outcome_token_alignment": [
                {"outcome_label": str(label), "token_id": str(token)} for label, token in zip(outcomes, clob_tokens, strict=True)
            ],
            "graph_links": graph_by_market[market_id],
            "sig_anchor_rows": [anchor_rows[str(anchor)] for anchor in parse_json_array(row.get("sig_exchange_ids_json"))],
        }
        universe_markets.append(market_record)

    baseline_plan = build_baseline_plan(selected, anchor_rows)
    gamma_market_cache = lane / "gamma_markets.jsonl"
    gamma_event_cache = lane / "gamma_events.jsonl"
    gamma_market_cache.write_text(
        "".join(json.dumps(raw_market_by_id[mid], sort_keys=True, separators=(",", ":")) + "\n" for mid in sorted(raw_market_by_id, key=int)),
        encoding="utf-8",
    )
    gamma_event_cache.write_text(
        "".join(json.dumps(raw_event_by_id[eid], sort_keys=True, separators=(",", ":")) + "\n" for eid in sorted(raw_event_by_id, key=int)),
        encoding="utf-8",
    )
    write_json(lane / "data004_universe.json", {
        "schema_version": 1,
        "source_repository_commit": __import__("subprocess").check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "source_files": {
            "acquisition": {"path": str(ACQUISITION_PATH.relative_to(ROOT)), "sha256": sha256_file(ACQUISITION_PATH)},
            "market_graph": {"path": str(MARKET_GRAPH_PATH.relative_to(ROOT)), "sha256": sha256_file(MARKET_GRAPH_PATH)},
            "sig_anchor_graph": {"path": str(ANCHOR_GRAPH_PATH.relative_to(ROOT)), "sha256": sha256_file(ANCHOR_GRAPH_PATH)},
            "accepted_mapping": {"path": str(MAPPING_PATH.relative_to(ROOT)), "sha256": sha256_file(MAPPING_PATH)},
        },
        "gamma": {
            "api_base": GAMMA_BASE,
            "market_snapshot_path": str(gamma_market_cache),
            "market_snapshot_sha256": sha256_file(gamma_market_cache),
            "market_snapshot_rows": len(raw_market_by_id),
            "event_snapshot_path": str(gamma_event_cache),
            "event_snapshot_sha256": sha256_file(gamma_event_cache),
            "event_snapshot_rows": len(raw_event_by_id),
            "event_fetch_errors": event_errors,
        },
        "counts": {
            "markets": len(universe_markets),
            "conditions": len({row["condition_id"] for row in universe_markets}),
            "tokens": len(token_rows),
            "p0_markets": counts["FILLS_P0"],
            "p1_markets": counts["FILLS_P1"],
            "selected_sig_exchange_ids": len(selected_anchor_ids),
            "market_graph_links": sum(len(row["graph_links"]) for row in universe_markets),
        },
        "markets": universe_markets,
        "tokens": token_rows,
    })
    write_json(lane / "data004_baseline_pairing.json", baseline_plan)
    summary = {
        "status": "PREPARED",
        "markets": len(universe_markets),
        "conditions": len({row["condition_id"] for row in universe_markets}),
        "tokens": len(token_rows),
        "p0_markets": counts["FILLS_P0"],
        "p1_markets": counts["FILLS_P1"],
        "selected_sig_exchange_ids": len(selected_anchor_ids),
        "market_graph_links": sum(len(row["graph_links"]) for row in universe_markets),
        "gamma_markets_cached": len(raw_market_by_id),
        "gamma_events_cached": len(raw_event_by_id),
        "gamma_event_fetch_errors": len(event_errors),
        "baseline_source_markets": baseline_plan["source_scope"]["unique_market_ids"],
        "baseline_conditions": baseline_plan["source_scope"]["unique_condition_ids"],
        "baseline_tokens": baseline_plan["source_scope"]["unique_token_ids"],
        "baseline_overlap_market_condition_token": baseline_plan["source_scope"]["overlap"],
        "universe_json": str(lane / "data004_universe.json"),
        "baseline_pairing_json": str(lane / "data004_baseline_pairing.json"),
    }
    write_json(lane / "prepare_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
