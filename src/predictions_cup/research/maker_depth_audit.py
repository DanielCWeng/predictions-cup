"""Offline, fail-closed quality audit of the untrusted maker-depth-mirror-v1 feed.

No trading dependencies, network, maker imports, exchange clients or DB writes.
Local REST receipt time is NOT venue event time; this module NEVER certifies paired PAPER.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any

D = Decimal
MAX_FILE_BYTES = 32 * 1024 * 1024
SCHEMA = "maker-depth-mirror-v1"
DEFAULT_SIZES = (1, 5, 10, 20)


class AuditError(ValueError):
    """Input/evidence error, never a basis to admit a trade."""


@dataclass(frozen=True)
class Accepted:
    exchange: str
    market: str
    token: str
    direction: str
    event: str | None
    mapping_sha256: str
    tournament: str


@dataclass(frozen=True)
class Snapshot:
    exchange: str
    market: str
    run: str
    received: datetime
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    line: int
    record_sha256: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dec(value: Any) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise AuditError("DECIMAL_NOT_STRING")
    try:
        result = D(value)
    except (ValueError, InvalidOperation):
        raise AuditError("INVALID_DECIMAL") from None
    if not result.is_finite():
        raise AuditError("NONFINITE_DECIMAL")
    return result


def utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise AuditError("MISSING_RECEIPT_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(UTC)
    except ValueError:
        raise AuditError("INVALID_RECEIPT_TIMESTAMP") from None


def positive_sha(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def accepted_specs(mapping: Path, acceptance: Path, ids: tuple[str, ...]) -> dict[str, Accepted]:
    """Never accept a soft/title-only match or a self-asserted EXACT identity."""
    if not ids or len(set(ids)) != len(ids):
        raise AuditError("UNIVERSE_EMPTY_OR_DUPLICATED")
    payload = mapping.read_bytes()
    source_hash = sha256_bytes(payload)
    proof = json.loads(acceptance.read_text(encoding="utf-8"))
    if (proof.get("all_records_verified") is not True
            or proof.get("artifact_sha256", {}).get("crosswalk_json") != source_hash):
        raise AuditError("ACCEPTED_MAPPING_HASH_MISMATCH")
    records = json.loads(payload).get("records", [])
    wanted = set(ids)
    result: dict[str, Accepted] = {}
    for row in records:
        eid = row.get("sig_exchange_id")
        if eid not in wanted:
            continue
        pm = row.get("direct_polymarket")
        if (eid in result or row.get("status") != "VERIFIED"
                or row.get("mapping_class") != "EXACT"
                or row.get("mapping_direction") not in ("SAME", "COMPLEMENT")
                or row.get("sig_outcome_label", "").upper() != "YES"
                or not isinstance(pm, dict) or len(pm.get("token_ids", [])) != 2
                or len(pm.get("outcomes", [])) != 2
                or pm.get("mapped_token_id") not in pm["token_ids"]
                or pm.get("mapped_outcome") != pm["outcomes"][pm["token_ids"].index(
                    pm["mapped_token_id"])]):
            raise AuditError(f"UNACCEPTED_EXACT_MAPPING:{eid}")
        result[eid] = Accepted(eid, str(row["sig_market_id"]),
                               str(pm["mapped_token_id"]), row["mapping_direction"],
                               str(pm["event_id"]) if pm.get("event_id") else None,
                               source_hash, str(row.get("sig_tournament_id", "")))
    if set(result) != wanted:
        raise AuditError(f"MISSING_ACCEPTED_IDS:{sorted(wanted-set(result))}")
    return result


def levels(raw: Any, *, reverse: bool, tick: Decimal | None) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, list):
        raise AuditError("INVALID_LADDER_TYPE")
    parsed: list[tuple[Decimal, Decimal]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"price", "quantity"}:
            raise AuditError("INVALID_LADDER_LEVEL")
        p, q = dec(item["price"]), dec(item["quantity"])
        if not D(0) < p < D(1) or q <= 0:
            raise AuditError("INVALID_PRICE_OR_QUANTITY")
        if tick is not None and p % tick != 0:
            raise AuditError("PRICE_OFF_ASSUMED_TICK")
        parsed.append((p, q))
    if len(set(p for p, _ in parsed)) != len(parsed):
        raise AuditError("DUPLICATE_PRICE_LEVEL")
    if parsed != sorted(parsed, key=lambda pq: pq[0], reverse=reverse):
        raise AuditError("UNSORTED_LADDER")
    return tuple(parsed)


def quantile(vals: list[float], fraction: float) -> float | None:
    if not vals:
        return None
    ordered = sorted(vals)
    pos = (len(ordered) - 1) * fraction
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo), 6) if hi != lo else round(ordered[lo], 6)


def dist(vals: list[float]) -> dict[str, Any]:
    return {"count": len(vals), "p10": quantile(vals, .1), "p50": quantile(vals, .5),
            "p90": quantile(vals, .9), "max": max(vals) if vals else None}


def walk(levels_: tuple[tuple[Decimal, Decimal], ...], qty: int,
         haircut: Decimal) -> dict[str, str] | None:
    remaining, cash = D(qty), D(0)
    marginal: Decimal | None = None
    for price, size in levels_:
        take = min(remaining, (size * haircut).to_integral_value(rounding=ROUND_FLOOR))
        if take <= 0:
            continue
        cash += take * price
        remaining -= take
        marginal = price
        if not remaining:
            break
    if remaining or marginal is None:
        return None
    return {"vwap": str(cash / D(qty)), "marginal": str(marginal),
            "worst_limit_cash": str(marginal * qty)}


def liquidity(book: Snapshot, sizes: tuple[int, ...], haircut: Decimal,
              fee_per_share: Decimal | None) -> dict[str, Any]:
    # SIG is one YES exchange: BUY NO crosses complemented YES bids, not a second book.
    no_asks = tuple((D(1) - p, q) for p, q in book.bids)
    result: dict[str, Any] = {}
    for label, ladder in (("buy_yes", book.asks), ("buy_no", no_asks)):
        best = ladder[0][0]
        side: dict[str, Any] = {"best_price": str(best), "sizes": {}}
        for size in sizes:
            trade = walk(ladder, size, haircut)
            if trade is not None:
                trade["slippage_vs_best"] = str(D(trade["vwap"]) - best)
                trade["fee_adjusted_cost_per_share"] = (
                    str(D(trade["vwap"]) + fee_per_share)
                    if fee_per_share is not None else None)
            side["sizes"][str(size)] = trade
        result[label] = side
    return result


def _strict_record(row: Any, specs: dict[str, Accepted], run: str | None,
                   tick: Decimal | None) -> Snapshot:
    if not isinstance(row, dict) or row.get("schema") != SCHEMA:
        raise AuditError("WRONG_SCHEMA")
    if (row.get("event") != "SIG_MAKER_REST_RECONCILED_BOOK"
            or row.get("source") != "MAKER_ALREADY_FETCHED_SIG_REST"
            or row.get("snapshot_at_reconciliation") is not True):
        raise AuditError("WRONG_EVENT_OR_ORIGIN")
    if (row.get("source_clock_verified") is not False
            or row.get("source_sequence_verified") is not False
            or row.get("trusted_for_paired_paper") is not False
            or row.get("venue_timestamp") is not None
            or row.get("venue_revision") is not None):
        raise AuditError("SPOOFED_SOURCE_PROVENANCE")
    rid = row.get("run_id")
    if not isinstance(rid, str) or len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid):
        raise AuditError("INVALID_RUN_ID")
    if run is not None and rid != run:
        raise AuditError("MULTIPLE_RUNS_IN_FILE")
    eid = row.get("exchange_id")
    if not isinstance(eid, str) or eid not in specs:
        raise AuditError("UNACCEPTED_EXCHANGE_ID")
    spec = specs[eid]
    if row.get("market_id") != spec.market or row.get("tournament_id") != spec.tournament:
        raise AuditError("MARKET_OR_TOURNAMENT_MISMATCH")
    rev = row.get("triggering_realtime_revision")
    if rev is not None and (type(rev) is not int or rev < 0):
        raise AuditError("INVALID_TRIGGER_REVISION")
    bids = levels(row.get("bids"), reverse=True, tick=tick)
    asks = levels(row.get("asks"), reverse=False, tick=tick)
    if not bids or not asks:
        raise AuditError("ONE_SIDED_OR_ZERO_DEPTH")
    if bids[0][0] >= asks[0][0]:
        raise AuditError("CROSSED_OR_LOCKED_BOOK")
    return Snapshot(eid, spec.market, rid, utc(row.get("received_at")),
                    bids, asks, 0, "")


def audit(path: Path, expected_sha256: str, specs: dict[str, Accepted], *,
          tick: Decimal | None = None, sizes: tuple[int, ...] = DEFAULT_SIZES,
          haircut: Decimal = D("0.5"), fee_per_share: Decimal | None = None,
          gap_seconds: float = 20, max_bytes: int = MAX_FILE_BYTES,
          counters: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[Snapshot]]:
    if (not positive_sha(expected_sha256) or not sizes or any(x < 1 for x in sizes)
            or not D(0) < haircut <= 1 or gap_seconds <= 0 or max_bytes <= 0
            or tick is not None and (tick <= 0 or tick >= 1)
            or fee_per_share is not None and fee_per_share < 0):
        raise AuditError("INVALID_AUDIT_PARAMETERS")
    source_size = path.stat().st_size
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha256:
        raise AuditError("INPUT_SHA256_MISMATCH")
    if source_size > max_bytes:
        raise AuditError("FILE_OVER_APPROVED_CAP")
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        # Never silently ignore a partial final book from a killed writer.
        trailing_partial = True
    else:
        trailing_partial = False
    snapshots: list[Snapshot] = []
    ledger: list[dict[str, Any]] = []
    rejects: Counter[str] = Counter()
    per: dict[str, dict[str, Any]] = {eid: {"snapshots": 0, "liquidity_eligible": 0,
            "rejects": {}, "gap_count": 0, "max_gap_seconds": None,
            "first_received_at": None, "last_received_at": None, "unchanged_ladders": 0,
            "inter_receipt_gap_seconds": [], "spreads": [], "best_bid_changes": [],
            "best_ask_changes": [], "sizes_fully_visible": {
                str(n): {"buy_yes": 0, "buy_no": 0} for n in sizes},
            "conditional_buy_yes_vwap": {str(n): [] for n in sizes},
            "conditional_buy_no_vwap": {str(n): [] for n in sizes}} for eid in specs}
    run: str | None = None
    last: dict[str, Snapshot] = {}
    global_last: datetime | None = None
    seen: set[str] = set()
    for line_no, raw_line in enumerate(data.splitlines(keepends=True), 1):
        reason: str | None = None
        eid: str | None = None
        line_hash = sha256_bytes(raw_line)
        row: Any = None
        try:
            if not raw_line.endswith(b"\n"):
                raise AuditError("TRUNCATED_FINAL_LINE")
            row = json.loads(raw_line)
            if isinstance(row, dict) and isinstance(row.get("exchange_id"), str):
                eid = row["exchange_id"]
            parsed = _strict_record(row, specs, run, tick)
            if run is None:
                run = parsed.run
            if line_hash in seen:
                raise AuditError("DUPLICATE_RECORD")
            if global_last is not None and parsed.received < global_last:
                raise AuditError("OUT_OF_ORDER_GLOBAL_RECEIPT")
            prev = last.get(parsed.exchange)
            if prev is not None and parsed.received <= prev.received:
                raise AuditError("DUPLICATE_OR_OUT_OF_ORDER_EXCHANGE_RECEIPT")
            parsed = Snapshot(parsed.exchange, parsed.market, parsed.run, parsed.received,
                              parsed.bids, parsed.asks, line_no, line_hash)
            if prev:
                dt = (parsed.received - prev.received).total_seconds()
                p = per[parsed.exchange]
                p["inter_receipt_gap_seconds"].append(dt)
                if parsed.bids == prev.bids and parsed.asks == prev.asks:
                    p["unchanged_ladders"] += 1
                if dt > gap_seconds:
                    p["gap_count"] += 1
                p["max_gap_seconds"] = max(p["max_gap_seconds"] or 0, dt)
                p["best_bid_changes"].append(float(abs(parsed.bids[0][0] - prev.bids[0][0])))
                p["best_ask_changes"].append(float(abs(parsed.asks[0][0] - prev.asks[0][0])))
            last[parsed.exchange] = parsed
            global_last = parsed.received
            seen.add(line_hash)
            snapshots.append(parsed)
            liq = liquidity(parsed, sizes, haircut, fee_per_share)
            p = per[parsed.exchange]
            p["snapshots"] += 1
            if p["first_received_at"] is None:
                p["first_received_at"] = parsed.received.isoformat()
            p["last_received_at"] = parsed.received.isoformat()
            p["liquidity_eligible"] += 1
            p["spreads"].append(float(parsed.asks[0][0] - parsed.bids[0][0]))
            for side in ("buy_yes", "buy_no"):
                for n in sizes:
                    v = liq[side]["sizes"][str(n)]
                    if v is not None:
                        p["sizes_fully_visible"][str(n)][side] += 1
                        p[f"conditional_{side}_vwap"][str(n)].append(float(v["vwap"]))
            ledger.append({"line": line_no, "raw_sha256": line_hash, "exchange_id": eid,
                           "state": "INDICATIVE_LIQUIDITY_ONLY", "reason": "SIG_NATIVE_CLOCK_SEQUENCE_UNKNOWN",
                           "received_at": parsed.received.isoformat(), "run_id": run,
                           "conditional_liquidity": liq, "paired_depth_trusted": False,
                           "certified_paired_paper": False})
        except (UnicodeDecodeError, json.JSONDecodeError, AuditError, TypeError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, AuditError) else "MALFORMED_JSONL_RECORD"
            rejects[reason] += 1
            if eid in per:
                p = per[eid]
                p["rejects"][reason] = p["rejects"].get(reason, 0) + 1
            ledger.append({"line": line_no, "raw_sha256": line_hash, "exchange_id": eid,
                           "state": "REJECTED", "reason": reason, "paired_depth_trusted": False,
                           "certified_paired_paper": False})
    totals = len(ledger)
    for p in per.values():
        for key in ("spreads", "inter_receipt_gap_seconds", "best_bid_changes", "best_ask_changes"):
            p[key] = dist(p[key])
        for side in ("buy_yes", "buy_no"):
            p[f"conditional_{side}_vwap"] = {
                k: dist(v) for k, v in p[f"conditional_{side}_vwap"].items()}
    counter_report: dict[str, Any] = {"available": counters is not None,
        "independently_attested": False, "queue_drops": None, "drop_rate": None,
        "unobserved_producer_attempts": None, "reconnect_count": None}
    if counters is not None:
        # Diagnostic ONLY: cannot cryptographically bind process logs to this file.
        keys = ("accepted", "written", "dropped", "errors", "queue_pending")
        if all(type(counters.get(k)) is int and counters[k] >= 0 for k in keys):
            counter_report["queue_drops"] = counters["dropped"]
            counter_report["unobserved_producer_attempts"] = counters["accepted"] + counters["dropped"]
            attempts = counters["accepted"] + counters["dropped"]
            counter_report["drop_rate"] = counters["dropped"] / attempts if attempts else None
            counter_report["written_count_matches_file"] = counters["written"] == totals
        else:
            counter_report["invalid_counters"] = True
    report: dict[str, Any] = {
        "schema": "lane-b-maker-depth-audit-v1", "mode": "OFFLINE_READ_ONLY",
        "input": {"sha256": actual_sha, "bytes": source_size, "expected_sha256_verified": True,
                  "complete_final_newline": not trailing_partial, "max_bytes_allowed": max_bytes},
        "mapping_sha256": next(iter(specs.values())).mapping_sha256,
        "universe": sorted(specs), "run_id": run,
        "sessions_detected": 1 if run else 0, "session_start_end_only_from_receipts": True,
        "session_boundaries_verified": False, "reconnect_count": None,
        "record_lines": totals, "valid_two_sided_snapshots": len(snapshots),
        "rejected_snapshots": sum(rejects.values()), "rejection_reasons": dict(sorted(rejects.items())),
        "offline_liquidity_eligible_observations": len(snapshots),
        "independently_certified_causal_paired_paper_observations": 0,
        "certification_scope": "IMPOSSIBLE_FROM_MAKER_MIRROR_V1_ALONE",
        "native_sig_event_time": None, "native_sig_revision": None,
        "source_book_age_ms": None, "unknown_clock_coverage": len(snapshots),
        "tick_assumption": str(tick) if tick is not None else None,
        "whole_lot_assumption": "floor per level after liquidity haircut",
        "haircut_fraction": str(haircut), "fee_per_share_assumption": (
            str(fee_per_share) if fee_per_share is not None else None),
        "capture_cadence_sec": None, "scheduled_expected_snapshots": None,
        "coverage_rate": None, "missing_segments": None,
        "recording_gaps_detected_threshold_seconds": gap_seconds,
        "producer_counters": counter_report, "markets": per,
        "real_trades": None, "real_fill_probability": None, "net_pnl": None,
        "rehearsal_authorised": False, "live_trading_authorised": False,
    }
    return report, ledger, snapshots


def pm_pair_rejections(snapshots: list[Snapshot], specs: dict[str, Accepted],
                       pm_path: Path, expected_pm_sha256: str, *,
                       max_skew_seconds: float = 2,
                       fee_per_share: Decimal | None = None,
                       haircut: Decimal = D("0.5"), size: int = 5
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Optional PM comparison: never assert PM epoch/seed or native SIG causality.

    Emits Lane B v1 rows accepted by Lane C's existing *rejection* bridge. Every
    comparison is labelled CONDITIONAL/NO_TRADE and carries null observed fills.
    """
    if not positive_sha(expected_pm_sha256) or sha256_file(pm_path) != expected_pm_sha256:
        raise AuditError("PM_INPUT_SHA256_MISMATCH")
    if pm_path.stat().st_size > MAX_FILE_BYTES:
        raise AuditError("PM_FILE_OVER_REVIEW_CAP")
    if size < 1 or max_skew_seconds <= 0:
        raise AuditError("INVALID_PAIR_PARAMETERS")
    pm: dict[str, list[tuple[datetime, Any, Any, str]]] = defaultdict(list)
    pm_rejects: Counter[str] = Counter()
    previous_pm: dict[str, datetime] = {}
    seen_pm: set[str] = set()
    for n, line in enumerate(pm_path.read_bytes().splitlines(keepends=True), 1):
        try:
            if not line.endswith(b"\n"):
                pm_rejects["TRUNCATED_PM_LINE"] += 1
                continue
            digest = sha256_bytes(line)
            if digest in seen_pm:
                pm_rejects["DUPLICATE_PM_RECORD"] += 1
                continue
            seen_pm.add(digest)
            raw = json.loads(line)
            if not isinstance(raw, dict) or raw.get("venue") != "PM" or not isinstance(raw.get("instrument_id"), str):
                pm_rejects["INVALID_PM_SOURCE_OR_ID"] += 1
                continue
            stamp = utc(raw.get("received_at"))
            token = raw["instrument_id"]
            if token in previous_pm and stamp < previous_pm[token]:
                pm_rejects["OUT_OF_ORDER_PM_RECEIPT"] += 1
                continue
            previous_pm[token] = stamp
            def pm_levels(key: str, reverse: bool) -> tuple[tuple[Decimal, Decimal], ...]:
                ladder = raw.get(key)
                if not isinstance(ladder, list):
                    return ()
                normalized = [{"price": x["price"], "quantity": str(x.get("size", x.get("quantity")))}
                              for x in ladder if isinstance(x, dict)]
                if len(normalized) != len(ladder):
                    raise AuditError("INVALID_PM_LADDER")
                return levels(normalized, reverse=reverse, tick=None)
            b = pm_levels("bids", True)
            a = pm_levels("asks", False)
            if not b or not a or b[0][0] >= a[0][0]:
                pm_rejects["PM_INVALID_TWO_SIDED_DEPTH"] += 1
                continue
            pm[token].append((stamp, b, a, digest))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pm_rejects["PM_MALFORMED_OR_INVALID_RECORD"] += 1
    for token in pm:
        pm[token].sort(key=lambda x: x[0])
    paper: list[dict[str, Any]] = []
    indicative = 0
    for book in snapshots:
        spec = specs[book.exchange]
        prior = [x for x in pm.get(spec.token, []) if x[0] <= book.received]
        near = prior[-1] if prior else None
        for side in ("BUY_YES", "BUY_NO"):
            reason = "PM_MISSING_ASOF_DEPTH"
            comparison: dict[str, Any] | None = None
            pm_at = None
            if near is not None:
                pm_at = near[0].isoformat()
                if (book.received - near[0]).total_seconds() > max_skew_seconds:
                    reason = "PM_STALE_RECEIPT"
                else:
                    mapped_yes = (side == "BUY_YES") == (spec.direction == "SAME")
                    pm_levels = near[1] if mapped_yes else tuple((D(1) - p, q) for p, q in near[2])
                    sig_levels = book.asks if side == "BUY_YES" else tuple(
                        (D(1) - p, q) for p, q in book.bids)
                    left = walk(sig_levels, size, haircut)
                    right = walk(pm_levels, size, haircut)
                    reason = "CONDITIONAL_DEPTH_NOT_SOURCE_CERTIFIED"
                    if left is None or right is None:
                        reason = "INSUFFICIENT_CONSERVATIVE_WHOLE_LOT_DEPTH"
                    else:
                        indicative += 1
                        comparison = {"sig_buy_vwap": left["vwap"],
                                      "pm_reference_sell_vwap": right["vwap"],
                                      "indicative_spread_per_share": str(D(right["vwap"]) - D(left["vwap"])),
                                      "indicative_after_assumed_cost": (
                                          str(D(right["vwap"]) - D(left["vwap"]) - fee_per_share)
                                          if fee_per_share is not None else None),
                                      "hypothetical_lots": size, "currency_comparable": False}
            paper.append({"schema_version": "lane-b-paper-v1",
                          "label": "CONDITIONAL_NOT_EXECUTABLE", "sequence": book.line,
                          "sig_exchange_id": book.exchange, "sig_market_id": book.market,
                          "pm_token_id": spec.token, "pm_event_id": spec.event,
                          "mapping_sha256": spec.mapping_sha256,
                          "mapping_direction": spec.direction, "decision_at": book.received.isoformat(),
                          "sig_received_at": book.received.isoformat(), "pm_received_at": pm_at,
                          "sig_exchange_at": None, "pm_exchange_at": None,
                          "sig_depth": {"bids": [{"price": str(p), "size": str(q)} for p, q in book.bids],
                                        "asks": [{"price": str(p), "size": str(q)} for p, q in book.asks]},
                          "pm_depth": (None if near is None else {
                              "bids": [{"price": str(p), "size": str(q)} for p, q in near[1]],
                              "asks": [{"price": str(p), "size": str(q)} for p, q in near[2]]}),
                          "side": side, "decision": "NO_TRADE",
                          "reason": reason, "conditional_only": comparison,
                          "sig_trust": False, "pm_trust": False,
                          "paired_depth_trusted": False, "observed_fill_qty": None,
                          "observed_pnl": None, "fill_probability": None})
    return paper, {"pm_input_sha256": expected_pm_sha256, "conditional_comparisons": indicative,
                   "certified_pair_count": 0, "actual_trades": None, "real_pnl": None,
                   "pm_readable_depth_rows": sum(len(x) for x in pm.values()),
                   "pm_rejection_reasons": dict(sorted(pm_rejects.items())),
                   "pm_epoch_seed_continuity_verified": False,
                   "trust_gate": "NO_TRADE_UNVERIFIED_SOURCE_NATIVE_TIME_AND_PM_EPOCH"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only maker depth quality audit; no source upgrades")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--exchange-ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--pm-input", type=Path)
    parser.add_argument("--pm-expected-sha256")
    parser.add_argument("--lane-b-paper-output", type=Path)
    parser.add_argument("--tick-size")
    parser.add_argument("--fee-per-share")
    parser.add_argument("--haircut", default="0.5")
    parser.add_argument("--gap-seconds", type=float, default=20)
    parser.add_argument("--producer-counters", type=Path)
    args = parser.parse_args()
    outputs = [p for p in (args.output, args.ledger, args.lane_b_paper_output) if p is not None]
    sources = [p for p in (args.input, args.mapping, args.acceptance, args.pm_input,
                            args.producer_counters) if p is not None]
    if (len({p.resolve() for p in outputs}) != len(outputs)
            or any(p.exists() or p.resolve() in {x.resolve() for x in sources} for p in outputs)):
        raise AuditError("OUTPUT_NOT_NEW_OR_OVERLAPPING_SOURCE")
    if bool(args.pm_input) != bool(args.pm_expected_sha256) or bool(args.pm_input) != bool(args.lane_b_paper_output):
        raise AuditError("PM_INPUT_DIGEST_AND_LEDGER_REQUIRED_TOGETHER")
    specs = accepted_specs(args.mapping, args.acceptance, tuple(args.exchange_ids.split(",")))
    tick = dec(args.tick_size) if args.tick_size is not None else None
    fee = dec(args.fee_per_share) if args.fee_per_share is not None else None
    counters = (json.loads(args.producer_counters.read_text(encoding="utf-8"))
                if args.producer_counters else None)
    report, ledger, books = audit(args.input, args.expected_sha256, specs, tick=tick,
                                   haircut=dec(args.haircut), fee_per_share=fee,
                                   gap_seconds=args.gap_seconds, counters=counters)
    paper: list[dict[str, Any]] = []
    if args.pm_input and args.pm_expected_sha256:
        paper, report["paired_adapter"] = pm_pair_rejections(
            books, specs, args.pm_input, args.pm_expected_sha256, fee_per_share=fee,
            haircut=dec(args.haircut))
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, sort_keys=True, indent=2)
        out.write("\n")
    if args.ledger:
        with args.ledger.open("x", encoding="utf-8") as out:
            for row in ledger:
                out.write(json.dumps(row, sort_keys=True) + "\n")
    if args.lane_b_paper_output:
        with args.lane_b_paper_output.open("x", encoding="utf-8") as out:
            for row in paper:
                out.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({"report": str(args.output), "input_sha256": report["input"]["sha256"],
                      "indicative_books": len(books), "certified_paired": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
