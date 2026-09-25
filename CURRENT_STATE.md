# Current State

**Phase:** FOUNDATION / DOMAIN MODELS + EXPERIMENTAL EXTERNAL DATA CAPTURE

## Implemented

Production-foundation work:

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

EXPERIMENT-001A adds a deliberately separate, read-only Polymarket research recorder:

- current Gamma keyset discovery and metadata normalization;
- configurable 2026 U.S. election-market universe selection with manual overrides;
- public CLOB `/books` initialization;
- persistent market WebSocket capture with bounded heartbeat/reconnect handling;
- Decimal order-book state with snapshot-before-delta enforcement;
- source timestamp + local `observed_at` preservation;
- public trade-event capture where emitted;
- 1-second normalized research snapshots;
- restart-safe local SQLite/WAL storage;
- feed/book/trade/storage health separation.

The recorder is **experimental research infrastructure**, not part of the live SIG trading path.

## Not implemented

- SIG HTTP integration;
- SIG authentication requests;
- SIG realtime;
- production market-state engine;
- SIG ↔ external market mapping;
- semantic information-family graph;
- LOO-PRICE / LOO-FAMILY fair value;
- coherent probability solver;
- opportunity scanning;
- risk decisions/calculations;
- execution;
- order submission;
- portfolio accounting;
- shadow/paper fills;
- live trading.

## Trading capability

**NONE**

`trading_enabled` defaults to `False`. Setting it to `True` is only configuration intent and
requires a separately supplied trade credential; no execution path or network trading client
exists.

`polymarket_capture_enabled` controls only a separate read-only public-data recorder. It has no
wallet, key, signing or order method.

## Recommended next work

Production path: **BUILD-003 — SIG authenticated REST client**.

Experiment path: run EXPERIMENT-001A long enough to validate multi-day capture quality, then define
EXPERIMENT-001B for LOO-FAMILY / structural-FV testing without adding strategy logic to the
recorder.
