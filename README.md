# Predictions Cup

Foundation for a quantitative prediction-market trading system being developed for the 2026 Susquehanna Predictions Trading Cup.

## Current status

The accepted `main` baseline includes BUILD-007, EXPERIMENT-001A and EXPERIMENT-002. It has **no trading capability**. BUILD-007 / PR #20 is merged and live-accepted as the read-only EC2 supervision / strict-universe / Parquet capture layer.

BUILD-003 provides authenticated, read-only SIG REST access for market discovery, prices, orderbooks, trades/history, market nodes, exchanges, and account health. EXPERIMENT-001A provides a separate public read-only Polymarket research recorder for external market capture. Neither path submits or cancels orders, calculates fair value, or runs strategies.

BUILD-002 remains the owner of typed configuration and canonical domain objects. BUILD-003 validates SIG wire payloads separately and converts into those canonical contracts only where the conversion is lossless.

## Canonical project-control read order

New orchestrators/reviewers should start with `ORCHESTRATOR.md`, then `CURRENT_STATE.md`, `BUILD_LEDGER.md`, `ARCHITECTURE.md`, `DATA_CONTRACTS.md` and `OPERATIONS.md`. Chat history is working context; merged repository state is canonical.

BUILD-004 is accepted on `main` as the read-only SIG Realtime/state foundation. BUILD-006 /
PR #19 is also accepted: it keeps full-tournament Realtime capture, makes resident authoritative
depth explicitly tracked-only, uses the bulk price/BBO endpoint for broad state, and places live
SIG REST behind one priority governor with shared 429 cooldown. It adds no strategy or execution
capability.

## Requirements

- Python 3.12 or newer
- `pip`

Runtime dependencies are intentionally limited to:

- `pydantic` for canonical typed validation/serialization;
- `pydantic-settings` for deterministic environment-driven configuration;
- `httpx` for pooled asynchronous read-only SIG HTTP;
- `aiohttp` for public Polymarket Gamma/CLOB HTTP and market WebSocket capture;
- `pyarrow` for immutable ZSTD Parquet research shards and replay reads;
- `supabase` for the accepted BUILD-004 private SIG Realtime subscription.

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
PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS
PREDICTIONS_CUP_SIG_REST_GOVERNOR_RATE_PER_SECOND
PREDICTIONS_CUP_SIG_REST_SHARED_COOLDOWN_MAX_SECONDS
PREDICTIONS_CUP_SIG_REALTIME_OPEN_BOOK_REFRESH_SECONDS
PREDICTIONS_CUP_SIG_REALTIME_BULK_PRICE_REFRESH_SECONDS
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
PREDICTIONS_CUP_POLYMARKET_RESEARCH_PATH
PREDICTIONS_CUP_POLYMARKET_PARQUET_SHARD_SECONDS
PREDICTIONS_CUP_POLYMARKET_PARQUET_MAX_ROWS_PER_SHARD
PREDICTIONS_CUP_POLYMARKET_UNIVERSE
PREDICTIONS_CUP_POLYMARKET_INCLUDE_IDS
PREDICTIONS_CUP_POLYMARKET_EXCLUDE_IDS
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS
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

### SIG Realtime — accepted BUILD-006 runtime

BUILD-004 provides the explicit read-only SIG capture command:

    python -m predictions_cup.sig.capture --list-tournaments
    python -m predictions_cup.sig.capture --tournament-id <TOURNAMENT_UUID>

BUILD-006 / PR #19 changes live depth maintenance so tournament-wide Realtime no longer implies
tournament-wide resident full depth. Full depth is opt-in with repeatable --tracked-exchange-id
arguments or the comma-separated `PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS` runtime
setting; the safe default is no tracked depth. The broad universe is maintained through
GET /exchanges/prices in batches of at most 100 IDs, while tracked bookDirty/recovery work is
HIGH priority behind one governed REST budget.

