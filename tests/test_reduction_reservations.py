"""Synthetic SQLite adversarial tests; no SIG, maker, risk gate or network."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from predictions_cup.research.reduction_reservations import (
    OfflineReductionReservations,
    ReductionReservation,
    ReservationDenied,
)
from predictions_cup.research.signed_reduction_screen import SignedInventoryEvidence


def ev(ledger: dict[int, int] | None = None) -> SignedInventoryEvidence:
    values = {-1: -2500, 1: 97500} if ledger is None else ledger
    signed = {str(key): qty for key, qty in values.items()}
    return SignedInventoryEvidence(
        signed_by_exchange=signed,
        account_gross=sum(abs(qty) for qty in signed.values()),
        account_net=sum(signed.values()),
        account_trusted=True,
        reconciliation_complete=True,
        signed_positions_verified=True,
        venue_netting_verified=True,
        market_open_verified=True,
        global_halt_clear=True,
        account_age_seconds=1.0,
    )


def start(
    tmp_path: Path, data: SignedInventoryEvidence | None = None
) -> OfflineReductionReservations:
    r = OfflineReductionReservations(tmp_path / "isolated_reduction_sim.sqlite")
    r.seed(epoch="signed-account-01", evidence=data or ev(), cap=100000, age=5.0)
    return r


def reserve(
    r: OfflineReductionReservations, *, intent: str = "i1", qty: int = 1000,
    ex: str = "-1", side: str = "YES", action: str = "BUY",
    data: SignedInventoryEvidence | None = None,
) -> ReductionReservation:
    return r.reserve(
        epoch="signed-account-01", evidence=data or ev(), intent_id=intent,
        exchange_id=ex, outcome_side=side, action=action, quantity=qty,
        cap=100000, age=5.0,
    )


def test_canonical_both_sides_and_default_no_execution(tmp_path: Path) -> None:
    r = start(tmp_path)
    held_no = reserve(r)
    held_yes = reserve(r, intent="yes-close", ex="1", qty=500, side="NO", action="BUY")
    assert (held_no.signed_delta, held_yes.signed_delta) == (1000, -500)
    assert held_no.execution_authorised is False
    assert held_yes.execution_authorised is False


def test_parallel_atomic_allocation_cross_instances(tmp_path: Path) -> None:
    r = start(tmp_path)
    r2 = OfflineReductionReservations(tmp_path / "isolated_reduction_sim.sqlite")

    def attempt(pair: tuple[OfflineReductionReservations, str]) -> int:
        engine, intent = pair
        try:
            return reserve(engine, intent=intent, qty=1500).quantity
        except ReservationDenied:
            return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [(r, "x"), (r2, "y")]))
    assert sorted(results) == [0, 1500]


def test_replay_same_intent_idempotent_and_conflict_denied(tmp_path: Path) -> None:
    r = start(tmp_path)
    first = reserve(r)
    assert reserve(r) == first
    with pytest.raises(ReservationDenied, match="conflicting_logical_intent_replay"):
        reserve(r, qty=999)


def test_timeout_cancel_restart_never_release_slice(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r, intent="a", qty=2000)
    r.observe(intent_id="a", event_id="timeout", kind="TIMEOUT")
    r.observe(intent_id="a", event_id="cancel", kind="CANCEL_REQUESTED")
    restarted = OfflineReductionReservations(tmp_path / "isolated_reduction_sim.sqlite")
    with pytest.raises(ReservationDenied, match="inventory_slice_already_reserved"):
        reserve(restarted, intent="b", qty=1000)
    with pytest.raises(ReservationDenied, match="unresolved_venue_order_or_cancel"):
        restarted.reconcile(
            prior_epoch="signed-account-01", new_epoch="signed-account-02",
            evidence=ev(), cap=100000, age=5.0,
        )


def test_partial_fill_does_not_recycle_unconfirmed_remainder(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r, qty=2000)
    fill = r.observe(
        intent_id="i1", event_id="fillone", kind="CUMULATIVE_FILL", cumulative_filled=500,
    )
    assert fill.cumulative_filled == 500
    with pytest.raises(ReservationDenied, match="inventory_slice_already_reserved"):
        reserve(r, intent="i2", qty=501)
    r.observe(intent_id="i1", event_id="cancel-req", kind="CANCEL_REQUESTED")
    with pytest.raises(ReservationDenied):
        r.reconcile(
            prior_epoch="signed-account-01", new_epoch="signed-account-02",
            evidence=ev({-1: -2000, 1: 97500}), cap=100000, age=5.0,
        )


def test_terminal_exact_signed_reconciliation_and_new_epoch(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r, qty=2000)
    r.observe(
        intent_id="i1", event_id="partial", kind="CUMULATIVE_FILL", cumulative_filled=500,
    )
    r.observe(
        intent_id="i1", event_id="authoritative-terminal",
        kind="TERMINAL", cumulative_filled=500,
    )
    with pytest.raises(
        ReservationDenied, match="authoritative_signed_fill_reconciliation_mismatch"
    ):
        r.reconcile(
            prior_epoch="signed-account-01", new_epoch="signed-account-02",
            evidence=ev(), cap=100000, age=5.0,
        )
    r.reconcile(
        prior_epoch="signed-account-01", new_epoch="signed-account-02",
        evidence=ev({-1: -2000, 1: 97500}), cap=100000, age=5.0,
    )
    with pytest.raises(ReservationDenied, match="stale_or_changed_ledger_epoch"):
        reserve(r, intent="old-again")
    answer = r.reserve(
        epoch="signed-account-02", evidence=ev({-1: -2000, 1: 97500}),
        intent_id="new", exchange_id="-1", outcome_side="NO", action="SELL",
        quantity=1000, cap=100000, age=5.0,
    )
    assert answer.signed_delta == 1000


def test_conflicting_event_and_reversed_fill_fail_closed(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r)
    r.observe(
        intent_id="i1", event_id="f", kind="CUMULATIVE_FILL", cumulative_filled=800,
    )
    assert r.observe(
        intent_id="i1", event_id="f", kind="CUMULATIVE_FILL", cumulative_filled=800,
    ).cumulative_filled == 800
    with pytest.raises(ReservationDenied, match="conflicting_duplicate_event"):
        r.observe(
            intent_id="i1", event_id="f", kind="CUMULATIVE_FILL", cumulative_filled=900,
        )
    with pytest.raises(ReservationDenied, match="invalid_cumulative_fill"):
        r.observe(
            intent_id="i1", event_id="reverse",
            kind="CUMULATIVE_FILL", cumulative_filled=300,
        )
    with pytest.raises(ReservationDenied, match="invalid_cumulative_fill"):
        r.observe(
            intent_id="i1", event_id="too-many", kind="TERMINAL", cumulative_filled=1001,
        )


@pytest.mark.parametrize(
    "alter",
    [
        {"signed_positions_verified": False},
        {"account_trusted": False},
        {"reconciliation_complete": False},
        {"account_age_seconds": 99.0},
        {"uncertain_order_exposure": 1},
        {"reservation_exposure": 1},
        {"open_order_exposure": 1},
        {"venue_netting_verified": False},
        {"account_net": 100000},
    ],
)
def test_invalid_account_proofs_fail_before_bootstrap(
    tmp_path: Path, alter: dict[str, object]
) -> None:
    data = replace(ev(), **alter)
    with pytest.raises(ReservationDenied):
        start(tmp_path, data)


def test_changed_ledger_same_epoch_denied(tmp_path: Path) -> None:
    r = start(tmp_path)
    with pytest.raises(ReservationDenied, match="stale_or_changed_ledger_epoch"):
        reserve(r, data=ev({-1: -2000, 1: 97500}))


def test_signed_provenance_required_per_intent(tmp_path: Path) -> None:
    r = start(tmp_path)
    with pytest.raises(ReservationDenied, match="signed_position_provenance_missing"):
        reserve(r, data=replace(ev(), signed_positions_verified=False))


def test_restarted_engine_cannot_resync_from_open_orders(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r)
    r.observe(intent_id="i1", event_id="final", kind="TERMINAL", cumulative_filled=0)
    with pytest.raises(ReservationDenied, match="unresolved_order_or_reservation"):
        r.reconcile(
            prior_epoch="signed-account-01", new_epoch="signed-account-02",
            evidence=replace(ev(), uncertain_order_exposure=1), cap=100000, age=5.0,
        )
    with pytest.raises(ReservationDenied, match="already_seeded_no_implicit_reset"):
        r.seed(epoch="signed-account-03", evidence=ev(), cap=100000, age=5.0)


def test_old_intent_and_account_epoch_cannot_be_reused(tmp_path: Path) -> None:
    r = start(tmp_path)
    reserve(r, intent="original", qty=500)
    r.observe(
        intent_id="original", event_id="terminal-a", kind="TERMINAL", cumulative_filled=0
    )
    r.reconcile(
        prior_epoch="signed-account-01", new_epoch="signed-account-02",
        evidence=ev(), cap=100000, age=5.0,
    )
    with pytest.raises(ReservationDenied, match="logical_intent_id_reused_after_epoch"):
        r.reserve(
            epoch="signed-account-02", evidence=ev(), intent_id="original",
            exchange_id="-1", outcome_side="YES", action="BUY", quantity=500,
            cap=100000, age=5.0,
        )
    r.reserve(
        epoch="signed-account-02", evidence=ev(), intent_id="second",
        exchange_id="-1", outcome_side="YES", action="BUY", quantity=500,
        cap=100000, age=5.0,
    )
    r.observe(intent_id="second", event_id="terminal-b", kind="TERMINAL")
    with pytest.raises(ReservationDenied, match="account_epoch_reuse_denied"):
        r.reconcile(
            prior_epoch="signed-account-02", new_epoch="signed-account-01",
            evidence=ev(), cap=100000, age=5.0,
        )
