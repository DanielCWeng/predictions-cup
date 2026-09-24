# Predictions Cup

Foundation for a quantitative prediction-market trading system being developed for the 2026 Susquehanna Predictions Trading Cup.

## Current status

This repository is in **FOUNDATION** phase. It has **no trading capability**. It does not connect to SIG or any external API, submit orders, calculate fair value, or run strategies.

## Requirements

- Python 3.12 or newer
- `pip`

The repository uses a `src/` package layout with `pytest`, `ruff`, and `mypy`. Runtime code currently uses only the Python standard library. Development-tool and build-backend versions are pinned in `pyproject.toml` for reproducible setup.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

No credentials are required for BUILD-001.

## Run the application shell

```bash
python -m predictions_cup.app
```

For an explicit smoke-test invocation:

```bash
python -m predictions_cup.app --smoke-test
```

Both paths only emit startup/shutdown logs and exit. No network access or trading behavior is implemented.

## Validation

```bash
ruff check .
mypy
pytest
```

CI runs these same checks on Python 3.12 for pushes and pull requests.

## Repository orientation

Start with `CURRENT_STATE.md`. The canonical control documents are:

- `CURRENT_STATE.md` — what is and is not implemented now;
- `ARCHITECTURE.md` — accepted current/planned architecture boundaries;
- `BUILD_LEDGER.md` — implementation ticket record;
- `DECISION_LOG.md` — architectural decisions;
- `DATA_CONTRACTS.md` — canonical domain-contract ownership/status;
- `RISK_POLICY.md` — non-negotiable risk architecture principles;
- `OPERATIONS.md` — current and future operating expectations;
- `RESEARCH_HANDOFFS.md` — schema for research-to-build handoffs;
- `STRATEGY_REGISTRY.md` — canonical strategy registry.

## Secrets

Secrets come from environment/runtime configuration. Secrets are never committed and never logged. `.env` and `.env.*` are ignored; `.env.example` is deliberately trackable and contains no credentials. BUILD-002 will define the typed configuration model and any required secret names.
