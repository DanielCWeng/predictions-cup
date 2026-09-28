# ADDENDUM 1 (Daniel, 28-09 ~09:35Z) — overrides TASK.md where they conflict

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
