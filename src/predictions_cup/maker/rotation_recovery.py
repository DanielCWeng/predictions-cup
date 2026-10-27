"""Safety primitives for evidence capture and journalled capital rotation.

These are intentionally adapter-driven: no connection, I/O, or order sender
is created here. Production must inject existing RISK-002 / BUILD-009 gates.
Nothing trades merely by importing this module.
"""
from __future__ import annotations

import asyncio
import math
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class RotationEvidence:
    exit_exchange_id: str
    entry_exchange_id: str
    quantity: int
    net_improvement: float
    entry_ev: float
    observed_ns: int

    def __post_init__(self) -> None:
        if not self.exit_exchange_id or not self.entry_exchange_id:
            raise ValueError("exchange identity required")
        if self.exit_exchange_id == self.entry_exchange_id:
            raise ValueError("cannot rotate into identical exchange")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("positive integral size required")
        if self.observed_ns < 0 or any(
            not math.isfinite(x) for x in (self.net_improvement, self.entry_ev)
        ) or self.net_improvement <= 0 or self.entry_ev <= 0:
            raise ValueError("positive finite expected value required")

    @property
    def key(self) -> tuple[str, str]:
        return self.exit_exchange_id, self.entry_exchange_id


class RotationEvidenceCache:
    """Capture transient sightings; never confuse evidence with trading authority.

    A trust failure invalidates active candidates, but leaves bounded historical
    sightings available for diagnostics. Routine order changes invalidate only
    their own exchange. A confirmed position change invalidates all active
    candidates because the global gross-risk constraint may have changed.
    """

    def __init__(self, *, ttl_ns: int = 5_000_000_000, max_history: int = 64) -> None:
        if ttl_ns <= 0 or max_history < 1:
            raise ValueError("invalid bounded evidence cache")
        self.ttl_ns = ttl_ns
        self.history: deque[RotationEvidence] = deque(maxlen=max_history)
        self.active: dict[tuple[str, str], RotationEvidence] = {}
        self._positions: tuple[tuple[object, ...], ...] | None = None
        self._orders: dict[str, tuple[tuple[object, ...], ...]] = {}
        self.trusted = False
        self.qualified_observations = 0
        self.invalidations = 0

    def update_account(
        self, *,
        trusted: bool,
        positions: tuple[tuple[object, ...], ...],
        pending_orders: tuple[tuple[object, ...], ...],
    ) -> None:
        if not trusted:
            if self.active:
                self.invalidations += 1
            self.active.clear()
            self.trusted = False
            return
        self.trusted = True
        canonical_positions = tuple(sorted(positions))
        if self._positions != canonical_positions:
            self.active.clear()
            self.invalidations += 1
            self._positions = canonical_positions
        by_exchange: dict[str, list[tuple[object, ...]]] = {}
        for row in pending_orders:
            if not row or not isinstance(row[0], str):
                raise ValueError("orders require exchange ID in first field")
            by_exchange.setdefault(row[0], []).append(row)
        updated = {eid: tuple(sorted(items)) for eid, items in by_exchange.items()}
        changed = {
            eid for eid in self._orders.keys() | updated.keys()
            if self._orders.get(eid) != updated.get(eid)
        }
        for key in tuple(self.active):
            if key[0] in changed or key[1] in changed:
                self.active.pop(key)
                self.invalidations += 1
        self._orders = updated

    def offer(
        self,
        evidence: RotationEvidence,
        *,
        now_ns: int,
        publish: Callable[[RotationEvidence], None] | None = None,
    ) -> bool:
        if not self.trusted or now_ns < evidence.observed_ns:
            return False
        if now_ns - evidence.observed_ns > self.ttl_ns:
            return False
        previous = self.active.get(evidence.key)
        # Preserve meaningful arrivals instead of 100 repeated WS ticks.
        fresh = previous is None or (
            abs(previous.net_improvement - evidence.net_improvement) >= 1.0
            or now_ns - previous.observed_ns >= self.ttl_ns // 2
        )
        self.active[evidence.key] = evidence
        if fresh:
            self.history.append(evidence)
            self.qualified_observations += 1
            if publish is not None:
                publish(evidence)
        return fresh

    def eligible(self, now_ns: int) -> tuple[RotationEvidence, ...]:
        if not self.trusted:
            return ()
        for key, item in tuple(self.active.items()):
            if now_ns < item.observed_ns or now_ns - item.observed_ns > self.ttl_ns:
                self.active.pop(key)
        return tuple(sorted(self.active.values(), key=lambda x: -x.net_improvement))


