#!/usr/bin/env python3
"""Run one repository-defined Kaggle job from GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import re
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
        "auth_check", "run", "status", "output", "logs", "dataset_files", "dataset_probe",
        "dataset_fetch", "dataset_analysis", "artifact_analysis"
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



def dataset_files(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_files manifest requires 'dataset' as owner/slug")
    page_size = int(data.get("page_size", 1000))
    if not 1 <= page_size <= 10000:
        raise ValueError("page_size must be between 1 and 10000")

    result = run_command(
        [
            "kaggle",
            "datasets",
            "files",
            dataset,
            "--page-size",
            str(page_size),
            "--csv",
        ]
    )
    (output_dir / "dataset_files.csv").write_text(result.stdout, encoding="utf-8")
    write_summary(
        [
            "## Kaggle dataset file inventory",
            "",
            f"- Dataset: {dataset}",
            f"- Page size: {page_size}",
        ]
    )




def dataset_fetch(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    file_name = str(data.get("file", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_fetch manifest requires 'dataset' as owner/slug")
    if not file_name:
        raise ValueError("dataset_fetch manifest requires 'file'")

    dest = output_dir / "fetched"
    dest.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "kaggle",
            "datasets",
            "download",
            dataset,
            "-f",
            file_name,
            "-p",
            str(dest),
            "--unzip",
        ]
    )
    files = sorted(p for p in dest.rglob("*") if p.is_file())
    if not files:
        raise RuntimeError(f"No file downloaded for {file_name!r}")
    write_summary(
        [
            "## Kaggle dataset file fetch",
            "",
            f"- Dataset: {dataset}",
            f"- File: {file_name}",
            f"- Downloaded files: {len(files)}",
        ]
    )


def dataset_probe(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    file_name = str(data.get("file", "")).strip()
    if "/" not in dataset:
        raise ValueError("dataset_probe manifest requires 'dataset' as owner/slug")
    if not file_name:
        raise ValueError("dataset_probe manifest requires 'file'")

    probe_root = output_dir / "probe"
    probe_root.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "kaggle",
            "datasets",
            "download",
            dataset,
            "-f",
            file_name,
            "-p",
            str(probe_root),
            "--unzip",
        ]
    )
    run_command(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "pyarrow==25.0.1",
        ]
    )

    candidates = sorted(probe_root.rglob("*.parquet"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one parquet for {file_name!r}; found "
            f"{[p.relative_to(probe_root).as_posix() for p in candidates]}"
        )
    parquet = candidates[0]
    probe_code = """
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

path = Path(sys.argv[1])
out = Path(sys.argv[2])
pf = pq.ParquetFile(path)
schema = pf.schema_arrow
sample = []
if pf.metadata.num_rows:
    batch = next(pf.iter_batches(batch_size=5, use_threads=False), None)
    if batch is not None:
        sample = batch.to_pylist()[:5]
        sample = [
            {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if v is not None else None)
             for k, v in row.items()}
            for row in sample
        ]
time_stats = {}
for index, field in enumerate(schema):
    name = field.name
    if not any(part in name.lower() for part in ("time", "timestamp", "observed", "received")):
        continue
    lo = None
    hi = None
    for rg in range(pf.metadata.num_row_groups):
        stats = pf.metadata.row_group(rg).column(index).statistics
        if stats is None or not stats.has_min_max:
            continue
        a, b = stats.min, stats.max
        lo = a if lo is None or a < lo else lo
        hi = b if hi is None or b > hi else hi
    time_stats[name] = {"min": str(lo), "max": str(hi)}
