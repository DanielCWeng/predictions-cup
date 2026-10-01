# BUILD-004 — SIG Realtime State, REST Reconciliation and Replayable Capture

**Status:** MERGED / ACCEPTED — PR #12  
**Branch:** `build/004-sig-realtime-state`  
**Accepted on main:** yes — 25 September 2026

## Purpose

BUILD-004 added the accepted read-only live SIG state foundation required before replay-driven
strategy work. Realtime is used for low-latency events and invalidation. Authoritative financial
state comes from REST.

The subsystem deliberately contains no strategy, fair value, mapping, opportunity scanning,
risk decision, order placement, cancellation, portfolio accounting or live-trading path.

## Tournament selection

Capture never guesses a tournament. Supply an explicit tournament UUID through
`--tournament-id` or `PREDICTIONS_CUP_TOURNAMENT_ID`.

Accessible tournaments can be enumerated without subscribing:

```bash
python -m predictions_cup.sig.capture --list-tournaments
```

## Realtime token and subscription

The existing authenticated read-only SIG adapter calls `POST /realtime/token` and strictly
validates the documented `token`, `expiresAt`, `supabaseUrl`, `anonKey` and
`channels.user` fields. Token/key values use secret types and are never logged.

**Superseded 2026-10-01 (fix/sig-realtime-no-deliveries).** SIG's published contract now states
that the tournament-wide `tournament:{tournament_id}` topic no longer carries market updates.
Live probing confirmed it: that topic accepted the private join but delivered zero messages while
the tournament traded, whereas per-market topics delivered `market_batch` immediately.

Capture and MAKE now subscribe one private channel per unsettled tournament market:

```text
tournament:{tournament_id}:market:{market_id}
```

Supabase rejects more than 100 channels per socket (`ChannelRateLimitReached: Too many
channels`), so topics are sharded across sockets at 90 channels each. Revision continuity is
topic-local (`revision <= last` is a duplicate; `previousRevision > last` is a gap), and a gap,
malformed batch or `resyncRequired` batch on a market topic recovers only that market over REST.
Batches may also carry `books[]` (versioned full books), trade `id`/`sequence`, and the compact
`{resyncRequired, delivery}` form; these validate but pushed books are not yet applied to state.

## Trust and revision rules

Topic delivery continuity is determined only from `delivery.revision` and
`delivery.previousRevision`.

- before first subscription: mint a Realtime token, then perform one authoritative REST seed;
- first valid revision after that seed becomes the topic delivery baseline;
- duplicate revision: delivery is recorded for diagnostics, but state effects are ignored;
- continuous revision: accept when `previousRevision == last_accepted_revision`;
- revision gap: mark state untrusted and REST-resynchronize before continuing;
- reconnect: invalidate and REST-resynchronize;
- token refresh: invalidate and REST-resynchronize;
- socket error: invalidate and REST-resynchronize;
- malformed batch: invalidate and REST-resynchronize.

`sourceSequenceFrom` and `sourceSequenceThrough` are persisted as engine provenance only. They
are never used as the topic-local gap counter.

## Authoritative reconciliation

Initial state enumerates the selected tournament market catalogue through REST, persists those
authoritative market snapshots, derives the exchange universe from them, and fetches an
authoritative orderbook for every exchange whose market is still open. Closed/settled markets
retain authoritative market state but do not trigger pointless orderbook reads.

A `bookDirty` item says that a book changed; it does not contain authoritative depth. Dirty
exchange IDs are deduplicated within the batch, marked untrusted, then refreshed with the
existing read-only REST orderbook method. State returns to trusted only after a successful
response and identity check.

A `marketSettled` signal invalidates all known exchanges for that market and calls
`GET /markets/{id}` for authoritative market status/settlement state. If the market is now
closed/settled, stale local books are cleared and the exchanges become trusted against that
authoritative non-open state without an unnecessary orderbook request.

SIG explicitly documents that **order expiry emits no Realtime event**. The same contract notes
that expired orders are filtered at authoritative read time, but this implementation's
aggregated exchange-orderbook DTO has no `expirationDate` metadata from which to schedule an
exact expiry timer. BUILD-004 therefore applies a bounded freshness rule to trusted open books:
after `PREDICTIONS_CUP_SIG_REALTIME_OPEN_BOOK_REFRESH_SECONDS` since the last successful REST
book observation (default 30 seconds), the next one-second maintenance tick marks the exchange
untrusted **before** issuing an authoritative REST refresh. A failed refresh leaves it
untrusted. Closed/settled markets are excluded.

