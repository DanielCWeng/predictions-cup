"""Read-only ZERO-SIG-fee archived four-pair price dislocation screen.

Parquet input is existing recorder output; PM continuity requires separate
actual REST_SEED_COMPLETE/WS_EVENT source-bridge NDJSON. Never writes or polls.
All price gaps are conditional *references*: no fill or execution proof.
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from predictions_cup.research.native_paired_source_bridge import (
    MAX_SOURCE_LAG,
    PM_EXACT_SAME_YES,
    SIG_MARKETS,
    SIG_TOURNAMENT,
)

SIZES = (1, 5, 10, 20)
HORIZONS_MS = (250, 1000, 2000)
MAX_FILES = 1000
MAX_ROWS = 250_000
MAX_LAG_MS = 2000
MAX_TARGET_BRACKET_MS = 100


def utc(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive archive timestamp")
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        raise ValueError("explicit UTC timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("non-UTC timestamp")
    return parsed


def number(value: object, *, price: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid level") from exc
    if not result.is_finite() or not (
        0 < result < 1 if price else result > 0
    ):
        raise ValueError("invalid price or size")
    return result


def book(value: object) -> dict[str, list[tuple[Decimal, Decimal]]]:
    if not isinstance(value, dict):
        raise ValueError("no book")
    result: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for side in ("bids", "asks"):
        raw = value.get(side)
        if not isinstance(raw, list) or not 0 < len(raw) <= 4096:
            raise ValueError("missing full depth")
        levels: list[tuple[Decimal, Decimal]] = []
        for row in raw:
            if isinstance(row, dict):
                p, q = row.get("price"), row.get("quantity", row.get("size"))
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                p, q = row
            else:
                raise ValueError("malformed level")
            levels.append((number(p, price=True), number(q)))
        prices = [p for p, _ in levels]
        if any((a <= b if side == "bids" else a >= b)
               for a, b in zip(prices, prices[1:], strict=False)):
            raise ValueError("unsorted or duplicate")
        result[side] = levels
    if result["bids"][0][0] >= result["asks"][0][0]:
        raise ValueError("crossed book")
    return result


def vwap(levels: list[tuple[Decimal, Decimal]], size: int) -> Decimal | None:
    remaining = size
    cost = Decimal(0)
    for price, quantity in levels:
        available = int(quantity.to_integral_value(rounding=ROUND_DOWN))
        filled = min(remaining, available)
        cost += price * filled
        remaining -= filled
        if remaining == 0:
            return cost / Decimal(size)
    return None


def capacity(b: dict[str, list[tuple[Decimal, Decimal]]], side: str) -> int:
    return sum(int(q.to_integral_value(rounding=ROUND_DOWN))
               for _, q in b[side])


def fmt(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.000001")))


def parquet_rows(root: Path | None, name: str, counters: dict[str, int],
                 start: datetime | None, end: datetime | None) -> list[dict[str, Any]]:
    if root is None:
        return []
    path = root / name
    files = sorted(path.rglob("*.parquet")) if path.is_dir() else (
        [path] if path.is_file() and path.suffix == ".parquet" else [])
    if len(files) > MAX_FILES:
        raise ValueError("archive shard budget exceeded; restrict local window")
    counters[name + "_shards"] = len(files)
    rows: list[dict[str, Any]] = []
    for file in files:
        reader = pq.ParquetFile(file)
        for batch in reader.iter_batches(batch_size=4096):
            for item in batch.to_pylist():
                counters[name + "_scanned"] += 1
                if counters[name + "_scanned"] > MAX_ROWS:
                    raise ValueError("archive row budget exceeded; restrict local window")
                at = item.get("local_receive_at", item.get(
                    "recorded_at", item.get("observed_at")))
                if at is not None:
                    dt = utc(at)
                    if (start is not None and dt < start) or (
                        end is not None and dt > end
                    ):
                        continue
                rows.append(item)
    return rows


def snapshot(at: datetime, b: dict[str, list[tuple[Decimal, Decimal]]],
             epoch: int, revision: int) -> dict[str, Any]:
    return {"at": at, "book": b, "epoch": epoch, "revision": revision}


def sig_original(rows: list[dict[str, Any]], counters: dict[str, int],
                 uncertainty: timedelta) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    prior: dict[str, tuple[str, int, int, datetime, int]] = {}
    native: dict[str, tuple[str, int, int, datetime]] = {}
    dirty: dict[str, tuple[str, int]] = {}
    ordered = sorted(rows, key=lambda x: utc(x["local_receive_at"]))
    for row in ordered:
        raw = row.get("raw_json")
        if not isinstance(raw, str):
            counters["sig_invalid"] += 1
            continue
        try:
            frame = json.loads(raw)
            if not isinstance(frame, dict):
                raise ValueError("not a mapping")
        except (ValueError, TypeError):
            counters["sig_invalid"] += 1
            continue
        if row.get("tournament_id") not in (SIG_TOURNAMENT, None):
            continue
        topic = str(row.get("topic", ""))
        if not topic.startswith("tournament:" + SIG_TOURNAMENT + ":market:"):
            continue
        market = topic.rsplit(":", 1)[-1]
        if market not in SIG_MARKETS:
            continue
        counters["sig_batches"] += 1
        counters["sig_batches_m" + market] += 1
        receive = utc(row["local_receive_at"])
        epoch = int(row.get("connection_epoch") or 0)
        session = str(row.get("session_id") or "")
        delivery = frame.get("delivery")
        if not isinstance(delivery, dict) or epoch < 1 or not session:
            counters["sig_invalid"] += 1
            prior.pop(market, None)
            native.pop(market, None)
            continue
        try:
            rev = int(delivery["revision"])
            prev = int(delivery["previousRevision"])
            seq_from = int(delivery["sourceSequenceFrom"])
            seq_to = int(delivery["sourceSequenceThrough"])
            if min(rev, prev, seq_from, seq_to) < 0 or seq_from > seq_to:
                raise ValueError("source delivery invalid")
        except (KeyError, ValueError, TypeError):
            counters["sig_invalid"] += 1
            prior.pop(market, None)
            native.pop(market, None)
            continue
        if dirty.get(market) not in (None, (session, epoch)):
            dirty.pop(market, None)  # a genuine new session/epoch boundary
        old = prior.get(market)
        clean = (old is not None and old[0] == session and old[1] == epoch
                 and old[2] == prev and rev > prev and seq_from >= old[4] and
                 receive > old[3] + uncertainty * 2 and
                 frame.get("resyncRequired") is not True)
        if not clean:
            counters["sig_gaps_or_unanchored"] += 1
            counters["sig_gaps_m" + market] += 1
            if old is not None and old[:2] == (session, epoch):
                dirty[market] = (session, epoch)
        prior[market] = (session, epoch, rev, receive, seq_to)
        books = frame.get("books", [])
        if not isinstance(books, list):
            counters["sig_invalid"] += 1
            native.pop(market, None)
            continue
        if not books:
            counters["sig_nonbook"] += 1
            counters["sig_nonbook_m" + market] += 1
        for item in books:
            if dirty.get(market) == (session, epoch):
                counters["sig_quarantined_books"] += 1
                continue
            if not isinstance(item, dict) or str(item.get("exchangeId")) != SIG_MARKETS[market]:
                counters["sig_invalid"] += 1
                continue
            counters["sig_full_claims"] += 1
            counters["sig_full_claims_m" + market] += 1
            try:
                asof = item["asOf"]
                publisher = utc(asof["at"])
                seq = int(asof["sequence"])
                b = book(item)
                valid_clock = (
                    publisher <= receive + uncertainty
                    and receive - (publisher - uncertainty) <= MAX_SOURCE_LAG
                )
                old_native = native.get(market)
                valid_native = (old_native is None or
                                old_native[0] != session or old_native[1] != epoch or
                                (seq > old_native[2] and
                                 old_native[3] + uncertainty < publisher - uncertainty))
                if not valid_clock or not valid_native or seq < 1:
                    raise ValueError("native source regression or stale")
            except (ValueError, TypeError, KeyError):
                counters["sig_rejected_books"] += 1
                dirty[market] = (session, epoch)
                native.pop(market, None)
                continue
            native[market] = (session, epoch, seq, publisher)
            counters["sig_valid_full"] += 1
            counters["sig_valid_full_m" + market] += 1
            if clean and dirty.get(market) != (session, epoch):
                output[market].append(snapshot(receive, b, epoch, seq))
                counters["sig_eligible_full"] += 1
                counters["sig_eligible_full_m" + market] += 1
            else:
                counters["sig_unanchored_full"] += 1
    return output


def pm_original(path: Path | None, counters: dict[str, int],
                start: datetime | None, end: datetime | None,
                uncertainty: timedelta) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if path is None or not path.is_file():
        return output
    last_epoch: int | None = None
    last_ordinal = 0
    last_at: datetime | None = None
    seeded: set[str] = set()
    active = False

    def invalidate(at: datetime) -> None:
        for token in seeded:
            if output.get(token):
                output[token][-1]["valid_until"] = at

    tokens = {triple[2] for triple in PM_EXACT_SAME_YES.values()}
    token_market = {triple[2]: market for market, triple in PM_EXACT_SAME_YES.items()}
    with path.open("r", encoding="utf-8") as stream:
        for line_index, line in enumerate(stream):
            if line_index >= MAX_ROWS:
                raise ValueError("PM NDJSON row budget exceeded")
            counters["pm_events_scanned"] += 1
            event_at: datetime | None = None
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or item.get("schema") != "alpha2-source-bridge-v1":
                    raise ValueError("not source bridge ABI")
                if item.get("venue") != "PM" or item.get("kind") not in (
                    "REST_SEED_COMPLETE", "WS_EVENT", "DISCONNECT"
                ):
                    raise ValueError("unexpected event")
                if item.get("issuer_authenticated") is not False or (
                    item.get("source_admitted") is not False
                ):
                    raise ValueError("unsupported provenance")
                at = utc(item["received_at"])
                event_at = at
                epoch = int(item["connection_epoch"])
                if epoch < 1 or (last_epoch is not None and epoch < last_epoch):
                    raise ValueError("epoch reversal")
                if last_epoch != epoch:
                    if last_at is not None:
                        invalidate(last_at)
                    last_epoch = epoch
                    last_ordinal = 0
                    last_at = None
                    seeded.clear()
                    active = True
                if last_at is not None and not last_at + uncertainty < at - uncertainty:
                    raise ValueError("PM receipt ambiguity")
                kind = item["kind"]
                if kind == "REST_SEED_COMPLETE":
                    token = str(item["token_id"])
                    if token not in tokens or token in seeded or not active or (
                        item.get("ws_local_ordinal") != 0
                    ):
                        raise ValueError("invalid seed")
                    b = book(item["full_depth"])
                    seeded.add(token)
                    counters["pm_seeded_complete"] += 1
                    counters["pm_seeded_complete_m" + token_market[token]] += 1
                    if (start is None or at >= start) and (end is None or at <= end):
                        output[token].append(snapshot(at, b, epoch, 0))
                elif kind == "WS_EVENT":
                    ordinal = int(item["ws_local_ordinal"])
                    if not active or ordinal != last_ordinal + 1:
                        raise ValueError("gap in WS ordinal")
                    last_ordinal = ordinal
                    missing = item.get("uninitialized_tokens", [])
                    if not isinstance(missing, list):
                        raise ValueError("malformed uninitialized")
                    for token in missing:
                        if token in seeded and output.get(token):
                            output[token][-1]["valid_until"] = at
                        seeded.discard(token)
                    depths = item.get("full_books")
                    if not isinstance(depths, dict):
                        raise ValueError("invalid WS depths")
                    if item.get("gap_or_unanchored") is True:
                        raise ValueError("unanchored PM WS")
                    for token, data in depths.items():
                        if token not in seeded:
                            raise ValueError("unseeded token")
                        b = book(data)
                        counters["pm_ws_complete"] += 1
                        counters["pm_ws_complete_m" + token_market[token]] += 1
                        if (start is None or at >= start) and (end is None or at <= end):
                            output[token].append(snapshot(at, b, epoch, ordinal))
                    counters["pm_ws_events"] += 1
                else:
                    invalidate(at)
                    active = False
                    seeded.clear()
                    counters["pm_disconnects"] += 1
                last_at = at
            except (ValueError, KeyError, TypeError, OverflowError):
                if event_at is not None:
                    invalidate(event_at)
                elif last_at is not None:
                    invalidate(last_at)
                counters["pm_gaps_or_unseeded"] += 1
                active = False
                seeded.clear()
                last_at = None  # may only continue after a NEW epoch genuine seed
    return output


def latest_before(rows: list[dict[str, Any]], t: datetime) -> dict[str, Any] | None:
    times = [cast(datetime, row["at"]) for row in rows]
    index = bisect.bisect_right(times, t) - 1
    return rows[index] if index >= 0 else None


def horizon(rows: list[dict[str, Any]], at: datetime, milliseconds: int
            ) -> dict[str, Any] | None:
    """Requires independently recorded observations bracketing target within 100ms."""
    target = at + timedelta(milliseconds=milliseconds)
    times = [cast(datetime, row["at"]) for row in rows]
    index = bisect.bisect_left(times, target)
    if index < 1 or index == len(rows):
        return None
    before, after = rows[index - 1], rows[index]
    dt = (cast(datetime, after["at"]) - cast(datetime, before["at"])).total_seconds()
    if dt > MAX_TARGET_BRACKET_MS / 1000 or cast(datetime, before["at"]) > target:
        return None
    if cast(datetime, after["at"]) - target > timedelta(milliseconds=50):
        return None
    if before["epoch"] != after["epoch"]:
        return None
    return after


def compare(sig: dict[str, Any], pm: dict[str, Any], size: int,
            direction: str) -> tuple[Decimal, Decimal, Decimal] | None:
    a, b = sig["book"], pm["book"]
    if direction == "BUY_SIG_SELL_PM_YES":
        buy, sell = vwap(a["asks"], size), vwap(b["bids"], size)
    else:
        buy, sell = vwap(b["asks"], size), vwap(a["bids"], size)
    return (buy, sell, sell - buy) if buy is not None and sell is not None else None


def ranked(sig: dict[str, list[dict[str, Any]]],
           pm: dict[str, list[dict[str, Any]]], uncertainty: timedelta,
           counters: dict[str, int]) -> tuple[list[dict[str, object]], int]:
    candidates: dict[tuple[str, str, int], dict[str, object]] = {}
    matched = 0
    for market in SIG_MARKETS:
        token = PM_EXACT_SAME_YES[market][2]
        ss = sorted(sig.get(market, []), key=lambda x: cast(datetime, x["at"]))
        pp = sorted(pm.get(token, []), key=lambda x: cast(datetime, x["at"]))
        for snap in ss:
            at = cast(datetime, snap["at"])
            partner = latest_before(pp, at)
            if partner is None or (
                partner.get("valid_until") is not None
                and at >= cast(datetime, partner["valid_until"])
            ) or at - cast(datetime, partner["at"]) > (
                timedelta(milliseconds=MAX_LAG_MS)
            ) or cast(datetime, partner["at"]) + uncertainty >= at - uncertainty:
                counters["pair_unmatched"] += 1
                continue
            matched += 1
            counters["matched_m" + market] += 1
            for direction in ("BUY_SIG_SELL_PM_YES", "BUY_PM_SELL_SIG_YES"):
                for size in SIZES:
                    original = compare(snap, partner, size, direction)
                    if original is None:
                        counters["pair_size_unavailable"] += 1
                        continue
                    buy, sell, gap = original
                    if gap <= 0:
                        continue
                    capacity_size = min(
                        capacity(snap["book"], "asks" if direction.startswith("BUY_SIG")
                                 else "bids"),
                        capacity(partner["book"], "bids" if direction.startswith("BUY_SIG")
                                 else "asks"),
                    )
                    markouts: dict[str, str | None] = {}
                    for ms in HORIZONS_MS:
                        later_sig = horizon(ss, at, ms)
                        later_pm = horizon(pp, at, ms)
                        measured = (compare(later_sig, later_pm, size, direction)
                                    if later_sig is not None and later_pm is not None
                                    else None)
                        markouts[str(ms) + "ms_gap_delta"] = (
                            fmt(measured[2] - gap) if measured is not None else None)
                    candidate: dict[str, object] = {
                        "market": market,
                        "exchange": SIG_MARKETS[market],
                        "pm_yes_token": token,
                        "at": at.isoformat().replace("+00:00", "Z"),
                        "direction": direction,
                        "n": size,
                        "effective_whole_size": capacity_size,
                        "buy_ask_vwap": fmt(buy),
                        "sell_bid_vwap": fmt(sell),
                        "spread_per_share": fmt(gap),
                        "conditional_upside_zero_fee": fmt(gap * size),
                        "severe_adverse_25c_stress": fmt(-Decimal("0.25") * size),
                        "unhedged_long_leg_worst_case_stress": fmt(-buy * size),
                        "n_attempted": None,
                        "n_fill_observed": None,
                        "fill_rate": None,
                        "observed_tail_p05": None,
                        "markout": markouts,
                        "evidence": "CONDITIONAL_UNAUTHENTICATED_DEPTH_REFERENCE",
                    }
                    counters["positive_size_dislocations"] += 1
                    key = (market, direction, size)
                    old_best = candidates.get(key)
                    if old_best is None or Decimal(
                        str(candidate["conditional_upside_zero_fee"])
                    ) > Decimal(str(old_best["conditional_upside_zero_fee"])):
                        candidates[key] = candidate
    top = sorted(
        candidates.values(),
        key=lambda c: Decimal(str(c["conditional_upside_zero_fee"])),
        reverse=True,
    )[:5]
    return top, matched


def wrong_market_placebo(sig: dict[str, list[dict[str, Any]]],
                         pm: dict[str, list[dict[str, Any]]],
                         uncertainty: timedelta) -> dict[str, object]:
    """Deliberately WRONG-contract, non-executable negative control."""
    controls = {"223": "224", "224": "223", "270": "271", "271": "270"}
    count = 0
    best: Decimal | None = None
    for market, other_market in controls.items():
        other_token = PM_EXACT_SAME_YES[other_market][2]
        wrong = sorted(pm.get(other_token, []),
                       key=lambda item: cast(datetime, item["at"]))
        for sig_book in sig.get(market, []):
            at = cast(datetime, sig_book["at"])
            partner = latest_before(wrong, at)
            if partner is None or (
                partner.get("valid_until") is not None
                and at >= cast(datetime, partner["valid_until"])
            ):
                continue
            receipt = cast(datetime, partner["at"])
            if (receipt + uncertainty >= at - uncertainty or
                    at - receipt > timedelta(milliseconds=MAX_LAG_MS)):
                continue
            count += 1
            for direction in ("BUY_SIG_SELL_PM_YES", "BUY_PM_SELL_SIG_YES"):
                gap = compare(sig_book, partner, 1, direction)
                if gap is not None:
                    best = max(best, gap[2]) if best is not None else gap[2]
    return {
        "wrong_contract_pairs": count,
        "max_wrong_contract_gap_1share": fmt(best),
        "label": "INVALID_MARKET_NEGATIVE_CONTROL_NOT_TRADABLE",
    }


def proxy(sig_rows: list[dict[str, Any]], pm_rows: list[dict[str, Any]],
          counters: dict[str, int]) -> list[dict[str, object]]:
    sig_by_market: dict[str, list[tuple[datetime, Decimal, Decimal]]] = defaultdict(list)
    pm_by_token: dict[str, list[tuple[datetime, Decimal, Decimal]]] = defaultdict(list)
    for row in sig_rows:
        market = next((m for m, ex in SIG_MARKETS.items()
                       if ex == str(row.get("exchange_id"))), None)
        if market is None or row.get("event_type") != "BBO_SNAPSHOT":
            continue
        try:
            sig_by_market[market].append((
                utc(row["observed_at"]), number(row["best_bid"], price=True),
                number(row["best_ask"], price=True)))
        except (ValueError, KeyError):
            continue
    for row in pm_rows:
        token = str(row.get("token_id"))
        if token not in {t[2] for t in PM_EXACT_SAME_YES.values()} or (
            row.get("book_valid") is not True
        ):
            continue
        try:
            pm_by_token[token].append((
                utc(row["observed_at"]), number(row["best_bid"], price=True),
                number(row["best_ask"], price=True)))
        except (ValueError, KeyError):
            continue
    output: list[dict[str, object]] = []
    for market in SIG_MARKETS:
        token = PM_EXACT_SAME_YES[market][2]
        sig_quotes = sorted(sig_by_market[market])
        pm_quotes = sorted(pm_by_token[token])
        times = [r[0] for r in pm_quotes]
        maximum: Decimal | None = None
        matches = 0
        for at, bid, ask in sig_quotes:
            index = bisect.bisect_right(times, at) - 1
            if index < 0 or at - times[index] > timedelta(milliseconds=MAX_LAG_MS):
                continue
            pbid, pask = pm_quotes[index][1:]
            if bid >= ask or pbid >= pask:
                continue
            matches += 1
            gap = max(pbid - ask, bid - pask)
            maximum = gap if maximum is None else max(maximum, gap)
        if matches:
            output.append({"market": market, "n_proxy_pairs": matches,
                           "max_scalar_gap_per_share": fmt(maximum),
                           "label": "NONEXECUTABLE_PROXY_NO_DEPTH_NO_FILL"})
            counters["proxy_pairs"] += matches
    return output


def analyze(sig_root: Path | None, pm_root: Path | None, pm_events: Path | None,
            start: datetime | None = None, end: datetime | None = None,
            uncertainty_ms: int = 100) -> dict[str, object]:
    if uncertainty_ms < 0 or uncertainty_ms > 1000:
        raise ValueError("clock uncertainty must be 0..1000ms")
    counters: dict[str, int] = defaultdict(int)
    uncertainty = timedelta(milliseconds=uncertainty_ms)
    sr = parquet_rows(sig_root, "raw_events", counters, start, end)
    sn = parquet_rows(sig_root, "normalized_events", counters, start, end)
    po = parquet_rows(pm_root, "observations", counters, start, end)
    pd = parquet_rows(pm_root, "depth_snapshots", counters, start, end)
    # Existing periodically sampled PM depth can be truncated and has no
    # per-token seed/WS ordinal chain; count source-shaped rows, never admit.
    for row in pd:
        try:
            book(row)
            counters["pm_periodic_two_sided_unproven"] += 1
        except ValueError:
            counters["pm_periodic_incomplete"] += 1
    ss = sig_original(sr, counters, uncertainty)
    pp = pm_original(pm_events, counters, start, end, uncertainty)
    top, n_matched = ranked(ss, pp, uncertainty, counters)
    scalar = proxy(sn, po, counters)
    placebo = wrong_market_placebo(ss, pp, uncertainty)
    observed = [utc(x["local_receive_at"]) for x in sr if x.get("local_receive_at")]
    observed.extend(utc(x["observed_at"]) for x in po if x.get("observed_at"))
    observed.extend(utc(x["recorded_at"]) for x in pd if x.get("recorded_at"))
    observed.extend(utc(x["observed_at"]) for x in sn if x.get("observed_at"))
    observed.extend(cast(datetime, quote["at"])
                    for quotes in pp.values() for quote in quotes)
    return {
        "schema": "alpha2-zero-fee-offline-v1",
        "input": {
            "sig_root_exists": bool(sig_root is not None and sig_root.is_dir()),
            "sig_raw_shards": counters["raw_events_shards"],
            "pm_bridge_present": bool(pm_events is not None and pm_events.is_file()),
            "pm_periodic_depth_shards": counters["depth_snapshots_shards"],
            "window_min_utc": min(observed).isoformat() if observed else None,
            "window_max_utc": max(observed).isoformat() if observed else None,
            "clock_uncertainty_ms": uncertainty_ms,
        },
        "four_exact_markets": list(SIG_MARKETS),
        "per_market": [
            {
                "sig_market": market,
                "sig_exchange": SIG_MARKETS[market],
                "pm_condition": PM_EXACT_SAME_YES[market][1],
                "pm_yes_token": PM_EXACT_SAME_YES[market][2],
                "n_sig_batches": counters["sig_batches_m" + market],
                "n_sig_nonbook": counters["sig_nonbook_m" + market],
                "n_sig_full_claims": counters["sig_full_claims_m" + market],
                "n_sig_valid_full": counters["sig_valid_full_m" + market],
                "n_sig_eligible_full": counters["sig_eligible_full_m" + market],
                "n_sig_gaps": counters["sig_gaps_m" + market],
                "n_pm_proven_seed": counters["pm_seeded_complete_m" + market],
                "n_pm_ws_complete": counters["pm_ws_complete_m" + market],
                "n_pairs": counters["matched_m" + market],
            }
            for market in SIG_MARKETS
        ],
        "source_counts": dict(sorted(counters.items())),
        "n_sig_full_native_claims": counters["sig_full_claims"],
        "n_sig_valid_full": counters["sig_valid_full"],
        "n_pm_complete_seed": counters["pm_seeded_complete"],
        "n_matched_pairs": n_matched,
        "n_attempted": None,
        "n_fill_observed": None,
        "fees_sig": "0",
        "top_five_conditional_upside": top,
        "scalar_proxy": scalar,
        "placebo": placebo,
        "status": "CONDITIONAL_REFERENCE_ONLY" if n_matched else "DATA_GAP_NO_VERIFIED_PAIRS",
        "operator_boundaries": "NO_TRADES_NO_NETWORK_NO_SERVICE_CHANGES_NO_OFFHOST_UPLOAD",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sig-root", type=Path, required=True,
                        help="EXISTING local SIG archive root containing raw_events/ Parquet")
    parser.add_argument("--pm-root", type=Path, default=None,
                        help="EXISTING PM observations/depth_snapshots Parquet (PROXY only)")
    parser.add_argument("--pm-events", type=Path, default=None,
                        help="EXISTING alpha2-source-bridge-v1 PM seed/WS JSONL (not invented)")
    parser.add_argument("--start", default=None, help="UTC lower bound inclusive")
    parser.add_argument("--end", default=None, help="UTC upper bound inclusive")
    parser.add_argument("--uncertainty-ms", type=int, default=100)
    parser.add_argument(
        "--periodic-depth-proxy", action="store_true",
        help="Bounded archived native SIG vs PM PERIODIC full-depth numeric table; "
             "NEVER admitted or executable; window <=3h required",
    )
    args = parser.parse_args()
    if args.periodic_depth_proxy:
        if args.start is None or args.end is None:
            parser.error("--periodic-depth-proxy requires --start and --end UTC")
        from predictions_cup.research.archived_depth_proxy import analyze_archived_proxy

        result = analyze_archived_proxy(
            args.sig_root, args.pm_root, utc(args.start), utc(args.end),
        )
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
        return
    summary = analyze(args.sig_root, args.pm_root, args.pm_events,
                      utc(args.start) if args.start else None,
                      utc(args.end) if args.end else None,
                      uncertainty_ms=args.uncertainty_ms)
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    main()
