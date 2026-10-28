"""Public election-night Texas Senate vote-count adapter, NOT a forecast.

This module makes only a GET to an allowlisted official URL. It never infers
a settlement probability or emits an executable trading instruction.
The official pre-election page currently reports ZERO precincts; synthetic,
mock, premature, malformed, stale and missing totals must all fail closed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal

import httpx

TEXAS_RESULTS_URL = "https://electionresults.sos.state.tx.us/results.html"
TEXAS_RACE = "Texas U.S. Senate"
ELECTION_DAY = datetime(2026, 11, 3, tzinfo=UTC)
# Earliest conservative start: no election-night source is actionable before
# the actual election has begun. This is a permissive data gate, NOT a call.
MOCK_WORDS = re.compile(r"\b(mock|test results|demonstration|rehearsal)\b", re.I)
COUNT = r"([0-9][0-9,]*)"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.strings: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, text: str) -> None:
        if not self.hidden_depth and text.strip():
            self.strings.append(text.strip())


@dataclass(frozen=True, slots=True)
class TexasSenateObservation:
    race: str
    source_url: str
    observed_at: str
    body_sha256: str
    status: Literal[
        "PRE_ELECTION", "NO_RESULTS_YET", "UNVERIFIED_COUNTS",
        "INVALID_OFFICIAL_PAGE", "SOURCE_MOCK_DATA",
    ]
    precincts_reporting_percent: float | None
    votes_by_party: dict[str, int]
    actionable_probability: bool = False
    execution_authority: bool = False

    def as_document(self) -> dict[str, object]:
        return asdict(self)


def parse_texas_senate(html: str, *, observed_at: datetime) -> TexasSenateObservation:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observation must have an aware timestamp")
    if len(html.encode("utf-8")) > 12_000_000:
        raise ValueError("unexpectedly large official results document")
    parser = _VisibleText()
    parser.feed(html)
    all_text = re.sub(r"\s+", " ", " ".join(parser.strings))
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()

    status: Literal[
        "PRE_ELECTION", "NO_RESULTS_YET", "UNVERIFIED_COUNTS",
        "INVALID_OFFICIAL_PAGE", "SOURCE_MOCK_DATA",
    ] = "INVALID_OFFICIAL_PAGE"
    reporting: float | None = None
    counts: dict[str, int] = {}

    # Refuse site redirects / cached prior election and ambiguous layout.
    if "2026 NOVEMBER GENERAL ELECTION" not in all_text.upper():
        return TexasSenateObservation(
            TEXAS_RACE, TEXAS_RESULTS_URL, observed_at.isoformat(),
            digest, status, reporting, counts,
        )

    # Restrict parsing to U.S. Senate, NOT the adjacent House contest.
    match = re.search(
        r"U\.?\s*S\.?\s*SENATOR\b(.*?)U\.?\s*S\.?\s*REPRESENTATIVE\s+DISTRICT\s+1\b",
        all_text, flags=re.I | re.S,
    )
    if match is None:
        return TexasSenateObservation(
            TEXAS_RACE, TEXAS_RESULTS_URL, observed_at.isoformat(),
            digest, status, reporting, counts,
        )
    section = match.group(1)
    pct_match = re.search(
        r"Polling Locations Reporting:\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        section, flags=re.I,
    )
    if pct_match is None:
        return TexasSenateObservation(
            TEXAS_RACE, TEXAS_RESULTS_URL, observed_at.isoformat(),
            digest, status, reporting, counts,
        )
    reporting = float(pct_match.group(1))
    if not 0 <= reporting <= 100:
        raise ValueError("invalid reported precinct percentage")
    if MOCK_WORDS.search(all_text):
        status = "SOURCE_MOCK_DATA"
    elif observed_at.astimezone(UTC) < ELECTION_DAY:
        status = "PRE_ELECTION"
    elif reporting == 0:
        status = "NO_RESULTS_YET"
    else:
        # Fixed candidate spellings are a **schema check**, not a political
        # prediction. Future changes to ballot layout must stop the adapter.
        for party, candidate in (
            ("REPUBLICAN", "KEN PAXTON"),
            ("DEMOCRATIC", "JAMES TALARICO"),
            ("LIBERTARIAN", "TED BROWN"),
        ):
            match_count = re.search(
                rf"\b{party}\s+{candidate}\s+{COUNT}\b", section, flags=re.I
            )
            if match_count is None:
                break
            counts[party] = int(match_count.group(1).replace(",", ""))
        if (
            len(counts) == 3
            and sum(counts.values()) > 0
            and all(v >= 0 for v in counts.values())
        ):
            status = "UNVERIFIED_COUNTS"
        else:
            counts.clear()
    # Even well-formed official votes are UNOFFICIAL and omit unreported
    # precincts, provisional ballots and later eligible ballots.
    return TexasSenateObservation(
        TEXAS_RACE, TEXAS_RESULTS_URL, observed_at.isoformat(),
        digest, status, reporting, counts,
    )


async def fetch_texas_senate(*, now: datetime | None = None) -> TexasSenateObservation:
    observed_at = now or datetime.now(UTC)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), follow_redirects=False,
        headers={"User-Agent": "predictions-cup-public-results-shadow/1.0"},
    ) as client:
        response = await client.get(TEXAS_RESULTS_URL)
        response.raise_for_status()
        if str(response.url) != TEXAS_RESULTS_URL:
            raise ValueError("official source URL changed")
        if "text/html" not in response.headers.get("content-type", "").lower():
            raise ValueError("official page content type unexpected")
        return parse_texas_senate(response.text, observed_at=observed_at)
