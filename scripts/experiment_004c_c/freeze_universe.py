from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "data/mappings/sig_polymarket_2026.json"
ACCEPTANCE = ROOT / "data/mappings/sig_polymarket_2026_acceptance.json"
OUT = ROOT / "data/experiments/experiment_004c/crossvenue"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def joined_hash(values: list[str]) -> str:
    return sha256_bytes(("\n".join(sorted(values)) + "\n").encode())


def main() -> None:
    document = json.loads(MAPPING.read_text())
    records = document["records"]
    exact = [r for r in records if r["mapping_class"] == "EXACT" and r["status"] == "VERIFIED"]
    derived = [r for r in records if r["mapping_class"] == "DERIVED" and r["status"] == "VERIFIED"]

    if len(exact) != 140 or len(derived) != 87:
        raise SystemExit(
            f"accepted mapping universe changed: EXACT={len(exact)} DERIVED={len(derived)}"
        )
    if any(r["mapping_direction"] != "SAME" for r in exact):
        raise SystemExit("primary EXACT universe must be SAME-direction only")

    expected_note = (
        "Party-win probability is represented by the union/sum of all mutually "
        "exclusive Yes margin buckets for that party."
    )
    if any((r.get("resolution_notes") or "").strip() != expected_note for r in derived):
        raise SystemExit("DERIVED universe contains an unreviewed transformation")

    OUT.mkdir(parents=True, exist_ok=True)
    exact_path = OUT / "frozen_exact_universe.csv"
    with exact_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sig_exchange_id", "sig_market_id", "polymarket_market_id", "polymarket_token_id"]
        )
        for r in sorted(exact, key=lambda x: int(x["sig_exchange_id"])):
            p = r["direct_polymarket"]
            writer.writerow(
                [r["sig_exchange_id"], r["sig_market_id"], p["market_id"], p["mapped_token_id"]]
            )

    derived_path = OUT / "frozen_derived_universe.csv"
    with derived_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sig_exchange_id",
                "sig_market_id",
                "component_count",
                "component_market_ids_sha256",
                "component_token_ids_sha256",
            ]
        )
        for r in sorted(derived, key=lambda x: int(x["sig_exchange_id"])):
            components = r["polymarket_components"]
            writer.writerow(
                [
                    r["sig_exchange_id"],
                    r["sig_market_id"],
                    len(components),
                    joined_hash([c["market_id"] for c in components]),
                    joined_hash([c["mapped_token_id"] for c in components]),
                ]
            )

    capture_tokens = sorted(
        {r["direct_polymarket"]["mapped_token_id"] for r in exact}
        | {c["mapped_token_id"] for r in derived for c in r["polymarket_components"]}
    )
    manifest = {
        "schema_version": 1,
        "mapping_source": str(MAPPING.relative_to(ROOT)),
        "mapping_sha256": sha256(MAPPING),
        "acceptance_source": str(ACCEPTANCE.relative_to(ROOT)),
        "acceptance_sha256": sha256(ACCEPTANCE),
        "exact_selector": "mapping_class=EXACT,status=VERIFIED,mapping_direction=SAME",
        "derived_selector": "mapping_class=DERIVED,status=VERIFIED,reviewed_union_only",
        "derived_transformation": "SUM_MUTUALLY_EXCLUSIVE_YES_BUCKETS",
        "exact_count": len(exact),
        "derived_count": len(derived),
        "capture_token_count": len(capture_tokens),
        "capture_token_ids_sha256": joined_hash(capture_tokens),
        "exact_universe_sha256": sha256(exact_path),
        "derived_universe_sha256": sha256(derived_path),
    }
    (OUT / "universe_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
