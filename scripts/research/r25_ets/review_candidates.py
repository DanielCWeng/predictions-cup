#!/usr/bin/env python3
"""Semantically review Gamma ETS candidates against the frozen SIG anchor layer.

This review is deliberately about settlement meaning and shared election state. Gamma
tags are discovery evidence, but they never accept a contract by themselves. Every accepted
contract must have a verified CLOB identity, a usable resolution description, a U.S. 2026
election target, and a relationship to one or more actual SIG anchors.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ABBREVIATIONS = {v: k for k, v in STATES.items()}
PARTY_PATTERNS = {
    "DEMOCRATIC": re.compile(r"\b(democratic party|democrats?|democratic nominee|democratic candidate)\b", re.I),
    "REPUBLICAN": re.compile(r"\b(republican party|republicans?|republican nominee|republican candidate)\b", re.I),
    "INDEPENDENT": re.compile(r"\b(independent party|independents?)\b", re.I),
}
CHAMBER_CONTROL = re.compile(r"\b(control|controls|majority|flip|hold|wins?|win)\b", re.I)
SEAT_COUNT = re.compile(r"\b(seat|seats|seat total|seat count|majority size|supermajority)\b", re.I)
SEAT_RANGE = re.compile(r"\b(between|range|bucket|bracket|from .+ to)\b|\d+\s*(?:-|–|to|through|or)\s*\d+", re.I)
SEAT_THRESHOLD = re.compile(r"\b(at least|at most|more than|fewer than|less than|over|under|or more|or fewer|threshold)\b|(?:<=|>=|≤|≥)", re.I)
MULTI_MARKERS = re.compile(r"\b(both|joint|balance of power|trifecta|unified|split control|combination|combo|all of|at least .+ races|and)\b", re.I)
PIVOTAL_MARKERS = re.compile(r"\b(closest|toss.?up|within \d+(?:\.\d+)?%?|key race|pivotal|swing seat)\b", re.I)
PRIMARY_MARKERS = re.compile(r"\b(primary|runoff|nominee|nomination|advance|first round|second round|announce.{0,30}run|run.{0,30}for)\b", re.I)
PRE_CAMPAIGN_MARKERS = re.compile(r"\b(announce|announces|announced|run for|running for|enter the race|file for|candidate for)\b", re.I)
LOCAL_OFFICE_MARKERS = re.compile(r"\b(mayor|mayoral|city council|county judge|county commissioner|sheriff|school board)\b", re.I)
STATE_OFFICE_MARKERS = re.compile(
    r"\b(attorney general|secretary of state|lieutenant governor|state treasurer|state auditor|"
    r"state legislature|state senate|state house|state assembly)\b", re.I,
)
LEGISLATIVE_POLICY_MARKERS = re.compile(
    r"\b(pass|passes|passed|sign|signs|signed|veto|vetoes|enact|enacted)\b.{0,80}"
    r"\b(bill|act|law|funding|appropriation|war powers|legislation)\b|"
    r"\b(bill|act|law|funding|appropriation|war powers|legislation)\b.{0,80}"
    r"\b(pass|passes|passed|sign|signs|signed|veto|vetoes|enact|enacted)\b", re.I,
)
NONVOTING_DELEGATE_MARKERS = re.compile(r"\b(dc|district of columbia|puerto rico|guam|delegate)\b", re.I)
PRESIDENTIAL_MARKERS = re.compile(r"\b(presidential|president of the united states|president nominee|president primary)\b", re.I)
ELECTION_MARKERS = re.compile(
    r"\b(election|midterm|primary|nominee|district|race|seat|chamber|governor|senate|house|"
    r"ballot|referendum|turnout|party|candidate|congress|majority|speaker)\b", re.I,
)
YEAR_MARKER = re.compile(r"\b(202[3-9]|2030)\b")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def anchor_index(mapping: dict[str, Any]) -> dict[str, Any]:
    anchors = [
        r for r in mapping["records"]
        if r.get("mapping_class") in {"EXACT", "DERIVED", "NEAR"} and r.get("status") == "VERIFIED"
    ]
    controls: dict[str, dict[str, str]] = {"HOUSE": {}, "SENATE": {}}
    house_by_race: dict[tuple[str, str], dict[str, str]] = {}
    senate_by_state: dict[str, dict[str, str]] = {}
    governor_by_state: dict[str, dict[str, str]] = {}
    other_by_state: dict[str, list[str]] = {}
    direct_markets: set[str] = set()
    direct_cids: set[str] = set()
    direct_events: set[str] = set()

    for record in anchors:
        title = str(record.get("sig_market_title") or "")
        sig_id = str(record.get("sig_market_id") or "")
        lower = title.lower()
        party = "DEMOCRATIC" if "democratic party" in lower else (
            "REPUBLICAN" if "republican party" in lower else (
                "INDEPENDENT" if "independent party" in lower else "OTHER"
            )
        )
        if "u.s. house" in lower:
            controls["HOUSE"][party] = sig_id
        elif "u.s. senate" in lower:
            controls["SENATE"][party] = sig_id
        else:
            district = re.search(r"\b([A-Z]{2})-(\d{1,2})\s+House race\b", title, re.I)
            if district:
                state = ABBREVIATIONS.get(district.group(1).upper())
                if state:
                    house_by_race.setdefault((state, district.group(2)), {})[party] = sig_id
                    other_by_state.setdefault(state, []).append(sig_id)
                    continue
            state_match = next((s for s in sorted(STATES, key=len, reverse=True) if re.search(rf"\b{re.escape(s)}\b", title, re.I)), None)
            if state_match and "governor" in lower:
                governor_by_state.setdefault(state_match, {})[party] = sig_id
                other_by_state.setdefault(state_match, []).append(sig_id)
            elif state_match and "senate" in lower:
                senate_by_state.setdefault(state_match, {})[party] = sig_id
                other_by_state.setdefault(state_match, []).append(sig_id)

        components = [record.get("direct_polymarket")] + (record.get("polymarket_components") or [])
        for component in components:
            if not isinstance(component, dict):
                continue
            if component.get("market_id"):
                direct_markets.add(str(component["market_id"]))
            if component.get("condition_id"):
                direct_cids.add(str(component["condition_id"]))
            if component.get("event_id"):
                direct_events.add(str(component["event_id"]))

    return {
        "anchors": anchors,
        "controls": controls,
        "house_by_race": house_by_race,
        "senate_by_state": senate_by_state,
        "governor_by_state": governor_by_state,
        "other_by_state": other_by_state,
        "direct_markets": direct_markets,
        "direct_cids": direct_cids,
        "direct_events": direct_events,
    }


def tag_labels(row: dict[str, str]) -> set[str]:
    return {
        str(x.get("label") or x.get("slug") or "") if isinstance(x, dict) else str(x)
        for x in parse_json_list(row.get("tags"))
    }


def extract_states(text: str, labels: set[str]) -> list[str]:
    # A global-election tag and a foreign place name override a bare state-name match
    # unless Gamma also carries a U.S./state-midterm tag or the question says U.S.
    lower = text.lower()
    state_tags = {f"{s} midterm" for s in STATES}
    explicit_us = bool(re.search(r"\b(united states|u\.?s\.?|us house|us senate|us congress|american)\b", text, re.I))
    lower_labels = {x.lower() for x in labels}
    us_tag = bool({"us election", "united states", "us politics", "u.s. politics", "midterms"} & lower_labels)
    us_tag = us_tag or bool({x.lower() for x in state_tags} & lower_labels)
    matches: list[tuple[int, int, str]] = []
    for s in sorted(STATES, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(s)}\b", text, re.I):
            if not any(m.start() < end and m.end() > start for start, end, _ in matches):
                matches.append((m.start(), m.end(), s))
    found = [s for _, _, s in sorted(matches)]
    if re.search(r"\bbaja california(?: sur)?\b", lower):
        found = [s for s in found if s not in {"california"}]
    if "global elections" in lower_labels and not (explicit_us or state_tag_present(lower_labels)):
        return []
    if not (explicit_us or us_tag):
        return []
    return list(dict.fromkeys(found))


def explicit_multi_state_scope(text: str, expected_states: list[str]) -> bool:
    """Require a combination label or a conjunction between distinct state mentions."""
    if len(expected_states) < 2:
        return False
    if re.search(r"\b(closer|three.?way|combo|combination|multi.?state|compare)\b", text, re.I):
        return True
    mentions: list[tuple[int, int, str]] = []
    for state_name in sorted(expected_states, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(state_name)}\b", text, re.I):
            if not any(match.start() < end and match.end() > start for start, end, _ in mentions):
                mentions.append((match.start(), match.end(), state_name))
    mentions.sort()
    for left, right in zip(mentions, mentions[1:]):
        if left[2] != right[2] and re.search(r"\b(and|or|versus|vs\.?)\b", text[left[1]:right[0]], re.I):
            return True
    return False


def state_tag_present(lower_labels: set[str]) -> bool:
    return bool({f"{s} midterm" for s in STATES} & lower_labels)


def extract_district(text: str) -> tuple[str, str] | None:
    at_large = re.search(r"\b([A-Z]{2})[- ]AL\b", text, re.I)
    if at_large:
        state = ABBREVIATIONS.get(at_large.group(1).upper())
        return (state, "AL") if state else None
    m = re.search(r"\b([A-Z]{2})[- ](\d{1,2})\b", text, re.I)
    if not m:
        return None
    state = ABBREVIATIONS.get(m.group(1).upper())
    return (state, str(int(m.group(2)))) if state else None


def party_in_text(text: str) -> str | None:
    for party, pattern in PARTY_PATTERNS.items():
        if pattern.search(text):
            return party
    return None


def party_targets(targets: dict[str, str], party: str | None) -> list[str]:
    if party and party in targets:
        return [targets[party]]
    return [targets[p] for p in sorted(targets)]


def target_year_status(row: dict[str, str], text: str, scope: str, labels: set[str], state: str | None, district: tuple[str, str] | None, index: dict[str, Any]) -> tuple[bool, str]:
    """Require a 2026 election, an after-midterms outcome, or a 2026 candidacy signal."""
    event_id = str(row.get("event_id") or "")
    q_scope = " ".join(row.get(k, "") or "" for k in ("event_title", "question"))
    description = " ".join(row.get(k, "") or "" for k in ("description", "resolution_criteria"))
    is_presidential = bool(PRESIDENTIAL_MARKERS.search(q_scope))
    if is_presidential:
        return False, "future presidential primary/election is not the 2026 House, Senate, or governor state represented by the SIG anchors"

    target_2026 = bool(re.search(r"\b2026\b", q_scope, re.I))
    target_2026 = target_2026 or bool(re.search(
        r"2026.{0,100}(election|midterm|primary|race|seat|governor|senate|congress)|"
        r"(election|midterm|primary|race|seat|governor|senate|congress).{0,100}2026", description, re.I,
    ))
    after_midterms = bool(
        "Midterms" in labels
        and re.search(r"after (the )?midterms|following (the )?midterms|after midterm", q_scope, re.I)
        and (row.get("end_date", "")[:4] in {"2026", "2027"} or "2026" in description)
    )
    if after_midterms:
        target_2026 = True

    pre_campaign = bool(PRE_CAMPAIGN_MARKERS.search(q_scope))
    pre_campaign_2026 = bool(
        pre_campaign
        and re.search(r"\b2026\b|2026 midterm|2026 election", description, re.I)
        and (
            (state and (
                ("governor" in q_scope.lower() and state in index["governor_by_state"])
                or ("senate" in q_scope.lower() and state in index["senate_by_state"])
            ))
            or district
        )
    )
    if pre_campaign and not pre_campaign_2026 and re.search(r"\b(governor|senate|house|congress)\b", q_scope, re.I):
        return False, "the candidate announcement is not tied to a mapped 2026 office-specific SIG race anchor"
    if pre_campaign_2026:
        target_2026 = True

    # An explicit different contest year wins over a generic 2026 listing tag. Keep
    # pre-campaign questions only when Gamma says the intended race is the 2026 cycle.
    election_years = {y for y in YEAR_MARKER.findall(q_scope)}
    if election_years and "2026" not in election_years:
        if not pre_campaign_2026:
            year = sorted(election_years)[0]
            return False, f"question resolves on a {year} election/campaign cycle, outside the 2026 SIG universe"
    if target_2026:
        return True, "Gamma question/description or 2026 Midterms context identifies a 2026 contest or its post-election state"

    # A current primary or runoff may omit the year from individual candidate questions.
    # Accept only when the Gamma end date is within the 2026 election resolution period,
    # U.S. election tags are present, and the market is about a mapped state/chamber race.
    current_cycle = (
        row.get("end_date", "")[:4] in {"2026", "2027"}
        and bool({"US Election", "Midterms", "United States"} & labels)
        and bool(state or district or re.search(r"\b(us|u\.s\.|united states)\b", q_scope, re.I))
        and bool(re.search(r"\bmidterms?\b", q_scope, re.I))
        and bool(re.search(r"election|primary|runoff|nominee|margin|turnout|race|seats?", text, re.I))
    )
    if current_cycle:
        return True, "Gamma contest description, U.S. election tags, and 2026/27 resolution date identify a current 2026 cycle race"

    return False, "no 2026 target election or 2026 midterm relationship is established by Gamma question, description, tags, and dates"


def edge(market_id: str, sig_id: str, relation: str, direction: str, rationale: str, confidence: str) -> dict[str, str]:
    return {
        "market_id": market_id,
        "sig_market_id": sig_id,
        "review_status": "SEMANTIC_REVIEWED_ACCEPTED",
        "relationship_class": relation,
        "relationship_direction": direction,
        "economic_rationale": rationale,
        "confidence": confidence,
        "rejection_reason": "",
    }


def reject(market_id: str, reason: str) -> dict[str, str]:
    return {
        "market_id": market_id,
        "sig_market_id": "",
        "review_status": "REJECTED",
        "relationship_class": "",
        "relationship_direction": "",
        "economic_rationale": "",
        "confidence": "",
        "rejection_reason": reason,
    }


def review_candidate(row: dict[str, str], index: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    market_id = str(row.get("market_id") or "").strip()
    cid = str(row.get("condition_id") or "").strip()
    if market_id in index["direct_markets"] or cid in index["direct_cids"]:
        return "DIRECT_DUPLICATE", []

    if row.get("identity_status") != "VERIFIED" or not cid:
        if re.search(r"congressional map|redistrict|district map", str(row.get("question") or ""), re.I):
            reason = (
                "Gamma resolution describes a 2026 U.S. congressional-map outcome that could affect House seat distributions, "
                "but live Gamma still returns no condition ID or CLOB token IDs; the identity gate prevents acceptance."
            )
        else:
            reason = "Gamma candidate has no verified condition ID and outcome/token alignment; it cannot enter the tradable ETS freeze."
        return "REJECTED_UNVERIFIED_IDENTITY", [reject(
            market_id,
            reason,
        )]

    question = str(row.get("question") or "")
    title = str(row.get("event_title") or "")
    description = str(row.get("description") or "")
    criteria = str(row.get("resolution_criteria") or "")
    full_text = " ".join([title, str(row.get("event_slug") or ""), str(row.get("slug") or ""), question, description, criteria])
    # Slugs can contain generated timestamps (including 2026) and are not semantic evidence.
    scope = " ".join([title, question])
    labels = tag_labels(row)
    question_states = extract_states(question, labels)
    scope_states = extract_states(scope, labels)
    states = extract_states(full_text, labels)
    district = extract_district(scope)
    title_states = extract_states(title, labels)
    if len(question_states) == 1 and len(title_states) == 1 and question_states[0] != title_states[0]:
        return "REJECTED_GAMMA_SEMANTIC_CONFLICT", [reject(
            market_id,
            f"Gamma event title identifies {title_states[0].title()} while the market question identifies {question_states[0].title()}; the state-specific settlement target is inconsistent.",
        )]
    state = district[0] if district else (
        question_states[0] if len(question_states) == 1 else (
            title_states[0] if len(title_states) == 1 else (states[0] if len(states) == 1 else None)
        )
    )
    tags_midterm = "Midterms" in labels or any("midterm" in t.lower() for t in labels)

    if not (question and row.get("event_id") and row.get("event_slug") and row.get("slug")):
        return "REJECTED_INCOMPLETE_GAMMA_IDENTITY", [reject(
            market_id,
            "Gamma does not provide the event and market fields needed to establish canonical identity.",
        )]
    if not description and not criteria:
        return "REJECTED_UNDERDESCRIBED", [reject(
            market_id,
            "Gamma provides no resolution description or criteria, so the contract's settlement meaning cannot be established from the discovery snapshot.",
        )]

    us_text = bool(re.search(r"\b(united states|u\.?s\.?|us house|us senate|us congress|american)\b", full_text, re.I))
    state_midterm_tag = state_tag_present({x.lower() for x in labels})
    national_midterm = bool(tags_midterm and re.search(r"midterm", full_text, re.I))
    lower_labels = {x.lower() for x in labels}
    district_us_tag = bool(district and ({"us election", "midterms", "house elections", "house primary", "united states"} & lower_labels or re.search(r"\b(us|u\.s\.|united states)\b", full_text, re.I)))
    state_us_context = bool(states and (us_text or state_midterm_tag or state_us_tag(labels) or national_midterm))
    national_us_context = bool(us_text or (tags_midterm and re.search(r"(house|senate|governor|midterm|seat|chamber)", full_text, re.I)))
    if not (state_us_context or district_us_tag or national_us_context):
        return "REJECTED_NON_US_OR_UNANCHORED", [reject(
            market_id,
            "Gamma resolution identifies a non-U.S. election or no U.S. race/aggregate represented by an accepted SIG anchor.",
        )]

    is_2026, year_reason = target_year_status(row, full_text, scope, labels, state, district, index)
    if not is_2026:
        return "REJECTED_WRONG_OR_UNPROVEN_CYCLE", [reject(market_id, year_reason)]

    if LOCAL_OFFICE_MARKERS.search(scope):
        # County-level subtotals in a federal/statewide contest are components; county
        # executive, mayoral, and school-board offices are separate local outcomes.
        if not re.search(r"\b(county|city)\b.{0,50}\b(in|for)\b.{0,50}\b(governor|senate|house|congress|midterm)\b", question, re.I):
            return "REJECTED_LOCAL_OFFICE", [reject(
                market_id,
                "This resolves a municipal/county office, not a mapped statewide race or a constituent seat in the SIG House/Senate aggregate.",
            )]

    if LEGISLATIVE_POLICY_MARKERS.search(scope):
        return "REJECTED_NON_ELECTORAL_LEGISLATION", [reject(
            market_id,
            "This resolves on a policy or legislative action, not an election result, candidate decision, seat count, or electoral-performance variable linked to a mapped SIG target.",
        )]

    if re.search(r"\b(congressional map|redistrict|district map)\b", full_text, re.I):
        return "REJECTED_UNTRADEABLE_MAP_MARKET", [reject(
            market_id,
            "The question concerns 2026 U.S. House district maps, but Gamma returned no verified CLOB condition/token identity for this candidate.",
        )]

    # Exclude policy ballots that share an election date but do not resolve on a party,
    # race, chamber, seat count, or electoral-rule outcome.
    if re.search(r"\b(referendum|ballot measure|proposition|amendment)\b", full_text, re.I) and not re.search(r"\b(redistrict|congressional map|district map)\b", full_text, re.I):
        return "REJECTED_POLICY_MEASURE", [reject(
            market_id,
            "This settles on a policy referendum rather than a SIG race, party outcome, seat aggregate, or election-structure variable; same-state ballot timing alone is insufficient.",
        )]

    # Do not admit a textually election-related contract unless Gamma's settlement
    # description/criteria identifies an electoral result, candidate, turnout, seat,
    # control state, or a pre-election signal for one of those outcomes.
    if not ELECTION_MARKERS.search(full_text) and not re.search(r"\b(announce|retire|retirement|odds|favorite|voter|turnout)\b", full_text, re.I):
        return "REJECTED_NO_ELECTORAL_SEMANTICS", [reject(
            market_id,
            "The resolution text does not settle on a 2026 race, electoral aggregate, candidate decision, turnout, or party-performance variable.",
        )]

    candidates = parse_json_list(row.get("clob_token_ids"))
    outcomes = parse_json_list(row.get("outcomes"))
    if len(candidates) != len(outcomes) or len(candidates) < 2:
        return "REJECTED_TOKEN_ALIGNMENT", [reject(
            market_id,
            "Gamma outcomes and CLOB token IDs do not form a complete one-to-one market identity.",
        )]

    party = party_in_text(" ".join([title, question]))
    is_primary = bool(PRIMARY_MARKERS.search(scope))
    is_candidate_signal = bool(
        is_primary or PRE_CAMPAIGN_MARKERS.search(scope)
        or re.search(r"\b(endorse|endorses|endorsement|retire|retires|retirement|file to run)\b", scope, re.I)
    )
    is_pivotal = bool(PIVOTAL_MARKERS.search(scope) or {"tossup house races", "key house races"} & lower_labels)
    house_words = bool(re.search(r"\b(house|congress|congressional)\b", scope, re.I) or {"house elections", "house primary"} & lower_labels)
    senate_words = bool(re.search(r"\bsenate\b", scope, re.I) or {"senate elections", "senate primary", "senate races"} & lower_labels)
    gov_words = bool(re.search(r"\b(governor|gubernatorial|governorship)\b", scope, re.I) or {"governor races", "governor elections"} & lower_labels)
    state_legislature = bool(STATE_OFFICE_MARKERS.search(scope))
    has_dist = bool(district and house_words and not re.search(r"state house|state legislature", scope, re.I))
    is_presidential = bool(PRESIDENTIAL_MARKERS.search(scope))
    if is_presidential:
        return "REJECTED_PRESIDENTIAL", [reject(
            market_id,
            "This is a presidential primary/nominee market, outside the accepted SIG universe of 2026 House, Senate, and governor outcomes.",
        )]

    added: list[dict[str, str]] = []
    id_to_anchor = {str(r.get("sig_market_id")): r for r in index["anchors"]}

    def add_links(sig_ids: list[str], relation: str, direction: str, reason: str, confidence: str) -> None:
        for sig_id in sig_ids:
            if sig_id:
                added.append(edge(market_id, sig_id, relation, direction, reason, confidence))

    def control_links(chamber: str, relation: str, direction: str, reason: str, confidence: str, only_party: str | None = None) -> None:
        add_links(party_targets(index["controls"][chamber], only_party), relation, direction, reason, confidence)

    if re.search(r"\b(delegate|delegates)\b", scope, re.I) and NONVOTING_DELEGATE_MARKERS.search(scope):
        return "REJECTED_NONVOTING_DELEGATE", [reject(
            market_id,
            "The contract concerns a non-voting territorial or District of Columbia delegate, which does not contribute a voting seat to the SIG U.S. House control target.",
        )]

    # Description boilerplate can mention unrelated places; only event/question scope
    # can establish a multi-race combination.
    if explicit_multi_state_scope(scope, scope_states) and (senate_words or house_words or gov_words):
        related_ids: list[str] = []
        for s in scope_states:
            if gov_words:
                related_ids.extend(index["governor_by_state"].get(s, {}).values())
            if senate_words and not state_legislature:
                related_ids.extend(index["senate_by_state"].get(s, {}).values())
            if house_words:
                related_ids.extend(v for (st, _), parties in index["house_by_race"].items() if st == s for v in parties.values())
        if related_ids:
            add_links(sorted(set(related_ids)), "MULTI_RACE_COMBO", "joint/nonlinear; exact combo semantics remain in the source contract",
                      "Gamma question and event scope enumerate multiple 2026 U.S. races/states. Each linked SIG anchor is a named race component; the combined payout remains one contract.", "MEDIUM")
            return "ACCEPTED", list({(e["sig_market_id"], e["relationship_class"]): e for e in added}.values())

    if has_dist:
        dist_key = (district[0], district[1])
        race_anchors = index["house_by_race"].get(dist_key, {})
        local_subresult = bool(re.search(r"\b(county|parish|borough)\b", question, re.I))
        relation = "PIVOTAL_RACE" if is_pivotal else "CONDITIONAL_OUTCOME"
        rationale = (
            f"Gamma settlement criteria identify the 2026 U.S. House contest {district[0].upper()}-{district[1]}. "
            "That district contributes one elected member to the U.S. House seat total, and therefore changes the chamber-control state."
        )
        if local_subresult:
            control_links("HOUSE", "OTHER_ECONOMICALLY_LINKED", "county vote subtotal contributes to the district result; nonlinear signal for House control",
                          f"Gamma resolves on a county-level vote result inside the 2026 {district[0].upper()}-{district[1]} House contest. The county is a partial geographic subtotal, not a separate seat.", "MEDIUM")
        elif is_candidate_signal:
            control_links("HOUSE", "CONDITIONAL_OUTCOME", "nomination or candidacy signal is conditional on the district's general-election seat result",
                          f"Gamma resolves on a 2026 {district[0].upper()}-{district[1]} House primary, nomination, candidacy, endorsement, or retirement signal. It can change the probability of the district seat outcome but does not itself award a House seat.", "MEDIUM")
        else:
            control_links("HOUSE", "TARGET_CONSTITUENT_OF_AGGREGATE", "party-dependent; the district winner contributes one seat to House control", rationale, "HIGH")
        target_relation = "PIVOTAL_RACE" if is_pivotal else "CONDITIONAL_OUTCOME"
        specific_reason = (
            f"Gamma resolution describes the same 2026 {district[0].upper()}-{district[1]} House contest as the mapped SIG target. "
            "The market resolves on a candidate, primary, or margin outcome from that contest, which is economically conditional on the SIG party-winner result."
        )
        add_links(party_targets(race_anchors, party), target_relation,
                  "party/result dependent; same district ballot and official count", specific_reason,
                  "HIGH" if not is_primary else "MEDIUM")

    local_subresult = bool(re.search(r"\b(county|parish|borough)\b", question, re.I))
    if not has_dist and senate_words and not state_legislature and state:
        senate_anchors = index["senate_by_state"].get(state, {})
        sen_relation = "PIVOTAL_RACE" if is_pivotal else "CONDITIONAL_OUTCOME"
        senate_reason = (
            f"Gamma resolution criteria identify a 2026 U.S. Senate contest in {state.title()}. "
            "The state result contributes one seat to the U.S. Senate party total and chamber-control state."
        )
        if senate_anchors and not local_subresult:
            if is_candidate_signal:
                control_links("SENATE", "CONDITIONAL_OUTCOME", "nomination or candidacy signal is conditional on the state's general-election seat result", senate_reason, "MEDIUM")
            else:
                control_links("SENATE", "TARGET_CONSTITUENT_OF_AGGREGATE", "party-dependent; the state winner contributes one Senate seat", senate_reason, "HIGH")
        elif not senate_anchors and re.search(r"\bspecial election\b", scope, re.I):
            control_links("SENATE", "TARGET_CONSTITUENT_OF_AGGREGATE", "special-election winner contributes a seat to Senate control", senate_reason, "MEDIUM")
        if senate_anchors:
            add_links(party_targets(senate_anchors, party), "CONDITIONAL_OUTCOME" if local_subresult else sen_relation,
                      "county vote subtotal is conditional on the same statewide Senate result" if local_subresult else "party/result dependent; same state Senate contest",
                      f"Gamma resolution criteria identify the same 2026 {state.title()} Senate contest as the mapped SIG target; the market resolves on its nomination, winner, margin, or county vote subtotal.",
                      "HIGH" if not is_primary else "MEDIUM")

    if not has_dist and gov_words and state:
        gov_anchors = index["governor_by_state"].get(state, {})
        gov_relation = "PIVOTAL_RACE" if is_pivotal else "CONDITIONAL_OUTCOME"
        if gov_anchors:
            add_links(party_targets(gov_anchors, party), "CONDITIONAL_OUTCOME" if local_subresult else gov_relation,
                      "party/result dependent; same statewide gubernatorial contest",
                      f"Gamma resolution criteria identify the 2026 {state.title()} gubernatorial contest, the same office and state as the mapped SIG target.",
                      "HIGH" if not is_primary else "MEDIUM")
        elif index["other_by_state"].get(state):
            control_links("HOUSE", "SAME_STATE_RELATED", "same-state coelectorate; nonlinear and not a House seat constituent",
                          f"Gamma resolution criteria identify the 2026 {state.title()} governor election. It shares the state midterm electorate with the mapped House control target, but it does not contribute a House seat.", "LOW")
            control_links("SENATE", "SAME_STATE_RELATED", "same-state coelectorate; nonlinear and not a Senate seat constituent",
                          f"Gamma resolution criteria identify the 2026 {state.title()} governor election. It shares the state midterm electorate with the mapped Senate control target, but it does not contribute a Senate seat.", "LOW")

    if state_legislature and state:
        same_state = sorted(set(index["other_by_state"].get(state, [])))
        if same_state:
            add_links(same_state, "SAME_STATE_RELATED", "shared statewide partisan electorate; no direct seat equivalence",
                      f"Gamma resolution criteria identify a 2026 {state.title()} state-office contest on the same statewide electoral environment as these mapped SIG anchors. The relationship is a coelectorate/trifecta link, not a claim that the offices are equivalent.", "MEDIUM")

    # National controls, exact/range/threshold seat counts, joint outcomes, and other
    # aggregate election-state variables have no single district geography.
    if not added:
        seat_count_question = bool(
            not re.search(r"\b(retire|retires|retired|retirement|not running|will not run|won't run|resign|vacate)\b", question, re.I)
            and re.search(
                r"\b(how many|number of|at least|at most|more than|fewer than|less than|over|under|between)\b.{0,70}\b(seats?|members?)\b|"
                r"\b(seats?|members?)\b.{0,70}\b(after|following|between|at least|at most|more than|fewer than)\b",
                question, re.I,
            )
        )
        house_control_scope = bool(re.search(
            r"\b(control|majority|balance of power|party seats?|party seat total|house odds|house favorite|speaker|retire|incumbent)\b|"
            r"\bwin.{0,20}house\b|\bhouse.{0,20}win\b|\b(turnout|popular vote|vote share)\b", scope, re.I,
        ))
        senate_control_scope = bool(re.search(
            r"\b(control|majority|balance of power|party seats?|party seat total|senate odds|senate favorite|leader|retire|incumbent)\b|"
            r"\bwin.{0,20}senate\b|\bsenate.{0,20}win\b|\b(turnout|popular vote|vote share)\b", scope, re.I,
        ))
        has_house_control = house_words and (house_control_scope or seat_count_question)
        has_senate_control = senate_words and not state_legislature and (senate_control_scope or seat_count_question)
        both_chambers = bool(
            house_words and senate_words
            and re.search(r"\b(both|joint|balance of power|trifecta|unified|split control|house and senate|and)\b", scope, re.I)
            and re.search(r"\b(control|win|hold|have|majority|seat|seats|how many)\b", scope, re.I)
            and not state_legislature
        )
        joint_state_offices = bool(
            senate_words and gov_words
            and re.search(r"\b(any|or|and|both|combo|combination)\b", scope, re.I)
        )
        gov_count = bool(re.search(r"(how many|more|fewer|number of|hold).{0,70}governorship", scope, re.I))
        demographics = bool(re.search(r"\b(voters?|men|women|latino|hispanic|white voters|young voters|age group|turnout|popular vote)\b", scope, re.I))
        if both_chambers:
            rationale = "Gamma's settlement terms encode a joint 2026 House/Senate condition. Each SIG anchor is one chamber marginal of that explicitly joint contract."
            relation = "JOINT_OUTCOME" if seat_count_question else "JOINT_CHAMBER"
            for chamber in ("HOUSE", "SENATE"):
                control_links(chamber, relation, "joint/conditional; the other chamber condition changes the joint payout", rationale, "HIGH")
        elif joint_state_offices:
            senate_ids = list(index["senate_by_state"].get(state, {}).values()) if state else []
            governor_ids = list(index["governor_by_state"].get(state, {}).values()) if state else []
            if not senate_ids:
                senate_ids = list(index["controls"]["SENATE"].values())
            if not state:
                governor_ids = [str(r.get("sig_market_id")) for r in index["anchors"] if "governor" in str(r.get("sig_market_title") or "").lower()]
            rationale = "Gamma resolves on a 2026 joint/OR outcome across Senate and gubernatorial elections named by the contract. Linked SIG anchors cover the state race components where mapped and otherwise the Senate chamber aggregate; the combination remains one contract."
            add_links(sorted(set(senate_ids + governor_ids)), "JOINT_OUTCOME", "joint OR/combination across eligible state contests", rationale, "MEDIUM")
        elif gov_count:
            gov_ids = [str(r.get("sig_market_id")) for r in index["anchors"] if "governor" in str(r.get("sig_market_title") or "").lower()]
            rationale = "Gamma settles on the aggregate number/party count of 2026 governorships; each mapped state governor result contributes to that national total."
            relation = "SEAT_THRESHOLD" if SEAT_THRESHOLD.search(full_text) else ("SEAT_RANGE" if SEAT_RANGE.search(full_text) else "AGGREGATE_CONTAINS_TARGET")
            add_links(gov_ids, relation, "threshold-dependent aggregate across state governor outcomes", rationale, "HIGH")
        elif has_house_control or has_senate_control:
            chambers = [c for c, yes in (("HOUSE", has_house_control), ("SENATE", has_senate_control)) if yes]
            if both_chambers:
                chambers = ["HOUSE", "SENATE"]
            if both_chambers:
                relation = "JOINT_OUTCOME"
            elif seat_count_question:
                relation = "SEAT_THRESHOLD" if SEAT_THRESHOLD.search(question) else ("SEAT_RANGE" if SEAT_RANGE.search(question) else "SEAT_EXACT")
            elif is_primary or "leader" in full_text.lower() or "speaker" in full_text.lower() or "retire" in full_text.lower():
                relation = "CONDITIONAL_OUTCOME"
            elif both_chambers:
                relation = "JOINT_CHAMBER"
            elif re.search(r"\b(odds|favorite|favored|forecast|model|chance|turnout|popular vote|vote share|retire|incumbent)\b", scope, re.I):
                relation = "OTHER_ECONOMICALLY_LINKED"
            else:
                relation = "CHAMBER_CONTROL"
            party_filter = party if party in {"DEMOCRATIC", "REPUBLICAN"} else None
            direction = "party-specific control/seat threshold" if party_filter else "nonlinear; contract concerns chamber performance/control but does not specify one party direction"
            rationale = "Gamma resolution criteria identify a 2026 U.S. chamber-control, party-seat, leadership, turnout, or election-performance contract; the linked SIG anchor measures that chamber's party result."
            for chamber in chambers:
                control_links(chamber, relation, direction, rationale, "HIGH" if relation in {"CHAMBER_CONTROL", "SEAT_EXACT", "SEAT_RANGE", "SEAT_THRESHOLD"} else "MEDIUM", party_filter)
        elif demographics:
            rationale = "Gamma resolves this on 2026 midterm voter-group or election-performance results. Those outcomes measure party performance in the same electorate and can contain information about both chamber-control targets, with no fixed sign."
            for chamber in ("HOUSE", "SENATE"):
                control_links(chamber, "OTHER_ECONOMICALLY_LINKED", "nonlinear electorate signal; no fixed sign", rationale, "LOW")

    # State-specific office markets outside the three federal/gubernatorial families
    # are accepted only when an explicit same-state SIG anchor supplies the link.
    if not added and state_legislature and state:
        same_state = sorted(set(index["other_by_state"].get(state, [])))
        if same_state:
            add_links(same_state, "SAME_STATE_RELATED", "shared statewide partisan electorate; not the same office",
                      f"Gamma criteria identify a 2026 {state.title()} state-legislative contest; the shared state ballot/party control links it to the mapped {state.title()} SIG race.", "MEDIUM")

    if not added:
        return "REJECTED_NO_EXPLICIT_ECONOMIC_LINK", [reject(
            market_id,
            "A 2026 U.S. political/election reference is present, but Gamma settlement semantics do not establish a shared SIG race, chamber seat, party aggregate, or named multi-race relationship.",
        )]

    # Normalize repeated links and keep only schema-supported relationship labels.
    dedup: dict[tuple[str, str], dict[str, str]] = {}
    for e in added:
        key = (e["sig_market_id"], e["relationship_class"])
        dedup.setdefault(key, e)
    return "ACCEPTED", list(dedup.values())


def state_us_tag(labels: set[str]) -> bool:
    lower = {x.lower() for x in labels}
    state_tags = {f"{s} midterm" for s in STATES}
    return bool({"us election", "united states", "us politics", "u.s. politics", "midterms"} & lower or {x.lower() for x in state_tags} & lower)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    index = anchor_index(load_json(args.mapping))
    candidates = read_csv(args.candidates)
    output_rows: list[dict[str, str]] = []
    outcomes = Counter()
    statuses = Counter()
    classes = Counter()
    accepted_markets: set[str] = set()
    rejected_markets: set[str] = set()
    for row in candidates:
        market_id = str(row.get("market_id") or "").strip()
        status, decisions = review_candidate(row, index)
        outcomes[status] += 1
        if status == "DIRECT_DUPLICATE":
            continue
        if status == "ACCEPTED":
            accepted_markets.add(market_id)
            for d in decisions:
                classes[d["relationship_class"]] += 1
                output_rows.append(d)
        else:
            rejected_markets.add(market_id)
            output_rows.extend(decisions)
        for d in decisions:
            statuses[d["review_status"]] += 1

    fields = [
        "market_id", "sig_market_id", "review_status", "relationship_class",
        "relationship_direction", "economic_rationale", "confidence", "rejection_reason",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in sorted(output_rows, key=lambda r: (int(r["market_id"]) if r["market_id"].isdigit() else 0, r["market_id"], r["sig_market_id"])):
            writer.writerow(row)

    summary = {
        "schema_version": 1,
        "canonical_mapping_sha256": sha256(args.mapping),
        "gamma_candidates_sha256": sha256(args.candidates),
        "review_ledger_sha256": sha256(args.output),
        "candidate_row_count_including_direct": len(candidates),
        "direct_duplicate_row_count": outcomes["DIRECT_DUPLICATE"],
        "non_direct_candidate_row_count": len(candidates) - outcomes["DIRECT_DUPLICATE"],
        "accepted_ets_market_count": len(accepted_markets),
        "accepted_edge_count": sum(classes.values()),
        "rejected_candidate_count": len(rejected_markets),
        "review_decision_counts": dict(sorted(outcomes.items())),
        "review_status_counts": dict(sorted(statuses.items())),
        "relationship_class_edge_counts": dict(sorted(classes.items())),
        "unresolved_identity_candidates": [
            {"market_id": r.get("market_id"), "question": r.get("question"), "condition_id": r.get("condition_id")}
            for r in candidates if r.get("identity_status") != "VERIFIED"
        ],
        "unresolved_candidate_rule": "Each unresolved market is rejected from the frozen tradable set; the live Gamma identity check for discovery's unresolved rows is retained in the lane evidence.",
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
