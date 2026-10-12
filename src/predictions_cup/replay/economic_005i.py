"""LANE-3 offline 005I economic bridge. No order sender or network dependency.

Input is a causally produced, completed-minute 005I feature tape plus accepted
replay capture events. This is an execution *scenario*, not evidence of fills.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping

from predictions_cup.replay.model import InstrumentView, ReplayEvent, ReplaySource
from predictions_cup.replay.runner import ReplayRunner

D = Decimal
HORIZONS = (5, 30, 300, 3600)
STRATEGIES = (
    "NO_TRADE", "REVERSAL", "PERSISTENCE", "OFI_BASELINE",
    "OFI_CHALLENGER", "DIRECT_PM_FV", "RANDOM_TIME",
)
FEATURES = (
    "mid", "relative_spread", "depth_total5", "quote_events",
    "rv_5m", "imbalance1", "ret_1m", "ret_5m", "ofi_depth_norm",
)


@dataclass(frozen=True)
class ExactPair:
    market_id: str
    pm_yes: str
    sig_yes: str
    sig_no: str
    acceptance: str

    def __post_init__(self) -> None:
        if self.acceptance != "ACCEPTED_EXACT":
            raise ValueError("only independently accepted EXACT pairs are permitted")
        if not all((self.market_id, self.pm_yes, self.sig_yes, self.sig_no)):
            raise ValueError("pair identity is incomplete")
        if self.sig_yes == self.sig_no:
            raise ValueError("YES and NO must be distinct SIG contracts")


@dataclass(frozen=True)
class MinuteFeature:
    market_id: str
    pm_yes: str
    minute_end: datetime
    available_at: datetime
    values: Mapping[str, float]
    provenance: str

    def __post_init__(self) -> None:
        if self.provenance != "005I_V3_CAUSAL_COMPLETED_MINUTE":
            raise ValueError("unverified 005I feature provenance")
        for ts in (self.minute_end, self.available_at):
            if ts.tzinfo is None or ts.utcoffset() is None:
                raise ValueError("feature clocks must include timezone")
        if self.minute_end.second != 0 or self.minute_end.microsecond != 0:
            raise ValueError("minute_end must be a minute boundary")
        if self.available_at < self.minute_end:
            raise ValueError("feature available before its minute completed")
        if set(self.values) != set(FEATURES):
            raise ValueError("features must equal the frozen 005I causal set")
        if any(not math.isfinite(float(v)) for v in self.values.values()):
            raise ValueError("nonfinite or missing feature")


@dataclass(frozen=True)
class EconomicConfig:
    fee_bps_per_side: Decimal
    latency_ms: int
    min_edge: Decimal
    quantity: Decimal
    capital_limit: Decimal
    max_quote_age_ms: int = 2000
    max_feature_delay_ms: int = 5000
    max_arrival_wait_ms: int = 1000
    max_exit_wait_ms: int = 2000
    depth_haircut: Decimal = D("0.5")
    cooldown_seconds: int = 300

    def __post_init__(self) -> None:
        if any(x < 0 for x in (
            self.fee_bps_per_side, self.latency_ms, self.min_edge,
            self.max_quote_age_ms, self.max_feature_delay_ms,
            self.max_arrival_wait_ms, self.max_exit_wait_ms, self.cooldown_seconds,
        )):
            raise ValueError("negative cost or timing assumption")
        if self.quantity <= 0 or self.capital_limit <= 0:
            raise ValueError("quantity and capital must be positive")
        if not D("0") < self.depth_haircut <= D("1"):
            raise ValueError("depth haircut must be in (0, 1]")
        if self.cooldown_seconds < 300:
            raise ValueError("must purge overlapping 005I 5-minute events")


def frozen_probability(values: Mapping[str, float], model: Mapping[str, Any]) -> float:
    features = model["features"]
    if not set(features).issubset(values):
        raise ValueError("missing frozen model input")
    logit = float(model["intercept"])
    for i, key in enumerate(features):
        x = float(values[key])
        # The frozen TRAIN medians, standard-scaler means and scales are immutable.
        if not math.isfinite(x) or float(model["scaler_scale"][i]) <= 0:
            raise ValueError("invalid frozen model input")
        z = (x - float(model["scaler_mean"][i])) / float(model["scaler_scale"][i])
        logit += z * float(model["coef"][i])
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))


def _valid(view: InstrumentView | None, at: datetime, age_ms: int, *, sig: bool) -> bool:
    if view is None or view.quote_observed_at is None or not view.book_valid:
        return False
    if not view.source_available or (sig and not view.trusted):
        return False
    if not timedelta(0) <= at - view.quote_observed_at <= timedelta(milliseconds=age_ms):
        return False
    if view.best_bid is None or view.best_ask is None:
        return False
    if not (D(0) <= view.best_bid < view.best_ask <= D(1)):
        return False
    if not view.bids or not view.asks:
        return False
    return True


def _walk(levels: Iterable[Any], qty: Decimal, *, buy: bool, haircut: Decimal) -> Decimal | None:
    """Executable VWAP, no top-of-book price used for quantity beyond actual depth."""
    remaining = qty
    paid = D(0)
    sorted_levels = sorted(levels, key=lambda x: x.price, reverse=not buy)
    previous = None
    for level in sorted_levels:
        if level.quantity < 0 or level.price <= 0 or level.price >= 1:
            return None
        if previous is not None and level.price == previous:
            return None
        previous = level.price
        take = min(remaining, level.quantity * haircut)
        if take > 0:
            paid += take * level.price
            remaining -= take
        if remaining <= 0:
            return paid
    return None


def _signal(strategy: str, feature: MinuteFeature, pair: ExactPair,
            frozen: Mapping[str, Any]) -> str | None:
    v = feature.values
    if strategy == "NO_TRADE":
        return None
    if strategy == "REVERSAL":
        return "YES" if v["ret_5m"] < 0 else "NO" if v["ret_5m"] > 0 else None
    if strategy == "PERSISTENCE":
        return "NO" if v["ret_5m"] < 0 else "YES" if v["ret_5m"] > 0 else None
    if strategy in ("OFI_BASELINE", "OFI_CHALLENGER"):
        key = "baseline" if strategy == "OFI_BASELINE" else "ofi_challenger"
        return "YES" if frozen_probability(v, frozen["models"][key]) > 0.5 else "NO"
    if strategy == "RANDOM_TIME":
        h = hashlib.sha256(
            (pair.market_id + "|" + feature.minute_end.isoformat()).encode()
        ).digest()[0]
        return ("YES" if h & 1 else "NO") if h < 128 else None
    if strategy == "DIRECT_PM_FV":
        return "BOTH"
    raise ValueError("unknown strategy")


def _quote_at(points: list[tuple[datetime, InstrumentView]],
              when: datetime, age_ms: int, *, sig: bool) -> InstrumentView | None:
    if not points:
        return None
    idx = bisect.bisect_right([p[0] for p in points], when) - 1
    if idx < 0:
        return None
    v = points[idx][1]
    return v if _valid(v, when, age_ms, sig=sig) else None


def _next_quote(points: list[tuple[datetime, InstrumentView]],
                when: datetime, wait_ms: int, age_ms: int) -> tuple[datetime, InstrumentView] | None:
    times = [p[0] for p in points]
    i = bisect.bisect_left(times, when)
    while i < len(points) and points[i][0] <= when + timedelta(milliseconds=wait_ms):
        t, v = points[i]
        # Fresh actual book observation after the simulated transport latency.
        if v.quote_observed_at is not None and v.quote_observed_at >= when and _valid(
            v, t, age_ms, sig=True
        ):
            return t, v
        i += 1
    return None


def _as_float(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def evaluate(
    events: Iterable[ReplayEvent],
    rows: Iterable[MinuteFeature],
    pairs: Iterable[ExactPair],
    config: EconomicConfig,
    freeze: Mapping[str, Any],
) -> dict[str, Any]:
    """Offline scenario; every strategy gets its own identical capital budget."""
    pair_list = tuple(pairs)
    by_pair = {(p.market_id, p.pm_yes): p for p in pair_list}
    if len(by_pair) != len(pair_list):
        raise ValueError("duplicate market/polymarket pair")
    if set(freeze["models"]) != {"baseline", "ofi_challenger"}:
        raise ValueError("unexpected frozen model set")
    tapes: dict[tuple[ReplaySource, str], list[tuple[datetime, InstrumentView]]] = defaultdict(list)
    frame_times: list[datetime] = []
    needed = {(ReplaySource.POLYMARKET, p.pm_yes) for p in pair_list}
    needed |= {(ReplaySource.SIG, s) for p in pair_list for s in (p.sig_yes, p.sig_no)}

    def snapshot(frame: Any) -> None:
        frame_times.append(frame.observed_at)
        for key in needed:
            if any((e.source, e.instrument_id) == key for e in frame.events):
                v = frame.state.view(*key)
                if v is not None:
                    tapes[key].append((frame.observed_at, v))

    ReplayRunner(events).run(snapshot)
    if not frame_times:
        return {"status": "NO_ACCEPTED_OBSERVATIONS", "promotion": "BLOCKED", "results": {}}

    seen = set()
    feature_list = sorted(rows, key=lambda r: (r.available_at, r.market_id, r.pm_yes))
    for row in feature_list:
        key = (row.market_id, row.pm_yes)
        if key not in by_pair:
            raise ValueError("feature row lacks accepted EXACT mapping")
        uid = (key, row.minute_end)
        if uid in seen:
            raise ValueError("duplicate feature minute")
        seen.add(uid)
    fee = config.fee_bps_per_side / D(10000)
    results: dict[str, Any] = {}
    for strategy in STRATEGIES:
        attempts = 0
        qualified = 0
        fills_touch_upper = 0
        exited = 0
        rejected: dict[str, int] = defaultdict(int)
        outcomes: list[dict[str, Any]] = []
        active_until: dict[str, datetime] = {}
        market_last: dict[str, datetime] = {}
        active_capital: list[tuple[datetime | None, Decimal]] = []
        for row in feature_list:
            pair = by_pair[(row.market_id, row.pm_yes)]
            side = _signal(strategy, row, pair, freeze)
            if side is None:
                continue
            attempts += 1
            di = bisect.bisect_right(frame_times, row.available_at)
            if di >= len(frame_times):
                rejected["no_decision_frame"] += 1
                continue
            decision = frame_times[di]
            if decision - row.available_at > timedelta(milliseconds=config.max_feature_delay_ms):
                rejected["delayed_feature"] += 1
                continue
            if decision <= row.minute_end:
                rejected["early_decision"] += 1
                continue
            pm = _quote_at(tapes[(ReplaySource.POLYMARKET, pair.pm_yes)],
                           decision, config.max_quote_age_ms, sig=False)
            if pm is None or pm.midpoint is None:
                rejected["pm_stale_untrusted_or_depth_missing"] += 1
                continue
            pm_mid = pm.midpoint
            options = ("YES", "NO") if side == "BOTH" else (side,)
            proposals = []
            for choice in options:
                contract = pair.sig_yes if choice == "YES" else pair.sig_no
                book = _quote_at(tapes[(ReplaySource.SIG, contract)],
                                 decision, config.max_quote_age_ms, sig=True)
                if book is None:
                    continue
                entry_cost = _walk(book.asks, config.quantity, buy=True,
                                   haircut=config.depth_haircut)
                if entry_cost is None:
                    continue
                fair_per_share = pm_mid if choice == "YES" else D(1) - pm_mid
                # Conservative round-trip fee reserve; direction score is NOT fair value.
                net_estimate = fair_per_share - entry_cost / config.quantity - (
                    fair_per_share + entry_cost / config.quantity
                ) * fee
                if net_estimate >= config.min_edge:
                    proposals.append((net_estimate, choice, contract, entry_cost,
                                      book.asks[0].price))
            if not proposals:
                rejected["no_positive_crossing_edge_or_depth"] += 1
                continue
            _, choice, contract, expected_cost, _ = max(proposals)
            if pair.market_id in market_last and decision - market_last[pair.market_id] < timedelta(
                seconds=config.cooldown_seconds
            ):
                rejected["overlap_purge"] += 1
                continue
            if active_until.get(pair.market_id, datetime.min.replace(tzinfo=UTC)) > decision:
                rejected["inventory_locked"] += 1
                continue
            # Release only exposures whose predetermined 5m horizon has elapsed.
            active_capital = [(until, capital) for until, capital in active_capital
                              if until is None or until > decision]
            if sum((c for _, c in active_capital), D(0)) + config.quantity > config.capital_limit:
                rejected["capital_exhausted"] += 1
                continue
            qualified += 1
            market_last[pair.market_id] = decision
            limit = expected_cost / config.quantity
            arrival_at = decision + timedelta(milliseconds=config.latency_ms)
            arrival = _next_quote(tapes[(ReplaySource.SIG, contract)], arrival_at,
                                  config.max_arrival_wait_ms, config.max_quote_age_ms)
            if arrival is None:
                rejected["no_latency_safe_arrival"] += 1
                continue
            fill_time, fill_book = arrival
            filled_cost = _walk(fill_book.asks, config.quantity, buy=True,
                                haircut=config.depth_haircut)
            if filled_cost is None or filled_cost / config.quantity > limit:
                rejected["ioc_limit_or_depth_no_fill"] += 1
                continue
            fills_touch_upper += 1
            entry_fee = filled_cost * fee
            marks: dict[str, float | None] = {}
            pnl_5m: Decimal | None = None
            close_time: datetime | None = None
            for seconds in HORIZONS:
                target = fill_time + timedelta(seconds=seconds)
                exit_snapshot = _next_quote(tapes[(ReplaySource.SIG, contract)], target,
                                            config.max_exit_wait_ms, config.max_quote_age_ms)
                proceeds = None
                if exit_snapshot is not None:
                    proceeds = _walk(exit_snapshot[1].bids, config.quantity, buy=False,
                                     haircut=config.depth_haircut)
                pnl = None if proceeds is None else proceeds - filled_cost - entry_fee - proceeds * fee
                marks[str(seconds)] = _as_float(pnl)
                if seconds == 300:
                    pnl_5m = pnl
                    close_time = exit_snapshot[0] if pnl is not None else None
            exited += int(pnl_5m is not None)
            # Unknown close is NOT marked as a profitable exit. Reserve forever and
            # report liquidation-zero downside separately until captured.
            if close_time is None:
                active_capital.append((None, config.quantity))
                active_until[pair.market_id] = datetime.max.replace(tzinfo=UTC)
            else:
                active_capital.append((close_time, config.quantity))
                active_until[pair.market_id] = close_time
            outcomes.append({
                "market": pair.market_id, "day": decision.date().isoformat(),
                "decision_at": decision.isoformat(), "filled_at": fill_time.isoformat(),
                "side": choice, "qty": float(config.quantity),
                "entry_cost": float(filled_cost), "entry_fee": float(entry_fee),
                "markouts_net_5s_30s_5m_1h": marks,
                "net_pnl_5m": _as_float(pnl_5m),
                "unresolved_liquidation_zero_bound": float(-filled_cost - entry_fee),
            })
        net_known = sum(o["net_pnl_5m"] for o in outcomes if o["net_pnl_5m"] is not None)
        pessimistic = sum(
            o["net_pnl_5m"] if o["net_pnl_5m"] is not None else
            o["unresolved_liquidation_zero_bound"] for o in outcomes
        )
        day_pnls: dict[str, float] = defaultdict(float)
        for o in outcomes:
            day_pnls[o["day"]] += (
                o["net_pnl_5m"] if o["net_pnl_5m"] is not None else
                o["unresolved_liquidation_zero_bound"]
            )
        lower = None
        if len(day_pnls) >= 5:
            daily = list(day_pnls.values())
            rng = random.Random(205)
            boots = sorted(sum(rng.choices(daily, k=len(daily))) for _ in range(2000))
            lower = boots[49]
        results[strategy] = {
            "signals": attempts, "qualified_orders": qualified,
            "touch_fill_upper_bound": fills_touch_upper,
            "filled_with_5m_depth_exit": exited,
            "empirically_calibrated_fill_probability": None,
            "net_pnl_known_exits": net_known,
            "net_pnl_liquidation_zero_stress": pessimistic,
            "lower_95pct_day_bootstrap_stress": lower,
            "independent_days": len(day_pnls),
            "rejections": dict(sorted(rejected.items())),
            "trades": outcomes,
        }
    return {
        "schema_version": 1,
        "status": "SCENARIO_ONLY_UNCALIBRATED_FILLS",
        "promotion": "BLOCKED_PENDING_PROSPECTIVE_FILL_AND_ECONOMIC_EVIDENCE",
        "parameters": {
            "fee_bps_per_side": str(config.fee_bps_per_side),
            "latency_ms": config.latency_ms, "min_edge": str(config.min_edge),
            "quantity": str(config.quantity), "capital_limit": str(config.capital_limit),
            "depth_haircut": str(config.depth_haircut),
            "five_minute_purge_seconds": config.cooldown_seconds,
        },
        "input_counts": {"events": len(frame_times), "feature_minutes": len(feature_list),
                         "accepted_pairs": len(pair_list)},
        "results": results,
        "claim_boundary": (
            "Latency-safe top/depth crossing is a hypothetical touch upper bound, "
            "not an observed fill; missing executable exits are not silently imputed. "
            "No strategy can be promoted from this output alone."
        ),
    }
