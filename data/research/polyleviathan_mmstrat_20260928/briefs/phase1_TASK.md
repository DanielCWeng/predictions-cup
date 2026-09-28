# TASK: best market-making wallets in three election families (HUN_2026, COL_2026, PER_2026)

Lane dir: /home/ubuntu/campaigns/mmstrat_20260928 (write everything here). When finished, write REPORT.md and then touch .done.

## Question (Daniel)
Across three families of Polymarket election markets, which wallets behaved as market makers (they paid no fee on most of their fills, i.e. they were mostly maker / zero-fee), and which of them made the most PnL? What did the best ones actually do? Describe their strategy with numbers.

Families (market counts come from the inventory):
- HUN_2026: 2026 Hungarian parliamentary election, 200 markets, 840,134 fills, 25-07-2025 → 09-05-2026
- COL_2026: 2026 Colombian elections, 380 markets, 693,573 fills, 29-07-2025 → 15-07-2026
- PER_2026: 2026 Peruvian general election, 393 markets, 1,212,643 fills, 16-12-2025 → 30-07-2026

Total: 973 markets. These fill counts are the exact targets your rebuilt fills must hit (from /home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/fills_MANIFEST_20260926.md).

## Read first
1. /home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/polyleviathan_election_market_inventory.csv. This is the market universe: research_family, condition_id, yes/no/other token ids, is_resolved, winner_outcome, resolution_time, question, event.
2. /home/ubuntu/polymarketwhale/campaigns/sisterreq_20260925/export_fills_20260926.py plus fills_MANIFEST_20260926.*. This is how the fills corpus was built from the OCI trades lake, including dedup keys (tx_hash, log_index, token_id) and the exchange_taker_rows column.
3. recovered/stage.py and recovered/assemble.py. These are the fee extraction used on 27-09: custody fee/refund legs in the scoped txs, rebate-distributor payouts, and a market fee snapshot. They were recovered from a transcript, so treat them as a strong starting point, not gospel.
4. Memory notes worth reading:
   - /home/ubuntu/.claude/projects/-home-ubuntu/memory/reference_fees.md
   - /home/ubuntu/.claude/projects/-home-ubuntu/memory/resolution_payout_types.md
   - /home/ubuntu/.claude/projects/-home-ubuntu/memory/reference_contracts.md
   - /home/ubuntu/.claude/projects/-home-ubuntu/memory/polymarket_data_api.md

## Step 1: data (already built; do NOT rebuild)
Use /home/ubuntu/state_backup_20260902/sisterreq_fees_20260927/ (read-only; Daniel restored it):
- fees_HUN_2026.parquet (840,134 rows), fees_COL_2026.parquet (693,573), fees_PER_2026.parquet (1,212,643). One row per OrderFilled = one participant order, WITH per-fill fees attached. Row counts equal the fills manifest exactly, so this IS the fills corpus.
- Read DATA_DICTIONARY.md, POLYMARKET_FEE_DATA_NOTES.md, STATS.md, request.md in that folder FIRST. Grain: each match emits one row per maker order plus one for the taker order (counterparty = exchange contract; order_is_match_taker_order=true). participant_address is the order owner. Use fee_evidence / fee_net_usd_equiv / fees_enabled / fee_taker_only for the zero-fee test; `custody_not_scanned_pre_fee_era` means fee unknown-but-era-zero, say how you treat it.
- maker_rebate_payouts_scoped_wallets.parquet, polymarket_fee_regimes.*, unattributed_fee_legs.parquet are there too.
- recovered/ scripts are background only. Ignore the fills/fee rebuild instructions below; the gate is simply: row counts per family equal the numbers above.
- The canonical build is currently stopped, so memory is freer, but keep the 6G cap.

## Step 2: per-wallet metrics per family (and pooled)
Work out the fill semantics before computing anything:
- which address is the maker and which the taker on each row;
- what the exchange-as-taker rows are (exchange_taker_rows), so nothing is double counted;
- how NegRisk / multi-outcome conversions show up.
Write this down in REPORT.md with 3 worked tx examples from the data.

