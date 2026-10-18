"""Bounded local-only archived SIG-vs-PM periodic-depth price proxy.

DELIBERATELY NOT EXECUTABLE: PM snapshots do not prove REST seed/WS ordinal;
SIG native source claims alone do not prove live quote retention. No fills.
"""
from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

from predictions_cup.research.native_paired_source_bridge import (
    PM_EXACT_SAME_YES,
    SIG_MARKETS,
)
from predictions_cup.research.zero_fee_spread_latency import (
    SIZES,
    book,
    compare,
    fmt,
    utc,
)

MAX_SHARDS_PER_STREAM = 160
MAX_COMPRESSED_BYTES_PER_STREAM = 90 * 1024 * 1024
MAX_ROWS_PER_STREAM = 210_000
PAIR_WINDOW_MS = 5000
SOURCE_AGE_MAX_MS = 5000
MARKETS = tuple(SIG_MARKETS)
REVERSE_EXCHANGE = {ex: market for market, ex in SIG_MARKETS.items()}
REVERSE_TOKEN = {identity[2]: market for market, identity in PM_EXACT_SAME_YES.items()}
DIRECTIONS = ("BUY_SIG_SELL_PM_YES", "BUY_PM_SELL_SIG_YES")


def _files(root: Path | None, stream: str, start: datetime,
           end: datetime) -> list[Path]:
    if root is None:
        return []
    directory = root / stream
    if not directory.is_dir():
        return []
    files: list[Path] = []
    # Minute-bucket recorder paths. Only enumerate hours overlapping input.
    first = start.replace(minute=0, second=0, microsecond=0)
    hour = first
    while hour <= end:
        bucket = directory / hour.strftime("%Y/%m/%d/%H")
        if bucket.is_dir():
            for path in bucket.glob("*.parquet"):
                # Known minute-stamped shards can be discarded by their name.
                try:
                    stamp = datetime.strptime(
                        path.name[:16], "%Y%m%dT%H%M%SZ"
                    ).replace(tzinfo=start.tzinfo)
                except ValueError:
                    stamp = None
                if stamp is None or start - timedelta(minutes=1) <= stamp <= end:
                    files.append(path)
        hour += timedelta(hours=1)
    # Explicit direct test fixtures, never scan arbitrary nested directories.
    if not files:
        files = list(directory.glob("*.parquet"))
    files.sort()
    if len(files) > MAX_SHARDS_PER_STREAM:
        raise ValueError("PROXY_SHARD_BUDGET_EXCEEDED_NARROW_WINDOW")
    if sum(p.stat().st_size for p in files) > MAX_COMPRESSED_BYTES_PER_STREAM:
        raise ValueError("PROXY_ARCHIVE_BYTES_BUDGET_EXCEEDED_NARROW_WINDOW")
    return files


