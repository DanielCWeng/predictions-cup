# DATA-001 — Historical Replay Corpus

**Status:** READY FOR INDEPENDENT RE-REVIEW (routing fix) — not accepted  
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
Routing: V1 through the `2026-04-13T19` hourly partition, V2 from `2026-04-13T20:00Z` on
(`PMXT_V2_FIRST_HOUR`). The ticket originally routed V2 from 19:00, but the raw V2 19:00 file only
starts receiving at 19:42:26.6 while V1 covers the whole hour, which left a ~42.5-minute
regime-wide blind spot. The independent review moved the whole 19:00 hour to V1. No partition
mixes V1 and V2 rows.

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
`<FAMILY>/date=/hour=/events.parquet`. The checkpoint is per `(hour, family)` and records the raw
file's SHA-256 and row count. `--hour YYYY-MM-DDTHH` (repeatable) re-acquires single hours.

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

## Results (build at pipeline commit `0b1f5b3`)

The corpus manifest is `data/manifests/historical/data_001_corpus_manifest.json`, with identity
in `data_001_market_identity.csv` and quality in `data_001_corpus_quality.json`.
`validate` re-checked all 115 output files: hashes, row counts, schemas, window containment and
ordering. There were 0 problems and 0 rejected malformed rows.

| Regime | Markets candidate / with book / tokens with book | Depth snapshots | BBO changes | Trade prints | Fills | Regime-wide max silence | Book coverage | Fill coverage |
|---|---|---|---|---|---|---|---|---|
| `colombia_first_round` | 236 / 130 / 260 | 62,410 | 64,046,576 | 21,663 | 58,990 | 263 s | 05-29T00:00 → 06-02T23:59 | 05-29T00:00 → 06-02T23:58 |
| `colombia_runoff` | 236 / 42 / 84 | 29,534 | 12,576,636 | 13,699 | 34,671 | 590 s | 06-19T00:00 → 06-23T23:59 | 06-19T00:00 → 06-23T23:57 |
| `peru_first_round` | 327 / 133 / 266 | 102,784 | 21,607,086 | 32,630 | 265,128 | 145 s | 04-10T00:00 → 04-14T23:59 | 04-10T00:00 → 04-14T23:59 |
| `peru_runoff` | 327 / 45 / 90 | 112,277 | 21,324,303 | 41,908 | 122,149 | 243 s | 06-05T00:00 → 06-09T23:59 | 06-05T00:00 → 06-09T23:59 |
| `hungary_election` | 174 / 48 / 96 | 154,071 | 6,964,035 | 2,166 | 262,077 | 204 s | 04-05T00:00 → 04-14T23:59 | 04-05T00:00 → 04-14T23:57 |

Totals: 319 conditions / 638 tokens with book evidence, 126,518,636 BBO change rows, 461,076 depth
snapshots, 112,066 trade prints, 743,015 fills, 1.19 GB of ZSTD Parquet. There are no missing
source hours.

"Candidate" markets are the PolyLeviathan market families selected per research family. The
remainder had no rows in the archive during the window. They are placeholders ("Candidate J",
"Party B"), markets resolved before the window, or markets the archive never recorded.

### Known holes (read before using a regime)

1. **V2-only markets start at 2026-04-13T20:00 and lose their initial depth snapshot (routing
   fix side effect).** V1 recorded a fixed subset (Peru 40 conditions / 80 tokens, Hungary 6 / 12)
   every hour. V2 subscribed to 93 more Peru and 32 more Hungary conditions at 19:42:26, and its
   19:00 file holds their only subscription snapshots. With 19:00 routed to V1, those 186 Peru and
   64 Hungary tokens first appear at 20:00 as BBO changes. Their first depth snapshot now comes
   1.5–26 h later (Peru median 9.1 h, 4,108,909 changes before it). 36 Hungary tokens have **no**
   depth snapshot left in the window (`PRICE_ONLY` evidence only). BUILD-005 slices on these tokens
   fail closed until a snapshot arrives. This is recorded, not repaired; see the routing-fix section
   below.
