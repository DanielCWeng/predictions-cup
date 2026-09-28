## REPORT V2 — rebate, fee-eligibility, and strict-MM correction

**This section is the corrected decision report.** The original V1 text below is retained as an audit trail; its rebate-inflated PnL, all-fill zero-fee shares, and MM-like rankings are superseded here.

### Corrected conclusion

The pooled highest ex-rebate wallet across any strategy remains 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a at **USD 377,034.08**, on 9,096 fills and USD 2.39m notional, with USD 2,988.47 net fees. The highest strict pooled MM-like wallet is 0xc8b9a30184244d427169cf62485dde6041b2b836 at **USD 81,020.55**. These are separate populations: overall PnL leader is directional/resolution-led, while strict MM-like ranks require repeated, short-hold inventory recycling.

Corrected total PnL is fill cashflow + resolution payout − net fees. It sums to **USD −292,163.97 pooled**, close to the USD 291,919.36 fees. The prior USD 1.43m pro-rata rebate allocation is removed from every PnL and rank. The source has USD 2,230,607.84 of wallet rebate payouts, but available data cannot attribute those platform-wide payouts to these election markets. The † column shows the wallet’s platform-wide rebates as separate context and never adds them to the PnL.

### Corrected scope and fee-eligible fill summary

| Scope | Fills | Notional | Fee-eligible fills | Eligible % of fills | Eligible zero-fee % | Wallets with ≥20 eligible fills | Fees | Σ PnL ex-rebates | Strict tier | Loose 60% | Loose 80% | Loose 90% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | 2,746,350 | USD 232,837,636.18 | 1,732,763 | 63.1% | 85.7% | 5,496 | USD 291,919.36 | USD -292,163.97 | 668 | 2,283 | 2,158 | 2,028 |
| HUN_2026 | 840,134 | USD 106,023,965.51 | 232,447 | 27.7% | 97.7% | 1,024 | USD 2,588.66 | USD -2,588.79 | 258 | 1,235 | 1,229 | 1,218 |
| COL_2026 | 693,573 | USD 39,039,419.66 | 418,184 | 60.3% | 73.2% | 1,513 | USD 90,803.28 | USD -91,047.57 | 160 | 460 | 411 | 377 |
| PER_2026 | 1,212,643 | USD 87,774,251.01 | 1,082,132 | 89.2% | 88.0% | 3,626 | USD 198,527.41 | USD -198,527.61 | 371 | 874 | 815 | 741 |

Low-fee two-sided (loose reference) tier: activity-qualified, ≥20% of traded tokens two-sided, and eligible-fill zero-fee share at the displayed threshold; wallets with fewer than 20 eligible fills are exempt. Strict tier adds the full funnel below. The table gives total eligible fills, their share of all family fills, and zero-fee share within eligible fills.

Fee eligibility uses custody-scanned fills on/after the 2026-03-30 broad rollout when the condition had observed fee-leg evidence; an explicit fees_enabled=true snapshot also qualifies within this period. HUN/COL historical snapshots are null, so that is a conservative condition/date evidence proxy rather than a complete historical market-settings archive. Pre-fee and unscanned fills are excluded. Wallet zero-fee share is n/a below 20 eligible fills; the fee gate is waived as instructed.

### Strict behavioral MM-like definition and funnel

Activity gate: ≥200 fills or ≥USD 10,000. Base tier requires two-sided buys/sells on ≥50% of traded tokens and zero-fee share ≥80% among fee-eligible fills when there are ≥20 such fills. Strict tier also requires ≥30 FIFO round trips; volume-weighted median FIFO hold ≤72 hours; at least five completed inventory reductions to ≤10% of running peak exposure across each wallet’s five highest-notional tokens; and FIFO component ≥50% of (|FIFO PnL| + |ex-rebate residual PnL|). Chain maker-order role is descriptive only. FIFO residual includes terminal/directional economics, net fees, and external-basis effects.

| Scope | All wallets | Activity | + two-sided ≥50% | + fee gate | + ≥30 FIFO round trips | + VW hold ≤72h | + ≥5 inventory returns | + FIFO share ≥50% | All strict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | 121,639 | 2,796 | 2,251 | 1,997 | 1,200 | 905 | 755 | 668 | 668 |
| HUN_2026 | 53,938 | 1,469 | 1,142 | 1,133 | 533 | 374 | 282 | 258 | 258 |
| COL_2026 | 35,728 | 668 | 479 | 373 | 300 | 210 | 182 | 160 | 160 |
| PER_2026 | 54,571 | 1,079 | 872 | 750 | 630 | 506 | 411 | 371 | 371 |

At loose thresholds 60% / 80% / 90%, the pooled counts are 2,283 / 2,158 / 2,028. Strict counts are 668 pooled, 258 HUN, 160 COL, and 371 PER. Family scopes overlap in pooled addresses. Funnel stages are cumulative.

### Strict MM-like leaderboard — pooled top 25 by PnL ex-rebates

Protocol maker-order role is the on-chain role share and is descriptive. † The wallet’s platform-wide rebates are not attributable to these markets from available data.

| # | Wallet | Fills | Notional | Protocol maker-order role fills | Eligible fills (scope %) | Eligible zero-fee share | Fees | Wallet platform-wide rebate† | PnL ex-rebates | FIFO RTs | VW hold h | Inventory returns | FIFO component | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,796 | USD 670,165.19 | 87.9% | 1,777 (98.9%) | 97.7% | USD 1,840.97 | USD 713.70 | USD 81,020.55 | 1,451 | 36.3 | 16 | 70.5% | — |
| 2 | 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 1,433 | USD 1,178,465.71 | 93.4% | 1,433 (100.0%) | 95.9% | USD 1,065.54 | USD 999.82 | USD 55,431.27 | 1,363 | 36.1 | 11 | 82.0% | — |
| 3 | 0x9c2617462567859fcf5764e03b9a687ebca274bc | 1,253 | USD 1,057,653.73 | 98.0% | 1,201 (95.8%) | 97.8% | USD 147.97 | USD 1,072.73 | USD 22,694.16 | 1,075 | 48.6 | 5 | 53.0% | — |
| 4 | 0x98d04f9eb52d08ef9697f6eb8d7cfe8d46e5f14a | 997 | USD 189,447.38 | 80.8% | 726 (72.8%) | 90.8% | USD 178.00 | USD 305.03 | USD 20,761.50 | 841 | 51.2 | 8 | 91.4% | — |
| 5 | 0xef5c8e2487d9b4d07565616b337e69f8488a661d | 308 | USD 173,962.72 | 82.5% | 308 (100.0%) | 93.8% | USD 1,112.13 | USD 319.21 | USD 17,286.45 | 300 | 32.3 | 16 | 94.3% | — |
| 6 | 0x515e2144bf38402568979db4ea7f6b64636c8309 | 425 | USD 198,218.89 | 97.6% | 425 (100.0%) | 97.6% | USD 182.29 | USD 564.20 | USD 14,362.61 | 421 | 26.2 | 6 | 98.8% | — |
| 7 | 0xfd8f5bba5b09d286b6d1db35cda8249bdd356f11 | 2,811 | USD 766,552.36 | 84.7% | 2,601 (92.5%) | 92.7% | USD 674.02 | USD 323.37 | USD 12,662.46 | 2,736 | 35.4 | 12 | 95.6% | — |
| 8 | 0xfd39b573bc2aa618c9bc226624e985133c1d109d | 1,301 | USD 376,583.07 | 94.8% | 1,033 (79.4%) | 100.0% | USD 0.00 | USD 52.62 | USD 11,641.81 | 1,297 | 14.9 | 7 | 99.8% | — |
| 9 | 0x40cfb29411d29f4fa0908f2a121297042cccd21d | 2,525 | USD 248,862.12 | 88.9% | 2,017 (79.9%) | 97.8% | USD 230.54 | USD 1,242.11 | USD 10,891.69 | 2,279 | 15.7 | 8 | 68.0% | fee_attribution_ambiguous |
| 10 | 0xd8d5289c35124b5353f5a10a28b8cc29aec38935 | 3,300 | USD 896,472.94 | 90.2% | 2,838 (86.0%) | 96.6% | USD 364.62 | USD 1,238.71 | USD 8,145.82 | 2,790 | 21.2 | 31 | 80.6% | fee_attribution_ambiguous |
| 11 | 0x4da76bbf120899fc10fa6e0aad4bffdd19a7355e | 350 | USD 114,789.93 | 99.1% | 324 (92.6%) | 100.0% | USD 0.00 | USD 371.65 | USD 7,024.23 | 332 | 43.7 | 5 | 89.9% | — |
| 12 | 0x7809521c05ba2724429e2ce924399eecad0c196a | 374 | USD 98,561.44 | 92.5% | 331 (88.5%) | 99.1% | USD 115.63 | USD 92.73 | USD 6,691.09 | 364 | 46.3 | 8 | 98.3% | — |
| 13 | 0x8597ca63e722d6216bfc3057591fdc67ec49daee | 251 | USD 128,436.55 | 94.8% | 215 (85.7%) | 96.7% | USD 65.99 | USD 227.16 | USD 6,642.84 | 242 | 49.6 | 6 | 99.0% | — |
| 14 | 0xc20c3ccd49e06eda6c336539abc16714872be2bd | 71 | USD 44,029.09 | 88.7% | 71 (100.0%) | 88.7% | USD 263.46 | USD 93.95 | USD 6,642.78 | 68 | 1.7 | 5 | 96.3% | — |
| 15 | 0x8454f39e7fa6957dfbf1ecc574820ada1c92331a | 939 | USD 355,713.88 | 95.3% | 827 (88.1%) | 99.3% | USD 30.95 | USD 889.63 | USD 5,428.09 | 871 | 3.1 | 16 | 78.7% | — |
| 16 | 0x744c072005bde6ddab8764a7477f61d3d22ae37f | 184 | USD 209,642.04 | 96.2% | 184 (100.0%) | 97.3% | USD 9.13 | USD 832.29 | USD 5,321.28 | 180 | 0.8 | 10 | 99.8% | — |
| 17 | 0x47ab026767cc320ac6e62f6ec747d59cf4d795df | 808 | USD 183,343.50 | 95.9% | 787 (97.4%) | 98.5% | USD 6.81 | USD 458.72 | USD 5,066.35 | 597 | 49.2 | 10 | 89.5% | — |
| 18 | 0x2200b75c27835935a40308779b5656c09ab40f2d | 546 | USD 164,840.83 | 83.5% | 534 (97.8%) | 92.5% | USD 256.62 | USD 258.03 | USD 5,044.69 | 483 | 0.4 | 16 | 83.4% | — |
| 19 | 0x1f0a343513aa6060488fabe96960e6d1e177f7aa | 8,698 | USD 478,726.47 | 97.3% | 2,211 (25.4%) | 99.0% | USD 19.97 | USD 315.29 | USD 4,780.34 | 8,535 | 5.8 | 129 | 96.3% | — |
| 20 | 0x4593f31010e8f0c1fce4af9b33021c6e6aa98d75 | 1,898 | USD 51,549.71 | 71.0% | 1,730 (91.1%) | 95.2% | USD 119.77 | USD 17.67 | USD 4,706.58 | 1,864 | 6.1 | 30 | 97.2% | — |
| 21 | 0xc84f7e76ec28ef20e7773b7b4926bfb7378be0c5 | 679 | USD 1,041,319.85 | 94.1% | 548 (80.7%) | 100.0% | USD 0.00 | USD 11.08 | USD 4,657.29 | 617 | 8.7 | 5 | 96.8% | — |
| 22 | 0x6dd9d5a1fe8021317c477104a0131205ecda8cc1 | 43 | USD 168,327.62 | 0.0% | 34 (79.1%) | 97.1% | USD 0.00 | USD 0.00 | USD 4,605.83 | 35 | 6.5 | 5 | 99.8% | — |
| 23 | 0x08458f7e9d2858027de579e4c3ca305475496b6f | 213 | USD 107,348.89 | 92.0% | 213 (100.0%) | 96.2% | USD 9.39 | USD 781.58 | USD 4,538.02 | 197 | 13.0 | 6 | 89.5% | — |
| 24 | 0x96ef5e15412e280b23b23a92c444c8d262a15523 | 97 | USD 41,902.01 | 93.8% | 44 (45.4%) | 95.5% | USD 2.02 | USD 61.74 | USD 4,310.05 | 84 | 15.9 | 6 | 89.3% | — |
| 25 | 0x8245ea0d5a476678cb354b69e136908f0757ebed | 2,072 | USD 790,612.83 | 94.7% | 1,601 (77.3%) | 99.7% | USD 9.10 | USD 82.46 | USD 4,280.58 | 1,782 | 31.3 | 5 | 80.0% | — |

### Strict MM-like leaderboard — HUN_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills | Eligible fills (scope %) | Eligible zero-fee share | Fees | Wallet platform-wide rebate† | PnL ex-rebates | FIFO RTs | VW hold h | Inventory returns | FIFO component | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xfd39b573bc2aa618c9bc226624e985133c1d109d | 1,301 | USD 376,583.07 | 94.8% | 1,033 (79.4%) | 100.0% | USD 0.00 | USD 52.62 | USD 11,641.81 | 1,297 | 14.9 | 7 | 99.8% | — |
| 2 | 0xfd8f5bba5b09d286b6d1db35cda8249bdd356f11 | 453 | USD 144,833.05 | 90.3% | 243 (53.6%) | 100.0% | USD 0.00 | USD 323.37 | USD 9,982.25 | 450 | 19.5 | 5 | 100.0% | — |
| 3 | 0x98d04f9eb52d08ef9697f6eb8d7cfe8d46e5f14a | 235 | USD 31,087.50 | 83.8% | 0 (0.0%) | n/a | USD 0.00 | USD 305.03 | USD 5,585.93 | 208 | 59.6 | 5 | 96.0% | — |
| 4 | 0x8454f39e7fa6957dfbf1ecc574820ada1c92331a | 384 | USD 207,966.00 | 96.4% | 276 (71.9%) | 100.0% | USD 0.00 | USD 889.63 | USD 5,287.61 | 348 | 9.0 | 10 | 77.7% | — |
| 5 | 0x35bbbad2415fe5e39b12da9a316cdc80b022009b | 444 | USD 255,282.51 | 94.4% | 444 (100.0%) | 100.0% | USD 0.00 | USD 505.84 | USD 5,053.30 | 438 | 1.1 | 7 | 98.7% | — |
| 6 | 0xc84f7e76ec28ef20e7773b7b4926bfb7378be0c5 | 647 | USD 1,027,126.69 | 94.1% | 548 (84.7%) | 100.0% | USD 0.00 | USD 11.08 | USD 4,558.82 | 617 | 8.7 | 5 | 98.9% | — |
| 7 | 0x96ef5e15412e280b23b23a92c444c8d262a15523 | 93 | USD 41,401.01 | 95.7% | 40 (43.0%) | 100.0% | USD 0.00 | USD 61.74 | USD 4,357.07 | 81 | 15.9 | 5 | 89.3% | — |
| 8 | 0x1f0a343513aa6060488fabe96960e6d1e177f7aa | 6,683 | USD 323,271.18 | 98.0% | 596 (8.9%) | 100.0% | USD 0.00 | USD 315.29 | USD 4,046.09 | 6,642 | 4.7 | 130 | 98.8% | — |
| 9 | 0x64f799b93cc3bd75a57ed265b07a4b4d0123afa6 | 305 | USD 43,855.17 | 87.9% | 16 (5.2%) | n/a | USD 0.00 | USD 0.00 | USD 4,032.60 | 297 | 0.7 | 7 | 100.0% | — |
| 10 | 0x368ba69507fd67972059fa178af5e459b72f2458 | 433 | USD 175,578.36 | 99.8% | 189 (43.6%) | 100.0% | USD 0.00 | USD 373.34 | USD 3,488.50 | 420 | 5.4 | 14 | 86.6% | — |
| 11 | 0x8633c1c6844c5928b284a71e211584739474a08d | 763 | USD 154,505.17 | 94.9% | 709 (92.9%) | 100.0% | USD 0.00 | USD 141.59 | USD 3,092.10 | 759 | 21.9 | 11 | 99.6% | — |
| 12 | 0x3fec6926255ec2eec1b4c8bdf4e4bad34025728e | 50 | USD 30,611.71 | 88.0% | 10 (20.0%) | n/a | USD 0.00 | USD 111.80 | USD 2,930.51 | 35 | 29.1 | 5 | 99.9% | — |
| 13 | 0xee67fa473868893bbe35a5f628298ba75168fd6d | 288 | USD 40,914.88 | 69.8% | 26 (9.0%) | 100.0% | USD 0.00 | USD 98.72 | USD 2,708.07 | 244 | 60.9 | 8 | 97.7% | — |
| 14 | 0x264cbb7263b0c336c58fb5a5de1773896e6bbaca | 1,093 | USD 294,866.74 | 95.3% | 707 (64.7%) | 100.0% | USD 0.00 | USD 776.55 | USD 2,592.32 | 1,080 | 1.6 | 26 | 99.6% | negative_fill_inventory |
| 15 | 0x47ab026767cc320ac6e62f6ec747d59cf4d795df | 106 | USD 56,424.30 | 95.3% | 86 (81.1%) | 100.0% | USD 0.00 | USD 458.72 | USD 2,199.38 | 99 | 58.8 | 6 | 99.5% | — |
| 16 | 0x6a681a1e83a5f1b49837c7a02ba1bbfd72a56679 | 578 | USD 42,467.43 | 100.0% | 73 (12.6%) | 100.0% | USD 0.00 | USD 273.22 | USD 2,123.21 | 502 | 18.6 | 15 | 53.1% | — |
| 17 | 0xd3a72382f2c459af33a839866b3f2852e4723362 | 11,258 | USD 956,304.99 | 99.9% | 6,417 (57.0%) | 100.0% | USD 0.00 | USD 12,484.11 | USD 2,103.55 | 11,239 | 0.3 | 987 | 100.0% | negative_fill_inventory |
| 18 | 0x8e78878271452acb5a5be368d117baf181e7ea77 | 1,014 | USD 73,151.06 | 96.3% | 299 (29.5%) | 100.0% | USD 0.00 | USD 11.17 | USD 2,014.48 | 991 | 15.3 | 31 | 92.6% | — |
| 19 | 0x2375550d3b98ce364476c81f663a0b878cb0691f | 144 | USD 40,178.93 | 0.0% | 136 (94.4%) | 94.1% | USD 0.57 | USD 0.00 | USD 1,751.23 | 133 | 10.2 | 9 | 99.7% | — |
| 20 | 0x21ffd2b7a212a6f277ed3eca1a9f8efcbca90d71 | 7,173 | USD 316,036.23 | 96.6% | 473 (6.6%) | 97.7% | USD 0.62 | USD 19,503.19 | USD 1,734.72 | 6,835 | 27.0 | 38 | 80.9% | fee_attribution_ambiguous |
| 21 | 0x73d225c851df25d73b14846a370bdeef9434f9b7 | 129 | USD 24,423.38 | 99.2% | 34 (26.4%) | 100.0% | USD 0.00 | USD 1,249.86 | USD 1,728.20 | 121 | 18.5 | 11 | 91.3% | — |
| 22 | 0xd97c5c25774ee6e7ebbc4456e1eaa32f2b29aedb | 137 | USD 66,974.17 | 92.7% | 59 (43.1%) | 100.0% | USD 0.00 | USD 13.52 | USD 1,704.82 | 133 | 14.8 | 9 | 100.0% | — |
| 23 | 0xbefa95c276ee8ef6ff3ef43d1c1c454f52bc300d | 6,514 | USD 13,323.22 | 99.7% | 2,763 (42.4%) | 99.9% | USD 0.00 | USD 1,530.30 | USD 1,622.82 | 5,807 | 5.7 | 68 | 85.8% | negative_fill_inventory/fee_attribution_ambiguous |
| 24 | 0xff7c2da97732a3d523996c4ec8d120e485353768 | 437 | USD 60,364.22 | 92.4% | 87 (19.9%) | 100.0% | USD 0.00 | USD 0.00 | USD 1,613.86 | 371 | 46.0 | 11 | 91.8% | — |
| 25 | 0xd8d5289c35124b5353f5a10a28b8cc29aec38935 | 1,065 | USD 525,203.02 | 93.2% | 603 (56.6%) | 98.7% | USD 11.57 | USD 1,238.71 | USD 1,576.08 | 863 | 3.1 | 18 | 59.8% | fee_attribution_ambiguous |

