#!/usr/bin/env python3
"""Cache Gamma public-search results for the election families in the campaign brief."""
from __future__ import annotations
import json, re, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "gamma_cache"
OUT = ROOT / "gamma_events.json"
QUERIES = [
    "Colombia 2026 presidential election",
    "Peru 2026 presidential election",
    "Peru 2026 election",
    "Hungary 2026 parliamentary election",
    "Hungary 2026 election",
    "2024 US presidential election",
    "2024 US Senate election",
    "2024 US House election",
    "2024 US election balance of power",
    "2024 US election electoral votes popular vote",
    "2024 Republican House seats election",
    "2024 Democratic House seats election",
    "2024 Senate control election",
    "2024 House control election seat count",
    "Republican House seats after 2024 election",
    "Democratic House seats after 2024 election",
    "How many Republican House seats after 2024 election",
    "How many Democratic House seats after 2024 election",
    "2024 House of Representatives seat count brackets",
    "2024 Republican House seats bracket election",
    "2024 Democratic House seats bracket election",
    "2024 US House seat count market",
    "Democratic Senate seats after 2024 election",
    "2024 Senate seat count election",
    "2024 presidential Senate House balance of power",
    "2024 divided government trifecta election",
    "2024 House election battleground district winner",
    "Arizona Presidential Election Winner 2024",
    "Georgia Presidential Election Winner 2024",
    "Michigan Presidential Election Winner 2024",
    "Nevada Presidential Election Winner 2024",
    "North Carolina Presidential Election Winner 2024",
    "Pennsylvania Presidential Election Winner 2024",
    "Wisconsin Presidential Election Winner 2024",
    "Kari Lake Ruben Gallego Arizona Senate 2024",
    "Sherrod Brown Bernie Moreno Ohio Senate 2024",
    "Bob Casey Dave McCormick Pennsylvania Senate 2024",
    "Elissa Slotkin Mike Rogers Michigan Senate 2024",
    "Tammy Baldwin Eric Hovde Wisconsin Senate 2024",
    "Jon Tester Tim Sheehy Montana Senate 2024",
    "Angus King Maine Senate election 2024",
    "Jacky Rosen Sam Brown Nevada Senate 2024",
    "Rick Scott Debbie Mucarsel Powell Florida Senate 2024",
    "Ted Cruz Colin Allred Texas Senate 2024",
    "Larry Hogan Angela Alsobrooks Maryland Senate 2024",
    "Andy Kim Curtis Bashaw New Jersey Senate 2024",
    "Deb Fischer Dan Osborn Nebraska Senate 2024",
    "2024 House of Representatives battleground races candidates",
    "2024 House election swing districts winner",
    "2024 congressional district election winner market",
    "2024 Michigan Senate election winner",
    "Michigan US Senate race winner election 2024",
    "Pennsylvania Senate election winner 2024",
    "Pennsylvania US Senate race election 2024",
    "Will a Democrat win Michigan US Senate election 2024",
    "Will a Democrat win Pennsylvania US Senate election 2024",
    "Republican seat count House 2024 election",
    "Democratic seat count House 2024 election",
    "Virginia Governor election 2025",
    "New Jersey Governor election 2025",
    "Virginia House of Delegates election 2025",
    "Virginia New Jersey 2025 election sweep",
    "2025 Virginia governor margin of victory",
    "2025 New Jersey governor margin of victory",
    "2025 Virginia House of Delegates party control seats",
    "2025 Virginia New Jersey governor sweep",
    "Canada federal election 2025",
    "Canada Liberal Conservative seats 2025 election",
    "Canada majority minority government 2025 election",
    "Canadian election 2025 Liberal seats",
    "Canadian election 2025 Conservative seats",
    "Canadian federal election 2025 popular vote winner",
    "Canadian 2025 federal election riding winner",
    "Canadian election 2025 constituency candidate",
    "Canada 2025 federal election riding winner candidate",
    "Canadian election most votes popular vote 2025",
    "Germany federal election 2025",
    "Germany Bundestag coalition election 2025",
    "Germany federal election 2025 party vote share",
    "Germany federal election 2025 party seat count",
    "Germany federal election 2025 chancellor coalition",
    "Germany election 2025 majority seats",
    "Who will win the German federal election 2025 most seats",
    "CDU AfD SPD Greens Bundestag seats 2025 election",
    "Germany 2025 Bundestag election vote share bins",
    "Which parties form next government Germany 2025 election",
    "Australia federal election 2025",
    "Australia 2025 federal election Labor seats",
    "Australia 2025 federal election Coalition seats",
    "Australia federal election 2025 majority minority government",
    "Australia 2025 electorate election winner",
    "Australian federal election 2025 Labor Coalition seats",
    "2025 Australian election seat count brackets",
    "2025 Australian federal election popular vote",
    "Poland presidential election 2025",
    "Poland presidential election first round winner 2025",
    "Poland presidential election runoff 2025",
    "Poland presidential election candidate vote share 2025",
    "Karol Nawrocki Rafal Trzaskowski Poland election runoff 2025",
    "Bolivia presidential election 2025",
    "Bolivia presidential election first round winner 2025",
    "Bolivia presidential election runoff 2025",
    "Bolivia presidential election candidate vote share 2025",
    "Rodrigo Paz Jorge Quiroga Bolivia presidential runoff 2025",
    "Ecuador presidential election 2025",
    "Ecuador presidential election outright first round win 2025",
    "Ecuador presidential election runoff 2025",
    "Ecuador presidential candidate vote share 2025",
    "Daniel Noboa Luisa Gonzalez Ecuador election runoff 2025",
    "Chile presidential election 2025",
    "Japan House of Councillors election 2025",
    "Norway parliamentary election 2025",
    "Norwegian election 2025 party winner seats",
    "Norway parliamentary election 2025 seat count government",
    "Netherlands parliamentary election 2025",
    "Netherlands 2025 election government coalition majority",
    "Dutch parliamentary election 2025 popular vote winner",
    "Czech Republic parliamentary election 2025",
    "Czech election 2025 party seats coalition government",
    "Hungary 2026 TISZA seat count",
    "Hungary Fidesz KDNP seats 2026 election",
    "Hungary party most seats 2026 election",
    "Hungary 2026 coalition government election",
    "TISZA seat count brackets Hungary election",
    "Hungary Tisza 70 79 seats parliamentary election",
    "Colombia 2026 Senate election",
    "Colombia 2026 Chamber of Representatives election",
    "Peru 2026 Congress seat count election",
    "Peru 2026 parliamentary seat election",
    "German Bundestag election winner 2025",
    "CDU Bundestag seats 2025 election",
    "Australian federal election winner 2025",
    "Australian federal election 2025 parliamentary seats",
    "Chile presidential election overall winner 2025",
    "Chile presidential election runoff winner 2025",
    "Poland presidential runoff candidates pair 2025",
    "Bolivia presidential election overall winner 2025",
    "Norway party with most seats 2025 election",
    "Colombia Chamber of Representatives first place 2026 election winner",
    "Colombia Senate election party most seats 2026",
    "Pacto Historico Senate seats Colombia 2026 election",
    "Colombia congressional election 2026 candidate winner",
    "Peru 2026 Senate election seat count",
    "Chile Presidential Election Runoff Winner 2025",
    "Chile presidential election second round winner December 2025",
    "Which party wins most seats Netherlands election 2025",
    "Netherlands parliamentary election popular vote winner 2025",
    "Netherlands next government after 2025 election",
    "Norway Parliamentary Election Winner 2025",
    "Germany Federal Election Winner 2025 party most votes",
    "Canada Next Government after 2025 election",
    "Liberal Conservative seat count Canada election 2025",
    "Australia Coalition seats 2025 federal election",
    "Bolivia presidential election winner second round 2025",
    "Ecuador election first round winner 2025",
    "Republican House seat bins 2024 election US",
    "How many seats will Republicans win in the House 2024",
    "How many seats will Democrats win in the House 2024",
]
SENATE_STATES_2024 = [
    "Alabama", "Alaska", "Arizona", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Jersey", "New Mexico", "New York", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
]
QUERIES.extend(f"{state} US Senate election winner 2024" for state in SENATE_STATES_2024)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:90]


