"""Offline, read-only SIG leaderboard and tournament scenario analysis.

No HTTP clients, authentication, portfolio reads or execution imports. Input must
already have been captured through a permitted participant API consumer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PERIODS = frozenset({"1d", "7d", "30d", "all"})
STARTING_SUSQIES = Decimal("100000")


def dec(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be finite numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite numeric")
    return result


def utc_timestamp(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be ISO 8601 with timezone") from exc
    if dt.utcoffset() is None:
        raise ValueError("observed_at must have a timezone")
    return dt.isoformat()


def normalize_official_payload(
    payload: dict[str, Any], *, observed_at: str, source_url: str,
    raw_payload_sha256: str | None = None,
) -> list[dict[str, str]]:
    """Normalize *one* actual API response; preserve full-precision wire fields.

    Missing rows, time windows, volume and ROI are not inferred. PnL/volume is
    descriptive, not executable net spread. Output can be CSV-serialized.
    """
    timestamp = utc_timestamp(observed_at)
    url = urlsplit(source_url)
    if (
        url.scheme != "https"
        or url.netloc != "sig.thesuper.market"
        or not re.fullmatch(r"/api/v1/tournaments/[^/]+/leaderboard", url.path)
        or url.fragment
    ):
        raise ValueError("source_url must be the official SIG participant leaderboard endpoint")
    if raw_payload_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", raw_payload_sha256):
        raise ValueError("raw_payload_sha256 must be a lowercase SHA256 hex digest")
    period = payload.get("period")
    if period not in PERIODS:
        raise ValueError("unrecognized leaderboard period")
    limit = payload.get("limit")
    offset = payload.get("offset")
    rows = payload.get("leaderboard")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid official page limit")
    if type(offset) is not int or offset < 0:
        raise ValueError("invalid official page offset")
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("invalid official leaderboard rows")
    if type(payload.get("total")) is not int or payload["total"] < len(rows):
        raise ValueError("invalid leaderboard total")
    seen: set[str] = set()
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("malformed leaderboard entry")
        profile = raw.get("profileId")
        if not isinstance(profile, str) or not profile.strip() or profile in seen:
            raise ValueError("invalid or duplicated profileId")
        seen.add(profile)
        rank = raw.get("rank")
        trades = raw.get("tradesCount")
        if type(rank) is not int or rank < 1:
            raise ValueError("invalid leaderboard rank")
        if type(trades) is not int or trades < 0:
            raise ValueError("invalid reported trade count")
        pnl = dec(raw.get("pnl"), "pnl")
        volume = dec(raw.get("volume"), "volume")
        if volume < 0:
            raise ValueError("negative reported volume")
        roi = dec(raw.get("roi"), "roi")
        user = raw.get("username")
        if user is not None and not isinstance(user, str):
            raise ValueError("invalid username")
        win_rate = raw.get("winRate")
        if win_rate is not None:
            dec(win_rate, "winRate")
        normalized.append({
            "observed_at": timestamp,
            "source_url": source_url,
            "source_kind": "official_participant_api_declared_offline",
            "raw_payload_sha256": raw_payload_sha256 or "",
            "period": period,
            "rank": str(rank),
            "profile_id": profile,
            "username": user or "",
            "pnl_wire": str(pnl),
            "trades_count": str(trades),
            "volume_wire": str(volume),
            "roi_wire": str(roi),
            "win_rate_wire": "" if win_rate is None else str(dec(win_rate, "winRate")),
            "pnl_per_volume": "" if volume == 0 else str(pnl / volume),
            "start_bankroll_return_pct": (
                str(100 * pnl / STARTING_SUSQIES) if period == "all" else ""
            ),
            "execution_details": "NOT_OBSERVABLE",
            "settled_pnl": "NOT_OBSERVABLE",
        })
    return normalized


def compare_cumulative_snapshots(
    older: list[dict[str, str]], newer: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Compare identical cumulative period and profile IDs, never window proxies."""
    if not older or not newer:
        return []
    periods = {r["period"] for r in (*older, *newer)}
    if len(periods) != 1 or periods.pop() != "all":
        raise ValueError("changes require same all-time cumulative window")
    first_time = {r["observed_at"] for r in older}
    last_time = {r["observed_at"] for r in newer}
    if len(first_time) != 1 or len(last_time) != 1:
        raise ValueError("each snapshot must have one capture timestamp")
    t0, t1 = next(iter(first_time)), next(iter(last_time))
    if datetime.fromisoformat(utc_timestamp(t1)) <= datetime.fromisoformat(utc_timestamp(t0)):
        raise ValueError("new snapshot must follow older snapshot")
    by_profile = {r["profile_id"]: r for r in older}
    changed = []
    for now in newer:
        was = by_profile.get(now["profile_id"])
        if was is None:
            continue
        changed.append({
            "profile_id": now["profile_id"],
            "older_at": t0,
            "newer_at": t1,
            "pnl_change": str(dec(now["pnl_wire"], "pnl") - dec(was["pnl_wire"], "pnl")),
            "reported_volume_change": str(
                dec(now["volume_wire"], "volume") - dec(was["volume_wire"], "volume")
            ),
            "trade_count_change": str(int(now["trades_count"]) - int(was["trades_count"])),
        })
    return changed


