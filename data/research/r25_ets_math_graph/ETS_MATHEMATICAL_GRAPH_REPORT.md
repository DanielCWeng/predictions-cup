# ETS mathematical graph report

## Scope and result

This is a semantic and mathematical review of the 1,279-market R2.5 ETS v2 candidate universe. The graph has 5,191 candidate links to 231 SIG anchors and 231 separate accepted-crosswalk baseline rows. The candidate-market classes are:

| Class | Markets | Meaning |
| --- | ---: | --- |
| Exact structural | 587 | Matched event identity, a party-specific margin interval, or another deterministic relation under the stated settlement assumptions. |
| Model-dependent structural | 210 | A deterministic count/joint function exists, but recovering target marginals needs a dependence model or leaves partial-identification bounds. |
| Predictive-only | 472 | A possible feature relationship has no logical implication for the SIG event. |
| Rejected | 10 | Candidacy, withdrawal, or primary matchup does not resolve the 2026 general-election winner event. |

The corresponding 5,191 source-scope edges are 1,194 exact-structural, 2,369 model-dependent-structural, 1,588 predictive-only, and 40 rejected. The machine-readable row-level classifications and their evidence are in [`ETS_MARKET_GRAPH.csv`](ETS_MARKET_GRAPH.csv) and [`ETS_FILL_ACQUISITION.csv`](ETS_FILL_ACQUISITION.csv).

“Exact” here is a property of event indicators, not a claim that a Polymarket trade is a fair probability or that the two venues can be locked together. All formulas are conditional on matching office, contest, election round, party/candidate definition, cutoff, tie/recount/runoff procedure, and resolution source. The SIG DTO has status and settlement fields but does not carry the full rule text. Accepted mapping semantics are inherited from `MAPPING-001` and remain subject to rule alignment.

## Mathematical classes

Let `X` denote the complete latent election state and `A(X)` a contract's Yes indicator. An exact mapping requires `A_SIG(X) = A_PM(X)` for every settlement state in the shared domain. Exact disjoint buckets obey

\[
P(A)=\sum_{k\in S}P(B_k)
\]

only if the `B_k` are mutually exclusive, cover every state in `A`, and use aligned rules. For a non-exhaustive list, `sum_k P(B_k) <= 1`; the residual is real probability mass, not a reason to renormalize the selected outcomes. For distinct Democratic and Republican win indicators, `A_D A_R=0` under a single-winner contest, but generally `A_D+A_R<1` when an independent/Other/tie outcome exists.

For an incomplete joint market, a listed cell `J` contained in target event `A` provides `P(J) <= P(A)`. If only two marginals are known, the Fréchet bounds are

\[
\max(0,P(A)+P(B)-1)\leq P(A\cap B)\leq\min(P(A),P(B)).
\]

A count contract is a function of race indicators, for example `K = sum_i I_i`. It constrains the joint distribution and may bound a constituent probability, but it does not point-identify each `P(I_i)` absent additional assumptions. Any later LP must include the actual count buckets, residual categories, and support restrictions. Independence is an optional sensitivity model, never a default identity.

Predictive-only links have no such equation. A turnout threshold is an event about turnout, a within-margin question is directionless, a relative-closeness contract is a joint comparison, and a county result or a different state-chamber control rule is not the statewide target event.

## Contract inventory by mathematical role

The 587 exact-structural candidates comprise 448 party-specific local race margin-bin contracts, 138 Democratic/Republican local race-winner contracts, and one independent-candidate winner contract. The 210 model-dependent structural candidates comprise 137 count-distribution cells, 3 Governor-count comparisons, 7 multi-race count cells, 13 Senate×House count-table cells, 40 two-race joint cells, 8 three-race joint cells, one four-race cell, and one “any Republican win in a Biden–Trump state” event.

The 472 predictive-only candidates comprise 321 local turnout thresholds, 11 complete national House-turnout bins, 13 national House popular-vote margin bins, 10 voter-group outcome questions, 31 within-margin questions, 45 cross-race comparison questions, 28 county outcomes, 12 state legislative chamber-control questions, and one pre-election House-majority question. Counts are mutually exclusive in the semantic partition.

## Worked rank and identification checks

The exact computation inputs, matrices, ranks, and proofs are in [`ETS_COMPONENTS.json`](ETS_COMPONENTS.json). Rank is not a quality score; it counts independent linear constraints on the categorical latent-state probabilities.

### Georgia Senate × Governor

The four selected v2 cells (`3729337`–`3729340`) are D/D, D/R, R/D, and R/R, with the first coordinate denoting Governor and the second Senate. The full Gamma event has one additional explicit `Other` contract. On the fine state space `{D,R,O} × {D,R,O}`, there are nine probabilities and eight simplex degrees of freedom. The 4×9 observation matrix has rank 4; with normalization, rank is 5, so four fine-state dimensions remain unidentified. At the coarsened five-category level the four listed probabilities identify the residual `Other` probability, but they still do not allocate Other among the five fine states.

