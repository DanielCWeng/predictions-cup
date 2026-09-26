# Architecture

## Current implementation

The repository is a single Python package using a `src/` layout and remains a modular monolith.

BUILD-001 established the application shell, CI, logging, logical package boundaries, and canonical project-control documents.

BUILD-002 added on-demand typed runtime configuration, secret-aware SIG credential representation, canonical domain contracts, Decimal/time/identifier validation, and deterministic Pydantic serialization.

BUILD-003 provides the accepted **read-only** SIG REST boundary under `predictions_cup.sig`:

- one reusable asynchronous `httpx.AsyncClient` with a shared connection pool;
- Bearer authentication from the BUILD-002 read credential only;
- explicit request timeouts and clean async shutdown;
- strict SIG-specific transport DTOs;
- JSON-number to `Decimal` parsing before canonical conversion;
- ISO-8601 wire timestamp parsing at the adapter boundary;
- typed API errors and bounded retries for documented transient GET failures;
- explicit `tournament_id` parameters without any automatic context resolver.

EXPERIMENT-001A additionally provides the accepted **public read-only Polymarket research capture** path under `predictions_cup.external.polymarket`: public Gamma/CLOB discovery, authoritative REST book seeding, persistent market WebSocket ingestion, normalized event-time book-change/trade persistence, a lean 1-second top-of-book panel, slower bounded depth snapshots, SQLite/WAL storage, and feed-health/reconnect handling.

BUILD-004 is accepted on main as the read-only SIG tournament Realtime/state foundation. It subscribes once to the private tournament topic, persists complete market batches, checks topic revision continuity, and uses REST as the authoritative source of financial state. Its first credentialed tournament smoke exposed that the original all-open-exchange full-depth freshness fallback does not scale to the observed 237-exchange universe.

### BUILD-006 corrective architecture — IN REVIEW, PR #19

BUILD-006 preserves full-tournament Realtime capture but separates three concepts that BUILD-004 conflated:

    known exchange
        !=
    depth-tracked exchange
        !=
    currently trusted full-depth exchange

The candidate runtime is:

    authoritative tournament catalogue
            |
            +-- all known exchanges -> bulk price/BBO snapshots (<=100 IDs/request)
            |
            +-- explicit tracked subset -> authoritative full orderbooks
            |
            +-- tournament Realtime -> trades/bookDirty/settlement/revision for all
            |
            +-- one shared governed REST budget
                    HIGH: tracked invalidation/recovery
                    NORMAL: tracked seed/expiry-safety
                    BACKGROUND: broad scalar/metadata work

Untracked exchanges use the explicit UNTRACKED_DEPTH state. Tracked depth is usable only in TRACKED_TRUSTED; invalidation/freshness expiry moves it to TRACKED_UNTRUSTED before an awaited REST recovery.

The live governed client wraps the accepted BUILD-003 transport rather than replacing it. Every live HTTP attempt shares configurable pacing and a per-key 429 cooldown. The branch default is 3 requests/second as an operational deployment choice informed by live observation, not a SIG-published venue limit.

The 30-second tracked-depth freshness default is similarly project policy. SIG documents silent order expiry, while the aggregate exchange orderbook has no expirationDate metadata. BUILD-006 therefore retains a bounded fallback only for tracked books instead of polling the entire tournament.

BUILD-006 adds no strategy, fair value, mapping selection, risk decision, order placement/cancellation or portfolio path.

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
- BUILD-003 and BUILD-004 remain read-only. BUILD-006 / PR #19 corrects live REST/depth scaling on an unmerged branch and adds no order placement/cancellation path.
- EXPERIMENT-001A is public/read-only and isolated from normal application startup.
- Polymarket disconnect or receive/PONG liveness failure invalidates local book trust; reconnect performs an authoritative REST reseed before subsequent deltas are trusted.
- The 1-second research panel stores scalar top-of-book state only; top-20 depth defaults to a separate 60-second cadence, while normalized price-change deltas are durable at event observation time.