### Strict MM-like leaderboard — COL_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills | Eligible fills (scope %) | Eligible zero-fee share | Fees | Wallet platform-wide rebate† | PnL ex-rebates | FIFO RTs | VW hold h | Inventory returns | FIFO component | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 1,192 | USD 224,496.40 | 97.8% | 1,192 (100.0%) | 97.9% | USD 328.89 | USD 11,228.77 | USD 52,919.97 | 778 | 69.9 | 5 | 73.5% | — |
| 2 | 0x0a7ed19909e9b4c6cd0fc1d59c1d481415a1fce4 | 222 | USD 63,806.30 | 85.6% | 222 (100.0%) | 85.6% | USD 145.31 | USD 3,691.44 | USD 7,775.68 | 203 | 0.7 | 6 | 98.2% | — |
| 3 | 0x6b17d237e6b7f74ce8171d6dc68e07bc21c803b2 | 4,117 | USD 138,540.42 | 98.3% | 2,302 (55.9%) | 98.7% | USD 15.58 | USD 1,603.64 | USD 2,164.12 | 3,612 | 10.1 | 132 | 91.1% | — |
| 4 | 0xcc7c1f044ddb2e002345f9f05b4e941e54a11d18 | 889 | USD 72,593.97 | 100.0% | 889 (100.0%) | 100.0% | USD 0.00 | USD 1,195.38 | USD 1,622.14 | 885 | 13.9 | 23 | 100.0% | — |
| 5 | 0x5833ff8a9876e3f24e266f58e57184148a604893 | 106 | USD 10,744.82 | 80.2% | 106 (100.0%) | 80.2% | USD 22.17 | USD 42.82 | USD 1,009.91 | 86 | 48.8 | 5 | 90.6% | — |
| 6 | 0x4eaa84eb42e31ab0d7723ebc6748660ede409dd9 | 1,704 | USD 22,340.40 | 74.1% | 674 (39.6%) | 96.1% | USD 26.16 | USD 3.18 | USD 774.12 | 1,676 | 70.5 | 32 | 96.8% | — |
| 7 | 0x401ee31e9ebf9ab9f6315cd95faca5f950436fc9 | 477 | USD 171,931.64 | 98.1% | 456 (95.6%) | 93.0% | USD 61.31 | USD 974.13 | USD 772.12 | 394 | 0.6 | 8 | 82.7% | — |
| 8 | 0xeda045f91a24d199f78ef1f591fbbe6a5b517dff | 349 | USD 7,454.55 | 98.0% | 0 (0.0%) | n/a | USD 0.00 | USD 1,522.98 | USD 515.71 | 316 | 28.1 | 18 | 98.5% | — |
| 9 | 0x4ffe49ba2a4cae123536a8af4fda48faeb609f71 | 2,114 | USD 5,154.12 | 94.7% | 678 (32.1%) | 95.7% | USD 2.90 | USD 3,081.90 | USD 501.69 | 1,751 | 40.5 | 87 | 79.8% | — |
| 10 | 0xc8ab97a9089a9ff7e6ef0688e6e591a066946418 | 3,213 | USD 386,416.11 | 99.3% | 2,877 (89.5%) | 99.5% | USD 13.96 | USD 46,860.33 | USD 487.66 | 3,083 | 34.8 | 13 | 76.7% | — |
| 11 | 0x682c92615993fd1f75cdfe101efdc1a8adcb17ae | 8,222 | USD 32,419.18 | 99.0% | 6,112 (74.3%) | 99.4% | USD 5.44 | USD 2,639.49 | USD 413.19 | 8,202 | 1.8 | 313 | 98.8% | — |
| 12 | 0x1229233bad2fd7462955c6383448904b5432df38 | 729 | USD 61,830.40 | 84.2% | 618 (84.8%) | 87.7% | USD 296.65 | USD 262.40 | USD 396.63 | 719 | 35.5 | 27 | 70.0% | — |
| 13 | 0x5f8839f9b16c66d0c75f69d150e69a876e391d4d | 2,566 | USD 85,058.59 | 95.6% | 1,620 (63.1%) | 98.0% | USD 41.44 | USD 352.50 | USD 330.57 | 2,535 | 19.3 | 88 | 98.2% | — |
| 14 | 0x29d1b7603b5aace930b6f1885320d55a922e32f4 | 172 | USD 60,099.72 | 82.6% | 172 (100.0%) | 82.6% | USD 137.42 | USD 89.78 | USD 320.76 | 139 | 26.6 | 5 | 83.9% | — |
| 15 | 0x1f0a343513aa6060488fabe96960e6d1e177f7aa | 618 | USD 51,039.06 | 93.9% | 268 (43.4%) | 95.5% | USD 7.98 | USD 315.29 | USD 288.33 | 582 | 49.8 | 14 | 98.5% | — |
| 16 | 0x1c266db0f8529b1f25b77123e9c0c918ac2f6e31 | 280 | USD 5,566.96 | 78.2% | 0 (0.0%) | n/a | USD 0.00 | USD 0.00 | USD 246.13 | 230 | 21.6 | 15 | 95.5% | — |
| 17 | 0x8190816855b676a5efd8c5b344135a72337bb247 | 217 | USD 18,513.18 | 83.9% | 217 (100.0%) | 83.9% | USD 22.80 | USD 121.29 | USD 227.39 | 193 | 68.6 | 10 | 95.1% | — |
| 18 | 0xbe31401e28fb1b527c5042882a92e724e6dca6a2 | 70 | USD 20,691.96 | 100.0% | 70 (100.0%) | 91.4% | USD 0.41 | USD 15.81 | USD 218.60 | 64 | 4.9 | 6 | 99.8% | — |
| 19 | 0x816820f2b81c84a3ecf545c9b5d19b81b4656857 | 57 | USD 12,092.41 | 94.7% | 57 (100.0%) | 87.7% | USD 3.94 | USD 239.53 | USD 206.00 | 54 | 0.9 | 7 | 98.2% | — |
| 20 | 0x6268011cdc123d7ab44cabdef70b7a571f286031 | 936 | USD 2,026.20 | 88.6% | 931 (99.5%) | 89.8% | USD 11.48 | USD 160.66 | USD 203.66 | 897 | 0.9 | 85 | 94.4% | — |
| 21 | 0xa42940d612c9b2e02d3cd35a4c3f17062782368a | 157 | USD 21,109.27 | 100.0% | 157 (100.0%) | 100.0% | USD 0.00 | USD 317.21 | USD 195.28 | 147 | 10.0 | 17 | 100.0% | — |
| 22 | 0x6b0ebbef2630b9a0da35594bf7ddd1911246e3ea | 760 | USD 6,062.52 | 99.5% | 448 (58.9%) | 100.0% | USD 0.00 | USD 726.47 | USD 180.98 | 724 | 3.2 | 80 | 90.5% | — |
| 23 | 0x8a98109fb0f1d87d9bfcb4486ba3587b95c51b92 | 700 | USD 27,816.98 | 0.0% | 700 (100.0%) | 96.9% | USD 16.14 | USD 4.32 | USD 179.99 | 592 | 4.1 | 49 | 55.8% | — |
| 24 | 0xec829edacf53dad6fdfb89f752ca7b8004a26eaa | 1,935 | USD 3,501.39 | 99.6% | 1,148 (59.3%) | 99.7% | USD 1.33 | USD 992.46 | USD 172.49 | 1,648 | 15.2 | 184 | 91.8% | — |
| 25 | 0x4959175440b8f38229b32f2f036057f6893ea6f5 | 1,133 | USD 18,991.94 | 99.7% | 212 (18.7%) | 100.0% | USD 0.00 | USD 0.00 | USD 160.42 | 1,084 | 5.0 | 104 | 99.1% | — |

### Strict MM-like leaderboard — PER_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills | Eligible fills (scope %) | Eligible zero-fee share | Fees | Wallet platform-wide rebate† | PnL ex-rebates | FIFO RTs | VW hold h | Inventory returns | FIFO component | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,654 | USD 594,916.10 | 87.5% | 1,654 (100.0%) | 97.8% | USD 1,839.47 | USD 713.70 | USD 75,412.95 | 1,328 | 30.0 | 16 | 71.1% | — |
| 2 | 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 678 | USD 591,887.07 | 88.5% | 678 (100.0%) | 92.3% | USD 946.35 | USD 999.82 | USD 26,111.87 | 673 | 50.7 | 12 | 96.6% | — |
| 3 | 0xef5c8e2487d9b4d07565616b337e69f8488a661d | 306 | USD 173,753.92 | 82.7% | 306 (100.0%) | 94.1% | USD 1,111.86 | USD 319.21 | USD 17,269.93 | 299 | 32.3 | 16 | 94.3% | — |
| 4 | 0x98d04f9eb52d08ef9697f6eb8d7cfe8d46e5f14a | 618 | USD 109,805.25 | 81.7% | 618 (100.0%) | 93.7% | USD 121.41 | USD 305.03 | USD 12,295.80 | 515 | 19.3 | 13 | 87.1% | — |
| 5 | 0x40cfb29411d29f4fa0908f2a121297042cccd21d | 1,734 | USD 174,344.65 | 88.6% | 1,698 (97.9%) | 98.7% | USD 148.05 | USD 1,242.11 | USD 8,911.18 | 1,592 | 14.1 | 15 | 90.9% | — |
| 6 | 0x2b9dbf4b6e0e11309a9d6d2a09b72f65f652adc0 | 994 | USD 111,752.07 | 85.3% | 947 (95.3%) | 98.1% | USD 248.11 | USD 130.01 | USD 8,048.93 | 734 | 35.0 | 7 | 88.1% | — |
| 7 | 0xc20c3ccd49e06eda6c336539abc16714872be2bd | 71 | USD 44,029.09 | 88.7% | 71 (100.0%) | 88.7% | USD 263.46 | USD 93.95 | USD 6,642.78 | 68 | 1.7 | 5 | 96.3% | — |
| 8 | 0x7809521c05ba2724429e2ce924399eecad0c196a | 329 | USD 89,741.57 | 92.4% | 329 (100.0%) | 99.4% | USD 115.58 | USD 92.73 | USD 6,324.71 | 321 | 46.3 | 7 | 98.2% | — |
| 9 | 0xd8d5289c35124b5353f5a10a28b8cc29aec38935 | 1,839 | USD 304,612.46 | 88.2% | 1,839 (100.0%) | 97.0% | USD 205.74 | USD 1,238.71 | USD 5,203.15 | 1,648 | 49.5 | 18 | 97.1% | — |
| 10 | 0x2200b75c27835935a40308779b5656c09ab40f2d | 411 | USD 146,951.44 | 80.0% | 407 (99.0%) | 91.6% | USD 239.92 | USD 258.03 | USD 4,724.71 | 365 | 0.7 | 16 | 84.8% | — |
| 11 | 0x4593f31010e8f0c1fce4af9b33021c6e6aa98d75 | 1,876 | USD 51,535.21 | 70.7% | 1,708 (91.0%) | 95.2% | USD 119.63 | USD 17.67 | USD 4,714.22 | 1,851 | 6.1 | 30 | 97.3% | — |
| 12 | 0xfd8f5bba5b09d286b6d1db35cda8249bdd356f11 | 2,026 | USD 570,295.59 | 84.3% | 2,026 (100.0%) | 94.0% | USD 618.66 | USD 323.37 | USD 4,711.51 | 1,994 | 31.0 | 11 | 89.8% | — |
| 13 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | 373 | USD 141,420.21 | 63.5% | 365 (97.9%) | 87.1% | USD 1,004.81 | USD 402.97 | USD 4,549.28 | 346 | 4.9 | 22 | 99.6% | — |
| 14 | 0xe2b1fc269d0c2e27da11fadd1d9596fe28227d2a | 275 | USD 94,406.97 | 85.5% | 275 (100.0%) | 85.5% | USD 528.76 | USD 551.27 | USD 4,056.25 | 271 | 4.6 | 11 | 89.7% | — |
| 15 | 0xc668530ed5c50cbe964a5f36cf8fbb8da8da4f06 | 184 | USD 11,301.68 | 29.3% | 184 (100.0%) | 91.8% | USD 23.75 | USD 15.04 | USD 4,045.08 | 174 | 22.9 | 10 | 97.2% | — |
| 16 | 0xfb5148fc7223630e0967dbfa8cd920d83ab4742d | 121 | USD 29,782.87 | 95.0% | 121 (100.0%) | 95.0% | USD 111.71 | USD 1,286.57 | USD 3,605.53 | 116 | 3.1 | 6 | 97.1% | — |
| 17 | 0x8245ea0d5a476678cb354b69e136908f0757ebed | 1,590 | USD 661,840.21 | 94.4% | 1,590 (100.0%) | 99.9% | USD 8.55 | USD 82.46 | USD 2,782.60 | 1,324 | 36.2 | 5 | 71.2% | — |
| 18 | 0x47ab026767cc320ac6e62f6ec747d59cf4d795df | 650 | USD 105,857.03 | 95.8% | 650 (100.0%) | 98.2% | USD 6.81 | USD 458.72 | USD 2,780.82 | 499 | 49.2 | 9 | 86.2% | — |
| 19 | 0x2e62eddf5acffa59a97706de674348a2e600b22e | 6,745 | USD 217,468.96 | 98.8% | 5,500 (81.5%) | 99.7% | USD 7.78 | USD 2,050.02 | USD 2,560.97 | 6,277 | 1.7 | 456 | 91.7% | — |
| 20 | 0x57bed5fa10152f14d99a7cdc6b76f66c5e440deb | 1,119 | USD 19,466.71 | 77.2% | 980 (87.6%) | 99.3% | USD 41.47 | USD 0.00 | USD 2,404.72 | 1,113 | 60.9 | 10 | 98.3% | — |
| 21 | 0x2cfd4ea62cb479488df607081bdf806b180ee348 | 111 | USD 55,638.06 | 66.7% | 111 (100.0%) | 85.6% | USD 568.59 | USD 43.40 | USD 2,395.60 | 105 | 11.1 | 19 | 83.9% | — |
| 22 | 0x2375550d3b98ce364476c81f663a0b878cb0691f | 1,025 | USD 22,147.64 | 0.0% | 960 (93.7%) | 98.1% | USD 39.59 | USD 0.00 | USD 1,995.47 | 919 | 34.7 | 14 | 97.4% | — |
| 23 | 0xe80b7905777b7c75a3eef9b9125ee0b335867e1b | 447 | USD 37,077.61 | 79.2% | 447 (100.0%) | 91.5% | USD 184.42 | USD 9.96 | USD 1,948.69 | 443 | 41.0 | 9 | 92.0% | — |
| 24 | 0x72a22d4d155079da8f50c89d4c6c03b6428e7b65 | 266 | USD 111,674.14 | 91.0% | 266 (100.0%) | 94.7% | USD 31.10 | USD 484.27 | USD 1,942.16 | 262 | 43.6 | 13 | 99.3% | — |
| 25 | 0x076baf4f9155d6d596bbcd2e380c798a5f1357af | 3,095 | USD 175,281.46 | 88.9% | 2,971 (96.0%) | 98.9% | USD 50.84 | USD 350.07 | USD 1,839.65 | 3,080 | 23.7 | 23 | 97.4% | — |

### Strict-tier anatomy — pooled top 10

FIFO PnL is matched buy/sell economics before fees. Directional + fee residual equals PnL ex-rebates less FIFO PnL. Tail values are notional below 5¢ / above 95¢. The strict pooled top 10 have weighted median FIFO holds of 14.9–51.2h and 5–16 top-token reversion cycles. This removes the prior 0xffe985… false positive, whose 4,103h median FIFO hold represented multi-week inventory rather than short-horizon quoting.

