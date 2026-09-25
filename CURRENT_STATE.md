# Current State

**Phase:** FOUNDATION / DOMAIN MODELS

## Operating posture — 25 September 2026

- Broad strategy/mathematical research is now frozen by default; new work should answer a failed test, implementation ambiguity, live venue observation or specific architectural decision.
- Initial empirical strategy work is limited to direct external lead/lag, simple residual/relative value and simple selective market making.
- **Monday 28 September gate:** those initial hypotheses must be runnable through replay using data captured by our own infrastructure; otherwise priority collapses entirely onto data → replay → experiment.
- Competition-history findings are canonicalized in docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md.
- BUILD-003 remains an independent branch/review stream and is not modified by the documentation launch-plan work.

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
