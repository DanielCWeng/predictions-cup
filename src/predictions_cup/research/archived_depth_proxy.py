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
            # Pilot parsed exact-market rows once; full reader remains unchanged.
            body = row["_pilot_decoded"] if "_pilot_decoded" in row else json.loads(raw)
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
        except (TypeError, ValueError):
            source_time = None
        if source_time is None:
            counts["pm_missing_publisher_clock_m" + market] += 1
            source_age: int | None = None
        else:
            source_age = int((receive - source_time).total_seconds() * 1000)
            if source_time > receive:
                counts["pm_future_publisher_clock_m" + market] += 1
            if receive - source_time > timedelta(milliseconds=SOURCE_AGE_MAX_MS):
                counts["pm_stale_publisher_clock_m" + market] += 1
        # Deliberately retain every price-valid periodic row for ALL_AGE diagnostics.
        # Fresh reference admission is decided independently at the SIG receipt.
        counts["pm_usable_periodic_m" + market] += 1
        output[market].append({
            "at": receive, "book": depth, "publisher_at": source_time,
            "source_age_ms": source_age, "epoch": None,
            "input_uncertainty_ms": None,
        })
    return output


def _fresh_reason(sig_at: datetime, pm: dict[str, Any]) -> str | None:
    """Apparent source-age screen only, NEVER certified quote/WS admission."""
    publisher = pm.get("publisher_at")
    if not isinstance(publisher, datetime):
        return "missing_pm_publisher"
    recorded = cast(datetime, pm["at"])
    if publisher > recorded or publisher > sig_at:
        return "future_pm_publisher"
    if sig_at - publisher > timedelta(milliseconds=SOURCE_AGE_MAX_MS):
        return "stale_effective_pm_publisher_age"
    return None


def _blank_extremes() -> dict[str, dict[str, dict[str, str | None]]]:
    return {
        direction: {str(n): {"max": None, "min": None} for n in SIZES}
        for direction in DIRECTIONS
    }


def _blank_counts() -> dict[str, dict[str, int]]:
    return {direction: {str(n): 0 for n in SIZES} for direction in DIRECTIONS}


def _extreme(extremes: dict[str, dict[str, dict[str, str | None]]],
             direction: str, size: int, gap: Decimal) -> None:
    limits = extremes[direction][str(size)]
    amount = fmt(gap)
    if limits["max"] is None or gap > Decimal(str(limits["max"])):
        limits["max"] = amount
    if limits["min"] is None or gap < Decimal(str(limits["min"])):
        limits["min"] = amount


def _candidate(market: str, item: dict[str, Any], partner: dict[str, Any],
               direction: str, size: int, buy: Decimal, sell: Decimal,
               gap: Decimal, label: str) -> dict[str, object]:
    at = cast(datetime, item["at"])
    recorded = cast(datetime, partner["at"])
    publisher = partner.get("publisher_at")
    return {
        "market": market, "direction": direction, "size_whole_shares": size,
        "sample_receive_utc": at.isoformat(),
        "sig_native_publisher_utc":
            cast(datetime, item["publisher_at"]).isoformat(),
        "pm_periodic_recorded_utc": recorded.isoformat(),
        "pm_publisher_utc": (
            publisher.isoformat() if isinstance(publisher, datetime) else None
        ),
        "snapshot_age_ms": int((at - recorded).total_seconds() * 1000),
        "sig_source_age_ms": item["source_age_ms"],
        "pm_source_age_ms": partner["source_age_ms"],
        "pm_effective_publisher_age_ms": (
            int((at - publisher).total_seconds() * 1000)
            if isinstance(publisher, datetime) else None
        ),
        "sig_input_clock_uncertainty_ms": None,
        "pm_input_clock_uncertainty_ms": None,
        "buy_ask_vwap": fmt(buy), "sell_bid_vwap": fmt(sell),
        "maximum_positive_proxy_per_share": fmt(gap),
        "maximum_positive_proxy_total": fmt(gap * size),
        "severe_25c_per_share_adverse_stress_total":
            fmt(-Decimal("0.25") * size),
        "unhedged_buy_premium_total_loss_stress": fmt(-buy * size),
        "observed_tail_loss": None, "observed_fill_count": None,
        "label": label,
    }