| Wallet | Fills | Markets/events | Two-sided tokens | FIFO RTs | FIFO PnL | Directional + fee residual | VW hold h | Inventory returns | FIFO share | <5¢ notional | >95¢ notional | Fees | Wallet platform-wide rebate† | PnL ex-rebates | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,796 | 42/17 | 76.6% | 1,451 | USD 57,148.99 | USD 23,871.56 | 36.3 | 16 | 70.5% | USD 2,775.40 | USD 246,833.54 | USD 1,840.97 | USD 713.70 | USD 81,020.55 | — |
| 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 1,433 | 8/4 | 88.9% | 1,363 | USD 45,433.37 | USD 9,997.90 | 36.1 | 11 | 82.0% | USD 1,000.00 | USD 571,285.86 | USD 1,065.54 | USD 999.82 | USD 55,431.27 | — |
| 0x9c2617462567859fcf5764e03b9a687ebca274bc | 1,253 | 9/5 | 88.9% | 1,075 | USD 12,035.25 | USD 10,658.91 | 48.6 | 5 | 53.0% | USD 0.00 | USD 724,177.19 | USD 147.97 | USD 1,072.73 | USD 22,694.16 | — |
| 0x98d04f9eb52d08ef9697f6eb8d7cfe8d46e5f14a | 997 | 49/24 | 81.8% | 841 | USD 18,982.69 | USD 1,778.81 | 51.2 | 8 | 91.4% | USD 383.15 | USD 70,568.23 | USD 178.00 | USD 305.03 | USD 20,761.50 | — |
| 0xef5c8e2487d9b4d07565616b337e69f8488a661d | 308 | 7/2 | 100.0% | 300 | USD 18,398.63 | USD -1,112.17 | 32.3 | 16 | 94.3% | USD 753.62 | USD 29,555.28 | USD 1,112.13 | USD 319.21 | USD 17,286.45 | — |
| 0x515e2144bf38402568979db4ea7f6b64636c8309 | 425 | 3/2 | 100.0% | 421 | USD 14,544.89 | USD -182.28 | 26.2 | 6 | 98.8% | USD 0.00 | USD 36,190.74 | USD 182.29 | USD 564.20 | USD 14,362.61 | — |
| 0xfd8f5bba5b09d286b6d1db35cda8249bdd356f11 | 2,811 | 26/13 | 100.0% | 2,736 | USD 13,275.22 | USD -612.76 | 35.4 | 12 | 95.6% | USD 808.22 | USD 242,861.37 | USD 674.02 | USD 323.37 | USD 12,662.46 | — |
| 0xfd39b573bc2aa618c9bc226624e985133c1d109d | 1,301 | 4/2 | 100.0% | 1,297 | USD 11,623.08 | USD 18.74 | 14.9 | 7 | 99.8% | USD 0.00 | USD 45,662.66 | USD 0.00 | USD 52.62 | USD 11,641.81 | — |
| 0x40cfb29411d29f4fa0908f2a121297042cccd21d | 2,525 | 70/26 | 94.2% | 2,279 | USD 7,404.35 | USD 3,487.34 | 15.7 | 8 | 68.0% | USD 2,024.23 | USD 65,446.05 | USD 230.54 | USD 1,242.11 | USD 10,891.69 | — |
| 0xd8d5289c35124b5353f5a10a28b8cc29aec38935 | 3,300 | 74/33 | 72.8% | 2,790 | USD 10,725.90 | USD -2,580.08 | 21.2 | 31 | 80.6% | USD 744.49 | USD 543,235.12 | USD 364.62 | USD 1,238.71 | USD 8,145.82 | — |

### Strict-tier anatomy — top 3 per family

| Wallet | Fills | Markets/events | Two-sided tokens | FIFO RTs | FIFO PnL | Directional + fee residual | VW hold h | Inventory returns | FIFO share | <5¢ notional | >95¢ notional | Fees | Wallet platform-wide rebate† | PnL ex-rebates | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0xfd39b573bc2aa618c9bc226624e985133c1d109d | 1,301 | 4/2 | 100.0% | 1,297 | USD 11,623.08 | USD 18.74 | 14.9 | 7 | 99.8% | USD 0.00 | USD 45,662.66 | USD 0.00 | USD 52.62 | USD 11,641.81 | — |
| 0xfd8f5bba5b09d286b6d1db35cda8249bdd356f11 | 453 | 3/3 | 100.0% | 450 | USD 9,982.25 | USD 0.01 | 19.5 | 5 | 100.0% | USD 0.00 | USD 41,856.07 | USD 0.00 | USD 323.37 | USD 9,982.25 | — |
| 0x98d04f9eb52d08ef9697f6eb8d7cfe8d46e5f14a | 235 | 10/5 | 63.6% | 208 | USD 5,365.05 | USD 220.88 | 59.6 | 5 | 96.0% | USD 9.21 | USD 13,677.74 | USD 0.00 | USD 305.03 | USD 5,585.93 | — |

| Wallet | Fills | Markets/events | Two-sided tokens | FIFO RTs | FIFO PnL | Directional + fee residual | VW hold h | Inventory returns | FIFO share | <5¢ notional | >95¢ notional | Fees | Wallet platform-wide rebate† | PnL ex-rebates | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0x23d81ba9371e576015c1e562db09c689f56b0288 | 1,192 | 10/5 | 53.8% | 778 | USD 38,885.99 | USD 14,033.99 | 69.9 | 5 | 73.5% | USD 810.79 | USD 103,674.63 | USD 328.89 | USD 11,228.77 | USD 52,919.97 | — |
| 0x0a7ed19909e9b4c6cd0fc1d59c1d481415a1fce4 | 222 | 15/8 | 100.0% | 203 | USD 7,920.99 | USD -145.30 | 0.7 | 6 | 98.2% | USD 78.50 | USD 46,874.03 | USD 145.31 | USD 3,691.44 | USD 7,775.68 | — |
| 0x6b17d237e6b7f74ce8171d6dc68e07bc21c803b2 | 4,117 | 33/2 | 63.6% | 3,612 | USD 2,398.51 | USD -234.39 | 10.1 | 132 | 91.1% | USD 1,144.70 | USD 8,670.50 | USD 15.58 | USD 1,603.64 | USD 2,164.12 | — |

| Wallet | Fills | Markets/events | Two-sided tokens | FIFO RTs | FIFO PnL | Directional + fee residual | VW hold h | Inventory returns | FIFO share | <5¢ notional | >95¢ notional | Fees | Wallet platform-wide rebate† | PnL ex-rebates | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,654 | 36/12 | 80.5% | 1,328 | USD 53,595.71 | USD 21,817.24 | 30.0 | 16 | 71.1% | USD 2,775.40 | USD 190,327.00 | USD 1,839.47 | USD 713.70 | USD 75,412.95 | — |
| 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 678 | 4/2 | 100.0% | 673 | USD 27,058.22 | USD -946.35 | 50.7 | 12 | 96.6% | USD 1,000.00 | USD 165,500.27 | USD 946.35 | USD 999.82 | USD 26,111.87 | — |
| 0xef5c8e2487d9b4d07565616b337e69f8488a661d | 306 | 6/1 | 100.0% | 299 | USD 18,381.83 | USD -1,111.90 | 32.3 | 16 | 94.3% | USD 753.62 | USD 29,555.28 | USD 1,111.86 | USD 319.21 | USD 17,269.93 | — |

### Strict-tier winners versus losers

Both groups are highly two-sided with near-100% median zero-fee share on eligible fills. The strict screen describes behavior; it does not guarantee positive edge.

| Scope | Tier side | Count | Median PnL ex-rebates | Median PnL/notional | Eligible zero-fee share | Two-sided tokens | VW hold h | FIFO share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | winners | 438 | USD 137.94 | 1.5% | 99.1% | 98.2% | 6.8 | 93.3% |
| POOLED | losers | 230 | USD -110.60 | -1.5% | 99.2% | 100.0% | 1.8 | 98.1% |
| HUN_2026 | winners | 194 | USD 85.96 | 1.2% | 100.0% | 100.0% | 4.5 | 98.7% |
| HUN_2026 | losers | 64 | USD -18.72 | -1.4% | 100.0% | 100.0% | 2.6 | 99.4% |
| COL_2026 | winners | 93 | USD 50.73 | 1.5% | 98.7% | 100.0% | 9.1 | 90.9% |
| COL_2026 | losers | 67 | USD -38.43 | -2.0% | 99.1% | 100.0% | 2.7 | 98.9% |
| PER_2026 | winners | 239 | USD 103.82 | 1.6% | 98.6% | 100.0% | 6.1 | 92.5% |
| PER_2026 | losers | 132 | USD -209.54 | -1.8% | 99.3% | 100.0% | 3.9 | 95.8% |

### Ex-rebate PnL leaders across all strategies

This corrects the broad all-strategy PnL ranking. No rebate allocation is included.

| Rank | Wallet | PnL ex-rebates | Fills | Notional | Fees |
| --- | --- | --- | --- | --- | --- |
| 1 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | USD 377,034.08 | 9,096 | USD 2,389,908.98 | USD 2,988.47 |
| 2 | 0x88b59d79b6e1659c95a0043028e5bb7a26e6205c | USD 199,048.69 | 2,170 | USD 1,091,299.19 | USD 1,020.32 |
| 3 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | USD 183,892.90 | 1,406 | USD 536,023.94 | USD 8.43 |
| 4 | 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | USD 152,830.98 | 42 | USD 550,123.74 | USD 7.05 |
| 5 | 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | USD 149,796.06 | 384 | USD 265,131.51 | USD 1,165.15 |

The table below reranks the original pooled top 25 and family top 5 by corrected PnL. Behavior tags are carried over only where the previous anatomy already covered that wallet; they are indicative, while V2 PnL and fee columns are recomputed.

| Scope | Rank | Wallet | Fills | Eligible zero-fee share | Fees | Platform-wide rebate† | PnL ex-rebates | Behavior tag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | 1 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | 9,096 | 97.4% | USD 2,988.47 | USD 2,727.24 | USD 377,034.08 | Directional hold / resolution-led |
| POOLED | 2 | 0x88b59d79b6e1659c95a0043028e5bb7a26e6205c | 2,170 | 92.6% | USD 1,020.32 | USD 337.28 | USD 199,048.69 | Tail-priced directional exposure |
| POOLED | 3 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | 1,406 | 99.5% | USD 8.43 | USD 1.24 | USD 183,892.90 | Two-sided inventory recycling / round-trip-led |
| POOLED | 4 | 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | 42 | 90.9% | USD 7.05 | USD 21.46 | USD 152,830.98 | Tail-priced directional exposure |
| POOLED | 5 | 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | 384 | 98.2% | USD 1,165.15 | USD 669.16 | USD 149,796.06 | Directional hold / resolution-led |
| POOLED | 6 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | 3,208 | 99.3% | USD 7.01 | USD 2.24 | USD 147,760.63 | Two-sided inventory recycling / round-trip-led |
| POOLED | 7 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | 871 | 98.8% | USD 347.33 | USD 432.55 | USD 147,461.83 | Mixed recycling; activity near large VWAP moves |
| POOLED | 8 | 0x4b28a660415bf1d58f358b32fc729dc6b396d19a | 3,589 | 100.0% | USD 0.00 | USD 0.00 | USD 136,144.27 | Directional hold / resolution-led |
| POOLED | 9 | 0x448861155279dbf833d041b963e3ac854599e319 | 3,531 | 92.1% | USD 3,498.80 | USD 5,203.88 | USD 130,632.50 | Two-sided inventory recycling / round-trip-led |
| POOLED | 10 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | 141 | 100.0% | USD 0.00 | USD 0.00 | USD 118,152.99 | Two-sided inventory recycling / round-trip-led |
| POOLED | 11 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | 284 | 100.0% | USD 0.00 | USD 560.99 | USD 111,283.82 | Two-sided inventory recycling / round-trip-led |
| POOLED | 12 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | 6,732 | 98.5% | USD 4,342.78 | USD 1,092.44 | USD 103,174.96 | Two-sided inventory recycling / round-trip-led |
| POOLED | 13 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | 618 | 96.1% | USD 30.35 | USD 0.00 | USD 97,076.63 | Mixed inventory recycling and direction |
| POOLED | 14 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | 579 | 100.0% | USD 0.00 | USD 732.65 | USD 97,021.80 | Two-sided inventory recycling / round-trip-led |
| POOLED | 15 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | 217 | 100.0% | USD 0.00 | USD 0.00 | USD 91,601.47 | Two-sided inventory recycling / round-trip-led |
| POOLED | 16 | 0xcf6087abd66e16b59a122d45bfcdc05d4b7247ce | 1,006 | 99.0% | USD 667.85 | USD 269.11 | USD 89,108.28 | Tail-priced directional exposure |
| POOLED | 17 | 0xa527e3a082c41b56f81d6bd98cb9fde1e9904228 | 125 | 72.7% | USD 273.13 | USD 0.00 | USD 87,020.89 | Close-to-resolution directional exposure |
| POOLED | 18 | 0x22e4248bdb066f65c9f11cd66cdd3719a28eef1c | 1,078 | 97.4% | USD 1,164.17 | USD 1,006.68 | USD 81,882.69 | Directional hold / resolution-led |
| POOLED | 19 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,796 | 97.7% | USD 1,840.97 | USD 713.70 | USD 81,020.55 | Two-sided inventory recycling / round-trip-led |
| POOLED | 20 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 2,213 | 96.4% | USD 2,885.45 | USD 11,228.77 | USD 79,243.87 | Mixed inventory recycling and direction |
| POOLED | 21 | 0xa83c7adfd9c81b49f69b8a16e8c77641fa9aa8f4 | 769 | 29.1% | USD 1,736.44 | USD 100.28 | USD 78,833.59 | Two-sided inventory recycling / round-trip-led |
| POOLED | 22 | 0x94c4fe3b18435aa92024c7c1589537fcda3f43ca | 718 | 100.0% | USD 0.00 | USD 2,014.11 | USD 68,127.07 | Two-sided inventory recycling / round-trip-led |
| POOLED | 23 | 0x016a003d0a79f87fb2da292af3d520afc8ec848d | 1,218 | 100.0% | USD 0.00 | USD 0.00 | USD 67,513.16 | Two-sided inventory recycling / round-trip-led |
| POOLED | 24 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | 630 | 82.9% | USD 1,726.65 | USD 402.97 | USD 62,244.34 | Mixed inventory recycling and direction |
| POOLED | 25 | 0x5b90d4fab4e082795eeb231b121691a0cb12c44e | 106 | 100.0% | USD 0.00 | USD 0.00 | USD 62,157.91 | Two-sided inventory recycling / round-trip-led |
| HUN_2026 | 1 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | 4,858 | 100.0% | USD 0.00 | USD 2,727.24 | USD 293,225.48 | Directional hold / resolution-led |
| HUN_2026 | 2 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | 1,406 | 99.5% | USD 8.43 | USD 1.24 | USD 183,892.90 | Two-sided inventory recycling / round-trip-led |
| HUN_2026 | 3 | 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | 42 | 90.9% | USD 7.05 | USD 21.46 | USD 152,830.98 | Tail-priced directional exposure |
| HUN_2026 | 4 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | 3,208 | 99.3% | USD 7.01 | USD 2.24 | USD 147,760.63 | Two-sided inventory recycling / round-trip-led |
| HUN_2026 | 5 | 0x4b28a660415bf1d58f358b32fc729dc6b396d19a | 3,589 | 100.0% | USD 0.00 | USD 0.00 | USD 136,144.27 | Directional hold / resolution-led |
| COL_2026 | 1 | 0xa83c7adfd9c81b49f69b8a16e8c77641fa9aa8f4 | 751 | 30.1% | USD 1,400.95 | USD 100.28 | USD 79,199.84 | Two-sided inventory recycling / round-trip-led |
| COL_2026 | 2 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | 253 | 74.0% | USD 721.84 | USD 402.97 | USD 57,735.14 | Mixed inventory recycling and direction |
| COL_2026 | 3 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 1,192 | 97.9% | USD 328.89 | USD 11,228.77 | USD 52,919.97 | Two-sided inventory recycling / round-trip-led |
| COL_2026 | 4 | 0xcb25c43d98019b6acf4d6912a231bbb689a45ab5 | 1,320 | 67.5% | USD 1,361.59 | USD 20.37 | USD 42,702.95 | Two-sided inventory recycling / round-trip-led |
| COL_2026 | 5 | 0x2d4bf8f846bf68f43b9157bf30810d334ac6ca7a | 24 | n/a | USD 0.00 | USD 275.04 | USD 40,142.19 | not separately classified |
| PER_2026 | 1 | 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | 384 | 98.2% | USD 1,165.15 | USD 669.16 | USD 149,796.06 | Directional hold / resolution-led |
| PER_2026 | 2 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | 737 | 98.6% | USD 347.33 | USD 432.55 | USD 148,883.88 | Mixed recycling; activity near large VWAP moves |
| PER_2026 | 3 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | 6,210 | 98.5% | USD 4,306.32 | USD 1,092.44 | USD 98,829.43 | Two-sided inventory recycling / round-trip-led |
| PER_2026 | 4 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | 3,529 | 96.7% | USD 2,690.97 | USD 2,727.24 | USD 87,713.21 | Directional hold / resolution-led |
| PER_2026 | 5 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,654 | 97.8% | USD 1,839.47 | USD 713.70 | USD 75,412.95 | Two-sided inventory recycling / round-trip-led |

### Reconciliation and prior cross-check

| Scope | Corrected sum PnL | −net fees | Residual |
| --- | ---: | ---: | ---: |
| HUN_2026 | USD −2,588.79 | USD −2,588.66 | USD −0.13 |
| COL_2026 | USD −91,047.57 | USD −90,803.28 | USD −244.28 |
| PER_2026 | USD −198,527.61 | USD −198,527.41 | USD −0.19 |

