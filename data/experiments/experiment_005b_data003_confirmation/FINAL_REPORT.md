# EXPERIMENT-005B → DATA-003 Fresh Confirmation

**Classification:** FRESH_CONFIRMATION_ONLY  
**Dataset:** `polyleviathan/sig-cup-data-003-sig-actual-fills`  
**Frozen carry-forward:** seven clock realised-movement horizons only  
**DATA-003 training/tuning:** none

## Verdict

The historical 005B realised-movement result survived the causal-ordering correction, but it **does not transfer to DATA-003 under the frozen fresh-confirmation test**.

**0 / 7 horizons pass.**

Every horizon fails both because the frozen historical model does not beat all four frozen MAE baselines and because the condition-cluster bootstrap versus persistence is not positive. In fact, every bootstrap 95% interval is entirely below zero.

| Horizon | DATA-003 rows | Conditions | SIG markets | MAE improvement vs persistence | Predictive IC | Bootstrap 95% interval vs persistence | Pass |
|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1s | 28,897 | 642 | 227 | -1.72% | 0.195 | [-0.001916, -0.001239] | No |
| 5s | 33,854 | 654 | 227 | -0.33% | 0.198 | [-0.001783, -0.000848] | No |
| 15s | 37,974 | 664 | 229 | -3.51% | 0.204 | [-0.002954, -0.001998] | No |
| 30s | 39,983 | 664 | 229 | -5.45% | 0.215 | [-0.003594, -0.002516] | No |
| 60s | 42,122 | 676 | 229 | -8.74% | 0.230 | [-0.004243, -0.003066] | No |
| 120s | 44,487 | 676 | 229 | -10.15% | 0.234 | [-0.004619, -0.003374] | No |
| 300s | 48,275 | 676 | 229 | -9.60% | 0.253 | [-0.005774, -0.004092] | No |

The positive predictive ICs show that the historical models still rank some relative movement information, but their absolute forecasts are miscalibrated enough that simple frozen baselines have lower MAE. Under the preregistered rule, that is a failure of transfer.

## Data integrity

The pre-evaluation DATA-003 block gate passed:

- 132,928 source participant rows
- 55,302 transaction-condition groups
- 55,274 accepted groups
- 77,554 reconstructed economic fills
- 43,222 Polygon blocks
- 0 missing block numbers
- 0 missing block timestamps
- 0 timestamp mismatches
- 0 duplicate `(block_number, log_index)` groups
- only 28 groups rejected, all for frozen size-conservation failure

There is no evidence here that the 0/7 result is caused by the ordering defect that affected historical 005B.

## Mapping-class diagnostic

The NEAR mapping class shows positive MAE improvement versus persistence at all seven horizons, but it contains only **four conditions**. The frozen protocol states that mapping-class diagnostics are not selection gates and that mapping-class subsetting is prohibited.

Therefore the NEAR result **cannot rescue this confirmation**. Chasing it would constitute a new hypothesis and would need a separately preregistered experiment with fresh data.

## Final interpretation

- Historical 005B movement result: **not falsified by ordering correction**
- Fresh DATA-003 transfer: **failed 0 / 7**
- Current mapped 2026 SIG universe support: **no**
- New candidates introduced: **0**
- Retuning or mapping-class rescue performed: **no**
- Suitable for live execution as established alpha: **no**

The appropriate conclusion is that 005B uncovered a historically persistent movement-state relationship that is **not portable in calibrated predictive form to the current DATA-003 universe**. The seven historical models should not be carried into live SIG decision-making without a genuinely new hypothesis and fresh validation.