def _evaluate(sig: dict[str, list[dict[str, Any]]],
              pm: dict[str, list[dict[str, Any]]],
              counts: dict[str, int]) -> tuple[
                  list[dict[str, object]], list[dict[str, object]],
                  dict[str, dict[str, object]],
              ]:
    metrics: dict[str, dict[str, object]] = {}
    all_age_top: dict[tuple[str, str, int], dict[str, object]] = {}
    fresh_top: dict[tuple[str, str, int], dict[str, object]] = {}
    for market in MARKETS:
        p = sorted(pm[market], key=lambda x: cast(datetime, x["at"]))
        stamps = [cast(datetime, row["at"]) for row in p]
        all_age_feasible = _blank_counts()
        all_age_ext = _blank_extremes()
        all_age_total = _blank_extremes()
        fresh_feasible = _blank_counts()
        fresh_ext = _blank_extremes()
        fresh_total = _blank_extremes()
        fresh_positive = _blank_counts()
        fresh_infeasible = _blank_counts()
        all_age_infeasible = _blank_counts()
        rejects = {
            "missing_pm_publisher": 0,
            "future_pm_publisher": 0,
            "stale_effective_pm_publisher_age": 0,
            "native_clock_or_depth_rejected":
                counts["sig_native_clock_or_depth_rejected_m" + market],
            "native_revision_reversed": counts["sig_native_reversal_m" + market],
            "native_duplicate_asof": counts["sig_duplicate_asof_m" + market],
        }
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
            "n_pm_future_publisher_clock":
                counts["pm_future_publisher_clock_m" + market],
            "n_matched_0_2s": 0, "n_matched_2_5s": 0,
            "n_matched_total": 0,
            # Backward-compatible fields are explicitly ALL_AGE diagnostics;
            # never consult them when deciding the PRIMARY top-five.
            "n_feasible": all_age_feasible,
            "gap_extremes_per_share": all_age_ext,
            "gap_extremes_total": all_age_total,
            "all_age_nonexecutable_diagnostic": {
                "n_feasible": all_age_feasible,
                "n_infeasible": all_age_infeasible,
                "gap_extremes_per_share": all_age_ext,
                "gap_extremes_total": all_age_total,
                "classification": "ALL_AGE_NONEXECUTABLE_DIAGNOSTIC",
            },
            "fresh_reference_proxy": {
                "n_matched": 0, "n_matched_0_2s": 0, "n_matched_2_5s": 0,
                "n_feasible": fresh_feasible, "n_infeasible": fresh_infeasible,
                "n_positive": fresh_positive,
                "gap_extremes_per_share": fresh_ext,
                "gap_extremes_total": fresh_total,
                "rejected_matched_receipts": rejects,
                "classification": "PUBLISHER_FRESH_UNCERTIFIED_NONEXECUTABLE",
            },
            "source_clock_uncertainty_ms": None,
            "pm_seed_ws_proven": False,
            "250ms_markout": None, "1s_markout": None, "2s_markout": None,
            "n_order_attempted": None, "n_fill_observed": None,
            "n_admitted_pairs": 0,
            "classification": "NONEXECUTABLE_PROXY_PERIODIC_PM",
        }
        fresh = cast(dict[str, Any], per["fresh_reference_proxy"])
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
            rejection = _fresh_reason(at, partner)
            if rejection is not None:
                rejects[rejection] += 1
            else:
                fresh["n_matched"] += 1
                fresh[band] += 1
            for direction in DIRECTIONS:
                for size in SIZES:
                    outcome = compare(item, partner, size, direction)
                    if outcome is None:
                        all_age_infeasible[direction][str(size)] += 1
                        if rejection is None:
                            fresh_infeasible[direction][str(size)] += 1
                        continue
                    buy, sell, gap = outcome
                    all_age_feasible[direction][str(size)] += 1
                    _extreme(all_age_ext, direction, size, gap)
                    _extreme(all_age_total, direction, size, gap * size)
                    if gap > 0:
                        key = (market, direction, size)
                        candidate = _candidate(
                            market, item, partner, direction, size,
                            buy, sell, gap, "ALL_AGE_NONEXECUTABLE_DIAGNOSTIC",
                        )
                        old = all_age_top.get(key)
                        if old is None or gap * size > Decimal(
                            str(old["maximum_positive_proxy_total"])
                        ):
                            all_age_top[key] = candidate
                    if rejection is not None:
                        continue
                    fresh_feasible[direction][str(size)] += 1
                    _extreme(fresh_ext, direction, size, gap)
                    _extreme(fresh_total, direction, size, gap * size)
                    if gap <= 0:
                        continue
                    fresh_positive[direction][str(size)] += 1
                    key = (market, direction, size)
                    candidate = _candidate(
                        market, item, partner, direction, size,
                        buy, sell, gap,
                        "PUBLISHER_FRESH_NONEXECUTABLE_PROXY_NOT_ADMITTED",
                    )
                    old = fresh_top.get(key)
                    if old is None or gap * size > Decimal(
                        str(old["maximum_positive_proxy_total"])
                    ):
                        fresh_top[key] = candidate
        metrics[market] = per

    def sorted_top(candidates: dict[tuple[str, str, int], dict[str, object]]
                   ) -> list[dict[str, object]]:
        return sorted(
            candidates.values(),
            key=lambda row: Decimal(str(row["maximum_positive_proxy_total"])),
            reverse=True,
        )[:5]

    return sorted_top(fresh_top), sorted_top(all_age_top), metrics