HUN/PER residuals are sub-dollar. COL’s USD −244.28 is the previously audited late-fill anomaly: post-resolution fills across five markets contribute approximately USD −244 of cashflow imbalance; this is retained rather than tuned away. The zero-sum identity is fill cashflow + resolution payout − fees. No rebate enters it. The prior API cross-check was key-exact for its selected top five and remains documented in V1; the corrected all-strategy top five was not queried again in this fix pass.

### What I did NOT do / caveats

- Wallet rebate transfers are platform-wide and have no market attribution in the available data. V2 reports them separately and excludes them from PnL.
- Historical fee settings are absent for HUN/COL snapshots. The eligibility proxy can omit fee-enabled conditions that had no observed fee leg.
- The classifier is fill-based behavior and cannot prove posted resting quotes. Chain order-role is not a real-world liquidity-provider indicator.
- Negative starting inventory means shares entered outside these fills; basis is unknown. FIFO decomposition inherits that limitation.
- Inventory return episodes are defined as exposure rising to a running peak then falling to ≤10% of that peak, on five highest-notional tokens. Another cycle definition could change strict counts.
- The V2 strict anatomy tables report recycled inventory, holding time, tail exposure, fees, and market/event coverage. Detailed timing relative to resolution and big moves remains in the original V1 anatomy for previously selected addresses.
- No phase 2 analysis was started.

### V2 artifacts

- Corrected standard deliverables: outputs/mm_leaderboard.csv, outputs/other_profitable_strategies.csv, outputs/strategy_anatomy.csv, and outputs/wallet_metrics_*.parquet. Their original versions are retained with _v1 suffixes.
- Explicit V2 copies and fee evidence: outputs/mm_leaderboard_v2.csv, outputs/other_profitable_strategies_v2.csv, outputs/wallet_fee_eligibility_v2.parquet, outputs/wallet_metrics_*_v2.parquet, and outputs/platform_wide_wallet_rebates_v2.csv.
- Funnel, winners/losers, strict leaderboards, anatomy, and detailed strict metrics: outputs/strict_funnel_winner_loser_v2.csv, outputs/strict_leaderboard_*_v2.csv, outputs/strict_anatomy_*_v2.csv, outputs/strict_fifo_metrics_v2.csv, and outputs/strict_metrics_*_v2.parquet.

---

# REPORT V1 — historical audit trail (superseded by REPORT V2 above)







# Market-making-like wallets and profitable strategies in HUN, COL, and PER (2026)

Analysis date: 2026-09-28. Source scope is the prebuilt per-fill corpus supplied for these three election families.

## Executive summary

The largest fill-ledger PnL belongs to **0x629bc4a1e53e1d475beb7ea3d388791e96dd995a** at **$379,761** pooled. Its FIFO buy-to-sell proxy is **−$2,023**, while terminal resolution exposure contributes **+$382,046**: the result is directional/resolution-led, not repeatable spread capture. It traded 9,096 fills, about $2.39m notional, and 97 markets across all three families.

Under the ADDENDUM classifier (zero-fee share plus same-token two-sided behavior), the highest PnL pooled MM-like wallet is **0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599** at **$183,894**. It has 1,406 fills/$536k turnover, 99.9% zero-fee fills, and buys and sells on one of its two traded tokens. FIFO matched buy-to-sell PnL is +$130.9k and resolution/directional PnL is +$53.0k; its median FIFO hold is about 4,104 hours and 85.4% of fills were more than 30 days from resolution. That pattern is multi-week inventory recycling on two HUN markets, not intraday quoting.

The winner/loser comparison shows that high zero-fee and two-sided shares alone do not separate profitable wallets: pooled MM-like winners and losers both have median zero-fee shares near 100% and median two-sided-token shares of 100%. Winners have median PnL +$514 and median PnL/notional +3.29%; losers have median PnL −$89 and −1.54%. Results depend on inventory direction, entry price, event selection, and resolution outcomes.

**The classifier is a behavioral proxy, not proof of posted liquidity.** Chain maker/taker order roles are retained only as descriptive protocol fields. The report includes a counterfactual role-based count to show the difference.

## Scope and exact gates

Inventory counts below are the supplied market-universe counts. The supplied coverage windows are HUN 2025-07-25 to 2026-05-09, COL 2025-07-29 to 2026-07-15, and PER 2025-12-16 to 2026-07-30. “Markets with fills” are the unique conditions represented in the OrderFilled corpus; many inventory markets had no fills. The pooled wallet count deduplicates addresses across families.

| Scope | Inventory markets | Resolved inventory | Markets with fills | Fills actual / target | Exchange-taker rows actual / target | Wallets | Fill notional | Protocol maker-order role fills | Protocol maker-order role notional | Zero-fee fills | Zero-fee notional | Net fees | Allocated rebates | Total fill-ledger PnL | MM-like @80% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | 973 | 972 | 532 | 2,746,350 / 2,746,350 | 1,074,616 / 1,074,616 | 121,639 | $232,837,636.18 | 60.9% | 50.2% | 90.9% | 85.8% | $291,919.36 | $1,429,210.51 | $1,137,046.55 | 2,127 |
| HUN_2026 | 200 | 199 | 99 | 840,134 / 840,134 | 333,588 / 333,588 | 53,938 | $106,023,965.51 | 60.3% | 49.7% | 99.4% | 97.9% | $2,588.66 | $418,807.59 | $416,218.80 | 1,226 |
| COL_2026 | 380 | 380 | 216 | 693,573 / 693,573 | 279,952 / 279,952 | 35,728 | $39,039,419.66 | 59.6% | 43.3% | 83.7% | 69.4% | $90,803.28 | $396,043.09 | $304,995.53 | 386 |
| PER_2026 | 393 | 393 | 217 | 1,212,643 / 1,212,643 | 461,076 / 461,076 | 54,571 | $87,774,251.02 | 62.0% | 53.8% | 89.3% | 78.4% | $198,527.41 | $614,359.83 | $415,832.22 | 784 |

**Gates passed:** HUN 840,134 rows and 333,588 exchange-taker rows; COL 693,573 and 279,952; PER 1,212,643 and 461,076. Totals are 2,746,350 fills and 1,074,616 exchange-taker rows. The complete inventory is 973 markets (HUN 200, COL 380, PER 393), with 972 resolved; fill-active conditions are 532 (99, 216, and 217). One unresolved HUN inventory market has no fills.

## Method, fee treatment, and MM-like classifier

### Fill and role semantics

Each row is one participant order in an `OrderFilled` match. `participant_address` owns that order. `order_is_match_taker_order=true` means the participant submitted the taker order in the exchange call; false means that participant supplied the exchange call’s matched maker order. On true rows, `counterparty_address` is normally the exchange contract and `exchange_if_taker_is_exchange` identifies V1 CTF or V1 NegRisk; on the paired maker-order row, the counterparty is the other participant wallet. The paired participant rows are separate users’ orders, not duplicate copies of one wallet fill. `exchange_taker_rows` is a subset of the total rows and is not added again.

These are **protocol order roles**, not evidence of who was a real-world liquidity provider. In every table, protocol maker-order role share is labeled descriptively and is excluded from the primary MM-like classifier.

NegRisk is visible here through the exchange contract on taker rows and through inventory `negrisk_market_id` / event groupings. V1 NegRisk taker rows: HUN 321,336; COL 277,141; PER 458,964. ComboV3 taker rows are zero, OTHER-outcome fill rows are zero, and no transaction in the scoped corpus spans multiple condition IDs. The fill corpus therefore shows NegRisk order flow and sibling-market behavior, but does not contain the standalone split/merge/conversion ledger needed to reconstruct atomic conversion bundles.

### PnL and fees

Wallet PnL is the sum of sell proceeds less buy costs, plus canonical per-token payout on the fill-ledger inventory at recorded resolution time, less net fees, plus allocated maker-rebate payouts. The payout source is the canonical per-token resolution result, represented as a fraction; the method does not assume only binary winners. All resolved fill token pairs have exact payout coverage: HUN 198/198, COL 432/432, PER 434/434. There are zero unknown-payout wallet rows. No active unresolved fill positions remain in the scoped data, so unrealized PnL totals are $0.

Net fees are charged legs less refunds from the supplied fee evidence. A fill is classified zero-fee when absolute net fee is at most $0.00000001, or when its evidence is `custody_not_scanned_pre_fee_era`; those pre-scan-era fills are treated as era-zero per the data notes. Such rows are shown separately in wallet metrics as `zero_fee_unknown_rows` (31,959 pooled: HUN 16,791, COL 15,168, PER 0). Fee ambiguity flags are retained. Current `fees_enabled` / `fee_taker_only` market snapshots are context only and do not override the historical per-fill fee evidence.

Maker-rebate payout totals to scoped fill wallets are $2,230,607.84; $1,429,210.51 is assigned to these families, with $801,397.33 left unallocated where a wallet has no target-family maker-order notional. Allocated family totals: HUN $418,807.59, COL $396,043.09, PER $614,359.83. Family allocation is proportional to each wallet’s protocol maker-order notional across the three families, as the available allocation proxy; this does not assert actual liquidity-provider status.

For a resolved token, inventory is the signed sum of participant buys and sells through the recorded resolution timestamp; payout is shares times the canonical fraction. For unresolved markets, fill cashflow plus inventory marked at the last observed fill price is reported as unrealized. Resolution-time cutoffs are retained even where later fills appear; the resulting reconciliation issue is documented below.

### MM-like definition

Minimum activity is **at least 200 fills or $10,000 notional**. The behavioral screen is at least one token with both buys and sells, with such tokens representing at least 20% of the wallet’s traded tokens. The MM-like thresholds are then zero-fee fill shares of at least 60%, 80%, or 90%. This adds an observable same-token recycling signal to low-fee behavior. Two-sided YES/NO market coverage, NegRisk breadth, FIFO round trips, and holding time are reported as supporting anatomy; they are not required for the base count. The result is an operational proxy and is not an assertion that a wallet posted quotes.

The role-based counterfactual below uses the old role criterion (protocol maker-order role share plus zero-fee share at the same threshold), with the same activity gate. The sets differ materially at 80%.

| Scope | MM-like ≥60% | MM-like ≥80% | MM-like ≥90% | Role + zero-fee ≥80% counterfactual | Overlap | Behavior-only | Role-only |
| --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | 2249 | 2127 | 1982 | 1250 | 1118 | 1009 | 132 |
| HUN_2026 | 1229 | 1226 | 1217 | 656 | 563 | 663 | 93 |
| COL_2026 | 436 | 386 | 342 | 362 | 313 | 73 | 49 |
| PER_2026 | 847 | 784 | 701 | 622 | 574 | 210 | 48 |

At 80%, pooled behavior-based classification gives 2,127 wallets versus 1,250 under the protocol-role counterfactual; 1,118 overlap, 1,009 are behavior-only, and 132 are role-only. HUN is 1,226 vs 656 (563 overlap); COL 386 vs 362 (313 overlap); PER 784 vs 622 (574 overlap). This difference is why protocol role is not used to identify MM-like behavior.

## MM-like leaderboard: pooled top 25

Ranked by fill-ledger total PnL among wallets meeting the 80% MM-like definition. “Role %” is the protocol maker-order role share, descriptive only. FIFO is the buy-to-sell matching proxy; terminal is the residual resolution/directional component.

| # | Wallet | Fills | Notional | Protocol maker-order role fills / notional % | Zero-fee fills / notional % | Same-token buy+sell tokens / share | Net fees | Rebates | Realized / unrealized PnL | Total PnL | FIFO spread / terminal direction | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | 1,406 | $536,023.94 | 95.1% / 62.6% | 99.9% / 81.4% | 1/2 (50%) | $8.43 | $1.24 | $183,894.15 / $0.00 | $183,894.15 | $130,860.02 / $53,041.36 | — |
| 2 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | 871 | $679,000.00 | 87.1% / 39.8% | 98.9% / 93.9% | 12/19 (63%) | $347.33 | $432.55 | $147,894.38 / $0.00 | $147,894.38 | $42,146.11 / $105,663.07 | — |
| 3 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | 3,208 | $568,112.67 | 96.9% / 34.9% | 99.9% / 92.3% | 2/4 (50%) | $7.01 | $2.24 | $147,762.87 / $0.00 | $147,762.87 | $152,868.31 / −$5,100.53 | — |
| 4 | 0x448861155279dbf833d041b963e3ac854599e319 | 3,531 | $2,318,946.62 | 92.0% / 69.4% | 94.7% / 77.2% | 60/68 (88%) | $3,498.80 | $5,203.88 | $135,836.38 / $0.00 | $135,836.38 | $131,407.14 / $2,734.21 | fee attribution ambiguous |
| 5 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | 141 | $680,057.40 | 94.3% / 63.7% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $0.00 | $118,152.99 / $0.00 | $118,152.99 | $118,152.98 / $0.02 | — |
| 6 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | 284 | $619,211.72 | 96.5% / 17.0% | 100.0% / 100.0% | 3/4 (75%) | $0.00 | $560.99 | $111,844.81 / $0.00 | $111,844.81 | $104,018.60 / $7,265.32 | — |
| 7 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | 6,732 | $2,538,027.83 | 94.6% / 61.7% | 98.6% / 87.1% | 19/45 (42%) | $4,342.78 | $1,092.44 | $104,267.40 / $0.00 | $104,267.40 | $101,656.02 / $9,724.19 | fee attribution ambiguous; negative fill inventory; max shortfall 103,332.7 sh; excess-sell proceeds $84,093.16 have unknown basis |
| 8 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | 579 | $551,682.67 | 95.3% / 10.5% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $732.65 | $97,754.45 / $0.00 | $97,754.45 | $97,021.86 / $0.00 | — |
| 9 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | 618 | $187,894.58 | 93.0% / 73.2% | 99.0% / 99.1% | 4/8 (50%) | $30.35 | $0.00 | $97,076.63 / $0.00 | $97,076.63 | −$1,650.42 / $98,757.40 | — |
| 10 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | 217 | $471,683.08 | 97.2% / 76.2% | 100.0% / 100.0% | 1/2 (50%) | $0.00 | $0.00 | $91,601.47 / $0.00 | $91,601.47 | $87,409.92 / $4,191.50 | — |
| 11 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 2,213 | $755,654.53 | 96.2% / 57.5% | 96.4% / 57.8% | 13/26 (50%) | $2,885.45 | $11,228.77 | $90,472.64 / $0.00 | $90,472.64 | $33,721.18 / $47,752.02 | negative fill inventory; max shortfall 10,273.3 sh; excess-sell proceeds $9,967.84 have unknown basis |
| 12 | 0xcf6087abd66e16b59a122d45bfcdc05d4b7247ce | 1,006 | $1,656,479.01 | 95.1% / 49.5% | 99.0% / 88.3% | 9/16 (56%) | $667.85 | $269.11 | $89,377.40 / $0.00 | $89,377.40 | $19,262.53 / $70,513.60 | — |
| 13 | 0xa527e3a082c41b56f81d6bd98cb9fde1e9904228 | 125 | $149,209.70 | 56.0% / 8.4% | 92.8% / 93.2% | 4/8 (50%) | $273.13 | $0.00 | $87,020.89 / $0.00 | $87,020.89 | $223.27 / $87,070.72 | fee attribution ambiguous |
| 14 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,796 | $670,165.19 | 87.9% / 57.0% | 97.8% / 79.7% | 36/47 (77%) | $1,840.97 | $713.70 | $81,734.25 / $0.00 | $81,734.25 | $57,149.02 / $25,712.51 | — |
| 15 | 0x94c4fe3b18435aa92024c7c1589537fcda3f43ca | 718 | $412,627.22 | 94.4% / 67.7% | 100.0% / 100.0% | 9/21 (43%) | $0.00 | $2,014.11 | $70,141.18 / $0.00 | $70,141.18 | $39,786.39 / $28,340.68 | — |
| 16 | 0x016a003d0a79f87fb2da292af3d520afc8ec848d | 1,218 | $242,550.69 | 95.0% / 74.3% | 100.0% / 100.0% | 1/2 (50%) | $0.00 | $0.00 | $67,513.16 / $0.00 | $67,513.16 | $52,082.87 / $15,430.29 | — |
| 17 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | 630 | $237,948.31 | 65.4% / 28.6% | 85.1% / 64.7% | 17/21 (81%) | $1,726.65 | $402.97 | $62,647.31 / $0.00 | $62,647.31 | $15,625.55 / $48,345.44 | — |
| 18 | 0x5b90d4fab4e082795eeb231b121691a0cb12c44e | 106 | $276,144.13 | 95.3% / 61.5% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $0.00 | $62,157.91 / $0.00 | $62,157.91 | $62,157.90 / $0.01 | — |
| 19 | 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 1,433 | $1,178,465.71 | 93.4% / 72.2% | 95.9% / 85.0% | 8/9 (89%) | $1,065.54 | $999.82 | $56,431.09 / $0.00 | $56,431.09 | $45,433.37 / $11,063.45 | — |
| 20 | 0x944e0472b99926f4f712fc97dc0a846a8c5d8c94 | 2,464 | $420,351.96 | 94.4% / 77.0% | 98.3% / 90.4% | 32/58 (55%) | $451.94 | $1,178.76 | $52,445.43 / $0.00 | $52,445.43 | $20,855.16 / $30,863.44 | — |
| 21 | 0x92659eace43282fb4069b6d2fb558095501d2cc0 | 15 | $237,033.40 | 0.0% / 0.0% | 100.0% / 100.0% | 2/2 (100%) | $0.00 | $0.00 | $50,722.81 / $0.00 | $50,722.81 | $50,722.82 / $0.00 | — |
| 22 | 0xdfe3fedc5c7679be42c3d393e99d4b55247b73c4 | 301 | $202,325.11 | 97.0% / 88.8% | 99.7% / 97.4% | 2/6 (33%) | $128.58 | $13.40 | $47,599.84 / $0.00 | $47,599.84 | $37,134.25 / $10,580.78 | — |
| 23 | 0x776571cdb170a6999fb1d2b032f8f5011a08ccb8 | 381 | $78,133.14 | 91.3% / 10.5% | 99.7% / 19.8% | 1/1 (100%) | $350.90 | $3.94 | $46,840.77 / $0.00 | $46,840.77 | $47,187.74 / $0.00 | — |
| 24 | 0x3dca854f147d9b9ecc56d37800f36b347ce23c13 | 1,316 | $528,207.69 | 67.8% / 32.8% | 84.3% / 56.3% | 37/64 (58%) | $2,505.84 | $332.63 | $45,878.62 / $0.00 | $45,878.62 | $21,489.15 / $26,562.69 | — |
| 25 | 0xfa5f246bb1dbfe039dc7f4d3eede0d9808a86bfe | 18 | $19,239.80 | 94.4% / 100.0% | 94.4% / 100.0% | 1/1 (100%) | $0.02 | $43,125.37 | $42,761.20 / $0.00 | $42,761.20 | −$364.15 / $0.00 | — |