@dataclass(frozen=True, slots=True)
class AuthoritativeProof:
    exchange_id: str
    signed_shares: int
    version: int
    trusted: bool
    reconciliation_complete: bool
    open_orders: int
    uncertain_exposure: int

    def clean(self) -> bool:
        return (
            self.trusted and self.reconciliation_complete
            and self.open_orders == 0 and self.uncertain_exposure == 0
            and type(self.signed_shares) is int and type(self.version) is int
        )


@dataclass(frozen=True, slots=True)
class RotationRequest:
    operation_id: str
    exit_exchange_id: str
    entry_exchange_id: str
    requested_shares: int
    min_incremental_gain: float

    def __post_init__(self) -> None:
        if not all((self.operation_id, self.exit_exchange_id, self.entry_exchange_id)):
            raise ValueError("identities required")
        if self.exit_exchange_id == self.entry_exchange_id:
            raise ValueError("distinct exchanges required")
        if type(self.requested_shares) is not int or self.requested_shares < 1:
            raise ValueError("integer size required")
        if not math.isfinite(self.min_incremental_gain) or self.min_incremental_gain <= 0:
            raise ValueError("positive gain hurdle required")


@dataclass(frozen=True, slots=True)
class ExitApproval:
    quantity: int
    executable_limit_ticks: int
    worst_case_cash_cost: float
    depth_age_ns: int
    approved_by_risk: bool
    strict_reduction_proved: bool

    def clean(self, max_age_ns: int) -> bool:
        return (
            type(self.quantity) is int and self.quantity > 0
            and type(self.executable_limit_ticks) is int
            and 1 <= self.executable_limit_ticks <= 199
            and math.isfinite(self.worst_case_cash_cost)
            and 0 <= self.worst_case_cash_cost
            and 0 <= self.depth_age_ns <= max_age_ns
            and self.approved_by_risk and self.strict_reduction_proved
        )


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    operation_id: str
    state: str

    @property
    def certain(self) -> bool:
        return self.state in {"ACKED", "FILLED", "PARTIALLY_FILLED", "CANCELLED", "RECONCILED"}


@dataclass(frozen=True, slots=True)
class EntryApproval:
    quantity: int
    net_incremental_gain: float
    worst_case_cash_cost: float
    depth_age_ns: int
    approved_by_risk: bool

    def clean(self, max_age_ns: int, min_gain: float, max_notional: float) -> bool:
        return (
            type(self.quantity) is int and self.quantity > 0
            and math.isfinite(self.net_incremental_gain)
            and self.net_incremental_gain >= min_gain
            and math.isfinite(self.worst_case_cash_cost)
            and 0 <= self.worst_case_cash_cost <= max_notional
            and 0 <= self.depth_age_ns <= max_age_ns
            and self.approved_by_risk
        )


@dataclass(frozen=True, slots=True)
class RotationOutcome:
    status: str
    confirmed_reduction_shares: int = 0
    entry_shares: int = 0
    reason: str = ""


