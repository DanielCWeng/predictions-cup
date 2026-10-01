# LIVE-ALPHA-001 — live tape alpha sweep

## Scope

- Canonical EXACT/SAME mappings: **140**.
- Matched SIG trades: **1,017** across **95 markets**.
- Tape: **2026-10-01 16:29:58.417182+00:00 → 2026-10-01 17:00:04.177638+00:00**; chronological DEV/VAL cut: **2026-10-01 16:48:01.873455600+00:00**.
- Polymarket is the external fair-value anchor. Lead-lag is not a promotable strategy in this run.
- Passive fills are optimistic: first in queue and any trade through our quote fills us.
- Intraday SIG leaderboard marking remains unresolved; economics are against PM fair value.

## Aggregate observed maker economics

- Entry edge: **4.01c mean / 1.00c median**.
- Maker markout 15s / 60s / 300s: **4.08c / 4.09c / 4.15c**.
- PM move against maker in prior 5s: **0.001c**.

## Validated PM-anchored maker rules

| offset_c | max_pm_spread_c | min_depth | min_excess_spread_c | dev_n | val_n | val_m60_c | val_m60_ci_lo_c | val_m300_c | val_markets | val_max_market_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.750 | 1.000 | 50.000 | 0.500 | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| 0.750 | 2.000 | 50.000 | 0.500 | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| 1.000 | 1.000 | 50.000 | 0.500 | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| 1.000 | 2.000 | 50.000 | 0.500 | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| 0.750 | 1.000 | 50.000 | 1.000 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| 0.750 | 1.000 | 100.000 | 0.500 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| 0.750 | 2.000 | 50.000 | 1.000 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| 0.750 | 2.000 | 100.000 | 0.500 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| 1.000 | 1.000 | 50.000 | 1.000 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| 1.000 | 1.000 | 100.000 | 0.500 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| 1.000 | 2.000 | 50.000 | 1.000 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| 1.000 | 2.000 | 100.000 | 0.500 | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| 0.750 | 1.000 | 100.000 | 1.000 | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |
| 0.750 | 1.000 | 200.000 | 0.500 | 24.000 | 11.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.364 |
| 0.750 | 2.000 | 100.000 | 1.000 | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |

## Market selection

| exchange_id | title | dev_n | val_n | val_edge_c | val_m60_c | val_m60_ci_lo_c | val_m300_c | val_pre_move_against_c | val_notional |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1072 | Will the Democratic Party win the Oklahoma Senate? | 0 | 15 | 26.763 | 26.763 | 8.752 | 26.763 | 0.000 | 98485.970 |
| 980 | Will the Democratic Party win the South Dakota Senate? | 32 | 11 | 3.168 | 3.168 | 2.705 | 3.239 | 0.000 | 34.580 |
| 933 | Will the Republican Party win the TX-15 House race? | 3 | 9 | 2.722 | 2.375 | 2.130 | 2.500 | 0.056 | 443.340 |
| 928 | Will the Democratic Party win the PA-10 House race? | 0 | 4 | 2.250 | 2.250 | 1.967 | 2.250 | 0.000 | 508.350 |
| 1031 | Will the Democratic Party win the VA-01 House race? | 22 | 12 | 2.958 | 2.958 | 1.708 | 3.364 | 0.000 | 1355.885 |
| 1014 | Will the Republican Party win the FL-16 House race? | 4 | 2 | 1.500 | 1.500 | 1.500 | 1.500 | 0.000 | 60.480 |
| 1065 | Will the Republican Party win the NH-01 House race? | 4 | 7 | 2.021 | 2.021 | 1.027 | 2.021 | 0.000 | 91.775 |
| 1037 | Will the Republican Party win the WA-03 House race? | 9 | 2 | 2.000 | 2.000 | 1.020 | 2.000 | 0.000 | 10.500 |
| 1077 | Will the Republican Party win the Rhode Island Senate? | 73 | 16 | 6.500 | 7.214 | 1.012 | 7.214 | 0.000 | 225.670 |
| 1033 | Will the Democratic Party win the VA-05 House race? | 11 | 5 | 0.900 | 0.900 | 0.704 | 0.875 | 0.000 | 163.500 |
| 960 | Will the Republican Party win the Maine Senate? | 31 | 37 | 0.878 | 0.803 | 0.368 | 0.903 | 0.000 | 5726.750 |
| 1075 | Will the Republican Party win the Delaware Senate? | 13 | 4 | 1.738 | 1.875 | 0.356 | 1.625 | 0.188 | 69.295 |
| 1045 | Will the Democratic Party win the Kansas Senate? | 14 | 7 | 0.429 | 0.429 | 0.289 | 0.429 | 0.000 | 441.930 |
| 951 | Will the Democratic Party win the Idaho Senate? | 17 | 6 | 0.533 | 0.383 | 0.057 | 0.050 | 0.000 | 10.825 |
| 970 | Will the Republican Party win the Nebraska Senate? | 21 | 7 | 0.357 | 0.357 | 0.005 | 0.250 | 0.000 | 2437.810 |