## MM-like leaderboard: HUN_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills / notional % | Zero-fee fills / notional % | Same-token buy+sell tokens / share | Net fees | Rebates | Realized / unrealized PnL | Total PnL | FIFO spread / terminal direction | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | 1,406 | $536,023.94 | 95.1% / 62.6% | 99.9% / 81.4% | 1/2 (50%) | $8.43 | $1.24 | $183,894.15 / $0.00 | $183,894.15 | $130,860.02 / $53,041.36 | — |
| 2 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | 3,208 | $568,112.67 | 96.9% / 34.9% | 99.9% / 92.3% | 2/4 (50%) | $7.01 | $2.24 | $147,762.87 / $0.00 | $147,762.87 | $152,868.31 / −$5,100.53 | — |
| 3 | 0x448861155279dbf833d041b963e3ac854599e319 | 1,610 | $947,087.59 | 92.5% / 78.9% | 98.5% / 98.0% | 25/32 (78%) | $72.24 | $2,416.36 | $132,320.49 / $0.00 | $132,320.49 | $87,210.93 / $42,765.44 | fee attribution ambiguous |
| 4 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | 141 | $680,057.40 | 94.3% / 63.7% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $0.00 | $118,152.99 / $0.00 | $118,152.99 | $118,152.98 / $0.02 | — |
| 5 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | 284 | $619,211.72 | 96.5% / 17.0% | 100.0% / 100.0% | 3/4 (75%) | $0.00 | $560.99 | $111,844.81 / $0.00 | $111,844.81 | $104,018.60 / $7,265.32 | — |
| 6 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | 579 | $551,682.67 | 95.3% / 10.5% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $732.65 | $97,754.45 / $0.00 | $97,754.45 | $97,021.86 / $0.00 | — |
| 7 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | 618 | $187,894.58 | 93.0% / 73.2% | 99.0% / 99.1% | 4/8 (50%) | $30.35 | $0.00 | $97,076.63 / $0.00 | $97,076.63 | −$1,650.42 / $98,757.40 | — |
| 8 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | 217 | $471,683.08 | 97.2% / 76.2% | 100.0% / 100.0% | 1/2 (50%) | $0.00 | $0.00 | $91,601.47 / $0.00 | $91,601.47 | $87,409.92 / $4,191.50 | — |
| 9 | 0xa527e3a082c41b56f81d6bd98cb9fde1e9904228 | 125 | $149,209.70 | 56.0% / 8.4% | 92.8% / 93.2% | 4/8 (50%) | $273.13 | $0.00 | $87,020.89 / $0.00 | $87,020.89 | $223.27 / $87,070.72 | fee attribution ambiguous |
| 10 | 0x94c4fe3b18435aa92024c7c1589537fcda3f43ca | 718 | $412,627.22 | 94.4% / 67.7% | 100.0% / 100.0% | 9/21 (43%) | $0.00 | $2,014.11 | $70,141.18 / $0.00 | $70,141.18 | $39,786.39 / $28,340.68 | — |
| 11 | 0x016a003d0a79f87fb2da292af3d520afc8ec848d | 1,218 | $242,550.69 | 95.0% / 74.3% | 100.0% / 100.0% | 1/2 (50%) | $0.00 | $0.00 | $67,513.16 / $0.00 | $67,513.16 | $52,082.87 / $15,430.29 | — |
| 12 | 0x5b90d4fab4e082795eeb231b121691a0cb12c44e | 106 | $276,144.13 | 95.3% / 61.5% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $0.00 | $62,157.91 / $0.00 | $62,157.91 | $62,157.90 / $0.01 | — |
| 13 | 0xcf6087abd66e16b59a122d45bfcdc05d4b7247ce | 322 | $1,305,442.81 | 92.2% / 45.4% | 98.8% / 88.1% | 2/6 (33%) | $95.20 | $194.66 | $52,784.05 / $0.00 | $52,784.05 | $18,127.67 / $34,556.95 | — |
| 14 | 0x92659eace43282fb4069b6d2fb558095501d2cc0 | 15 | $237,033.40 | 0.0% / 0.0% | 100.0% / 100.0% | 2/2 (100%) | $0.00 | $0.00 | $50,722.81 / $0.00 | $50,722.81 | $50,722.82 / $0.00 | — |
| 15 | 0xdfe3fedc5c7679be42c3d393e99d4b55247b73c4 | 234 | $192,644.69 | 97.0% / 92.0% | 100.0% / 100.0% | 1/5 (20%) | $0.00 | $13.22 | $47,038.91 / $0.00 | $47,038.91 | $36,444.92 / $10,580.78 | — |
| 16 | 0x5b6331e7ff0831a3fe2ed12004747db1a9c911a4 | 325 | $254,944.50 | 1.2% / 0.8% | 98.8% / 97.4% | 10/29 (34%) | $1.44 | $196.82 | $36,339.92 / $0.00 | $36,339.92 | $39,411.70 / −$3,267.16 | — |
| 17 | 0xb2758de1469288a74f817cc0d15b75a3abcf7152 | 167 | $133,865.54 | 95.8% / 71.0% | 100.0% / 100.0% | 2/2 (100%) | $0.00 | $0.00 | $35,209.02 / $0.00 | $35,209.02 | $35,209.02 / $0.00 | — |
| 18 | 0xded313b467fce8ff1d6b009d26f47f3af7f0fe68 | 228 | $143,406.26 | 98.7% / 98.5% | 100.0% / 100.0% | 1/1 (100%) | $0.00 | $0.00 | $34,168.21 / $0.00 | $34,168.21 | $34,168.15 / $0.06 | — |
| 19 | 0xbacd00c9080a82ded56f504ee8810af732b0ab35 | 863 | $270,039.77 | 96.1% / 45.0% | 100.0% / 100.0% | 1/4 (25%) | $0.00 | $761.72 | $30,097.19 / $0.00 | $30,097.19 | $12,619.89 / $16,715.58 | — |
| 20 | 0xea7957606f259bcba522a4681494555547a7a9cc | 162 | $162,458.40 | 90.7% / 29.8% | 100.0% / 100.0% | 3/5 (60%) | $0.00 | $64.98 | $28,994.00 / $0.00 | $28,994.00 | $20,992.90 / $7,936.11 | — |
| 21 | 0x5d4449e4b5bf3068127c99c0cb78c85d0310e507 | 135 | $118,657.10 | 88.9% / 82.9% | 100.0% / 100.0% | 1/2 (50%) | $0.00 | $0.00 | $27,209.85 / $0.00 | $27,209.85 | $25,630.79 / $1,579.06 | — |
| 22 | 0xa39c488ea8269609aea27f5f8486044d839908bc | 231 | $126,013.78 | 94.8% / 46.1% | 98.7% / 81.0% | 1/2 (50%) | $2.75 | $1,293.94 | $25,869.55 / $0.00 | $25,869.55 | $12,378.37 / $12,199.99 | — |
| 23 | 0x6edd22c2fbdf1d8bb7d446ab33107443f67d728c | 9 | $42,999.82 | 0.0% / 0.0% | 100.0% / 100.0% | 1/5 (20%) | $0.00 | $0.00 | $24,053.78 / $0.00 | $24,053.78 | $173.74 / $23,880.05 | — |
| 24 | 0xfa8024ffad06e222eff6c4084d489189cf583aa0 | 1,498 | $239,413.61 | 95.6% / 80.7% | 99.9% / 97.7% | 16/22 (73%) | $18.60 | $0.00 | $23,948.22 / $0.00 | $23,948.22 | $22,623.64 / $1,343.17 | — |
| 25 | 0x44a1159b925c145e70f746bb3f0f58380553bd21 | 286 | $83,929.48 | 96.5% / 95.5% | 100.0% / 100.0% | 5/6 (83%) | $0.00 | $18,026.75 | $22,483.06 / $0.00 | $22,483.06 | $2,754.67 / $1,701.64 | — |

## MM-like leaderboard: COL_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills / notional % | Zero-fee fills / notional % | Same-token buy+sell tokens / share | Net fees | Rebates | Realized / unrealized PnL | Total PnL | FIFO spread / terminal direction | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | 253 | $93,568.18 | 69.2% / 43.1% | 81.4% / 58.3% | 6/8 (75%) | $721.84 | $239.11 | $57,974.25 / $0.00 | $57,974.25 | $11,095.93 / $47,361.05 | — |
| 2 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 1,192 | $224,496.40 | 97.8% / 72.3% | 97.9% / 72.3% | 7/13 (54%) | $328.89 | $4,197.29 | $57,117.26 / $0.00 | $57,117.26 | $38,885.99 / $14,362.88 | — |
| 3 | 0x639df1ee9bc0f016673bd78609c0bd01e68e4777 | 227 | $32,712.05 | 89.9% / 56.8% | 95.2% / 68.2% | 5/7 (71%) | $119.58 | $100.12 | $29,441.10 / $0.00 | $29,441.10 | $6,664.55 / $22,796.02 | — |
| 4 | 0x6f40bd79f70ca03c59d7318dfebe22e65b55eb64 | 174 | $37,791.17 | 88.5% / 55.4% | 91.4% / 57.7% | 2/3 (67%) | $294.15 | $107.94 | $22,077.35 / $0.00 | $22,077.35 | $1,590.09 / $20,673.48 | — |
| 5 | 0xecaa8806a9a05049d7d5260a33dc924220e377a9 | 813 | $309,778.52 | 95.9% / 59.9% | 95.7% / 59.9% | 5/9 (56%) | $768.25 | $1,367.69 | $21,255.64 / $0.00 | $21,255.64 | −$4,357.44 / $25,013.64 | — |
| 6 | 0xb1ca909e848cc24ec4e220ce1c453bc290c51705 | 1,140 | $865,426.19 | 99.3% / 95.6% | 95.4% / 89.6% | 7/11 (64%) | $51.77 | $1,325.21 | $19,698.42 / $0.00 | $19,698.42 | $14,857.47 / $3,557.52 | negative fill inventory; max shortfall 9,999.9 sh; excess-sell proceeds $10.00 have unknown basis |
| 7 | 0x1521b47bf0c41f6b7fd3ad41cdec566812c8f23e | 312 | $240,778.35 | 99.0% / 71.7% | 99.0% / 71.7% | 2/2 (100%) | $30.85 | $817.80 | $18,167.05 / $0.00 | $18,167.05 | $17,380.08 / $0.00 | — |
| 8 | 0xfb5148fc7223630e0967dbfa8cd920d83ab4742d | 310 | $146,836.31 | 94.8% / 62.3% | 94.8% / 62.3% | 3/6 (50%) | $229.14 | $836.07 | $15,044.49 / $0.00 | $15,044.49 | $252.69 / $14,184.87 | — |
| 9 | 0x296bd652f74deac6a8bd9bcb04265f3a65fd2cf2 | 602 | $44,807.32 | 82.6% / 49.1% | 90.4% / 53.7% | 15/16 (94%) | $239.93 | $68.30 | $14,588.65 / $0.00 | $14,588.65 | $13,705.48 / $1,054.80 | — |
| 10 | 0x72f7b4aa5acaff9629917cf4ec34620a41c492f7 | 604 | $29,971.23 | 78.5% / 44.9% | 97.2% / 74.8% | 15/27 (56%) | $37.97 | $24.11 | $13,984.49 / $0.00 | $13,984.49 | $4,349.28 / $9,649.07 | — |
| 11 | 0x59d646eede51dbba370b5046a3593133c5fc62c8 | 1,290 | $91,792.41 | 90.3% / 44.3% | 97.5% / 57.9% | 14/17 (82%) | $343.02 | $37.62 | $11,584.69 / $0.00 | $11,584.69 | $12,510.45 / −$620.37 | — |
| 12 | 0x9c2617462567859fcf5764e03b9a687ebca274bc | 210 | $274,023.21 | 98.6% / 98.4% | 94.3% / 92.3% | 2/2 (100%) | $12.67 | $307.01 | $11,501.80 / $0.00 | $11,501.80 | $3,381.99 / $7,825.47 | — |
| 13 | 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 82 | $102,425.55 | 92.7% / 78.7% | 91.5% / 70.5% | 1/2 (50%) | $119.19 | $94.68 | $10,563.95 / $0.00 | $10,563.95 | −$474.98 / $11,063.44 | — |
| 14 | 0x0a6d26d31b28fd5a84c301f8b27296612f3b1d0a | 729 | $52,630.98 | 85.5% / 97.4% | 96.2% / 99.3% | 12/16 (75%) | $4.93 | $807.36 | $10,498.13 / $0.00 | $10,498.13 | −$100.97 / $9,796.66 | — |
| 15 | 0x143d8058b292de4f11c4f309b156f31b50285fd5 | 58 | $15,363.49 | 81.0% / 1.7% | 96.6% / 20.0% | 2/3 (67%) | $5.42 | $0.00 | $9,217.40 / $0.00 | $9,217.40 | $9,922.75 / −$699.93 | — |
| 16 | 0xa43da0aab839cf651a70e0e310462bb53a3b00f4 | 97 | $33,048.53 | 85.6% / 62.1% | 85.6% / 62.1% | 4/7 (57%) | $113.88 | $176.43 | $9,142.19 / $0.00 | $9,142.19 | $4,713.96 / $4,365.68 | — |
| 17 | 0x944e0472b99926f4f712fc97dc0a846a8c5d8c94 | 259 | $43,810.60 | 96.1% / 79.2% | 96.9% / 80.2% | 5/11 (45%) | $38.64 | $126.36 | $8,832.95 / $0.00 | $8,832.95 | $5,831.03 / $2,914.21 | — |
| 18 | 0x0a7ed19909e9b4c6cd0fc1d59c1d481415a1fce4 | 222 | $63,806.30 | 85.6% / 78.4% | 85.6% / 78.4% | 15/15 (100%) | $145.31 | $805.40 | $8,581.08 / $0.00 | $8,581.08 | $7,920.99 / $0.01 | — |
| 19 | 0xc8ab97a9089a9ff7e6ef0688e6e591a066946418 | 3,213 | $386,416.11 | 99.3% / 98.3% | 99.6% / 98.5% | 45/49 (92%) | $13.96 | $7,474.69 | $7,962.35 / $0.00 | $7,962.35 | $698.30 / −$196.68 | — |
| 20 | 0xac4a1fabdac2438d6afa2a9e8e83845310a0bf1e | 1,988 | $97,247.67 | 99.8% / 99.9% | 99.8% / 99.9% | 28/42 (67%) | $2.13 | $4,374.62 | $7,895.33 / $0.00 | $7,895.33 | $2,442.73 / $862.26 | negative fill inventory; max shortfall 1,888.8 sh; excess-sell proceeds $436.07 have unknown basis |
| 21 | 0xfb60be21a6035e0523bedc524d4334e23a9f71f5 | 177 | $29,608.47 | 96.0% / 98.1% | 96.0% / 98.1% | 2/3 (67%) | $6.25 | $34.28 | $7,364.51 / $0.00 | $7,364.51 | $6,167.00 / $1,169.47 | — |
| 22 | 0xc7d02944a76b9f83b199e9090ecc92c82d241f8a | 232 | $16,510.68 | 91.4% / 98.2% | 90.1% / 98.1% | 15/45 (33%) | $4.53 | $2,868.71 | $7,040.81 / $0.00 | $7,040.81 | $397.05 / $3,779.58 | — |
| 23 | 0x945a49252f772a10c6ddd1d1e1e24ee20438a48c | 3,047 | $10,784.08 | 98.3% / 99.6% | 99.2% / 99.8% | 33/84 (39%) | $0.37 | $6,348.61 | $6,388.06 / $0.00 | $6,388.06 | −$39.03 / $78.86 | — |
| 24 | 0xa5e3044fd953605f9407d58c76e48fd75a394d7e | 4,988 | $152,894.65 | 86.9% / 93.6% | 99.2% / 98.5% | 27/30 (90%) | $43.76 | $4,365.13 | $6,349.70 / $0.00 | $6,349.70 | $751.04 / $1,383.36 | negative fill inventory; max shortfall 3,596.7 sh; excess-sell proceeds $3,412.38 have unknown basis |
| 25 | 0x21ffd2b7a212a6f277ed3eca1a9f8efcbca90d71 | 6,920 | $383,668.36 | 98.2% / 99.0% | 99.9% / 99.9% | 112/123 (91%) | $8.47 | $7,403.80 | $6,211.41 / $0.00 | $6,211.41 | $3,911.19 / −$5,095.11 | — |

## MM-like leaderboard: PER_2026 top 25

