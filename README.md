# Predictions Cup

Foundation for a quantitative prediction-market trading system being developed for the 2026 Susquehanna Predictions Trading Cup.

## Current status

This repository is in **FOUNDATION / READ-ONLY SIG REST** phase. It has **no trading capability**. BUILD-003 adds an authenticated, read-only SIG REST adapter for market discovery, prices, orderbooks, trades/history, market nodes, exchanges, and account health. It does not submit/cancel orders, use realtime, calculate fair value, or run strategies.

BUILD-002 remains the owner of typed configuration and canonical domain objects. BUILD-003 validates SIG wire payloads separately and converts into those canonical contracts only where the conversion is lossless.

## Requirements

- Python 3.12 or newer
- `pip`

Runtime dependencies are intentionally limited to:

- `pydantic` for canonical typed validation/serialization;
- `pydantic-settings` for deterministic environment-driven configuration;
- `httpx` for pooled asynchronous read-only SIG HTTP.

The project uses a `src/` package layout with `pytest`, `ruff`, and strict `mypy`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

No credentials are required to install, test, or run the application shell.

## Configuration

Configuration is loaded on demand from environment variables. A local `.env` file is supported for developer convenience and remains ignored by Git; runtime environment variables take precedence.

Canonical variables:

```text
PREDICTIONS_CUP_ENVIRONMENT
PREDICTIONS_CUP_LOG_LEVEL
PREDICTIONS_CUP_SIG_API_BASE_URL
PREDICTIONS_CUP_SIG_READ_CREDENTIAL
PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL
PREDICTIONS_CUP_TOURNAMENT_ID
PREDICTIONS_CUP_TOURNAMENT_SLUG
PREDICTIONS_CUP_TRADING_ENABLED
```

The default SIG API base is `https://www.thesuper.market/api/v1`.

`PREDICTIONS_CUP_TRADING_ENABLED` defaults to `false`. No API key, environment name, or other setting implicitly enables trading. Even a validated `true` setting creates no execution path.

Read and trade credentials use Pydantic secret types. Ordinary settings representations/serialization redact them. Actual key scope remains a server-side property of the credential issued by SIG.

## Run the application shell

```bash
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

Both paths load validated settings, emit non-secret state, make zero network calls, perform no trading, and exit. The SIG client is only constructed and used when explicitly invoked by application code.

## SIG read-only REST adapter

`predictions_cup.sig.SigRestClient` uses `PREDICTIONS_CUP_SIG_READ_CREDENTIAL` with `Authorization: Bearer ...`, a reusable `httpx.AsyncClient`, explicit timeouts, strict transport DTOs, and bounded retries for documented transient GET failures.

Tournament context is an explicit caller concern. Read methods accept `tournament_id` where the API is context-sensitive, and the client never injects `PREDICTIONS_CUP_TOURNAMENT_ID` automatically. Omitting it therefore preserves the API-defined public / organization-wide / default-tournament behaviour.

SIG JSON numbers used for prices and quantities are decoded through `Decimal` before validation. ISO-8601 wire timestamps are parsed in the SIG transport layer; canonical models continue to reject timestamp strings.

The current SIG surface is read-only. There is no order placement, cancellation, realtime/WebSocket, automatic tournament resolver, or portfolio accounting.

## Domain contracts

Canonical contracts live under `predictions_cup.models`. They are deliberately separate from SIG/external API transport payloads.

Core rules include:

- `Decimal` rather than binary float for financial/probability/order values;
- prices/probabilities constrained to `[0,1]`;
- positive quantities where an economic amount must be positive;
- canonical timestamps require an actual timezone-aware `datetime` and normalise to UTC;
- timestamp strings/epoch integers are parsed by transport adapters before canonical construction;
- opaque non-blank string identifiers;
- immutable historical/value models;
- canonical `OrderKind` values are MARKET and LIMIT;
- LIMIT orders require a `limit_price`, while MARKET orders forbid one;
- canonical `Position.quantity` represents non-negative outcome-share holdings, not signed directional risk exposure.

See `DATA_CONTRACTS.md` for contract ownership and meanings.

## Validation

```bash
ruff check .
mypy
pytest
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

CI runs lint, strict type checking, tests, and both app invocations on Python 3.12.

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

Secrets come from local/runtime configuration, are never committed, and must never be logged. `.env` and `.env.*` are ignored while `.env.example` remains safe to commit.
