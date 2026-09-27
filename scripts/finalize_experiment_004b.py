"""Ingest a completed 004B Kaggle run and build the compact frozen evidence package."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from predictions_cup.learning.election_market_structure import (
    DISCOVERY_EVENTS,
    DISCOVERY_SPEC_VERSION,
    EXPECTED_004A2_PROTOCOL_SHA256,
    EXPECTED_004A2_UNIVERSE_SHA256,
    EXPECTED_004A_REGIME_SHA256,
    EXPECTED_DATA001_MANIFEST_SHA256,
    PACKAGE_VERSION,
    PRIMARY_REGIMES,
    SEALED_EVENTS,
    assert_no_sealed_empirical_evidence,
    evidence_package_sha256,
    frozen_discovery_manifest,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/experiments/experiment_004b"
COMPACT_OUTPUTS = (
    "panel_summary.json",
    "contemporaneous_structure.csv",
    "response_matrix.csv",
    "structural_residuals.csv",
    "factor_spectrum.csv",
    "null_results.csv",
    "stability_summary.csv",
    "structure_summary.json",
    "response_matrix_external.json",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _float(value: str | None) -> float | None:
    if value in (None, "", "None", "nan"):
        return None
    return float(value)


def _file_record(path: Path) -> dict[str, object]:
    rows: int | None = None
    if path.suffix == ".csv":
        with path.open(encoding="utf-8") as handle:
            rows = max(0, sum(1 for _ in handle) - 1)
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "rows": rows,
    }


def _market_metadata() -> dict[str, dict[str, str]]:
    rows = _read_csv(OUT / "market_universe.csv")
    return {
        row["condition_id"]: row
        for row in rows
        if row["admitted"] == "true" and row["event"] in DISCOVERY_EVENTS
    }


def build_propagation_network(full_response: Path) -> list[dict[str, object]]:
    metadata = _market_metadata()
    market_stats: dict[tuple[str, ...], dict[str, float]] = defaultdict(
        lambda: {"out_sum": 0.0, "out_count": 0.0, "out_max": 0.0, "in_sum": 0.0, "in_count": 0.0}
    )
    family_stats: dict[tuple[str, ...], dict[str, float]] = defaultdict(
        lambda: {"sum": 0.0, "count": 0.0, "max": 0.0}
    )
    with full_response.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            effect = _float(row.get("effect"))
            if effect is None:
                continue
            event = row["event"]
            if event in SEALED_EVENTS:
                raise ValueError(f"sealed event leaked into response matrix: {event}")
            source = row["source_condition"]
            target = row["target_condition"]
            source_meta = metadata[source]
            target_meta = metadata[target]
            base = (
                event,
                row["regime"],
                row["resolution_seconds"],
                row["horizon_seconds"],
                row["metric"],
            )
            magnitude = abs(effect)
            source_key = (*base, source)
            target_key = (*base, target)
            market_stats[source_key]["out_sum"] += magnitude
            market_stats[source_key]["out_count"] += 1
            market_stats[source_key]["out_max"] = max(
                market_stats[source_key]["out_max"], magnitude
            )
            market_stats[target_key]["in_sum"] += magnitude
            market_stats[target_key]["in_count"] += 1
            family_key = (*base, source_meta["market_family"], target_meta["market_family"])
            family_stats[family_key]["sum"] += magnitude
            family_stats[family_key]["count"] += 1
            family_stats[family_key]["max"] = max(family_stats[family_key]["max"], magnitude)
    output: list[dict[str, object]] = []
    for key, stats in sorted(market_stats.items()):
        event, regime, resolution, horizon, metric, condition = key
        out_mean = stats["out_sum"] / stats["out_count"] if stats["out_count"] else None
        in_mean = stats["in_sum"] / stats["in_count"] if stats["in_count"] else None
        net = None if out_mean is None or in_mean is None else out_mean - in_mean
        concentration = stats["out_max"] / stats["out_sum"] if stats["out_sum"] > 0 else None
        output.append(
            {
                "row_type": "MARKET",
                "event": event,
                "regime": regime,
                "resolution_seconds": resolution,
                "horizon_seconds": horizon,
                "metric": metric,
                "condition_id": condition,
                "market_family": metadata[condition]["market_family"],
                "outgoing_response_strength": out_mean,
                "incoming_response_strength": in_mean,
                "net_directional_response": net,
                "outgoing_response_concentration": concentration,
                "available_outgoing_edges": int(stats["out_count"]),
                "available_incoming_edges": int(stats["in_count"]),
            }
        )
    for key, stats in sorted(family_stats.items()):
        event, regime, resolution, horizon, metric, source_family, target_family = key
        output.append(
            {
                "row_type": "MARKET_FAMILY_PAIR",
                "event": event,
                "regime": regime,
                "resolution_seconds": resolution,
                "horizon_seconds": horizon,
                "metric": metric,
                "source_market_family": source_family,
                "target_market_family": target_family,
                "mean_abs_response": stats["sum"] / stats["count"],
                "max_abs_response": stats["max"],
                "available_edges": int(stats["count"]),
            }
        )
    return output


def build_information_map(response_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[tuple[int, float]]] = defaultdict(list)
    target_price_metrics = {
        "price_to_future_price",
        "imbalance_to_future_price",
        "liquidity_to_future_price",
        "activity_to_future_price",
        "depth_activity_to_future_price",
        "venue_trade_count_to_future_price",
        "venue_trade_notional_to_future_price",
        "fill_count_to_future_price",
        "fill_notional_to_future_price",
    }
    for row in response_rows:
        if row["event"] in SEALED_EVENTS or row["metric"] not in target_price_metrics:
            continue
        value = _float(row.get("mean_abs_effect"))
        if value is None:
            continue
        key = (row["event"], row["regime"], row["resolution_seconds"], row["metric"])
        grouped[key].append((int(row["horizon_seconds"]), value))
    output: list[dict[str, object]] = []
    for key, values in sorted(grouped.items()):
        values.sort()
        strongest = max(values, key=lambda item: (item[1], -item[0]))
        output.append(
            {
                "event": key[0],
                "regime": key[1],
                "resolution_seconds": int(key[2]),
                "propagation_metric": key[3],
                "supported_horizons": ";".join(str(item[0]) for item in values),
                "strongest_horizon_seconds": strongest[0],
                "strongest_mean_abs_effect": strongest[1],
                "mean_abs_effect_across_supported_horizons": sum(v for _, v in values)
                / len(values),
            }
        )
    return output


def _compact_contemporaneous(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["event"], row["regime"], row["resolution_seconds"], row["metric"])].append(row)
    output: list[dict[str, object]] = []
    for key, values in sorted(grouped.items()):
        available = [(row, _float(row.get("effect"))) for row in values]
        available = [(row, value) for row, value in available if value is not None]
        magnitudes = sorted(abs(value) for _, value in available)
        strongest = (
            max(
                available,
                key=lambda item: (abs(item[1]), item[0]["condition_i"], item[0]["condition_j"]),
            )
            if available
            else None
        )
        output.append(
            {
                "event": key[0],
                "regime": key[1],
                "resolution_seconds": int(key[2]),
                "metric": key[3],
                "tested_pairs": len(values),
                "available_pairs": len(available),
                "mean_abs_effect": sum(magnitudes) / len(magnitudes) if magnitudes else None,
                "median_abs_effect": magnitudes[len(magnitudes) // 2] if magnitudes else None,
                "strongest_condition_i": strongest[0]["condition_i"] if strongest else "",
                "strongest_condition_j": strongest[0]["condition_j"] if strongest else "",
                "strongest_effect": strongest[1] if strongest else None,
            }
        )
    return output


def build_package() -> dict[str, Any]:
    manifest = frozen_discovery_manifest()
    universe = [row for row in _read_csv(OUT / "market_universe.csv") if row["admitted"] == "true"]
    edges = _read_csv(OUT / "structural_edges.csv")
    semantic_edges = [row for row in edges if row["verification_status"] != "UNVERIFIED"]
    contemporaneous = _compact_contemporaneous(_read_csv(OUT / "contemporaneous_structure.csv"))
    response = _read_csv(OUT / "response_matrix.csv")
    factors = _read_csv(OUT / "factor_spectrum.csv")
    nulls = _read_csv(OUT / "null_results.csv")
    stability = _read_csv(OUT / "stability_summary.csv")
    information_map = _read_csv(OUT / "information_map.csv")
    structure_summary = json.loads((OUT / "structure_summary.json").read_text())
    sealed = json.loads((OUT / "sealed_holdout_manifest.json").read_text())
    package: dict[str, Any] = {
        "package_version": PACKAGE_VERSION,
        "experiment_id": "EXPERIMENT-004B",
        "status": "EVIDENCE_ONLY_NO_HYPOTHESES",
        "discovery_spec_version": DISCOVERY_SPEC_VERSION,
        "discovery_spec_sha256": manifest["discovery_spec_sha256"],
        "upstream": {
            "data001_manifest_sha256": EXPECTED_DATA001_MANIFEST_SHA256,
            "004a_regime_sha256": EXPECTED_004A_REGIME_SHA256,
            "004a2_condition_universe_sha256": EXPECTED_004A2_UNIVERSE_SHA256,
            "004a2_validation_protocol_sha256": EXPECTED_004A2_PROTOCOL_SHA256,
        },
        "discovery_events": list(DISCOVERY_EVENTS),
        "primary_regimes": list(PRIMARY_REGIMES),
        "eligible_market_metadata": universe,
        "structural_graph": {
            "verified_or_semantic_edges": semantic_edges,
            "unverified_edge_count": sum(
                row["verification_status"] == "UNVERIFIED" for row in edges
            ),
            "verified_mechanical_edge_count": sum(
                row["verification_status"] == "MECHANICAL" for row in edges
            ),
        },
        "contemporaneous_summary": contemporaneous,
        "response_summary": response,
        "information_map": information_map,
        "factor_evidence": factors,
        "null_adjusted_evidence": nulls,
        "stability_evidence": stability,
        "structure_summary": structure_summary,
        "limitations": structure_summary["limitations"],
        "sealed_holdout_declaration": sealed,
        "restrictions": {
            "contains_alpha_hypotheses": False,
            "contains_trading_rules": False,
            "contains_strategy_recommendations": False,
            "contains_sealed_event_empirical_results": False,
        },
    }
    assert_no_sealed_empirical_evidence(package)
    package["package_sha256"] = evidence_package_sha256(package)
    return package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("download_dir", type=Path)
    args = parser.parse_args()
    download = args.download_dir.resolve()
    run = json.loads((download / "kaggle_run_summary.json").read_text())
    manifest = frozen_discovery_manifest()
    if run["discovery_spec_sha256"] != manifest["discovery_spec_sha256"]:
        raise ValueError("Kaggle output discovery-spec hash mismatch")
    if run["sealed_event_empirical_loads"] != 0:
        raise ValueError("sealed event empirical load detected")
    for filename in COMPACT_OUTPUTS:
        source = download / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, OUT / filename)
    external = json.loads((OUT / "response_matrix_external.json").read_text())
    full_response = download / external["filename"]
    if sha256_file(full_response) != external["sha256"]:
        raise ValueError("external full response hash mismatch")
    external.update(
        {
            "external_location": (
                "kaggle://polyleviathan/sig-cup-exp004b-election-market-structure/"
                "response_matrix_full.csv"
            ),
            "data001_manifest_sha256": EXPECTED_DATA001_MANIFEST_SHA256,
            "discovery_spec_sha256": manifest["discovery_spec_sha256"],
        }
    )
    (OUT / "response_matrix_external.json").write_text(
        json.dumps(external, indent=2, sort_keys=True) + "\n"
    )
    network = build_propagation_network(full_response)
    _write_csv(OUT / "propagation_network.csv", network)
    information = build_information_map(_read_csv(OUT / "response_matrix.csv"))
    _write_csv(OUT / "information_map.csv", information)
    package = build_package()
    (OUT / "hypothesis_generation_package.json").write_text(
        json.dumps(package, indent=2, sort_keys=True) + "\n"
    )
    files = [
        _file_record(path)
        for path in sorted(OUT.iterdir())
        if path.is_file() and path.name != "evidence_file_manifest.json"
    ]
    evidence_manifest = {
        "experiment_id": "EXPERIMENT-004B",
        "package_version": PACKAGE_VERSION,
        "package_sha256": package["package_sha256"],
        "discovery_spec_sha256": manifest["discovery_spec_sha256"],
        "files": files,
    }
    (OUT / "evidence_file_manifest.json").write_text(
        json.dumps(evidence_manifest, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "package_sha256": package["package_sha256"],
                "files": len(files),
                "network_rows": len(network),
                "information_rows": len(information),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
