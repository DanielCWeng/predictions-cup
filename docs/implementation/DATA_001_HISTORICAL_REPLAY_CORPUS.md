# DATA-001 — Historical Replay Corpus

**Status:** READY FOR INDEPENDENT REVIEW — not accepted  
**Branch:** `data/001-historical-replay-corpus`  
**Replaces:** HIST-DATA-001 / PR #17 (closed unmerged; lessons reused, branch not revived)

## Purpose

DATA-001 produces trusted, evidence-graded historical Polymarket observations for five preselected
2026 election regimes, in a form the accepted BUILD-005 replay and EXPERIMENT-002 machinery consume
without a parallel replay path. It does not test whether any strategy makes money.

GitHub holds code, manifests, checksums, quality evidence and a tiny fixture. The corpus itself
lives outside Git (local disk or Kaggle).

## Regimes

Windows are half-open `[start, end)` in UTC (`end` = the day after the ticket's `23:59:59Z`).

| Regime | Family | Window | Election |
|---|---|---|---|
| `colombia_first_round` | COL_2026 | 2026-05-29 → 2026-06-03 | 2026-05-31 |
| `colombia_runoff` | COL_2026 | 2026-06-19 → 2026-06-24 | 2026-06-21 |
| `peru_first_round` | PER_2026 | 2026-04-10 → 2026-04-15 | 2026-04-12 |
| `peru_runoff` | PER_2026 | 2026-06-05 → 2026-06-10 | 2026-06-07 |
| `hungary_election` | HUN_2026 | 2026-04-05 → 2026-04-15 | 2026-04-12 |

No initialization data before a window is included. BUILD-005 slices are fail-closed at their
start, and the corpus follows the same rule: the first depth snapshot for each token arrives
inside the window, and BBO changes before it are counted in the quality report.

## Sources and what the bytes actually show

`data/manifests/historical/data_001_sources.json` is the machine-readable inventory. It includes
every raw archive hour's URL, size, ETag, row count and row-group count, read from the remote
Parquet footer by HTTP range request, plus the SHA-256 of every extract and PolyLeviathan file.

### PendulumFlow PMXT order-book archive

Hourly Parquet at `https://archive.pendulumflow.com/pmxt/{v1,v2}/polymarket_orderbook_<hour>.parquet`.
Routing follows the ticket: V1 before `2026-04-13T19:00Z`, V2 from then on.

| | PMXT V1 | PMXT V2 |
|---|---|---|
| Recorder | mirror of a third-party capture | PendulumFlow's own capture |
| Rows | `market_id`, `update_type`, JSON `data` | 16 typed columns |
| Book messages | `book_snapshot` with `[[price,size]]` levels | `book` with JSON levels (asks listed high→low) |
| Deltas | `price_change`: `change_price/size/side` + post-change BBO | `price_change`: `price/size/side` + post-change BBO |
| Trades | none | `last_trade_price` with transaction hash |
| `timestamp_received` | recorder receive time (ms) | archive receive time (ms) |
| Venue event time | **none** | `timestamp` (ms) |

The V1 finding matters. The payload `timestamp` equals `timestamp_received` to the millisecond
(verified on real rows), so it is the recorder's receive time, not a venue time.
`timestamp_created_at` lags by 4 ms to 7 s and is an archive write time. V1 rows therefore have a
null `source_timestamp`.

### Exact-token extracts

`python -m predictions_cup.historical acquire` downloads each unique archive hour once, keeps only
raw rows for candidate tokens (V2 by `asset_id`, V1 by condition `market_id`; V1 token filtering
is exact in the normalization stage), preserves the raw columns and writes
`<FAMILY>/date=/hour=/events.parquet`. The checkpoint is per `(hour, family)`.

### PolyLeviathan fills

`fills_<FAMILY>.parquet` / `markets_<FAMILY>.parquet` from the `sisterreq_fills_20260926`
export. There is one row per `(tx_hash, log_index, token_id)`; the exporter reports 0 duplicate
keys. `price` / `size_shares` / `value_usd` are **binary float64 at the source**. Timestamps are
block time at 1-second resolution. `side` comes from the upstream fills table and its aggressor
semantics are not established. `is_exchange_taker` only says the taker address is an exchange
contract.

## Evidence grades

Grades reflect the strongest claim the bytes justify, not the dataset description.

| Stream | Grade | Why |
|---|---|---|
| `books/depth_snapshots` | `BOOK_SNAPSHOT` | full L2 depth from a venue book message: exact state at that observable time |
| `books/book_changes` | `PRICE_ONLY` | per-level deltas carrying post-change best bid/ask (L1). Same-millisecond ties are unordered, so this is not exact event replay |
| `books/trades` | `TRADE_FILL` | venue trade prints (V2 only) |
| `fills` | `TRADE_FILL` | on-chain fills, a separate evidence family |

Nothing is graded `FULL_EVENT_REPLAY`. PendulumFlow classifies PMXT as snapshot grade:
millisecond receive times tie, the original order within a tie is unrecoverable, and export order
is not stable. The corpus therefore makes **no** claim to queue reconstruction, cancellation
timing, maker fill probability, passive quote survival or exact sub-millisecond sequencing.

## Corpus layout and contract

```text
<corpus>/schema_version=1/
  <regime>/books/depth_snapshots/date=YYYY-MM-DD/part-0.parquet
  <regime>/books/book_changes/date=YYYY-MM-DD/part-0.parquet
  <regime>/books/trades/date=YYYY-MM-DD/part-0.parquet
  <regime>/books/tick_size_changes/date=YYYY-MM-DD/part-0.parquet   (metadata; not replayed)
  <regime>/books/rejects/date=YYYY-MM-DD/part-0.parquet             (malformed rows, visible)
  <regime>/fills/date=YYYY-MM-DD/part-0.parquet
  corpus_manifest.json  corpus_quality.json  market_identity.csv
```

The book streams **are the accepted BUILD-007 Polymarket research schemas**, with two provenance
columns appended (`source_version`, `evidence_grade`). `<regime>/books` is passed straight to
BUILD-005 `load_polymarket_capture`. No adapter or forked loader is needed. Following BUILD-007,
`market_id` holds the condition ID. The numeric Polymarket market ID is in the identity manifest
and the fill stream.

Mappings:

- PMXT `book` / V1 `book_snapshot` → `depth_snapshots`. Levels are re-ordered bids-descending /
  asks-ascending by exact decimal price. `best_bid/ask`, `midpoint` and `spread` are derived
  exactly. `recorded_at = state_observed_at = observed_at` proxy.
- `price_change` → `book_changes` (`side`, `price`, `size`, post-change `best_bid/ask`).
- V2 `last_trade_price` → `trades`, with `event_id = hashed_trade_event_id(token, tx_hash)`.
- Fills use their own schema (`fill_id = tx_hash:log_index:token_id`), not BUILD-007 `trades`.
  BUILD-005 de-duplicates trades on `(token_id, transaction_hash)`, which would silently collapse
  distinct on-chain fills sharing a transaction.

All prices and sizes are decimal text. Book prices never pass through binary float; fill
floats are rendered with Arrow's shortest round-trip text and not otherwise computed on.

## Timestamps and no-lookahead

| Field | Meaning |
|---|---|
| `observed_at` / `recorded_at` (books) | `HISTORICAL_PROXY_ARCHIVE_RECEIVE_TIME`: when the archive's recorder received the message. It is **not** a time this project possessed the data, and recorder latency is unknown |
| `source_timestamp` (V2 books) | venue event time; retained, never used for ordering |
| `source_timestamp` (V1 books) | null (no venue time exists) |
| `source_timestamp` (fills) | on-chain block time |
| `observed_at` (fills) | `BLOCK_TIME_PROXY`, equal to block time |

Rules enforced:

- Every row lies inside its regime window; `validate` re-checks this per output file.
- Files are ordered by observable time. Ties are ordered by row content, never by archive export
  order. Only rows identical in every column are dropped (idempotent redundant deliveries).
- No interpolation, no fabricated quiet-period snapshots, no backward fill, no centered windows
  and no nearest-timestamp joins. The only cross-stream statistic (book/fill overlap) compares a
  fill time against the book's observed span and never feeds replay.
- Replay ordering remains BUILD-005's: `observed_at` only, same-time events applied as one batch.

## Quality evidence

`corpus_quality.json` has, per regime and per token: first/last timestamp, row counts, distinct
observation instants, median/p95 observation interval, maximum gap, material gaps (> 300 s: count,
total and largest three), duplicates removed, malformed rejects by reason, crossed snapshots and
crossed BBO changes, empty-side incidence, ambiguous same-millisecond BBO groups, changes before
the first snapshot, venue-time-after-receive-time rows, median/p95 depth levels, fill counts and
book/fill overlap. Regime-level entries add regime-wide silences (no token observed at all), the
stronger outage signal, plus the pipeline row counts: raw extract rows, rows matching tokens,
outside/inside window, duplicates removed, rejects and final rows, separately for books and fills.

Intervals are measured between distinct observation instants. Quantiles are exact below 1 s and
bucketed above (100 ms to 60 s, then 1 s). Maximum and material gaps are exact.

## Results (build at pipeline commit `5925892`)

The corpus manifest is `data/manifests/historical/data_001_corpus_manifest.json`, with identity
in `data_001_market_identity.csv` and quality in `data_001_corpus_quality.json`.
`validate` re-checked all 115 output files: hashes, row counts, schemas, window containment and
ordering. There were 0 problems and 0 rejected malformed rows.

| Regime | Markets candidate / with book / tokens with book | Depth snapshots | BBO changes | Trade prints | Fills | Regime-wide max silence | Book coverage | Fill coverage |
|---|---|---|---|---|---|---|---|---|
| `colombia_first_round` | 236 / 130 / 260 | 62,410 | 64,046,576 | 21,663 | 58,990 | 263 s | 05-29T00:00 → 06-02T23:59 | 05-29T00:00 → 06-02T23:58 |
| `colombia_runoff` | 236 / 42 / 84 | 29,534 | 12,576,636 | 13,699 | 34,671 | 590 s | 06-19T00:00 → 06-23T23:59 | 06-19T00:00 → 06-23T23:57 |
| `peru_first_round` | 327 / 133 / 266 | 101,458 | 21,634,169 | 32,980 | 265,128 | **2,547 s** | 04-10T00:00 → 04-14T23:59 | 04-10T00:00 → 04-14T23:59 |
| `peru_runoff` | 327 / 45 / 90 | 112,277 | 21,324,303 | 41,908 | 122,149 | 243 s | 06-05T00:00 → 06-09T23:59 | 06-05T00:00 → 06-09T23:59 |
| `hungary_election` | 174 / 48 / 96 | 153,887 | 6,968,448 | 2,217 | 262,077 | **2,550 s** | 04-05T00:00 → 04-14T23:59 | 04-05T00:00 → 04-14T23:57 |

Totals: 319 conditions / 638 tokens with book evidence, 126,550,132 BBO change rows, 459,566 depth
snapshots, 112,467 trade prints, 743,015 fills, 1.19 GB of ZSTD Parquet. There are no missing
source hours.

"Candidate" markets are the PolyLeviathan market families selected per research family. The
remainder had no rows in the archive during the window. They are placeholders ("Candidate J",
"Party B"), markets resolved before the window, or markets the archive never recorded.

### Known holes (read before using a regime)

1. **V1→V2 cutover silence, 2026-04-13 18:59:56 → 19:42:26 UTC (~42.5 min), Peru first round and
   Hungary.** The ticket routes hours from 19:00 to PMXT V2, but the raw V2 file for 19:00 starts
   receiving at 19:42:26.6 (read from its Parquet footer). V1 continues until 2026-04-16T05 and
   would cover the gap. Changing the routing rule is left to review. The silence is recorded as a
   regime-wide material silence, not filled.
2. **Hungary seat-count coverage.** Only 48 of 174 candidate markets (96 tokens) have book
   evidence. PMXT V1 did not record most seat-bin/seat-count markets. 45 tokens have fills
   (15,037 fills) but no book rows; a direct check of the raw election-night V1 file found none of
   their condition or token IDs among its 24,542 markets. They are listed with
   `book_available=false, fills_available=true`.
3. **Same-millisecond ambiguity.** 1,384,712 (token, millisecond) groups across the regimes hold
   BBO change rows with differing post-change best bid/ask (Colombia first round alone: 785,649).
   Their true order is unknowable, and replay applies each group as one BUILD-005 same-time batch.
4. **Sparse full snapshots.** Median depth snapshots per token over the whole window: Colombia
   first round 81, Colombia runoff 26, Peru first round 10, Peru runoff 334.5, Hungary 9. BBO
   changes before a token's first snapshot are counted per token and total 994,015 across regimes.
5. **Quiet-market silences.** Almost every token has at least one > 300 s silence. The largest
   single-token silence is 5,920 s (Colombia first round). These cannot be told apart from
   recorder outages except where the whole regime goes silent. Colombia runoff shows three
   6–10 minute regime-wide silences on 2026-06-19 around 04:00–05:00 UTC.
6. **Anomalies surfaced, not repaired.** 7 crossed depth snapshots, 584 crossed BBO rows,
   47,717 empty-side snapshots, 2,195 Peru-runoff rows whose venue time is after the archive
   receive time, and 96,077 exact duplicate rows removed (mostly PMXT V1 redundant deliveries).
7. **Fills outside the book's observed span.** Hungary's median book/fill overlap is low because
   fills continue on tokens whose book evidence is thin or absent.


## Commands

Local run (the one used to produce the committed manifests):

```bash
python -m predictions_cup.historical acquire --fills <polyleviathan-dir> --output <extracts-dir> --scratch <tmp>
python -m predictions_cup.historical sources --orderbooks <extracts-dir> --fills <polyleviathan-dir> --output data/manifests/historical/data_001_sources.json --remote-footers
python -m predictions_cup.historical build --orderbooks <extracts-dir> --fills <polyleviathan-dir> --output <corpus-dir>
python -m predictions_cup.historical validate --corpus <corpus-dir>
python -m predictions_cup.historical smoke --corpus <corpus-dir> --regime <regime> --target-token <token> --reference-token <token> --start-at <ISO> --end-at <ISO> --output data/manifests/historical/data_001_experiment002_smoke.json
python -m predictions_cup.replay --polymarket-db <corpus-dir>/schema_version=1/<regime>/books
```

`acquire` accepts `--worker K --workers N` for parallel downloads. The archive caps each connection
at about 20 MB/s, and each worker's memory is bounded by 25k-row batches.

Kaggle uses the same entrypoint with mounted inputs. The repository is private, so the kernel
installs a wheel built from the reviewed commit (`python -m pip wheel --no-deps .`) and attached
as a Kaggle dataset, rather than cloning GitHub with a token:

```bash
pip install /kaggle/input/<predictions-cup-wheel-dataset>/predictions_cup-0.1.0-py3-none-any.whl
python -m predictions_cup.historical build \
  --orderbooks /kaggle/input/<pmxt-extracts-dataset> \
  --fills /kaggle/input/<polyleviathan-fills-dataset> \
  --output /kaggle/working/historical_replay_corpus
python -m predictions_cup.historical validate --corpus /kaggle/working/historical_replay_corpus
```

## Provenance of the extracts used for this build

The extracts used for the recorded build were produced before this branch by a standalone script
with the same filter semantics. Re-running this repository's `acquire` stage on two archive hours
reproduced them exactly: same schema, same rows, same row order. The hours were
`2026-04-05T00` (PMXT V1, Hungary, 25,576 rows) and `2026-06-19T00` (PMXT V2, Colombia,
112,542 rows). Every extract's SHA-256 is in `data_001_sources.json`, and every raw archive
hour's size, ETag and row count is recorded next to it.

## Determinism

Given identical input bytes and configuration, `build` produces identical canonical output. The
manifests contain no wall-clock build time, rows are content-ordered, one row group is written per
source hour, and the synthetic regression builds twice and compares manifests. Output SHA-256
hashes depend on the PyArrow writer version; logical equality does not.

## Tests

`tests/test_historical_corpus.py` (synthetic) covers V1/V2 normalization, level ordering, exact
decimals, malformed/crossed handling, window and token filtering, content-deterministic ordering,
fill separation, determinism, tamper detection and fail-visible float prices.
`tests/test_historical_fixture.py` loads the committed real-data fixture through unmodified
BUILD-005 and runs EXPERIMENT-002 on it.

## Non-goals

No strategy, tuning, alpha claims, SIG fill modelling, queue simulation, execution or trading. No
expansion beyond the five regimes. No fabricated book states.
