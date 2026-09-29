# EXPERIMENT-005B — Final Historical Price / Fill Predictive Atlas

## Disposition

This report is a discovery atlas, not an executable strategy or alpha claim. Feature/target definitions, splits, screening policy and model grids were frozen before intentional inspection of prior ranked result cells. HOLDOUT was opened only after the TRAIN/DEV shortlist was hash-bound.

## Corpus and reconstruction

- DATA-002 participant-side rows: **18,317,515**.
- Transaction-condition groups: **8,238,705**.
- Accepted canonical groups: **8,236,688**.
- Rejected groups: **2,017**.
- Canonical economic fills: **10,071,855**.

The active aggregate OrderFilled row is audit-only; passive legs form the economic trade series. Binary prices are canonicalised to the YES axis. Role, fee and address fields do not enter the 005B predictors.

## Atlas dimensions

- Feature count: **231**.
- Target count: **47**.
- Redundancy clusters: **134**.
- Representative features after TRAIN-only collapse: **134**.
- Feature-build rows: **10071855**.

### Split support by family

| Family | TRAIN rows | DEV rows | HOLDOUT rows |
|---|---:|---:|---:|
| US_2024 | 22,238 | 353,505 | 7,514,720 |
| CAN_2025 | 509,830 | 2,735 | 336 |
| COL_2026 | 55,219 | 197,648 | 159,996 |
| HUN_2026 | 19,359 | 109,379 | 376,657 |
| PER_2026 | 539,247 | 191,992 | 18,994 |

The split boundaries are synchronized chronological time-span boundaries, not row quantiles. Row-count imbalances therefore reflect when trading activity occurred inside each family and are retained rather than repaired post hoc.

## TRAIN → DEV screen

Targets with at least one promoted scalar candidate: **20 / 47**.
Targets opened in sealed HOLDOUT: **20**.

Candidate labels are descriptive evidence labels only. A stable sign across TRAIN and DEV does not by itself establish economic usefulness, portability, or executability.

## Candidate registry

