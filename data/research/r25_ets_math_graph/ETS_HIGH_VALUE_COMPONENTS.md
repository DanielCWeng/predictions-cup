# High-value ETS components

This review identifies where structural relations are strongest and where later data could have research value. It does not rank markets by historic returns, liquidity, or predicted alpha; no such history was accessed.

## 1. Georgia Senate × Governor: smallest useful structural pilot

Gamma event `georgia-senate-and-governor-combo-20260810210432707` has four v2 cells:

| Polymarket ID | Selected joint cell |
| ---: | --- |
| 3729337 | Democratic Governor / Democratic Senate |
| 3729338 | Republican Governor / Republican Senate |
| 3729339 | Democratic Governor / Republican Senate |
| 3729340 | Republican Governor / Democratic Senate |

The event also contains an explicit `Other` outcome outside the frozen v2 set. The four Yes indicators do not form a complete D/R-only state space. On the fine D/R/Other 3×3 state space, their matrix has rank 4; adding normalization gives rank 5, leaving four dimensions in the five pooled Other states unidentified. The coarsened Other residual is `1 - p_DD - p_DR - p_RD - p_RR`.

There are already accepted baseline sources outside v2: Georgia Governor D Yes is derived from margin markets `3365590`–`3365594`, Governor R Yes from `3365585`–`3365589`; Senate D/R winner sources are market IDs `630692`/`630693`. These map to SIG markets 166/167 and 258/259 (exchange IDs 855/856 and 947/948). Keep those as a separately scoped source request; they are not rows in the v2 fill CSV.

The first later study should check description-level identity, same-time quote consistency, and LP bounds for the Governor/Senate marginal probabilities using all five coarsened joint categories. Only after that should a frozen holdout test ask whether joint-market changes precede SIG quote changes. Georgia has four selected cells, two target offices, and accepted baseline mappings, so it keeps the manual rule audit small. It is not a locked hedge or arbitrage: `Other` prevents complement assumptions and execution/settlement are not matched.

## 2. U.S. Senate × House seat-count surface

The 13 selected markets `2683257`–`2683269` represent a complete set of coarsened count categories across four Senate and four House bands. Its rank is 13 on 16 fine cells, with 12 grouped probability degrees and 3 unobserved within-band degrees. It is a high-value aggregate dependence surface: selected category probabilities constrain joint party seat-count uncertainty without imposing independence.

The band boundaries straddle majority cutoffs (Senate 50–52 straddles 51; House 208–222 straddles 218). A downstream system must not treat majority control as exact from this surface. Use bucket-support linear programs for bounds and report the residual uncertainty. Do not invert count bins to individual Senate or House races without an explicit model.

## 3. Republican national governorship count

Markets `907686`–`907692` provide seven disjoint bands over a count from 0 through 50. Their grouped probabilities identify exact aggregate probabilities such as `P(K >= 26)` by summing `<26`-complement buckets, but 44 within-band fine-count degrees remain unresolved and no specific state race is identified. The three additional “more/same number of governorships” contracts are comparison events and should be treated as a different model-dependent surface, not more count bins.

## 4. Same-race winner and margin families

The strongest same-race source families are the 138 D/R winner contracts, the single independent winner, and 448 party-specific local margin bins. A party-specific margin interval is a subset of the corresponding party-win event. A complete, aligned collection of disjoint bins can be summed into the winner probability. Before doing so, verify every sibling, `Other` and tie outcome, boundary convention, candidate party, and runoff/recount scope; a v2 subset alone may not cover the settlement space.

The accepted SIG crosswalk already contains 227 exact-structural baseline anchors (140 direct exact rows and 87 reviewer-accepted derived rows). The baseline PM sources do not intersect the v2 market set. Treat them as a baseline acquisition lane to be planned separately from the 1,279 v2 rows.

## 5. National margin, turnout, and predictive feature surfaces

The 13 U.S. House popular-vote margin bins (`1395450`–`1395462`) plus a full-event `Other` sibling identify the coarsened national signed-margin distribution. This can be an aggregate feature or a test of the national-margin baseline; it does not reveal district outcomes.

The 11 national House-turnout candidates (`1399389`–`1399399`) span the documented voting-Representative turnout bands. Their description excludes Delegates and the Resident Commissioner and specifies boundary handling. Turnout does not structurally identify any winner. Of the remaining turnout candidates, 321 local thresholds are metadata-only; retain their IDs and descriptions but do not request fills under this phase.

The 10 voter-group outcome contracts, 31 race closeness thresholds, 45 cross-race comparison contracts, and 28 county contracts are P2/P3 feature candidates. Any forecast use needs an outcome-blind, event-clustered, held-out test. Keep the 12 state-chamber-control and one pre-election House-majority contracts metadata-only due target/rule mismatch.

## 6. Semantic hazards to carry into downstream code

- D and R wins are disjoint, but `No` on one party is not necessarily `Yes` on the other.
- Event sibling counts and group labels do not prove the selected v2 list is complete; inspect event children and rule descriptions.
- Party, ballot affiliation, independent/caucus attribution, tie treatment, recounts, runoffs, and bucket boundary rules define the event.
- `endDate` varies among children in 32 event groups. Preserve market-level dates but derive the actual resolution horizon from each rule.
- `Florida Senate` may refer to a regularly scheduled seat or the special election. Keep source labels distinct.
- `negRisk` and `groupItemThreshold` are metadata only; do not use them as partition proofs or economic cutoff definitions.
- The graph is connected through shared count targets, but that is not a claim that every linked race has a fully observed joint distribution.
