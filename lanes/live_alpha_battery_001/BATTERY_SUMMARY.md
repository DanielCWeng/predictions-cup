# LIVE ALPHA BATTERY 001 — Synthesis

Completed lane results found: **5**.

## maker_queue

Artifact: lanes/live_alpha_battery_001/live-alpha-battery-maker_queue/result.json

Candidate count: **104**.

Top result snapshot:
[
  {
    "concentration": 0.3,
    "depth": 50,
    "dev_n": 44,
    "excess_c": 0.0,
    "offset_c": 1.5,
    "pm_max_c": 1.0,
    "val_ci_lo_c": 1.5000000000000013,
    "val_m300_c": 1.5000000000000013,
    "val_m60_c": 1.5000000000000013,
    "val_markets": 6,
    "val_n": 10,
    "val_pre_move_c": 0.0,
    "val_through_c": 0.19999999999999904
  },
  {
    "concentration": 0.3,
    "depth": 50,
    "dev_n": 31,
    "excess_c": 0.5,
    "offset_c": 1.5,
    "pm_max_c": 1.0,
    "val_ci_lo_c": 1.5000000000000013,
    "val_m300_c": 1.5000000000000013,
    "val_m60_c": 1.5000000000000013,
    "val_markets": 6,
    "val_n": 10,
    "val_pre_move_c": 0.0,
    "val_through_c": 0.19999999999999904
  },
  {
    "concentration": 0.3,
    "depth": 50,
    "dev_n": 31,
    "excess_c": 1.0,
    "offset_c": 1.5,
    "pm_max_c": 1.0,
    "val_ci_lo_c": 1.5000000000000013,
    "val_m300_c": 1.5000000000000013,
    "val_m60_c": 1.5000000000000013,
    "val_markets": 6,
    "val_n": 10,
    "val_pre_move_c": 0.0,
    "val_through_c": 0.19999999999999904
  },
  {
    "concentration": 0.3,
    "depth": 50,
    "dev_n": 44,
    "excess_c": 0.0,
    "offset_c": 1.5,
    "pm_max_c": 2.0,
    "val_ci_lo_c": 1.5000000000000013,
    "val_m300_c": 1.5000000000000013,
    "val_m60_c": 1.5000000000000013,
    "val_markets": 6,
    "val_n": 10,
    "val_pre_move_c": 0.0,
    "val_through_c": 0.19999999999999904
  },
  {
    "concentration": 0.3,
    "depth": 50,
    "dev_n": 31,
    "excess_c": 0.5,
    "offset_c": 1.5,
    "pm_max_c": 2.0,
    "val_ci_lo_c": 1.5000000000000013,
    "val_m300_c": 1.5000000000000013,
    "val_m60_c": 1.5000000000000013,
    "val_markets": 6,
    "val_n": 10,
    "val_pre_move_c": 0.0,
    "val_through_c": 0.19999999999999904
  }
]

## microstructure

Artifact: lanes/live_alpha_battery_001/live-alpha-battery-microstructure/result.json


Top result snapshot:
[
  {
    "ci_lo_c": 9.850817887056317,
    "economic_c": 15.062043795620436,
    "markets": 8,
    "mechanism": "FLOW_CONTEXT_ONLY",
    "n": 137,
    "spec": "prior60_abs>DEV_q0.9",
    "split": "VAL"
  },
  {
    "ci_lo_c": 6.489269281575462,
    "economic_c": 9.538223938223938,
    "markets": 16,
    "mechanism": "FLOW_CONTEXT_ONLY",
    "n": 259,
    "spec": "prior60_abs>DEV_q0.75",
    "split": "VAL"
  },
  {
    "ci_lo_c": 5.1749744554801715,
    "economic_c": 7.348875,
    "markets": 28,
    "mechanism": "FLOW_CONTEXT_ONLY",
    "n": 400,
    "spec": "prior60_abs>DEV_q0.5",
    "split": "VAL"
  },
  {
    "ci_lo_c": 0.1364440990545821,
    "economic_c": 0.2689655172413801,
    "markets": 12,
    "mechanism": "005I_5M_REVERSION_LIVE",
    "n": 54,
    "spec": "abs_pm_ret5>=0.005",
    "split": "VAL"
  },
  {
    "ci_lo_c": 0.0775730311484432,
    "economic_c": 0.19761904761904808,
    "markets": 18,
    "mechanism": "005I_5M_REVERSION_LIVE",
    "n": 88,
    "spec": "abs_pm_ret5>=0.0025",
    "split": "VAL"
  }
]

## pred006_live_hazard

Artifact: lanes/live_alpha_battery_001/live-alpha-battery-hazard/result.json

Candidate models: **3**.

Top result snapshot:
[
  {
    "auc": 0.7008218410484699,
    "base_brier": 0.16467658215825765,
    "horizon_s": 300,
    "market_positive_fraction": 0.5899280575539568,
    "markets_evaluated": 139,
    "model": "FULL7",
    "model_brier": 0.15408709860984254,
    "relative_improvement": 0.06430473240110356,
    "train_n": 2552,
    "val_n": 888
  },
  {
    "auc": 0.710390530149737,
    "base_brier": 0.16467658215825765,
    "horizon_s": 300,
    "market_positive_fraction": 0.6187050359712231,
    "markets_evaluated": 139,
    "model": "FULL15",
    "model_brier": 0.15672412291288612,
    "relative_improvement": 0.04829137902394066,
    "train_n": 2552,
    "val_n": 888
  },
  {
    "auc": 0.6911700262927256,
    "base_brier": 0.08611554731698344,
    "horizon_s": 60,
    "market_positive_fraction": 0.5971223021582733,
    "markets_evaluated": 139,
    "model": "FULL7",
    "model_brier": 0.08406037313216544,
    "relative_improvement": 0.023865309445843655,
    "train_n": 2552,
    "val_n": 1444
  }
]