2. **Hungary seat-count coverage.** Only 48 of 174 candidate markets (96 tokens) have book
   evidence, and 60 of those tokens have a depth snapshot. PMXT V1 did not record most seat-bin/seat-count markets. 45 tokens have fills
   (15,037 fills) but no book rows; a direct check of the raw election-night V1 file found none of
   their condition or token IDs among its 24,542 markets. They are listed with
   `book_available=false, fills_available=true`.
3. **Same-millisecond ambiguity.** 1,378,157 (token, millisecond) groups across the regimes hold
   BBO change rows with differing post-change best bid/ask (Colombia first round alone: 785,649).
   Their true order is unknowable, and replay applies each group as one BUILD-005 same-time batch.
4. **Sparse full snapshots.** Median depth snapshots per token over the whole window: Colombia
   first round 81, Colombia runoff 26, Peru first round 9, Peru runoff 334.5, Hungary 8. BBO
   changes before a token's first snapshot are counted per token and total 5,204,028 across regimes
   (994,015 before the routing fix; the increase is hole 1).
5. **Quiet-market silences.** Almost every token has at least one > 300 s silence. The largest
   single-token silence is 5,920 s (Colombia first round). These cannot be told apart from
   recorder outages except where the whole regime goes silent. Colombia runoff shows three
   6–10 minute regime-wide silences on 2026-06-19 around 04:00–05:00 UTC; no other regime has a
   regime-wide silence over 300 s.
6. **Anomalies surfaced, not repaired.** 7 crossed depth snapshots, 584 crossed BBO rows,
   47,551 empty-side snapshots, 2,195 Peru-runoff rows whose venue time is after the archive
   receive time, and 96,442 exact duplicate rows removed (mostly PMXT V1 redundant deliveries).
7. **Fills outside the book's observed span.** Hungary's median book/fill overlap is low because
   fills continue on tokens whose book evidence is thin or absent.


## Routing fix (2026-04-13T19 → PMXT V1)

**V1 19:00 source.** `https://archive.pendulumflow.com/pmxt/v1/polymarket_orderbook_2026-04-13T19.parquet`,
939,842,152 bytes, ETag `"f06c20c4469ae096d5ab7cf9883b6191-10"`, Last-Modified
`Wed, 19 Aug 2026 15:23:54 GMT`, 45,814,371 rows in 46 row groups (remote footer). The raw downloaded
file's SHA-256 is `2b972538b320fbf1ffaf5000e55923fa6806890f988884ff30b02287b251587c`. It was
re-acquired with `acquire --hour 2026-04-13T19`. Extracts: PER_2026 158,080 rows
(`a41369859ab01c73d8a2ccb3046f3d1c7e649804d006e96d80bda55c4d574472`), HUN_2026 2,708 rows
(`3a0925f4890747ee3aaaa5ab0ae3be1f84e88a7bd4a5d2d665cf4ab62635c6e7`). They span 19:00:00.3 →
19:59:59.8 (PER) and 19:00:02.9 → 19:59:58.7 (HUN); the largest receive gap inside the hour is 46 s
(PER) and 65 s (HUN). The replaced V2 extracts (PER `cf1b7646…`, HUN `4bd9d372…`) are kept outside
the extracts tree for audit only.

**Before/after** (final rows; "before" = build at `5925892`, routing V2 from 19:00):

