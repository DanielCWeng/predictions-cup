# ETS fair-value methods specification

## Current boundary

This document describes how a later team could estimate fair values from the graph. This phase has not acquired or inspected prices, fills, order books, or resolved outcomes. It has not calculated probabilities, calibrated a price-to-probability model, selected weights, or asserted an executable trade. The hierarchy below is a method specification, not an FV result.

## 1. Freeze event identity before combining prices

Use the graph relationship and the Gamma description hash to identify each source. Before combining contracts, match office, district/state, election round, candidate/party attribution, ballot rules, cutoff, ties, recounts, runoffs, and resolution source. Treat blank Gamma `resolutionSource` or frozen criteria hashes as missing metadata. If the full descriptions disagree or have drifted, exclude that source from an exact formula until a reviewed rule record resolves the mismatch.

Do not use one event-level `endDate` as the settlement time when child dates differ. Preserve market-level dates and apply the written rule horizon. The Georgia/Texas combo date mismatch is an example of why description text is authoritative.

## 2. Build synchronized probability intervals from later market data

For each selected Polymarket condition, acquire the approved trade/fill history first and a later order-book snapshot for the 797 rows marked `book_needed_later=true`. Preserve condition ID, token IDs, event/market IDs, timestamp, side, price, size, and source response. Retain raw responses under the applicable lane cap. A binary Yes/No quote is not two independent observations: validate complement consistency and capture spread/depth rather than treating a last print as a fair probability.

Construct same-time price intervals from executable bid/ask or observed fill-side data. Apply fees, price tick, stale-quote rules, thin-market rules, and time alignment according to a protocol fixed before outcome access. Keep the exact chosen quote statistic and exclusions auditable. Do not silently replace an unavailable quote with a last trade.

## 3. Exact-structural source aggregation

For a reviewer-accepted exact event, the latent probability equality is direct:

\[
P_{SIG}(A)=P_{PM}(A).
\]

For a reviewed derived partition such as a party's mutually exclusive margin bands:

\[
P_{SIG}(A)=\sum_{k\in S}P_{PM}(B_k),
\]

only after checking all buckets cover exactly the same event and include no overlap, omitted Other, or ambiguous tie state. Propagate source quote intervals through the sum (and account for cross-market quote simultaneity). If only a subset is available, report a lower bound from observed disjoint cells and an upper bound that includes all unresolved target-compatible states; do not renormalize.

## 4. Model-dependent sources and partial identification

Encode each joint or aggregate market as a row in an observation matrix `A`; let `x` be nonnegative latent state probabilities with `1'x=1`. Given market probability intervals `l <= A x <= u`, derive target lower/upper bounds by minimizing and maximizing `c'x` under those constraints and the reviewed support. Report solver, matrix rank, residual support, constraints, and sensitivity to stale or missing markets. Do not convert a point estimate to “exact” merely because the optimization has a narrow interval.

For count distributions, use the actual grouped counts and the exact included race universe. For joint surfaces, preserve an explicit Other state. The Senate×House count surface has three unresolved within-bin degrees and its majority-straddling bands do not yield exact control odds. The Republican governor count yields exact grouped aggregate sums but no state-level point estimates.

If the team elects to impose independence, correlation, or a national/state hierarchy, fit it as a separately named model on training data and report a sensitivity range against assumption-free LP bounds. The default output should remain a partial-identification interval when data do not identify a point.

## 5. Predictive-only sources

Turnout, voter-group results, county results, closeness, and cross-race comparisons should enter only as features in a preregistered forecast. Fit no structural equation from these contracts. Compare each feature family with a frozen baseline using event-clustered, time-ordered holdouts and reliability/calibration measures. Leave metadata-only rows outside the feature set unless a separate hypothesis is registered.

The Florida regular-versus-special Senate mismatch needs manual identity resolution before any predictive join. A same-state legislative chamber question remains only a feature candidate for a governor target.

## 6. Translate estimates into an execution study only after calibration

An eventual SIG reference price can be compared to a source estimate only after computing uncertainty, venue timing, and execution costs. A discrepancy alone is not arbitrage. A later execution study needs current SIG books, fill probability, queue/depth, fees, capital lockup, cancellation risk, and exact settlement alignment. The 797 P0/P1 candidates are only the proposed later order-book scope; no order-book request was made in this phase.

## 7. Do not fit weights yet

The graph has no observed fill prices or outcomes, so no weight is estimable here. Once an outcome-blind plan is frozen and later authorized inputs are available, estimate any blending weights on a training interval, tune only inside that interval, and evaluate by later-time and event-cluster holdout. Include the accepted crosswalk baseline, exact v2 sources, model-dependent bounds, and predictive-only feature model as separate comparators. Publish an unweighted structural baseline and assumption-free bounds alongside any fitted composite.
