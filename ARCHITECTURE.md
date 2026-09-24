# Architecture

## Current implementation

The repository is a single Python package using a `src/` layout and remains a modular monolith.

BUILD-001 established the application shell, CI, logging, logical package boundaries, and canonical project-control documents.

BUILD-002 adds:

- on-demand typed runtime configuration under `predictions_cup.config`;
- secret-aware SIG read/trade credential representation;
- canonical domain contracts under `predictions_cup.models`;
- Decimal/time/identifier validation primitives;
- deterministic serialization through Pydantic models.

There is still no HTTP/WebSocket client, external integration, persistence layer, realtime subsystem, market-state engine, strategy logic, risk-decision engine, execution path, or trading capability.

## Model boundary

Future adapters must conceptually perform:

```text
remote API payload
        ↓
transport validation
        ↓
canonical domain object
```

Canonical domain models are deliberately not endpoint-response DTOs. BUILD-003 may add SIG-specific transport types without redefining the canonical contracts.

## Planned architecture

The accepted initial direction is a **modular monolith: one Python service, primarily `asyncio`, with logical modules rather than separate services**.

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

## Constraints

- Prefer standard-library functionality where reasonable.
- Keep one Python application until demonstrated needs justify otherwise.
- Avoid distributed infrastructure and generic abstraction frameworks without evidence.
- Treat REST as the eventual authoritative financial/state truth; realtime may accelerate/invalidate state but must be reconciled.
- Strategies may eventually propose `OrderIntent` objects, but central Risk must mediate any future execution.
- Configuration is loaded on demand; no settings singleton/module-global state is created.
- `trading_enabled` alone can never submit an order.