def _outcomes(raw: object, *, name: str) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{name} outcomes missing")
    found: list[tuple[Decimal, Decimal]] = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError(f"invalid {name} row")
        value = dec(row.get("value"), f"{name}.value")
        prob = dec(row.get("probability"), f"{name}.probability")
        if value < 0 or not 0 <= prob <= 1:
            raise ValueError("invalid value or probability")
        found.append((value, prob))
    if sum((p for _, p in found), Decimal(0)) != 1:
        raise ValueError(f"{name} probabilities must sum to 1 exactly")
    return found


def score_tournament_scenarios(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Explicit independent outcome-vs-third-place model, NOT calibrated odds.

    Competitor third-place threshold is an uncertain *terminal* value, not
    today's mark. Strict greater-than treats ties as non-podium conservatively.
    """
    thresholds = _outcomes(spec.get("third_place_thresholds"), name="competitor")
    strategies = spec.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("strategies missing")
    results = []
    for strategy in strategies:
        if not isinstance(strategy, dict) or not strategy.get("name"):
            raise ValueError("strategy name missing")
        outcomes = _outcomes(strategy.get("final_bankroll_outcomes"), name="strategy")
        mean = sum((v * p for v, p in outcomes), Decimal(0))
        podium = sum(
            (our_p * comp_p for our_v, our_p in outcomes for target, comp_p in thresholds
             if our_v > target),
            Decimal(0),
        )
        cumulative = Decimal(0)
        percentile5 = Decimal(0)
        for value, probability in sorted(outcomes):
            cumulative += probability
            if cumulative >= Decimal("0.05"):
                percentile5 = value
                break
        results.append({
            "strategy": str(strategy["name"]),
            "illustrative_expected_terminal_bankroll": str(mean),
            "illustrative_p05_terminal_bankroll": str(percentile5),
            "illustrative_podium_probability": str(podium),
            "assumption": "independent discrete hypothetical terminal outcomes; ties lose",
            "executable_alpha_validated": "NO",
        })
    return results


def required_volume(*, profit_gap: object, net_profit_per_volume: object) -> Decimal:
    """Optimistic turnover lower bound; no guarantee of fills or available depth."""
    gap = dec(profit_gap, "profit_gap")
    margin = dec(net_profit_per_volume, "net_profit_per_volume")
    if gap < 0 or margin <= 0 or margin > 1:
        raise ValueError("profit gap must be nonnegative and margin in (0,1]")
    return gap / margin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-json", type=Path, help="Already-captured official API JSON")
    parser.add_argument("--observed-at", help="UTC capture time, required for official JSON")
    parser.add_argument("--source-url", help="Permitted endpoint URL for provenance")
    parser.add_argument("--csv-out", type=Path, help="Local output path")
    parser.add_argument("--scenarios", type=Path, help="Offline scenario spec JSON")
    args = parser.parse_args()
    if bool(args.official_json) == bool(args.scenarios):
        parser.error("choose exactly one of --official-json or --scenarios")
    if args.official_json:
        if not args.observed_at or not args.source_url or not args.csv_out:
            parser.error("official mode needs --observed-at, --source-url, --csv-out")
        raw_bytes = args.official_json.read_bytes()
        payload = json.loads(raw_bytes)
        rows = normalize_official_payload(
            payload,
            observed_at=args.observed_at,
            source_url=args.source_url,
            raw_payload_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
        if not rows:
            parser.error("empty response: no CSV will be written")
        if args.csv_out.exists():
            parser.error("refusing to overwrite prior immutable snapshot")
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        with args.csv_out.open("x", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} authorized rows to {args.csv_out}")
    else:
        spec = json.loads(args.scenarios.read_text(encoding="utf-8"))
        print(json.dumps(score_tournament_scenarios(spec), indent=2))


if __name__ == "__main__":
    main()
