# Predictions Cup

Foundation for a quantitative prediction-market trading system being developed for the 2026 Susquehanna Predictions Trading Cup.

## Current status

The accepted `main` baseline now includes BUILD-003 and EXPERIMENT-001A. It has **no trading capability**.

BUILD-003 provides authenticated, read-only SIG REST access for market discovery, prices, orderbooks, trades/history, market nodes, exchanges, and account health. EXPERIMENT-001A provides a separate public read-only Polymarket research recorder for external market capture. Neither path submits or cancels orders, calculates fair value, or runs strategies.

BUILD-002 remains the owner of typed configuration and canonical domain objects. BUILD-003 validates SIG wire payloads separately and converts into those canonical contracts only where the conversion is lossless.

BUILD-004 is currently **IN REVIEW in PR #12** and is not part of the accepted `main` baseline.
Its candidate implementation adds read-only SIG Realtime invalidation, authoritative REST
reconciliation, trusted/untrusted exchange state and replayable normalized capture. It adds no
strategy or execution capability.

## Requirements

- Python 3.12 or newer
- `pip`

Runtime dependencies are intentionally limited to:

- `pydantic` for canonical typed validation/serialization;
- `pydantic-settings` for deterministic environment-driven configuration;
- `httpx` for pooled asynchronous read-only SIG HTTP;
- `aiohttp` for public Polymarket Gamma/CLOB HTTP and market WebSocket capture;
- `supabase` for the BUILD-004 candidate private SIG Realtime subscription.

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
PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH
PREDICTIONS_CUP_SIG_REALTIME_BOOK_DEPTH
PREDICTIONS_CUP_SIG_REALTIME_TOKEN_REFRESH_MARGIN_SECONDS
PREDICTIONS_CUP_SIG_REALTIME_RETENTION_DAYS
```

EXPERIMENT-001A public capture variables:

```text
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED
PREDICTIONS_CUP_POLYMARKET_GAMMA_BASE_URL
PREDICTIONS_CUP_POLYMARKET_CLOB_BASE_URL
PREDICTIONS_CUP_POLYMARKET_WS_URL
PREDICTIONS_CUP_POLYMARKET_SNAPSHOT_INTERVAL_SECONDS
PREDICTIONS_CUP_POLYMARKET_BOOK_DEPTH
PREDICTIONS_CUP_POLYMARKET_DEPTH_SNAPSHOT_INTERVAL_SECONDS
PREDICTIONS_CUP_POLYMARKET_GAMMA_PAGE_LIMIT
PREDICTIONS_CUP_POLYMARKET_GAMMA_REFRESH_SECONDS
PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH
PREDICTIONS_CUP_POLYMARKET_UNIVERSE
PREDICTIONS_CUP_POLYMARKET_INCLUDE_IDS
PREDICTIONS_CUP_POLYMARKET_EXCLUDE_IDS
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

The accepted `main` SIG surface is read-only REST. There is no order placement, cancellation,
automatic tournament resolver, or portfolio accounting on `main`.

### SIG Realtime candidate — BUILD-004 IN REVIEW

PR #12 adds a separate explicit read-only capture command:

```bash
python -m predictions_cup.sig.capture --list-tournaments
python -m predictions_cup.sig.capture --tournament-id <TOURNAMENT_UUID>
```

It subscribes once to `tournament:{tournament_id}`, records the local receive time for each
batch, checks topic `revision/previousRevision`, and treats `bookDirty` as an invalidation
signal. Initial subscription, reconnect, token refresh, socket error or revision gap requires
authoritative REST reconciliation. A REST failure leaves state untrusted.

This path is never started by `python -m predictions_cup.app` and contains no write/order path.
See `docs/implementation/BUILD_004_SIG_REALTIME.md` for the candidate contract.

## Polymarket research recorder

EXPERIMENT-001A is an accepted, separate public read-only capture process. It is never started by normal application startup and contains no wallet, signing or order path.

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

It persists normalized event-time book changes, public last-trade events, a lean 1-second top-of-book panel and slower configurable depth snapshots. REST seed batches retain their own local receipt timestamps, and stale receive/PONG liveness forces reconnect followed by authoritative REST reseeding.

See `docs/experiments/EXPERIMENT_001_POLYMARKET_SHADOW.md` for storage sizing and capture semantics.

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