For every wallet:
- fills, notional, maker share and zero-fee share (by count and by notional), fees paid, and maker rebates received (rebate payouts are per wallet; allocate to these families pro rata to that wallet's maker notional, and say so);
- PnL = sell proceeds − buy cost − fees + allocated rebates + resolution payout on shares held at resolution. The payout can be a fraction (resolution_payout_types.md), and a void resolves with a NULL winner.
  - Unresolved markets: mark at the last fill price and report that part separately as unrealised.
  - A wallet whose share inventory goes negative in a token got shares outside fills (split/merge/transfer). Flag it and report how much PnL is affected. Do not invent the cost basis.
- MM definition: maker share ≥ 80% of fills AND zero-fee share ≥ 80%. Also show sensitivity at 60% and 90%. Require a minimum activity level and state the threshold, e.g. ≥200 fills or ≥$10k notional.

## Step 3: strategy anatomy for the top 10 MM wallets by PnL (pooled across the 3 families) plus the top 3 per family
Per wallet, with numbers:
- PnL split into spread capture vs directional/resolution: e.g. round-trip matching (FIFO) spread vs terminal inventory × (payout − cost);
- two-sidedness (buys and sells on the same token; quoting both YES and NO; across all outcomes of a NegRisk event);
- inventory profile: max and median net position, turnover, median holding time;
- behaviour through time: activity vs days to resolution, and around big price moves;
- fill size, price levels quoted (e.g. tails < 5c or > 95c vs the middle);
- markets covered, and overlap with the other families.

Also compare: what separates the top MMs from the MMs that lost money?

## Cross-check (mandatory before reporting numbers)
- Zero-sum: per resolved market, Σ wallet PnL across all wallets should equal −Σfees + rebates, within what splits/merges explain. Report the residual per family.
- External: for the top 5 wallets, compare against the Polymarket data API (/activity or /positions per wallet; polymarket_data_api.md). A mismatch is reported, not tuned away.

## Hard constraints
- The machine is tight on disk (~13 GiB free) and a canonical build is running (unit canonical-resume-handoff-20260928T084943Z-2356268). Check free disk before each stage; if free drops below 9 GiB, pause and write PAUSED_DISK.md. Keep intermediates small (zstd parquet). Delete your own scratch at the end, but keep data/*.parquet and outputs.
- Memory: run heavy steps under `sudo systemd-run --collect -p User=ubuntu -p MemoryMax=6G -p MemorySwapMax=512M -p OOMScoreAdjust=800 ...` with POLARS_MAX_THREADS=2, or stream per day.
- Read-only: OCI (read), Postgres (read only, if needed), the data API. No RPC. No writes to Postgres, to /home/ubuntu/pnl_artifacts, or to /home/ubuntu/polymarketwhale.
- NEVER run anything with sudo that touches /home/ubuntu/polymarketwhale/Sonar/.env. Load it with python-dotenv as user ubuntu only.
- Do not touch systemd units other than your own, and do not restart services.

## Deliverable
- REPORT.md: method (incl. fill semantics), gates hit (exact numbers), leaderboard tables (top 25 MM wallets by PnL, pooled and per family: wallet, fills, notional, maker %, zero-fee %, fees, rebates, realised PnL, unrealised PnL, spread-vs-directional split, flags), strategy anatomy for the top wallets, what separates winners from losers, and a mandatory "What I did NOT do / caveats" section.
- outputs/: wallet_metrics_<FAMILY>.parquet, wallet_metrics_pooled.parquet, mm_leaderboard.csv.
- Then `touch /home/ubuntu/campaigns/mmstrat_20260928/.done`.


## ADDENDUM 1 (appended; also in ADDENDUM_1.md)

1. The chain maker/taker ROLE IS NOT real-world maker/taker. `order_is_match_taker_order` / maker_address / taker_address describe the on-chain OrderFilled role only (who was the matched "taker order" in the exchange call), NOT who provided liquidity in practice. Do NOT define "market maker" by chain role share.
   - Keep the chain-role share as a descriptive column only.
   - Primary MM signal = the ZERO-FEE share (fills where no fee was charged/net fee = 0, with the fee-era caveat for custody_not_scanned_pre_fee_era), PLUS behavioural evidence of liquidity provision: two-sided activity on the same token (buys and sells), quoting both YES and NO / all outcomes of a NegRisk event, many small round-trips, mean-reverting inventory, spread capture (FIFO round-trip PnL) being a large part of PnL, and short holding times.
   - Define the MM classifier from those signals, state the thresholds, and show the sensitivity. In REPORT.md, show how different the result would have been if chain role were used, to prove the point either way.
2. ALSO surface profitable strategies that are NOT MM-like (low zero-fee share, or directional). Add a section "Other profitable strategies". Take the top wallets by PnL of ANY type (top 25 pooled + top 5 per family) and cluster or classify their behaviour, e.g.:
   - directional conviction / hold-to-resolution;
   - late resolution sniping (buying near-certain outcomes close to resolution);
   - tail buying/selling (<5c / >95c);
   - NegRisk cross-outcome arbitrage (sum of prices ≠ 1);
   - news/momentum reaction;
   - event-to-event rotation.
   Give numbers per wallet (PnL split, holding time, price levels, timing vs resolution, fee paid). Daniel wants every distinct profitable strategy thrown at him, not only the MMs.


## ADDENDUM 2 (11:00Z)
Time-box: write REPORT.md from existing outputs within ~15 min; unfinished items go to "What I did NOT do"; then touch .done.


## ADDENDUM 3 (11:15Z) — see ADDENDUM_3.md: rebates ex-ranking, fee-eligible-only zero-fee test, stricter MM tier; ~30-40 min.
