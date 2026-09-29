"""R2.5 ETS candidate discovery. Runs only as a Kaggle Internet-enabled kernel."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://gamma-api.polymarket.com"
OUT = Path("/kaggle/working/r25_ets_discovery")
HERE = Path(__file__).resolve().parent
MAPPING_NAME = "sig_polymarket_2026.json"
USER_AGENT = "predictions-cup-r25-ets-discovery/1.0"
PAGE_SIZE = 500

STATE_NAMES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}
ABBR_NAMES = {abbr: name for name, abbr in STATE_NAMES.items()}
TAG_RE = re.compile(r"election|politic|congress|senate|house|governor|midterm|ballot|vot|campaign|president|government", re.I)
ELECTION_RE = re.compile(r"election|midterm|congress|senate|house|seat|majority|control|balance of power|governor|governorship|state legislature|delegation|sweep|trifecta|candidate|race", re.I)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mapping_path() -> Path:
    matches = [HERE / MAPPING_NAME, Path.cwd() / MAPPING_NAME]
    input_root = Path("/kaggle/input")
    if input_root.exists():
        matches.extend(input_root.rglob(MAPPING_NAME))
    for path in matches:
        if path.is_file():
            return path
    raise FileNotFoundError(f"could not locate bundled canonical mapping {MAPPING_NAME}")


def get_json(path: str, params: dict[str, Any] | None = None, attempts: int = 6) -> Any:
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params, doseq=True)
    last: Exception | None = None
    for attempt in range(attempts):
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=40) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            last = exc
            if exc.code == 404:
                raise
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30, 2 ** attempt)
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            delay = min(30, 2 ** attempt)
        time.sleep(delay)
    raise RuntimeError(f"Gamma request failed after {attempts} attempts: {url}: {last}")


def rows_from(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return [x for x in value[key] if isinstance(x, dict)]
    return []


def fetch_tags(audit: dict[str, Any]) -> list[dict[str, Any]]:
    tags: dict[str, dict[str, Any]] = {}
    offset = 0
    page_sizes = []
    tag_limit = 100
    while offset <= 100_000:
        data = get_json("/tags", {"limit": tag_limit, "offset": offset})
        page = rows_from(data, "tags")
        page_sizes.append(len(page))
        if not page:
            break
        before = len(tags)
        for tag in page:
            if tag.get("id") is not None:
                tags[str(tag["id"])] = tag
        if len(page) < tag_limit or len(tags) == before:
            break
        offset += len(page)
    audit["tag_page_sizes"] = page_sizes
    audit["tag_count"] = len(tags)
    return list(tags.values())


def relevant_tags(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = {}
    for tag in tags:
        label = f"{tag.get('label', '')} {tag.get('slug', '')}"
        if TAG_RE.search(label):
            selected[str(tag["id"])] = tag
    # Gamma's tag relationships are a directed graph. Expand one hop from the tags
    # directly labelled with elections/politics/congress/chamber concepts.
    related_errors = []
    for tag_id in list(selected):
        tag = selected[tag_id]
        slug = str(tag.get("slug") or "").strip()
        if not slug:
            continue
        try:
            related = get_json(f"/tags/slug/{slug}/related-tags/tags", {"status": "active"})
            for item in rows_from(related, "tags"):
                if item.get("id") is not None:
                    selected.setdefault(str(item["id"]), item)
        except Exception as exc:  # the primary tag inventory remains complete evidence
            related_errors.append({"tag_id": tag_id, "error": f"{type(exc).__name__}: {exc}"[:250]})
    # Kept on the function for the discovery audit without changing the return type.
    relevant_tags.last_errors = related_errors
    return sorted(selected.values(), key=lambda x: (str(x.get("label", "")).lower(), str(x.get("id", ""))))


def page_events_for_tag(tag_id: str, closed: bool, audit_row: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = None
    found: dict[str, dict[str, Any]] = {}
    pages = 0
    while pages < 200:
        params: dict[str, Any] = {"tag_id": tag_id, "closed": str(closed).lower(), "limit": PAGE_SIZE}
        if cursor:
            params["after_cursor"] = cursor
        data = get_json("/events/keyset", params)
        events = rows_from(data, "events")
        pages += 1
        for event in events:
            if event.get("id") is not None:
                found[str(event["id"])] = event
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not events or not cursor:
            break
    audit_row.update({"closed": closed, "pages": pages, "event_count": len(found), "truncated": pages >= 200})
    return list(found.values())


def anchor_states(mapping: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value or "")
        for r in mapping["records"] if r.get("mapping_class") in {"EXACT", "DERIVED", "NEAR"}
        for value in [r.get("sig_market_title"), r.get("direct_polymarket", {}).get("question") if isinstance(r.get("direct_polymarket"), dict) else None]
    )
    states = {name for name in STATE_NAMES if re.search(rf"\b{re.escape(name)}\b", text, re.I)}
    for name, abbr in STATE_NAMES.items():
        # Congressional district spelling such as CA-08 / TX 23 is an exact state code.
        if re.search(rf"\b{abbr}\s*[-–]\s*\d{{1,2}}\b", text, re.I):
            states.add(name)
    return sorted(states)


def search_terms(states: list[str]) -> list[str]:
    terms = [
        "2026 US midterm elections", "2026 midterm election", "2026 congressional elections",
        "2026 United States House control", "2026 House control", "2026 Senate control",
        "2026 balance of power", "2026 House and Senate control", "2026 joint chamber control",
        "2026 unified government", "2026 House seats", "2026 Senate seats",
        "2026 House seat totals", "2026 Senate seat totals", "2026 House seat distribution",
        "2026 Senate seat distribution", "2026 House majority", "2026 Senate majority",
        "2026 election seat threshold", "2026 House majority threshold", "2026 Senate majority threshold",
        "2026 party wins all races", "2026 election sweep", "2026 state delegation control",
        "2026 gubernatorial elections", "2026 secretary of state elections",
    ]
    for state in states:
        terms.extend([
            f"2026 {state} election", f"2026 {state} House", f"2026 {state} Senate",
            f"2026 {state} governor", f"2026 {state} election combination",
        ])
    return terms


def search_events(term: str, audit_row: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    pages = []
    for page_no in range(1, 21):
        data = get_json("/public-search", {"q": term, "page": page_no, "limit_per_type": 100})
        events = rows_from(data, "events")
        pages.append(len(events))
        before = len(found)
        for event in events:
            if event.get("id") is not None:
                found[str(event["id"])] = event
        pagination = data.get("pagination") or {} if isinstance(data, dict) else {}
        if not events or not pagination.get("hasMore") or len(found) == before:
            break
    audit_row.update({
        "event_count": len(found), "page_sizes": pages,
        "has_more_after_limit": bool(pages and len(pages) == 20),
        "reported_total": pagination.get("totalResults") if isinstance(data, dict) else None,
    })
    return list(found.values())


def event_id(event: dict[str, Any]) -> str | None:
    value = event.get("id")
    return str(value) if value is not None else None


def fetch_event_details(events: dict[str, dict[str, Any]], audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ids = sorted(events, key=lambda x: (int(x) if x.isdigit() else 0, x))
    details: dict[str, dict[str, Any]] = {}
    errors = []
    def fetch_one(eid: str) -> tuple[str, Any]:
        try:
            return eid, get_json(f"/events/{eid}")
        except Exception as exc:
            return eid, {"_fetch_error": f"{type(exc).__name__}: {exc}"[:300]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_one, eid) for eid in ids]
        for future in as_completed(futures):
            eid, result = future.result()
            if isinstance(result, dict) and not result.get("_fetch_error"):
                details[eid] = result
            else:
                errors.append({"event_id": eid, "error": result.get("_fetch_error", "unexpected response")})
    audit["event_detail_fetch"] = {"requested": len(ids), "received": len(details), "errors": errors}
    return details


def flat_text(event: dict[str, Any], market: dict[str, Any]) -> str:
    values = []
    for obj in (event, market):
        for key in (
            "title", "question", "slug", "description", "resolutionSource", "resolution_source",
            "resolutionCriteria", "resolution_criteria", "groupItemTitle", "subtitle",
        ):
            value = obj.get(key)
            if value:
                values.append(str(value))
        tags = obj.get("tags") or []
        if isinstance(tags, list):
            values.extend(str(t.get("label") or t.get("slug") or "") if isinstance(t, dict) else str(t) for t in tags)
    return " ".join(values)


def relevant_event(event: dict[str, Any], seed_type: str, direct_event_ids: set[str], states: list[str]) -> bool:
    eid = str(event.get("id") or "")
    if eid in direct_event_ids:
        return True
    text = flat_text(event, {})
    year = "2026" in text or str(event.get("endDate") or "").startswith("2026") or str(event.get("startDate") or "").startswith("2026")
    election = bool(ELECTION_RE.search(text))
    state_hit = any(re.search(rf"\b{re.escape(s)}\b", text, re.I) for s in states)
    us_context = bool(re.search(r"\b(u\.?s\.?|united states|congress|house|senate|governor|midterm)\b", text, re.I))
    seeded_search = seed_type == "STATE_OR_ELECTION_SEARCH"
    return election and (year or state_hit or seeded_search) and (us_context or state_hit or seeded_search)


def market_lists(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets = event.get("markets") or []
    if isinstance(markets, list):
        return [m for m in markets if isinstance(m, dict)]
    return []


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def flatten_market(event: dict[str, Any], market: dict[str, Any], methods: list[str]) -> dict[str, Any]:
    outcomes = parse_list(market.get("outcomes"))
    tokens = parse_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    if not tokens and isinstance(market.get("tokens"), list):
        tokens = [str(t.get("token_id") or t.get("tokenId")) for t in market["tokens"] if isinstance(t, dict) and (t.get("token_id") or t.get("tokenId"))]
        if not outcomes:
            outcomes = [str(t.get("outcome") or t.get("name") or "") for t in market["tokens"] if isinstance(t, dict)]
    market_id = market.get("id")
    condition = market.get("conditionId") or market.get("condition_id")
    identity_status = "VERIFIED" if market_id and condition and len(outcomes) >= 2 and len(outcomes) == len(tokens) and len(set(tokens)) == len(tokens) else "UNRESOLVED"
    return {
        "event_id": str(event.get("id") or market.get("eventId") or ""),
        "market_id": str(market_id or ""), "condition_id": str(condition or ""),
        "event_slug": event.get("slug", ""), "event_title": event.get("title", ""),
        "slug": market.get("slug", ""), "question": market.get("question") or market.get("title", ""),
        "description": market.get("description", ""),
        "outcomes": json.dumps(outcomes, separators=(",", ":")),
        "clob_token_ids": json.dumps(tokens, separators=(",", ":")),
        "start_date": market.get("startDate") or market.get("startDateIso") or event.get("startDate", ""),
        "end_date": market.get("endDate") or event.get("endDate", ""),
        "created_at": market.get("createdAt") or event.get("createdAt", ""),
        "active": market.get("active", event.get("active", "")),
        "closed": market.get("closed", event.get("closed", "")),
        "resolved": market.get("resolved", event.get("resolved", "")),
        "resolution_time": market.get("resolutionTime") or market.get("resolvedAt") or event.get("resolvedAt", ""),
        "resolution_source": market.get("resolutionSource") or market.get("resolution_source") or event.get("resolutionSource", ""),
        "resolution_criteria": market.get("resolutionCriteria") or market.get("resolution_criteria") or "",
        "tags": json.dumps(market.get("tags") or event.get("tags") or [], separators=(",", ":")),
        "discovery_methods": json.dumps(sorted(set(methods)), separators=(",", ":")),
        "identity_status": identity_status,
        "market_type": market.get("marketType") or market.get("market_type") or "",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mapping_file = mapping_path()
    mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
    accepted = [r for r in mapping["records"] if r.get("mapping_class") in {"EXACT", "DERIVED", "NEAR"} and r.get("status") == "VERIFIED"]
    direct_market_ids: set[str] = set()
    direct_cids: set[str] = set()
    direct_event_ids: set[str] = set()
    for record in accepted:
        for component in [record.get("direct_polymarket")] + (record.get("polymarket_components") or []):
            if not isinstance(component, dict):
                continue
            for field, dest in (("market_id", direct_market_ids), ("condition_id", direct_cids), ("event_id", direct_event_ids)):
                if component.get(field):
                    dest.add(str(component[field]))

    audit: dict[str, Any] = {
        "kernel": "polyleviathan/r2-5-ets-universe-discovery",
        "api_base": API,
        "api_docs": "https://docs.polymarket.com/market-data/discover-markets",
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "canonical_mapping_sha256": sha256(mapping_file),
        "canonical_mapping_path_in_kernel": str(mapping_file),
        "sig_anchor_count": len(accepted), "direct_market_count": len(direct_market_ids),
        "direct_cid_count": len(direct_cids), "direct_token_count": len({
            str(t) for r in accepted for c in ([r.get("direct_polymarket")] + (r.get("polymarket_components") or []))
            if isinstance(c, dict) for t in (c.get("token_ids") or []) if t
        }),
        "discovery_method": [
            "canonical anchor event membership", "Gamma event tags and one-hop related tags",
            "Gamma public-search chamber/seat/balance/joint/state queries", "event membership snapshots",
        ],
    }

    tags = fetch_tags(audit)
    tagged = relevant_tags(tags)
    audit["relevant_tags"] = [{"id": t.get("id"), "slug": t.get("slug"), "label": t.get("label")} for t in tagged]
    audit["related_tag_fetch_errors"] = getattr(relevant_tags, "last_errors", [])
    states = anchor_states(mapping)
    audit["anchor_state_names"] = states

    event_seeds: dict[str, dict[str, Any]] = {}
    for eid in direct_event_ids:
        event_seeds[eid] = {"id": eid, "_seed_methods": ["CANONICAL_DIRECT_ANCHOR_EVENT"]}
    tag_audit = []
    for tag in tagged:
        tid = str(tag["id"])
        for closed in (False, True):
            entry: dict[str, Any] = {"tag_id": tid, "tag_slug": tag.get("slug"), "tag_label": tag.get("label")}
            try:
                for event in page_events_for_tag(tid, closed, entry):
                    eid = event_id(event)
                    if eid:
                        prior = event_seeds.setdefault(eid, {**event, "_seed_methods": []})
                        prior["_seed_methods"].append(f"TAG:{tid}:{'closed' if closed else 'open'}")
            except Exception as exc:
                entry.update({"error": f"{type(exc).__name__}: {exc}"[:300]})
            tag_audit.append(entry)
    audit["tag_event_queries"] = tag_audit

    search_audit = []
    for term in search_terms(states):
        entry = {"query": term}
        try:
            for event in search_events(term, entry):
                eid = event_id(event)
                if eid:
                    prior = event_seeds.setdefault(eid, {**event, "_seed_methods": []})
                    prior["_seed_methods"].append("STATE_OR_ELECTION_SEARCH:" + term)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        search_audit.append(entry)
    audit["public_search_queries"] = search_audit
    audit["unique_event_seeds"] = len(event_seeds)

    details = fetch_event_details(event_seeds, audit)
    candidate_events: dict[str, dict[str, Any]] = {}
    market_methods: dict[str, set[str]] = {}
    for eid, event in details.items():
        methods = event_seeds[eid].get("_seed_methods", [])
        is_direct_event = eid in direct_event_ids
        if not relevant_event(event, "STATE_OR_ELECTION_SEARCH" if any(x.startswith("STATE_OR_ELECTION_SEARCH:") for x in methods) else "TAG", direct_event_ids, states):
            continue
        candidate_events[eid] = {"event": event, "discovery_methods": sorted(set(methods))}
        for market in market_lists(event):
            mid = str(market.get("id") or "")
            cid = str(market.get("conditionId") or market.get("condition_id") or "")
            if not mid:
                continue
            # Keep every member of a canonical direct event. In other events, a market itself
            # must expose election semantics; unrelated sibling contracts are not silently added.
            if is_direct_event or ELECTION_RE.search(flat_text(event, market)):
                key = mid
                market_methods.setdefault(key, set()).update(methods)

    # Include canonical direct markets if an event detail temporarily omits one of its children;
    # the review ledger can then label the exact identity as an existing direct mapping.
    for record in accepted:
        for component in [record.get("direct_polymarket")] + (record.get("polymarket_components") or []):
            if isinstance(component, dict) and component.get("market_id"):
                market_methods.setdefault(str(component["market_id"]), set()).add("CANONICAL_DIRECT_MAPPING")

    event_by_market: dict[str, dict[str, Any]] = {}
    rows: dict[str, dict[str, Any]] = {}
    for eid, record in candidate_events.items():
        event = record["event"]
        for market in market_lists(event):
            mid = str(market.get("id") or "")
            if mid in market_methods:
                event_by_market[mid] = event
                rows[mid] = flatten_market(event, market, sorted(market_methods[mid]))
    # Preserve canonical direct identities even where an event endpoint failed. The crosswalk
    # provides identity and orientation; lifecycle fields remain empty until Gamma confirms them.
    for record in accepted:
        for component in [record.get("direct_polymarket")] + (record.get("polymarket_components") or []):
            if not isinstance(component, dict) or not component.get("market_id"):
                continue
            mid = str(component["market_id"])
            if mid not in rows:
                rows[mid] = {
                    "event_id": str(component.get("event_id") or ""), "market_id": mid,
                    "condition_id": str(component.get("condition_id") or ""), "event_slug": "",
                    "event_title": "", "slug": component.get("slug", ""), "question": component.get("question", ""),
                    "description": "", "outcomes": json.dumps(component.get("outcomes") or []),
                    "clob_token_ids": json.dumps(component.get("token_ids") or []), "start_date": "", "end_date": "",
                    "created_at": "", "active": "", "closed": "", "resolved": "", "resolution_time": "",
                    "resolution_source": "", "resolution_criteria": "", "tags": "[]",
                    "discovery_methods": '["CANONICAL_DIRECT_MAPPING"]',
                    "identity_status": "VERIFIED" if component.get("condition_id") and len(component.get("token_ids") or []) == len(component.get("outcomes") or []) else "UNRESOLVED",
                    "market_type": "",
                }

    columns = [
        "event_id", "market_id", "condition_id", "event_slug", "event_title", "slug", "question",
        "description", "outcomes", "clob_token_ids", "start_date", "end_date", "created_at", "active",
        "closed", "resolved", "resolution_time", "resolution_source", "resolution_criteria", "tags",
        "discovery_methods", "identity_status", "market_type",
    ]
    candidate_rows = [rows[k] for k in sorted(rows, key=lambda x: (int(x) if x.isdigit() else 0, x))]
    with (OUT / "ETS_CANDIDATES.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(candidate_rows)
    snapshot = {
        "schema_version": 1,
        "metadata_source": API,
        "captured_at": audit["fetched_at"],
        "canonical_mapping_sha256": audit["canonical_mapping_sha256"],
        "events": [candidate_events[k] for k in sorted(candidate_events, key=lambda x: (int(x) if x.isdigit() else 0, x))],
    }
    (OUT / "ETS_DISCOVERY_EVENTS.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    audit["candidate_market_count_including_direct"] = len(candidate_rows)
    audit["candidate_market_count_excluding_direct"] = sum(1 for r in candidate_rows if r["market_id"] not in direct_market_ids and r["condition_id"] not in direct_cids)
    audit["candidate_event_count"] = len(candidate_events)
    audit["unresolved_identity_count"] = sum(r["identity_status"] != "VERIFIED" for r in candidate_rows)
    audit["candidates_sha256"] = sha256(OUT / "ETS_CANDIDATES.csv")
    audit["event_snapshot_sha256"] = sha256(OUT / "ETS_DISCOVERY_EVENTS.json")
    (OUT / "ETS_DISCOVERY_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "anchor_count": len(accepted), "direct_market_count": len(direct_market_ids),
        "candidate_market_count_including_direct": len(candidate_rows),
        "candidate_market_count_excluding_direct": audit["candidate_market_count_excluding_direct"],
        "candidate_event_count": len(candidate_events),
        "unresolved_identity_count": audit["unresolved_identity_count"],
        "outputs": sorted(p.name for p in OUT.iterdir()),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