| # | Wallet | Fills | Notional | Protocol maker-order role fills / notional % | Zero-fee fills / notional % | Same-token buy+sell tokens / share | Net fees | Rebates | Realized / unrealized PnL | Total PnL | FIFO spread / terminal direction | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | 737 | $435,495.76 | 85.9% / 32.4% | 98.6% / 90.5% | 10/12 (83%) | $347.33 | $225.97 | $149,109.85 / $0.00 | $149,109.85 | $46,133.52 / $103,097.71 | — |
| 2 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | 6,210 | $2,047,052.08 | 94.6% / 56.3% | 98.6% / 84.3% | 15/36 (42%) | $4,306.32 | $803.00 | $99,632.43 / $0.00 | $99,632.43 | $72,610.25 / $34,387.96 | negative fill inventory; max shortfall 103,332.7 sh; excess-sell proceeds $84,093.16 have unknown basis |
| 3 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | 1,654 | $594,916.10 | 87.5% / 54.5% | 97.8% / 78.7% | 33/41 (80%) | $1,839.47 | $605.75 | $76,018.70 / $0.00 | $76,018.70 | $53,595.71 / $23,656.73 | — |
| 4 | 0x784feec38475dc47e63c3a16cbd24b905dab5df9 | 8,543 | $1,208,483.32 | 93.6% / 52.4% | 99.3% / 91.8% | 24/55 (44%) | $2,067.26 | $692.55 | $65,093.11 / $0.00 | $65,093.11 | −$8,469.65 / $7,638.21 | negative fill inventory; max shortfall 149,639.3 sh; excess-sell proceeds $139,193.24 have unknown basis |
| 5 | 0x682db86f41a12d9292befded523564314c1f31ab | 3,027 | $966,800.80 | 82.1% / 39.1% | 98.1% / 91.3% | 70/74 (95%) | $1,172.24 | $231.19 | $53,099.58 / $0.00 | $53,099.58 | $54,337.76 / $564.91 | negative fill inventory; max shortfall 2,000.0 sh; excess-sell proceeds $5,326.29 have unknown basis |
| 6 | 0xf2f6af4f27ec2dcf4072095ab804016e14cd5817 | 1,439 | $442,186.15 | 96.5% / 71.9% | 99.5% / 86.7% | 5/16 (31%) | $18.50 | $963.06 | $52,264.28 / $0.00 | $52,264.28 | $41,591.92 / $9,782.28 | negative fill inventory; max shortfall 315.0 sh; excess-sell proceeds $260.50 have unknown basis |
| 7 | 0x776571cdb170a6999fb1d2b032f8f5011a08ccb8 | 381 | $78,133.14 | 91.3% / 10.5% | 99.7% / 19.8% | 1/1 (100%) | $350.90 | $3.94 | $46,840.77 / $0.00 | $46,840.77 | $47,187.74 / $0.00 | — |
| 8 | 0xfa5f246bb1dbfe039dc7f4d3eede0d9808a86bfe | 18 | $19,239.80 | 94.4% / 100.0% | 94.4% / 100.0% | 1/1 (100%) | $0.02 | $43,125.37 | $42,761.20 / $0.00 | $42,761.20 | −$364.15 / $0.00 | — |
| 9 | 0x74471a007ddcc488f6d57b5e86dfb35a8d48a16d | 782 | $251,275.08 | 91.2% / 48.5% | 98.8% / 78.3% | 13/17 (76%) | $358.93 | $526.95 | $39,213.21 / $0.00 | $39,213.21 | $29,512.50 / $9,532.68 | — |
| 10 | 0x944e0472b99926f4f712fc97dc0a846a8c5d8c94 | 1,526 | $317,424.55 | 92.8% / 75.3% | 97.8% / 90.1% | 16/24 (67%) | $413.14 | $870.96 | $37,241.79 / $0.00 | $37,241.79 | $12,534.46 / $24,249.50 | — |
| 11 | 0xcf6087abd66e16b59a122d45bfcdc05d4b7247ce | 684 | $351,036.20 | 96.5% / 64.6% | 99.1% / 89.1% | 7/10 (70%) | $572.65 | $74.46 | $36,593.34 / $0.00 | $36,593.34 | $1,134.87 / $35,956.66 | — |
| 12 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | 1,021 | $531,158.12 | 94.2% / 51.2% | 94.6% / 51.7% | 6/13 (46%) | $2,556.56 | $7,031.48 | $33,355.38 / $0.00 | $33,355.38 | −$5,164.80 / $33,389.14 | negative fill inventory; max shortfall 10,273.3 sh; excess-sell proceeds $9,967.84 have unknown basis |
| 13 | 0x182bd8bf2f11a572c4ec686aae3a0d919adcf3d3 | 221 | $211,165.38 | 93.7% / 50.8% | 93.7% / 50.8% | 2/2 (100%) | $783.45 | $389.90 | $31,091.11 / $0.00 | $31,091.11 | $31,484.64 / $0.01 | — |
| 14 | 0xb4d250f58c26840e09723a83ce9c8149aa32ce99 | 474 | $96,452.99 | 99.4% / 70.1% | 99.4% / 70.1% | 2/4 (50%) | $79.91 | $27,812.55 | $29,996.49 / $0.00 | $29,996.49 | $8,613.31 / −$6,349.46 | — |
| 15 | 0x7664aa7df89e2c02c01e3cd57e0abee078eb2e50 | 2,003 | $675,386.80 | 96.4% / 61.2% | 98.4% / 80.7% | 6/21 (29%) | $2,076.52 | $438.79 | $28,764.69 / $0.00 | $28,764.69 | $13,909.94 / $16,492.49 | — |
| 16 | 0x7bc14171ccb0d3e6bac219ec6a76211826e28db4 | 1,016 | $913,750.53 | 93.3% / 78.7% | 92.4% / 78.5% | 4/4 (100%) | $3,082.55 | $4,185.32 | $27,456.52 / $0.00 | $27,456.52 | $28,211.12 / −$1,857.37 | — |
| 17 | 0xc6dd722558dbfbd8fa780efcbe819ed8c6604b9f | 678 | $591,887.07 | 88.5% / 62.8% | 92.3% / 75.2% | 5/5 (100%) | $946.35 | $436.84 | $26,548.71 / $0.00 | $26,548.71 | $27,058.22 / $0.00 | — |
| 18 | 0x22e4248bdb066f65c9f11cd66cdd3719a28eef1c | 808 | $425,285.22 | 96.3% / 76.8% | 96.7% / 79.1% | 2/9 (22%) | $1,108.69 | $632.00 | $25,881.91 / $0.00 | $25,881.91 | $25,833.10 / $767.86 | negative fill inventory; max shortfall 5,000.0 sh; excess-sell proceeds $4,757.56 have unknown basis |
| 19 | 0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1 | 229 | $325,305.43 | 91.7% / 63.4% | 89.1% / 63.0% | 4/6 (67%) | $458.71 | $985.48 | $25,653.18 / $0.00 | $25,653.18 | $6,124.15 / $19,002.26 | — |
| 20 | 0x095dcfb123a4bc035ee6b0d624bab0cc964352cf | 1,914 | $360,694.31 | 95.7% / 89.7% | 95.0% / 89.0% | 3/4 (75%) | $31.45 | $156.20 | $24,450.24 / $0.00 | $24,450.24 | $24,324.89 / $0.60 | — |
| 21 | 0x614dc8d3542c12103d2c6a3553fd761e391d1546 | 1,020 | $369,671.74 | 93.9% / 73.7% | 94.0% / 82.3% | 8/9 (89%) | $714.18 | $1,073.21 | $24,418.22 / $0.00 | $24,418.22 | −$3,397.13 / $27,456.31 | — |
| 22 | 0xc8ab97a9089a9ff7e6ef0688e6e591a066946418 | 7,692 | $1,503,649.86 | 99.0% / 94.4% | 99.6% / 97.3% | 83/111 (75%) | $95.59 | $27,956.51 | $22,614.18 / $0.00 | $22,614.18 | −$3,477.15 / −$1,839.40 | negative fill inventory; max shortfall 1,000.0 sh; excess-sell proceeds $1,305.16 have unknown basis |
| 23 | 0xd1acd3925d895de9aec98ff95f3a30c5279d08d5 | 590 | $402,161.30 | 97.6% / 80.7% | 95.3% / 78.7% | 2/2 (100%) | $647.90 | $342.52 | $21,260.05 / $0.00 | $21,260.05 | $7,960.21 / $13,605.21 | — |
| 24 | 0xb4f2310271b208f35b543bde10522db44cf67851 | 206 | $88,166.92 | 97.6% / 79.8% | 100.0% / 100.0% | 2/5 (40%) | $0.00 | $124.98 | $20,732.07 / $0.00 | $20,732.07 | $6,819.03 / $13,788.06 | — |
| 25 | 0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292 | 95 | $134,329.79 | 98.9% / 96.2% | 98.9% / 96.2% | 1/3 (33%) | $22.42 | $1,566.40 | $20,007.74 / $0.00 | $20,007.74 | $2,999.95 / $15,463.81 | — |

## MM-like strategy anatomy: pooled top 10 and family top 3

The first table describes market coverage and behavior. “YES+NO markets” means the wallet traded both outcome tokens in that condition, regardless of trade direction. The <5c and >95c percentages are shares of executed fill notional, not resting quotes. Resolution timing is the percentage of fills before recorded resolution in 0–1, 1–7, 7–30, and >30 day buckets; unresolved is omitted here because all selected rows fall in resolved markets.

| Scope / rank | Wallet | Family overlap | Markets / events | Fills / turnover / median fill $ / shares | Two-sided tokens / share | YES+NO markets / share | NegRisk touched / multi / all | FIFO matched clips / shares | Median FIFO hold h | <5c / >95c fill-notional % | Fills to resolution % (0–1/1–7/7–30/>30d) | Fills in ±1d big-VWAP window |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED #1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | HUN_2026 | 2 / 1 | 1,406 / $536,023.94 / $27.62 / 50.0 sh | 1/2 (50.0%) | 0 (0.0%) | 0 / 0 / — | 1,122 / 299,999.5 sh | 4,103.5 | 0.1% / 55.9% | 9%/6%/0%/85% | 0.0% |
| POOLED #2 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | HUN_2026,PER_2026 | 17 / 9 | 871 / $679,000.00 / $31.26 / 69.4 sh | 12/19 (63.2%) | 2 (11.8%) | 6 / 4 / — | 433 / 358,987.2 sh | 53.1 | 0.0% / 21.4% | 0%/2%/31%/67% | 69.6% |
| POOLED #3 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | HUN_2026 | 3 / 1 | 3,208 / $568,112.67 / $1.10 / 43.1 sh | 2/4 (50.0%) | 1 (33.3%) | 0 / 0 / — | 1,040 / 371,313.3 sh | 1,609.3 | 0.9% / 63.0% | 0%/0%/3%/97% | 0.5% |
| POOLED #4 | 0x448861155279dbf833d041b963e3ac854599e319 | COL_2026,HUN_2026,PER_2026 | 54 / 22 | 3,531 / $2,318,946.62 / $31.66 / 60.0 sh | 60/68 (88.2%) | 14 (25.9%) | 17 / 15 / — | 2,329 / 1,032,852.0 sh | 107.4 | 0.1% / 46.8% | 9%/24%/51%/15% | 36.2% |
| POOLED #5 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | HUN_2026 | 1 / 1 | 141 / $680,057.40 / $63.75 / 85.0 sh | 1/1 (100.0%) | 0 (0.0%) | 0 / 0 / — | 140 / 405,156.7 sh | 76.4 | 0.0% / 58.7% | 0%/0%/33%/67% | 32.6% |
| POOLED #6 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | HUN_2026 | 4 / 2 | 284 / $619,211.72 / $37.25 / 55.4 sh | 3/4 (75.0%) | 0 (0.0%) | 1 / 1 / — | 190 / 360,697.4 sh | 103.8 | 0.0% / 56.6% | 0%/32%/1%/67% | 0.7% |
| POOLED #7 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | HUN_2026,PER_2026 | 35 / 13 | 6,732 / $2,538,027.83 / $17.99 / 53.0 sh | 19/45 (42.2%) | 10 (28.6%) | 8 / 5 / — | 3,974 / 1,461,148.9 sh | 124.1 | 0.2% / 19.7% | 0%/2%/29%/69% | 45.1% |
| POOLED #8 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | HUN_2026 | 1 / 1 | 579 / $551,682.67 / $49.98 / 79.8 sh | 1/1 (100.0%) | 0 (0.0%) | 0 / 0 / — | 578 / 329,802.5 sh | 190.3 | 0.0% / 58.8% | 0%/0%/29%/71% | 25.2% |
| POOLED #9 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | HUN_2026 | 8 / 6 | 618 / $187,894.58 / $10.97 / 18.0 sh | 4/8 (50.0%) | 0 (0.0%) | 4 / 2 / — | 11 / 3,726.0 sh | 10.3 | 0.0% / 0.0% | 0%/3%/1%/96% | 2.3% |
| POOLED #10 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | HUN_2026 | 2 / 2 | 217 / $471,683.08 / $57.00 / 66.7 sh | 1/2 (50.0%) | 0 (0.0%) | 1 / 0 / — | 187 / 285,030.4 sh | 204.0 | 0.0% / 38.1% | 0%/13%/35%/51% | 35.5% |
| HUN_2026 #1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | HUN_2026 | 2 / 1 | 1,406 / $536,023.94 / $27.62 / 50.0 sh | 1/2 (50.0%) | 0 (0.0%) | 0 / 0 / — | 1,122 / 299,999.5 sh | 4,103.5 | 0.1% / 55.9% | 9%/6%/0%/85% | 0.0% |
| HUN_2026 #2 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | HUN_2026 | 3 / 1 | 3,208 / $568,112.67 / $1.10 / 43.1 sh | 2/4 (50.0%) | 1 (33.3%) | 0 / 0 / — | 1,040 / 371,313.3 sh | 1,609.3 | 0.9% / 63.0% | 0%/0%/3%/97% | 0.5% |
| HUN_2026 #3 | 0x448861155279dbf833d041b963e3ac854599e319 | COL_2026,HUN_2026,PER_2026 | 25 / 13 | 1,610 / $947,087.59 / $18.87 / 51.1 sh | 25/32 (78.1%) | 7 (28.0%) | 9 / 7 / — | 1,295 / 468,943.8 sh | 105.4 | 0.1% / 48.5% | 17%/34%/36%/13% | 35.2% |
| COL_2026 #1 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | COL_2026,HUN_2026,PER_2026 | 7 / 4 | 253 / $93,568.18 / $84.70 / 195.1 sh | 6/8 (75.0%) | 1 (14.3%) | 3 / 2 / — | 128 / 49,339.9 sh | 774.0 | 0.0% / 12.6% | 0%/18%/35%/47% | 10.3% |
| COL_2026 #2 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | COL_2026,PER_2026 | 10 / 5 | 1,192 / $224,496.40 / $12.29 / 50.0 sh | 7/13 (53.8%) | 3 (30.0%) | 4 / 4 / — | 778 / 118,026.5 sh | 69.9 | 0.4% / 46.2% | 0%/20%/61%/19% | 6.7% |
| COL_2026 #3 | 0x639df1ee9bc0f016673bd78609c0bd01e68e4777 | COL_2026 | 5 / 3 | 227 / $32,712.05 / $9.88 / 34.9 sh | 5/7 (71.4%) | 2 (40.0%) | 2 / 1 / — | 92 / 25,914.1 sh | 1,093.5 | 0.8% / 0.0% | 0%/23%/0%/76% | 0.0% |
| PER_2026 #1 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | HUN_2026,PER_2026 | 10 / 4 | 737 / $435,495.76 / $21.70 / 60.1 sh | 10/12 (83.3%) | 2 (20.0%) | 4 / 4 / — | 365 / 252,943.8 sh | 53.1 | 0.1% / 0.0% | 0%/0%/21%/79% | 70.4% |
| PER_2026 #2 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | HUN_2026,PER_2026 | 27 / 7 | 6,210 / $2,047,052.08 / $14.95 / 50.0 sh | 15/36 (41.7%) | 9 (33.3%) | 6 / 5 / — | 3,534 / 1,165,589.4 sh | 214.7 | 0.3% / 10.7% | 0%/0%/26%/74% | 41.5% |
| PER_2026 #3 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | COL_2026,HUN_2026,PER_2026 | 36 / 12 | 1,654 / $594,916.10 / $20.00 / 62.3 sh | 33/41 (80.5%) | 5 (13.9%) | 12 / 8 / — | 1,328 / 521,314.6 sh | 30.0 | 0.5% / 32.0% | 0%/0%/40%/60% | 44.7% |

### PnL and inventory for these MM-like leaders

FIFO “spread” is only a matched buy-to-sell cashflow proxy and can include directional price movement. Terminal/directional PnL is the remaining fill-ledger inventory’s resolution value less its matched cost. `Attribution Δ` is actual wallet total PnL minus (FIFO + terminal + unresolved mark − fees + allocated rebate); nonzero values are retained, especially where fill-only inventory goes negative. Net exposure columns value wallet token positions at each token’s most recent fill price as the event-time fill stream advances.