## State-regime slices

| feature | bucket | dev_n | val_n | dev_m60_c | val_m60_c | val_ci_lo_c | val_m300_c | val_markets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pm_mid | (-0.001, 0.05] | 210 | 60 | 11.119 | 9.245 | 3.923 | 9.919 | 10 |
| pm_spread | (0.01, 0.02] | 117 | 40 | 1.566 | 10.211 | 2.457 | 10.864 | 6 |
| sig_spread | (0.03, 0.04] | 23 | 13 | 6.515 | 3.012 | 2.203 | 3.050 | 6 |
| pm_spread | (0.02, 0.04] | 18 | 10 | 1.289 | 2.210 | 1.505 | 2.090 | 2 |
| sig_residual_abs | (0.01, 0.02] | 223 | 89 | 3.684 | 4.880 | 1.265 | 5.148 | 24 |
| sig_spread | (0.04, 0.06] | 17 | 10 | 11.341 | 2.480 | 1.034 | 2.480 | 2 |
| prior60_count | (1.5, 3.5] | 164 | 54 | 1.218 | 1.246 | 0.732 | 1.231 | 25 |
| sig_spread | (0.01, 0.015] | 149 | 34 | 6.137 | 1.426 | 0.683 | 1.411 | 13 |
| sig_residual_abs | (0.005, 0.01] | 129 | 54 | 1.078 | 1.901 | 0.671 | 1.941 | 14 |
| sig_residual_abs | (-0.001, 0.005] | 167 | 39 | 5.290 | 0.846 | 0.655 | 0.894 | 14 |
| pm_spread | (-0.001, 0.005] | 76 | 17 | 11.224 | 1.732 | 0.645 | 1.600 | 4 |
| sig_spread | (-0.001, 0.01] | 312 | 119 | 2.541 | 3.314 | 0.581 | 3.488 | 26 |
| sig_spread | (0.015, 0.02] | 98 | 17 | 2.062 | 1.238 | 0.536 | 1.083 | 12 |
| pm_spread | (0.005, 0.01] | 520 | 155 | 4.310 | 1.184 | 0.517 | 1.229 | 30 |
| sig_spread | (0.02, 0.03] | 97 | 33 | 1.971 | 2.405 | 0.385 | 2.502 | 10 |

## Secondary residual taker rules

| threshold_c | max_pm_spread_c | min_depth | dev_n | val_n | dev_m60_c | val_m60_c | val_m60_ci_lo_c | val_m300_c | val_markets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.000 | 2.000 | 50.000 | 32.000 | 11.000 | 2.519 | 2.400 | 2.200 | 2.333 | 6.000 |
| 2.000 | 1.000 | 50.000 | 28.000 | 10.000 | 2.548 | 2.390 | 2.170 | 2.300 | 5.000 |
| 2.000 | 2.000 | 100.000 | 26.000 | 9.000 | 2.552 | 2.389 | 2.142 | 2.333 | 5.000 |
| 2.000 | 1.000 | 100.000 | 23.000 | 8.000 | 2.580 | 2.375 | 2.097 | 2.300 | 4.000 |
| 2.000 | 1.000 | 200.000 | 5.000 | 4.000 | 2.200 | 2.000 | 2.000 | 2.000 | 3.000 |
| 2.000 | 1.000 | 500.000 | 5.000 | 3.000 | 2.200 | 2.000 | 2.000 | 2.000 | 1.000 |
| 2.000 | 2.000 | 500.000 | 7.000 | 3.000 | 2.143 | 2.000 | 2.000 | 2.000 | 1.000 |
| 2.000 | 2.000 | 200.000 | 7.000 | 5.000 | 2.143 | 2.100 | 1.904 | 2.125 | 4.000 |
| 1.500 | 1.000 | 50.000 | 63.000 | 25.000 | 1.974 | 1.910 | 1.727 | 1.827 | 13.000 |
| 1.500 | 2.000 | 50.000 | 100.000 | 30.000 | 1.894 | 1.875 | 1.709 | 1.811 | 16.000 |
| 1.500 | 1.000 | 100.000 | 58.000 | 21.000 | 1.937 | 1.893 | 1.692 | 1.869 | 11.000 |
| 1.500 | 2.000 | 100.000 | 94.000 | 26.000 | 1.863 | 1.856 | 1.678 | 1.841 | 14.000 |

