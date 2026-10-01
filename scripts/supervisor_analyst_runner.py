#!/usr/bin/env python3
"""Optional external analyst spool consumer for SUPERVISOR-001."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from predictions_cup.supervisor.contracts import ActionCode
from predictions_cup.supervisor.persistence import atomic_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SUPERVISOR-001 analyst spool consumer")
    parser.add_argument("--root", type=Path, default=Path("data/supervisor"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-invocations-per-hour", type=int, default=4)
    return parser.parse_args()


class Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._times: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._times and now - self._times[0] >= 3600.0:
            self._times.popleft()
        if len(self._times) >= self.limit:
            return False
        self._times.append(now)
        return True


def main() -> int:
    args = parse_args()
    command_text = os.environ.get(
        "PREDICTIONS_CUP_SUPERVISOR_ANALYST_COMMAND", ""
    ).strip()
    if not command_text:
        raise SystemExit("PREDICTIONS_CUP_SUPERVISOR_ANALYST_COMMAND is required")
    command = shlex.split(command_text)
    if not command:
        raise SystemExit("analyst command is empty")

    budget = Budget(args.max_invocations_per_hour)
    while True:
        processed = process_one(
            root=args.root,
            command=command,
            timeout_seconds=args.timeout_seconds,
            budget=budget,
        )
        if args.once:
            return 0 if processed else 3
        time.sleep(args.poll_seconds)


def process_one(
    *,
    root: Path,
    command: list[str],
    timeout_seconds: float,
    budget: Budget,
) -> bool:
    analysis_root = root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    manifests = sorted(
        root.glob("bundles/events/*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
    )
    manifests += sorted(
        root.glob("bundles/hourly/*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
    )
    for manifest_path in manifests:
        bundle_dir = manifest_path.parent
        output_path = analysis_root / f"{bundle_dir.name}.json"
        if output_path.exists():
            continue
        if not budget.allow():
            return False
        result = invoke(
            bundle_dir=bundle_dir,
            command=command,
            timeout_seconds=timeout_seconds,
        )
        atomic_json(output_path, result)
        _write_requests(root, result, bundle_dir.name)
        return True
    return False


def invoke(
    *,
    bundle_dir: Path,
    command: list[str],
    timeout_seconds: float,
) -> dict[str, object]:
    prompt = build_prompt(bundle_dir)
    started = datetime.now(UTC)
    env = dict(os.environ)
    env["SUPERVISOR_BUNDLE_DIR"] = str(bundle_dir.resolve())
    try:
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema_version": "supervisor-001-analysis-v1",
            "bundle_id": bundle_dir.name,
            "status": "TIMEOUT",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        }
    stdout = result.stdout[-1_000_000:]
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {
            "assessment": stdout[-50_000:],
            "novel_findings": [],
            "hypotheses": [],
            "evidence_refs": [],
            "recommended_operator_checks": [],
            "recommended_code_investigations": [],
            "requested_actions": [],
            "urgency": "LATER",
            "confidence": 0.0,
        }
    if not isinstance(parsed, dict):
        parsed = {"assessment": str(parsed), "requested_actions": []}
    output = {str(key): value for key, value in parsed.items()}
    output.update(
        {
            "schema_version": "supervisor-001-analysis-v1",
            "bundle_id": bundle_dir.name,
            "command_exit": result.returncode,
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return output


def build_prompt(bundle_dir: Path) -> str:
    manifest = _read_json(bundle_dir / "manifest.json")
    supervisor = _read_json(bundle_dir / "supervisor.json")
    compact = {"manifest": manifest, "supervisor": supervisor}
    return """You are the advisory forensic analyst behind SUPERVISOR-001.
The deterministic supervisor is authoritative for severity and safety gates.

Investigate the supplied compact bundle. You may use installed read-only subagents/tools
if your execution environment permits them. Do not modify production files, Git, services,
risk state, execution state, runtime configuration, strategy parameters, or orders.

First classify observations as data-quality/observability, infrastructure, execution/risk,
or economics. Never turn 'cannot observe' into 'is broken'.

Return JSON only with:
assessment: string
novel_findings: array
hypotheses: array
evidence_refs: array
recommended_operator_checks: array
recommended_code_investigations: array
requested_actions: array of {action,target,reason_code,justification}
urgency: NOW|NEXT_HOUR|LATER
confidence: number 0..1

requested_actions may only use the supervisor's allowlisted housekeeping/service-recovery
action codes. They are requests, not commands, and will be independently validated.

BUNDLE:
""" + json.dumps(compact, sort_keys=True, default=str)


def _write_requests(
    root: Path,
    result: Mapping[str, object],
    bundle_id: str,
) -> None:
    raw = result.get("requested_actions")
    if not isinstance(raw, list):
        return
    request_root = root / "remediation_requests"
    request_root.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(raw[:10]):
        if not isinstance(item, dict):
            continue
        try:
            code = ActionCode(str(item.get("action")))
        except ValueError:
            continue
        request = {
            "request_id": f"{bundle_id}-{index}",
            "bundle_id": bundle_id,
            "action": code.value,
            "target": item.get("target"),
            "reason_code": item.get("reason_code"),
            "justification": item.get("justification"),
            "requested_at": datetime.now(UTC).isoformat(),
        }
        atomic_json(request_root / f"{bundle_id}-{index}.json", request)


def _read_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} must contain an object")
    return {str(key): value for key, value in raw.items()}


if __name__ == "__main__":
    raise SystemExit(main())
