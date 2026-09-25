# Predictions Cup

Foundation for a quantitative prediction-market trading system being developed for the 2026
Susquehanna Predictions Trading Cup.

## Current status

The production path remains in **FOUNDATION / DOMAIN MODELS** phase and has **no trading
capability**. It does not yet connect to SIG, submit orders, calculate fair value, or run
strategies.

EXPERIMENT-001A adds a deliberately separate, read-only Polymarket data recorder so live external
books and public trade events can be captured before later structural research. It is research
infrastructure, not a trading engine.

## Requirements

- Python 3.12 or newer
- `pip`

Runtime dependencies are intentionally small:

- `pydantic` for canonical typed validation/serialization;
- `pydantic-settings` for deterministic environment-driven configuration;
- `aiohttp` for the EXPERIMENT-001A public Gamma/CLOB HTTP and WebSocket transport.

The project uses a `src/` package layout with `pytest`, `ruff`, and strict `mypy`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

No credentials are required to install, test, run the application shell, or use the read-only
Polymarket recorder.

## Configuration

Configuration is loaded on demand from environment variables. A local `.env` file is supported for
developer convenience and remains ignored by Git; runtime environment variables take precedence.

Core variables:

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

EXPERIMENT-001A variables:

```text
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED
PREDICTIONS_CUP_POLYMARKET_GAMMA_BASE_URL
PREDICTIONS_CUP_POLYMARKET_CLOB_BASE_URL
PREDICTIONS_CUP_POLYMARKET_WS_URL
PREDICTIONS_CUP_POLYMARKET_SNAPSHOT_INTERVAL_SECONDS
PREDICTIONS_CUP_POLYMARKET_BOOK_DEPTH
PREDICTIONS_CUP_POLYMARKET_GAMMA_PAGE_LIMIT
PREDICTIONS_CUP_POLYMARKET_GAMMA_REFRESH_SECONDS
PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH
PREDICTIONS_CUP_POLYMARKET_UNIVERSE
PREDICTIONS_CUP_POLYMARKET_INCLUDE_IDS
PREDICTIONS_CUP_POLYMARKET_EXCLUDE_IDS
```

The default SIG API base is `https://www.thesuper.market/api/v1`.

`PREDICTIONS_CUP_TRADING_ENABLED` defaults to `false`. No API key, environment name, or other
setting implicitly enables trading. Even a validated `true` setting creates no execution path.

Read and trade credentials use Pydantic secret types. Ordinary settings
representations/serialization redact them. Actual key scope remains a server-side property of the
credential issued by SIG.

## Run the application shell

```bash
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

Both paths load validated settings, emit non-secret state, make zero network calls, perform no
trading, and exit.

## Run the experimental Polymarket recorder

Explicit long-running read-only capture:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

Optional manual public-data smoke check:

```bash
python -m predictions_cup.external.polymarket.recorder --smoke-test
```

The recorder uses public Gamma/CLOB market data only. It contains no Polymarket wallet, private
key, signing or order-submission path. Normal application startup never starts it automatically.
See `docs/experiments/EXPERIMENT_001_POLYMARKET_SHADOW.md` for capture semantics and limitations.

## Domain contracts

Canonical contracts live under `predictions_cup.models`. They are deliberately separate from SIG
and external-venue transport payloads.

Core rules include:

- `Decimal` rather than binary float for financial/probability/order values;
- prices/probabilities constrained to `[0,1]`;
- positive quantities where an economic amount must be positive;
- canonical timestamps require an actual timezone-aware `datetime` and normalise to UTC;
- timestamp strings/epoch integers must be parsed by transport adapters before canonical
  construction;
- opaque non-blank string identifiers;
- immutable historical/value models;
- canonical `OrderKind` values are MARKET and LIMIT;
- LIMIT orders require a `limit_price`, while MARKET orders forbid one;
- canonical `Position.quantity` represents non-negative outcome-share holdings, not signed
  directional risk exposure.

See `DATA_CONTRACTS.md` for contract ownership and meanings.

## Validation

```bash
ruff check .
mypy
pytest
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

CI runs lint, strict type checking, tests, and both normal app invocations on Python 3.12. The
networked Polymarket smoke test is intentionally manual and is not a CI dependency.

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

Secrets come from local/runtime configuration, are never committed, and must never be logged. `.env`
and `.env.*` are ignored while `.env.example` remains safe to commit.