def save(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def request_page(session: requests.Session, query: str, page: int) -> dict:
    path = CACHE / f"{safe_name(query)}_p{page:03}.json"
    if path.exists():
        return json.loads(path.read_text())
    if getattr(request_page, "last_request", 0):
        wait = 0.65 - (time.monotonic() - request_page.last_request)
        if wait > 0:
            time.sleep(wait)
    response = session.get(
        "https://gamma-api.polymarket.com/public-search",
        params={"q": query, "limit": 100, "page": page},
        headers=HEADERS,
        timeout=40,
    )
    request_page.last_request = time.monotonic()
    response.raise_for_status()
    payload = response.json()
    save(path, payload)
    print(f"cached query={query!r} page={page} events={len(payload.get('events', []))}", flush=True)
    return payload


def request_direct(session: requests.Session, endpoint: str, params: dict, cache_name: str):
    path = CACHE / cache_name
    if path.exists():
        return json.loads(path.read_text())
    if getattr(request_page, "last_request", 0):
        wait = 0.65 - (time.monotonic() - request_page.last_request)
        if wait > 0:
            time.sleep(wait)
    response = session.get(
        f"https://gamma-api.polymarket.com/{endpoint}",
        params=params,
        headers=HEADERS,
        timeout=40,
    )
    request_page.last_request = time.monotonic()
    response.raise_for_status()
    payload = response.json()
    save(path, payload)
    print(f"cached direct={endpoint} params={params} items={len(payload) if isinstance(payload, list) else 1}", flush=True)
    return payload


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    seen: dict[str, dict] = {}
    provenance: dict[str, set[str]] = {}
    direct_markets = []
    for query in QUERIES:
        first = request_page(session, query, 1)
        events = first.get("events", [])
        total = first.get("pagination", {}).get("totalResults")
        page_size = max(1, len(events))
        pages = min(50, max(1, (int(total or page_size) + page_size - 1) // page_size))
        for page in range(1, pages + 1):
            payload = first if page == 1 else request_page(session, query, page)
            for event in payload.get("events", []):
                event_id = str(event.get("id") or "")
                if not event_id:
                    continue
                if event_id not in seen:
                    seen[event_id] = event
                else:
                    prior = seen[event_id]
                    markets = {str(m.get("id")): m for m in prior.get("markets", []) if m.get("id")}
                    for market in event.get("markets", []):
                        if market.get("id"):
                            markets.setdefault(str(market["id"]), market)
                    prior["markets"] = list(markets.values())
                provenance.setdefault(event_id, set()).add(query)
    for endpoint, params, cache_name in [
        ("events", {"id": 263762}, "direct_event_263762.json"),
        ("events", {"id": 246787}, "direct_event_246787.json"),
        ("markets/948038", {}, "direct_market_path_948038.json"),
        ("markets/1570162", {}, "direct_market_path_1570162.json"),
    ]:
        payload = request_direct(session, endpoint, params, cache_name)
        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if endpoint.startswith("markets"):
                direct_markets.append(record)
                continue
            event_id = str(record.get("id") or "")
            if not event_id:
                continue
            seen.setdefault(event_id, record)
            provenance.setdefault(event_id, set()).add(f"direct:{endpoint}:{params['id']}")
    output = {
        "source": "https://gamma-api.polymarket.com/public-search",
        "retrieved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query_count": len(QUERIES),
        "event_count": len(seen),
        "direct_markets": direct_markets,
        "events": [
            {**seen[event_id], "_search_queries": sorted(provenance[event_id])}
            for event_id in sorted(seen, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else x))
        ],
    }
    save(OUT, output)
    print(f"wrote {OUT.name}: {len(seen)} unique events")


if __name__ == "__main__":
    main()