This is deliberately not tight polling: Realtime remains the normal invalidation path, while the
bounded REST refresh exists only to cover silent expiry and other no-event book aging.

If a REST read fails, the exchange stays untrusted and
`reconciliation_failure_count` increments. The subsystem does not manufacture apparently valid
state from stale books.

## Timestamp semantics

Three timing concepts stay distinct:

1. **source/event time** — `executedAt`, `at` or other timestamp supplied by SIG;
2. **Realtime observed_at** — timezone-aware UTC time when this process receives the broadcast;
3. **REST response observed_at** — timezone-aware UTC time immediately after the authoritative
   REST orderbook response is received.

Canonical orderbooks use the REST response observation timestamp because the documented SIG
orderbook response contains no server timestamp. Realtime observation time is never overwritten
with event time.

## Runtime state

Per exchange, the BUILD-004 baseline state keeps:

```text
exchange_id
market_id
tournament_id
trusted / untrusted
canonical bounded OrderBook
best_bid / best_ask
last relevant trade
last REST observation time
last Realtime observation time
last accepted revision
last reconciliation time
```

This is capture/replay state, not fair-value or strategy state.

## Persistence

Default path:

```text
data/sig_realtime.sqlite3
```

SQLite uses WAL and normalized tables:

- `realtime_deliveries` — topic revision metadata, correlation ID, source sequence provenance,
  local observation time;
- `realtime_trades` — exact documented market-batch trade fields plus revision and local
  observation time;
- `book_dirty_events` — documented book invalidations with source `at` and local observation time;
- `market_settled_events` — documented settlement/refund invalidations with source `at`,
  stamped outcome, and local observation time;
- `market_observations` — authoritative REST market status/settlement snapshots with REST
  observation time, refresh reason, and triggering Realtime revision;
- `book_observations` — authoritative bounded canonical book, best bid/ask, REST observation
  time, refresh reason, triggering Realtime revision;
- `trust_transitions` — trusted/untrusted/reconciling lifecycle records.

The default retention window is 14 days and is configurable with
`PREDICTIONS_CUP_SIG_REALTIME_RETENTION_DAYS`. BUILD-004 does not create an unbounded raw-message
archive.

## Health

`SigRealtimeStateEngine.health_snapshot()` exposes:

```text
connected
last_realtime_receive
last_valid_batch
last_rest_reconciliation
revision_gap_count
reconnect_count
reconciliation_failure_count
bounded_book_refresh_count
market_count
trusted_exchange_count
untrusted_exchange_count
```

No dashboard is introduced.

## Explicit operation

Normal application startup remains finite, network-free and non-trading:

```bash
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

Realtime capture requires explicit invocation and a read credential:

```bash
python -m predictions_cup.sig.capture --tournament-id <TOURNAMENT_UUID>
```

Finite live smoke command:

```bash
python -m predictions_cup.sig.capture \
  --tournament-id <TOURNAMENT_UUID> \
  --run-seconds 30
```

No live credentialed smoke was performed by the BUILD-004 implementation environment. The first later credentialed tournament smoke observed 237 open exchanges and showed that BUILD-004's tournament-wide 30-second full-depth fallback could not scale within the conservative observed REST envelope. BUILD-006 / PR #19 is the corrective tracked-depth/governor ticket. This note remains the historical BUILD-004 contract rather than rewriting that accepted history.

## Automated validation

The BUILD-004 tests cover strict documented token/batch shapes, a single initial authoritative
seed, source-sequence-vs-revision separation, duplicate delivery handling, revision-gap full
resynchronization, `bookDirty` refresh, authoritative settlement-market refetch, stale-book
clearing for settled markets, failed reconciliation remaining untrusted,
reconnect/token-refresh/socket-error lifecycle transitions, quiet-connection maintenance,
silent-expiry stale-depth removal, normalized SQLite/WAL persistence and retention.

Repository CI runs:

```bash
ruff check .
mypy
pytest
python -m predictions_cup.app
python -m predictions_cup.app --smoke-test
```

PR #12 was independently accepted and merged on 25 September 2026. BUILD-006 / PR #19 now carries the separate live scalability correction; it must not be treated as accepted until independently reviewed and merged.