def _placebo(sig: dict[str, list[dict[str, Any]]],
             pm: dict[str, list[dict[str, Any]]],
             shift_seconds: int = 0) -> dict[str, object]:
    """Wrong-contract or 60s time-shift controls with IDENTICAL age rules."""
    wrong = {"223": "224", "224": "223", "270": "271", "271": "270"}
    count = 0
    fresh_count = 0
    positive_all = 0
    positive_fresh = 0
    for market in MARKETS:
        other = market if shift_seconds else wrong[market]
        original = pm[other]
        shift = timedelta(seconds=shift_seconds)
        p = [{
            **row, "at": cast(datetime, row["at"]) + shift,
            "publisher_at": (
                cast(datetime, row["publisher_at"]) + shift
                if isinstance(row.get("publisher_at"), datetime) else None
            ),
        } for row in original]
        stamps = [cast(datetime, row["at"]) for row in p]
        for item in sig[market]:
            at = cast(datetime, item["at"])
            idx = bisect.bisect_right(stamps, at) - 1
            if idx < 0:
                continue
            partner = p[idx]
            lag = (at - cast(datetime, partner["at"])).total_seconds() * 1000
            if lag < 0 or lag > PAIR_WINDOW_MS:
                continue
            count += 1
            is_fresh = _fresh_reason(at, partner) is None
            if is_fresh:
                fresh_count += 1
            for direction in DIRECTIONS:
                result = compare(item, partner, 1, direction)
                if result is not None and result[2] > 0:
                    positive_all += 1
                    if is_fresh:
                        positive_fresh += 1
    return {
        "wrong_market_swapped_yes_pairs" if not shift_seconds
            else "shifted_60s_same_market_pairs": count,
        "all_age_positive_1share_directions": positive_all,
        "publisher_fresh_pairs": fresh_count,
        "publisher_fresh_positive_1share_directions": positive_fresh,
        "classification": "PLACEBO_INVALID_NOT_TRADABLE",
    }


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
    top, all_age_top, metrics = _evaluate(sig, pm, counts)
    return {
        "schema": "alpha2-nonexecutable-periodic-depth-proxy-v1",
        "utc_window_start": start.isoformat(),
        "utc_window_end_exclusive": end.isoformat(),
        "matched_window_bins_ms": ["0..2000", "(2000..5000]"],
        "sig_platform_fee": "0",
        "per_market": [metrics[market] for market in MARKETS],
        "top_five_measured_positive_depth_proxies": top if top else None,
        "top_five_publisher_fresh_reference_proxies": top if top else None,
        "top_five_all_age_nonexecutable_diagnostic": all_age_top or None,
        "wrong_market_placebo": _placebo(sig, pm),
        "time_shift_placebo": _placebo(sig, pm, shift_seconds=60),
        "n_publisher_fresh_matched_reference_pairs": sum(
            cast(int, cast(dict[str, Any], metrics[m]["fresh_reference_proxy"])["n_matched"])
            for m in MARKETS
        ),
        "source_admitted": False,
        "n_admitted_pairs": 0,
        "n_actual_executable_fills": None,
        "true_after_spread_latency_fill_pnl": None,
        "source_gap_uncertainty": (
            "SIG per-epoch native seq/publisher screened for repeat/regression; "
            "PM original REST seed+each WS ordinal and source clock uncertainty UNKNOWN. "
            "Primary price rank only requires apparent PM publisher age <=5s at SIG "
            "receipt, with nonfuture publisher timestamp at PM recording. "
            "ALL_AGE diagnostics may be stale and MUST NOT be ranked as opportunity. "
            "Periodic PM snapshot may be truncated; no quote survival proof."
        ),
        "input_counts": dict(sorted(counts.items())),
        "label": "NONEXECUTABLE_PROXY_ONLY_NO_ADMISSION",
    }


