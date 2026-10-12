"""Read-only CLI for LANE-3 005I economics. Requires precomputed causal minute tape.

Never reads the 005I sealed HOLDOUT as an economic tuning input.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from predictions_cup.replay.economic_005i import (
    EconomicConfig,
    ExactPair,
    MinuteFeature,
    evaluate,
)
from predictions_cup.replay.features_005i import causal_minute_features
from predictions_cup.replay.loaders import (
    CaptureSelection,
    load_polymarket_capture,
    load_sig_capture,
)


def parse_time(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamps require timezone")
    return dt


def run() -> int:
    parser = argparse.ArgumentParser(description="Offline 005I net-economics research ONLY")
    parser.add_argument("--sig-db", type=Path, required=True)
    parser.add_argument("--pm-capture", type=Path, required=True)
    parser.add_argument(
        "--features", type=Path,
        help="Optional externally audited causal minute tape; default derives from capture",
    )
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument(
        "--freeze", type=Path,
        default=Path("data/experiments/experiment_005i/OFI_TRAIN_MODEL_FREEZE.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("dev", "prospective"), required=True)
    parser.add_argument("--start", required=True, type=parse_time)
    parser.add_argument("--end", required=True, type=parse_time)
    parser.add_argument("--fee-bps-per-side", type=Decimal, required=True)
    parser.add_argument("--latency-ms", type=int, required=True)
    parser.add_argument("--min-edge", type=Decimal, required=True)
    parser.add_argument("--quantity", type=Decimal, required=True)
    parser.add_argument("--capital-limit", type=Decimal, required=True)
    parser.add_argument("--depth-haircut", type=Decimal, default=Decimal("0.5"))
    args = parser.parse_args()

    if args.end <= args.start:
        parser.error("end must follow start")
    if args.mode == "dev" and args.end.date().isoformat() > "2026-09-21":
        parser.error("DEV mode cannot touch September 21 onward")
    if args.mode == "prospective" and args.start.date().isoformat() < "2026-10-12":
        parser.error("prospective PAPER must start no earlier than October 12")
    pairs = [ExactPair(**item) for item in json.loads(args.pairs.read_text())]
    selection = CaptureSelection(
        start_at=args.start - timedelta(minutes=8),
        end_at=args.end,
        sig_exchange_ids=tuple(sorted({x for p in pairs for x in (p.sig_yes, p.sig_no)})),
        polymarket_token_ids=tuple(sorted({p.pm_yes for p in pairs})),
    )
    events = (
        *load_sig_capture(args.sig_db, selection=selection),
        *load_polymarket_capture(args.pm_capture, selection=selection),
    )
    if args.features is None:
        rows = list(causal_minute_features(
            events, pm_token_ids={p.pm_yes for p in pairs},
        ))
    else:
        rows = []
        with args.features.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                item = json.loads(line)
                item["minute_end"] = parse_time(item["minute_end"])
                item["available_at"] = parse_time(item["available_at"])
                rows.append(MinuteFeature(**item))
    rows = [row for row in rows if args.start <= row.available_at < args.end]
    if any("2026-09-21" <= row.minute_end.date().isoformat() <= "2026-09-28"
           for row in rows):
        raise ValueError("sealed HOLDOUT minute refused")
    result = evaluate(
        events, rows, pairs,
        EconomicConfig(
            fee_bps_per_side=args.fee_bps_per_side, latency_ms=args.latency_ms,
            min_edge=args.min_edge, quantity=args.quantity,
            capital_limit=args.capital_limit, depth_haircut=args.depth_haircut,
        ),
        json.loads(args.freeze.read_text()),
    )
    result["study_mode"] = args.mode
    result["input_window"] = {"start": args.start.isoformat(), "end": args.end.isoformat()}
    result["input_provenance"] = {
        "sig_db": str(args.sig_db), "pm_capture": str(args.pm_capture),
        "features": "FROM_REPLAY_EVENTS" if args.features is None else str(args.features),
        "pairs": str(args.pairs),
        "freeze": str(args.freeze),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({
        "status": result["status"], "promotion": result["promotion"],
        "input_counts": result.get("input_counts", {}),
        "output": str(args.output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
