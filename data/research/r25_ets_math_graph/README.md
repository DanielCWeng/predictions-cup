# R2.5 ETS mathematical graph and fill-acquisition spec

**Status:** semantic and mathematical specification complete; no price, fill, order-book, or resolved-outcome history acquired or examined.
**Provenance date:** 2026-09-29 UTC.
**Input freeze:** v2 candidate universe from base commit `09e9409`; Gamma descriptions re-fetched and hash-checked on 2026-09-29.
**Scope:** this package adds analysis artifacts under this directory. The frozen `data/research/r25_ets_v2_market_review/` package is unchanged.

## Read this first

This package separates logical relationships from statistical predictions. “Exact structural” means the contract indicator has an exact identity or subset relationship after settlement semantics match; it does not mean that a fair probability or executable price has been measured. Count and incomplete-joint surfaces are model-dependent or partially identified. Turnout, closeness, cross-race, county, voter-group, and differently defined chamber-control contracts remain predictive-only. Candidate markets that resolve candidacy, withdrawal, or primary matchups are rejected for the general-election winner target.

The resulting acquisition plan has 1,279 v2 rows: 587 `FILLS_P0`, 210 `FILLS_P1`, 34 `FILLS_P2`, 104 `FILLS_P3`, 334 `METADATA_ONLY`, and 10 `DROP`. P0 and P1 (797 markets) are the set marked `book_needed_later=true`. These are future recommendations, not acquired data.

## Artifacts

- [`ETS_RELATIONSHIP_TAXONOMY.json`](ETS_RELATIONSHIP_TAXONOMY.json): relationship definitions, equations, assumptions, and prohibited interpretations.
- [`ETS_MARKET_GRAPH.csv`](ETS_MARKET_GRAPH.csv): 5,191 v2 candidate edges plus 231 separate accepted-crosswalk baseline rows (5,422 total). Baseline source IDs are outside the v2 acquisition universe.
- [`ETS_SIG_ANCHOR_GRAPH.csv`](ETS_SIG_ANCHOR_GRAPH.csv): one row per 231 SIG anchors, including accepted source coverage, v2 links, identifiability, bounds, risks, and future history recommendation.
- [`ETS_COMPONENTS.json`](ETS_COMPONENTS.json): class totals, 237 Gamma event factors, connected-component summary, and exact rank calculations for four worked components.
- [`ETS_FILL_ACQUISITION.csv`](ETS_FILL_ACQUISITION.csv): one row per v2 candidate with semantic class, acquisition tier, intended later use, Gamma description hash, and book flag.
- [`ETS_GAMMA_SEMANTIC_AUDIT.csv`](ETS_GAMMA_SEMANTIC_AUDIT.csv): one row per candidate with endpoint provenance, event-family completeness, scope sibling counts, dates, rule-source metadata, and frozen description hash comparison.
- [`ETS_MATHEMATICAL_GRAPH_REPORT.md`](ETS_MATHEMATICAL_GRAPH_REPORT.md): mathematical interpretation and identification limits.
- [`ETS_HIGH_VALUE_COMPONENTS.md`](ETS_HIGH_VALUE_COMPONENTS.md): component review with the best structural pilot candidate and critical exceptions.
- [`ETS_FV_METHODS.md`](ETS_FV_METHODS.md): future fair-value estimation and fill/book collection methods; no weights or values fitted here.
- [`ETS_FALSIFICATION_PLAN.md`](ETS_FALSIFICATION_PLAN.md): outcome-blind preregistration plan and rejection criteria.

## Data and semantic provenance

The v2 candidate inventory contains 1,279 markets, 2,558 outcome tokens, 1,279 unique condition IDs, and 5,191 source-scope edges attached to 231 SIG anchors. The accepted crosswalk contributes 140 `EXACT`, 87 `DERIVED`, and 4 `NEAR` anchor mappings. The anchor graph marks 227 anchors as having exact-structural accepted baseline coverage (the exact and accepted derived partitions) and 4 as model-dependent (near chamber-control relations); this is source-coverage classification, not a measured FV.