# Prospective West pilot was precommitted BEFORE sampling any publisher-fresh
# prices: take the aligned ten minutes starting 30min into the previous 2h scan.
PILOT_FIXED_START_UTC = "2026-10-18T17:20:00Z"
PILOT_FIXED_END_UTC = "2026-10-18T17:30:00Z"
PILOT_MAX_DURATION = timedelta(minutes=10)
PILOT_MAX_SHARDS_PER_STREAM = 14
PILOT_MAX_ROWS_PER_STREAM = 35_000


def _pilot_files(root: Path | None, stream: str, start: datetime,
                 end: datetime) -> list[Path]:
    """Prune exact minute-named shards BEFORE opening their Parquet metadata.

    Single flat-file test fixtures are supported only when there is NO archive
    dated hierarchy. Never silently fall back to an entire dated archive.
    """
    if root is None:
        return []
    directory = root / stream
    if not directory.is_dir():
        return []
    paths: list[Path] = []
    hour = (start - timedelta(minutes=1)).replace(
        minute=0, second=0, microsecond=0,
    )
    while hour < end:
        bucket = directory / hour.strftime("%Y/%m/%d/%H")
        if bucket.is_dir():
            for path in bucket.glob("*.parquet"):
                try:
                    stamp = datetime.strptime(
                        path.name[:16], "%Y%m%dT%H%M%SZ",
                    ).replace(tzinfo=start.tzinfo)
                except ValueError as exc:
                    raise ValueError("PILOT_UNPRUNABLE_NAMED_SHARD") from exc
                if start - timedelta(minutes=1) <= stamp < end:
                    paths.append(path)
        hour += timedelta(hours=1)
    if not paths and not (directory / start.strftime("%Y")).exists():
        # Only named direct flat fixtures, never recursive historical globbing.
        paths = sorted(directory.glob("*.parquet"))
    paths.sort()
    if len(paths) > PILOT_MAX_SHARDS_PER_STREAM:
        raise ValueError("PILOT_SHARD_CAP_EXCEEDED")
    if sum(path.stat().st_size for path in paths) > 12 * 1024 * 1024:
        raise ValueError("PILOT_BYTES_CAP_EXCEEDED")
    return paths


def _pilot_stream(root: Path | None, stream: str, start: datetime,
                  end: datetime, counters: dict[str, int]
                  ) -> list[dict[str, Any]]:
    """Stream batches and retain ONLY four exact mapped books, not raw universe.

    Off-WEST pilot deliberately avoids CPU sleeps: contention reduction by
    sleeping has not been experimentally proven, so no invented CPU cap.
    """
    retained: list[dict[str, Any]] = []
    paths = _pilot_files(root, stream, start, end)
    counters[stream + "_shards"] = len(paths)
    columns = (
        ("local_receive_at", "raw_json", "connection_epoch", "session_id")
        if stream == "raw_events" else
        ("token_id", "recorded_at", "source_timestamp", "bids", "asks")
    )
    for path in paths:
        reader = pq.ParquetFile(path)
        projected = [key for key in columns if key in reader.schema_arrow.names]
        for batch in reader.iter_batches(batch_size=64, columns=projected):
            for row in batch.to_pylist():
                counters[stream + "_scanned"] += 1
                if counters[stream + "_scanned"] > PILOT_MAX_ROWS_PER_STREAM:
                    raise ValueError("PILOT_ROWS_CAP_EXCEEDED")
                at_raw = row.get(
                    "local_receive_at" if stream == "raw_events" else "recorded_at"
                )
                try:
                    at = utc(at_raw)
                except (ValueError, TypeError):
                    counters[stream + "_invalid_utc"] += 1
                    continue
                if not start <= at < end:
                    continue
                if stream == "depth_snapshots":
                    if str(row.get("token_id")) not in REVERSE_TOKEN:
                        continue
                else:
                    raw = row.get("raw_json")
                    if not isinstance(raw, str) or '"books"' not in raw:
                        continue
                    try:
                        decoded = json.loads(raw)
                        originals = decoded.get("books", [])
                        if not isinstance(originals, list) or not any(
                            isinstance(item, dict)
                            and str(item.get("exchangeId")) in REVERSE_EXCHANGE
                            for item in originals
                        ):
                            continue
                    except (ValueError, TypeError, AttributeError):
                        continue
                    row["_pilot_decoded"] = decoded
                retained.append(row)
                counters[stream + "_retained_exact_rows"] += 1
                if len(retained) > 4_000:
                    raise ValueError("PILOT_TARGET_ROWS_CAP_EXCEEDED")
    return retained