def _stream(root: Path | None, name: str, start: datetime,
            end: datetime, wanted: tuple[str, ...],
            counts: dict[str, int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    files = _files(root, name, start, end)
    counts[name + "_shards"] = len(files)
    for path in files:
        reader = pq.ParquetFile(path)
        columns = [key for key in wanted if key in reader.schema_arrow.names]
        for batch in reader.iter_batches(batch_size=128, columns=columns):
            for row in batch.to_pylist():
                counts[name + "_scanned"] += 1
                if counts[name + "_scanned"] > MAX_ROWS_PER_STREAM:
                    raise ValueError("PROXY_ROW_BUDGET_EXCEEDED_NARROW_WINDOW")
                observed = row.get("local_receive_at", row.get("recorded_at"))
                if observed is None:
                    counts[name + "_missing_utc"] += 1
                    continue
                try:
                    at = utc(observed)
                except ValueError:
                    counts[name + "_invalid_utc"] += 1
                    continue
                if not start <= at < end:
                    continue
                # Reduce resident allocation before parsing raw ladders.
                if name == "depth_snapshots":
                    if str(row.get("token_id")) not in REVERSE_TOKEN:
                        continue
                else:
                    raw = row.get("raw_json")
                    if not isinstance(raw, str) or '"books"' not in raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                        levels = parsed.get("books", [])
                        if not isinstance(levels, list) or not any(
                            isinstance(level, dict)
                            and str(level.get("exchangeId")) in REVERSE_EXCHANGE
                            for level in levels
                        ):
                            continue
                    except (TypeError, ValueError, AttributeError):
                        continue
                result.append(row)
    return result


def _sig_books(rows: list[dict[str, Any]], counts: dict[str, int]
              ) -> dict[str, list[dict[str, Any]]]:
    books: dict[str, list[dict[str, Any]]] = defaultdict(list)
    prior: dict[tuple[str, str, int], tuple[int, datetime]] = {}
    # Epoch-based version proof is necessary for the separate admitted path;
    # here unique candidate versions are only a duplicate/reversal screen.
    rows.sort(key=lambda row: utc(row["local_receive_at"]))
    for row in rows:
        raw = row.get("raw_json")
        if not isinstance(raw, str):
            counts["sig_raw_invalid"] += 1
            continue
        try:
            body = json.loads(raw)
        except (TypeError, ValueError):
            counts["sig_raw_invalid"] += 1
            continue
        if not isinstance(body, dict) or not isinstance(body.get("books"), list):
            counts["sig_nonbook_or_malformed"] += 1
            continue
        receive = utc(row["local_receive_at"])
        for original in body["books"]:
            if not isinstance(original, dict):
                counts["sig_raw_invalid"] += 1
                continue
            market = REVERSE_EXCHANGE.get(str(original.get("exchangeId")))
            if market is None:
                continue
            counts["sig_full_claims_m" + market] += 1
            if not original.get("bids") or not original.get("asks"):
                counts["sig_incomplete_m" + market] += 1
                continue
            counts["sig_raw_native_two_sided_m" + market] += 1
            try:
                asof = original["asOf"]
                if (not isinstance(asof, dict) or
                    not isinstance(asof.get("sequence"), int) or
                    isinstance(asof.get("sequence"), bool)):
                    raise ValueError("native version unavailable")
                seq = asof["sequence"]
                publisher = utc(asof["at"])
                depth = book(original)
                if publisher > receive:
                    raise ValueError("publisher after receive")
                lag = (receive - publisher).total_seconds() * 1000
                if lag > SOURCE_AGE_MAX_MS:
                    raise ValueError("native age >5s")
                epoch = int(row.get("connection_epoch") or 0)
                session = str(row.get("session_id") or "")
                if not epoch or not session:
                    raise ValueError("missing epoch")
            except (ValueError, TypeError, KeyError):
                counts["sig_native_clock_or_depth_rejected_m" + market] += 1
                continue
            key = (market, session, epoch)
            previous = prior.get(key)
            if previous is not None and seq == previous[0] and publisher == previous[1]:
                counts["sig_duplicate_asof_m" + market] += 1
                continue
            if previous is not None and (
                seq <= previous[0] or publisher <= previous[1]
            ):
                counts["sig_native_reversal_m" + market] += 1
                continue
            prior[key] = (seq, publisher)
            counts["sig_unique_native_m" + market] += 1
            books[market].append({
                "at": receive, "publisher_at": publisher,
                "book": depth, "epoch": epoch, "revision": seq,
                "source_age_ms": int(lag), "input_uncertainty_ms": None,
            })
    return books


def _pm_books(rows: list[dict[str, Any]], counts: dict[str, int]
             ) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    last: dict[str, datetime] = {}
    for row in sorted(rows, key=lambda r: utc(r["recorded_at"])):
        token = str(row.get("token_id"))
        market = REVERSE_TOKEN.get(token)
        if market is None:
            continue
        counts["pm_periodic_m" + market] += 1
        if not row.get("bids") or not row.get("asks"):
            counts["pm_periodic_incomplete_m" + market] += 1
            continue
        counts["pm_raw_two_sided_m" + market] += 1
        receive = utc(row["recorded_at"])
        if token in last and receive <= last[token]:
            counts["pm_duplicate_or_reversed_receipt_m" + market] += 1
            continue
        try:
            depth = book(row)
        except ValueError:
            counts["pm_level_invalid_m" + market] += 1
            continue
        last[token] = receive
        source_at = row.get("source_timestamp")
        try:
            source_time = utc(source_at) if source_at is not None else None
        except ValueError:
            source_time = None
        if source_time is None:
            counts["pm_missing_publisher_clock_m" + market] += 1
            source_age: int | None = None
        else:
            source_age = int((receive - source_time).total_seconds() * 1000)
            if source_age < 0:
                counts["pm_future_publisher_clock_m" + market] += 1
            if source_age > SOURCE_AGE_MAX_MS:
                counts["pm_stale_publisher_clock_m" + market] += 1
        counts["pm_usable_periodic_m" + market] += 1
        output[market].append({
            "at": receive, "book": depth,
            "source_age_ms": source_age, "epoch": None,
            "input_uncertainty_ms": None,
        })
    return output


def _evaluate(sig: dict[str, list[dict[str, Any]]],
              pm: dict[str, list[dict[str, Any]]],
              counts: dict[str, int]) -> tuple[list[dict[str, object]],
                                                dict[str, dict[str, object]]]:
    metrics: dict[str, dict[str, object]] = {}
    maxima: dict[tuple[str, str, int], dict[str, object]] = {}
    for market in MARKETS:
        p = pm[market]
        stamps = [cast(datetime, row["at"]) for row in p]
        per: dict[str, object] = {
            "market": market, "exchange": SIG_MARKETS[market],
            "pm_yes_token": PM_EXACT_SAME_YES[market][2],
            "n_raw_native_two_sided": counts["sig_raw_native_two_sided_m" + market],
            "n_pm_two_sided_periodic": counts["pm_raw_two_sided_m" + market],
            "n_sig_unique_native": counts["sig_unique_native_m" + market],
            "n_pm_usable_periodic": counts["pm_usable_periodic_m" + market],
            "n_sig_duplicate_asof": counts["sig_duplicate_asof_m" + market],
            "n_sig_clock_or_depth_rejected":
                counts["sig_native_clock_or_depth_rejected_m" + market],
            "n_sig_native_reversal": counts["sig_native_reversal_m" + market],
            "n_pm_periodic_clock_rejected":
                counts["pm_duplicate_or_reversed_receipt_m" + market],
            "n_pm_stale_publisher": counts["pm_stale_publisher_clock_m" + market],
            "n_pm_missing_publisher_clock":
                counts["pm_missing_publisher_clock_m" + market],
            "n_matched_0_2s": 0, "n_matched_2_5s": 0,
            "n_matched_total": 0,
            "n_feasible": {
                direction: {str(n): 0 for n in SIZES} for direction in DIRECTIONS
            },
            "gap_extremes_per_share": {
                direction: {str(n): {"max": None, "min": None} for n in SIZES}
                for direction in DIRECTIONS
            },
            "source_clock_uncertainty_ms": None,
            "pm_seed_ws_proven": False,
            "250ms_markout": None, "1s_markout": None, "2s_markout": None,
            "n_order_attempted": None, "n_fill_observed": None,
            "n_admitted_pairs": 0,
            "classification": "NONEXECUTABLE_PROXY_PERIODIC_PM",
        }
        for item in sig[market]:
            at = cast(datetime, item["at"])
            idx = bisect.bisect_right(stamps, at) - 1
            if idx < 0:
                continue
            partner = p[idx]
            lag_ms = int((at - cast(datetime, partner["at"])).total_seconds() * 1000)
            if lag_ms < 0 or lag_ms > PAIR_WINDOW_MS:
                continue
            band = "n_matched_0_2s" if lag_ms <= 2000 else "n_matched_2_5s"
            per[band] = cast(int, per[band]) + 1
            per["n_matched_total"] = cast(int, per["n_matched_total"]) + 1
            for direction in DIRECTIONS:
                feasible = cast(dict[str, dict[str, int]], per["n_feasible"])
                ext = cast(dict[str, dict[str, dict[str, str | None]]],
                           per["gap_extremes_per_share"])
                for size in SIZES:
                    outcome = compare(item, partner, size, direction)
                    if outcome is None:
                        continue
                    buy, sell, gap = outcome
                    feasible[direction][str(size)] += 1
                    limits = ext[direction][str(size)]
                    value = fmt(gap)
                    if value is None:
                        continue
                    if limits["max"] is None or gap > Decimal(str(limits["max"])):
                        limits["max"] = value
                    if limits["min"] is None or gap < Decimal(str(limits["min"])):
                        limits["min"] = value
                    if gap <= 0:
                        continue
                    key = (market, direction, size)
                    value_total = fmt(gap * size)
                    prior = maxima.get(key)
                    if prior is None or Decimal(str(value_total)) > Decimal(
                        str(prior["maximum_positive_proxy_total"])
                    ):
                        maxima[key] = {
                            "market": market, "direction": direction,
                            "size_whole_shares": size,
                            "sample_receive_utc": at.isoformat(),
                            "sig_native_publisher_utc":
                                cast(datetime, item["publisher_at"]).isoformat(),
                            "pm_periodic_recorded_utc":
                                cast(datetime, partner["at"]).isoformat(),
                            "snapshot_age_ms": lag_ms,
                            "sig_source_age_ms": item["source_age_ms"],
                            "pm_source_age_ms": partner["source_age_ms"],
                            "sig_input_clock_uncertainty_ms": None,
                            "pm_input_clock_uncertainty_ms": None,
                            "buy_ask_vwap": fmt(buy),
                            "sell_bid_vwap": fmt(sell),
                            "maximum_positive_proxy_per_share": value,
                            "maximum_positive_proxy_total": value_total,
                            "severe_25c_per_share_adverse_stress_total":
                                fmt(-Decimal("0.25") * size),
                            "unhedged_buy_premium_total_loss_stress":
                                fmt(-buy * size),
                            "observed_tail_loss": None,
                            "observed_fill_count": None,
                            "label": "NONEXECUTABLE_PROXY_NOT_ADMITTED",
                        }
        metrics[market] = per
    top = sorted(
        maxima.values(),
        key=lambda r: Decimal(str(r["maximum_positive_proxy_total"])),
        reverse=True,
    )[:5]
    return top, metrics


def _placebo(sig: dict[str, list[dict[str, Any]]],
             pm: dict[str, list[dict[str, Any]]]) -> dict[str, object]:
    wrong = {"223": "224", "224": "223", "270": "271", "271": "270"}
    count = 0
    positive = 0
    for market, counterpart in wrong.items():
        p = pm[counterpart]
        stamps = [cast(datetime, row["at"]) for row in p]
        for item in sig[market]:
            at = cast(datetime, item["at"])
            idx = bisect.bisect_right(stamps, at) - 1
            if idx < 0:
                continue
            lag = (at - stamps[idx]).total_seconds() * 1000
            if lag < 0 or lag > PAIR_WINDOW_MS:
                continue
            count += 1
            for direction in DIRECTIONS:
                outcome = compare(item, p[idx], 1, direction)
                if outcome is not None and outcome[2] > 0:
                    positive += 1
    return {"wrong_market_swapped_yes_pairs": count,
            "wrong_market_positive_1_share_directions": positive,
            "classification": "PLACEBO_WRONG_CONTRACT_NOT_TRADABLE"}


def analyze_archived_proxy(sig_root: Path | None, pm_root: Path | None,
                           start: datetime, end: datetime) -> dict[str, object]:
    """Actual archived depth reference only; independent of admitted join."""
    if start >= end or end - start > timedelta(hours=3):
        raise ValueError("PROXY_REQUIRES_BOUNDED_MAX_3H_UTC_WINDOW")
    counts: dict[str, int] = defaultdict(int)
    sr = _stream(sig_root, "raw_events", start, end,
                 ("raw_json", "local_receive_at", "connection_epoch",
                  "session_id", "topic"), counts)
    pd = _stream(pm_root, "depth_snapshots", start, end,
                 ("token_id", "recorded_at", "source_timestamp",
                  "state_observed_at", "bids", "asks"), counts)
    sig = _sig_books(sr, counts)
    pm = _pm_books(pd, counts)
    top, metrics = _evaluate(sig, pm, counts)
    return {
        "schema": "alpha2-nonexecutable-periodic-depth-proxy-v1",
        "utc_window_start": start.isoformat(),
        "utc_window_end_exclusive": end.isoformat(),
        "matched_window_bins_ms": ["0..2000", "(2000..5000]"],
        "sig_platform_fee": "0",
        "per_market": [metrics[market] for market in MARKETS],
        "top_five_measured_positive_depth_proxies": top if top else None,
        "wrong_market_placebo": _placebo(sig, pm),
        "n_admitted_pairs": 0,
        "n_actual_executable_fills": None,
        "true_after_spread_latency_fill_pnl": None,
        "source_gap_uncertainty": (
            "SIG per-epoch native seq/publisher screened for repeat/regression; "
            "PM original REST seed+each WS ordinal and source clock uncertainty UNKNOWN. "
            "Periodic PM snapshot may be truncated or stale; no quote survival proof."
        ),
        "input_counts": dict(sorted(counts.items())),
        "label": "NONEXECUTABLE_PROXY_ONLY_NO_ADMISSION",
    }