| | Peru first round before → after | Hungary before → after |
|---|---|---|
| Depth snapshots | 101,458 → 102,784 | 153,887 → 154,071 |
| BBO changes | 21,634,169 → 21,607,086 | 6,968,448 → 6,964,035 |
| Trade prints | 32,980 → 32,630 | 2,217 → 2,166 |
| Conditions / tokens with book evidence | 133 / 266 → 133 / 266 | 48 / 96 → 48 / 96 |
| Tokens with any depth snapshot | 266 → 266 | 96 → 60 |
| Regime-wide max silence | 2,546.7 s → 144.9 s | 2,550.2 s → 203.6 s |
| Regime-wide material (> 300 s) silences | 1 → 0 | 1 → 0 |
| Largest regime-wide gap on 2026-04-13 | 2,547 s (18:59:59.9–19:42:26.6) → 54 s (03:05:12–03:06:07) | 2,550 s (18:59:56.4–19:42:26.6) → 89 s (16:59:15–17:00:45) |
| BBO changes before first snapshot | 9,463 → 4,118,372 | 123,238 → 224,342 |
| Crossed snapshots / crossed BBO rows | 2 / 248 → 2 / 248 | 4 / 136 → 4 / 136 |
| Empty-side snapshots | 6,722 → 6,604 | 4,590 → 4,542 |
| Ambiguous same-ms BBO groups | 409,019 → 402,467 | 11,511 → 11,508 |
| Rejects | 0 → 0 | 0 → 0 |

The 19:00 hour now holds V1 rows only: Peru 1,592 snapshots + 156,133 changes on 40 conditions /
80 tokens from 19:00:00.3; Hungary 260 snapshots + 2,438 changes on 6 / 12 from 19:00:02.9. The
~42.5-minute blind spot is gone, not just smaller. Across 18:00–21:00 the largest regime-wide gap is
now 47 s (Peru) and 68 s (Hungary), and the 20:00 V1→V2 handover leaves no gap (Peru: last V1 row
19:59:59.8, first V2 row 20:00:00.1; Hungary: 19:59:58.7 and 20:00:01.2). Every output outside the two regimes'
`date=2026-04-13` partitions (107 of 115 files, all Colombia and Peru runoff) is SHA-256 identical
to the before build; `corpus_quality.json` and `market_identity.csv` changed as expected.

**V1/V2 overlap audit** (diagnostic only; `scripts/data_001_overlap_audit.py`, window 19:42:26.6 →
20:00, both versions through `pmxt.normalize_extract`):

| | PER_2026 | HUN_2026 |
|---|---|---|
| Conditions V1 only / V2 only / both | 0 / 93 / 40 | 0 / 32 / 6 |
| Tokens V1 only / V2 only / both | 0 / 186 / 80 | 0 / 64 / 12 |
| Tokens with a depth snapshot V1 / V2 | 16 / 266 | 4 / 76 |
| Snapshots V1 / V2 | 366 / 266 | 80 / 76 |
| BBO changes V1 / V2 | 40,712 / 183,216 | 864 / 6,851 |
| Trade prints V1 / V2 | 0 / 350 | 0 / 51 |
| Median distinct instants per token-minute, shared tokens V1 / V2 | 16.1 / 10.0 | 2.9 / 2.0 |
| As-of BBO at minute marks, shared tokens: compared / disagree | 1,426 / 0 | 190 / 0 |
| Marks with a same-ms ambiguous state / V2-only state | 8 / 6 | 0 / 26 |

On markets both recorded, V1 and V2 agree on every aligned best bid/ask, and V1 is denser. The
difference is coverage: V1 never subscribed to the extra V2 markets. That is the cost in known hole 1.

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
112,542 rows). The two V1 `2026-04-13T19` extracts were produced by this repository's
`acquire --hour`. Every extract's SHA-256 is in `data_001_sources.json`, and every raw archive
hour's size, ETag and row count is recorded next to it.

## Kaggle reproduction

Private kernel `polyleviathan/sig-cup-data-001-build` (`scripts/kaggle/`) mounted three private
datasets: `sig-cup-pmxt-orderbook-extracts` (version with the V1 `2026-04-13T19` extracts),
`sig-cup-polyleviathan-fills` and `sig-cup-predictions-cup-code` (wheel of commit `0b1f5b3`).
Kernel version 3 rebuilt and validated the corpus. **All 115 output files were SHA-256 identical to
the local build** and the totals matched (`data/manifests/historical/data_001_kaggle_run.json`).
Version 2 had mounted the previous extracts version and stopped at the build's extract
`source_version` check, as intended.

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
