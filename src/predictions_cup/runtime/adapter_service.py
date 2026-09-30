"""Generic FULLSTACK-001 wrapper for replaceable observer/learner processes."""
from __future__ import annotations

import argparse
import asyncio
import shlex
import signal
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from predictions_cup.runtime.fullstack import (
    LEARN,
    OBSERVE,
    CapabilityMode,
    GateState,
    parse_env_file,
    runtime_env_path,
    write_json_atomic,
)

ROLE_KEYS = {
    "live-learn": "PREDICTIONS_CUP_LIVE_LEARN_COMMAND",
    "observe": "PREDICTIONS_CUP_OBSERVE_COMMAND",
}
ROLE_UNITS = {
    "live-learn": LEARN,
    "observe": OBSERVE,
}


def _flag(values: dict[str, str], key: str) -> bool:
    return values.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _status_path(values: dict[str, str], role: str) -> Path:
    root = Path(values.get("PREDICTIONS_CUP_FULLSTACK_STATUS_DIR", "data/runtime/status"))
    return root / f"{role}.json"


def _publish(
    path: Path,
    *,
    role: str,
    state: GateState,
    provider_mode: str,
    reason: str,
    child_pid: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": "fullstack-001-adapter-v1",
        "role": role,
        "state": state.value,
        "provider_mode": provider_mode,
        "capability_mode": CapabilityMode.EXTERNAL_SERVICE.value,
        "owner_services": [ROLE_UNITS[role]],
        "reason": reason,
        "observed_at": datetime.now(UTC).isoformat(),
        "child_pid": child_pid,
    }
    write_json_atomic(path, payload)


async def _fixture(role: str, path: Path, stop: asyncio.Event) -> int:
    while not stop.is_set():
        _publish(
            path,
            role=role,
            state=GateState.DEGRADED,
            provider_mode="fixture",
            reason="deterministic FULLSTACK fixture; replace with merged real provider",
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1.0)
    return 0


async def _real(
    role: str,
    path: Path,
    stop: asyncio.Event,
    command: list[str],
) -> int:
    child = await asyncio.create_subprocess_exec(*command)
    _publish(
        path,
        role=role,
        state=GateState.DEGRADED,
        provider_mode="real",
        reason="provider started; provider may overwrite this health file when ready",
        child_pid=child.pid,
    )
    waiter = asyncio.create_task(child.wait())
    stop_waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait(
        {waiter, stop_waiter},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if stop_waiter in done and child.returncode is None:
        child.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(child.wait(), timeout=30.0)
        except TimeoutError:
            _publish(
                path,
                role=role,
                state=GateState.BLOCKED,
                provider_mode="real",
                reason="provider ignored SIGTERM; refusing forced kill",
                child_pid=child.pid,
            )
            return 2
    waiter.cancel()
    stop_waiter.cancel()
    await asyncio.gather(waiter, stop_waiter, return_exceptions=True)
    code = child.returncode or 0
    _publish(
        path,
        role=role,
        state=GateState.PASS if stop.is_set() and code == 0 else GateState.BLOCKED,
        provider_mode="real",
        reason="graceful shutdown" if stop.is_set() and code == 0 else f"provider exited rc={code}",
        child_pid=child.pid,
    )
    return code


async def run(role: str) -> int:
    values = parse_env_file(runtime_env_path())
    path = _status_path(values, role)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    key = ROLE_KEYS[role]
    raw = values.get(key, "").strip()
    if raw:
        command = shlex.split(raw)
        if not command:
            raise ValueError(f"{key} did not produce an executable command")
        return await _real(role, path, stop, command)
    if _flag(values, "PREDICTIONS_CUP_FULLSTACK_ALLOW_FIXTURES"):
        return await _fixture(role, path, stop)
    _publish(
        path,
        role=role,
        state=GateState.BLOCKED,
        provider_mode="missing",
        reason=f"{key} is not configured and fixtures are disabled",
    )
    return 78


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FULLSTACK replaceable capability wrapper")
    parser.add_argument("role", choices=tuple(ROLE_KEYS))
    parser.add_argument("--runtime-env-only", action="store_true")
    args = parser.parse_args(argv)
    del args.runtime_env_only
    return asyncio.run(run(args.role))


if __name__ == "__main__":
    raise SystemExit(main())
