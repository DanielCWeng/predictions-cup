"""Resolve a single unresolved MAKE placement by idempotent replay (ops tool).

SIG replays the stored response for a reused idempotency key with the same
payload, so this learns the order ids without placing new orders.
"""
import asyncio, sys
from predictions_cup.config import AppSettings
from predictions_cup.execution.journal import ExecutionJournal
from predictions_cup.execution.interlocks import assert_live_recovery_interlocks
from predictions_cup.execution.live import SigLiveSink
from predictions_cup.execution.reservations import ExecutionReservationBook
from predictions_cup.sig.rest_governor import SigRestGovernor
from predictions_cup.sig.trading_client import SigTradingClient

OP = sys.argv[1]

async def main() -> None:
    s = AppSettings()
    gov = SigRestGovernor(rate_per_second=s.sig_rest_governor_rate_per_second,
                          max_shared_cooldown_seconds=s.sig_rest_shared_cooldown_max_seconds)
    journal = ExecutionJournal(s.execution_journal_path)
    env = next(e for e in journal.unresolved() if e.logical_operation_id == OP)
    print("envelope", env.logical_operation_id, env.operation_kind, env.lifecycle_state, env.idempotency_key[:12])
    permit = assert_live_recovery_interlocks(s, explicit_live_invocation=True, account_trusted=True)
    sink = SigLiveSink(client=SigTradingClient(s, governor=gov), journal=journal, permit=permit,
                       reservations=ExecutionReservationBook())
    ev = await sink.dispatch_recovery(env)
    print("event", ev)
    print("still unresolved", [e.logical_operation_id for e in journal.unresolved()])
    journal.close()

asyncio.run(main())
