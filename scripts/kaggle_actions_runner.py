#!/usr/bin/env python3
"""Run one repository-defined Kaggle job from GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import zipfile
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
    if action not in {
        "auth_check",
        "run",
        "status",
        "output",
        "logs",
        "dataset_probe",
        "publish_code_dataset",
    }:
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


def staged_kernel_dir(
    data: dict[str, Any],
    kernel_dir: Path,
    output_dir: Path,
) -> Path:
    raw_specs = data.get("stage_paths")
    if raw_specs is None:
        return kernel_dir
    if not isinstance(raw_specs, list):
        raise ValueError("stage_paths must be a list")
    staging = output_dir / "kernel-staging"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(kernel_dir, staging)
    for index, raw_spec in enumerate(raw_specs):
        if not isinstance(raw_spec, dict):
            raise ValueError(f"stage_paths[{index}] must be an object")
        source_raw = str(raw_spec.get("source", "")).strip()
        destination_raw = str(raw_spec.get("destination", "")).strip()
        if not source_raw or not destination_raw:
            raise ValueError(f"stage_paths[{index}] requires source and destination")
        source = repo_path(source_raw)
        destination_rel = Path(destination_raw)
        if destination_rel.is_absolute() or ".." in destination_rel.parts:
            raise ValueError(f"invalid staged destination: {destination_raw}")
        destination = (staging / destination_rel).resolve()
        try:
            destination.relative_to(staging.resolve())
        except ValueError as exc:
            raise ValueError(f"staged destination escapes kernel: {destination_raw}") from exc
        if not source.exists():
            raise FileNotFoundError(f"staged source not found: {source_raw}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    return staging


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

    push_dir = staged_kernel_dir(data, kernel_dir, output_dir)
    push_result = run_command(["kaggle", "kernels", "push", "-p", str(push_dir)])
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


def dataset_probe(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_probe requires dataset='owner/slug'")

    status_result = run_command(
        ["kaggle", "datasets", "status", dataset],
        check=False,
    )
    (output_dir / "dataset_status.txt").write_text(
        (status_result.stdout or "") + (status_result.stderr or ""),
        encoding="utf-8",
    )

    files_result = run_command(
        ["kaggle", "datasets", "files", dataset, "--page-size", "1000", "--csv"],
        check=False,
    )
    (output_dir / "dataset_files.csv").write_text(
        (files_result.stdout or "") + (files_result.stderr or ""),
        encoding="utf-8",
    )

    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_result = run_command(
        ["kaggle", "datasets", "metadata", dataset, "-p", str(metadata_dir)],
        check=False,
    )
    (output_dir / "dataset_metadata_command.txt").write_text(
        (metadata_result.stdout or "") + (metadata_result.stderr or ""),
        encoding="utf-8",
    )

    probe_script = (
        "import json\n"
        "from kaggle.api.kaggle_api_extended import KaggleApi\n"
        "api=KaggleApi(); api.authenticate()\n"
        f"obj=api.dataset_view({dataset!r})\n"
        "def conv(v):\n"
        "    if isinstance(v,(str,int,float,bool)) or v is None: return v\n"
        "    if isinstance(v,dict): return {str(k):conv(x) for k,x in v.items()}\n"
        "    if isinstance(v,(list,tuple)): return [conv(x) for x in v]\n"
        "    d=getattr(v,'__dict__',None)\n"
        "    if isinstance(d,dict): return {str(k):conv(x) for k,x in d.items() if not str(k).startswith('_')}\n"
        "    return str(v)\n"
        "print(json.dumps(conv(obj), indent=2, sort_keys=True))\n"
    )
    api_result = run_command([sys.executable, "-c", probe_script], check=False)
    (output_dir / "dataset_api_view.json").write_text(
        api_result.stdout or "",
        encoding="utf-8",
    )
    (output_dir / "dataset_api_view.stderr.txt").write_text(
        api_result.stderr or "",
        encoding="utf-8",
    )

    if status_result.returncode != 0 or files_result.returncode != 0:
        raise RuntimeError(
            "Kaggle dataset probe failed; inspect dataset_status.txt and dataset_files.csv"
        )

    write_summary(
        [
            "## Kaggle dataset probe",
            "",
            f"- Dataset: {dataset}",
            f"- Status rc: {status_result.returncode}",
            f"- Files rc: {files_result.returncode}",
            f"- Metadata rc: {metadata_result.returncode}",
            f"- API view rc: {api_result.returncode}",
        ]
    )


def publish_code_dataset(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    source_dir = Path(str(data.get("source_dir", "src/predictions_cup"))).resolve()
    title = str(data.get("title", "Predictions Cup Code Snapshot")).strip()
    if "/" not in dataset:
        raise ValueError("publish_code_dataset requires dataset='owner/slug'")
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)

    commit = run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
    stage = output_dir / "code_dataset_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    archive_path = stage / "predictions_cup.zip"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        parent = source_dir.parent
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(parent))

    (stage / "CODE_SHA.txt").write_text(commit + "\n", encoding="utf-8")
    (stage / "dataset-metadata.json").write_text(
        json.dumps(
            {
                "title": title,
                "id": dataset,
                "licenses": [{"name": "other"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    exists = run_command(["kaggle", "datasets", "status", dataset], check=False)
    if exists.returncode == 0:
        action_result = run_command(
            [
                "kaggle",
                "datasets",
                "version",
                "-p",
                str(stage),
                "-m",
                f"predictions-cup {commit}",
            ]
        )
        operation = "version"
    else:
        action_result = run_command(
            ["kaggle", "datasets", "create", "-p", str(stage)]
        )
        operation = "create"

    (output_dir / "code_dataset_publish.txt").write_text(
        (action_result.stdout or "") + (action_result.stderr or ""),
        encoding="utf-8",
    )
    write_summary(
        [
            "## Kaggle code dataset publish",
            "",
            f"- Dataset: {dataset}",
            f"- Operation: {operation}",
            f"- Commit: {commit}",
            f"- Archive bytes: {archive_path.stat().st_size}",
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
    elif action == "logs":
        kernel = kernel_from_manifest(data)
        capture_logs(kernel, output_dir)
        write_summary(["## Kaggle logs", "", f"Kernel: {kernel}"])
    elif action == "dataset_probe":
        dataset_probe(data, output_dir)
    elif action == "publish_code_dataset":
        publish_code_dataset(data, output_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())