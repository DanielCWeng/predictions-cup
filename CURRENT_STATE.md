# Current State

**Phase:** FOUNDATION / DOMAIN MODELS

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
- runnable non-trading application shell.

## Not implemented

- SIG HTTP integration;
- SIG authentication requests;
- realtime;
- market-state engine;
- external market adapters;
- market mapping;
- fair value;
- relationships;
- opportunity scanning;
- risk decisions/calculations;
- execution;
- order submission;
- portfolio accounting;
- shadow trading;
- live trading.

## Trading capability

**NONE**

`trading_enabled` defaults to `False`. Setting it to `True` is only configuration intent and requires a separately supplied trade credential; no execution path or network client exists.

## Recommended next ticket

**BUILD-003 — SIG authenticated REST client**