| Scope / rank | Wallet | Total PnL | FIFO / terminal PnL | Fees / rebate | Attribution Δ | Max / median abs net portfolio value | Max / median abs token shares | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED #1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | $183,894.15 | $130,860.02 / $53,041.36 | $8.43 / $1.24 | −$0.04 | $320,550.36 / $144,083.32 | 420,948.4 / 216,235.0 | — |
| POOLED #2 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | $147,894.38 | $42,146.11 / $105,663.07 | $347.33 / $432.55 | −$0.02 | $329,479.39 / $217,330.56 | 218,865.5 / 23,599.7 | — |
| POOLED #3 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | $147,762.87 | $152,868.31 / −$5,100.53 | $7.01 / $2.24 | −$0.14 | $226,648.65 / $160,898.60 | 648,255.9 / 224,985.2 | — |
| POOLED #4 | 0x448861155279dbf833d041b963e3ac854599e319 | $135,836.38 | $131,407.14 / $2,734.21 | $3,498.80 / $5,203.88 | −$10.05 | $948,093.77 / $424,595.16 | 272,223.7 / 31,227.0 | fee attribution ambiguous |
| POOLED #5 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | $118,152.99 | $118,152.98 / $0.02 | $0.00 / $0.00 | −$0.02 | $363,353.21 / $144,738.29 | 405,156.8 / 212,627.7 | — |
| POOLED #6 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | $111,844.81 | $104,018.60 / $7,265.32 | $0.00 / $560.99 | −$0.10 | $276,596.87 / $128,855.28 | 299,653.8 / 83,227.0 | — |
| POOLED #7 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | $104,267.40 | $101,656.02 / $9,724.19 | $4,342.78 / $1,092.44 | −$3,862.47 | $665,818.08 / $448,849.17 | 262,763.5 / 67,324.5 | fee attribution ambiguous; negative fill inventory; max shortfall 103,332.7 sh; excess-sell proceeds $84,093.16 have unknown basis |
| POOLED #8 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | $97,754.45 | $97,021.86 / $0.00 | $0.00 / $732.65 | −$0.06 | $277,034.11 / $86,928.42 | 329,802.5 / 133,636.0 | — |
| POOLED #9 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | $97,076.63 | −$1,650.42 / $98,757.40 | $30.35 / $0.00 | −$0.00 | $270,439.78 / $23,360.04 | 268,669.5 / 34,719.9 | — |
| POOLED #10 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | $91,601.47 | $87,409.92 / $4,191.50 | $0.00 / $0.00 | $0.04 | $276,274.76 / $168,961.12 | 285,030.4 / 169,305.1 | — |
| HUN_2026 #1 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | $183,894.15 | $130,860.02 / $53,041.36 | $8.43 / $1.24 | −$0.04 | $320,550.36 / $144,083.32 | 420,948.4 / 216,235.0 | — |
| HUN_2026 #2 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | $147,762.87 | $152,868.31 / −$5,100.53 | $7.01 / $2.24 | −$0.14 | $226,648.65 / $160,898.60 | 648,255.9 / 224,985.2 | — |
| HUN_2026 #3 | 0x448861155279dbf833d041b963e3ac854599e319 | $132,320.49 | $87,210.93 / $42,765.44 | $72.24 / $2,416.36 | $0.00 | $388,126.58 / $233,598.06 | 202,916.9 / 15,049.8 | fee attribution ambiguous |
| COL_2026 #1 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | $57,974.25 | $11,095.93 / $47,361.05 | $721.84 / $239.11 | $0.01 | $86,672.40 / $9,448.44 | 55,072.5 / 15,583.6 | — |
| COL_2026 #2 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | $57,117.26 | $38,885.99 / $14,362.88 | $328.89 / $4,197.29 | −$0.00 | $101,893.13 / $17,236.74 | 47,379.8 / 7,436.5 | — |
| COL_2026 #3 | 0x639df1ee9bc0f016673bd78609c0bd01e68e4777 | $29,441.10 | $6,664.55 / $22,796.02 | $119.58 / $100.12 | $0.00 | $18,586.28 / $9,418.76 | 20,232.7 / 6,748.4 | — |
| PER_2026 #1 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | $149,109.85 | $46,133.52 / $103,097.71 | $347.33 / $225.97 | −$0.02 | $274,130.00 / $125,712.58 | 218,865.5 / 23,178.4 | — |
| PER_2026 #2 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | $99,632.43 | $72,610.25 / $34,387.96 | $4,306.32 / $803.00 | −$3,862.47 | $644,477.09 / $425,514.29 | 262,763.5 / 65,998.1 | negative fill inventory; max shortfall 103,332.7 sh; excess-sell proceeds $84,093.16 have unknown basis |
| PER_2026 #3 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | $76,018.70 | $53,595.71 / $23,656.73 | $1,839.47 / $605.75 | −$0.01 | $193,790.82 / $54,728.95 | 161,978.1 / 9,280.8 | — |

## Other profitable strategies

This section ranks wallets by total PnL without requiring the MM-like classifier: top 25 pooled and top 5 in each family. The same wallet may appear in more than one scope. `MM80` indicates whether it also meets the behavior-based 80% classifier. Pattern labels are deterministic summaries of measured round-trip share, two-sided breadth, fill price tails, and time-to-resolution distribution; they are not causal labels. In particular, big-VWAP-window activity does not establish news reaction or trade direction, and fills near resolution do not by themselves prove sniping.

### Pooled top 25

| # | Wallet | MM80? | Measured pattern | Fills / turnover / median fill $ / shares | Total PnL | FIFO / terminal PnL | Fees / rebate | Zero-fee fills % | Median FIFO hold h | <5c / >95c notional % | Fills to resolution % (0–1/1–7/7–30/>30d) | Attribution Δ | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | no | Directional hold / resolution-led | 9,096 / $2,389,908.98 / $15.79 / 41.2 sh | $379,761.32 | −$2,023.33 / $382,045.93 | $2,988.47 / $2,727.24 | 98.5% | 2.9 | 0.1% / 4.7% | 1%/11%/49%/39% | −$0.04 | — |
| 2 | 0x88b59d79b6e1659c95a0043028e5bb7a26e6205c | no | Tail-priced directional exposure | 2,170 / $1,091,299.19 / $92.26 / 101.9 sh | $199,385.97 | $43,209.21 / $156,042.74 | $1,020.32 / $337.28 | 93.8% | 204.7 | 0.1% / 39.5% | 1%/8%/71%/20% | $817.06 | negative_fill_inventory; external sell basis unknown |
| 3 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | yes | Two-sided inventory recycling / round-trip-led | 1,406 / $536,023.94 / $27.62 / 50.0 sh | $183,894.15 | $130,860.02 / $53,041.36 | $8.43 / $1.24 | 99.9% | 4,103.5 | 0.1% / 55.9% | 9%/6%/0%/85% | −$0.04 | — |
| 4 | 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | no | Tail-priced directional exposure | 42 / $550,123.74 / $9,465.23 / 13,200.2 sh | $152,852.45 | $7,097.06 / $145,740.98 | $7.05 / $21.46 | 95.2% | 645.0 | 0.0% / 37.2% | 0%/21%/45%/33% | $0.00 | — |
| 5 | 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | no | Directional hold / resolution-led | 384 / $265,131.51 / $14.41 / 22.3 sh | $150,465.23 | $0.00 / $150,961.21 | $1,165.15 / $669.16 | 98.2% | — | 0.0% / 0.0% | 0%/0%/30%/70% | $0.00 | — |
| 6 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | yes | Mixed recycling; activity near large VWAP moves | 871 / $679,000.00 / $31.26 / 69.4 sh | $147,894.38 | $42,146.11 / $105,663.07 | $347.33 / $432.55 | 98.9% | 53.1 | 0.0% / 21.4% | 0%/2%/31%/67% | −$0.02 | — |
| 7 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | yes | Two-sided inventory recycling / round-trip-led | 3,208 / $568,112.67 / $1.10 / 43.1 sh | $147,762.87 | $152,868.31 / −$5,100.53 | $7.01 / $2.24 | 99.9% | 1,609.3 | 0.9% / 63.0% | 0%/0%/3%/97% | −$0.14 | — |
| 8 | 0x4b28a660415bf1d58f358b32fc729dc6b396d19a | no | Directional hold / resolution-led | 3,589 / $221,083.95 / $1.40 / 47.8 sh | $136,144.27 | $0.00 / $136,144.26 | $0.00 / $0.00 | 100.0% | — | 2.7% / 0.0% | 0%/0%/0%/100% | $0.00 | — |
| 9 | 0x448861155279dbf833d041b963e3ac854599e319 | yes | Two-sided inventory recycling / round-trip-led | 3,531 / $2,318,946.62 / $31.66 / 60.0 sh | $135,836.38 | $131,407.14 / $2,734.21 | $3,498.80 / $5,203.88 | 94.7% | 107.4 | 0.1% / 46.8% | 9%/24%/51%/15% | −$10.05 | fee_attribution_ambiguous |
| 10 | 0xdfdcd929644e720a472e246d257dfa4bbcf8c7c7 | yes | Two-sided inventory recycling / round-trip-led | 141 / $680,057.40 / $63.75 / 85.0 sh | $118,152.99 | $118,152.98 / $0.02 | $0.00 / $0.00 | 100.0% | 76.4 | 0.0% / 58.7% | 0%/0%/33%/67% | −$0.02 | — |
| 11 | 0x94a428cfa4f84b264e01f70d93d02bc96cb36356 | yes | Two-sided inventory recycling / round-trip-led | 284 / $619,211.72 / $37.25 / 55.4 sh | $111,844.81 | $104,018.60 / $7,265.32 | $0.00 / $560.99 | 100.0% | 103.8 | 0.0% / 56.6% | 0%/32%/1%/67% | −$0.10 | — |
| 12 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | yes | Two-sided inventory recycling / round-trip-led | 6,732 / $2,538,027.83 / $17.99 / 53.0 sh | $104,267.40 | $101,656.02 / $9,724.19 | $4,342.78 / $1,092.44 | 98.6% | 124.1 | 0.2% / 19.7% | 0%/2%/29%/69% | −$3,862.47 | negative_fill_inventory, fee_attribution_ambiguous; external sell basis unknown |
| 13 | 0xab828a2bcb4a5a93a94cdeedf3cb70b6211babe5 | yes | Two-sided inventory recycling / round-trip-led | 579 / $551,682.67 / $49.98 / 79.8 sh | $97,754.45 | $97,021.86 / $0.00 | $0.00 / $732.65 | 100.0% | 190.3 | 0.0% / 58.8% | 0%/0%/29%/71% | −$0.06 | — |
| 14 | 0xfc0ee8f51fa3eb9ec0dc5880f88dff5e8c4dcf87 | yes | Mixed inventory recycling and direction | 618 / $187,894.58 / $10.97 / 18.0 sh | $97,076.63 | −$1,650.42 / $98,757.40 | $30.35 / $0.00 | 99.0% | 10.3 | 0.0% / 0.0% | 0%/3%/1%/96% | −$0.00 | — |
| 15 | 0xc58351a51d9a4db0ad7c39eb9d794ce3793dbd46 | yes | Two-sided inventory recycling / round-trip-led | 217 / $471,683.08 / $57.00 / 66.7 sh | $91,601.47 | $87,409.92 / $4,191.50 | $0.00 / $0.00 | 100.0% | 204.0 | 0.0% / 38.1% | 0%/13%/35%/51% | $0.04 | — |
| 16 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | yes | Mixed inventory recycling and direction | 2,213 / $755,654.53 / $18.90 / 55.0 sh | $90,472.64 | $33,721.18 / $47,752.02 | $2,885.45 / $11,228.77 | 96.4% | 24.0 | 0.1% / 15.1% | 0%/11%/69%/20% | $656.12 | negative_fill_inventory; external sell basis unknown |
| 17 | 0xcf6087abd66e16b59a122d45bfcdc05d4b7247ce | yes | Tail-priced directional exposure | 1,006 / $1,656,479.01 / $30.35 / 80.0 sh | $89,377.40 | $19,262.53 / $70,513.60 | $667.85 / $269.11 | 99.0% | 416.5 | 0.2% / 65.1% | 0%/1%/48%/51% | −$0.00 | — |
| 18 | 0xa527e3a082c41b56f81d6bd98cb9fde1e9904228 | yes | Close-to-resolution directional exposure | 125 / $149,209.70 / $149.84 / 448.5 sh | $87,020.89 | $223.27 / $87,070.72 | $273.13 / $0.00 | 92.8% | 886.8 | 0.0% / 0.0% | 0%/54%/24%/22% | $0.02 | fee_attribution_ambiguous |
| 19 | 0x22e4248bdb066f65c9f11cd66cdd3719a28eef1c | no | Directional hold / resolution-led | 1,078 / $712,763.05 / $35.02 / 46.6 sh | $82,889.37 | $25,833.10 / $57,456.12 | $1,164.17 / $1,006.68 | 97.4% | 39.3 | 0.0% / 12.6% | 0%/17%/44%/39% | −$242.35 | negative_fill_inventory; external sell basis unknown |
| 20 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | yes | Two-sided inventory recycling / round-trip-led | 1,796 / $670,165.19 / $18.00 / 58.0 sh | $81,734.25 | $57,149.02 / $25,712.51 | $1,840.97 / $713.70 | 97.8% | 36.3 | 0.4% / 36.8% | 0%/7%/37%/55% | −$0.01 | — |
| 21 | 0xa83c7adfd9c81b49f69b8a16e8c77641fa9aa8f4 | no | Two-sided inventory recycling / round-trip-led | 769 / $375,979.37 / $22.00 / 55.6 sh | $78,933.86 | $43,117.65 / $37,452.38 | $1,736.44 / $100.28 | 51.5% | 21.3 | 0.0% / 48.3% | 5%/20%/43%/32% | $0.00 | — |
| 22 | 0x94c4fe3b18435aa92024c7c1589537fcda3f43ca | yes | Two-sided inventory recycling / round-trip-led | 718 / $412,627.22 / $41.74 / 58.9 sh | $70,141.18 | $39,786.39 / $28,340.68 | $0.00 / $2,014.11 | 100.0% | 21.9 | 0.0% / 35.4% | 14%/52%/34%/0% | −$0.00 | — |
| 23 | 0x016a003d0a79f87fb2da292af3d520afc8ec848d | yes | Two-sided inventory recycling / round-trip-led | 1,218 / $242,550.69 / $10.04 / 40.8 sh | $67,513.16 | $52,082.87 / $15,430.29 | $0.00 / $0.00 | 100.0% | 1,223.9 | 0.6% / 54.2% | 0%/23%/2%/76% | $0.00 | — |
| 24 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | yes | Mixed inventory recycling and direction | 630 / $237,948.31 / $57.38 / 200.0 sh | $62,647.31 | $15,625.55 / $48,345.44 | $1,726.65 / $402.97 | 85.1% | 7.5 | 0.5% / 4.9% | 0%/7%/31%/62% | −$0.01 | — |
| 25 | 0x5b90d4fab4e082795eeb231b121691a0cb12c44e | yes | Two-sided inventory recycling / round-trip-led | 106 / $276,144.13 / $254.65 / 255.7 sh | $62,157.91 | $62,157.90 / $0.01 | $0.00 / $0.00 | 100.0% | 279.1 | 0.0% / 61.3% | 0%/0%/79%/21% | $0.00 | — |

### HUN_2026 top 5

| # | Wallet | MM80? | Measured pattern | Fills / turnover / median fill $ / shares | Total PnL | FIFO / terminal PnL | Fees / rebate | Zero-fee fills % | Median FIFO hold h | <5c / >95c notional % | Fills to resolution % (0–1/1–7/7–30/>30d) | Attribution Δ | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | no | Directional hold / resolution-led | 4,858 / $876,038.92 / $7.32 / 23.0 sh | $294,297.23 | $0.00 / $293,225.49 | $0.00 / $1,071.75 | 100.0% | — | 0.2% / 2.2% | 2%/15%/44%/39% | −$0.01 | — |
| 2 | 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | yes | Two-sided inventory recycling / round-trip-led | 1,406 / $536,023.94 / $27.62 / 50.0 sh | $183,894.15 | $130,860.02 / $53,041.36 | $8.43 / $1.24 | 99.9% | 4,103.5 | 0.1% / 55.9% | 9%/6%/0%/85% | −$0.04 | — |
| 3 | 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | no | Tail-priced directional exposure | 42 / $550,123.74 / $9,465.23 / 13,200.2 sh | $152,852.45 | $7,097.06 / $145,740.98 | $7.05 / $21.46 | 95.2% | 645.0 | 0.0% / 37.2% | 0%/21%/45%/33% | $0.00 | — |
| 4 | 0x0a59efd30de508bd0e7e197d4975f7ab49e107dd | yes | Two-sided inventory recycling / round-trip-led | 3,208 / $568,112.67 / $1.10 / 43.1 sh | $147,762.87 | $152,868.31 / −$5,100.53 | $7.01 / $2.24 | 99.9% | 1,609.3 | 0.9% / 63.0% | 0%/0%/3%/97% | −$0.14 | — |
| 5 | 0x4b28a660415bf1d58f358b32fc729dc6b396d19a | no | Directional hold / resolution-led | 3,589 / $221,083.95 / $1.40 / 47.8 sh | $136,144.27 | $0.00 / $136,144.26 | $0.00 / $0.00 | 100.0% | — | 2.7% / 0.0% | 0%/0%/0%/100% | $0.00 | — |

### COL_2026 top 5

| # | Wallet | MM80? | Measured pattern | Fills / turnover / median fill $ / shares | Total PnL | FIFO / terminal PnL | Fees / rebate | Zero-fee fills % | Median FIFO hold h | <5c / >95c notional % | Fills to resolution % (0–1/1–7/7–30/>30d) | Attribution Δ | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xa83c7adfd9c81b49f69b8a16e8c77641fa9aa8f4 | no | Two-sided inventory recycling / round-trip-led | 751 / $349,010.14 / $20.00 / 54.1 sh | $79,300.12 | $43,148.41 / $37,452.38 | $1,400.95 / $100.28 | 52.7% | 90.8 | 0.0% / 52.0% | 5%/20%/42%/32% | −$0.00 | — |
| 2 | 0xc7e53ac4a7c76d6df8b794de2e7d0794265d2d3a | yes | Mixed inventory recycling and direction | 253 / $93,568.18 / $84.70 / 195.1 sh | $57,974.25 | $11,095.93 / $47,361.05 | $721.84 / $239.11 | 81.4% | 774.0 | 0.0% / 12.6% | 0%/18%/35%/47% | $0.01 | — |
| 3 | 0x23d81ba9371e576015c1e562db09c689f56b0288 | yes | Two-sided inventory recycling / round-trip-led | 1,192 / $224,496.40 / $12.29 / 50.0 sh | $57,117.26 | $38,885.99 / $14,362.88 | $328.89 / $4,197.29 | 97.9% | 69.9 | 0.4% / 46.2% | 0%/20%/61%/19% | −$0.00 | — |
| 4 | 0x0d2d845a6ff64e31e04a70afce8a573940767ff5 | no | Mixed inventory recycling and direction | 51 / $1,382.55 / $19.94 / 53.0 sh | $54,503.18 | −$21.52 / $39.87 | $0.72 / $54,485.56 | 98.0% | 33.4 | 0.0% / 0.0% | 0%/0%/0%/100% | $0.00 | — |
| 5 | 0xcb25c43d98019b6acf4d6912a231bbb689a45ab5 | no | Two-sided inventory recycling / round-trip-led | 1,320 / $95,389.25 / $6.75 / 59.5 sh | $42,723.32 | $50,725.17 / −$6,660.57 | $1,361.59 / $20.37 | 67.5% | 837.7 | 0.6% / 2.8% | 0%/6%/32%/62% | −$0.05 | — |

