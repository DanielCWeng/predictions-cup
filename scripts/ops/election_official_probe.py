"""Public Texas election results reader: one-shot or bounded polling.

No SIG key, no probability inference, no trading, and no file storage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from predictions_cup.analysis.election_official_texas import fetch_texas_senate

STOP_AT = datetime(2026, 11, 4, 17, 0, tzinfo=UTC)


async def run(*, loop: bool, poll_seconds: int) -> None:
    last_sha: str | None = None
    while True:
        try:
            observed = await fetch_texas_senate()
            # Emit only genuine document changes, not endless identical pages.
            if last_sha != observed.body_sha256:
                print(json.dumps(observed.as_document(), sort_keys=True), flush=True)
                last_sha = observed.body_sha256
        except Exception as exc:
            # Print the exception type only; fail safely without URL or secrets.
            print(json.dumps({
                "mode": "OFFICIAL_READONLY_SOURCE",
                "status": "ABSTAIN_SOURCE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "execution_authority": False,
            }), flush=True)
        if not loop or datetime.now(UTC) >= STOP_AT:
            return
        await asyncio.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect official Texas Senate vote count")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    if not 30 <= args.poll_seconds <= 3600:
        parser.error("poll-seconds must be between 30 and 3600")
    asyncio.run(run(loop=args.loop, poll_seconds=args.poll_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
