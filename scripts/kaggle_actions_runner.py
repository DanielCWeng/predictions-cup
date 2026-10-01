#!/usr/bin/env python3
"""Run one repository-defined Kaggle job from GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
    supported_actions = {
        "auth_check",
        "dataset_fetch",
        "dataset_probe",
        "dataset_script",
        "kernel_fetch",
        "logs",
        "output",
        "run",
        "status",
    }
    if action not in supported_actions:
        raise ValueError(f"Unsupported action: {action!r}")
    return data



def canonical_kernel_from_push(
    result: subprocess.CompletedProcess[str], fallback: str
) -> str:
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    match = re.search(
        r"https://www\.kaggle\.com/code/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
        text,
    )
    if not match:
        return fallback
    return f"{match.group(1)}/{match.group(2)}"

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
    write_summary(
        [
            "## Kaggle auth check",
            "",
            "Authenticated successfully and listed owned kernels.",
        ]
    )


def dataset_fetch(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_fetch manifest requires 'dataset' as owner/slug")
    files_raw = data.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise ValueError("dataset_fetch manifest requires non-empty 'files' list")

    dest = output_dir / "dataset_files"
    dest.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for raw in files_raw:
        file_name = str(raw).strip()
        if not file_name:
            raise ValueError("dataset_fetch file names must be non-empty")
        file_dest = dest / file_name.replace("/", "__")
        file_dest.mkdir(parents=True, exist_ok=True)
        result = run_command(
            [
                "kaggle",
                "datasets",
                "download",
                dataset,
                "-f",
                file_name,
                "-p",
                str(file_dest),
                "--force",
            ],
            check=False,
        )
        results.append(
            {
                "file": file_name,
                "output_dir": str(file_dest.relative_to(output_dir)),
                "returncode": result.returncode,
                "downloaded": result.returncode == 0,
            }
        )

    (output_dir / "dataset_fetch.json").write_text(
        json.dumps(
            {"dataset": dataset, "files": results},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if not all(bool(item["downloaded"]) for item in results):
        raise RuntimeError("One or more requested Kaggle dataset files could not be fetched")


def dataset_script(data: dict[str, Any], output_dir: Path) -> None:
    """Download bounded private-dataset files, run a repo script, then delete raw bytes."""
    dataset = str(data.get("dataset", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_script manifest requires 'dataset' as owner/slug")
    files_raw = data.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise ValueError("dataset_script manifest requires non-empty 'files' list")
    if len(files_raw) > 250:
        raise ValueError("dataset_script is capped at 250 source files per job")

    script_raw = str(data.get("script", "")).strip()
    if not script_raw:
        raise ValueError("dataset_script manifest requires 'script'")
    script = repo_path(script_raw)
    if not script.is_file():
        raise FileNotFoundError(f"dataset_script script not found: {script_raw}")

    script_args_raw = data.get("script_args", [])
    if not isinstance(script_args_raw, list):
        raise ValueError("dataset_script 'script_args' must be a list")
    script_args = [str(value) for value in script_args_raw]

    pip_packages_raw = data.get("pip_packages", [])
    if not isinstance(pip_packages_raw, list):
        raise ValueError("dataset_script 'pip_packages' must be a list")
    pip_packages = [str(value).strip() for value in pip_packages_raw if str(value).strip()]

    if bool(data.get("install_project", False)):
        run_command([sys.executable, "-m", "pip", "install", "-e", "."])
    if pip_packages:
        run_command([sys.executable, "-m", "pip", "install", *pip_packages])

    input_root = output_dir / "_dataset_script_inputs"
    result_root = output_dir / "outputs"
    input_root.mkdir(parents=True, exist_ok=True)
    result_root.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, str]] = []

    try:
        for index, raw in enumerate(files_raw):
            file_name = str(raw).strip()
            if not file_name:
                raise ValueError("dataset_script file names must be non-empty")
            file_dest = input_root / f"{index:04d}"
            file_dest.mkdir(parents=True, exist_ok=True)
            result = run_command(
                [
                    "kaggle",
                    "datasets",
                    "download",
                    dataset,
                    "-f",
                    file_name,
                    "-p",
                    str(file_dest),
                    "--force",
                ],
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to download dataset file: {file_name}")

            candidates = sorted(path for path in file_dest.rglob("*") if path.is_file())
            if len(candidates) != 1:
                raise RuntimeError(
                    f"Expected one downloaded file for {file_name}; got "
                    f"{[path.name for path in candidates]}"
                )
            downloaded.append(
                {
                    "remote": file_name,
                    "local": str(candidates[0]),
                }
            )

        input_manifest = output_dir / "dataset_script_inputs.json"
        input_manifest.write_text(
            json.dumps(
                {
                    "dataset": dataset,
                    "files": downloaded,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        run_command(
            [
                sys.executable,
                str(script),
                "--input-manifest",
                str(input_manifest),
                "--output-dir",
                str(result_root),
                *script_args,
            ]
        )
    finally:
        shutil.rmtree(input_root, ignore_errors=True)

    write_summary(
        [
            "## Kaggle dataset script",
            "",
            f"- Dataset: {dataset}",
            f"- Source files: {len(downloaded)}",
            f"- Script: {script_raw}",
            "- Raw source bytes deleted before artifact upload: YES",
        ]
    )


def dataset_probe(data: dict[str, Any], output_dir: Path) -> None:
    datasets_raw = data.get("datasets")
    if not isinstance(datasets_raw, list) or not datasets_raw:
        raise ValueError("dataset_probe manifest requires non-empty 'datasets' list")

    summaries: list[dict[str, object]] = []
    for raw in datasets_raw:
        dataset = str(raw).strip()
        if "/" not in dataset:
            raise ValueError(f"Invalid Kaggle dataset id: {dataset!r}")
        result = run_command(["kaggle", "datasets", "files", dataset], check=False)
        text = (result.stdout or "") + (result.stderr or "")
        safe_name = dataset.replace("/", "__")
        (output_dir / f"dataset_{safe_name}.txt").write_text(text, encoding="utf-8")

        status_result = None
        status_text = ""
        if result.returncode == 0:
            status_result = run_command(
                ["kaggle", "datasets", "status", dataset, "--format", "json"],
                check=False,
            )
            status_text = (status_result.stdout or "") + (status_result.stderr or "")
            (output_dir / f"dataset_{safe_name}_status.json").write_text(
                status_text,
                encoding="utf-8",
            )

        summaries.append(
            {
                "dataset": dataset,
                "returncode": result.returncode,
                "accessible": result.returncode == 0,
                "status_returncode": (
                    None if status_result is None else status_result.returncode
                ),
                "status": status_text.strip() or None,
            }
        )

    (output_dir / "dataset_probe.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_summary(
        [
            "## Kaggle dataset probe",
            "",
            *[
                f"- {item['dataset']}: "
                f"{'ACCESSIBLE' if item['accessible'] else 'NOT_ACCESSIBLE'}"
                for item in summaries
            ],
        ]
    )


def kernel_fetch(data: dict[str, Any], output_dir: Path) -> None:
    kernel = kernel_from_manifest(data)
    dest = output_dir / "kernel"
    dest.mkdir(parents=True, exist_ok=True)
    run_command(
        ["kaggle", "kernels", "pull", kernel, "-p", str(dest), "-m"]
    )
    metadata = dest / "kernel-metadata.json"
    if not metadata.is_file():
        raise FileNotFoundError("Kaggle kernel pull did not return kernel-metadata.json")
    write_summary(
        [
            "## Kaggle kernel metadata fetch",
            "",
            f"- Kernel: {kernel}",
            f"- Metadata: {metadata.relative_to(ROOT)}",
        ]
    )


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

    push_result = run_command(["kaggle", "kernels", "push", "-p", str(kernel_dir)])
    active_kernel = canonical_kernel_from_push(push_result, declared_kernel)
    (output_dir / "canonical_kernel.txt").write_text(active_kernel + "\n", encoding="utf-8")
    if active_kernel != declared_kernel:
        print(f"Kaggle canonicalized kernel: {declared_kernel} -> {active_kernel}", flush=True)

    deadline = time.monotonic() + timeout_minutes * 60
    last_status = ""
    terminal = None

    while time.monotonic() < deadline:
        result = run_command(["kaggle", "kernels", "status", active_kernel], check=False)
        last_status = ((result.stdout or "") + (result.stderr or "")).strip()
        lowered = last_status.lower()
        if "complete" in lowered:
            terminal = "complete"
            break
        terminal_failure_tokens = (
            "error",
            "failed",
            "cancelled",
            "canceled",
            "cannot access kernel",
            "permission 'kernels.get' was denied",
        )
        if any(token in lowered for token in terminal_failure_tokens):
            terminal = "failed"
            break
        time.sleep(poll_seconds)

    (output_dir / "final_status.txt").write_text(last_status + "\n", encoding="utf-8")
    capture_logs(active_kernel, output_dir)

    if terminal is None:
        raise TimeoutError(f"Kaggle kernel did not finish within {timeout_minutes} minutes")
    if terminal != "complete":
        raise RuntimeError(f"Kaggle kernel failed: {last_status}")

    if bool(data.get("download_outputs", False)):
        download_outputs(data, active_kernel, output_dir)

    write_summary(
        [
            "## Kaggle run",
            "",
            f"- Declared kernel: {declared_kernel}",
            f"- Active kernel: {active_kernel}",
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
    elif action == "dataset_fetch":
        dataset_fetch(data, output_dir)
    elif action == "dataset_probe":
        dataset_probe(data, output_dir)
    elif action == "dataset_script":
        dataset_script(data, output_dir)
    elif action == "kernel_fetch":
        kernel_fetch(data, output_dir)
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
    elif action == "logs":
        kernel = kernel_from_manifest(data)
        capture_logs(kernel, output_dir)
        write_summary(["## Kaggle logs", "", f"Kernel: {kernel}"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())