## residual_taker

Artifact: lanes/live_alpha_battery_001/live-alpha-battery-residual_taker/result.json

Candidate count: **100**.

Top result snapshot:
[
  {
    "concentration": 0.36363636363636365,
    "cooldown_s": 60,
    "depth": 50,
    "dev_n": 57,
    "pm_max_c": 2.0,
    "threshold_c": 2.0,
    "val_ci_lo_c": 2.415061393420486,
    "val_entry_c": 2.553030303030303,
    "val_m300_c": 2.5500000000000007,
    "val_m60_c": 2.5440000000000005,
    "val_markets": 7,
    "val_n": 33
  },
  {
    "concentration": 0.5714285714285714,
    "cooldown_s": 60,
    "depth": 100,
    "dev_n": 37,
    "pm_max_c": 2.0,
    "threshold_c": 2.0,
    "val_ci_lo_c": 2.4006054083093287,
    "val_entry_c": 2.61904761904762,
    "val_m300_c": 2.5833333333333335,
    "val_m60_c": 2.5882352941176476,
    "val_markets": 6,
    "val_n": 21
  },
  {
    "concentration": 0.4,
    "cooldown_s": 60,
    "depth": 50,
    "dev_n": 49,
    "pm_max_c": 1.0,
    "threshold_c": 2.0,
    "val_ci_lo_c": 2.3876068183805885,
    "val_entry_c": 2.5250000000000004,
    "val_m300_c": 2.484615384615385,
    "val_m60_c": 2.5045454545454553,
    "val_markets": 5,
    "val_n": 30
  },
  {
    "concentration": 0.25,
    "cooldown_s": 300,
    "depth": 50,
    "dev_n": 16,
    "pm_max_c": 2.0,
    "threshold_c": 2.0,
    "val_ci_lo_c": 2.217665830826862,
    "val_entry_c": 2.5875000000000004,
    "val_m300_c": 2.6400000000000006,
    "val_m60_c": 2.5500000000000007,
    "val_markets": 7,
    "val_n": 12
  },
  {
    "concentration": 0.5714285714285714,
    "cooldown_s": 60,
    "depth": 100,
    "dev_n": 28,
    "pm_max_c": 0.5,
    "threshold_c": 1.5,
    "val_ci_lo_c": 2.192,
    "val_entry_c": 2.3642857142857148,
    "val_m300_c": 2.5250000000000004,
    "val_m60_c": 2.4125,
    "val_markets": 3,
    "val_n": 21
  }
]

## structural_rv

Artifact: lanes/live_alpha_battery_001/live-alpha-battery-structural_rv/result.json

Candidate count: **24**.

Top result snapshot:
[
  {
    "cooldown_s": 60,
    "depth": 50,
    "dev_n": 24,
    "pm_max_c": 1.0,
    "threshold_c": 1.0,
    "val_ci_lo_c": 0.9826048529136554,
    "val_edge_c": 1.1562499999999996,
    "val_m300_c": 1.299999999999999,
    "val_m60_c": 1.1818181818181819,
    "val_n": 16,
    "val_races": 4
  },
  {
    "cooldown_s": 60,
    "depth": 100,
    "dev_n": 24,
    "pm_max_c": 1.0,
    "threshold_c": 1.0,
    "val_ci_lo_c": 0.9826048529136554,
    "val_edge_c": 1.1562499999999996,
    "val_m300_c": 1.299999999999999,
    "val_m60_c": 1.1818181818181819,
    "val_n": 16,
    "val_races": 4
  },
  {
    "cooldown_s": 60,
    "depth": 200,
    "dev_n": 22,
    "pm_max_c": 1.0,
    "threshold_c": 1.0,
    "val_ci_lo_c": 0.9826048529136554,
    "val_edge_c": 1.1562499999999996,
    "val_m300_c": 1.299999999999999,
    "val_m60_c": 1.1818181818181819,
    "val_n": 16,
    "val_races": 4
  },
  {
    "cooldown_s": 60,
    "depth": 50,
    "dev_n": 25,
    "pm_max_c": 2.0,
    "threshold_c": 1.0,
    "val_ci_lo_c": 0.9826048529136554,
    "val_edge_c": 1.1562499999999996,
    "val_m300_c": 1.299999999999999,
    "val_m60_c": 1.1818181818181819,
    "val_n": 16,
    "val_races": 4
  },
  {
    "cooldown_s": 60,
    "depth": 100,
    "dev_n": 25,
    "pm_max_c": 2.0,
    "threshold_c": 1.0,
    "val_ci_lo_c": 0.9826048529136554,
    "val_edge_c": 1.1562499999999996,
    "val_m300_c": 1.299999999999999,
    "val_m60_c": 1.1818181818181819,
    "val_n": 16,
    "val_races": 4
  }
]