payload = {
    "path": path.name,
    "rows": int(pf.metadata.num_rows),
    "row_groups": int(pf.metadata.num_row_groups),
    "columns": [
        {"name": f.name, "type": str(f.type), "nullable": bool(f.nullable)}
        for f in schema
    ],
    "time_stats": time_stats,
    "sample_rows": sample,
}
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
"""
    probe_script = output_dir / "_probe.py"
    probe_script.write_text(probe_code, encoding="utf-8")
    run_command([sys.executable, str(probe_script), str(parquet), str(output_dir / "probe.json")])
    parquet.unlink()
    probe_script.unlink()
    write_summary(
        [
            "## Kaggle dataset parquet probe",
            "",
            f"- Dataset: {dataset}",
            f"- File: {file_name}",
        ]
    )



def dataset_analysis(data: dict[str, Any], output_dir: Path) -> None:
    dataset = str(data.get("dataset", "")).strip()
    script_raw = str(data.get("script", "")).strip()
    files = data.get("files")
    if "/" not in dataset:
        raise ValueError("dataset_analysis requires 'dataset' as owner/slug")
    if not script_raw:
        raise ValueError("dataset_analysis requires 'script'")
    if not isinstance(files, list) or not files or not all(isinstance(x, str) and x for x in files):
        raise ValueError("dataset_analysis requires a non-empty string list 'files'")

    script = repo_path(script_raw)
    if not script.is_file():
        raise FileNotFoundError(f"Analysis script not found: {script_raw}")

    packages = data.get(
        "packages",
        [
            "numpy==2.3.3",
            "pandas==2.3.3",
            "pyarrow==25.0.1",
            "scikit-learn==1.7.2",
        ],
    )
    if not isinstance(packages, list) or not all(isinstance(x, str) and x for x in packages):
        raise ValueError("packages must be a string list")
    run_command([sys.executable, "-m", "pip", "install", "--quiet", *packages])

    spec = {
        "dataset": dataset,
        "files": files,
        "worker_id": str(data.get("worker_id", output_dir.name)),
        "sample_per_hour": int(data.get("sample_per_hour", 3000)),
        "seed": int(data.get("seed", 505009)),
        "surface": str(data.get("surface", "TRAIN")),
    }
    analysis_params = data.get("analysis_params", {})
    if not isinstance(analysis_params, dict):
        raise ValueError("analysis_params must be an object")
    spec.update(analysis_params)
    if "thresholds" in data:
        if not isinstance(data["thresholds"], dict):
            raise ValueError("thresholds must be an object")
        spec["thresholds"] = data["thresholds"]
    spec_path = output_dir / "analysis_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    results = output_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            sys.executable,
            str(script),
            "--spec",
            str(spec_path),
            "--output-dir",
            str(results),
        ]
    )
    write_summary(
        [
            "## Kaggle dataset streaming analysis",
            "",
            f"- Dataset: {dataset}",
            f"- Files: {len(files)}",
            f"- Worker: {spec['worker_id']}",
            f"- Surface: {spec['surface']}",
        ]
    )



def artifact_analysis(data: dict[str, Any], output_dir: Path) -> None:
    import urllib.request
    import zipfile

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("artifact_analysis requires non-empty 'artifacts' list")
    script_raw = str(data.get("script", "")).strip()
    if not script_raw:
        raise ValueError("artifact_analysis requires 'script'")
    script = repo_path(script_raw)
    if not script.is_file():
        raise FileNotFoundError(f"Analysis script not found: {script_raw}")

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or "/" not in repository:
        raise RuntimeError("artifact_analysis requires GITHUB_TOKEN and GITHUB_REPOSITORY")

    packages = data.get("packages", ["numpy==2.3.3", "pandas==2.3.3", "pyarrow==25.0.1"])
    if packages:
        if not isinstance(packages, list) or not all(isinstance(x, str) and x for x in packages):
            raise ValueError("'packages' must be a list of non-empty strings")
        run_command([sys.executable, "-m", "pip", "install", "--quiet", *packages])

    artifacts_root = output_dir / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    for raw in artifacts:
        artifact_id = int(raw)
        zip_path = artifacts_root / f"{artifact_id}.zip"
        url = f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "predictions-cup-005i-artifact-analysis",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            zip_path.write_bytes(response.read())
        dest = artifacts_root / str(artifact_id)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(dest)
        zip_path.unlink()

    spec_path = output_dir / "artifact_spec.json"
    spec_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    results = output_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            sys.executable,
            str(script),
            "--artifacts-root",
            str(artifacts_root),
            "--spec",
            str(spec_path),
            "--output-dir",
            str(results),
        ]
    )
    write_summary(
        [
            "## Artifact analysis",
            "",
            f"- Artifacts: {len(artifacts)}",
            f"- Script: {script_raw}",
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
    elif action == "dataset_files":
        dataset_files(data, output_dir)
    elif action == "dataset_probe":
        dataset_probe(data, output_dir)
    elif action == "dataset_fetch":
        dataset_fetch(data, output_dir)
    elif action == "dataset_analysis":
        dataset_analysis(data, output_dir)
    elif action == "artifact_analysis":
        artifact_analysis(data, output_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())