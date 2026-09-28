# PHASE 2: strategy anatomy deep-dive (the team's questions). Run AFTER phase 1 is graded.

Inputs:
- phase 1 outputs in /home/ubuntu/campaigns/mmstrat_20260928/ (REPORT.md, outputs/, data/, .analysis_mm.py);
- per-fill data in /home/ubuntu/state_backup_20260902/sisterreq_fees_20260927/fees_{HUN,COL,PER}_2026.parquet.

The chain maker/taker role is NOT real-world maker/taker (ADDENDUM_1). Everything in TASK.md and ADDENDUM_1 still applies: constraints, fee semantics, PnL definition.

Wallet set:
- the top MM-like wallets (top 10 pooled + top 3 per family);
- the top non-MM profitable wallets (top 10 pooled + top 3 per family);
- a matched set of LOSING wallets with similar behaviour, as the base rate.

For each wallet answer every question below with numbers. Then add one cross-wallet comparison table per question.

## The team's questions
1. Market selection: which families, market types (winner / seat-count / vote-share / margin / NegRisk multi-outcome / binary), liquidity tier, and how many markets.
2. Entry distribution: histogram of entry prices (buy prices and sell-to-open prices), in buckets <5c, 5–20, 20–40, 40–60, 60–80, 80–95, >95c.
3. Holding period: the distribution in seconds / minutes / hours / days / held to resolution (FIFO lot matching). Give the median, p10 and p90.
4. Position construction: one-shot vs gradual accumulation (number of fills to reach peak position, time from first fill to peak, clip-size profile).
5. Inventory recycling: how often net position returns to ~0 (e.g. within 5% of the peak) per market, time between flat points, and inventory half-life.
6. Round trips: entry/exit spread (cents and bps), duration and size. Give the distribution and the share of round trips that are profitable.
7. Cross-market sequences: A-then-B patterns, sibling baskets within a NegRisk event (buying all/many outcomes), complements (YES on one + NO on another), and lead-lag between related markets.
8. Directional behaviour: momentum vs fade (trade direction vs the prior 1h/1d price move), averaging down vs up, and pyramiding.
9. Resolution exposure: fraction of volume/positions closed before resolution vs held, and PnL from each.
10. Regime behaviour: activity and PnL by time-to-resolution bucket (>30d, 7–30d, 1–7d, <24h, <1h), and around result nights / count releases. Does activity explode near results?
11. PnL attribution: spread capture vs directional/resolution vs rebates vs fees. It must sum to total PnL, and any residual is shown.
12. Consistency: PnL concentration (top-1 / top-3 markets' share of PnL, Gini over markets and over trades), number of independent repetitions, and whether it was hundreds of small edges or three giant bets.

## Additions (orchestrator)
13. Adverse selection / markouts: mid-price move 1m / 10m / 1h / 1d after each fill, signed by the wallet's side. This is the core test of whether it is a real MM (earns spread, gets picked off) or informed flow.
14. Reaction latency: for large price moves (> X c in < Y min), how many seconds until the wallet trades, and in which direction. Is it plausibly automated?
15. Counterparties: who they trade against (top counterparties, share vs retail-like wallets vs other top wallets), and whether counterparties are concentrated.
16. Capital and returns: peak capital at risk, return on peak capital, turnover (volume / peak capital), and PnL per $ traded.
17. Risk: max drawdown (on daily mark-to-market PnL), worst single market, daily PnL volatility, and a Sharpe-like ratio.
18. Timing: hour-of-day and weekday profile, and continuous 24/7 activity (bot) vs human-hours pattern.
19. Linked wallets: clusters that trade in lockstep (same markets within seconds, mirrored sizes) or that share funding sources in the fills data. Is one entity split across wallets? (Only from the data we have: no RPC. Flag it, don't assert it.)
20. Lifecycle and drift: first/last active dates, whether the strategy changed over time, and whether it stopped (and when).
21. Capacity: the share of each market's volume the wallet took, and whether PnL scales with size or decays (price impact of its own fills).
22. Fee-regime sensitivity: would the strategy's PnL survive the current fee regime (fees_enabled / fee_rate from polymarket_fee_regimes)? Recompute with today's fees.
23. Replicability verdict: per strategy, what the edge requires (speed, capital, information, inventory tolerance) and whether we could run it. One paragraph each, grounded in the numbers above.
24. Split/merge/conversion usage: how much inventory came from outside fills (negative-inventory flags from phase 1), and which strategies depend on minting/merging.

## Deliverable
- REPORT_PHASE2.md: one section per strategy archetype with example wallets; a per-question comparison table; charts as PNG in outputs/phase2/ (matplotlib is fine); a "What I did NOT do" section.
- outputs/phase2/*.parquet with the per-wallet, per-question metrics.
- Then touch .done_phase2.

## ORCHESTRATOR NOTES FOR THIS RUN (running IN PARALLEL with the phase-1 fix pass)
- Lane dir: /home/ubuntu/campaigns/mmstrat_phase2_20260928. Write ONLY here. Do not modify anything in /home/ubuntu/campaigns/mmstrat_20260928 (another agent is editing it). Read-only is fine: REPORT_v1.md, outputs/wallet_metrics_*.parquet, ADDENDUM_1.md, ADDENDUM_3.md, TASK.md.
- Apply ADDENDUM_3's corrections yourself. Do not wait for the fix pass:
  (a) PnL ranking EX-rebates; wallet rebates shown separately as platform-wide/non-attributable;
  (b) zero-fee share only over fee-eligible fills;
  (c) the strict MM tier (≥30 FIFO round trips, two-sided ≥50% of tokens, VW median FIFO hold ≤72h, inventory back near flat ≥5 times, FIFO ≥50% of FIFO+|terminal|, zero-fee ≥80% where applicable).
- Wallet set:
  - top 15 strict-tier MM wallets by ex-rebate PnL (pooled) + top 3 per family;
  - top 20 any-type by ex-rebate PnL (pooled) + top 3 per family;
  - a matched loser set of ~20 strict-tier or similar-behaviour wallets.
  If the strict tier has <15 wallets, say so and use what exists.
- Per-fill data: /home/ubuntu/state_backup_20260902/sisterreq_fees_20260927/fees_{HUN,COL,PER}_2026.parquet (read DATA_DICTIONARY.md there).
- Memory: another analysis lane runs concurrently. Cap your heavy jobs at MemoryMax=5G (sudo systemd-run --collect -p User=ubuntu -p MemoryMax=5G -p MemorySwapMax=512M -p OOMScoreAdjust=800 ...), POLARS_MAX_THREADS=2. Disk: pause if free < 9 GiB.
- Never sudo anything that touches /home/ubuntu/polymarketwhale/Sonar/.env. No RPC. No DB writes. Read-only Postgres ok.
- Time-box: aim for REPORT_PHASE2.md within ~90 min. Prioritise the team's 12 questions first, then 13-24. Anything unfinished goes in "What I did NOT do". Then touch .done_phase2.