### PER_2026 top 5

| # | Wallet | MM80? | Measured pattern | Fills / turnover / median fill $ / shares | Total PnL | FIFO / terminal PnL | Fees / rebate | Zero-fee fills % | Median FIFO hold h | <5c / >95c notional % | Fills to resolution % (0–1/1–7/7–30/>30d) | Attribution Δ | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | no | Directional hold / resolution-led | 384 / $265,131.51 / $14.41 / 22.3 sh | $150,465.23 | $0.00 / $150,961.21 | $1,165.15 / $669.16 | 98.2% | — | 0.0% / 0.0% | 0%/0%/30%/70% | $0.00 | — |
| 2 | 0x1fee90f352fd362ea4bf13c6ed7cdd83a4d2ed5e | yes | Mixed recycling; activity near large VWAP moves | 737 / $435,495.76 / $21.70 / 60.1 sh | $149,109.85 | $46,133.52 / $103,097.71 | $347.33 / $225.97 | 98.6% | 53.1 | 0.1% / 0.0% | 0%/0%/21%/79% | −$0.02 | — |
| 3 | 0xdc03d611e5bcc9f1c87edb95edeb91671471804c | yes | Two-sided inventory recycling / round-trip-led | 6,210 / $2,047,052.08 / $14.95 / 50.0 sh | $99,632.43 | $72,610.25 / $34,387.96 | $4,306.32 / $803.00 | 98.6% | 214.7 | 0.3% / 10.7% | 0%/0%/26%/74% | −$3,862.47 | negative_fill_inventory; external sell basis unknown |
| 4 | 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | no | Directional hold / resolution-led | 3,529 / $1,368,146.08 / $37.79 / 78.1 sh | $89,171.38 | −$2,023.33 / $92,427.54 | $2,690.97 / $1,458.17 | 96.8% | 2.9 | 0.0% / 4.1% | 0%/0%/59%/41% | −$0.03 | — |
| 5 | 0xc8b9a30184244d427169cf62485dde6041b2b836 | yes | Two-sided inventory recycling / round-trip-led | 1,654 / $594,916.10 / $20.00 / 62.3 sh | $76,018.70 | $53,595.71 / $23,656.73 | $1,839.47 / $605.75 | 97.8% | 30.0 | 0.5% / 32.0% | 0%/0%/40%/60% | −$0.01 | — |

### What the largest strategies actually did

- **0x629bc4a1… (largest pooled PnL):** $379,761 total, with −$2,023 FIFO matched buy-to-sell and +$382,046 terminal/resolution. It traded 97 markets across all three families; 39% of fills fell within the defined ±1-day large-VWAP-move window. This is primarily directional/resolution exposure, with no claim that the window caused the trades.

- **0xe5c215ac… (pooled rank 4):** $152,852 on 42 fills and $550,124 notional, mostly HUN. FIFO matched PnL is +$7,097 and terminal/resolution is +$145,741; 37.2% of notional traded above 95c. Its low fill count relative to notional and concentrated outcome exposure make this a sparse, tail-priced directional winner.

- **0x1fee90f3… (second pooled MM-like):** $147,894 from 871 fills/$679k, 12/19 tokens traded both ways, 6 NegRisk events touched (4 multi-market), 53.1h volume-weighted median FIFO hold, and 69.6% of fills within the large-VWAP window. PnL splits +$42,146 FIFO matched trade proxy and +$105,663 terminal/resolution. Its largest markets were Keiko Fujimori and Hungary PM outcomes; it spans HUN and PER.

- **0x44886115…:** $135,836 over 3,531 fills/$2.32m and 54 markets across all three families. 60/68 tokens were two-sided, it touched 17 NegRisk events (15 with multiple scoped markets), and its FIFO component was +$131,407 versus +$2,734 terminal/directional. This is the clearest broad inventory-recycling pattern among the top pooled MM-like leaders.

- **0xdfdcd929…:** $118,153 on 141 fills/$680k, entirely explained by about +$118,153 FIFO buy-to-sell matching and approximately $0.02 terminal PnL; 100% of its one traded token was two-sided and median matched hold was 76.4h. It qualifies under the one-token two-sided screen and illustrates how a small number of high-notional recycling cycles can dominate.

- **0xc4368bb2…:** $150,465 pooled and $150,465 in PER from two markets and 384 buys. No FIFO round trips were matched; terminal/resolution PnL was about +$150,961 before $1,165 net fees and $669 rebate. This is a concentrated hold-to-resolution strategy, not two-sided market making.

- **Tail-priced directional example, 0xcf6087ab…:** pooled PnL $89,377, with 65.1% of executed notional above 95c, +$70,514 terminal/directional PnL, about 416h median matched hold, and nearly half of fills 7–30 days from resolution.

- **Close-to-resolution activity example, 0xa527e3a0…:** pooled PnL $87,021; 54.4% of fills were 1–7 days before recorded resolution, while median FIFO matched hold was about 887h. That mismatch is why the label describes timing of fills, not a proven short-lived snipe.

- **Lower zero-fee profitable example, 0xa83c7adf…:** pooled PnL $78,934 with only 51.5% zero-fee fills. It remains profitable but fails the 80% zero-fee MM-like threshold; the table retains it among top any-type winners.

## What separates pooled MM-like winners from losers?

Among pooled MM-like wallets at the 80% threshold, 1,374 are positive, 693 negative, and 60 effectively flat. Winner and loser medians below show that qualifying as low-fee and two-sided does not guarantee positive returns.

| Scope | PnL cohort | Wallets | Median PnL | Median PnL / notional | Median zero-fee share | Median two-sided-token share | Negative fill-inventory wallets |
| --- | --- | --- | --- | --- | --- | --- | --- |
| POOLED | positive | 1374 | $513.51 | 3.29% | 99.9% | 100.0% | 8.2% |
| POOLED | negative | 693 | −$88.97 | -1.54% | 100.0% | 100.0% | 4.0% |
| HUN_2026 | positive | 791 | $501.20 | 3.25% | 100.0% | 100.0% | 7.5% |
| HUN_2026 | negative | 341 | −$38.06 | -0.57% | 100.0% | 100.0% | 5.0% |
| COL_2026 | positive | 286 | $270.04 | 5.78% | 98.9% | 95.7% | 8.7% |
| COL_2026 | negative | 98 | −$177.55 | -7.25% | 100.0% | 100.0% | 4.1% |
| PER_2026 | positive | 539 | $494.71 | 3.53% | 98.4% | 97.2% | 13.5% |
| PER_2026 | negative | 235 | −$474.02 | -4.48% | 99.5% | 100.0% | 5.5% |

For pooled winners versus losers, median zero-fee shares are 99.9% versus 100.0%, and median two-sided-token shares are 100.0% for both; both groups satisfy high values by construction. Winners have higher median PnL/turnover, while external inventory flags are more common among winners (8.2% vs 4.0%), which is not evidence of a causal advantage and makes their true PnL less certain because some share basis is outside the fill ledger. The strongest separators visible here are the resulting directional/resolution inventory and the outcome concentration of each wallet, rather than the fee screen alone.

## Negative fill-only inventory and unknown basis

Across pooled wallets, **835 wallets** had a token balance go negative using only scoped buys and sells. Their excess sell proceeds total **$3,201,121**; this is proceeds against shares supplied outside the scoped fill ledger, not profit and not a cost-basis estimate. In the pooled 80% MM-like cohort, 141 wallets have this flag and their excess sell proceeds total $1,730,013; the largest observed single-token shortfall is about 802,840 shares. Family figures overlap by wallet and must not be added to get the pooled unique count. These amounts identify where wallet PnL/rank can be affected; no invented cost basis was added.

## Cross-checks

### Resolved-market zero-sum check

For each resolved market, the residual is the sum of fill cashflow (sell proceeds minus buy cost) plus payout on net shares at the recorded resolution-time cutoff. This equals aggregate wallet PnL after adding back net fees and subtracting allocated rebates. Canonical payout coverage is complete in every resolved market that has fills.

| Family | Resolved markets with complete payout | Residual sum | Sum absolute residuals | 95th pct abs residual | Max abs market residual |
| --- | --- | --- | --- | --- | --- |
| HUN_2026 | 99 | −$0.1309 | $0.1527 | $0.0071 | $0.0233 |
| COL_2026 | 216 | −$244.2815 | $244.3131 | $0.0019 | $176.4879 |
| PER_2026 | 217 | −$0.1945 | $0.2563 | $0.0028 | $0.0694 |
| TOTAL | 532 | −$244.6069 | $244.7221 | — | — |

HUN and PER residual sums are −$0.1309 and −$0.1945. COL is −$244.2815 (maximum absolute market residual $176.4879), making the total residual −$244.6069. The COL exception is almost entirely fill flow after the inventory resolution timestamp: 158 rows across five markets have $353.0616 notional and −$244.2086 signed flow, with maximum recorded lag 7,064,851 seconds (about 81.8 days). Two affected NegRisk sibling markets contribute −$176.4879 (78 late rows) and −$67.7216 (72 late rows). Limiting those markets to pre-resolution flow leaves about $0.073 family residual, but the report keeps all late fill cashflow and the raw −$244.2815 family reconciliation residual. This is a timestamp/corpus mismatch investigation, not proof that the rows are split/merge conversions. HUN has 6 post-resolution rows/$239.76 notional/zero signed flow (max lag 42s); PER has 6/$6.286/zero flow (max lag 81s).

### External Polymarket Data API cross-check: top five pooled PnL wallets

Queried the public `/activity` endpoint with `type=TRADE`, `user`, scoped condition IDs, and offset pagination. Wallet/market activity docs: [Wallet Activity](https://docs.polymarket.com/trading/wallet-activity.md); API reference: [Data API v2 docs](https://data-api.polymarket.com/v2/docs). Each wallet’s source and API row count and transaction-condition-token-side key counts match exactly; there are zero source-only and zero API-only key groups. Raw notional differences remain visible below.

| Wallet | Source / API rows | Source / API tx-condition-token-side groups | Source notional | API notional | Raw API − source | Signed net-fee amount | Residual after fee amount | Source-only / API-only groups |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0x629bc4a1e53e1d475beb7ea3d388791e96dd995a | 9,096 | 9,096 | 8,967/8,967 | $2,389,908.98 | $2,392,795.85 | $2,886.86 | $2,988.47 | −$101.61 | 0 / 0 |
| 0x88b59d79b6e1659c95a0043028e5bb7a26e6205c | 2,170 | 2,170 | 2,149/2,149 | $1,091,299.19 | $1,092,188.78 | $889.60 | $1,003.24 | −$113.64 | 0 / 0 |
| 0xffe9858781cc1f053e7f3a5fd02a0cf6ee0f1599 | 1,406 | 1,406 | 1,346/1,346 | $536,023.94 | $536,015.51 | −$8.43 | −$8.43 | $0.00 | 0 / 0 |
| 0xe5c215ac428d4143f9ebc817c9ac6e717a2e2ab0 | 42 | 42 | 42/42 | $550,123.74 | $550,124.06 | $0.32 | $0.32 | −$0.00 | 0 / 0 |
| 0xc4368bb22b77b2be880fabedb705ac7a703947a2 | 384 | 384 | 383/383 | $265,131.51 | $266,296.58 | $1,165.07 | $1,165.15 | −$0.08 | 0 / 0 |

API activity `usdcSize` totals differ from source fill notional as shown. The signed net-fee cashflow explains much of the difference for several wallets, but fee-adjusted residuals remain (in table order) −$101.61, −$113.64, +$0.0008, approximately $0.00, and −$0.08. This is an inference about field semantics, not a reconciliation rule; no source notional or PnL was tuned to the API.

## Three worked transaction examples

`order_is_match_taker_order` below describes the exchange call’s matched order role. It is not labeled as a real-world liquidity maker/taker. In each example the listed owner is the participant whose fill row is counted.

1. **HUN complementary YES/NO buys through V1 NegRisk** — event 34038, “Will the next Prime Minister of Hungary be Péter Magyar?” Tx `0xd44880b145941eb887dfbf807e6f9244175347e36197c5ad6ba6f1acbef6591a`, condition `0x1480b819d03d4b6388d70e848b0384adf38c38d955cb783cdbcf6d4a436dee14`. Log 715: `0x63d43bbb87f85af03b8f2f9e2fad7b54334fa2f1` buys 20 NO at $0.39 ($7.80), taker-order flag false, counterparty `0x386849a339068bfa52de65d1e62a643182da6ba8`, zero net fee under the pre-fee-era evidence label. Log 717: `0x386849a339068bfa52de65d1e62a643182da6ba8` buys 20 YES at $0.61 ($12.20), taker-order flag true, counterparty V1 NegRisk exchange `0xc5d563a36ae78145c45a50134d48a1215220f80a`. These are two participants and complementary outcomes; they are not duplicate rows and do not by themselves establish a conversion.

2. **HUN same-token opposite-side match** — “Will Fidesz-KDNP win at least 80 seats?” Tx `0xb939a7080571d5aef1f63bd4ab9f627165ca3f97f7502c2d98288f070b3cbc83`, condition `0x3d418a3caa9a19037d40461032e4b18daefc08373ada02d584b9261d7e13c8f6`. Log 920: `0x4b8ae011176b76888949476d7fb7a56985faf4a4` buys 1.26 YES at $0.72 ($0.9072), taker-order flag false, with `0xdd8a8ff6c9b2fee3eaaaefc1d9696337d44f9c77` as counterparty. Log 922: `0xdd8a8ff6c9b2fee3eaaaefc1d9696337d44f9c77` sells the same 1.26 YES at $0.72 ($0.9072), taker-order flag true, with V1 CTF exchange `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e` as counterparty. Both participant rows are present; the exchange contract is not counted as a wallet.

3. **PER complementary YES/NO buys through V1 NegRisk** — “Will Vladimir Cerrón win the 2026 Peruvian presidential election?” Tx `0x0055ddc751497bb6b2b4f797cdee17e306b2923b44e645bb5b9b61a7ab61aa9c`, condition `0xa61438a389267c7deafc9d04cd6d0dc597d70253eb94702c0f5be458d75719a2`. Log 1358: `0xad5353afe30c2da57709e2704ef3ccdcf67eef24` buys 5 YES at $0.04 ($0.20), taker-order flag false. Log 1360: `0xb6fa57039ea79185895500dbd0067c288594abcf` buys 5 NO at $0.96 ($4.80), taker-order flag true, counterparty V1 NegRisk exchange `0xc5d563a36ae78145c45a50134d48a1215220f80a`. The prices sum to $1, but the two rows alone are not proof of arbitrage or an atomic conversion.

## What I did NOT do / caveats

- I did not use RPC or query split/merge/conversion contracts. These rows are an OrderFilled corpus; they do not fully show the off-fill share-supply paths that create negative fill-ledger positions.

- I did not invent cost basis for externally sourced shares. Negative-balance wallets are flagged, excess sell proceeds are reported as exposure, and PnL/ranks for those wallets can be wrong by an unknown amount. The FIFO attribution residual is shown rather than forced to zero.

- I did not use orderbook snapshots or resting quotes. Prices, tails, turnover, two-sidedness, and FIFO matching describe executed fills; they cannot prove quote placement, spread width, queue position, adverse selection, or a risk-free arbitrage.

- FIFO buy-to-sell PnL is a matched-trade price-change proxy, not pure spread capture; it can include directional price changes. The terminal/resolution component is inventory left after matched buys and sells, valued at payout or last fill.

- I did not infer news causality or automation from large-VWAP windows. The window is a descriptive rule: a token’s daily size-weighted fill VWAP moved at least $0.10 versus the prior observed fill day, and a wallet fill occurred within ±1 calendar day. No trade-side response or external news timestamps were modeled.

- I did not treat close-to-resolution fills as proof of sniping. Fill-to-resolution buckets use inventory timestamps; median FIFO hold is a separate statistic and can be much longer.

- I did not override fee evidence with current fee-regime snapshots or force Data API notional to match local fill notional. API raw mismatches are retained. Only the top five pooled PnL wallets were API-crosschecked, not all 121,639 wallets.

- Family rebate allocation is an attribution convention using each wallet’s protocol maker-order notional as the available proxy. It is not evidence those fills were passive or qualified for every rebate independently.

- The COL post-resolution flow anomaly remains in the reported family and wallet PnL. The nearly offsetting pre-resolution reconciliation is diagnostic only; the raw −$244.28 residual is not tuned away.

- Strategy archetype labels are descriptive heuristics from the supplied fills and payout records. They do not establish identity, common ownership, informed trading, or a durable future edge.

## Deliverables

- Wallet metrics: [`wallet_metrics_pooled.parquet`](outputs/wallet_metrics_pooled.parquet), [`wallet_metrics_HUN_2026.parquet`](outputs/wallet_metrics_HUN_2026.parquet), [`wallet_metrics_COL_2026.parquet`](outputs/wallet_metrics_COL_2026.parquet), [`wallet_metrics_PER_2026.parquet`](outputs/wallet_metrics_PER_2026.parquet).

- Ranked 80% MM-like top 25 per scope: [`mm_leaderboard.csv`](outputs/mm_leaderboard.csv).

- Top-any-type strategy cohort and anatomy details: [`other_profitable_strategies.csv`](outputs/other_profitable_strategies.csv), [`strategy_anatomy.csv`](outputs/strategy_anatomy.csv), [`strategy_anatomy.json`](data/strategy_anatomy.json).

- Resolution, API, and aggregate analysis support: [`analysis_summary.json`](data/analysis_summary.json), [`post_audit.json`](data/post_audit.json), [`api_crosscheck.json`](data/api_crosscheck.json).
