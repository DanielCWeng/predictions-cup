# EXPERIMENT-001A — Polymarket live data capture foundation

## Objective

Capture a trustworthy, synchronized, read-only Polymarket dataset before the SIG Cup so later
LOO-FAMILY, lead/lag, structural-probability and market-making research can be falsified on real
market data.

This experiment is **not** part of the production trading path. It has no wallet, private key,
signing, order placement, paper fills, PnL, fair-value model or execution logic.

## Public documentation checked

Checked against current Polymarket documentation on 2026-09-25:

- Market Data / Real-Time Data — raw market WebSocket URL, subscription frame, PING/PONG,
  `book`, `price_change`, `last_trade_price`, `tick_size_change`, and incremental
  subscribe/unsubscribe frames.
- Market Data / Discover Markets — Gamma `markets/keyset` pagination and `next_cursor` →
  `after_cursor` behavior.
- Market Data / Market Details — market/token identity, JSON-encoded outcome/token arrays,
  trading constraints and NegRisk/event metadata.
- API Reference / Get order books (request body) — public `POST /books` authoritative book
  summaries.
- API Reference / Rate Limits — Gamma and CLOB public-data limits.

The current market feed documents a book hash but no sequence/checksum recovery protocol. This
implementation preserves the hash but does not invent sequence semantics. Disconnect recovery
instead invalidates all local books and re-seeds them from `POST /books` before stream deltas are
trusted again.

## Reference implementation lessons inspected

The following `DanielCWeng/polymarketwhale` files were reviewed:

- `Sonar/websocket_service.py`
- `Sonar/orderbook_service.py`
- `Sonar/subscription_service.py`
- `Sonar/polymarket_service.py`
- `Sonar/jobs/prices.py`

Reused ideas:

- persistent WebSocket rather than repeated polling;
- bounded application-heartbeat send timeout for half-open connections;
- full snapshot establishes book state, then incremental changes mutate it;
- Decimal price/size state and zero-size level deletion;
- stable always-on market subscriptions;
- keyset-style Gamma pagination and deliberate metadata projection;
- separate feed activity from book/trade activity.

Deliberately rejected:

- frontend reference-count subscription machinery;
- reconnect-on-every-small-subscription-change where current Polymarket supports incremental
  subscribe/unsubscribe;
- trading SDK credentials, wallets and position state;
- Telegram, OCI/Postgres, PnL, trader analytics and legacy bot configuration;
- copying large modules wholesale.

There is no runtime dependency on `polymarketwhale`.

## Capture flow

```text
Gamma markets/keyset
        ↓
2026 U.S. election universe selector + manual overrides
        ↓
market / condition / event / NegRisk / token identity
        ↓
CLOB POST /books authoritative initialization
        ↓
market WebSocket (persistent)
        ↓
in-memory Decimal books
        ↓
normalized trade events + 1-second research snapshots
        ↓
SQLite/WAL local research store
```

## Market universe

The default selector is `us_elections_2026`. It requires an active, non-closed market with a 2026
(or election-cycle 2027 end-date) signal plus U.S. election structure such as Senate, House,
Congress, governor, chamber control, seat counts or midterms.

Manual include/exclude identifiers can override the heuristic. Market ID, condition ID or token ID
may be used. Excludes win over includes.

The selector is intentionally conservative and machine-readable; it is not the final SIG ↔
Polymarket semantic mapping layer. The full Polymarket universe is never subscribed.

## Metadata preserved

For selected markets the recorder stores:

- Gamma market ID and condition ID;
- question and slug;
- aligned outcomes and CLOB token IDs;
- active/closed/accepting-orders state;
- start/end timestamps;
- resolution source where supplied;
- event ID/slug/title;
- NegRisk flag and NegRisk market/group identifier where supplied;
- market group, group-item title/threshold and parent-event slug where supplied;
- minimum tick size and order size;
- useful liquidity/volume metadata.

This is enough raw identity/group structure for later information-family construction without
pretending this ticket has built the semantic graph.

## Order-book correctness

- `POST /books` or a full `book` message establishes trusted state.
- `price_change` deltas are ignored and counted when a token is not initialized.
- BUY changes update bids; SELL changes update asks.
- size `0` removes the level.
- internal price and size values use `Decimal`.
- emitted bids are descending and asks ascending.
- current tick-size changes update initialized local book state.
- any disconnect invalidates all local books; authoritative REST books are fetched again before
  the next subscription is sent.

No undocumented sequence/checksum mechanism is assumed.

## Timestamps

Every captured live observation keeps two clocks when the source supplies its own time:

- `source_timestamp` — the Polymarket event/book timestamp after wire parsing;
- `observed_at` — the UTC time this process observed the payload.

Research must not replace `observed_at` with exchange event time when estimating lead/lag or signal
half-life. Periodic snapshots also store `recorded_at`, the local sampler time, so a quiet but
healthy book still produces a clock-time panel.