| Target | Scalar feature | Label | TRAIN ρ | DEV ρ | HOLDOUT ρ | Model | HOLDOUT model metric | Bootstrap interval |
|---|---|---|---:|---:|---:|---|---|---|
| target_clock_realised_movement_1 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.423 | 0.3732 | 0.3227 | hist_gradient_boosting | MAE Δ 0.517 | [0.0007059, 0.001689] |
| target_clock_realised_movement_1 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.3965 | 0.3418 | 0.2084 | hist_gradient_boosting | MAE Δ 0.517 | [0.0007059, 0.001689] |
| target_clock_realised_movement_120 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.5562 | 0.5877 | 0.5484 | hist_gradient_boosting | MAE Δ 0.2099 | [0.003038, 0.005596] |
| target_clock_realised_movement_120 | absolute_return_900 | CROSS_FAMILY_CANDIDATE | 0.5015 | 0.4881 | 0.4044 | hist_gradient_boosting | MAE Δ 0.2099 | [0.003038, 0.005596] |
| target_clock_realised_movement_120 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4726 | 0.4708 | 0.6225 | hist_gradient_boosting | MAE Δ 0.2099 | [0.003038, 0.005596] |
| target_clock_realised_movement_15 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4695 | 0.4521 | 0.5908 | hist_gradient_boosting | MAE Δ 0.4804 | [0.001329, 0.002948] |
| target_clock_realised_movement_15 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.4672 | 0.4614 | 0.4229 | hist_gradient_boosting | MAE Δ 0.4804 | [0.001329, 0.002948] |
| target_clock_realised_movement_15 | absolute_return_900 | CROSS_FAMILY_CANDIDATE | 0.4197 | 0.404 | 0.3122 | hist_gradient_boosting | MAE Δ 0.4804 | [0.001329, 0.002948] |
| target_clock_realised_movement_30 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.4933 | 0.51 | 0.4879 | hist_gradient_boosting | MAE Δ 0.396 | [0.001742, 0.003523] |
| target_clock_realised_movement_30 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4742 | 0.4699 | 0.6163 | hist_gradient_boosting | MAE Δ 0.396 | [0.001742, 0.003523] |
| target_clock_realised_movement_30 | absolute_return_900 | CROSS_FAMILY_CANDIDATE | 0.4431 | 0.4362 | 0.3544 | hist_gradient_boosting | MAE Δ 0.396 | [0.001742, 0.003523] |
| target_clock_realised_movement_300 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.6013 | 0.6353 | 0.5724 | hist_gradient_boosting | MAE Δ 0.136 | [0.004297, 0.007767] |
| target_clock_realised_movement_300 | absolute_return_900 | CROSS_FAMILY_CANDIDATE | 0.5389 | 0.5164 | 0.4264 | hist_gradient_boosting | MAE Δ 0.136 | [0.004297, 0.007767] |
| target_clock_realised_movement_300 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4737 | 0.4627 | 0.6167 | hist_gradient_boosting | MAE Δ 0.136 | [0.004297, 0.007767] |
| target_clock_realised_movement_5 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4412 | 0.4155 | 0.4974 | ridge | MAE Δ 0.4847 | [0.0009285, 0.001846] |
| target_clock_realised_movement_5 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.4237 | 0.3947 | 0.2919 | ridge | MAE Δ 0.4847 | [0.0009285, 0.001846] |
| target_clock_realised_movement_60 | recent_max_move_900 | CROSS_FAMILY_CANDIDATE | 0.5216 | 0.5518 | 0.5266 | hist_gradient_boosting | MAE Δ 0.2992 | [0.002268, 0.004417] |
| target_clock_realised_movement_60 | realised_vol_5 | CROSS_FAMILY_CANDIDATE | 0.4719 | 0.4737 | 0.624 | hist_gradient_boosting | MAE Δ 0.2992 | [0.002268, 0.004417] |
| target_clock_realised_movement_60 | absolute_return_900 | CROSS_FAMILY_CANDIDATE | 0.4714 | 0.4651 | 0.3842 | hist_gradient_boosting | MAE Δ 0.2992 | [0.002268, 0.004417] |
| target_clock_sign_1 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | 0.2853 | 0.2405 | -0.0002913 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | 0.2547 | 0.2252 | 0.03716 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | momentum_120 | CROSS_FAMILY_CANDIDATE | 0.2355 | 0.1974 | -0.1836 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | logit_return_60 | CROSS_FAMILY_CANDIDATE | 0.2342 | 0.1992 | -0.2135 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | logit_return_30 | CROSS_FAMILY_CANDIDATE | 0.2333 | 0.1908 | -0.2386 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | momentum_300 | CROSS_FAMILY_CANDIDATE | 0.2273 | 0.2024 | -0.1358 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | momentum_15 | CROSS_FAMILY_CANDIDATE | 0.2268 | 0.1771 | -0.2639 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_1 | momentum_5 | CROSS_FAMILY_CANDIDATE | 0.2265 | 0.1672 | -0.2887 | logistic_l2 | accuracy 0.5532 | [0.01165, 0.04519] |
| target_clock_sign_120 | market_minus_event_momentum_30 | WITHIN_FAMILY_STABLE | -0.04074 | -0.08913 | -0.2509 | logistic_l2 | accuracy 0.3873 | [-0.08521, 0.007784] |
| target_clock_sign_120 | loo_event_activity_300 | CROSS_FAMILY_CANDIDATE | -0.03655 | -0.04973 | -0.01524 | logistic_l2 | accuracy 0.3873 | [-0.08521, 0.007784] |
| target_clock_sign_120 | loo_family_activity_30 | CROSS_FAMILY_CANDIDATE | -0.03521 | -0.05175 | -0.01721 | logistic_l2 | accuracy 0.3873 | [-0.08521, 0.007784] |
| target_clock_sign_120 | loo_event_activity_900 | CROSS_FAMILY_CANDIDATE | -0.03447 | -0.04529 | -0.01402 | logistic_l2 | accuracy 0.3873 | [-0.08521, 0.007784] |
| target_clock_sign_120 | loo_event_activity_120 | CROSS_FAMILY_CANDIDATE | -0.03419 | -0.0492 | -0.01621 | logistic_l2 | accuracy 0.3873 | [-0.08521, 0.007784] |
| target_clock_sign_15 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | 0.1579 | 0.0838 | -0.02993 | logistic_l2 | accuracy 0.4751 | [0.001879, 0.0207] |
| target_clock_sign_15 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | 0.1492 | 0.04511 | -0.1062 | logistic_l2 | accuracy 0.4751 | [0.001879, 0.0207] |
| target_clock_sign_15 | momentum_300 | WITHIN_FAMILY_STABLE | 0.1107 | 0.01939 | -0.2199 | logistic_l2 | accuracy 0.4751 | [0.001879, 0.0207] |
| target_clock_sign_15 | momentum_900 | CROSS_FAMILY_CANDIDATE | 0.1088 | 0.0355 | -0.1587 | logistic_l2 | accuracy 0.4751 | [0.001879, 0.0207] |
| target_clock_sign_30 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | 0.1253 | 0.05165 | -0.04515 | logistic_l2 | accuracy 0.449 | [-0.001453, 0.01727] |
| target_clock_sign_300 | market_minus_event_momentum_30 | WITHIN_FAMILY_STABLE | -0.05083 | -0.08908 | -0.2178 | logistic_l2 | accuracy 0.3907 | [-0.05432, 0.05301] |
| target_clock_sign_300 | distance_recent_high_300 | CROSS_FAMILY_CANDIDATE | -0.04507 | -0.1072 | -0.2358 | logistic_l2 | accuracy 0.3907 | [-0.05432, 0.05301] |
| target_clock_sign_300 | loo_family_activity_30 | CROSS_FAMILY_CANDIDATE | -0.0412 | -0.03678 | -0.01737 | logistic_l2 | accuracy 0.3907 | [-0.05432, 0.05301] |
| target_clock_sign_5 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | 0.2114 | 0.1285 | -0.07113 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | 0.2028 | 0.1449 | -0.004416 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | momentum_300 | CROSS_FAMILY_CANDIDATE | 0.1666 | 0.1017 | -0.2003 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | momentum_120 | CROSS_FAMILY_CANDIDATE | 0.1628 | 0.0887 | -0.252 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | momentum_900 | CROSS_FAMILY_CANDIDATE | 0.1597 | 0.1055 | -0.1378 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | logit_return_60 | CROSS_FAMILY_CANDIDATE | 0.1595 | 0.08372 | -0.2828 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | logit_return_30 | CROSS_FAMILY_CANDIDATE | 0.154 | 0.07092 | -0.3068 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_5 | momentum_5 | CROSS_FAMILY_CANDIDATE | 0.148 | 0.05268 | -0.3371 | logistic_l2 | accuracy 0.5014 | [0.007288, 0.03584] |
| target_clock_sign_60 | trend_slope_300 | WITHIN_FAMILY_STABLE | 0.0975 | 0.02407 | -0.05122 | logistic_l2 | accuracy 0.4287 | [-0.002072, 0.01663] |
| target_clock_sign_60 | loo_event_price_movement_5 | CROSS_FAMILY_CANDIDATE | -0.04163 | -0.02377 | -0.007291 | logistic_l2 | accuracy 0.4287 | [-0.002072, 0.01663] |
| target_clock_sign_60 | loo_event_activity_300 | CROSS_FAMILY_CANDIDATE | -0.03566 | -0.04838 | -0.018 | logistic_l2 | accuracy 0.4287 | [-0.002072, 0.01663] |
| target_clock_sign_60 | loo_family_activity_30 | CROSS_FAMILY_CANDIDATE | -0.03559 | -0.05522 | -0.01893 | logistic_l2 | accuracy 0.4287 | [-0.002072, 0.01663] |
| target_event_price_change_10 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | -0.1599 | -0.1623 | -0.1834 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | momentum_120 | CROSS_FAMILY_CANDIDATE | -0.1514 | -0.165 | -0.3043 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.1495 | -0.1669 | -0.3197 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | momentum_300 | CROSS_FAMILY_CANDIDATE | -0.1494 | -0.1624 | -0.2722 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | momentum_900 | CROSS_FAMILY_CANDIDATE | -0.1466 | -0.1561 | -0.226 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | logit_return_30 | CROSS_FAMILY_CANDIDATE | -0.1454 | -0.1626 | -0.3275 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | momentum_15 | CROSS_FAMILY_CANDIDATE | -0.1438 | -0.1572 | -0.3248 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_10 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | -0.1368 | -0.1198 | -0.1151 | elastic_net | MAE Δ 0.004896 | [2.787e-05, 0.0001548] |
| target_event_price_change_5 | momentum_120 | CROSS_FAMILY_CANDIDATE | -0.1264 | -0.1528 | -0.3087 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | momentum_300 | CROSS_FAMILY_CANDIDATE | -0.1264 | -0.1541 | -0.2755 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | -0.1255 | -0.1444 | -0.1849 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.1243 | -0.158 | -0.3256 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | momentum_900 | CROSS_FAMILY_CANDIDATE | -0.1235 | -0.1474 | -0.2284 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | logit_return_30 | CROSS_FAMILY_CANDIDATE | -0.1199 | -0.1492 | -0.3296 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | momentum_15 | CROSS_FAMILY_CANDIDATE | -0.1194 | -0.1432 | -0.324 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_price_change_5 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | -0.11 | -0.1109 | -0.1201 | univariate_linear | MAE Δ 0.0008658 | [-3.374e-05, 0.0001445] |
| target_event_sign_1 | distance_recent_high_300 | CROSS_FAMILY_CANDIDATE | -0.075 | -0.1227 | -0.3021 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | momentum_900 | WITHIN_FAMILY_STABLE | -0.05335 | -0.1129 | -0.2194 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | momentum_300 | WITHIN_FAMILY_STABLE | -0.04898 | -0.1169 | -0.2681 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.04703 | -0.1174 | -0.329 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | momentum_120 | WITHIN_FAMILY_STABLE | -0.04583 | -0.1117 | -0.3015 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | loo_event_price_movement_15 | CROSS_FAMILY_CANDIDATE | -0.04241 | -0.04092 | -0.007515 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_1 | loo_event_price_movement_5 | CROSS_FAMILY_CANDIDATE | -0.04105 | -0.04087 | -0.008196 | logistic_l2 | accuracy 0.5553 | [-0.002992, 0.003997] |
| target_event_sign_10 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | -0.145 | -0.1558 | -0.1833 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | momentum_120 | CROSS_FAMILY_CANDIDATE | -0.1418 | -0.1591 | -0.3052 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.1412 | -0.1636 | -0.3286 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | momentum_300 | CROSS_FAMILY_CANDIDATE | -0.1389 | -0.1586 | -0.2711 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | momentum_900 | CROSS_FAMILY_CANDIDATE | -0.1373 | -0.1531 | -0.2222 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | logit_return_30 | CROSS_FAMILY_CANDIDATE | -0.1354 | -0.1562 | -0.3352 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | momentum_15 | CROSS_FAMILY_CANDIDATE | -0.1334 | -0.1469 | -0.3285 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_10 | trend_slope_300 | CROSS_FAMILY_CANDIDATE | -0.1246 | -0.1184 | -0.1133 | logistic_l2 | accuracy 0.4756 | [0.03961, 0.08954] |
| target_event_sign_2 | distance_recent_high_300 | CROSS_FAMILY_CANDIDATE | -0.08066 | -0.11 | -0.2987 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | momentum_900 | CROSS_FAMILY_CANDIDATE | -0.06194 | -0.1091 | -0.2126 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | momentum_120 | WITHIN_FAMILY_STABLE | -0.06002 | -0.1049 | -0.2976 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | momentum_300 | CROSS_FAMILY_CANDIDATE | -0.05828 | -0.1108 | -0.2619 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.05764 | -0.1082 | -0.3206 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | momentum_15 | WITHIN_FAMILY_STABLE | -0.0496 | -0.08336 | -0.3229 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_2 | logit_return_30 | CROSS_FAMILY_CANDIDATE | -0.04914 | -0.09471 | -0.3264 | logistic_l2 | accuracy 0.5441 | [0.005294, 0.0174] |
| target_event_sign_5 | momentum_120 | CROSS_FAMILY_CANDIDATE | -0.1141 | -0.1433 | -0.3077 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | logit_return_60 | CROSS_FAMILY_CANDIDATE | -0.1136 | -0.1517 | -0.3326 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | momentum_300 | CROSS_FAMILY_CANDIDATE | -0.1135 | -0.1457 | -0.2728 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | momentum_900 | CROSS_FAMILY_CANDIDATE | -0.1115 | -0.1393 | -0.2235 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | logit_return_30 | CROSS_FAMILY_CANDIDATE | -0.1083 | -0.1414 | -0.3352 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | momentum_15 | CROSS_FAMILY_CANDIDATE | -0.1073 | -0.1329 | -0.3257 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |
| target_event_sign_5 | trend_slope_60 | CROSS_FAMILY_CANDIDATE | -0.1071 | -0.1344 | -0.1847 | logistic_l2 | accuracy 0.4972 | [0.02645, 0.06481] |

