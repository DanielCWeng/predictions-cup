"""Isolated public PM websocket transcript, read-only and bounded, NO REST seed.

Every reconnect starts a new epoch. Full WS book frames must arrive before
any delta is considered initialized. Never attach to or modify LIVE services.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from predictions_cup.config import load_settings
from predictions_cup.research.paired_depth import InvalidInput, load_specs

MAX_BYTES = 32 * 1024 * 1024
MAX_MINUTES = 60


class Transcript:
    def __init__(self, dest: Path) -> None:
        if dest.exists():
            raise InvalidInput("refuse to overwrite prior PAPER or capture tape")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._stream = dest.open("x", encoding="utf-8", buffering=1)
        self._bytes = 0
        self.records = 0
        self.sha256 = hashlib.sha256()

    def emit(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if self._bytes + len(data) > MAX_BYTES:
            raise InvalidInput("research output disk safety cap reached; stop and reject epoch")
        self._stream.write(data.decode())
        self._bytes += len(data)
        self.sha256.update(data)
        self.records += 1

    def close(self) -> None:
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()


async def _run(args: argparse.Namespace) -> int:
    specs = load_specs(args.mapping, args.acceptance, args.exchange_ids.split(","))
    if len(specs) > 6:
        raise InvalidInput("max six EXACT tokens in isolated PM proof")
    if not 0 < args.duration <= MAX_MINUTES * 60:
        raise InvalidInput("invalid 1-3600s duration")
    if args.preflight_only:
        print(json.dumps({"mode": "PREFLIGHT_ONLY", "accepted": len(specs),
                          "max_output_bytes": MAX_BYTES, "network_requests": 0}))
        return 0
    if not args.enable_network:
        raise InvalidInput("explicit --enable-network required")
    settings = load_settings()
    tokens = sorted({s.token for s in specs.values()})
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    start = time.monotonic()
    trace = Transcript(args.output)
    epoch = 0
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)
        ) as session:
            while not stop.is_set() and time.monotonic() - start < args.duration:
                epoch += 1
                at = datetime.now(UTC).isoformat()
                try:
                    async with session.ws_connect(str(settings.polymarket_ws_url),
                                                  heartbeat=15) as ws:
                        await ws.send_json({"assets_ids": tokens, "type": "market"})
                        trace.emit({"event": "BOUNDARY", "status": "CONNECTED",
                                    "received_at": datetime.now(UTC).isoformat(),
                                    "epoch": epoch})
                        while not stop.is_set():
                            remaining = args.duration - (time.monotonic() - start)
                            if remaining <= 0:
                                break
                            try:
                                msg = await asyncio.wait_for(
                                    ws.receive(), timeout=min(remaining, 10.0))
                            except TimeoutError:
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                if msg.data in ("PING", "PONG"):
                                    continue
                                received = datetime.now(UTC).isoformat()
                                try:
                                    decoded = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    trace.emit({"event": "BOUNDARY", "status": "SOURCE_GAP",
                                                "received_at": received,
                                                "reason": "INVALID_JSON", "epoch": epoch})
                                    break
                                events = decoded if isinstance(decoded, list) else [decoded]
                                for event in events:
                                    if not isinstance(event, dict):
                                        trace.emit({"event": "BOUNDARY",
                                                    "status": "SOURCE_GAP",
                                                    "received_at": received,
                                                    "reason": "INVALID_EVENT", "epoch": epoch})
                                        stop.set()
                                        break
                                    trace.emit({"event": "MESSAGE", "received_at": received,
                                                "payload": event, "epoch": epoch})
                                if stop.is_set():
                                    break
                            else:
                                break
                except (aiohttp.ClientError, OSError, TimeoutError) as exc:
                    trace.emit({"event": "BOUNDARY", "status": "API_ERROR",
                                "received_at": datetime.now(UTC).isoformat(),
                                "reason": type(exc).__name__, "epoch": epoch})
                finally:
                    trace.emit({"event": "BOUNDARY", "status": "DISCONNECTED",
                                "received_at": datetime.now(UTC).isoformat(),
                                "epoch": epoch})
                # Reconnect cannot retain a prior epoch's depth/continuity.
                if not stop.is_set() and time.monotonic() - start < args.duration:
                    await asyncio.sleep(1.0)
    except InvalidInput:
        # A bounded audit reaching its space limit stops, never overwrites
        # the existing source tape or synthesises missing rows.
        return 2
    finally:
        trace.close()
    print(json.dumps({"status": "PAPER_RAW_TRANSCRIPT_ONLY",
                      "records": trace.records, "bytes": trace._bytes,
                      "tape_sha256": trace.sha256.hexdigest(),
                      "actual_order_placements": 0}, sort_keys=True))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Public PM read-only raw WS proof (explicit opt-in)")
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--enable-network", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mapping", type=Path, required=True)
    p.add_argument("--acceptance", type=Path, required=True)
    p.add_argument("--exchange-ids", required=True)
    p.add_argument("--duration", type=float, default=30.0)
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
