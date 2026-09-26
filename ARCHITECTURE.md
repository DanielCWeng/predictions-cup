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

EXPERIMENT-001A additionally provides the accepted **public read-only Polymarket research capture semantics** under `predictions_cup.external.polymarket`: public Gamma/CLOB discovery, authoritative REST book seeding, persistent market WebSocket ingestion, normalized event-time book-change/trade persistence, a lean 1-second top-of-book panel, slower bounded depth snapshots, and feed-health/reconnect handling. Its accepted `main` baseline used SQLite/WAL for research history; BUILD-007 replaces that physical writer globally on its branch with ZSTD Parquet for high-frequency history plus small operational SQLite.

BUILD-004 is accepted on main as the read-only SIG tournament Realtime/state foundation. It subscribes once to the private tournament topic, persists complete market batches, checks topic revision continuity, and uses REST as the authoritative source of financial state. Its first credentialed tournament smoke exposed that the original all-open-exchange full-depth freshness fallback does not scale to the observed 237-exchange universe.

### BUILD-006 corrective architecture — ACCEPTED, PR #19

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

Untracked exchanges use the explicit UNTRACKED_DEPTH state. Tracked depth is usable only in TRACKED_TRUSTED; invalidation/freshness expiry moves it to TRACKED_UNTRUSTED before an awaited REST recovery. Broad scalar/BBO state is independently fail-closed: a bulk missingIds result clears any prior scalar latest-price/BBO/spread values and observation timestamp so stale fallback state cannot remain resident.

The live governed client wraps the accepted BUILD-003 transport rather than replacing it. Every live HTTP attempt shares configurable pacing and a per-key 429 cooldown. The branch default is 2 requests/second. The prior blocking curl + sleep probe demonstrated only roughly 2.0–2.4 request starts/second, so a 3 requests/second default is not treated as validated until a fixed-cadence live probe schedules request starts independently of response time. No numeric SIG venue limit is published in the supplied contract.

The 30-second tracked-depth freshness default is similarly project policy. SIG documents silent order expiry, while the aggregate exchange orderbook has no expirationDate metadata. BUILD-006 therefore retains a bounded fallback only for tracked books instead of polling the entire tournament.

BUILD-006 adds no strategy, fair value, mapping selection, risk decision, order placement/cancellation or portfolio path.

### BUILD-007 supervised capture correction — IN REVIEW, PR #20

The accepted EXPERIMENT-001A broad election selector and SQLite/WAL capture remain historical
baseline capability on `main`, but the live EC2 soak demonstrated that shape is not suitable as
the always-on Cup research lane. At 3,160 markets / 6,320 tokens, the 1-second scalar panel alone
would produce 546,048,000 rows/day; the live SQLite file was growing at roughly 53 GB/day at one
measured point, with a multi-GB WAL.

BUILD-007 therefore changes the **supervised** Polymarket path, not the research cadence:

    accepted SIG ↔ Polymarket mapping IDs from runtime.env
                    |
                    v
          strict include-only selector
       (market / condition / token IDs)
                    |
                    +--> Gamma metadata + small operational SQLite
                    |      markets / tokens / health only
                    |
                    +--> CLOB + WebSocket research streams
                           |
                           +--> 1s scalar/BBO
                           +--> price/book changes
                           +--> public trades
                           +--> periodic depth
                                   |
                                   v
                         immutable ZSTD Parquet shards

The systemd service passes `--require-explicit-universe` and the installer requires
`PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS`. Empty or unresolved IDs fail closed. No Cup market
ID is hard-coded. The intended source is the independently accepted LIVE-MAPPING-GATE-001
crosswalk; until that crosswalk exists, the supervised Polymarket service is not production-ready.
A deliberately bounded explicit public test universe may still be used for the required ARM64 EC2
Parquet smoke/soak; that test set is not production mapping evidence.

High-frequency streams are written to short immutable Parquet shards using temp file -> fsync ->
atomic rename. Source/event timestamps and local observation timestamps remain distinct. SQLite is
kept only for low-volume operational metadata and health. Legacy broad-soak SQLite files are not
deleted or migrated automatically.

Replay remains backward compatible with legacy Polymarket SQLite captures and can also load the
new Parquet research directory. The new path accepts the operational SQLite separately so health /
disconnect events remain available to replay without putting research history back into SQLite.

Raw Parquet trade capture is deliberately at-least-once. Hashed trades carry a deterministic
event identity derived from token ID + transaction hash, and canonical Parquet replay validates
that identity and keeps only the first observation. This preserves EXPERIMENT-001A's accepted
hashed-trade de-duplication semantics across duplicate delivery, shard boundaries and process
restarts without pretending the file writer itself is transactional exactly-once.

Gamma metadata maintenance follows the PolyLeviathan degraded-metadata boundary: initial discovery
fails closed, but after a valid universe is resident a later Gamma discovery/selection failure
records degraded status and preserves last-good WebSocket/book/snapshot capture. Partial refresh
state is never applied. Local persistence failures still surface.

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
- BUILD-003, BUILD-004 and BUILD-006 remain read-only; BUILD-006 is accepted and adds no order placement/cancellation path.
- EXPERIMENT-001A is public/read-only and isolated from normal application startup.
- Polymarket disconnect or receive/PONG liveness failure invalidates local book trust; reconnect performs an authoritative REST reseed before subsequent deltas are trusted.
- The 1-second Polymarket research panel remains high cadence; BUILD-007's supervised candidate narrows the universe instead of reducing cadence and routes the panel/deltas/trades/depth to ZSTD Parquet rather than unbounded SQLite.