## Mapping / semantics outliers

| exchange_id | title | dev_n | val_n | val_edge_c | val_m60_c | val_m300_c | val_sig_spread_c | val_pm_spread_c |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1072 | Will the Democratic Party win the Oklahoma Senate? | 0 | 15 | 26.763 | 26.763 | 26.763 | 1.000 | 1.100 |

## Candidate list

| family | status | spec | dev_n | val_n | val_m60_c | val_ci_lo_c | val_m300_c | val_markets | concentration |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=1.0c depth>=50 excess>=0.5c | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=2.0c depth>=50 excess>=0.5c | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=1.0c depth>=50 excess>=0.5c | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=2.0c depth>=50 excess>=0.5c | 32.000 | 15.000 | 1.000 | 1.000 | 1.000 | 8.000 | 0.267 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=1.0c depth>=50 excess>=1.0c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=1.0c depth>=100 excess>=0.5c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=2.0c depth>=50 excess>=1.0c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=2.0c depth>=100 excess>=0.5c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=1.0c depth>=50 excess>=1.0c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=1.0c depth>=100 excess>=0.5c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=2.0c depth>=50 excess>=1.0c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=2.0c depth>=100 excess>=0.5c | 32.000 | 13.000 | 1.000 | 1.000 | 1.000 | 7.000 | 0.308 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=1.0c depth>=100 excess>=1.0c | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=1.0c depth>=200 excess>=0.5c | 24.000 | 11.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=2.0c depth>=100 excess>=1.0c | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=0.75c pm<=2.0c depth>=200 excess>=0.5c | 24.000 | 11.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=1.0c depth>=100 excess>=1.0c | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=1.0c depth>=200 excess>=0.5c | 24.000 | 11.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=2.0c depth>=100 excess>=1.0c | 32.000 | 11.000 | 1.000 | 1.000 | 1.000 | 5.000 | 0.364 |
| PM_ANCHORED_MAKER | PAPER_CANDIDATE | offset=1.00c pm<=2.0c depth>=200 excess>=0.5c | 24.000 | 11.000 | 1.000 | 1.000 | 1.000 | 6.000 | 0.364 |
| MARKET_SELECTION | PAPER_CANDIDATE | 980 Will the Democratic Party win the South Dakota Senate? | 32.000 | 11.000 | 3.168 | 2.705 | 3.239 | 1.000 | 1.000 |
| MARKET_SELECTION | PAPER_CANDIDATE | 1031 Will the Democratic Party win the VA-01 House race? | 22.000 | 12.000 | 2.958 | 1.708 | 3.364 | 1.000 | 1.000 |
| MARKET_SELECTION | PAPER_CANDIDATE | 1077 Will the Republican Party win the Rhode Island Senate? | 73.000 | 16.000 | 7.214 | 1.012 | 7.214 | 1.000 | 1.000 |
| MARKET_SELECTION | PAPER_CANDIDATE | 1033 Will the Democratic Party win the VA-05 House race? | 11.000 | 5.000 | 0.900 | 0.704 | 0.875 | 1.000 | 1.000 |
| MARKET_SELECTION | PAPER_CANDIDATE | 960 Will the Republican Party win the Maine Senate? | 31.000 | 37.000 | 0.803 | 0.368 | 0.903 | 1.000 | 1.000 |

## Discipline

- PAPER_CANDIDATE is not LIVE approval.
- Prefer multi-market rules with positive holdout lower confidence bounds.
- Single-market winners are concentration risks even if economics are strong.
- Queue position, fees, inventory recycling, and SIG account marking must be measured before sizing.
- Re-run on a later tape before increasing size.
