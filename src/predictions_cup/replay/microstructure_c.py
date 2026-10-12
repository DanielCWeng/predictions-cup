"""LANE C: fail-closed, research-only microstructure PAPER opportunity evaluator.

Consumes prevalidated paired snapshots from #220; it never connects to an exchange,
submits orders, or treats hypothetical IOC touches as realized fills.
SIG YES/NO ladder conversion is delegated to Lane 3's corrected economics code.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.replay.economic_005i import _outcome_levels
from predictions_cup.replay.model import BookLevel, InstrumentView, ReplaySource

D = Decimal
DEFAULT_QTY = D(1)
DEFAULT_FEE_BPS = D(20)
DEFAULT_ZERO = D(0)
DEFAULT_HAIRCUT = D("0.5")
HORIZONS = (5, 30, 300, 3600)
PASSIVE = ("PASSIVE_SPREAD", "DEPTH_PASSIVE", "INVENTORY_SKEW", "PASSIVE_RANDOM")
CANDIDATES = (
    "PASSIVE_SPREAD", "DEPTH_PASSIVE", "STALE_QUOTE", "INVENTORY_SKEW",
    "TRADE_REVERSION", "REPLENISHMENT", "SHORT_DISCOVERY", "CROSSVENUE_STALE",
    "NO_TRADE", "PASSIVE_RANDOM", "SIMPLE_IMBALANCE", "FROZEN_005I",
    "DIRECT_FV", "ADVERSE_SELECTION",
)


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("missing UTC offset on observation time")
    return result.astimezone(UTC)


def _amount(value: Any) -> Decimal:
    number = D(str(value))
    if not number.is_finite():
        raise ValueError("nonfinite economic input")
    return number


@dataclass(frozen=True)
class Snapshot:
    observed_at: datetime
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    trusted: bool
    complete: bool

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Snapshot:
        def parse(key: str) -> tuple[BookLevel, ...]:
            levels = tuple(BookLevel(_amount(p), _amount(q)) for p, q in raw[key])
            if any(not D(0) < x.price < D(1) or x.quantity <= 0 for x in levels):
                raise ValueError("invalid ladder price/quantity")
            prices = [x.price for x in levels]
            if len(set(prices)) != len(prices):
                raise ValueError("duplicate ladder price")
            return levels

        bids, asks = parse("bids"), parse("asks")
        if not bids or not asks or max(x.price for x in bids) >= min(x.price for x in asks):
            raise ValueError("crossed/empty SIG depth")
        return cls(_time(raw["observed_at"]), bids, asks,
                   raw.get("trusted") is True, raw.get("complete") is True)


def _levels(book: Snapshot, side: str, buy: bool) -> tuple[BookLevel, ...]:
    # Reuse Lane 3's one-SIG-exchange YES/NO complement implementation.
    view = InstrumentView(
        source=ReplaySource.SIG, instrument_id="research_only",
        market_id=None, best_bid=max(x.price for x in book.bids),
        best_ask=min(x.price for x in book.asks), bids=book.bids, asks=book.asks,
        last_trade=None, quote_observed_at=book.observed_at,
        last_event_observed_at=book.observed_at, trusted=book.trusted,
        book_valid=book.complete, source_available=True,
    )
    return _outcome_levels(view, side=side, buy=buy)


def _walk(levels: tuple[BookLevel, ...], quantity: Decimal,
          *, buy: bool, haircut: Decimal, limit: Decimal | None = None
          ) -> tuple[Decimal, Decimal, Decimal | None]:
    """(shares, cash, marginal price), with no implied fill probability."""
    remaining, cash, marginal = quantity, D(0), None
    for level in sorted(levels, key=lambda x: x.price, reverse=not buy):
        if buy and limit is not None and level.price > limit:
            break
        fill = min(remaining, level.quantity * haircut)
        if fill <= 0:
            continue
        cash += fill * level.price
        remaining -= fill
        marginal = level.price
        if remaining <= 0:
            break
    return quantity - remaining, cash, marginal


def _direction(strategy: str, row: dict[str, Any]) -> str | None:
    def sign(value: Any, threshold: float = 0.0) -> str | None:
        if value is None:
            return None
        v = float(value)
        if not math.isfinite(v):
            raise ValueError("nonfinite signal")
        return "YES" if v > threshold else "NO" if v < -threshold else None

    features = row.get("features", {})
    if strategy == "NO_TRADE":
        return None
    if strategy == "PASSIVE_SPREAD":
        return "YES" if float(features.get("spread", 0)) >= 0.04 else None
    if strategy in ("DEPTH_PASSIVE", "SIMPLE_IMBALANCE"):
        return sign(features.get("imbalance"), 0.3)
    if strategy == "INVENTORY_SKEW":
        inventory = features.get("inventory_yes")
        return sign(-float(inventory)) if inventory is not None else None
    if strategy == "STALE_QUOTE":
        return (sign(features.get("pm_minus_sig"), 0.04)
                if features.get("reference_orientation") == "SIG_YES" else None)
    if strategy == "CROSSVENUE_STALE":
        if (features.get("verified_source_lag") is not True
                or features.get("reference_orientation") != "SIG_YES"):
            return None
        return sign(features.get("pm_minus_sig"), 0.04)
    if strategy == "TRADE_REVERSION":
        return sign(-float(features["aggressive_trade_sign"])) if (
            "aggressive_trade_sign" in features) else None
    if strategy == "ADVERSE_SELECTION":
        return sign(features.get("aggressive_trade_sign"))
    if strategy == "REPLENISHMENT":
        return sign(features.get("replenishment"), 0.2)
    if strategy == "SHORT_DISCOVERY":
        return sign(features.get("short_discovery"), 0.02)
    if strategy == "FROZEN_005I":
        # Frozen signs describe PM token payoff. Canonical SAME/COMPLEMENT
        # orientation must be resolved before using SIG YES/NO orders.
        if (features.get("frozen_005i_attested") is not True
                or row.get("mapping_direction") not in ("SAME", "COMPLEMENT")):
            return None
        raw_sign = features.get("frozen_005i_signed")
        if raw_sign is None:
            return None
        sign_in_sig_yes = float(raw_sign) * (
            -1 if row["mapping_direction"] == "COMPLEMENT" else 1)
        return sign(sign_in_sig_yes)
    if strategy == "DIRECT_FV":
        return (sign(features.get("pm_minus_sig"), 0.02)
                if features.get("reference_orientation") == "SIG_YES" else None)
    if strategy == "PASSIVE_RANDOM":
        digest = hashlib.sha256(str(row["episode_id"]).encode()).digest()
        return "YES" if digest[0] % 2 else "NO"
    raise ValueError("unknown strategy")


def _quote(book: Snapshot, expected: datetime, max_age_ms: int) -> bool:
    return (book.trusted and book.complete
            and timedelta(0) <= book.observed_at - expected
            <= timedelta(milliseconds=max_age_ms))


def _markouts(row: dict[str, Any], *, side: str, shares: Decimal,
              entry_cost: Decimal, fee_entry: Decimal, fee_bps: Decimal,
              haircut: Decimal, entry_at: datetime, max_wait_ms: int
              ) -> dict[str, float | None]:
    results: dict[str, float | None] = {}
    for horizon in HORIZONS:
        raw = row.get("exits", {}).get(str(horizon))
        value: float | None = None
        if raw is not None:
            book = Snapshot.from_json(raw)
            if _quote(book, entry_at + timedelta(seconds=horizon), max_wait_ms):
                qty, proceeds, _ = _walk(
                    _levels(book, side, False), shares, buy=False, haircut=haircut)
                if qty == shares:
                    value = float(proceeds - entry_cost - fee_entry
                                  - proceeds * fee_bps / D(10000))
        results[str(horizon)] = value
    return results


def _observed_order(raw: dict[str, Any], decision: datetime, side: str,
                    quantity: Decimal, *, exchange_id: str, strategy: str,
                    resting_limit: Decimal) -> tuple[bool, Decimal, Decimal, Decimal,
                                               datetime | None]:
    """Only matched real journal ACKs/fills count in passive calibration."""
    if raw.get("provenance") != "SIG_EXECUTION_JOURNAL_JOINED":
        raise ValueError("unverified passive order source")
    if (raw.get("side") != side or raw.get("action") != "buy"
            or not raw.get("order_id")
            or raw.get("strategy_id") != strategy
            or str(raw.get("sig_exchange_id")) != exchange_id):
        raise ValueError("journal order not unambiguously matched to candidate")
    ack = _time(raw["ack_at"])
    submitted = _time(raw["submitted_at"])
    if not decision <= submitted <= ack:
        raise ValueError("submission/ack chronology invalid")
    if _amount(raw["limit_price"]) != resting_limit:
        raise ValueError("passive limit not the decision-time resting quote")
    qty = _amount(raw["filled_qty"])
    price = _amount(raw.get("fill_price", "0"))
    fee = _amount(raw["fee_paid"])
    if (qty < 0 or qty > quantity or fee < 0
            or (qty > 0 and not D(0) < price <= resting_limit)):
        raise ValueError("invalid matched fill")
    filled_at = _time(raw["fill_at"]) if qty > 0 else None
    if filled_at is not None and filled_at < ack:
        raise ValueError("fill before acknowledgement")
    if (raw.get("cancel_ack_at") is not None and filled_at is not None
            and filled_at > _time(raw["cancel_ack_at"])):
        raise ValueError("fill after confirmed cancellation")
    return True, qty, qty * price, fee, filled_at


def _cluster_interval(rows: list[tuple[str, str, float]]) -> list[float] | None:
    """Bootstrap two-way event and day cluster sums; <5 of either -> unknown."""
    days = sorted({day for _, day, _ in rows})
    events = sorted({event for event, _, _ in rows})
    if len(days) < 5 or len(events) < 5:
        return None
    rand = random.Random(205207220)
    samples: list[float] = []
    for _ in range(1000):
        ds = [rand.choice(days) for _ in days]
        es = [rand.choice(events) for _ in events]
        dc = {d: ds.count(d) for d in days}
        ec = {e: es.count(e) for e in events}
        samples.append(sum(v * dc[d] * ec[e] for e, d, v in rows))
    samples.sort()
    return [samples[25], samples[975]]


def evaluate(rows: list[dict[str, Any]], *, quantity: Decimal = DEFAULT_QTY,
             fee_bps: Decimal = DEFAULT_FEE_BPS, latency_ms: int = 100,
             gross_free: Decimal = DEFAULT_ZERO, cash_free: Decimal = DEFAULT_ZERO,
             haircut: Decimal = DEFAULT_HAIRCUT, max_wait_ms: int = 1000
             ) -> dict[str, Any]:
    """Evaluate each proposed action and return a complete rejection ledger.

    Latency stress requires separate observed arrival_2x and arrival_5x books.
    If absent, those stress outputs are null rather than invented.
    """
    if (quantity < 1 or quantity != quantity.to_integral_value()
            or fee_bps < 0 or latency_ms < 0 or gross_free < 0 or cash_free < 0
            or not D(0) < haircut <= 1 or max_wait_ms < 0):
        raise ValueError("invalid research economic controls")
    ledger: list[dict[str, Any]] = []
    fills: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    unique_orders: set[str] = set()
    ack_counts: dict[str, list[int]] = defaultdict(list)
    last_episode: dict[tuple[str, str], datetime] = {}

    for row in sorted(rows, key=lambda r: _time(r["decision_at"])):
        decision = _time(row["decision_at"])
        episode_id = str(row["episode_id"])
        event_id = str(row["event_id"])
        day = decision.date().isoformat()
        exchange_id = str(row["sig_exchange_id"])
        if not (episode_id and event_id and exchange_id):
            raise ValueError("blank episode identity")
        for strategy in CANDIDATES:
            item: dict[str, Any] = {
                "episode_id": episode_id, "event_id": event_id, "day": day,
                "sig_exchange_id": exchange_id, "strategy": strategy,
                "side": None, "status": "REJECTED", "reason": None,
                "available_size": None, "conditional_markouts": {
                    str(h): None for h in HORIZONS},
                "conditional_net_5m": None, "conditional_capital_seconds": None,
                "arrival_stress_net_5m": {"1x": None, "2x": None, "5x": None},
                "observed_passive_fill": None, "evidence_class": "NOT_MEASURED",
            }
            ledger.append(item)
            side = _direction(strategy, row)
            item["side"] = side
            if strategy == "NO_TRADE":
                item["status"], item["reason"] = "ABSTAIN", "control_no_trade"
                continue
            if side is None:
                item["reason"] = "no_qualifying_signal"
                continue
            if row.get("exact_mapping_verified") is not True:
                item["reason"] = "mapping_not_independently_verified"
                continue
            if row.get("paired_depth_trusted") is not True:
                item["reason"] = "paired_depth_not_trusted"
                continue
            key = (strategy, exchange_id)
            prior = last_episode.get(key)
            if prior is not None and decision - prior < timedelta(minutes=5):
                item["reason"] = "overlapping_episode"
                continue
            last_episode[key] = decision
            if quantity > gross_free:
                item["reason"] = "gross_cap_full"
                continue
            book = Snapshot.from_json(row["decision_book"])
            if not book.trusted or not book.complete or (
                    abs((decision - book.observed_at).total_seconds()) > 2
                    or book.observed_at > decision):
                item["reason"] = "untrusted_or_stale_decision_depth"
                continue
            if strategy in PASSIVE:
                # A recorded ACK with ZERO fill still enters the denominator.
                proof = row.get("observed_orders", {}).get(strategy)
                if proof is None:
                    item["reason"] = "passive_queue_fill_unobserved"
                    continue
                order_id = str(proof.get("order_id"))
                if order_id in unique_orders:
                    raise ValueError("reused passive journal order across opportunities")
                unique_orders.add(order_id)
                resting_levels = _levels(book, side, False)
                resting_limit = max(level.price for level in resting_levels)
                _, shares, paid, fee_paid, fill_at = _observed_order(
                    proof, decision, side, quantity, exchange_id=exchange_id,
                    strategy=strategy, resting_limit=resting_limit)
                ack_counts[strategy].append(1 if shares > 0 else 0)
                item["observed_passive_fill"] = bool(shares)
                if shares == 0:
                    item["status"], item["reason"] = "NO_FILL", "acknowledged_but_unfilled"
                    item["evidence_class"] = "OBSERVED_NO_FILL"
                    continue
                if paid + fee_paid > cash_free:
                    item["reason"] = "insufficient_cash_for_matched_fill"
                    continue
                assert fill_at is not None
                item["status"], item["reason"] = "MARKOUT_ONLY", "observed_entry_only"
                item["evidence_class"] = "OBSERVED_ENTRY_HYPOTHETICAL_EXIT"
                item["available_size"] = float(shares)
                marks = _markouts(row, side=side, shares=shares, entry_cost=paid,
                                  fee_entry=fee_paid, fee_bps=fee_bps,
                                  haircut=haircut, entry_at=fill_at,
                                  max_wait_ms=max_wait_ms)
                item["conditional_markouts"] = marks
            else:
                levels = _levels(book, side, True)
                capacity = sum(x.quantity for x in levels) * haircut
                item["available_size"] = float(capacity)
                requested, decision_cost, limit = _walk(
                    levels, quantity, buy=True, haircut=haircut)
                if requested < quantity or limit is None:
                    item["reason"] = "insufficient_decision_depth"
                    continue
                cash_reserve = quantity * limit * (D(1) + fee_bps / D(10000))
                if cash_reserve > cash_free:
                    item["reason"] = "insufficient_worst_case_cash"
                    continue
                # The limit is the DECISION-TIME marginal price, not the VWAP.
                # No future-profit filter is allowed before an arrival touch.
                for multiplier in (1, 2, 5):
                    raw = row.get("arrivals", {}).get(str(multiplier) + "x")
                    if raw is None:
                        continue
                    arrival = Snapshot.from_json(raw)
                    target = decision + timedelta(milliseconds=latency_ms * multiplier)
                    if not _quote(arrival, target, max_wait_ms):
                        continue
                    shares, paid, _ = _walk(
                        _levels(arrival, side, True), quantity, buy=True,
                        haircut=haircut, limit=limit)
                    if shares < quantity:
                        continue
                    fee_entry = paid * fee_bps / D(10000)
                    marks = _markouts(row, side=side, shares=quantity,
                                      entry_cost=paid, fee_entry=fee_entry,
                                      fee_bps=fee_bps, haircut=haircut,
                                      entry_at=arrival.observed_at,
                                      max_wait_ms=max_wait_ms)
                    item["arrival_stress_net_5m"][str(multiplier) + "x"] = marks["300"]
                    if multiplier == 1:
                        item["conditional_markouts"] = marks
                        item["status"], item["reason"] = (
                            "IOC_TOUCH_UPPER_BOUND", "not_an_observed_fill")
                        item["evidence_class"] = "HYPOTHETICAL_IOC_TOUCH"
                if item["status"] == "REJECTED":
                    item["reason"] = "no_placeable_post_latency_arrival"
            net = item["conditional_markouts"]["300"]
            item["conditional_net_5m"] = net
            if net is not None:
                cash = (paid + fee_paid) if strategy in PASSIVE else cash_reserve
                item["conditional_capital_seconds"] = float(cash * D(300))
                if item["evidence_class"] == "OBSERVED_ENTRY_HYPOTHETICAL_EXIT":
                    fills[strategy].append((event_id, day, net))

    scorecard: dict[str, Any] = {}
    for strategy in CANDIDATES:
        selected = [x for x in ledger if x["strategy"] == strategy]
        observed = [x for x in selected if x["evidence_class"] ==
                    "OBSERVED_ENTRY_HYPOTHETICAL_EXIT"]
        scenario = [x for x in selected if x["conditional_net_5m"] is not None]
        interval = _cluster_interval(fills[strategy])
        status = "INSUFFICIENT_DATA"
        # Hypothetical exits cannot prove positive settled PnL or certify promotion.
        if strategy == "NO_TRADE":
            status = "REJECTED_AS_ALPHA_CONTROL"
        scorecard[strategy] = {
            "classification": status,
            "candidate_opportunities": len(selected),
            "signal_count": sum(x["side"] is not None for x in selected),
            "observed_passive_acks": len(ack_counts[strategy]),
            "observed_passive_fill_probability": (
                sum(ack_counts[strategy]) / len(ack_counts[strategy])
                if ack_counts[strategy] else None),
            "observed_entry_count": len(observed),
            "hypothetical_ioc_touch_count": sum(
                x["status"] == "IOC_TOUCH_UPPER_BOUND" for x in selected),
            "conditional_markout_count": len(scenario),
            "conditional_5m_net_sum": (
                sum(float(x["conditional_net_5m"]) for x in scenario)
                if scenario else None),
            "conditional_capital_seconds": (
                sum(float(x["conditional_capital_seconds"]) for x in scenario)
                if scenario else None),
            "day_event_clustered_markout_ci95": interval,
            "measured_realized_net_pnl": None,
            "expected_net_pnl": None,
            "profit_per_capital_second": None,
            "capacity_ceiling": None,
            "failure_counts": {reason: sum(x["reason"] == reason for x in selected)
                               for reason in sorted({str(x["reason"]) for x in selected})},
        }
    return {
        "schema": "lane_c_microstructure_v1",
        "research_only": True, "execution_authority": False,
        "actual_orders_sent": 0,
        "notional_fee_bps_per_side": float(fee_bps),
        "assumed_latency_ms": latency_ms,
        "assumed_depth_haircut": float(haircut),
        "sample_rows": len(rows),
        "scorecard": scorecard, "ledger": ledger,
        "science_gate": "NO_PROMOTION_UNTIL_OBSERVED_EXECUTABLE_OUTCOMES",
    }


def evaluate_file(input_path: Path, output_path: Path, *,
                  gross_free: Decimal, cash_free: Decimal) -> None:
    rows = [json.loads(line) for line in input_path.read_text().splitlines()
            if line.strip()]
    report = evaluate(rows, gross_free=gross_free, cash_free=cash_free)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
