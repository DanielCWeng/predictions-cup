# Kaggle via GitHub Actions

The canonical non-interactive Kaggle control path is:

`ChatGPT / GitHub -> kaggle/jobs/*.json -> GitHub Actions -> Kaggle CLI -> Kaggle`.

This removes EC2 / Remote Desktop Commander from routine Kaggle submission, status, log, and output work.

## Authentication

Repository Actions must expose the secret `KAGGLE_API_TOKEN`. The workflow never prints this value.

## Queueing work

Create or modify a JSON manifest under `kaggle/jobs/`. A push containing several changed manifests fans them out as independent jobs with `max-parallel: 5`.

Supported actions are:

- `auth_check`: verify Kaggle authentication without launching compute.
- `run`: push a kernel directory, poll until terminal state, capture logs, and optionally download selected outputs.
- `status`: record the latest status for an existing kernel.
- `output`: download output from an existing kernel.

Example run manifest:

```json
{
  "schema_version": 1,
  "action": "run",
  "kernel_dir": "scripts/kaggle/experiment_004c_b",
  "kernel": "polyleviathan/sig-cup-exp004c-b-structural-redistribution",
  "poll_seconds": 30,
  "timeout_minutes": 300,
  "download_outputs": true,
  "output_file_pattern": "(results_summary\\.json|MASTER_HANDOFF_.*\\.md)$"
}
```

For `run`, the declared kernel must match the `id` in that directory's `kernel-metadata.json`.

## Outputs

Runner evidence is written under `kaggle/action-output/` on the ephemeral GitHub runner and uploaded as a short-lived GitHub Actions artifact.

Large empirical outputs should normally remain on Kaggle. Prefer `output_file_pattern` to retrieve only compact evidence needed for review.

## Safety

The runner never places live trades. It only controls Kaggle kernel operations.

The workflow has `contents: read` GitHub permissions. Kaggle credentials are provided only through the Actions secret.
