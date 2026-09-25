# Architecture

## Current implementation

The repository is a single Python package using a `src/` layout and remains a modular monolith.

BUILD-001 established the application shell, CI, logging, logical package boundaries, and canonical project-control documents.

BUILD-002 added on-demand typed runtime configuration, secret-aware SIG credential representation, canonical domain contracts, Decimal/time/identifier validation, and deterministic Pydantic serialization.

On PR #4's branch, BUILD-003 adds a proposed **read-only** SIG REST boundary under `predictions_cup.sig`; it remains unmerged pending independent review:

- one reusable asynchronous `httpx.AsyncClient` with a shared connection pool;
- Bearer authentication from the BUILD-002 read credential only;
- explicit request timeouts and clean async shutdown;
- strict SIG-specific transport DTOs;
- JSON-number to `Decimal` parsing before canonical conversion;
- ISO-8601 wire timestamp parsing at the adapter boundary;
- typed API errors and bounded retries for documented transient GET failures;
- explicit `tournament_id` parameters without any automatic context resolver.

There is still no realtime subsystem, persistence layer, market-state engine, strategy logic, risk-decision engine, write/execution path, or trading capability.

## Transport / canonical boundary

The implemented boundary is:

```text
SIG JSON
    ↓
predictions_cup.sig transport DTO validation
    ↓
explicit conversion where lossless
    ↓
predictions_cup.models canonical domain objects
```

Canonical models do not mirror SIG endpoint payloads. API-only metadata such as pricing contexts, spreads, history coverage, market-node structure, and pagination remains in the transport layer unless/until a canonical contract exists for it.

SIG price/orderbook snapshots do not contain a server timestamp in the supplied OpenAPI contract. Canonical `Price` and `OrderBook` conversion therefore requires an explicit observation timestamp supplied by the caller rather than fabricating a remote timestamp.

SIG trade `side` is an outcome label (`YES` / `NO`), not an aggressor BUY/SELL direction. Canonical `Trade.side` remains `None` for these reads.

## Tournament context

Context-sensitive REST methods accept an explicit `tournament_id`.

The client deliberately does **not** read or inject configured `AppSettings.tournament_id`. Passing `None` preserves the API's documented omission semantics, which differ by endpoint/key binding.

Organization-wide market discovery can expose multiple tournament pricing contexts. Converting such a transport market to one canonical `Market` requires the caller to choose a tournament explicitly; BUILD-003 will not guess.

## Planned architecture

The accepted initial direction remains a **modular monolith: one Python service, primarily `asyncio`, with logical modules rather than separate services**.

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
- REST is authoritative for financial/state truth; future realtime may accelerate/invalidate state but must be reconciled.
- Strategies may eventually propose `OrderIntent` objects, but central Risk must mediate any future execution.
- Canonical order kinds are limited to MARKET and LIMIT, with matching price-shape validation.
- Configuration is loaded on demand; no settings singleton/module-global state is created.
- `trading_enabled` alone can never submit an order.
- BUILD-003 is read-only: no order placement/cancellation and no realtime token/WebSocket path.
