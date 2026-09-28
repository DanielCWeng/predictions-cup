# DATA-003 — SIG Actual-Market Fills (+ DATA-002 Fee Evidence)

**Status:** MERGED / ACCEPTED — PR #48
**Branch:** `data/003-sig-actual-fills`
**Depends on:** the accepted MAPPING-001 crosswalk (`data/mappings/sig_polymarket_2026.json`,
`origin/main` commit `f01212c00402002c111fc1b9488cf55a289af7b1`) for market scope, and reuses the
DATA-002 fee/refund/rebate attribution method — see
[Relationship to DATA-001 and DATA-002](#relationship-to-data-001-and-data-002). Neither existing
dataset is mutated.

## Purpose

DATA-003 is a frozen dataset of actual Polymarket `OrderFilled` rows scoped to the accepted SIG Cup
mapping, joined with DATA-002-style fee/refund/rebate evidence for the same fills. Where DATA-001 is
a broad five-election-family historical replay corpus and DATA-002 is broad fee evidence for that
same population, DATA-003 is scoped the other way: it starts from the SIG↔Polymarket mapping itself
(140 EXACT, 87 DERIVED, 4 NEAR records; the 6 NO_TRADE records are excluded) and pulls every fill on
the 693 conditions / 1,386 tokens that mapping resolves to.

**Fill-complete, not merely fee-transfer-complete.** Every scoped fill has a row, including fills
with zero matched fee (`fee_evidence` explains why): 132,928 fee rows for 132,928 fills.

## Scope and source

- Mapping source: `predictions-cup` `origin/main:data/mappings/sig_polymarket_2026.json`, commit
  `f01212c00402002c111fc1b9488cf55a289af7b1`.
- Scoped mapping: 693 binary conditions and 1,386 YES/NO tokens, with **zero overlap** against the
  DATA-001/DATA-002 `sisterreq` five-family inventory — DATA-003 is a disjoint population, not a
  re-export.
- Trades source: canonical daily Parquet objects in OCI bucket `polymarket-bot-state`, prefix
  `trades/`. The live object listing (rather than the stale pre-backfill inventory file, which had
  113/115) confirmed all 115 requested daily objects, 2026-02-28 through 2026-09-21.
- Custody source: canonical daily Parquet objects in the same bucket, prefix `custody/`; custody
  objects are available through 2026-09-19.
- Extraction cutoff: `2026-09-28T18:52:11Z`.

## Fill rows

Each fill retains the canonical trade fields (`timestamp`, `tx_hash`, `log_index`, `condition_id`,
`token_id`, `side`, `price`, `size_shares`, `value_usd`, `maker_address`, `taker_address`), plus
participant/counterparty aliases, YES/NO outcome, `window_id`, and the join keys `sig_market_id`,
`mapping_class`, `mapping_direction`. `block` is null for every row — the canonical trades schema
has no block field. Exchange facts (`is_exchange_taker`, `order_is_match_taker_order`,
`exchange_if_taker_is_exchange`, `exchange_address`) are preserved raw; no strategy label or
inferred actor classification is added, matching DATA-002's non-goals.

Fills are deduplicated on `(tx_hash, log_index, token_id)`. 132,928 unique fill rows, 0 duplicate
key groups, 0 conflicting-content groups, 0 cross-partition duplicates.

## Fee and rebate method

DATA-003 reuses the DATA-002 attribution rules exactly (adjacent log distance 2, token match for
outcome-token fees, taker-order selection when adjacent candidates are ambiguous, sender-owner
nearest-fill fallback for charges, nearest preceding owner/token match for refunds) against the
same transaction hashes as the scoped fills:

- **Custody-leg conservation:** 43,388 attributed + 69 unattributed = 43,457 selected legs (PASS).
- **Fee evidence:** `custody_not_ingested` 6,257, `fee_charged` 43,388, `no_fee_leg_observed`
  83,283. `no_fee_leg_observed` means the custody scan ran and found no matching charge — it is not
  a maker/taker classification.
- **Rebates:** the daily distributor payout table covers 2026-04-15 through 2026-07-31 from the
  complete DATA-002 reference stage (30,731 scoped-wallet rows, 11,080 wallets), then
  2026-08-01 through 2026-09-19 streamed directly from OCI with no whole custody day downloaded to
  scratch. No scoped rebate payouts observed in live custody after 2026-06-25. Rebate rows are
  wallet-day evidence, not per-fill allocations, matching DATA-002.
- **Custody gap:** no custody object exists for 2026-09-20 or 2026-09-21; those fill rows carry
  `fee_evidence=custody_not_ingested` with null charge/refund/USD-equivalent amounts. No fee value
  is imputed.
- **`unattributed_fee_legs/`:** scoped custody fee/refund legs not attributable under the DATA-002
  rules — shipped raw per day, not dropped.

`family` in the fees table is the package label `SIG_CUP`; SIG market identity and mapping class
come from the supplied mapping keys. Current Gamma market fee-snapshot columns are retained with
their DATA-002 names (current configuration evidence, not historical).

## Corpus layout

```text
sigfills_20260928/
  README.md
  COVERAGE.md
  MANIFEST.json
  fills/window_id=<W>/day=<D>/part-*.parquet
  fees/window_id=<W>/day=<D>/part-*.parquet
  rebates/utc_date=<D>/part-*.parquet
  unattributed_fee_legs/day=<D>/part-*.parquet
  polymarket_fee_regimes.csv / .parquet
  custody_coverage.csv
  sig_market_coverage.csv
  token_coverage.csv
  condition_coverage.csv
  zero_fill_conditions.csv
```

341 files, 24,250,196 bytes uncompressed. `MANIFEST.json` lists every file's `path`, `bytes`,
`rows` and `sha256` (except itself), plus `gates`, `source`, `mapping_source`, `windows_utc_inclusive`
and `timestamp_coverage`. The corpus itself lives outside Git, on Kaggle — GitHub holds only the
manifest, quality evidence and Kaggle-run record, the same split DATA-001/DATA-002 use.

### Window coverage

19 windows (`B0` + `W01`..`W18`) spanning 2026-02-28 through 2026-09-21; see
`data/manifests/fills/data_003_manifest.json.windows_utc_inclusive` for exact per-window UTC
bounds and `COVERAGE.md` (shipped inside the Kaggle package) for the per-window fill-row breakdown.

### Fills by mapping class

| Mapping class | Mapping records | Fill rows |
|---|---:|---:|
| EXACT | 140 | 48,615 |
| DERIVED | 87 | 27,481 |
| NEAR | 4 | 56,832 |

## Validation

The DATA-002 fee pipeline was rerun on all five-family tokens for `2026-04-15` and compared with
the published `fees_HUN_2026.parquet` day slice: 4,588 rows matched exactly, including schema and
values (`data/manifests/fills/data_003_manifest.json.gates.self_validation`).

## Manifest and quality evidence

- `data/manifests/fills/data_003_manifest.json` — the full 340-file package manifest (path, bytes,
  rows, sha256 per file) plus gates, source object-range evidence, mapping provenance and window
  bounds, copied verbatim from the package's own `MANIFEST.json`.
- `data/manifests/fills/data_003_quality.json` — the gate summary (fill-complete, dedup,
  custody-leg conservation, mapping scope, trade/custody object coverage, rebate coverage,
  self-validation, known gaps) extracted from the manifest for quick review.
- `data/manifests/fills/data_003_kaggle_run.json` — the Kaggle upload record (dataset ref, method,
  byte counts, resulting status).

## Kaggle hosting

Uploaded as a new, **private**, standalone dataset — DATA-001 and DATA-002 are not mutated:

- **Dataset:** `polyleviathan/sig-cup-data-003-sig-actual-fills`
  (https://www.kaggle.com/datasets/polyleviathan/sig-cup-data-003-sig-actual-fills)
- Static upload (`kaggle datasets create -p sigfills_20260928 --dir-mode zip`) of the extracted
  `sigfills_20260928/` tree from `sigfills_20260928.tar.zst`; per-directory zips for `fills/`,
  `fees/`, `rebates/`, `unattributed_fee_legs/`, loose top-level files otherwise. No Kaggle-kernel
  build — there are no mounted input datasets to record.
- Confirmed `ready` via `kaggle datasets status` immediately after creation.
- Full record: `data/manifests/fills/data_003_kaggle_run.json`.

## Relationship to DATA-001 and DATA-002

DATA-001/DATA-002 cover five broad election families (`US_2024`, `COL_2026`, `PER_2026`,
`HUN_2026`, `CAN_2025`) with zero SIG-mapping filtering. DATA-003 covers the SIG Cup mapping's own
231 markets / 1,370 tokens-with-rows, which the manifest confirms has **zero overlap** with that
five-family population:

```text
DATA-001 / DATA-002                          DATA-003
five election families, unfiltered           SIG Cup mapping scope only
(sisterreq inventory)                         (sig_polymarket_2026.json)
        │                                              │
        └──────────── disjoint populations ────────────┘
                  (0 condition / 0 token overlap)
```

DATA-003 reuses the DATA-002 fee-attribution method and regime table
(`polymarket_fee_regimes.csv`/`.parquet`, copied unchanged) rather than redefining it, so a reader
already familiar with DATA-002's `fee_evidence` semantics needs no new mental model here.

## Non-goals

No hard-coded `is_taker`/`is_maker` label. No maker-fill attribution manufactured from a wallet-day
rebate payment. No translation of a custody gap into a maker signal. No mutation of DATA-001 or
DATA-002. No strategy, alpha claim, or classification rule.
