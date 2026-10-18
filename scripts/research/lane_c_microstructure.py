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
from predictions_cup.replay.microstructure_c_bridge import bridge_lane_b


def main() -> None:
    parser = argparse.ArgumentParser(description="Lane C offline PAPER-only research")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-format", choices=("lane-c", "lane-b-paper"),
                        default="lane-b-paper")
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
    source_report = None
    if args.input_format == "lane-b-paper":
        rows, source_report = bridge_lane_b(rows)
    else:
        # The manually assembled Lane-C mode is for explicitly reviewed,
        # independent PAPER input. Self-declared trusted flags are NOT proof.
        for row in rows:
            if row.get("data_origin") != "LANE_B_PAIRED_PROSPECTIVE_PAPER":
                raise ValueError("not independently sourced #220 PAPER tape")
    for row in rows:
        pair = accepted.get(str(row.get("sig_exchange_id", "")))
        mapping_ok = (
            pair is not None and row.get("pm_token_id") == pair.pm_yes
            and row.get("mapping_direction") == pair.mapping_direction
        )
        row["exact_mapping_verified"] = mapping_ok
        if args.input_format == "lane-c" and not mapping_ok:
            raise ValueError("non-EXACT / wrong-token / wrong-direction mapping")
        # Never upgrade Lane B PAPER source booleans to independently audited
        # depth trust; this mode can only produce rejected opportunity ledgers.
        if args.input_format == "lane-b-paper":
            row["paired_depth_trusted"] = False
    output = evaluate(rows, cash_free=args.cash_free, gross_free=args.gross_free)
    if source_report is not None:
        output["lane_b_input_audit"] = source_report
    output["input_sha256"] = hashlib.sha256(payload).hexdigest()
    output["registry_source"] = "canonical_MAPPED_001_accepted_EXACT_only"
    output["input_source"] = args.input_format
    output["caveat"] = (
        "Self-declared PAPER provenance and observed-order markers are not independent "
        "audit proof. #207 must verify original tape, ACKs, fill fees and settlement."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("refusing to overwrite prior research scorecard")
    with args.output.open("x", encoding="utf-8") as out:
        out.write(json.dumps(output, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
