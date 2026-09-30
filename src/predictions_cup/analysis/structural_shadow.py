"""Evaluate R3 live structural shocks from launch capture artifacts.

This module is deliberately shadow-only. It detects source shocks from the explicit
R3 capture allowlist and measures subsequent SIG BBO response. It never places orders
and never labels gross top-of-book markouts as net alpha.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.dataset as ds


@dataclass(frozen=True, slots=True)
class QuotePoint:
    time_ns: int
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class SourceShock:
    candidate_id: str
    time_ns: int
    direction: int
    score: float
    trigger: str
    independent: bool
    source_value_before: float | None
    source_value_after: float | None
    details: dict[str, float | bool | str]


def _as_ns(value: object) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return int(round(value.timestamp() * 1_000_000_000))
    if isinstance(value, np.datetime64):
        return int(value.astype("datetime64[ns]").astype(np.int64))
    raise TypeError(f"unsupported timestamp type: {type(value)!r}")


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quote(value: dict[str, object], *, time_field: str = "observed_at") -> QuotePoint | None:
    bid = _finite_float(value.get("best_bid"))
    ask = _finite_float(value.get("best_ask"))
    observed = value.get(time_field)
    if bid is None or ask is None or observed is None:
        return None
    if not (0.0 <= bid <= ask <= 1.0):
        return None
    return QuotePoint(time_ns=_as_ns(observed), bid=bid, ask=ask)


def _dataset(root: Path, stream: str) -> ds.Dataset | None:
    directory = root / stream
    if not directory.exists():
        return None
    files = tuple(sorted(directory.rglob("*.parquet")))
    if not files:
        return None
    return ds.dataset([str(path) for path in files], format="parquet")


def _load_pm_quotes(root: Path, token_ids: set[str]) -> dict[str, list[QuotePoint]]:
    dataset = _dataset(root, "observations")
    if dataset is None:
        return {}
    table = dataset.to_table(
        columns=["token_id", "observed_at", "best_bid", "best_ask", "book_valid"],
        filter=ds.field("token_id").isin(sorted(token_ids)),
    )
    out: dict[str, dict[int, QuotePoint]] = {token: {} for token in token_ids}
    for row in table.to_pylist():
        if row.get("book_valid") is False:
            continue
        token = str(row.get("token_id") or "")
        if token not in out:
            continue
        point = _quote(row)
        if point is not None:
            out[token][point.time_ns] = point
    return {
        token: [by_time[key] for key in sorted(by_time)]
        for token, by_time in out.items()
        if by_time
    }


def _load_sig_quotes(root: Path, exchange_ids: set[str]) -> dict[str, list[QuotePoint]]:
    dataset = _dataset(root, "normalized_events")
    if dataset is None:
        return {}
    table = dataset.to_table(
        columns=["event_type", "exchange_id", "observed_at", "best_bid", "best_ask"],
        filter=(
            (ds.field("event_type") == "BBO_SNAPSHOT")
            & ds.field("exchange_id").isin(sorted(exchange_ids))
        ),
    )
    out: dict[str, dict[int, QuotePoint]] = {exchange: {} for exchange in exchange_ids}
    for row in table.to_pylist():
        exchange = str(row.get("exchange_id") or "")
        if exchange not in out:
            continue
        point = _quote(row)
        if point is not None:
            out[exchange][point.time_ns] = point
    return {
        exchange: [by_time[key] for key in sorted(by_time)]
        for exchange, by_time in out.items()
        if by_time
    }


def _simplex_projection(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered)
    indexes = np.arange(1, len(values) + 1)
    valid = ordered - (cumulative - 1.0) / indexes > 0
    if not np.any(valid):
        return np.full_like(values, 1.0 / len(values))
    rho = int(np.where(valid)[0][-1])
    theta = float((cumulative[rho] - 1.0) / (rho + 1))
    return np.maximum(values - theta, 0.0)


def _kl_projection(values: np.ndarray) -> np.ndarray:
    clipped = np.maximum(np.asarray(values, dtype=np.float64), 1e-12)
    total = float(clipped.sum())
    if total <= 0:
        return np.asarray(
            np.full_like(clipped, 1.0 / len(clipped)),
            dtype=np.float64,
        )
    return np.asarray(clipped / total, dtype=np.float64)


def _clr(values: np.ndarray) -> np.ndarray:
    clipped = np.maximum(np.asarray(values, dtype=np.float64), 1e-9)
    logs = np.log(clipped)
    return np.asarray(logs - float(logs.mean()), dtype=np.float64)


def _binary_clr_distance(before: float, after: float) -> float:
    b = np.asarray([before, 1.0 - before], dtype=float)
    a = np.asarray([after, 1.0 - after], dtype=float)
    return float(np.linalg.norm(_clr(a) - _clr(b)))


def _sign(value: float, tolerance: float = 1e-12) -> int:
    if value > tolerance:
        return 1
    if value < -tolerance:
        return -1
    return 0


def _seat_states(
    quotes: dict[str, list[QuotePoint]],
    tokens: list[str],
    *,
    freshness_seconds: int,
) -> list[dict[str, Any]]:
    events: list[tuple[int, str, QuotePoint]] = []
    for token in tokens:
        events.extend((point.time_ns, token, point) for point in quotes.get(token, ()))
    events.sort(key=lambda item: (item[0], item[1]))
    latest: dict[str, QuotePoint] = {}
    states: list[dict[str, Any]] = []
    previous_raw: tuple[float, ...] | None = None
    freshness_ns = freshness_seconds * 1_000_000_000

    index = 0
    while index < len(events):
        time_ns = events[index][0]
        end = index
        while end < len(events) and events[end][0] == time_ns:
            _, token, point = events[end]
            latest[token] = point
            end += 1
        index = end

        if any(token not in latest for token in tokens):
            continue
        if max(time_ns - latest[token].time_ns for token in tokens) > freshness_ns:
            continue
        raw = tuple(latest[token].mid for token in tokens)
        if raw == previous_raw:
            continue
        previous_raw = raw
        vector = np.asarray(raw, dtype=float)
        qp = _simplex_projection(vector)
        kl = _kl_projection(vector)
        states.append(
            {
                "time_ns": time_ns,
                "raw": vector,
                "qp": qp,
                "kl": kl,
                "t50_qp": float(qp[3:].sum()),
                "t50_kl": float(kl[3:].sum()),
                "clr_qp": _clr(qp),
                "clr_kl": _clr(kl),
                "raw_sum": float(vector.sum()),
            }
        )
    return states


def _seat_shocks(
    candidate: dict[str, Any],
    quotes: dict[str, list[QuotePoint]],
    *,
    freshness_seconds: int,
) -> list[SourceShock]:
    tokens = [str(row["token_id"]) for row in candidate["source_tokens"]]
    states = _seat_states(quotes, tokens, freshness_seconds=freshness_seconds)
    thresholds = candidate["thresholds"]
    shocks: list[SourceShock] = []
    for before, after in zip(states, states[1:], strict=False):
        dq_qp = float(after["t50_qp"] - before["t50_qp"])
        dq_kl = float(after["t50_kl"] - before["t50_kl"])
        dir_qp = _sign(dq_qp)
        dir_kl = _sign(dq_kl)
        agree = dir_qp != 0 and dir_qp == dir_kl
        clr_qp = float(np.linalg.norm(after["clr_qp"] - before["clr_qp"]))
        clr_kl = float(np.linalg.norm(after["clr_kl"] - before["clr_kl"]))
        scalar_trigger = bool(
            agree
            and abs(dq_qp) >= float(thresholds["scalar_t50_qp_abs_change"])
            and abs(dq_kl) >= float(thresholds["scalar_t50_kl_abs_change"])
        )
        clr_trigger = bool(
            agree
            and clr_qp >= float(thresholds["clr_qp_l2"])
            and clr_kl >= float(thresholds["clr_kl_l2"])
        )
        if not scalar_trigger and not clr_trigger:
            continue
        scalar_score = min(
            abs(dq_qp) / float(thresholds["scalar_t50_qp_abs_change"]),
            abs(dq_kl) / float(thresholds["scalar_t50_kl_abs_change"]),
        )
        clr_score = min(
            clr_qp / float(thresholds["clr_qp_l2"]),
            clr_kl / float(thresholds["clr_kl_l2"]),
        )
        shocks.append(
            SourceShock(
                candidate_id=str(candidate["id"]),
                time_ns=int(after["time_ns"]),
                direction=dir_qp,
                score=float(max(scalar_score, clr_score)),
                trigger=(
                    "SCALAR+CLR" if scalar_trigger and clr_trigger
                    else "SCALAR" if scalar_trigger
                    else "CLR"
                ),
                independent=True,
                source_value_before=float(before["t50_qp"]),
                source_value_after=float(after["t50_qp"]),
                details={
                    "dq_t50_qp": dq_qp,
                    "dq_t50_kl": dq_kl,
                    "clr_qp_l2": clr_qp,
                    "clr_kl_l2": clr_kl,
                    "qp_kl_direction_agree": agree,
                    "raw_sum_before": float(before["raw_sum"]),
                    "raw_sum_after": float(after["raw_sum"]),
                },
            )
        )
    return shocks


def _binary_shocks(
    candidate: dict[str, Any],
    quotes: dict[str, list[QuotePoint]],
) -> list[SourceShock]:
    token = str(candidate["source_tokens"][0]["token_id"])
    points = quotes.get(token, [])
    threshold = float(candidate["thresholds"]["binary_clr_l2"])
    shocks: list[SourceShock] = []
    previous: QuotePoint | None = None
    for point in points:
        if previous is None:
            previous = point
            continue
        if point.mid == previous.mid:
            previous = point
            continue
        distance = _binary_clr_distance(previous.mid, point.mid)
        direction = _sign(point.mid - previous.mid)
        if direction and distance >= threshold:
            shocks.append(
                SourceShock(
                    candidate_id=str(candidate["id"]),
                    time_ns=point.time_ns,
                    direction=direction,
                    score=(distance / threshold if threshold > 0 else distance),
                    trigger="BINARY_CLR" if threshold > 0 else "ANY_ECONOMIC_CHANGE",
                    independent=True,
                    source_value_before=previous.mid,
                    source_value_after=point.mid,
                    details={"binary_clr_l2": distance},
                )
            )
        previous = point
    return shocks


def _mark_independent(
    shocks: list[SourceShock],
    separation_seconds: int,
) -> list[SourceShock]:
    separation_ns = separation_seconds * 1_000_000_000
    result: list[SourceShock] = []
    last_independent: int | None = None
    for shock in sorted(shocks, key=lambda item: item.time_ns):
        independent = (
            last_independent is None
            or shock.time_ns - last_independent >= separation_ns
        )
        if independent:
            last_independent = shock.time_ns
        result.append(
            SourceShock(
                candidate_id=shock.candidate_id,
                time_ns=shock.time_ns,
                direction=shock.direction,
                score=shock.score,
                trigger=shock.trigger,
                independent=independent,
                source_value_before=shock.source_value_before,
                source_value_after=shock.source_value_after,
                details=shock.details,
            )
        )
    return result


def _asof(points: list[QuotePoint], query_ns: int, max_age_seconds: int) -> int | None:
    times = [point.time_ns for point in points]
    index = bisect.bisect_right(times, query_ns) - 1
    if index < 0:
        return None
    if query_ns - points[index].time_ns > max_age_seconds * 1_000_000_000:
        return None
    return index


def _first_at_or_after(
    points: list[QuotePoint],
    query_ns: int,
    max_delay_seconds: int,
) -> int | None:
    times = [point.time_ns for point in points]
    index = bisect.bisect_left(times, query_ns)
    if index >= len(points):
        return None
    if points[index].time_ns - query_ns > max_delay_seconds * 1_000_000_000:
        return None
    return index


def _previous_distinct(points: list[QuotePoint], index: int) -> QuotePoint | None:
    current = points[index]
    for candidate in reversed(points[:index]):
        if candidate.bid != current.bid or candidate.ask != current.ask:
            return candidate
    return None


def _first_change_after(
    points: list[QuotePoint],
    *,
    start_ns: int,
    pre: QuotePoint,
    max_seconds: int,
) -> QuotePoint | None:
    times = [point.time_ns for point in points]
    index = bisect.bisect_right(times, start_ns)
    limit = start_ns + max_seconds * 1_000_000_000
    for point in points[index:]:
        if point.time_ns > limit:
            break
        if point.bid != pre.bid or point.ask != pre.ask:
            return point
    return None


def _gross_markout(pre: QuotePoint, future: QuotePoint, direction: int) -> float:
    if direction > 0:
        return float(future.bid - pre.ask)
    if direction < 0:
        return float(pre.bid - future.ask)
    return 0.0


def _utc_iso(time_ns: int) -> str:
    return datetime.fromtimestamp(time_ns / 1_000_000_000, tz=UTC).isoformat()


def _shock_rows(
    shocks: list[SourceShock],
    candidate: dict[str, Any],
    sig_quotes: dict[str, list[QuotePoint]],
    *,
    horizons: list[int],
    pre_freshness_seconds: int,
    sampling_delay_seconds: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    shock_rows: list[dict[str, object]] = []
    markout_rows: list[dict[str, object]] = []
    for number, shock in enumerate(shocks, start=1):
        shock_id = f"{shock.candidate_id}-{number:05d}"
        shock_rows.append(
            {
                "shock_id": shock_id,
                "candidate_id": shock.candidate_id,
                "shock_at": _utc_iso(shock.time_ns),
                "direction": shock.direction,
                "score": shock.score,
                "trigger": shock.trigger,
                "independent": shock.independent,
                "source_value_before": shock.source_value_before,
                "source_value_after": shock.source_value_after,
                "details_json": json.dumps(shock.details, sort_keys=True),
            }
        )
        for target in candidate["target_exchange_ids"]:
            exchange = str(target["exchange_id"])
            target_points = sig_quotes.get(exchange, [])
            if not target_points:
                continue
            pre_index = _asof(
                target_points,
                shock.time_ns,
                pre_freshness_seconds,
            )
            if pre_index is None:
                continue
            pre = target_points[pre_index]
            prior = _previous_distinct(target_points, pre_index)
            reversal_direction = 0
            if prior is not None:
                reversal_direction = -_sign(pre.mid - prior.mid)
            target_direction = shock.direction * int(target["direction_multiplier"])
            first_change = _first_change_after(
                target_points,
                start_ns=shock.time_ns,
                pre=pre,
                max_seconds=max(horizons),
            )
            latency_seconds = (
                None
                if first_change is None
                else (first_change.time_ns - shock.time_ns) / 1_000_000_000
            )
            for horizon in horizons:
                query_ns = shock.time_ns + horizon * 1_000_000_000
                future_index = _first_at_or_after(
                    target_points,
                    query_ns,
                    sampling_delay_seconds,
                )
                if future_index is None:
                    continue
                future = target_points[future_index]
                gross = _gross_markout(pre, future, target_direction)
                reversal_gross = (
                    None
                    if reversal_direction == 0
                    else _gross_markout(pre, future, reversal_direction)
                )
                markout_rows.append(
                    {
                        "shock_id": shock_id,
                        "candidate_id": shock.candidate_id,
                        "independent": shock.independent,
                        "shock_at": _utc_iso(shock.time_ns),
                        "target_exchange_id": exchange,
                        "target_sig_market_id": str(target["target_sig_market_id"]),
                        "source_direction": shock.direction,
                        "target_direction": target_direction,
                        "horizon_seconds": horizon,
                        "pre_quote_age_seconds": (
                            shock.time_ns - pre.time_ns
                        ) / 1_000_000_000,
                        "sample_delay_seconds": (
                            future.time_ns - query_ns
                        ) / 1_000_000_000,
                        "pre_bid": pre.bid,
                        "pre_ask": pre.ask,
                        "pre_mid": pre.mid,
                        "future_bid": future.bid,
                        "future_ask": future.ask,
                        "future_mid": future.mid,
                        "gross_top_of_book_markout": gross,
                        "signed_mid_move": target_direction * (future.mid - pre.mid),
                        "target_reversal_direction": reversal_direction,
                        "target_reversal_gross_markout": reversal_gross,
                        "increment_vs_target_reversal": (
                            None if reversal_gross is None else gross - reversal_gross
                        ),
                        "first_target_change_latency_seconds": latency_seconds,
                    }
                )
    return shock_rows, markout_rows


def _mean(values: list[float]) -> float | None:
    return None if not values else float(np.mean(np.asarray(values, dtype=float)))


def _summaries(
    candidates: list[dict[str, Any]],
    shock_rows: list[dict[str, object]],
    markout_rows: list[dict[str, object]],
    horizons: list[int],
    gate: dict[str, Any],
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_id = str(candidate["id"])
        independent = [
            row for row in shock_rows
            if row["candidate_id"] == candidate_id and row["independent"] is True
        ]
        dates = {str(row["shock_at"])[:10] for row in independent}
        for target in candidate["target_exchange_ids"]:
            exchange = str(target["exchange_id"])
            for horizon in horizons:
                rows = [
                    row for row in markout_rows
                    if row["candidate_id"] == candidate_id
                    and row["target_exchange_id"] == exchange
                    and row["horizon_seconds"] == horizon
                    and row["independent"] is True
                ]
                gross = [
                    value
                    for row in rows
                    if (
                        value := _finite_float(
                            row["gross_top_of_book_markout"]
                        )
                    ) is not None
                ]
                increments = [
                    value
                    for row in rows
                    if (
                        value := _finite_float(
                            row["increment_vs_target_reversal"]
                        )
                    ) is not None
                ]
                dominance = (
                    None
                    if not gross or sum(abs(value) for value in gross) == 0
                    else max(abs(value) for value in gross)
                    / sum(abs(value) for value in gross)
                )
                support_ok = (
                    len(independent) >= int(gate["minimum_independent_shocks"])
                    and len(dates) >= int(gate["minimum_distinct_days_or_event_windows"])
                )
                mean_gross = _mean(gross)
                mean_increment = _mean(increments)
                gate_status = "SHADOW_CURIOSITY"
                if bool(candidate.get("gate_eligible")):
                    gate_status = (
                        "AWAIT_LIVE_SUPPORT" if not support_ok
                        else "AWAIT_NET_COST_AND_DOMINANCE_GATE"
                    )
                    if support_ok and mean_gross is not None and mean_gross <= 0:
                        gate_status = "FAIL_GROSS_MARKOUT"
                    if (
                        support_ok
                        and mean_increment is not None
                        and mean_increment <= 0
                    ):
                        gate_status = "FAIL_REVERSAL_INCREMENT"
                summaries.append(
                    {
                        "candidate_id": candidate_id,
                        "target_exchange_id": exchange,
                        "target_sig_market_id": str(target["target_sig_market_id"]),
                        "horizon_seconds": horizon,
                        "gate_eligible": bool(candidate.get("gate_eligible")),
                        "independent_shocks": len(independent),
                        "distinct_utc_days": len(dates),
                        "markouts_available": len(rows),
                        "mean_gross_top_of_book_markout": mean_gross,
                        "mean_increment_vs_target_reversal": mean_increment,
                        "single_shock_abs_markout_share": dominance,
                        "gate_status": gate_status,
                        "net_cost_gate_evaluated": False,
                    }
                )
    return summaries


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(
    *,
    sig_root: Path,
    polymarket_root: Path,
    config_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "SHADOW_ONLY":
        raise ValueError("structural-shadow analyzer must remain SHADOW_ONLY")
    if config.get("data001_used") is not False:
        raise ValueError("DATA-001 must remain disabled")

    candidates = list(config["candidates"])
    token_ids = {
        str(source["token_id"])
        for candidate in candidates
        for source in candidate["source_tokens"]
    }
    exchange_ids = {
        str(target["exchange_id"])
        for candidate in candidates
        for target in candidate["target_exchange_ids"]
    }
    pm_quotes = _load_pm_quotes(polymarket_root, token_ids)
    sig_quotes = _load_sig_quotes(sig_root, exchange_ids)

    all_shocks: list[SourceShock] = []
    for candidate in candidates:
        if candidate["type"] == "seat_distribution":
            raw = _seat_shocks(
                candidate,
                pm_quotes,
                freshness_seconds=int(config["source_freshness_seconds"]),
            )
        elif candidate["type"] == "binary_probability":
            raw = _binary_shocks(candidate, pm_quotes)
        else:
            raise ValueError(f"unknown candidate type: {candidate['type']}")
        all_shocks.extend(
            _mark_independent(
                raw,
                int(config["independent_shock_separation_seconds"]),
            )
        )

    shock_rows: list[dict[str, object]] = []
    markout_rows: list[dict[str, object]] = []
    horizons = [int(value) for value in config["horizons_seconds"]]
    candidate_by_id = {str(row["id"]): row for row in candidates}
    for candidate_id in sorted(candidate_by_id):
        candidate_shocks = [
            shock for shock in all_shocks if shock.candidate_id == candidate_id
        ]
        srows, mrows = _shock_rows(
            candidate_shocks,
            candidate_by_id[candidate_id],
            sig_quotes,
            horizons=horizons,
            pre_freshness_seconds=int(config["target_pre_shock_freshness_seconds"]),
            sampling_delay_seconds=int(config["max_horizon_sampling_delay_seconds"]),
        )
        shock_rows.extend(srows)
        markout_rows.extend(mrows)

    summaries = _summaries(
        candidates,
        shock_rows,
        markout_rows,
        horizons,
        dict(config["live_gate"]),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    _write_csv(output_root / "structural_shocks.csv", shock_rows)
    _write_csv(output_root / "structural_shadow_markouts.csv", markout_rows)
    _write_csv(output_root / "structural_shadow_summary.csv", summaries)

    result = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "mode": "SHADOW_ONLY",
        "data001_used": False,
        "source_tokens_requested": len(token_ids),
        "source_tokens_observed": len(pm_quotes),
        "target_exchanges_requested": len(exchange_ids),
        "target_exchanges_observed": len(sig_quotes),
        "raw_shocks": len(shock_rows),
        "independent_shocks": sum(
            1 for row in shock_rows if row["independent"] is True
        ),
        "markout_rows": len(markout_rows),
        "summaries": summaries,
        "net_cost_gate_evaluated": False,
        "promotion_allowed": False,
        "note": (
            "Markouts are executable-side top-of-book gross markouts. Fees and "
            "depth slippage are not included; this output cannot promote a strategy."
        ),
    }
    (output_root / "structural_shadow_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = [
        "# R3 Live Structural-Shadow Report",
        "",
        "**Mode:** SHADOW_ONLY",
        "",
        f"- Source tokens observed: {len(pm_quotes)} / {len(token_ids)}.",
        f"- SIG target exchanges observed: {len(sig_quotes)} / {len(exchange_ids)}.",
        f"- Raw shocks: {len(shock_rows)}.",
        f"- Independent shocks: {result['independent_shocks']}.",
        f"- Markout rows: {len(markout_rows)}.",
        "",
        "Gross markouts cross the captured top-of-book spread. They do not include "
        "fees or depth slippage, so this report cannot satisfy the frozen net-cost "
        "promotion gate by itself.",
    ]
    (output_root / "structural_shadow_report.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure live R3 structural-shadow response from capture Parquet"
    )
    parser.add_argument("--sig-root", type=Path, required=True)
    parser.add_argument("--polymarket-root", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("data/capture/r3_live_shadow_analysis.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(
        sig_root=args.sig_root,
        polymarket_root=args.polymarket_root,
        config_path=args.config,
        output_root=args.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
