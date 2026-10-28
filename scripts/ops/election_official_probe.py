"""One-shot, public, read-only election results probe.

No SIG credential, execution, probability generation or order submission.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from predictions_cup.analysis.election_official_texas import fetch_texas_senate


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect official Texas Senate count status")
    parser.parse_args()
    result = asyncio.run(fetch_texas_senate())
    print(json.dumps(result.as_document(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