Gamma was queried using `/markets?id=` and `/events?slug=`. The 1,279 market-ID requests ran from `2026-09-29T11:41:57Z` to `11:47:17Z`; 1,267 returned a market directly and 12 empty ID responses were recovered from their event children. The 237 event requests ran from `11:47:50Z` to `11:48:50Z`. Both endpoints were rate-limited below five requests per second. The compressed raw caches are 699,940 bytes (markets) and 1,122,570 bytes (events), below the 200 MB lane cap. All 1,279 Gamma descriptions match the frozen inventory SHA-256; no description drift was found. Gamma `resolutionSource` was blank for all 1,279, as was the frozen resolution-criteria SHA-256. Resolution semantics therefore come from the actual description text, not those metadata fields.

The 237 fetched event payloads contain 2,378 child markets, of which 1,099 are outside the v2 candidate set. Siblings were inspected only to understand whether a selected event family included `Other`, omitted outcomes, or relevant date variants. Off-scope siblings were not added to the graph or acquisition CSV. There are 32 event families whose candidate children have differing `endDate` values; do not use that field alone as a common settlement timestamp. Fifteen candidates have blank `groupItemTitle`. Neither `negRisk` nor `groupItemThreshold` alone proves that outcomes form a complete partition.

Outcome-blind handling follows [`docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md`](../../../docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md). No resolved-outcome table, historical price, fill, or book was read. Exact matrix ranks use rational arithmetic; only the compact component matrices are computed inline.

## Handoff: 11 decisions

1. **Surviving v2 contracts:** 1,269 survive semantic and mathematical classification; 10 candidacy, withdrawal, or primary-matchup contracts are dropped.
2. **Relationship strength:** by candidate market, 587 exact-structural, 210 model-dependent-structural, 472 predictive-only, and 10 rejected. By the 5,191 candidate edges, 1,194 exact-structural, 2,369 model-dependent-structural, 1,588 predictive-only, and 40 rejected.
3. **Priority fill-history tiers:** P0 587, P1 210, P2 34, P3 104. P0/P1 are the 797 later book-needed markets.
4. **Metadata-only retained markets:** 334 (321 local turnout thresholds, 12 state-chamber-control questions, and 1 pre-election House-majority question).
5. **Book/order-book work:** later book study is recommended for the 797 P0/P1 markets to measure spread, depth, quote age, and feasible execution. No book has been acquired.
6. **Accepted SIG baseline coverage:** 227 exact-structural anchors (140 direct exact plus 87 reviewed derived mappings) and 4 model-dependent near mappings. These accepted baseline Polymarket sources are outside the v2 candidate acquisition set; no numerical fair values are computed.
7. **Best component types:** same-race winner and party-margin bins; incomplete joint Governor×Senate outcomes; multi-race and chamber count surfaces; aggregate turnout and national House margin; closeness/comparison surfaces.
8. **Most dangerous semantics:** third-party/Other mass, pooled joint residuals, tie and bucket-boundary conventions, runoffs and recounts, party/caucus attribution, seat count versus chamber control, inconsistent candidate `endDate`, omitted event siblings, and stale rule wording.
9. **Invalid shortcuts:** Democratic Yes plus Republican Yes is not necessarily one; a closeness threshold gives no direction; turnout does not determine the winner; a national House popular-vote margin does not determine district winners; aggregate seat counts do not identify individual race probabilities without dependence assumptions; state-chamber control is not a governor result; candidacy/primary markets do not resolve general-election winners.
10. **Smallest useful pilot:** Georgia Senate×Governor's four listed D/R joint cells (`3729337`–`3729340`) against already accepted Georgia Governor margin-partition and Senate winner sources, with SIG targets 166/167 and 258/259 (exchange IDs 855/856 and 947/948). First check rule alignment and partial-identification bounds; later measure held-out SIG quote response. This is a research design, not an arbitrage claim.
11. **Next team action:** plan P0/P1 fill collection from the v2 set, separately scope the accepted direct/derived baseline sources already outside v2, then request later order-book snapshots for the 797 book-needed candidates. Freeze the Georgia pilot and failure criteria before accessing outcome history.
