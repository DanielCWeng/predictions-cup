# DATA-004 — ETS P0/P1 Fill Corpus

**Status:** BLOCKED / IN REVIEW; Kaggle upload is pending and research use is gated by the quality report.
**Branch:** `data/data004-ets-p0p1-fills`
**Scope source:** frozen ETS acquisition, market graph, and SIG anchor graph on
`research/r25-ets-math-graph`.

## Purpose and frozen universe

DATA-004 captures the full available OrderFilled history for the frozen P0/P1 adjacent and ETS
universe. The scope is exactly 298 selected markets: 210 P0 and 88 P1. P0 contains 147
count/comparison/multi-race-count markets, 13 Senate-by-House count-joint cells, and 50 race-level
joint cells. P1 contains 88 direct winner markets refreshing accepted DERIVED baselines. Metadata
for the full 1,279-market / 2,558-token frozen universe is retained with the delivery; fill history
is limited to P0/P1. The 797 `book_needed_later` markets remain metadata only.

The acquisition file, `ETS_MARKET_GRAPH.csv`, and `ETS_SIG_ANCHOR_GRAPH.csv` are hashed into the
manifest. Gamma metadata was refetched and cached in the campaign lane; all market IDs, condition
IDs, 596 outcome tokens, and outcome labels are checked against that snapshot. The corpus retains
the graph relationship IDs/classes, acquisition class/tier, intended use, and SIG anchor links.

## Source, time, and storage

The read-only source is Polyleviathan's canonical daily OCI trades lake in
`polymarket-bot-state/trades/`; custody Parquet in `polymarket-bot-state/custody/` supplies block
numbers by transaction hash. The exporter measures both live object inventories and scans every
available trade-object date from the earliest selected market creation date through the latest
listed trade date. Per-date source object names, ETags, sizes, hashes where supplied, custody
availability, matched rows, and missing dates are recorded in the manifest.

Fill timestamps retain the source lake's UTC block timestamp at one-second resolution. Reconstruct
same-token chronology with `(block_number, log_index, token_id)`; transaction hashes are not used as
an ordering proxy. Block numbers are never synthesized: missing or conflicting custody joins stay
null, carry explicit provenance, and fail the ordering gate.

DATA-004 v1 is immutable after its manifest is published at
`research/data004_ets_p0p1/v1/` in OCI. **Delivery is Kaggle (pending upload); the OCI prefix is the
immutable source copy.** The prepared private package is at
`/home/ubuntu/inbox/data004_20260929/sig-cup-data-004-ets-p0p1-fills/`, with archive
`sig-cup-data-004-ets-p0p1-fills.tar.zst`. `data/manifests/fills/data_004_kaggle_run.json` records
the exact upload command and archive hash. Compact review evidence is checked into Git under
`data/research/data004_ets_p0p1/v1/`; the OCI manifest gives the exact object paths, byte counts,
row counts, and hashes. The Kaggle package includes the full acquisition inventory, Gamma semantic
audit, market graph, anchor graph, relationship taxonomy, and component summary. DATA-003 remains
on its accepted Kaggle location.

## Fill grain and semantics

Deduplicate on `(tx_hash, log_index, token_id)`. Report raw source rows separately from retained
unique rows, including duplicate key groups and conflicting content. Each retained row carries the
source fill fields, market/event and outcome metadata, P0/P1 and graph/anchor links, and trade and
custody object provenance.

Keep the source signed-order `side` verbatim. Reconstruct economic BUY/SELL direction only where
the data dictionary's maker/taker order semantics make it determinate; mark the remainder UNKNOWN.
Participant and counterparty follow the signed-order ownership convention, and order role uses only
the registered exchange addresses. Unknown role or direction is explicit missing semantics, never
imputed classification.

## Frozen DATA-003 pairing plan

`data004_baseline_pairing.json` records the 231 accepted SIG anchors with observed DATA-003 rows,
their accepted EXACT/DERIVED/NEAR mapping classes, source market IDs, condition IDs, aligned
outcomes and token IDs, and the linked DATA-004 P0/P1 market IDs. The later comparison joins on
accepted SIG exchange IDs and preserves crosswalk direction plus each graph relation/equation.
Derived mappings retain their component identities. The plan is not a fill extract: DATA-003
source histories were not reacquired or copied into DATA-004.