def _pilot_strip_candidate(candidate: dict[str, object]) -> dict[str, object]:
    # Stdout is aggregate-only, without original token IDs or per-tick times.
    private_keys = {
        "sample_receive_utc", "sig_native_publisher_utc",
        "pm_periodic_recorded_utc", "pm_publisher_utc",
    }
    return {key: value for key, value in candidate.items()
            if key not in private_keys}


def analyze_short_window_pilot(sig_root: Path | None, pm_root: Path | None,
                               start: datetime, end: datetime
                               ) -> dict[str, object]:
    """One completed <=10m slice, bounded. No inference for unseen history."""
    if start >= end or end - start > PILOT_MAX_DURATION:
        raise ValueError("PILOT_REQUIRES_SINGLE_MAX_10MIN_UTC_SEGMENT")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("PILOT_REQUIRES_AWARE_UTC_TIMESTAMPS")
    counts: dict[str, int] = defaultdict(int)
    sr = _pilot_stream(sig_root, "raw_events", start, end, counts)
    pd = _pilot_stream(pm_root, "depth_snapshots", start, end, counts)
    data = _sig_books(sr, counts)
    pm = _pm_books(pd, counts)
    fresh_top, all_top, metrics = _evaluate(data, pm, counts)
    # No PM before start is queried; SIG in first 5s without an earlier
    # in-window PM sample cannot be called a measured absence of opportunity.
    boundary_unknown = 0
    boundary_by_market: dict[str, int] = {}
    for market in MARKETS:
        earliest_pm = min(
            (cast(datetime, item["at"]) for item in pm[market]),
            default=None,
        )
        uncertain = sum(
            1 for item in data[market]
            if cast(datetime, item["at"]) < start + timedelta(seconds=5)
            and (earliest_pm is None
                 or cast(datetime, item["at"]) < earliest_pm)
        )
        boundary_by_market[market] = uncertain
        boundary_unknown += uncertain
    if not counts["raw_events_shards"] or not counts["depth_snapshots_shards"]:
        status = "UNKNOWN_ARCHIVE_INPUT_INCOMPLETE"
    elif boundary_unknown:
        status = "WINDOW_COMPLETE_WITH_UNKNOWN_PRESTART_PM_CONTEXT"
    else:
        status = "WINDOW_COMPLETE_OBSERVED_ONLY"
    market_rows = []
    for market in MARKETS:
        output = {
            k: v for k, v in metrics[market].items()
            if k != "pm_yes_token"
        }
        output["n_boundary_prior_pm_context_unknown"] = boundary_by_market[market]
        market_rows.append(output)
    def safe_top(items: list[dict[str, object]]) -> list[dict[str, object]] | None:
        return ([_pilot_strip_candidate(row) for row in items] if items else None)
    return {
        "schema": "alpha2-short-window-pilot-v1",
        "classification": "WINDOW_LIMITED_ONLY_NONEXECUTABLE",
        "selected_window_rule": "2026-10-18 PRECOMMITTED_START_16:50Z_PLUS_30MIN_10MIN",
        "utc_completed_start": start.isoformat(),
        "utc_completed_end_exclusive": end.isoformat(),
        "other_unseen_intervals": "UNKNOWN_NOT_SCANNED",
        "status": status,
        "n_boundary_prior_pm_context_unknown": boundary_unknown,
        "n_publisher_fresh_matched_reference_pairs": sum(
            cast(int, cast(dict[str, Any],
                           metrics[market]["fresh_reference_proxy"])["n_matched"])
            for market in MARKETS
        ),
        "per_market": market_rows,
        "top_five_publisher_fresh_reference_proxies": safe_top(fresh_top),
        "top_five_all_age_nonexecutable_diagnostic": safe_top(all_top),
        "wrong_market_placebo": _placebo(data, pm),
        "time_shift_placebo": _placebo(data, pm, shift_seconds=60),
        "input_counts": dict(sorted(counts.items())),
        "n_admitted_pairs": 0,
        "source_admitted": False,
        "n_actual_executable_fills": None,
        "real_after_spread_fill_pnl": None,
        "pm_seed_ws_and_clock_proof": "UNKNOWN",
        "label": "NONEXECUTABLE_NOT_A_FULL_WINDOW_RESULT",
    }