For Democratic Governor probability, the valid bound is

\[
p_{DD}+p_{DR}\leq P(G_D)\leq p_{DD}+p_{DR}+p_O.
\]

The analogous Democratic Senate bound is `p_DD+p_RD <= P(S_D) <= p_DD+p_RD+p_O`. Do not silently force the event to a 2×2 D/R table.

### U.S. Senate × House seat-count table

The 13 contracts (`2683257`–`2683269`) partition 16 fine cells formed by Senate bands `{<=46, 47–49, 50–52, >=53}` and House bands `{<=192, 193–207, 208–222, >=223}`. The 13×16 matrix has rank 13, and normalization is already in its row span. The 15-dimensional fine simplex therefore has 12 observed grouped-probability degrees of freedom and 3 unobserved within-bucket degrees. In particular, Senate `50–52` and House `208–222` straddle the majority cutoffs (51 and 218); the table cannot yield exact chamber-control probabilities from these bins alone.

### Republican governorship count

The seven contracts (`907686`–`907692`) group a 51-state count `K` into `<22`, `22–23`, `24–25`, `26–27`, `28–29`, `30–31`, and `32+`. The 7×51 matrix has rank 7; the seven grouped probabilities have 6 free degrees and the 44 within-bucket count degrees are lost. The probability that Republicans hold at least 26 governorships is exactly the sum of the last four bucket probabilities. That does not identify any one state race margin.

### U.S. House national popular-vote margin

The 13 v2 contracts (`1395450`–`1395462`) are 13 selected signed-margin bins. Gamma shows an explicit `Other` sibling outside v2. The 13×14 observation matrix has rank 13; adding normalization raises it to 14. The 13 bucket probabilities plus the residual identify the coarsened national margin distribution, subject to the written boundary rules. The event concerns national popular vote among voting Representatives in the 50 states; it does not identify any district winner.

## Gamma rule and group audit

The selected markets were fetched by market ID and their full event child sets were fetched by event slug. All 1,279 descriptions match their frozen SHA-256. Twelve market-ID requests were empty but each candidate was recovered from an event child. The full event responses contain 1,099 off-scope siblings; these were used only to identify missing `Other` outcomes and group coverage, not added to the v2 acquisition scope. `resolutionSource` and frozen criteria hashes are blank for every selected candidate, so neither may be treated as the settlement rule.

The `Georgia Senate and Governor Combo` event has four selected D/R cells and an explicit off-scope `Other` cell. Its description treats independent/third-party wins as Other, includes runoffs, attributes party by ballot-listed affiliation/nomination, and resolves using an AP/Fox/NBC consensus or official certification. Its Gamma `endDate` is 2027-04-01; the Texas equivalent shows 2026-11-04 while its described resolution window extends into 2027. Across all candidate-linked event families, 32 have differing child `endDate` values. Use description rules and stable scope IDs; do not infer settlement time from a group-level end date.

For Nebraska Senate margin, Gamma has 13 children while v2 contains 11; the two omitted children are Person A/Other outcomes. The actual description defines absolute top-two valid-vote margin, party nomination, independent treatment, boundary ties, recount timing, and Other resolution. The v2 rows are not a self-proving complete partition. South Dakota Senate margin has 13 children while v2 contains only Independent Wins and Democrat Wins. Inspect the audit and sibling set before summing any family.

`negRisk=true` appears on 1,233 candidates, but it is not proof of mutually exclusive, exhaustive outcomes. `groupItemThreshold` is often an ordering value, not an economic cutoff. There are 15 blank `groupItemTitle` values; infer thresholds from description text and verify them. None of these Gamma metadata fields supersedes settlement wording.

## Key non-equivalences and exclusions

- The four state Senate-control questions in this graph use state-chamber control rules, including majority-seat and sometimes chamber-leadership fallback. They do not resolve the SIG Governor target; keep them predictive-only and same-state at most.
- `Closer Senate Race: Florida or South Carolina?` references the regularly scheduled Florida Senate race, while other v2 questions refer to Florida Special. A SIG anchor labelled simply Florida Senate does not settle that identity issue. Keep the comparison predictive-only.
- National House popular-vote margin and national House turnout are aggregate election features, not House district winner outcomes.
- The 31 local `within 1%`/`within 5%` closeness thresholds provide no party direction. Cross-race “closer”, “closest”, or “perform best/worst” contracts are also predictive-only.
- Ten candidates concern Buttigieg/Walz/Hunter Biden candidacy, Graham Platner withdrawal, or Texas primary matchups. Their contract events differ from final general-election winners and are rejected.
- The graph's bipartite topology forms one connected component under both the all-edge and structural-edge definitions, primarily because aggregate counts connect many anchors. This graph connectivity does not assert one joint stochastic model or independence structure across every election.
