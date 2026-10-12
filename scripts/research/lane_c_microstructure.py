"""Run LANE C economic research offline against #220 paired PAPER JSONL.

No network connections, credentials, order clients or LIVE execution capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from predictions_cup.replay.mapping_005i import load_accepted_005i_pairs
from predictions_cup.replay.microstructure_c import evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description="Lane C offline PAPER-only research")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cash-free", type=Decimal, required=True,
                        help="Authoritatively verified FREE cash, not total equity")
    parser.add_argument("--gross-free", type=Decimal, required=True,
                        help="Authoritatively verified residual gross-share allowance")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be separate")
    if args.cash_free < 0 or args.gross_free < 0:
        parser.error("free resources must be nonnegative")
    payload = args.input.read_bytes()
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines()
            if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        parser.error("each input line must be one JSON object")
    accepted = {p.sig_exchange_id: p for p in load_accepted_005i_pairs(args.repo_root)}
    # Caller-supplied booleans NEVER self-attest a mapping: canonical registry wins.
    for row in rows:
        pair = accepted.get(str(row.get("sig_exchange_id", "")))
        if (pair is None or row.get("pm_token_id") != pair.pm_yes
                or row.get("mapping_direction") != pair.mapping_direction):
            raise ValueError("non-EXACT / wrong-token / wrong-direction mapping")
        row["exact_mapping_verified"] = True
        if row.get("data_origin") != "LANE_B_PAIRED_PROSPECTIVE_PAPER":
            raise ValueError("not independently sourced #220 PAPER tape")
        # Depth/health integrity still requires independent #220 reviewer certification.
        if row.get("paired_depth_trusted") is not True:
            raise ValueError("missing independently trusted paired full depth")
    output = evaluate(rows, cash_free=args.cash_free, gross_free=args.gross_free)
    output["input_sha256"] = hashlib.sha256(payload).hexdigest()
    output["registry_source"] = "canonical_MAPPED_001_accepted_EXACT_only"
    output["input_source"] = "LANE_B_PAIRED_PROSPECTIVE_PAPER"
    output["caveat"] = (
        "Self-declared PAPER provenance and observed-order markers are not independent "
        "audit proof. #207 must verify original tape, ACKs, fill fees and settlement."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
