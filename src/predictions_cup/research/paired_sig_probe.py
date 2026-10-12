"""Bounded, opt-in SIG REST full-depth research probe: never places an order.

The critical admission invariant is host-wide participation: the existing LIVE
maker AND standalone collector must already use the exact same flocked bucket.
A second uncoordinated governor is NOT an account-wide limit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import subprocess
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from predictions_cup.config import AppSettings, load_settings
from predictions_cup.research.paired_depth import InvalidInput, load_specs
from predictions_cup.sig.client import RetryPolicy
from predictions_cup.sig.governed_client import GovernedSigRestClient
from predictions_cup.sig.rest_governor import RestPriority

_PRODUCTION_SERVICES = (
    "predictions-cup-maker-live.service",
    "predictions-cup-sig-capture.service",
)


@dataclass(frozen=True)
class Admission:
    permitted: bool
    reasons: tuple[str, ...]
    shared_budget_path: str | None
    proposed_requests_per_minute: float


def _service_environment(unit: str) -> tuple[bool, dict[str, str]]:
    """Read only a whitelisted setting, never expose bearer credentials."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "-p", "ActiveState"],
            check=True, capture_output=True, text=True, timeout=3,
        )
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        pid = int(state.get("MainPID", "0"))
        if state.get("ActiveState") != "active" or pid <= 0:
            return False, {}
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        prefix = b"PREDICTIONS_CUP_SIG_REST_ACCOUNT_BUDGET_PATH="
        values = [item[len(prefix):] for item in raw.split(b"\0") if item.startswith(prefix)]
        budget = values[-1].decode() if values else ""
        return True, {"shared_path": budget}
    except (ValueError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        return False, {}


def inspect_admission(
    settings: AppSettings,
    *,
    service_status: dict[str, tuple[bool, dict[str, str]]],
    interval_s: float,
    universe_size: int,
    authorisation: dict[str, Any] | None,
    now: datetime,
) -> Admission:
    reasons: list[str] = []
    path = settings.sig_rest_account_budget_path
    if path is None or not path.is_absolute():
        reasons.append("SHARED_BUDGET_NOT_CONFIGURED")
    if interval_s < 20 or universe_size < 1 or universe_size > 6:
        reasons.append("UNBOUNDED_READ_RATE_OR_UNIVERSE")
    rate = universe_size * 60 / interval_s if interval_s > 0 else float("inf")
    for name in _PRODUCTION_SERVICES:
        running, fields = service_status.get(name, (False, {}))
        if not running:
            reasons.append(f"HOST_SERVICE_NOT_VERIFIABLE:{name}")
        if path is None or not fields.get("shared_path"):
            reasons.append(f"HOST_SHARED_BUDGET_MISSING:{name}")
        elif Path(fields["shared_path"]).resolve() != path.resolve():
            reasons.append(f"SHARED_BUDGET_MISMATCH:{name}")
    if authorisation is None:
        reasons.append("OPERATOR_AUTHORISATION_MISSING")
    else:
        try:
            reviewed_at = datetime.fromisoformat(
                str(authorisation["measured_at"]).replace("Z", "+00:00")
            )
            expires_at = datetime.fromisoformat(
                str(authorisation["expires_at"]).replace("Z", "+00:00")
            )
            if (reviewed_at.tzinfo is None or expires_at.tzinfo is None or reviewed_at > now
                    or now - reviewed_at > timedelta(minutes=10) or expires_at <= now):
                reasons.append("AUTHORISATION_STALE")
            if authorisation["operator_approved"] is not True:
                reasons.append("OPERATOR_DENIED")
            if str(authorisation["shared_budget_path"]) != str(path):
                reasons.append("AUTHORISED_BUDGET_PATH_MISMATCH")
            # Assessed *after* reserving maker/capture high-priority traffic.
            spare = float(authorisation["verified_free_reads_per_minute"])
            if not 0 < rate <= min(spare * 0.5, 6.0):
                reasons.append("UNVERIFIED_RESIDUAL_CAPACITY")
            if not (
                isinstance(authorisation["approval_ticket"], str)
                and authorisation["approval_ticket"]
            ):
                reasons.append("APPROVAL_TICKET_MISSING")
        except (KeyError, TypeError, ValueError):
            reasons.append("INVALID_AUTHORISATION")
    return Admission(not reasons, tuple(reasons), str(path) if path else None, rate)


def _safe_snapshot(book: Any, *, exchange: str, market: str, started: datetime,
                   received: datetime, run_id: str) -> dict[str, Any]:
    """REST orderbook has NO native exchange-event timestamp or revision."""
    correct_identity = str(book.exchange_id) == exchange and str(book.market_id) == market
    return {
        "venue": "SIG", "instrument_id": exchange, "market_id": str(book.market_id),
        "received_at": received.isoformat(), "request_started_at": started.isoformat(),
        "exchange_at": None, "source_timestamp_status": "NOT_IN_SIG_REST_RESPONSE",
        "bids": [{"price": str(x.price), "size": str(x.quantity)} for x in book.bids],
        "asks": [{"price": str(x.price), "size": str(x.quantity)} for x in book.asks],
        "complete": bool(correct_identity and int(book.depth) > 0),
        "connected": True, "continuity": False, "trusted": False,
        "provenance_status": "AUTHORITATIVE_REST_DEPTH_NO_SOURCE_REVISION",
        "source": "SIG_GOVERNED_READ_ONLY_REST", "connection_epoch": run_id,
        "revision": None, "previous_revision": None,
        "elapsed_request_ms": (received - started).total_seconds() * 1000,
        "reason": None if correct_identity else "WRONG_SIG_EXCHANGE_OR_MARKET",
    }


async def _probe(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise InvalidInput("output must be exclusive NEW research path")
    settings = load_settings()
    specs = load_specs(args.mapping, args.acceptance, args.exchange_ids.split(","))
    services = {x: _service_environment(x) for x in _PRODUCTION_SERVICES}
    auth = json.loads(args.authorisation.read_text()) if args.authorisation else None
    admission = inspect_admission(
        settings, service_status=services, interval_s=args.interval,
        universe_size=len(specs), authorisation=auth, now=datetime.now(UTC),
    )
    print(json.dumps({"decision": "ADMITTED" if admission.permitted else "BLOCKED",
                      "reasons": admission.reasons,
                      "proposed_requests_per_minute": admission.proposed_requests_per_minute},
                     sort_keys=True))
    if args.preflight_only or not admission.permitted:
        return 0 if args.preflight_only else 2
    # Never let the run extend beyond the operator's explicit approval.
    duration = min(args.duration, 3600)
    if duration <= 0:
        raise InvalidInput("invalid duration")
    if settings.sig_read_credential is None:
        raise InvalidInput("SIG read-only credential not configured")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for code in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(code, stop.set)
    run_id = uuid.uuid4().hex
    max_reads = min(int(duration * admission.proposed_requests_per_minute / 60) + len(specs), 180)
    count = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    start_mono = time.monotonic()
    until = datetime.fromisoformat(auth["expires_at"].replace("Z", "+00:00"))
    with args.output.open("x", encoding="utf-8", buffering=1) as out:
        out.write(json.dumps({"event": "START", "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(), "approval_ticket": auth["approval_ticket"],
            "mapping_sha256": next(iter(specs.values())).mapping_sha256,
            "research_only": True, "max_reads": max_reads}) + "\n")
        try:
            async with GovernedSigRestClient(
                settings, timeout_seconds=5.0, retry_policy=RetryPolicy(max_attempts=1),
            ) as client:
                while not stop.is_set() and time.monotonic() - start_mono < duration:
                    for s in specs.values():
                        if stop.is_set() or datetime.now(UTC) >= until or count >= max_reads:
                            stop.set()
                            break
                        started = datetime.now(UTC)
                        try:
                            async with client.priority(RestPriority.BACKGROUND):
                                book = await client.get_orderbook(s.exchange, depth=20)
                            row = _safe_snapshot(book, exchange=s.exchange, market=s.market,
                                                 started=started, received=datetime.now(UTC),
                                                 run_id=run_id)
                        except Exception as exc:
                            row = {
                                "event": "SIG_API_ERROR", "venue": "SIG",
                                "instrument_id": s.exchange,
                                   "received_at": datetime.now(UTC).isoformat(),
                                   "reason": type(exc).__name__, "trusted": False}
                            # Account 429 / transport problems should not trigger retries/spin.
                            stop.set()
                        count += 1
                        out.write(json.dumps(row, sort_keys=True) + "\n")
                        if stop.is_set():
                            break
                        with suppress(TimeoutError):
                            await asyncio.wait_for(
                                stop.wait(), timeout=args.interval / len(specs)
                            )
        finally:
            out.write(json.dumps({"event": "STOP", "run_id": run_id,
                                  "received_at": datetime.now(UTC).isoformat(),
                                  "reads_attempted": count, "clean_shutdown": True}) + "\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="SIG read-only depth research (DEFAULT DENY)")
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--authorisation", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mapping", type=Path, required=True)
    p.add_argument("--acceptance", type=Path, required=True)
    p.add_argument("--exchange-ids", required=True)
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--duration", type=float, default=1800.0)
    return asyncio.run(_probe(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