A finite conservative smoke can report governor/depth health explicitly:

    python -m predictions_cup.sig.capture \
      --tournament-id <TOURNAMENT_UUID> \
      --tracked-exchange-id <EXCHANGE_ID_1> \
      --tracked-exchange-id <EXCHANGE_ID_2> \
      --run-seconds 60 \
      --print-health

The default governed rate is 2 requests/second. The prior blocking curl + sleep probe only
demonstrated roughly 2.0–2.4 request starts/second; 3 requests/second has not yet been directly
validated with fixed-cadence starts. The configured rate is project policy, not a published SIG
rate limit. Likewise, the default 30-second tracked-book refresh
is our expiry-safety policy because SIG documents silent order expiry while the aggregate
orderbook carries no per-order expirationDate; it is not a SIG-required interval.

Realtime remains best-effort invalidation/event capture. REST remains authoritative. Scalar/BBO
bulk observations never make full depth trusted; a bulk missingIds result clears prior resident
scalar/BBO state rather than leaving a stale fallback. Failed tracked reconciliation remains
fail-closed. Normal application startup remains network-free and no write/order path exists.

See docs/implementation/BUILD_004_SIG_REALTIME.md for the historical accepted baseline and
docs/implementation/BUILD_006_SIG_REST_GOVERNOR.md for the accepted corrective contract.

## EC2 systemd supervision — BUILD-007 accepted

BUILD-007 / PR #20 adds two read-only systemd services and `scripts/install_runtime_services.sh`. Both use
only `~/.config/predictions-cup/runtime.env`, disable repo-local dotenv loading, strip the SIG
trade credential from the process environment and remain non-trading.

The supervised Polymarket path is now deliberately different from the accepted broad
EXPERIMENT-001A research baseline. A live broad-universe soak selected 3,160 markets / 6,320
tokens and demonstrated that 1 Hz SQLite research history is not operationally sustainable on the
EC2 host. BUILD-007 therefore preserves the 1-second research cadence while changing **scope and
storage**:

- systemd requires non-empty `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS`;
- those IDs are a strict include-only market/condition/token universe with no heuristic fallback;
- the intended production source is the accepted SIG ↔ Polymarket live crosswalk;
- unresolved IDs fail closed;
- an upgrade that still requests Polymarket capture but lacks the new strict IDs first disables/
  stops any existing Polymarket service before failing;
- 1-second scalar/BBO, book changes, public trades and depth snapshots write to immutable ZSTD
  Parquet shards below `data/polymarket_research/`;
- fresh `data/polymarket_operational.sqlite3` stores only metadata/tokens/health;
- raw Parquet trade capture is at-least-once, but hashed trades carry deterministic token+hash
  event identity and canonical replay de-duplicates them;
- legacy `data/polymarket_capture.sqlite3` captures remain readable and are not deleted.

The service command is:

```text
<repo>/.venv/bin/python -m predictions_cup.external.polymarket.recorder \
  --runtime-env-only --require-explicit-universe
```

Until LIVE-MAPPING-GATE-001 supplies an accepted crosswalk, the production-intended supervised
Polymarket universe is not available and the Polymarket service should remain stopped rather than
fall back to the 3,160-market heuristic. The SIG supervised collector can run independently.
The required ARM64 EC2 PyArrow/Parquet smoke/soak passed before merge on a deliberately bounded
3-market / 6-token public test universe. PyArrow 25.0.1, ZSTD shard production/readback, scheduled
Gamma refresh, the positive 1-second 429 retry floor and manual restart continuation were all
validated. That temporary test set is not production mapping evidence and must not be promoted.

Gamma failures also fail at the correct boundary: startup without a valid universe fails closed;
after startup, a failed scheduled metadata discovery/selection keeps the last-good universe and
research capture alive. Local persistence failures still surface.

See `OPERATIONS.md` and
`docs/implementation/BUILD_007_EC2_RUNTIME_SUPERVISION.md` for the accepted deployment contract and remaining production mapping-bounded gates.

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
bash -n scripts/install_runtime_services.sh
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

CI runs lint, shell validation (including shellcheck when available), strict type checking, tests, and both app invocations on Python 3.12.

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
