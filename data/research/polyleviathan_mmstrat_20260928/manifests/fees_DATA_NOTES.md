# Polymarket fee / rebate extract — 5 election families (27-09-2026)

Scope: every fill of US_2024, COL_2026, PER_2026, HUN_2026, CAN_2025 (1,747 markets, 3,646 tokens), identical to the
sisterreq_fills_20260926 population: 18,317,515 fills, per-family counts match that manifest exactly. No wallet pre-filter.
Source: our stored lake only (OCI trades/ + custody/ daily Parquet). No RPC, no re-scrape.

## 1. How fees and rebates worked (see polymarket_fee_regimes.csv)
- Before 2026-01-05: no trading fees. OrderFilled.fee = 0.
- 2026-01-05 .. 2026-04-27 (CLOB V1, fees phased in by category; broad rollout 2026-03-30, Geopolitics/World exempt):
  the exchange charges a GROSS fee on each order fill into a FeeModule, then the FeeModule refunds part or all of it to the
  order owner in the same tx. Realised fee = charge - refund. Makers were charged too and mostly refunded.
- 2026-04-28 onward (CLOB V2): the realised fee is one collateral transfer exchange -> fee receiver 0x115f48dc..., on the
  TAKER order. No refunds. Maker rebates are paid separately, daily, by distributor 0x3a9418b2... (not per fill).
- 2026-07-10: Sports fee 0.03 -> 0.05, maker rebate 25% -> 15% (not relevant to these election markets except as context).

## 2. Contracts / records
- Fee charge legs: transfer INTO 0x115f48dc2a731aa16251c6d6e1befc42f92accc9 (V2 receiver), 0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0
  (V1 CTF FeeModule) or 0xb768891e3130f6df18214ac804d4db76c2c37730 (NegRisk FeeModule). Asset = USDC collateral or the outcome
  token itself (V1 BUY fees were taken in shares).
- Refund legs: transfer OUT of a FeeModule to the order owner.
- Rebates: transfers out of 0x3a9418b2651c8164db5ebc56f12008137865e0f7.
- Attribution to a fill (receipt-verified): the charge leg sits exactly 2 logs from its OrderFilled in the same tx
  (V1: before it; V2: after it), and a token-denominated fee must be in the fill's token. When two fills are 2 logs away,
  the taker-order fill is chosen (V2 receipt blk 86,142,516: fee on the taker order, maker fill fee 0). Refunds go to the
  fill in the tx whose owner is the recipient, same token, nearest preceding.
- Conservation: attributed + unattributed legs = all scoped legs exactly (charge USDC $293,259.93 + $87.77 = $293,347.70;
  shares 56,413.96 + 819.24 = 57,233.20). 594 legs (0.22%) are unattributed -> unattributed_fee_legs.parquet.

## 3. Irregular / inconsistent periods
- V1 era (to 2026-04-27): fees were charged gross and refunded, often fully. fee>0 on a MAKER order in V1 is normal and is
  usually refunded (HUN_2026 V1 maker orders: $7,076.76 charged, $7,047.28 refunded).
- Early crypto fees (Jan 2026) were fully refunded; not relevant to these markets (first scoped fee leg: 2026-03-04).
- Direct rebate payouts observed only 2026-04-29 .. 2026-06-25. Nothing after 06-25 in custody.
- Pre-era days were NOT fully scanned: custody was scanned on every day >= 2025-12-01 and on the 15th of each earlier month
  (24 probe days, 359,937 scoped fills, 0 fee legs). Other pre-era fills carry fee_evidence = custody_not_scanned_pre_fee_era.
- Failed/reverted txs emit no logs, so they are absent from both trades/ and custody/. No flag population exists.

## 4. Market types
Per-market settings come from Gamma (fee_type, fee_rate, fee_taker_only, fee_rebate_rate, fees_enabled, fee_source,
fee_category) as a CURRENT snapshot — not historical. fee_source='keyword' is our guess, 'gamma' is authoritative.

## 5. Confidence
- fee>0 => taker/aggressor: strong in V2. In the V2 era 242,052 of 246,261 taker-order fills (98.3%) carry a fee leg,
  vs 1,738 of 400,909 maker-order fills (0.43%). In V1 it is NOT valid on the gross charge — use fee_net_usd_equiv.
- rebate>0 => passive maker: only at wallet-day level. There is no per-fill rebate record; the rebate table is per payout.
  The distributor also pays system addresses (e.g. 0x2d507657... received $85.1M in 166 transfers); the scoped table keeps
  only wallets that appear in these fills.
- fee=0 and no rebate: classifiable only with order_is_match_taker_order (a chain-role fact from the fill: taker_address is
  the exchange) plus the regime. Before 2026 it carries no maker/taker information at all.
