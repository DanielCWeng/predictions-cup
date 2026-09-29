# R2.5 ETS v2 market universe: team review (29-09-2026)

**Status: REVIEW. Not frozen; no history has been acquired for this list.**

This is the scope-v2 re-cut of the R2.5 ETS universe: **1,279 Polymarket markets / 2,558 tokens / 5,191 links to the 231 SIG anchors**. v1 had 18,173 markets / 36,346 tokens / 42,096 links.

| Office | Markets |
|---|---|
| House | 436 |
| Senate | 405 |
| Governor | 153 |
| Aggregate (control, seat counts, thresholds, combos, voter groups) | 285 |

Status: 1,267 open, 12 closed.

## Scope rules applied
- Included:
  - direct SIG-race adjacencies (the same contest as a SIG anchor);
  - genuine ETS aggregates;
  - general-election races with a specific structural or economic link to a SIG target.
- Excluded:
  - primaries and nominee markets (2,727);
  - placeholders, including unnamed slots and bare-letter "Will A win…" markets (9,967);
  - markets linked only through chamber control (4,111);
  - markets linked only by being in the same state (49).
- Chamber control is kept as an aggregate node only.
- No volume or liquidity filter.
- 40 borderline markets (dated forecast/odds contracts) are **not** in this list.
- ME-02 (both party sides) has no adjacent markets; every other ME-02 market is a placeholder or a primary.

## How to review
- **`REVIEW.md`**: grouped by office, then SIG race. Comment on a line in the PR, e.g. "fills + book", "fills only" or "drop".
- **`ETS_V2_MARKETS_FOR_REVIEW.csv`**: one row per market, with blank `want_fills`, `want_orderbook`, `priority` and `notes` columns. Fill them in and push to this branch.

Nothing is fetched for a market unless someone asks for it.

## Known possible misses (flag them)
The primary/candidacy filter is text-based. A few "announce a Senate run" candidacy markets slipped through, e.g. 520639, 523416 and 2305108. Mark any others you see with "drop".

## Provenance
- Source: the v1 freeze on `research/r25-ets-universe-data` (6031d95).
- Re-cut: /home/ubuntu/campaigns/r25ets_scope_20260929 on the Polyleviathan host. Its REPORT.md has the waterfall, the hand-checks and the ambiguous bucket.
- Summary and hashes: `ETS_V2_SUMMARY.json`.
