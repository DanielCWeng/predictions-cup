# Architecture

## Current implementation

The repository is a single Python package using a `src/` layout and remains a modular monolith.

BUILD-001 established the application shell, CI, logging, logical package boundaries, and canonical
project-control documents.

BUILD-002 adds:

- on-demand typed runtime configuration under `predictions_cup.config`;
- secret-aware SIG read/trade credential representation;
- canonical domain contracts under `predictions_cup.models`;
- Decimal/time/identifier validation primitives;
- deterministic serialization through Pydantic models.

EXPERIMENT-001A adds a **separate experimental read-only capture process** under
`predictions_cup.external.polymarket`. It is intentionally not wired into normal application
startup or the future execution path.

```text
Gamma metadata
      ↓
experimental universe selection
      ↓
CLOB public books + market WebSocket
      ↓
transport normalization / Decimal in-memory books
      ↓
1-second research snapshots + public trade events
      ↓
local SQLite/WAL dataset
```

There is still no SIG HTTP/WebSocket client, production market-state engine, market mapping,
strategy logic, risk-decision engine, execution path, portfolio accounting or trading capability.

## Model boundary

Future adapters must conceptually perform:

```text
remote API payload
        ↓
transport validation / wire-format parsing
        ↓
canonical domain object
```

Canonical domain models are deliberately not endpoint-response DTOs. BUILD-003 may add SIG-specific
transport types without redefining the canonical contracts.

EXPERIMENT-001A follows the same boundary: Polymarket-specific metadata, books and capture records
live under `external/polymarket` and do not distort canonical SIG/domain models. A later
`MarketMapping` ticket will connect venue identities explicitly.

Canonical timestamp fields therefore accept only actual timezone-aware `datetime` objects. Parsing
API strings, epoch values, or other wire representations is an adapter responsibility. The
Polymarket adapter keeps source/event time distinct from local `observed_at`.

Canonical `Position` represents non-negative platform outcome-share holdings. Any later signed
directional exposure is a derived state/risk concept rather than a negative canonical holding.

## Experimental external-data invariants

- Public/read-only endpoints only; no Polymarket auth, wallet, signing or order methods.
- REST `/books` establishes authoritative book state before deltas are trusted.
- Disconnect/reconnect invalidates all old in-memory books and re-establishes them from REST.
- A WebSocket heartbeat is feed health; unchanged book state is not itself feed staleness.
- Stable subscriptions are preferred. Current incremental subscribe/unsubscribe is used rather
  than reconnecting for every small universe change.
- SQLite is local research persistence only, not production financial truth.
- Normal `python -m predictions_cup.app` startup remains finite and network-free.

## Planned architecture

The accepted initial direction is a **modular monolith: one Python service, primarily `asyncio`,
with logical modules rather than separate services**.

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

The EXPERIMENT-001A recorder is a research side path feeding future evidence into mapping/fair-value
work. It must not silently become the production execution service.

## Constraints

- Prefer standard-library functionality where reasonable.
- Keep one Python application until demonstrated needs justify otherwise.
- Avoid distributed infrastructure and generic abstraction frameworks without evidence.
- Treat REST as the eventual authoritative financial/state truth; realtime may accelerate/invalidate
  state but must be reconciled.
- Strategies may eventually propose `OrderIntent` objects, but central Risk must mediate any future
  execution.
- Canonical order kinds are limited to MARKET and LIMIT, with matching price-shape validation.
- Configuration is loaded on demand; no settings singleton/module-global state is created.
- `trading_enabled` alone can never submit an order.
- Experimental capture enablement cannot create a trading path.
