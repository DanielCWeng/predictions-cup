# FV-LIVE-001

This is an offline research replay. It is not imported by runtime model
registration and cannot place orders.

`run_live.py` reads a copied read-only SQLite backup, verified EXACT/NEAR
mapping rows, and copied Polymarket observation partitions. The fitting cutoff
is 2026-10-01 16:00Z. Calibration uses only PM samples whose five-minute
forward endpoint also exists in the pre-live-only extract. The 16:00Z onward
window is held out for forecasts and replay metrics.

The mean-reversion input is the frozen 005I five-minute return. Its price
conversion coefficient was not frozen in the artifacts found, so the return
and the frozen 005I OFI model score are jointly mapped to five-minute PM return
using pre-live least squares only. The 005I logistic coefficients remain
unchanged. Because the minute panel does not include raw quote-event count,
depth5, or top-level size imbalance, the score uses the frozen median values
for those inputs. The OFI signal is the one-minute BBO-price-change proxy; it
does not reconstruct full depth.

The pull gate is a hypothesis: withdraw when the mapped PM BBO has remained
unchanged for at least the frozen 005I stale threshold (210 seconds). The
available extracts do not contain fitted PRED-006/005F coefficients or an
exact 005G state-dwell input, and that boundary is recorded in the result.

The inventory skew grid is declared in `model.py`:
0, 0.5, 1, and 2 ticks per 500 lots. Quotes use one-cent half width and tick
rounding. A SIG realtime trade at or through a passive quote is treated as a
full fill with queue priority. Markouts use side-aware PM mids at 60 and 300
seconds. Gross PnL is marked to the final PM observation and excludes fees,
rebates, latency, cancellations, capital costs, and settlement.

Example local invocation:

```bash
.venv/bin/python -m predictions_cup.research.fv_live.run_live \
  --data-root /home/ubuntu/codex_lane/fv-live-001 \
  --mapping data/mappings/sig_polymarket_2026.csv \
  --output /home/ubuntu/codex_lane/fv-live-001/fv_live_results.json
```
