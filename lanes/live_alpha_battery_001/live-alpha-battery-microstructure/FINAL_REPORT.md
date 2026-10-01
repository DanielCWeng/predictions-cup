# LIVE ALPHA BATTERY — 005F / 005I Microstructure

This lane distinguishes exact observable concepts from live proxies; it does not relabel proxies as frozen-model replications.
Lead-lag is diagnostic only and cannot be promoted.

## Validation slices
| mechanism                   | spec                             | split   |   n |   markets |   economic_c |     ci_lo_c |
|:----------------------------|:---------------------------------|:--------|----:|----------:|-------------:|------------:|
| FLOW_CONTEXT_ONLY           | prior60_abs>DEV_q0.9             | VAL     | 137 |         8 |   15.062     |   9.85082   |
| FLOW_CONTEXT_ONLY           | prior60_abs>DEV_q0.75            | VAL     | 259 |        16 |    9.53822   |   6.48927   |
| FLOW_CONTEXT_ONLY           | prior60_abs>DEV_q0.5             | VAL     | 400 |        28 |    7.34887   |   5.17497   |
| 005I_5M_REVERSION_LIVE      | abs_pm_ret5>=0.005               | VAL     |  54 |        12 |    0.268966  |   0.136444  |
| 005I_5M_REVERSION_LIVE      | abs_pm_ret5>=0.0025              | VAL     |  88 |        18 |    0.197619  |   0.077573  |
| 005I_REPLENISHMENT_PROXY    | depth_delta1>=200                | VAL     | 301 |        95 |   -0.0311404 |  -0.0521627 |
| 005F_SIG_BBO_AGE_PROXY      | sig_age_s>=15                    | VAL     | 698 |       139 |    0.171199  |  -0.102514  |
| 005I_LIQUIDITY_STRESS_PROXY | rel_spread>=0.9 depth_total<=605 | VAL     |  71 |         7 |    0.151667  | nan         |
| 005F_SIG_BBO_AGE_PROXY      | sig_age_s>=30                    | VAL     |  56 |        54 |   -1         | nan         |
| 005I_5M_REVERSION_LIVE      | abs_pm_ret5>=0.01                | VAL     |   2 |         1 |  nan         | nan         |
