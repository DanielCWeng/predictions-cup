"""Frozen 005I after-cost executability: SYNTHETIC, reviewer-only oracles.

No production evaluator, native feed, issuer attestation, order or real economic
result is present. The purpose is to pin falsifiable input/output interfaces.
Source: 005I frozen SPLIT_PLAN/PRE_HOLDOUT_FREEZE and draft #209→#214.
All apparent positive numbers are invented-book scenarios, never alpha.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

D = Decimal
WHOLE_LOTS = (1, 5, 10, 20)
HORIZONS = (5, 30, 300)
CONTROLS = frozenset({"NO_TRADE", "REVERSAL", "MOMENTUM", "RANDOM"})
TRAIN_START = datetime(2026, 9, 1, tzinfo=UTC)
DEV_END = datetime(2026, 9, 20, 23, 59, 59, tzinfo=UTC)
SEALED_END = datetime(2026, 9, 28, 23, 59, 59, tzinfo=UTC)


@dataclass(frozen=True)
class Minute:
    ended_at: datetime
    received_at: datetime
    source_sequence: int
    epoch: str
    seed_hash: str
    raw_sha: str


@dataclass(frozen=True)
class Level:
    price: Decimal
    shares: int


@dataclass(frozen=True)
class Book:
    received_at: datetime
    published_at: datetime
    exchange: str
    source_sequence: int
    epoch: str
    revision: str
    raw_sha: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]


@dataclass(frozen=True)
class InventedTrial:
    decision_at: datetime
    minutes: tuple[Minute, ...]
    decision_book: Book
    arrival: Book
    exits: tuple[Book, ...]
    quantity: Decimal
    side: str
    mapping: str
    mapping_sha: str | None
    rule_info_sha: str | None
    pm_original_sha: str | None
    sig_original_sha: str | None
    issuer_clock_proven: bool
    fee_provenance_sha: str | None
    fee_per_share_per_side: Decimal | None
    holdout_reused: bool = False


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    gross_quote_scenario: Decimal | None = None
    after_cost_quote_scenario: Decimal | None = None
    realised_net_pnl: Decimal | None = None


def _reject(reason: str) -> Verdict:
    return Verdict("NET_NOT_ESTIMABLE", reason)


def _price_for_shares(
    book: Book, side: str, entering: bool, quantity: int,
) -> Decimal | None:
    """Whole-share 50% depth haircut, ONE native YES-denominated exchange."""
    if side not in {"YES", "NO"} or quantity not in WHOLE_LOTS:
        return None
    levels = (*book.bids, *book.asks)
    if (
        not book.bids or not book.asks
        or any(not D(0) < x.price < D(1) or x.shares < 0 for x in levels)
        or max(x.price for x in book.bids) >= min(x.price for x in book.asks)
        or len({x.price for x in book.bids}) != len(book.bids)
        or len({x.price for x in book.asks}) != len(book.asks)
    ):
        return None
    # Held NO is short YES on this SAME exchange:
    # NO buy: complement YES BID; NO sell: complement YES ASK.
    if side == "YES":
        ladder = book.asks if entering else book.bids
        oriented = [(x.price, x.shares) for x in ladder]
    else:
        ladder = book.bids if entering else book.asks
        oriented = [(D(1) - x.price, x.shares) for x in ladder]
    remaining, cash = quantity, D(0)
    for price, shares in sorted(oriented, reverse=not entering):
        # A displayed 1-share level has ZERO conservative whole-share depth.
        take = min(remaining, shares // 2)
        remaining -= take
        cash += price * take
        if remaining == 0:
            return cash
    return None


def evaluate_invented_trial(trial: InventedTrial) -> Verdict:
    """Fail closed on absent provenance; never infer realised PnL from quotes."""
    if trial.holdout_reused or TRAIN_START <= trial.decision_at <= SEALED_END:
        return _reject("SEALED_005I_HOLDOUT_NOT_AN_ECONOMIC_TEST")
    if trial.decision_at.tzinfo is None or trial.decision_at.utcoffset() is None:
        return _reject("NO_UTC_DECISION_CLOCK")
    if (
        not trial.mapping_sha or not trial.rule_info_sha
        or not trial.pm_original_sha or not trial.sig_original_sha
        or not trial.issuer_clock_proven
    ):
        return _reject("MISSING_ORIGINAL_SOURCE_RULES_OR_ISSUER_CLOCK")
    if (
        trial.fee_per_share_per_side is None or not trial.fee_provenance_sha
        or trial.fee_per_share_per_side < 0
    ):
        return _reject("NATIVE_FEES_NULL_OR_UNVERIFIED")
    if trial.mapping not in {"SAME", "COMPLEMENT"} or trial.side not in {"YES", "NO"}:
        return _reject("BAD_NO_ORIENTATION_OR_MAPPING")
    if trial.quantity != trial.quantity.to_integral_value():
        return _reject("SUB_SHARE_NOT_EXECUTABLE")
    if int(trial.quantity) not in WHOLE_LOTS:
        return _reject("WHOLE_LOT_NOT_IN_FROZEN_SIZES")
    if len(trial.minutes) != 6:
        return _reject("NOT_SIX_COMPLETED_PM_MINUTES")
    ends = [m.ended_at for m in trial.minutes]
    if any(right - left != timedelta(minutes=1) for left, right in zip(
        ends, ends[1:], strict=True,
    )):
        return _reject("NONCONTIGUOUS_COMPLETED_PM_MINUTES")
    if (
        ends[-1].second or ends[-1].microsecond
        or trial.decision_at <= ends[-1]
        or any(m.received_at < m.ended_at or m.received_at > trial.decision_at
               for m in trial.minutes)
        or any(not m.epoch or not m.seed_hash or not m.raw_sha
               for m in trial.minutes)
        or len({(m.epoch, m.seed_hash) for m in trial.minutes}) != 1
        or any(right.source_sequence <= left.source_sequence
               for left, right in zip(trial.minutes, trial.minutes[1:], strict=True))
    ):
        return _reject("PM_SEED_EPOCH_SEQUENCE_OR_FUTURE_LEAK")
    if len(trial.exits) != len(HORIZONS):
        return _reject("MISSING_5S_30S_300S_EXIT")
    books = (trial.decision_book, trial.arrival, *trial.exits)
    if (
        len({b.exchange for b in books}) != 1
        or not books[0].exchange
        or len({b.epoch for b in books}) != 1
        or any(not b.revision or not b.raw_sha for b in books)
        or any(b.published_at > b.received_at for b in books)
        or any(right.source_sequence <= left.source_sequence
               for left, right in zip(books, books[1:], strict=True))
    ):
        return _reject("SIG_ISSUER_RECEIPT_REVISION_OR_EXCHANGE_NOT_PROVEN")
    if not (
        timedelta(0) <= trial.decision_at - books[0].received_at
        <= timedelta(seconds=2)
        and timedelta(milliseconds=100) <= trial.arrival.received_at - trial.decision_at
        <= timedelta(milliseconds=1000)
    ):
        return _reject("DECISION_OR_POST_LATENCY_ARRIVAL_STALE")
    for secs, exit_book in zip(HORIZONS, trial.exits, strict=True):
        age = exit_book.received_at - (trial.decision_at + timedelta(seconds=secs))
        if not timedelta(0) <= age <= timedelta(seconds=2):
            return _reject(f"STALE_MISSING_OR_REUSED_EXIT_{secs}S")
    qty = int(trial.quantity)
    # The decision book is a feasibility screen only, NOT a filled order.
    if _price_for_shares(trial.decision_book, trial.side, True, qty) is None:
        return _reject("DECISION_INSUFFICIENT_WHOLE_SHARE_DEPTH")
    entry = _price_for_shares(trial.arrival, trial.side, True, qty)
    if entry is None:
        return _reject("ARRIVAL_ZERO_DEPTH_OR_UNEXECUTABLE")
    closes = [_price_for_shares(b, trial.side, False, qty) for b in trial.exits]
    if any(v is None for v in closes):
        return _reject("EXIT_ZERO_DEPTH_OR_UNEXECUTABLE")
    exit_300 = closes[2]
    assert exit_300 is not None
    gross = exit_300 - entry
    scenario_net = gross - 2 * trial.fee_per_share_per_side * qty
    # These book prices are INVENTED. Even positive scenario_net is not a fill.
    return Verdict("SYNTHETIC_SCENARIO_ONLY", "NOT_NATIVE_FILL_OR_EXIT_CASH",
                   gross, scenario_net, None)


def invented_book(t: datetime, seq: int, bid: str = "0.40",
                  ask: str = "0.50", qty: int = 40) -> Book:
    return Book(t, t - timedelta(milliseconds=100), "SIG_EX_123", seq, "E1",
                f"r{seq}", "f" * 64, (Level(D(bid), qty),),
                (Level(D(ask), qty),))


def invented_trial(side: str = "YES", quantity: str = "5") -> InventedTrial:
    t = datetime(2026, 10, 17, 12, 0, 1, tzinfo=UTC)
    last_end = t.replace(second=0)
    minutes = tuple(Minute(
        last_end - timedelta(minutes=5 - i),
        last_end - timedelta(minutes=5 - i) + timedelta(milliseconds=100),
        i + 1, "PM_EPOCH_1", "PM_SEED", "a" * 64,
    ) for i in range(6))
    return InventedTrial(
        decision_at=t, minutes=minutes,
        decision_book=invented_book(t - timedelta(milliseconds=200), 1),
        arrival=invented_book(t + timedelta(milliseconds=200), 2,
                              bid="0.40", ask="0.42"),
        exits=tuple(invented_book(t + timedelta(seconds=s), i + 3,
                                  bid="0.55", ask="0.60")
                    for i, s in enumerate(HORIZONS)),
        quantity=D(quantity), side=side, mapping="SAME",
        mapping_sha="b" * 64, rule_info_sha="c" * 64,
        pm_original_sha="d" * 64, sig_original_sha="e" * 64,
        issuer_clock_proven=True, fee_provenance_sha="f" * 64,
        fee_per_share_per_side=D("0.005"),
    )


@pytest.mark.parametrize("qty", ["1", "5", "10", "20"])
def test_whole_lots_expose_only_synthetic_quote_scenario(qty: str) -> None:
    result = evaluate_invented_trial(invented_trial(quantity=qty))
    assert result.status == "SYNTHETIC_SCENARIO_ONLY"
    assert result.gross_quote_scenario == D("0.13") * D(qty)
    assert result.after_cost_quote_scenario == D("0.12") * D(qty)
    assert result.realised_net_pnl is None


def test_no_uses_same_exchange_yes_bid_and_complementary_yes_ask() -> None:
    trial = invented_trial("NO")
    books = (trial.decision_book, trial.arrival, *trial.exits)
    changed = tuple(replace(b, bids=(Level(D("0.65"), 40),),
                            asks=(Level(D("0.75"), 40),))
                    for b in books[:2])
    exits = tuple(replace(b, bids=(Level(D("0.40"), 40),),
                          asks=(Level(D("0.50"), 40),))
                  for b in trial.exits)
    result = evaluate_invented_trial(replace(
        trial, decision_book=changed[0], arrival=changed[1], exits=exits,
    ))
    assert result.gross_quote_scenario == D("0.15") * trial.quantity
    assert result.after_cost_quote_scenario == D("0.14") * trial.quantity
    assert result.realised_net_pnl is None


@pytest.mark.parametrize("field", [
    "pm_original_sha", "sig_original_sha", "rule_info_sha", "mapping_sha",
])
def test_missing_original_provenance_censors_net(field: str) -> None:
    assert evaluate_invented_trial(replace(invented_trial(),
                                          **{field: None})).status == "NET_NOT_ESTIMABLE"


def test_missing_native_fee_or_issuer_clock_is_not_zero_cost() -> None:
    t = invented_trial()
    assert evaluate_invented_trial(replace(
        t, fee_per_share_per_side=None,
    )).reason == "NATIVE_FEES_NULL_OR_UNVERIFIED"
    assert evaluate_invented_trial(replace(
        t, issuer_clock_proven=False,
    )).status == "NET_NOT_ESTIMABLE"


def test_whole_share_and_canonical_mapping_gates() -> None:
    t = invented_trial()
    assert evaluate_invented_trial(replace(
        t, quantity=D("1.5"),
    )).reason == "SUB_SHARE_NOT_EXECUTABLE"
    assert evaluate_invented_trial(replace(
        t, mapping="NOT_REVIEWED",
    )).reason == "BAD_NO_ORIENTATION_OR_MAPPING"


def test_midpoint_illusion_cannot_hide_negative_crossed_book_exit() -> None:
    t = invented_trial()
    # Decision midpoint 0.45 vs future midpoint 0.65 (looks +0.20).
    # True crossed entry ask 0.60 vs exit bid 0.55 loses before fees.
    arr = replace(t.arrival, asks=(Level(D("0.60"), 40),))
    exits = tuple(replace(b, bids=(Level(D("0.55"), 40),),
                          asks=(Level(D("0.75"), 40),)) for b in t.exits)
    result = evaluate_invented_trial(replace(t, arrival=arr, exits=exits))
    assert result.status == "SYNTHETIC_SCENARIO_ONLY"
    assert result.after_cost_quote_scenario < 0
    assert result.realised_net_pnl is None


def test_same_price_depth_50_to_zero_invalidates_arrival_x1_negative() -> None:
    t = invented_trial()
    # A newer receipt supersedes the prior book despite UNCHANGED prices.
    arrival = replace(t.arrival, bids=(Level(D("0.40"), 0),),
                      asks=(Level(D("0.42"), 0),))
    result = evaluate_invented_trial(replace(t, arrival=arrival))
    assert result.reason == "ARRIVAL_ZERO_DEPTH_OR_UNEXECUTABLE"
    assert result.realised_net_pnl is None


@pytest.mark.parametrize("seconds", HORIZONS)
def test_missing_or_stale_exit_is_censored(seconds: int) -> None:
    t = invented_trial()
    i = HORIZONS.index(seconds)
    exits = list(t.exits)
    exits[i] = replace(exits[i], received_at=exits[i].received_at
                       + timedelta(seconds=10))
    result = evaluate_invented_trial(replace(t, exits=tuple(exits)))
    assert result.status == "NET_NOT_ESTIMABLE"
    assert result.reason == f"STALE_MISSING_OR_REUSED_EXIT_{seconds}S"


def test_six_finished_minutes_and_pm_seed_are_mandatory() -> None:
    t = invented_trial()
    assert evaluate_invented_trial(replace(
        t, minutes=t.minutes[:-1],
    )).reason == "NOT_SIX_COMPLETED_PM_MINUTES"
    changed = list(t.minutes)
    changed[3] = replace(changed[3], seed_hash="UNINITIALISED")
    assert evaluate_invented_trial(replace(
        t, minutes=tuple(changed),
    )).reason == "PM_SEED_EPOCH_SEQUENCE_OR_FUTURE_LEAK"
    changed = list(t.minutes)
    changed[-1] = replace(changed[-1], received_at=t.decision_at
                          + timedelta(seconds=1))
    assert evaluate_invented_trial(replace(
        t, minutes=tuple(changed),
    )).reason == "PM_SEED_EPOCH_SEQUENCE_OR_FUTURE_LEAK"


def test_old_sealed_holdout_cannot_be_replayed_as_fresh_economics() -> None:
    t = invented_trial()
    assert evaluate_invented_trial(replace(
        t, holdout_reused=True,
    )).reason == "SEALED_005I_HOLDOUT_NOT_AN_ECONOMIC_TEST"
    old = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert evaluate_invented_trial(replace(
        t, decision_at=old,
    )).status == "NET_NOT_ESTIMABLE"


def clustered_holdout_gate(
    episodes: tuple[tuple[str, str, datetime], ...], controls: frozenset[str],
) -> str:
    """Test-only frozen min sample sizes; never iid-bootstrap repeated quotes."""
    if not CONTROLS.issubset(controls):
        return "NET_NOT_ESTIMABLE"
    if len({event_id for event_id, _, _ in episodes}) < 10:
        return "NET_NOT_ESTIMABLE"
    if len({at.date() for _, _, at in episodes}) < 5 or len(episodes) < 100:
        return "NET_NOT_ESTIMABLE"
    last_by_market: dict[str, datetime] = {}
    for _, market, at in sorted(episodes, key=lambda x: x[2]):
        if market in last_by_market and at - last_by_market[market] < timedelta(
            seconds=300,
        ):
            return "NET_NOT_ESTIMABLE"
        last_by_market[market] = at
    # Even independent *synthetic* clusters cannot certify realised economics.
    return "SYNTHETIC_SAMPLE_GATE_ONLY"


def test_serial_duplicates_and_missing_controls_do_not_fake_holdout_power() -> None:
    t = datetime(2026, 10, 17, 12, tzinfo=UTC)
    serial = tuple(("event_1", "market_1", t + timedelta(seconds=i))
                   for i in range(100))
    assert clustered_holdout_gate(serial, CONTROLS) == "NET_NOT_ESTIMABLE"
    assert clustered_holdout_gate(serial, frozenset({"REVERSAL"})) == (
        "NET_NOT_ESTIMABLE"
    )
