# LIVE ALPHA BATTERY — PRED-006-Inspired Repricing Hazard

This is not a frozen PRED-006 replication: live fee features are absent and the snapshot is too short for its 600s/1800s targets.
It tests the same economic question at 60s/300s: can observable state improve the hazard of a material SIG repricing versus own-history state?

## Results
|   horizon_s | model   |   train_n |   val_n |   base_brier |   model_brier |   relative_improvement |      auc |   market_positive_fraction |   markets_evaluated |
|------------:|:--------|----------:|--------:|-------------:|--------------:|-----------------------:|---------:|---------------------------:|--------------------:|
|          60 | FULL7   |      2552 |    1444 |    0.0861155 |     0.0840604 |             0.0238653  | 0.69117  |                   0.597122 |                 139 |
|          60 | FULL15  |      2552 |    1444 |    0.0861155 |     0.0857481 |             0.00426723 | 0.681135 |                   0.597122 |                 139 |
|         300 | FULL7   |      2552 |     888 |    0.164677  |     0.154087  |             0.0643047  | 0.700822 |                   0.589928 |                 139 |
|         300 | FULL15  |      2552 |     888 |    0.164677  |     0.156724  |             0.0482914  | 0.710391 |                   0.618705 |                 139 |

## Candidates
|   horizon_s | model   |   train_n |   val_n |   base_brier |   model_brier |   relative_improvement |      auc |   market_positive_fraction |   markets_evaluated |
|------------:|:--------|----------:|--------:|-------------:|--------------:|-----------------------:|---------:|---------------------------:|--------------------:|
|         300 | FULL7   |      2552 |     888 |    0.164677  |     0.154087  |              0.0643047 | 0.700822 |                   0.589928 |                 139 |
|         300 | FULL15  |      2552 |     888 |    0.164677  |     0.156724  |              0.0482914 | 0.710391 |                   0.618705 |                 139 |
|          60 | FULL7   |      2552 |    1444 |    0.0861155 |     0.0840604 |              0.0238653 | 0.69117  |                   0.597122 |                 139 |
