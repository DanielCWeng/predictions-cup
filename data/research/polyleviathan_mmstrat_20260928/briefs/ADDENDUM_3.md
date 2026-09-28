# ADDENDUM 3 (orchestrator grade, 11:15Z): fix pass. Keep REPORT.md; write REPORT_v2 sections into it.

The plumbing is accepted: row gates exact, zero-sum residuals to cents (COL late-fill anomaly explained), API key-exact for the top 5. Three defects must be fixed before this goes to Daniel's team:

1. REBATES ARE MISALLOCATED. Pooled allocated rebates are $1.43M against $292k fees in these families, and HUN has $419k rebates against $2.6k fees. That is impossible if the rebates were earned here. Each wallet's platform-wide rebate payouts are being pushed into these three families pro rata to in-family maker notional only. Artifacts: COL #4 0x0d2d845a ($1,383 notional, $54.5k PnL, which is all rebate); 0xfa5f246b (18 fills, $43k rebate); 0x945a4925 ($10.8k notional, $6.3k rebate); 0x44a1159b; 0xb4d250f5; 0xc8ab97a9.
   FIX: rank on PnL EX-rebates (fill cashflow + payout − fees). Show rebates as a separate, clearly-caveated column: "wallet's platform-wide rebates, not attributable to these markets from available data". Remove them from totals and from the zero-sum identity text.

2. THE ZERO-FEE TEST DOES NOT DISCRIMINATE WHERE FEES WERE OFF. HUN is 99.4% zero-fee fills, i.e. mostly pre-fee-era or fee-disabled markets, where takers also paid 0. There, zero-fee says nothing about being a maker.
   FIX: compute zero-fee share ONLY over fills that were fee-eligible (the market had fees enabled at fill time and custody was scanned; use fees_enabled / fee_evidence / fee regime). Report per family the share of fills that were fee-eligible. Wallets with fewer than 20 fee-eligible fills get zero-fee share = n/a, and the zero-fee criterion is not applied to them.

3. THE MM-LIKE CLASSIFIER IS TOO LOOSE. "≥1 two-sided token and ≥20% of tokens" puts 2,127 wallets in. Winners and losers both sit at a 100% two-sided median. The pooled "#1 MM-like" 0xffe9858 has a 4,103h median FIFO hold, 85% of fills >30d from resolution and 56% of notional >95c, which is not market making.
   FIX: a stricter, behavioural MM-like tier (keep the loose tier, renamed "low-fee two-sided", for reference). Suggested, and yours to tune with sensitivity shown:
   - ≥30 FIFO round trips;
   - two-sided on ≥50% of traded tokens;
   - volume-weighted median FIFO hold ≤72h;
   - net inventory returns to within 10% of its running peak at least 5 times in its main tokens;
   - FIFO component ≥50% of (FIFO + |terminal|);
   - zero-fee share (fee-eligible fills only) ≥80% where applicable.
   Re-rank the MM top 25 (pooled + per family) under this tier and redo the anatomy for the new top 10 + top 3 per family. Show how many wallets pass each criterion (funnel table).

Also rerun the winners-vs-losers table on the strict tier.

Time-box: about 30-40 min. Then touch .done. Do not start any phase 2 items.
