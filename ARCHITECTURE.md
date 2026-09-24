# Architecture

## Current implementation

BUILD-001 is a single Python package using a `src/` layout. It contains logical package boundaries, a minimal application entry point, logging, tests, static checks, and canonical project-control documents.

There is no trading engine, external integration, persistence layer, realtime subsystem, strategy logic, risk engine, or execution path yet.

## Planned architecture

The accepted initial direction is a **modular monolith: one Python service, primarily `asyncio`, with logical modules rather than separate services**.

Planned logical pipeline:

```text
SIG / external inputs
        ↓
market state + normalisation
        ↓
mapping
        ↓
fair value
        ↓
opportunity scanner
        ↓
risk
        ↓
execution
        ↓
fills/state
        ↓
learning/attribution
```

The package boundaries under `src/predictions_cup/` reserve these logical domains. They do not imply separate deployables or implemented behavior.

## Constraints

- Prefer standard-library functionality where reasonable.
- Keep one Python application until demonstrated needs justify otherwise.
- Avoid distributed infrastructure and generic abstraction frameworks without evidence.
- Treat REST as the eventual authoritative financial/state truth; realtime may accelerate/invalidate state but must be reconciled.
- Strategies may eventually propose trades, but central Risk must mediate execution.
