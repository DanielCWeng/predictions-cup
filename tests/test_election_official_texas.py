"""Synthetic official-election parser tests; no outbound requests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from predictions_cup.analysis.election_official_texas import parse_texas_senate

TEXT = """
<html><body><h1>2026 NOVEMBER GENERAL ELECTION</h1>
<h2>U. S. SENATOR</h2>Polling Locations Reporting: {pct}%
<table><tr><th>Party</th><th>Candidate</th><th>Ballots Cast</th></tr>
<tr><td>REPUBLICAN</td><td>KEN PAXTON</td><td>{r}</td></tr>
<tr><td>DEMOCRATIC</td><td>JAMES TALARICO</td><td>{d}</td></tr>
<tr><td>LIBERTARIAN</td><td>TED BROWN</td><td>{l}</td></tr></table>
<h2>U. S. REPRESENTATIVE DISTRICT 1</h2>
REPUBLICAN Example 99999999
</body></html>
"""
ELECTION_NIGHT = datetime(2026, 11, 4, 2, tzinfo=UTC)


def test_pre_election_rejects_invented_counts() -> None:
    page = TEXT.format(pct=28, r="12,000", d="9,000", l="300")
    x = parse_texas_senate(
        page, observed_at=datetime(2026, 10, 28, tzinfo=UTC)
    )
    assert x.status == "PRE_ELECTION"
    assert x.votes_by_party == {}
    assert not x.actionable_probability
    assert not x.execution_authority


def test_zero_reporting_abstains_even_after_election() -> None:
    x = parse_texas_senate(
        TEXT.format(pct=0, r="", d="", l=""), observed_at=ELECTION_NIGHT
    )
    assert x.status == "NO_RESULTS_YET"
    assert x.votes_by_party == {}
    assert len(x.body_sha256) == 64


def test_realistic_later_counts_still_do_not_create_a_probability() -> None:
    x = parse_texas_senate(
        TEXT.format(pct=26.4, r="200,100", d="198,001", l="1,292"),
        observed_at=ELECTION_NIGHT,
    )
    assert x.status == "UNVERIFIED_COUNTS"
    assert x.votes_by_party == {
        "REPUBLICAN": 200100, "DEMOCRATIC": 198001,
        "LIBERTARIAN": 1292,
    }
    assert x.actionable_probability is False
    assert x.execution_authority is False


def test_mocks_and_bad_layout_never_enter_model() -> None:
    page = TEXT.format(pct=51, r="100", d="90", l="2")
    assert parse_texas_senate(page + " MOCK RESULTS", observed_at=ELECTION_NIGHT).status == (
        "SOURCE_MOCK_DATA"
    )
    assert parse_texas_senate(
        page.replace("2026 NOVEMBER GENERAL ELECTION", "2024 GENERAL ELECTION"),
        observed_at=ELECTION_NIGHT,
    ).status == "INVALID_OFFICIAL_PAGE"
    assert parse_texas_senate(
        page.replace("KEN PAXTON", "UNKNOWN CANDIDATE"), observed_at=ELECTION_NIGHT,
    ).votes_by_party == {}


def test_refuse_impossible_reporting_and_naive_datetime() -> None:
    with pytest.raises(ValueError, match="precinct"):
        parse_texas_senate(TEXT.format(pct=120, r=1, d=2, l=3), observed_at=ELECTION_NIGHT)
    with pytest.raises(ValueError, match="aware"):
        parse_texas_senate(
            TEXT.format(pct=0, r="", d="", l=""),
            observed_at=datetime(2026, 11, 4),
        )
