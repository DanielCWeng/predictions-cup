# Current State

**Phase:** FOUNDATION / READ-ONLY SIG REST

## Implemented

- repository foundation;
- Python tooling and CI;
- typed application configuration;
- environment-driven loading with optional local `.env` support;
- secret-safe read/trade credential representation;
- fail-closed trading configuration;
- canonical core domain models;
- Decimal-based financial/probability values;
- timezone-aware canonical timestamps;
- opaque external identifiers;
- validation invariants;
- runnable non-trading application shell;
- authenticated read-only asynchronous SIG REST client;
- strict SIG transport DTOs separate from canonical models;
- market and exchange discovery/detail reads;
- market-node transport preservation;
- single and bulk price snapshots;
- orderbook snapshots;
- price-history and cursor-paginated trade reads;
- account/authentication health read;
- explicit tournament context parameters with no automatic resolver;
- bounded retries for documented transient GET failures.

## Not implemented

- tournament selection/resolution;
- realtime/WebSockets;
- market-state engine;
- external market adapters;
- market mapping;
- fair value;
- relationships;
- opportunity scanning;
- risk decisions/calculations;
- execution;
- order submission or cancellation;
- portfolio accounting;
- shadow trading;
- live trading.

## Trading capability

**NONE**

`trading_enabled` defaults to `False`. Setting it to `True` is only configuration intent and requires a separately supplied trade credential; BUILD-003 exposes no write endpoint and never uses the trade credential.

Normal `python -m predictions_cup.app` startup remains network-free and safe without credentials.

## Recommended next ticket

**BUILD-004 — explicit tournament resolver / context selection**
