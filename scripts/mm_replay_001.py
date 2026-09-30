#!/usr/bin/env python3
"""Binding, audit and Kaggle job preparation for MM-REPLAY-001."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from predictions_cup.mm_replay_001 import (
    DATA_STATUS_BOUND,
    DATA_STATUS_WAITING,
    DatasetBinding,
    inspect_input,
    write_audit,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "experiments" / "mm_replay_001" / "input_manifest.json"
KERNEL_DIR = ROOT / "scripts" / "kaggle" / "mm_replay_001"
KERNEL_METADATA = KERNEL_DIR / "kernel-metadata.json"
JOB_TEMPLATE = KERNEL_DIR / "job-template.json"
JOB_PATH = ROOT / "kaggle" / "jobs" / "mm-replay-001.json"


def load_json(path: Path) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return dict(payload)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def bind_dataset(args: argparse.Namespace) -> None:
    payload = load_json(MANIFEST)
    payload.update(
        {
            "status": DATA_STATUS_BOUND,
            "kaggle_dataset_slug": args.slug,
            "dataset_version": args.version,
            "source": args.source,
            "schema_version": args.schema_version,
            "relation_to_data003": args.relation_to_data003,
            "acquisition_version": args.acquisition_version,
            "root_hint": args.root_hint,
        }
    )
    write_json(MANIFEST, payload)
    DatasetBinding.load(MANIFEST).require_bound()
    print(f"bound {args.slug}@{args.version}")


def audit(args: argparse.Namespace) -> None:
    binding = DatasetBinding.load(MANIFEST)
    report = inspect_input(Path(args.root).resolve(), binding)
    output_dir = ROOT / "data" / "experiments" / "mm_replay_001"
    write_audit(report, output_dir)
    print(report.to_markdown())
    if not report.passed:
        raise SystemExit(2)


def prepare_job(_args: argparse.Namespace) -> None:
    binding = DatasetBinding.load(MANIFEST)
    binding.require_bound()
    manifest = load_json(MANIFEST)
    slug = binding.kaggle_dataset_slug
    assert slug is not None

    metadata = load_json(KERNEL_METADATA)
    sources = [slug]
    frozen_artifacts = str(manifest.get("frozen_005f_artifact_dataset_slug") or "").strip()
    if frozen_artifacts:
        sources.append(frozen_artifacts)
    metadata["dataset_sources"] = sources
    write_json(KERNEL_METADATA, metadata)

    template = load_json(JOB_TEMPLATE)
    write_json(JOB_PATH, template)
    print(f"prepared {JOB_PATH.relative_to(ROOT)} for {', '.join(sources)}")


def reset_waiting(_args: argparse.Namespace) -> None:
    payload = load_json(MANIFEST)
    for key in (
        "kaggle_dataset_slug",
        "dataset_version",
        "source",
        "schema_version",
        "relation_to_data003",
        "acquisition_version",
    ):
        payload[key] = None
    payload["status"] = DATA_STATUS_WAITING
    payload["scientific_result"] = "NOT_RUN"
    write_json(MANIFEST, payload)

    metadata = load_json(KERNEL_METADATA)
    metadata["dataset_sources"] = []
    write_json(KERNEL_METADATA, metadata)
    if JOB_PATH.exists():
        JOB_PATH.unlink()
    print("DATA_STATUS=WAITING_FOR_DATA")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind-dataset")
    bind.add_argument("slug")
    bind.add_argument("--version", required=True)
    bind.add_argument("--source", required=True)
    bind.add_argument("--schema-version", required=True)
    bind.add_argument("--acquisition-version", required=True)
    bind.add_argument(
        "--relation-to-data003",
        default="DATA-003-linked current Cup mapped order-book acquisition",
    )
    bind.add_argument("--root-hint", default="/kaggle/input")
    bind.set_defaults(func=bind_dataset)

    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("root")
    audit_parser.set_defaults(func=audit)

    prepare = sub.add_parser("prepare-job")
    prepare.set_defaults(func=prepare_job)

    reset = sub.add_parser("reset-waiting")
    reset.set_defaults(func=reset_waiting)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