## Negative controls and baselines

Within-market circular-shift controls were comparable for **47** targets; absolute DEV rank association was lower after the shift for **47** of them. The complete per-target control panel remains in the TRAIN/DEV freeze and is never used for candidate selection.

For model-eligible targets, sealed HOLDOUT compares the DEV-selected model against persistence, own recent price movement, current absolute movement and recent anonymous activity. Regression uncertainty is reported with hierarchical family→market and calendar moving-block resampling; classification uses the same family/market support breakdown and calibrated probability metrics.

## Evidence limitations

- Historical election families are not globally pristine because earlier experiments used portions of the same corpus. 005B therefore treats HOLDOUT as an internal development holdout, not a claim of untouched external replication.
- Economic fills share markets, participants, events and time bursts; raw row count is not interpreted as IID confirmation.
- Anonymous fill activity is intentionally separated from maker/taker and participant identity. Those mechanisms belong to other 005-series experiments.
- 005B is a predictive research atlas. It does not include trading costs, execution policy, position sizing or live SIG order placement.

## Reproducibility

The branch stores the preregistration, feature and target dictionaries, operational amendments, reconstruction evidence, TRAIN/DEV freeze, sealed HOLDOUT result, candidate registry and a run manifest with SHA-256 hashes for the evidence files.