## Storage

Storage is a local SQLite database in WAL mode. Default path:

```text
data/polymarket_capture.sqlite3
```

Tables:

- `polymarket_markets`
- `polymarket_tokens`
- `polymarket_book_snapshots`
- `polymarket_trades`
- `ingestion_health`

Market/token metadata upserts idempotently. Snapshot and trade history appends across ordinary
process restarts. Trade rows with a transaction hash are de-duplicated by token + transaction
hash.

### Book depth choice

The default research snapshot stores top **20** levels per side as compact JSON arrays in one row
per token per sample. Best bid, best ask, midpoint and spread are also stored as scalar columns.
This retains materially useful executable depth while avoiding an unbounded full-depth-per-second
row explosion. The depth is configurable.

## Recorder cadence

Default normalized snapshot cadence is 1 second. This is not REST polling every second:

```text
WebSocket updates → in-memory book → 1-second snapshot sampler
```

Gamma metadata refresh defaults to every 300 seconds. Universe additions are REST-seeded before
being incrementally subscribed; removals are unsubscribed and invalidated.

## Reconnect behavior

The WebSocket uses Polymarket's documented application heartbeat: text `PING` every 10 seconds,
with a shorter bounded local send timeout so a half-open connection cannot stall the event loop.
Reconnects use bounded exponential backoff plus jitter.

On every connection attempt:

1. all prior local books are invalidated;
2. the desired token set is fetched from public `POST /books`;
3. every desired token must be initialized successfully;
4. only then is the market WebSocket subscription sent.

Current Polymarket incremental subscribe/unsubscribe frames are used for ordinary universe changes,
so small metadata changes do not force reconnect storms.

## Health / observability

Health records and logs separate:

- WebSocket connected state;
- last feed message/heartbeat activity;
- last valid book update;
- last book change;
- last trade;
- selected market/token counts;
- reconnect count and last cause;
- parse failures;
- unknown event count;
- delta-before-initialization count;
- Gamma refresh status;
- snapshot recorder status;
- storage failures.

A quiet market is therefore not automatically classified as a stale feed.

## Raw-data policy

Normalized metadata, books, trades and health records are durable. Malformed or unknown live
messages are counted and a bounded payload representation is logged for diagnosis. The subsystem
does not archive every raw WebSocket message forever.

## Configuration

```text
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED
PREDICTIONS_CUP_POLYMARKET_GAMMA_BASE_URL
PREDICTIONS_CUP_POLYMARKET_CLOB_BASE_URL
PREDICTIONS_CUP_POLYMARKET_WS_URL
PREDICTIONS_CUP_POLYMARKET_SNAPSHOT_INTERVAL_SECONDS
PREDICTIONS_CUP_POLYMARKET_BOOK_DEPTH
PREDICTIONS_CUP_POLYMARKET_GAMMA_PAGE_LIMIT
PREDICTIONS_CUP_POLYMARKET_GAMMA_REFRESH_SECONDS
PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH
PREDICTIONS_CUP_POLYMARKET_UNIVERSE
PREDICTIONS_CUP_POLYMARKET_INCLUDE_IDS
PREDICTIONS_CUP_POLYMARKET_EXCLUDE_IDS
```

No Polymarket credential exists or is required.

## Run

The normal application shell remains unchanged and does not start any external feed.

Explicit recorder:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

Explicit public-data smoke test (manual only; never CI-required):

```bash
python -m predictions_cup.external.polymarket.recorder --smoke-test
```

The smoke test is read-only: it performs Gamma discovery and a small public CLOB book request,
submits no order and modifies no external state.

## Known limitations

- The default election universe is a metadata/text heuristic plus manual overrides, not the final
  semantic mapping graph.
- Gamma page size `100` is a conservative configurable operating choice inherited from prior live
  experience; current documentation is authoritative for the keyset cursor contract and does not
  need that value to be treated as a mathematical/API guarantee.
- Public `last_trade_price` events are stored when emitted; this ticket does not claim an
  independently reconciled exchange-wide historical trade tape.
- No exchange sequence/checksum recovery is implemented because the current raw market-stream
  contract does not document one.
- SQLite is deliberately local research storage, not a production server database.
- No retention/compaction job is implemented yet; disk growth must be observed during the first
  multi-day capture.

## Intentionally not implemented

No:

- SIG market mapping automation;
- LOO-PRICE or LOO-FAMILY fair value;
- coherent probability solver;
- hard-arbitrage scanner;
- simulated fills or paper trading;
- queue model;
- PnL;
- market making;
- risk sizing;
- order submission;
- wallets, keys or signing;
- frontend/dashboard.

## EXPERIMENT-001B handoff

After enough clean live data exists, the next experiment should validate capture quality and then
run the preregistered LOO-FAMILY / structural-FV research against held-out target families. It
should consume this dataset rather than adding strategy logic to the recorder.
