# Election inventory limits and near-resolved-market policy
**Research-backed proposal — 25 October 2026. NOT deployed. No changes to live trading permissions or orders.**

## Decision
Stop pursuing speculative alpha backtests as the primary workflow. Protect productive capital, size exposure by race and correlated outcome, and use polling as a *valuation input* rather than a price-momentum instruction. Existing holdings are **grandfathered**, not dumped at the touch. Record only executable opportunities that survive market-identity, freshness, liquidity, fees, settlement, risk and journal checks.

**Already in production configuration:** both maker and taker receive `PREDICTIONS_CUP_MAKER_MIN_FAIR_VALUE=0.10` and `PREDICTIONS_CUP_MAKER_MAX_FAIR_VALUE=0.90`. The existing code excludes out-of-band *fair values*; this is **not** a guarantee every individual order/touch stays inside 10c–90c. At present the maker's `fv_outside_band` rule returns `CANCEL`, not an inventory-reducing-only mode. No exceptional reversal strategy exists.

## Proposed new-entry rules (illustrative risk budget, requires approval before code/config rollout)

| Independent conservative fair / probability band | New exposure | Per-event/race inventory ceiling for new risk | Treatment of existing positions |
| --- | --- | --- | --- |
| 0.25–0.75 | Normal risk-gated maker/taker | 1,000 gross equivalent shares and a capital-at-risk budget | Hold, reduce, or exit according to expected executable value; no forced exit |
| 0.10–0.25 or 0.75–0.90 | Reduced-size, stronger economics screen | 500 equivalent shares | Inventory-reducing quotes still permitted with trusted books and risk approval |
| Below 0.10 or above 0.90 | **No routine inventory-increasing orders** | 0 additional new shares by default | **Reduce-only**, not automatic liquidation; no flip through zero |
| Exceptional cheap-side reversal | Independently verified catalyst, manual promotion to separately metered risk sleeve | <=0.25% of current equity at cost per entry, <=1% total concurrent at-risk tail capital; still obey per-event signed-inventory and cash controls | Do NOT liquidate the rest of the portfolio solely to fund the speculative trade |

Additional requirements:
- Proposed ceilings apply at **resolution-event/race** scope, aggregated across all SIG outcomes representing the same race, not independently per contract. Preserve exact wording and resolution rules; do not assume two party-outcome contracts are perfect complements, especially for independent/third-party outcomes.
- Cap BOTH gross equivalent shares and **cash/collateral or worst-case settlement loss**; 2,500 YES shares bought at 10c and 90c are not the same capital risk. Take the worst case of positions plus reservations and open orders. Group correlated exposure from an across-the-board House swing and cap correlated new risk rather than simply allocating 1,000 shares to each district independently.
- Existing positions above new limits may be held or gradually reduced when fair and executable liquidation value justify it, but may never be increased by new orders. Maintain complete account/journal reconciliation and broker-level authority.
- A raise in limits must be explicit, auditable and expiry-dated; an ordinary price tick, one partisan poll, or an untrusted order book is not a valid override.
- Reserve sufficient executable capacity for favourable trades; do not infer available cash from gross share exposure.

## Exception: buying a 10c outcome when it starts to reverse

This is **event-driven value trading**, not buying merely because the percentage price move looks large:
1. Confirm a verified, timestamped catalyst: new race-specific likely-voter polling with methodology and sample, corrected resolution information or credible confirmed election development. Compare at least two independent sources when possible, and account for partisan sponsorship, age and correlated polling errors.
2. Re-estimate a **conservative** probability interval *after* the catalyst, separately from the observed market price; require the pessimistic fair bound to exceed the executable ask (for buys) plus depth sweep, fees, latency, adverse-selection allowance and a material safety edge (illustrative >=3 percentage points beyond all costs). Reverse for sales. Poll margins are not probabilities without a calibrated mapping.
3. Fresh SIG BBO + trusted depth, verified exact mapping, up-to-date external book, venue acknowledgement/rate capacity, RISK-002 and BUILD-009 admission, no self-cross, no account proxy inventory uncertainty. No fabricated liquidity, midpoint fills or theoretical 10x claims.
4. No all-portfolio liquidation. Execution plan capped by the tail sleeve and current exposure/race limit, with an ex ante exit target and loss/time stop tied to liquidity and the 4 November competition deadline.
5. “10x” is possible **gross payout only on a 10c contract that settles at 1.00**. A move from 10c to 20c is 2x gross value (before costs), not 10x, and a bad tail bet can lose its entire premium. Rapid repricing also increases spreads/adverse selection.