class TwoPhaseRotation:
    """One-at-a-time controlled exit -> authoritative reconcile -> recheck -> entry.

    Each adapter must use existing risk-approved, journalled order operations,
    including cancellation of unfilled exit remainder. No order is considered
    finished merely because a request got an ACK.
    """

    def __init__(
        self, *, live_enabled: bool = False,
        max_tranche_shares: int = 100,
        max_entry_notional: float = 100.0,
        max_depth_age_ns: int = 1_500_000_000,
    ) -> None:
        if max_tranche_shares < 1 or max_entry_notional <= 0 or max_depth_age_ns <= 0:
            raise ValueError("invalid canary limits")
        self.live_enabled = live_enabled
        self.max_tranche_shares = max_tranche_shares
        self.max_entry_notional = max_entry_notional
        self.max_depth_age_ns = max_depth_age_ns
        self._used_operations: set[str] = set()
        self._lock = asyncio.Lock()

    async def run(
        self,
        request: RotationRequest,
        *,
        read_before: Callable[[str], Awaitable[AuthoritativeProof]],
        prepare_exit: Callable[[RotationRequest, AuthoritativeProof], Awaitable[ExitApproval]],
        dispatch_exit: Callable[[RotationRequest, ExitApproval], Awaitable[DispatchReceipt]],
        reconcile_exit: Callable[[str], Awaitable[AuthoritativeProof]],
        prepare_entry: Callable[[RotationRequest, int], Awaitable[EntryApproval]],
        dispatch_entry: Callable[[RotationRequest, EntryApproval], Awaitable[DispatchReceipt]],
    ) -> RotationOutcome:
        if not self.live_enabled:
            return RotationOutcome("DISABLED")
        if self._lock.locked() or request.operation_id in self._used_operations:
            return RotationOutcome("BLOCKED", reason="busy_or_duplicate")
        async with self._lock:
            if request.operation_id in self._used_operations:
                return RotationOutcome("BLOCKED", reason="duplicate")
            # Consume ID before first asynchronous operation; a timeout cannot
            # cause the same logical operation to place a second exit.
            self._used_operations.add(request.operation_id)
            before = await read_before(request.exit_exchange_id)
            if (
                not before.clean()
                or before.exchange_id != request.exit_exchange_id
                or before.signed_shares == 0
            ):
                return RotationOutcome("BLOCKED", reason="before_not_authoritative")
            exit_order = await prepare_exit(request, before)
            if (
                not exit_order.clean(self.max_depth_age_ns)
                or exit_order.quantity > min(
                    request.requested_shares, self.max_tranche_shares,
                    abs(before.signed_shares),
                )
            ):
                return RotationOutcome("BLOCKED", reason="exit_not_proved")
            receipt = await dispatch_exit(request, exit_order)
            if not receipt.certain or receipt.operation_id != request.operation_id + ':exit':
                return RotationOutcome("HALTED", reason="exit_outcome_uncertain")
            after = await reconcile_exit(request.exit_exchange_id)
            if not after.clean() or after.exchange_id != before.exchange_id:
                return RotationOutcome("HALTED", reason="post_exit_not_authoritative")
            if after.version <= before.version:
                return RotationOutcome("HALTED", reason="no_new_authoritative_version")
            if before.signed_shares > 0:
                filled = before.signed_shares - after.signed_shares
                no_flip = 0 <= after.signed_shares <= before.signed_shares
            else:
                filled = after.signed_shares - before.signed_shares
                no_flip = before.signed_shares <= after.signed_shares <= 0
            if not no_flip or not 0 < filled <= exit_order.quantity:
                return RotationOutcome("HALTED", reason="no_verified_reduction")
            next_entry = await prepare_entry(request, filled)
            if (
                not next_entry.clean(
                    self.max_depth_age_ns, request.min_incremental_gain,
                    self.max_entry_notional,
                )
                or next_entry.quantity > filled
            ):
                return RotationOutcome(
                    "RELEASED_ONLY", filled, reason="replacement_no_longer_approved"
                )
            new_receipt = await dispatch_entry(request, next_entry)
            if not new_receipt.certain or new_receipt.operation_id != request.operation_id + ':entry':
                return RotationOutcome(
                    "ENTRY_UNCERTAIN", filled, reason="requires_order_reconciliation"
                )
            # ACK is not a fill; account reconciliation still owns realised P&L.
            return RotationOutcome("ENTRY_DISPATCHED", filled, next_entry.quantity)
