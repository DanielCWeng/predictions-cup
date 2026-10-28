# Election-week terminal settlement comparison (28 October 2026)

**Status:** offline, read-only decision-support code on draft PR #368. No West or live SIG access, no order sender, no claim of validated forecasting advantage.

## Run

From a checked-out repository with its Python dependencies installed:

```bash
python scripts/ops/election_terminal_score.py tests/fixtures/election_terminal_example.json
```

The bundled example is **entirely synthetic**. It demonstrates two correlated races and two distinct probability views; none of its prices or probabilities are evidence about the actual 2026 election.

For real analysis, copy the JSON structure and supply:
- `cash`: authoritative spendable cash **after existing venue holds**, not equity or buying power inferred from portfolio marks.
- `target_balance`: the explicit hypothetical **post-settlement** winning balance to beat, not current leaderboard mark. Review sensitivity to different targets.
- `holdings`: each market's actually reconciled existing YES and NO share quantities, never inferred from resting order sizes.
- `scenarios`: mutually exclusive, collectively exhaustive **joint** YES/NO outcomes across every included market (including correlated Senate seats and chamber-control contracts). Scenarios are outcome vectors, not assumed independent legs.
- `probability_views`: complete distributions over the **same** joint outcome scenarios, with independent, traceable `evidence_reference` strings. Distinct IDs are not proof of independent or calibrated forecasts.
- `plans.trades`: ordered, immediately executable BUY/SELL of owned shares with known `quantity`, full-size `executable_price`, `displayed_shares` and `extra_cost_per_share`. Do not extrapolate a one-share best quote into a larger hypothetical order. If the full ladder reprices, calculate the actual size-dependent price first.

The tool rejects invalid scenario coverage, non-normalized probabilities, unsupported quantities, insufficient cash at any intermediate leg, non-owned sales, and insufficient quoted depth. It reports per-model terminal balance, incremental expected balance, and change in probability of **strictly beating** the chosen target. Ranking prioritizes the worst-model improvement in reaching that target over ordinary EV.

## Limitations that matter

- **No calibrated election probabilities are created here.** In particular, a poll lead or a live vote-count lead is not an implied win probability; official reports are initially unofficial, and reporting order/remaining ballots are essential.
- The output is a comparison **conditional on supplied scenarios**. A missing scenario (e.g. narrow third-party outcome, ranked-choice path, recount uncertainty, seat-control edge case) is not assigned any probability by this code. Unsupported market settlement semantics must be resolved before including a market.
- Does not model conditional fill probability, partial fills, cancellations, future quote movement, unquoted size, or uncertain settlement timing. Ordered sales before purchases are modeled as fully filled; this is a **counterfactual**, not permission to run a two-leg rotation.
- Does not set RISK-002 limits or interact with live maker/taker. No economic performance is claimed.
- The score treats the leaderboard target as fixed and exogenous. Rerun for different plausible final rival balances; it is not an actual probability of winning first place unless the target distribution is justified.
- Until genuine independent event evidence and directly observed SIG executable prices are supplied, **the scorer identifies no live trade**.

## Verified source warning

Ohio Secretary of State's election-night XML/results page presently advertises **mock election figures** as preparation for 3 November; do not treat its currently displayed counts as real election outcomes. Official state feeds are useful after poll closure, but the U.S. Election Assistance Commission says unofficial results can change before canvass and certification.

Source: https://liveresults.ohiosos.gov/ and https://www.eac.gov/election-officials/election-results-canvass-and-certification