## Initial polling orientation (NOT standalone order signals)

- National House environment as of 20–23 October: RCP generic ballot ~D+8.1; Silver Bulletin ~D+9.1, after a renewed increase. A party-wide edge is **not** an unpriced individual-district edge.
- **IA-03**: two current forecasts disagree materially (~53% vs ~78% Democratic win chance); the same portfolio snapshot showed ~2,500 shares for the Democratic contract *and* ~2,500 for the Republican contract. Verify any hedge against distinct SIG resolution terms before rebalancing.
- **TX-15**: Cook rates toss-up as of 23 October; the snapshot showed both party contracts heavily held. Do not double count or liquidate both without checking costs and resolution.
- **PA-01**: polling R+3.2 as of 23 October, with House rating Lean R. High exposure merits close monitoring, not a party-wide bet.
- **CO-08**: Cook Lean D; separate model approximately 70% Democratic win probability on 25 October. If the SIG Republican side is priced at a steep premium, check that risk relative to current polling.
- **Nebraska Senate**: polling near R+0–2 versus *independent* Dan Osborn. Never label that opponent as a Democratic win contract or infer binary party complement.
- **South Carolina Senate**: aggregated estimates conflict by poll selection; one aggregator D+2.4 while another's recent-poll average is R+2.0. Treat as competitive rather than certain.
- Many thinly polled district outcomes should be sized by model disagreement and weak depth, not invented quantitative precision.

Reference material (all checked 2026-10-25):
- https://www.realclearpolling.com/polls/state-of-the-union/generic-congressional-vote
- https://www.natesilver.net/p/generic-ballot-average-2026-nate-silver-bulletin-congress-polls
- https://www.cookpolitical.com/ratings/house-race-ratings
- https://www.pollingforecast.com/us/house/ia-03
- https://www.onpointpolitics.us/forecast/ia/house-3
- https://www.callthemap.com/polls/HOU-PA-1
- https://www.callthemap.com/polls/SEN-NE
- https://www.onpointpolitics.us/polls/sc/senate

## Engineering implementation scope — NO LIVE CHANGE IN THIS DOCUMENT

1. Derive an auditable per-resolution-event grouping from verified SIG contracts and resolution terms; maintain authoritative signed inventory and reservations; separate capital cost from gross shares.
2. Maker policy: replace extreme fair-value blanket `CANCEL` with a **provably inventory-reducing-only** decision if and only if all account, fair, BBO, depth and execution safety gates pass; otherwise stay CANCEL/HOLD. Ensure quote sizing cannot cross zero, including open orders and in-flight fills.
3. Taker: only permit extreme-market inventory reduction under the standard risk gate; keep exceptional *opening* tail orders disabled until independently verified catalyst, a separate budget, and controlled approval exist.
4. Keep the current risk engine's hard limits and execution reconciliation. Do not force exits or perform all-portfolio reallocations automatically.
5. Safely integrate at the next coordinated production deployment. Necessary risk/safety regression checks still apply; **no speculative historical alpha-testing project**.

## Historical portfolio reference (refresh against authoritative account before action)
25 Oct afternoon RISK-002 equity around 101,600 SUSQ, gross exposure around 100,000 shares over ~69 markets, ~28 markets near 2,500 share exposure, realised P&L around -61 SUSQ; no complete maker/taker P&L attribution. These are observations, not assumptions for future trade sizing.
