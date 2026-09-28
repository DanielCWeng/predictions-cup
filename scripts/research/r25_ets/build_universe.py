#!/usr/bin/env python3
"""Build the frozen R2.5 ETS graph from canonical mappings and reviewed Gamma candidates.

This module contains no discovery or predictive code. Gamma candidate discovery runs in
Kaggle; a separate, explicit semantic review file is the only way a market enters the ETS
universe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MAPPING = ROOT / "data/mappings/sig_polymarket_2026.json"
DEFAULT_ACCEPTANCE = ROOT / "data/mappings/sig_polymarket_2026_acceptance.json"
OUT_DIR = ROOT / "data/research/ets_universe"
TRADEABLE_CLASSES = {"EXACT", "DERIVED", "NEAR"}
RELATIONSHIP_CLASSES = {
    "CHAMBER_CONTROL", "JOINT_CHAMBER", "SEAT_EXACT", "SEAT_RANGE", "SEAT_THRESHOLD",
    "AGGREGATE_CONTAINS_TARGET", "TARGET_CONSTITUENT_OF_AGGREGATE", "MUTUALLY_EXCLUSIVE_SIBLING",
    "JOINT_OUTCOME", "MULTI_RACE_COMBO", "PIVOTAL_RACE", "SAME_STATE_RELATED",
    "CONDITIONAL_OUTCOME", "OTHER_ECONOMICALLY_LINKED",
}
EDGE_FIELDS = [
    "source_type", "source_id", "target_type", "target_id", "relationship_class",
    "relationship_direction", "economic_rationale", "review_status", "confidence",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def repository_head() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_cell(row.get(name)) for name in fields})


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, dict, tuple)):
        return json_text(value)
    return value


def accepted_records(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        r for r in mapping["records"]
        if r.get("mapping_class") in TRADEABLE_CLASSES and r.get("status") == "VERIFIED"
    ]


def canonical_components(record: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    direct = record.get("direct_polymarket")
    if isinstance(direct, dict):
        components.append(direct)
    for component in record.get("polymarket_components") or []:
        if isinstance(component, dict):
            components.append(component)
    by_market: dict[str, dict[str, Any]] = {}
    for component in components:
        market_id = str(component.get("market_id") or "").strip()
        if not market_id:
            continue
        old = by_market.get(market_id)
        if old and old.get("condition_id") != component.get("condition_id"):
            raise ValueError(f"canonical mapping has conflicting market IDs: {market_id}")
        by_market[market_id] = component
    return [by_market[k] for k in sorted(by_market, key=lambda s: (int(s) if s.isdigit() else 0, s))]


def make_anchor_rows(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for r in accepted_records(mapping):
        comps = canonical_components(r)
        if not comps:
            raise ValueError(f"tradeable SIG target has no canonical Polymarket identity: {r['sig_market_id']}")
        rows.append({
            "sig_market_id": str(r["sig_market_id"]),
            "sig_exchange_id": str(r["sig_exchange_id"]),
            "sig_tournament_id": r.get("sig_tournament_id"),
            "sig_question": r.get("sig_market_title"),
            "sig_outcome": r.get("sig_outcome_label"),
            "mapping_class": r.get("mapping_class"),
            "mapping_direction": r.get("mapping_direction"),
            "mapping_confidence": r.get("mapping_confidence"),
            "direct_polymarket_market_ids": [str(c["market_id"]) for c in comps],
            "direct_polymarket_event_ids": sorted({str(c["event_id"]) for c in comps if c.get("event_id")}),
            "direct_condition_ids": sorted({str(c["condition_id"]) for c in comps if c.get("condition_id")}),
            "direct_token_ids": sorted({str(t) for c in comps for t in (c.get("token_ids") or []) if t}),
            "direct_polymarket_questions": [c.get("question") for c in comps],
            "mapped_outcomes": [c.get("mapped_outcome") for c in comps],
            "resolution_notes": r.get("resolution_notes"),
            "semantic_notes": r.get("semantic_notes"),
        })
    rows.sort(key=lambda r: (int(r["sig_exchange_id"]), int(r["sig_market_id"])))
    return rows


def direct_identity_sets(mapping: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    market_ids: set[str] = set()
    condition_ids: set[str] = set()
    token_ids: set[str] = set()
    for record in accepted_records(mapping):
        for component in canonical_components(record):
            if component.get("market_id"):
                market_ids.add(str(component["market_id"]))
            if component.get("condition_id"):
                condition_ids.add(str(component["condition_id"]))
            token_ids.update(str(t) for t in (component.get("token_ids") or []) if t)
    return market_ids, condition_ids, token_ids


def command_anchors(args: argparse.Namespace) -> None:
    mapping = read_json(args.mapping)
    rows = make_anchor_rows(mapping)
    fields = [
        "sig_market_id", "sig_exchange_id", "sig_tournament_id", "sig_question", "sig_outcome",
        "mapping_class", "mapping_direction", "mapping_confidence", "direct_polymarket_market_ids",
        "direct_polymarket_event_ids", "direct_condition_ids", "direct_token_ids",
        "direct_polymarket_questions", "mapped_outcomes", "resolution_notes", "semantic_notes",
    ]
    write_csv(args.output, rows, fields)
    market_ids, condition_ids, token_ids = direct_identity_sets(mapping)
    print(json.dumps({
        "anchors": len(rows), "direct_market_ids": len(market_ids),
        "direct_condition_ids": len(condition_ids), "direct_token_ids": len(token_ids),
        "mapping_sha256": sha256(args.mapping), "output": str(args.output),
    }, sort_keys=True))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _json_field(row: dict[str, str], name: str, default: Any) -> Any:
    raw = row.get(name, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain JSON: {raw[:120]}") from exc


def command_finalize(args: argparse.Namespace) -> None:
    """Materialize the graph from candidate metadata plus the explicit reviewed edge file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = read_json(args.mapping)
    acceptance = read_json(args.acceptance)
    gamma_metadata = Path(args.gamma_metadata)
    candidates = read_csv(args.candidates)
    reviewed_edges = read_csv(args.review_edges)
    anchor_rows = make_anchor_rows(mapping)
    direct_market_ids, direct_cids, direct_tokens = direct_identity_sets(mapping)
    anchors_by_id = {r["sig_market_id"]: r for r in anchor_rows}

    by_market: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        market_id = str(candidate.get("market_id") or "").strip()
        cid = str(candidate.get("condition_id") or "").strip()
        if not market_id:
            continue
        old = by_market.get(market_id)
        if old:
            for identity_field in ("condition_id", "event_id", "outcomes", "clob_token_ids"):
                old_value, new_value = old.get(identity_field, ""), candidate.get(identity_field, "")
                if old_value and new_value and old_value != new_value:
                    raise ValueError(
                        f"Gamma returned conflicting {identity_field} for market {market_id}: "
                        f"{old_value!r} != {new_value!r}"
                    )
        by_market[market_id] = candidate

    edges: list[dict[str, Any]] = []
    decisions_by_market: dict[str, list[dict[str, str]]] = defaultdict(list)
    for edge in reviewed_edges:
        market_id = str(edge.get("market_id") or "").strip()
        sig_id = str(edge.get("sig_market_id") or "").strip()
        if not market_id or market_id not in by_market:
            raise ValueError(f"review decision points to unknown candidate market: {market_id}")
        if market_id in direct_market_ids:
            raise ValueError(f"direct mapping market cannot enter adjacent ETS review: {market_id}")
        status = edge.get("review_status", "").strip()
        if status not in {"SEMANTIC_REVIEWED_ACCEPTED", "REJECTED", "PENDING_REVIEW"}:
            raise ValueError(f"invalid review status {status!r} for market {market_id}")
        if status == "SEMANTIC_REVIEWED_ACCEPTED" and sig_id not in anchors_by_id:
            raise ValueError(f"accepted edge points to unknown SIG anchor: {market_id} -> {sig_id}")
        if status != "SEMANTIC_REVIEWED_ACCEPTED" and sig_id and sig_id not in anchors_by_id:
            raise ValueError(f"review decision points to unknown SIG anchor: {market_id} -> {sig_id}")
        decisions_by_market[market_id].append(edge)
        if status != "SEMANTIC_REVIEWED_ACCEPTED":
            continue
        for field in ("relationship_class", "relationship_direction", "economic_rationale", "confidence"):
            if not edge.get(field, "").strip():
                raise ValueError(f"accepted edge missing {field}: market={market_id} SIG={sig_id}")
        if edge["relationship_class"] not in RELATIONSHIP_CLASSES:
            raise ValueError(f"accepted edge uses undocumented relationship class: {edge['relationship_class']}")
        if market_id not in by_market:
            raise ValueError(f"accepted market absent from Gamma candidate inventory: {market_id}")
        if not by_market[market_id].get("condition_id"):
            raise ValueError(f"accepted candidate has no canonical condition ID: {market_id}")
        edges.append({
            "source_type": "SIG_ANCHOR", "source_id": sig_id,
            "target_type": "ETS_MARKET", "target_id": market_id,
            **{k: edge[k] for k in EDGE_FIELDS[4:]},
        })

    accepted_ids = {e["target_id"] for e in edges}
    if len(accepted_ids) != len(edges) and len({(e["target_id"], e["source_id"]) for e in edges}) != len(edges):
        raise ValueError("duplicate accepted ETS edge")
    missing = accepted_ids - by_market.keys()
    if missing:
        raise ValueError(f"accepted markets absent from candidates: {sorted(missing)[:10]}")

    # Every accepted contract must have one CID and an explicit outcome/token alignment.
    market_rows = []
    token_rows = []
    for market_id in sorted(accepted_ids, key=lambda x: (int(x) if x.isdigit() else 0, x)):
        row = by_market[market_id]
        outcome_labels = _json_field(row, "outcomes", [])
        token_ids = _json_field(row, "clob_token_ids", [])
        missing_identity = [field for field in ("event_id", "event_slug", "slug", "question") if not row.get(field, "").strip()]
        if missing_identity:
            raise ValueError(f"accepted market lacks canonical Gamma identity fields {missing_identity}: {market_id}")
        if (
            len(outcome_labels) != len(token_ids) or len(token_ids) < 2
            or len(set(token_ids)) != len(token_ids) or len(set(outcome_labels)) != len(outcome_labels)
            or any(not str(value).strip() for value in [*outcome_labels, *token_ids])
        ):
            raise ValueError(f"ambiguous outcome/token alignment for accepted market {market_id}")
        if any(str(t) in direct_tokens for t in token_ids):
            raise ValueError(f"accepted ETS market reuses a direct-mapping token: {market_id}")
        class_set = sorted({e["relationship_class"] for e in edges if e["target_id"] == market_id})
        links = sorted({e["source_id"] for e in edges if e["target_id"] == market_id}, key=lambda x: int(x))
        if not links:
            raise ValueError(f"accepted ETS market does not link to a SIG anchor: {market_id}")
        market_rows.append({
            **row,
            "existing_direct_mapping": False,
            "relationship_class_set": class_set,
            "linked_sig_market_ids": links,
            "linked_sig_exchange_ids": sorted({anchors_by_id[s]["sig_exchange_id"] for s in links}, key=int),
            "linked_sig_target_count": len(links),
            "earliest_historical_data_at": "",
            "latest_historical_data_at": "",
        })
        for outcome, token in zip(outcome_labels, token_ids, strict=True):
            token_rows.append({
                "event_id": row.get("event_id", ""), "market_id": market_id,
                "condition_id": row["condition_id"], "token_id": str(token),
                "outcome": str(outcome), "question": row.get("question", ""),
                "slug": row.get("slug", ""), "event_slug": row.get("event_slug", ""),
                "start_date": row.get("start_date", ""), "end_date": row.get("end_date", ""),
                "existing_direct_mapping": False, "relationship_class_set": class_set,
                "linked_sig_target_count": len(links),
            })

    # Candidate and rejection ledgers remain separate from the accepted frozen set.
    candidate_rows: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    for market_id, row in sorted(by_market.items(), key=lambda kv: (int(kv[0]) if kv[0].isdigit() else 0, kv[0])):
        decisions = decisions_by_market.get(market_id, [])
        if market_id in accepted_ids:
            status, reason = "ACCEPTED_ETS", ""
        elif market_id in direct_market_ids or row.get("condition_id") in direct_cids:
            status, reason = "REJECTED", "duplicate of canonical direct mapping"
        elif decisions and all(d.get("review_status") == "REJECTED" for d in decisions):
            status = "REJECTED"
            reason = "; ".join(sorted({d.get("rejection_reason", "semantic review rejected") for d in decisions}))
        elif decisions:
            status, reason = "PENDING_REVIEW", ""
        else:
            status, reason = "PENDING_REVIEW", "semantic review not recorded"
        candidate_rows.append({**row, "candidate_status": status, "rejection_reason": reason})
        if status == "REJECTED":
            rejection_rows.append({
                "market_id": market_id, "condition_id": row.get("condition_id", ""),
                "question": row.get("question", ""), "event_slug": row.get("event_slug", ""),
                "rejection_reason": reason,
            })

    node_rows: list[dict[str, Any]] = []
    for anchor in anchor_rows:
        node_rows.append({
            "node_type": "SIG_ANCHOR", "node_id": anchor["sig_market_id"],
            "market_id": "", "event_id": "", "condition_id": "", "token_ids": [],
            "question": anchor["sig_question"], "description": "", "slug": "", "event_slug": "",
            "mapping_class": anchor["mapping_class"], "existing_direct_mapping": False,
            "metadata_source": "canonical SIG crosswalk",
        })
    direct_by_mid: dict[str, dict[str, Any]] = {}
    for record in accepted_records(mapping):
        for component in canonical_components(record):
            mid = str(component["market_id"])
            direct_by_mid.setdefault(mid, component)
    for mid, component in sorted(direct_by_mid.items(), key=lambda kv: (int(kv[0]) if kv[0].isdigit() else 0, kv[0])):
        node_rows.append({
            "node_type": "DIRECT_POLYMARKET", "node_id": mid, "market_id": mid,
            "event_id": component.get("event_id", ""), "condition_id": component.get("condition_id", ""),
            "token_ids": component.get("token_ids", []), "question": component.get("question", ""),
            "description": "", "slug": component.get("slug", ""), "event_slug": "",
            "mapping_class": "", "existing_direct_mapping": True,
            "metadata_source": "canonical SIG crosswalk",
        })
    for row in market_rows:
        node_rows.append({
            "node_type": "ETS_MARKET", "node_id": row["market_id"], "market_id": row["market_id"],
            "event_id": row.get("event_id", ""), "condition_id": row["condition_id"],
            "token_ids": _json_field(row, "clob_token_ids", []), "question": row.get("question", ""),
            "description": row.get("description", ""), "slug": row.get("slug", ""),
            "event_slug": row.get("event_slug", ""), "mapping_class": "",
            "existing_direct_mapping": False, "metadata_source": "Gamma snapshot",
        })
    node_rows.sort(key=lambda r: (r["node_type"], r["node_id"]))

    # Canonical mapping anchor edges make the direct layer explicit in the graph.
    direct_edges = []
    for anchor in anchor_rows:
        records = [r for r in accepted_records(mapping) if str(r["sig_market_id"]) == anchor["sig_market_id"]]
        record = records[0]
        for component in canonical_components(record):
            direct_edges.append({
                "source_type": "SIG_ANCHOR", "source_id": anchor["sig_market_id"],
                "target_type": "DIRECT_POLYMARKET", "target_id": str(component["market_id"]),
                "relationship_class": "EXISTING_DIRECT_MAPPING",
                "relationship_direction": record.get("mapping_direction", ""),
                "economic_rationale": "Canonical accepted SIG-to-Polymarket mapping; retained as an anchor node.",
                "review_status": "CANONICAL_MAPPING_ACCEPTED",
                "confidence": record.get("mapping_confidence", ""),
            })
    all_edges = direct_edges + edges
    relationship_counts = Counter(e["relationship_class"] for e in edges)

    def save_csv(filename: str, rows: list[dict[str, Any]], fields: list[str]) -> None:
        write_csv(OUT_DIR / filename, rows, fields)

    save_csv("SIG_ANCHOR_UNIVERSE.csv", anchor_rows, list(anchor_rows[0]))
    candidate_fields = list(dict.fromkeys(k for r in candidate_rows for k in r))
    save_csv("ETS_CANDIDATES.csv", candidate_rows, candidate_fields or ["market_id"])
    save_csv("ETS_REJECTIONS.csv", rejection_rows, ["market_id", "condition_id", "question", "event_slug", "rejection_reason"])
    inventory_fields = list(dict.fromkeys(k for r in market_rows for k in r))
    save_csv("ETS_MARKET_INVENTORY.csv", market_rows, inventory_fields or ["market_id"])
    save_csv("ETS_TOKEN_INVENTORY.csv", token_rows, [
        "event_id", "market_id", "condition_id", "token_id", "outcome", "question", "slug",
        "event_slug", "start_date", "end_date", "existing_direct_mapping",
        "relationship_class_set", "linked_sig_target_count",
    ])
    node_fields = list(dict.fromkeys(k for r in node_rows for k in r))
    save_csv("ETS_NODES.csv", node_rows, node_fields)
    save_csv("ETS_EDGES.csv", all_edges, EDGE_FIELDS)

    graph = {
        "schema_version": 1,
        "generated_at": args.discovery_timestamp,
        "anchor_mapping_sha256": sha256(args.mapping),
        "nodes": node_rows,
        "edges": all_edges,
    }
    graph_path = OUT_DIR / "ETS_RELATIONSHIP_GRAPH.json"
    graph_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cid_rows: dict[str, dict[str, Any]] = {}
    for row in market_rows:
        cid = str(row["condition_id"])
        prior = cid_rows.get(cid)
        tokens = _json_field(row, "clob_token_ids", [])
        if prior and (prior["token_ids"] != tokens or prior["market_id"] != str(row["market_id"])):
            raise ValueError(f"condition ID resolves to conflicting market/token identity: {cid}")
        cid_rows[cid] = {"condition_id": cid, "market_id": str(row["market_id"]), "token_ids": tokens}
    (OUT_DIR / "ETS_CONDITION_IDS.txt").write_text("".join(f"{cid}\n" for cid in sorted(cid_rows)), encoding="utf-8")
    all_tokens = sorted({str(t["token_id"]) for t in token_rows})
    (OUT_DIR / "ETS_TOKEN_IDS.txt").write_text("".join(f"{t}\n" for t in all_tokens), encoding="utf-8")
    (OUT_DIR / "ETS_CONDITION_TOKEN_MAP.json").write_text(
        json.dumps({cid: row["token_ids"] for cid, row in sorted(cid_rows.items())}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    inventory_path = OUT_DIR / "ETS_MARKET_INVENTORY.csv"
    token_inventory_path = OUT_DIR / "ETS_TOKEN_INVENTORY.csv"
    nodes_path = OUT_DIR / "ETS_NODES.csv"
    edges_path = OUT_DIR / "ETS_EDGES.csv"
    freeze = {
        "schema_version": 1,
        "base_main_sha": args.base_sha,
        "repository_commit_at_freeze": repository_head(),
        "canonical_sig_mapping_sha256": sha256(args.mapping),
        "mapping_acceptance_sha256": sha256(args.acceptance),
        "discovery_code_sha256": args.discovery_code_sha,
        "discovery_timestamp": args.discovery_timestamp,
        "freeze_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sig_anchor_count": len(anchor_rows),
        "candidate_ets_market_count": sum(1 for r in candidate_rows if r["market_id"] not in direct_market_ids and r.get("condition_id") not in direct_cids),
        "discovered_market_count_including_direct": len(candidate_rows),
        "accepted_ets_market_count": len(market_rows),
        "rejected_candidate_count": sum(
            r["candidate_status"] == "REJECTED" and r["market_id"] not in direct_market_ids and r.get("condition_id") not in direct_cids
            for r in candidate_rows
        ),
        "rejected_direct_duplicate_count": sum(
            r["candidate_status"] == "REJECTED" and (r["market_id"] in direct_market_ids or r.get("condition_id") in direct_cids)
            for r in candidate_rows
        ),
        "pending_candidate_count": sum(
            r["candidate_status"] == "PENDING_REVIEW" and r["market_id"] not in direct_market_ids and r.get("condition_id") not in direct_cids
            for r in candidate_rows
        ),
        "pending_relationship_review_count": sum(e.get("review_status") == "PENDING_REVIEW" for e in reviewed_edges),
        "unique_cid_count": len(cid_rows),
        "unique_token_count": len(all_tokens),
        "relationship_class_counts": dict(sorted(relationship_counts.items())),
        "direct_anchor_counts": {
            "direct_market_ids": len(direct_market_ids), "direct_cids": len(direct_cids),
            "direct_tokens": len(direct_tokens),
        },
        "sha256": {
            "nodes": sha256(nodes_path), "edges": sha256(edges_path),
            "market_inventory": sha256(inventory_path), "cid_token_inventory": sha256(token_inventory_path),
            "condition_ids": sha256(OUT_DIR / "ETS_CONDITION_IDS.txt"),
            "token_ids": sha256(OUT_DIR / "ETS_TOKEN_IDS.txt"),
            "condition_token_map": sha256(OUT_DIR / "ETS_CONDITION_TOKEN_MAP.json"),
            "relationship_graph": sha256(graph_path),
            "gamma_metadata": sha256(gamma_metadata),
        },
    }
    if freeze["pending_candidate_count"] or freeze["pending_relationship_review_count"]:
        raise ValueError(
            "cannot freeze with unresolved review work: "
            f"{freeze['pending_candidate_count']} candidates, "
            f"{freeze['pending_relationship_review_count']} relationship edges"
        )
    (OUT_DIR / "ETS_UNIVERSE_FREEZE.json").write_text(
        json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(freeze, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    anchors = sub.add_parser("anchors")
    anchors.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    anchors.add_argument("--output", type=Path, default=OUT_DIR / "SIG_ANCHOR_UNIVERSE.csv")
    anchors.set_defaults(func=command_anchors)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    finalize.add_argument("--acceptance", type=Path, default=DEFAULT_ACCEPTANCE)
    finalize.add_argument("--candidates", type=Path, required=True)
    finalize.add_argument("--gamma-metadata", required=True)
    finalize.add_argument("--review-edges", type=Path, required=True)
    finalize.add_argument("--base-sha", required=True)
    finalize.add_argument("--discovery-code-sha", required=True)
    finalize.add_argument("--discovery-timestamp", required=True)
    finalize.set_defaults(func=command_finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
