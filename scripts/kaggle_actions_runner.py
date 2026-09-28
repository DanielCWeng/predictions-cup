#!/usr/bin/env python3
"""Run one repository-defined Kaggle job from GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "kaggle" / "action-output"


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(args)}")
    return result


def repo_path(raw: str) -> Path:
    path = (ROOT / raw).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {raw}") from exc
    return path


def write_summary(lines: list[str]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    action = data.get("action")
    if action not in {"auth_check", "run", "status", "output"}:
        raise ValueError(f"Unsupported action: {action!r}")
    return data


def kernel_identity(kernel_dir: Path) -> str:
    metadata_path = kernel_dir / "kernel-metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing Kaggle metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    kernel = str(metadata.get("id", "")).strip()
    if "/" not in kernel:
        raise ValueError(f"Invalid Kaggle kernel id in {metadata_path}: {kernel!r}")
    return kernel


def output_dir_for(manifest_path: Path) -> Path:
    path = OUTPUT_ROOT / manifest_path.stem
    path.mkdir(parents=True, exist_ok=True)
    return path


def auth_check(output_dir: Path) -> None:
    result = run_command(["kaggle", "kernels", "list", "--mine", "--page", "1"])
    (output_dir / "auth_check.txt").write_text(result.stdout, encoding="utf-8")
    write_summary(["## Kaggle auth check", "", "Authenticated successfully and listed owned kernels."])


def kernel_from_manifest(data: dict[str, Any]) -> str:
    kernel = str(data.get("kernel", "")).strip()
    if not kernel:
        raise ValueError("Manifest requires 'kernel'")
    if "/" not in kernel:
        raise ValueError(f"Invalid Kaggle kernel id: {kernel!r}")
    return kernel


def capture_logs(kernel: str, output_dir: Path) -> None:
    result = run_command(["kaggle", "kernels", "logs", kernel], check=False)
    text = (result.stdout or "") + (result.stderr or "")
    (output_dir / "kernel.log").write_text(text, encoding="utf-8")


def status(kernel: str, output_dir: Path) -> str:
    result = run_command(["kaggle", "kernels", "status", kernel])
    text = (result.stdout or "") + (result.stderr or "")
    (output_dir / "status.txt").write_text(text, encoding="utf-8")
    return text


def download_outputs(data: dict[str, Any], kernel: str, output_dir: Path) -> None:
    dest = output_dir / "outputs"
    dest.mkdir(parents=True, exist_ok=True)
    args = ["kaggle", "kernels", "output", kernel, "-p", str(dest), "--force"]
    pattern = str(data.get("output_file_pattern", "")).strip()
    if pattern:
        args.extend(["--file-pattern", pattern])
    run_command(args)


def run_kernel(data: dict[str, Any], output_dir: Path) -> None:
    kernel_dir_raw = str(data.get("kernel_dir", "")).strip()
    if not kernel_dir_raw:
        raise ValueError("Run manifest requires 'kernel_dir'")
    kernel_dir = repo_path(kernel_dir_raw)
    if not kernel_dir.is_dir():
        raise FileNotFoundError(f"Kernel directory not found: {kernel_dir_raw}")

    metadata_kernel = kernel_identity(kernel_dir)
    declared_kernel = str(data.get("kernel", metadata_kernel)).strip()
    if declared_kernel != metadata_kernel:
        raise ValueError(
            f"Manifest kernel {declared_kernel!r} does not match metadata id {metadata_kernel!r}"
        )

    poll_seconds = int(data.get("poll_seconds", 30))
    timeout_minutes = int(data.get("timeout_minutes", 300))
    if poll_seconds < 10:
        raise ValueError("poll_seconds must be at least 10")
    if not 1 <= timeout_minutes <= 330:
        raise ValueError("timeout_minutes must be between 1 and 330")

    run_command(["kaggle", "kernels", "push", "-p", str(kernel_dir)])

    deadline = time.monotonic() + timeout_minutes * 60
    last_status = ""
    terminal = None

    while time.monotonic() < deadline:
        result = run_command(["kaggle", "kernels", "status", declared_kernel], check=False)
        last_status = ((result.stdout or "") + (result.stderr or "")).strip()
        lowered = last_status.lower()
        if "complete" in lowered:
            terminal = "complete"
            break
        if any(token in lowered for token in ("error", "failed", "cancelled", "canceled")):
            terminal = "failed"
            break
        time.sleep(poll_seconds)

    (output_dir / "final_status.txt").write_text(last_status + "\n", encoding="utf-8")
    capture_logs(declared_kernel, output_dir)

    if terminal is None:
        raise TimeoutError(f"Kaggle kernel did not finish within {timeout_minutes} minutes")
    if terminal != "complete":
        raise RuntimeError(f"Kaggle kernel failed: {last_status}")

    if bool(data.get("download_outputs", False)):
        download_outputs(data, declared_kernel, output_dir)

    write_summary(
        [
            "## Kaggle run",
            "",
            f"- Kernel: {declared_kernel}",
            f"- Final state: {terminal.upper()}",
            f"- Downloaded outputs: {bool(data.get('download_outputs', False))}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    if not os.environ.get("KAGGLE_API_TOKEN"):
        raise RuntimeError("KAGGLE_API_TOKEN is not set")

    manifest_path = repo_path(args.manifest)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {args.manifest}")

    data = load_manifest(manifest_path)
    output_dir = output_dir_for(manifest_path)
    (output_dir / "manifest.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    action = data["action"]
    if action == "auth_check":
        auth_check(output_dir)
    elif action == "run":
        run_kernel(data, output_dir)
    elif action == "status":
        kernel = kernel_from_manifest(data)
        text = status(kernel, output_dir)
        write_summary(["## Kaggle status", "", f"Kernel: {kernel}", "", text])
    elif action == "output":
        kernel = kernel_from_manifest(data)
        download_outputs(data, kernel, output_dir)
        write_summary(["## Kaggle output download", "", f"Kernel: {kernel}"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
