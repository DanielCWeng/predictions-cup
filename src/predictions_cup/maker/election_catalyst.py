"""Bounded, event-driven election catalyst qualification; never submits orders.

Evidence and conservative probability *bounds* must be authenticated/validated
by an upstream operator or licensed source adapter. Hostname checks prevent
accidental mismaps but do not authenticate a news report's contents.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Literal, Mapping
from urllib.parse import urlparse

from predictions_cup.maker.taker_economics import evaluate_taker_economics
from predictions_cup.runtime.models import RuntimeLevel

Direction = Literal["BUY", "SELL"]
_MAX_EVENTS = 128
_MAX_REPORT_BYTES = 8192


@dataclass(frozen=True, slots=True)
class CatalystReport:
    event_key: str
    exchange_id: str
    report_key: str
    source_id: str
    source_family: str
    source_url: str
    direction: Direction
    fair_lower: float
    fair_upper: float
    published_epoch_ms: int
    received_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class MarketView:
    exchange_id: str
    market_open: bool
    account_trusted: bool
    bbo_trusted: bool
    depth_trusted: bool
    depth_observed_ns: int | None
    now_monotonic_ns: int
    bids: tuple[RuntimeLevel, ...]
    asks: tuple[RuntimeLevel, ...]
    available_cash: float | None
    reducible_shares: int


@dataclass(frozen=True, slots=True)
class QualifiedCatalyst:
    event_key: str
    exchange_id: str
    direction: Direction
    fair_bound: float
    quantity: int
    limit_price_ticks: int
    net_edge_per_share: float
    net_expected_value: float
    worst_case_loss: float
    evidence_urls: tuple[str, str]
    decision_monotonic_ns: int


class ElectionCatalystGate:
    """Two independent evidence families + fresh executable depth + EV screen.

    This does not call the exchange, refresh timestamps, estimate odds from vote
    percentages, grant risk authority or generate an order. It qualifies a
    *wake-up candidate* to the existing taker pipeline, which independently
    enforces PM pricing, RISK-002 and BUILD-009.
    """

    def __init__(
        self,
        *,
        exact_exchange_ids: frozenset[str],
        source_registry: Mapping[str, tuple[str, str]],
        max_evidence_age_ns: int = 10_000_000_000,
        max_publication_age_ms: int = 120_000,
        max_depth_age_ns: int = 1_500_000_000,
        min_net_edge: float = 0.005,
        size: int = 50,
        fee_per_share: float = 0.0,
        latency_haircut: float = 0.005,
        adverse_haircut: float = 0.005,
    ) -> None:
        if not exact_exchange_ids or not source_registry:
            raise ValueError("explicit verified markets and trusted source registry required")
        if min(max_evidence_age_ns, max_publication_age_ms, max_depth_age_ns, size) <= 0:
            raise ValueError("positive age limits and size required")
        if any(x < 0 or not math.isfinite(x) for x in
               (min_net_edge, fee_per_share, latency_haircut, adverse_haircut)):
            raise ValueError("invalid economics")
        self.exact_exchange_ids = exact_exchange_ids
        self.source_registry = dict(source_registry)
        self.max_evidence_age_ns = max_evidence_age_ns
        self.max_publication_age_ms = max_publication_age_ms
        self.max_depth_age_ns = max_depth_age_ns
        self.min_net_edge = min_net_edge
        self.size = size
        self.fee = fee_per_share
        self.latency = latency_haircut
        self.adverse = adverse_haircut
        self._evidence: OrderedDict[tuple[str, str], dict[str, CatalystReport]] = OrderedDict()
        self._disqualified: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._completed: OrderedDict[tuple[str, str], int] = OrderedDict()
        self.counters: dict[str, int] = {}

    def _count(self, reason: str) -> str:
        self.counters[reason] = self.counters.get(reason, 0) + 1
        return reason

    def _prune(self, now_ns: int) -> None:
        for store in (self._evidence, self._disqualified, self._completed):
            while store:
                key, value = next(iter(store.items()))
                age = (max((v.received_monotonic_ns for v in value.values()))
                       if isinstance(value, dict) else value)
                if len(store) <= _MAX_EVENTS and 0 <= now_ns - age <= self.max_evidence_age_ns:
                    break
                store.popitem(last=False)

    def consider(
        self,
        report: CatalystReport,
        *,
        now_epoch_ms: int,
        now_monotonic_ns: int,
        view: MarketView | None,
    ) -> tuple[str, QualifiedCatalyst | None]:
        self._prune(now_monotonic_ns)
        key = (report.event_key, report.exchange_id)
        if key in self._completed:
            return self._count("duplicate_event"), None
        if key in self._disqualified:
            return self._count("contradicted_event"), None
        if report.exchange_id not in self.exact_exchange_ids:
            return self._count("unknown_mapping"), None
        spec = self.source_registry.get(report.source_id)
        host = urlparse(report.source_url)
        if (
            spec is None or report.source_family != spec[0]
            or host.scheme != "https" or host.hostname != spec[1]
            or not report.report_key or not report.event_key
            or len(report.report_key) > 160 or len(report.event_key) > 160
        ):
            return self._count("unverified_source"), None
        if report.direction not in ("BUY", "SELL") or not (
            math.isfinite(report.fair_lower) and math.isfinite(report.fair_upper)
            and 0.0 <= report.fair_lower <= report.fair_upper <= 1.0
        ):
            return self._count("invalid_probability_bound"), None
        age_ns = now_monotonic_ns - report.received_monotonic_ns
        publication_age = now_epoch_ms - report.published_epoch_ms
        if (
            age_ns < 0 or age_ns > self.max_evidence_age_ns
            or publication_age < -2_000 or publication_age > self.max_publication_age_ms
        ):
            return self._count("stale_or_future_evidence"), None
        family_reports = self._evidence.setdefault(key, {})
        previous = family_reports.get(report.source_family)
        if previous is not None:
            if previous.report_key == report.report_key:
                return self._count("duplicate_report"), None
            if report.direction != previous.direction:
                self._disqualified[key] = now_monotonic_ns
                self._evidence.pop(key, None)
                return self._count("contradictory_reports"), None
        if any(r.direction != report.direction for r in family_reports.values()):
            self._disqualified[key] = now_monotonic_ns
            self._evidence.pop(key, None)
            return self._count("contradictory_reports"), None
        family_reports[report.source_family] = report
        self._evidence.move_to_end(key)
        while len(self._evidence) > _MAX_EVENTS:
            self._evidence.popitem(last=False)
        if len(family_reports) < 2:
            return self._count("awaiting_independent_confirmation"), None
        reports = tuple(family_reports.values())
        if any(now_monotonic_ns - r.received_monotonic_ns > self.max_evidence_age_ns
               for r in reports):
            return self._count("stale_corrobation"), None
        if max(r.fair_lower for r in reports) > min(r.fair_upper for r in reports):
            self._disqualified[key] = now_monotonic_ns
            self._evidence.pop(key, None)
            return self._count("contradictory_bounds"), None
        bound = (
            min(r.fair_lower for r in reports)
            if report.direction == "BUY"
            else max(r.fair_upper for r in reports)
        )
        if view is None or not (
            view.exchange_id == report.exchange_id and view.market_open
            and view.account_trusted and view.bbo_trusted
            and view.depth_trusted and view.available_cash is not None
            and 0 <= now_monotonic_ns - view.now_monotonic_ns <= 250_000_000
        ):
            return self._count("market_or_account_untrusted"), None
        levels = view.asks if report.direction == "BUY" else view.bids
        econ = evaluate_taker_economics(
            direction=report.direction,
            levels=levels,
            conservative_fair=bound,
            desired_quantity=self.size,
            trusted_depth=view.depth_trusted,
            book_age_ns=(
                -1 if view.depth_observed_ns is None
                else view.now_monotonic_ns - view.depth_observed_ns
            ),
            reference_age_ns=max(
                now_monotonic_ns - r.received_monotonic_ns for r in reports
            ),
            max_book_age_ns=self.max_depth_age_ns,
            max_reference_age_ns=self.max_evidence_age_ns,
            fee_per_share=self.fee,
            latency_haircut=self.latency,
            adverse_selection_haircut=self.adverse,
            min_net_edge_per_share=self.min_net_edge,
            available_cash=view.available_cash,
            reducible_inventory_shares=view.reducible_shares,
        )
        if not econ.admissible or econ.limit_price_ticks is None or econ.net_expected_value <= 0:
            return self._count("not_executable_" + econ.reason), None
        qualified = QualifiedCatalyst(
            event_key=report.event_key,
            exchange_id=report.exchange_id,
            direction=report.direction,
            fair_bound=bound,
            quantity=econ.quantity,
            limit_price_ticks=econ.limit_price_ticks,
            net_edge_per_share=econ.net_edge_per_share,
            net_expected_value=econ.net_expected_value,
            worst_case_loss=econ.worst_case_settlement_loss,
            evidence_urls=(reports[0].source_url, reports[1].source_url),
            decision_monotonic_ns=now_monotonic_ns,
        )
        self._completed[key] = now_monotonic_ns
        self._evidence.pop(key, None)
        return self._count("qualified_for_taker_recheck"), qualified


class CatalystWakeHandoff:
    """Wakes existing maker/taker evaluation; never sends an order."""

    def __init__(self, notify_exchange: Callable[[tuple[str, ...], int], None]) -> None:
        self._notify = notify_exchange
        self.forwarded = 0

    def forward(self, candidate: QualifiedCatalyst) -> None:
        self._notify((candidate.exchange_id,), observed_monotonic_ns=candidate.decision_monotonic_ns)
        self.forwarded += 1


class ElectionCatalystRouter:
    """Event-driven, in-process connector to the existing taker wake path.

    Upstream MUST authenticate and independently verify event content and fair
    bounds before calling on_verified_report. No order API is exposed here.
    """

    def __init__(
        self,
        gate: ElectionCatalystGate,
        *,
        current_market_view: Callable[[str], MarketView | None],
        notify_sig: Callable[..., None],
        utc_ms: Callable[[], int],
        monotonic_ns: Callable[[], int],
    ) -> None:
        self.gate = gate
        self._get_market_view = current_market_view
        self._wake = CatalystWakeHandoff(notify_sig)
        self._utc_ms = utc_ms
        self._mono_ns = monotonic_ns

    def on_verified_report(
        self, report: CatalystReport
    ) -> tuple[str, QualifiedCatalyst | None]:
        now_ns = self._mono_ns()
        reason, candidate = self.gate.consider(
            report,
            now_epoch_ms=self._utc_ms(),
            now_monotonic_ns=now_ns,
            view=self._get_market_view(report.exchange_id),
        )
        if candidate is not None:
            self._wake.forward(candidate)
        return reason, candidate

    @property
    def forwarded(self) -> int:
        return self._wake.forwarded
