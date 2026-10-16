"""#84 P0: off-network durable OBSERVE admission counterexamples on frozen #218.

Only synthetic httpx.MockTransport, isolated SQLite and temporary files.
Original #268 strict tests are copied byte-identically in a separate file.
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import threading
from pathlib import Path
from typing import cast

import httpx
import pytest

from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.models import LifecycleState
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.observe import (
    BoundedObservationEmitter,
    CallbackObservationSink,
    ObservationKind,
)
from predictions_cup.observe.contracts import ObservationSink, VenueObservation
from predictions_cup.sig.errors import SigExecutionUncertainError
from test_build009_live_sink import _permit
from test_redteam_218_live_observation import _client, _make_plan, _success


@pytest.mark.parametrize(
    "failure", ["worker_exception", "blocked_after_enqueue", "queue_full", "closed"]
)
def test_84_no_first_wire_without_durable_observation_ack(
    tmp_path: Path, failure: str,
) -> None:
    """A boolean enqueue (or a drop) cannot authorise an unobservable wire."""
    plan = _make_plan("durable-prewire-" + failure)
    wires: list[bytes] = []
    disk_records: list[ObservationKind] = []
    worker_entered = threading.Event()
    allow_worker = threading.Event()

    def persist(obs: VenueObservation) -> None:
        worker_entered.set()
        if failure in {"blocked_after_enqueue", "queue_full"}:
            assert allow_worker.wait(timeout=30), "fixture worker was not released"
        if failure == "worker_exception":
            raise OSError("synthetic observation database disk failure")
        disk_records.append(obs.kind)

    observer = BoundedObservationEmitter(
        cast(ObservationSink, CallbackObservationSink(persist)),
        queue_max=1 if failure == "queue_full" else 100,
    )
    if failure == "closed":
        observer.close()
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        return _success()

    client = _client(httpx.MockTransport(respond))
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=observer,
        observation_process_instance_id="synthetic-durable-fault",
    )
    try:
        result = asyncio.run(sink.dispatch(plan))
        assert result.state is LifecycleState.OPEN
        stats = observer.health()
        assert wires == [plan.envelope.payload_json.encode()]
        if failure == "closed":
            assert stats.accepted == 0 and stats.dropped > 0
        else:
            assert stats.accepted > 0, "No accepted-in-memory emission"
        if failure in {"blocked_after_enqueue", "queue_full"}:
            assert worker_entered.wait(timeout=3)
            assert disk_records == [], "Deferred worker somehow persisted before release"
        if failure == "queue_full":
            assert stats.dropped > 0, "Must actually saturate queue before assessing wire"
        if failure == "worker_exception":
            observer.close()
            assert observer.health().sink_failures > 0
            assert disk_records == [], "Writer-failure fixture persisted a durable record"

        assert not wires, (
            "FIRST economic mock POST occurred with no durable observer admission: "
            + failure
        )
    finally:
        allow_worker.set()
        observer.close()
        asyncio.run(client.aclose())
        journal.close()


@pytest.mark.parametrize("first", ["503", "lost_ack"])
def test_84_per_http_wire_attempts_are_all_correlated_even_on_retry(
    tmp_path: Path, first: str,
) -> None:
    """Transport outcome must not hide first wire when a later attempt succeeds."""
    plan = _make_plan("durable-attempt-" + first)
    wires: list[bytes] = []
    stored: list[VenueObservation] = []

    def respond(req: httpx.Request) -> httpx.Response:
        wires.append(req.content)
        if len(wires) == 1:
            if first == "503":
                return httpx.Response(503, json={"error": {"code": "SERVER_ERROR"}})
            raise httpx.ReadTimeout("synthetic lost ACK", request=req)
        return _success()

    emitter = BoundedObservationEmitter(
        CallbackObservationSink(stored.append), queue_max=100
    )
    client = _client(httpx.MockTransport(respond), attempts=2)
    journal = ExecutionJournal(tmp_path / "retries.sqlite")
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=emitter,
        observation_process_instance_id="synthetic-attempt",
    )
    try:
        try:
            outcome = asyncio.run(sink.dispatch(plan))
        except SigExecutionUncertainError:
            assert journal.lifecycle_state(
                plan.envelope.logical_operation_id
            ) is LifecycleState.UNCERTAIN
            assert reservations.contains_operation(
                plan.envelope.logical_operation_id, plan.envelope.intent_ids
            )
        else:
            assert outcome.state is LifecycleState.OPEN
        emitter.close()
        assert len(wires) >= 1
        assert all(wire == plan.envelope.payload_json.encode() for wire in wires)
        dispatches = [o for o in stored if o.kind is ObservationKind.REQUEST_DISPATCHED]
        assert len(dispatches) == len(wires), (
            "Per-wire HTTP attempt disappeared from OBSERVE; " + first
        )
        expected_kind = (
            ObservationKind.SERVER_ERROR
            if first == "503"
            else ObservationKind.TRANSPORT_EXCEPTION
        )
        assert any(o.kind is expected_kind for o in stored), (
            "First 503/lost-ACK attempt outcome lost from OBSERVE: " + first
        )
    finally:
        emitter.close()
        asyncio.run(client.aclose())
        journal.close()


def _crash_after_mock_wire(
    db_path: str, wire_path: str, observed_path: str, accepted_path: str,
) -> None:
    """Child dies at fake HTTP boundary with unflushed in-memory OBSERVE queue."""
    never_flush = threading.Event()

    def deferred_writer(obs: VenueObservation) -> None:
        # Worker has NO opportunity to commit observation before hard process exit.
        if never_flush.wait(timeout=30):
            Path(observed_path).write_text(obs.kind.value)

    observer = BoundedObservationEmitter(
        CallbackObservationSink(deferred_writer), queue_max=32
    )
    plan = _make_plan("crash-after-possible-wire")
    journal = ExecutionJournal(Path(db_path))
    reservations = ExecutionReservationBook()
    reservations.reserve(plan.envelope.logical_operation_id, plan.intents)

    def respond(req: httpx.Request) -> httpx.Response:
        Path(accepted_path).write_text(str(observer.health().accepted))
        Path(wire_path).write_bytes(req.content)
        os._exit(0)

    client = _client(httpx.MockTransport(respond))
    sink = SigLiveSink(
        client=client, journal=journal, permit=_permit(),
        reservations=reservations, observation_emitter=observer,
        observation_process_instance_id="synthetic-crash-process",
    )
    asyncio.run(sink.dispatch(plan))
    raise AssertionError("Synthetic crash fake HTTP callback unexpectedly returned")


def test_84_mocked_crash_preserves_unresolved_journal_but_not_observe_delivery(
    tmp_path: Path,
) -> None:
    """Crash witness: durable journal identity != durable OBSERVE dispatch proof.

    PENDING stays unresolved, NOT a terminal fill. A process-local reservation
    does NOT constitute proven cross-restart global risk capacity.
    """
    db = tmp_path / "crash-journal.sqlite"
    wire = tmp_path / "synthetic-wire.bin"
    observed = tmp_path / "deferred-observation.txt"
    accepted = tmp_path / "queued-count.txt"
    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_crash_after_mock_wire,
        args=(str(db), str(wire), str(observed), str(accepted)),
    )
    proc.start()
    proc.join(timeout=25)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
    assert proc.exitcode == 0
    assert wire.exists(), "Fake transport did not reach the synthetic wire boundary"
    assert int(accepted.read_text()) > 0, "No queue accepted event pre-wire"
    assert not observed.exists(), "Deferred worker unexpectedly durably wrote observation"
    journal = ExecutionJournal(db)
    try:
        assert journal.lifecycle_state("crash-after-possible-wire") in {
            LifecycleState.PENDING, LifecycleState.UNCERTAIN,
        }
        assert any(
            e.event_type == "SUBMISSION" for e in journal.events(
                "crash-after-possible-wire"
            )
        )
    finally:
        journal.close()