## Required gates

The manifest and quality summary report every gate outcome and every failure:

- exact 298-market / 298-condition / 596-token universe, Gamma outcome-token alignment, and graph
  and anchor links;
- fill-key completeness and zero duplicate/conflicting output keys after deduplication;
- block-number provenance mix, zero imputation, and strictly increasing block/log order per token;
- core-field missingness, explicit unknown role/direction counts, price bounds `[0,1]`, and
  condition/token identity;
- maker/taker size conservation where a match-equivalent source group can be reconstructed;
- fill timestamps bounded by Gamma creation/end metadata;
- complete source-date and per-market/condition/token/event/date coverage (the market table carries
  both market and condition IDs; they are one-to-one in this frozen universe), with zero-fill markets
  listed individually and classified as new, no trading, or source-gap exposure.

The source does not expose a stable common match ID for every participant-order row. The exporter
therefore checks maker/taker size symmetry in transaction/token/timestamp/price groups and records
that grouping as a limitation; it does not claim exact match-level reconciliation from that proxy.

## Measured quality outcome

The immutable v1 source manifest has status `BLOCKED_QUALITY_GATE`. It contains 231,964 raw and
deduplicated rows. The frozen 298-market / 298-condition / 596-token scope, Gamma outcome alignment,
market-graph links, key completeness, deduplication, price bounds, field completeness, market
lifecycle checks, trade-object date coverage, and per-market coverage pass. All 298 markets have
fills; the zero-fill market file is empty.

Across the 231,964 rows, economic direction is BUY 150,984 / SELL 80,980 / UNKNOWN 0, all
reconstructed from signed-order side. The role split is MAKER 135,334 / TAKER 96,630. P0 and P1
contribute 86,224 and 145,740 rows. `fill_composition.json` records these counts.

Custody objects are available through 2026-09-19. The trade lake has selected fills on 2026-09-20
(1,625 rows) and 2026-09-21 (3,364 rows), with no custody object on either date. In total 4,989
rows retain null block numbers with `MISSING_CUSTODY_OBJECT` provenance and no imputation. Those
rows fail block provenance and cannot participate in the strict per-token block/log ordering gate;
there are no ordering inversions among rows with block numbers. The match-equivalent symmetry audit
covers 42,415 paired groups, with 4,418 size mismatches and 124,293 one-sided groups. Since the
source lacks a common participant-order match ID, these results remain an unresolved failed gate
rather than a claim of exact per-match conservation.

Trade-object coverage is complete across 348 dates, 2025-10-09 through 2026-09-21, with no missing
trade-object date in the scan range. Fill rows occur on 343 dates, from 2025-10-14 through
2026-09-21. The complete daily inventory, including five scanned days with no selected rows, is
`source_day_inventory.csv`.

## Research boundary

This is a data acquisition and quality review only. No R3 predictive or fair-value analysis, outcome
inspection for graph redesign, order-book history, RPC, Postgres writes, or order execution is part
of DATA-004. A passing corpus gate permits the separately frozen R3 pilot to be specified; it does
not itself establish an edge or strategy.

## Evidence

- `data/research/data004_ets_p0p1/v1/data004_manifest.json` — immutable corpus, source, schema,
  counts, coverage, provenance, and gate record.
- `data/research/data004_ets_p0p1/v1/data004_quality.json` — pass/fail summary with all failures.
- `data/research/data004_ets_p0p1/v1/data004_baseline_pairing.json` — frozen baseline plan.
- `data/research/data004_ets_p0p1/v1/{market,condition,token,event,date}_coverage.csv` — coverage tables.
- `data/research/data004_ets_p0p1/v1/source_day_inventory.csv` — source-object inventory and all 348 scan dates.
- `data/research/data004_ets_p0p1/v1/full_universe_manifest.json` — hashes and row counts for the six full-universe metadata files (1,279 candidates).
- `data/research/data004_ets_p0p1/v1/fill_composition.json` — economic direction, source side, role, and tier counts from the Parquet rows.
- `data/manifests/fills/data_004_kaggle_run.json` — pending manual upload, archive hash and command.
- `/home/ubuntu/campaigns/data004_20260929/REPORT.md` — seven-item delivery and recommendation.
