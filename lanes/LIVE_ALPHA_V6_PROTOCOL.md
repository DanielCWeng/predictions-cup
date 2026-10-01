# LIVE-ALPHA-V6 — Overnight Out-of-Sample Validation Protocol

## Mission

Use the post-freeze SIG + Polymarket tape as a genuinely untouched validation set. V6 is not another broad in-sample alpha search. It first asks whether the four useful live findings from 1 October survive a different time-of-day, more competition, more trades, and materially more markets.

The training freeze is **2026-10-01 17:18:47Z**. Nothing after that timestamp may be used to choose the primary rules before their overnight score is produced.

## Primary hypotheses

1. **PM-anchored maker:** quote 1.5c from PM mid when PM <=1c wide, at least 50 units exist on both PM top levels, SIG is at least 0.5c wider than PM, and both feeds are fresh.
2. **Residual taker:** take only executable EXACT/SAME SIG dislocations >=2c from PM fair value, PM <=2c wide, >=50 top size, one event per market/direction per 60s.
3. **Structural pair:** same-race Democratic + Republican pair discrepancy >=1c against a coherent PM pair, with both PM legs liquid.
4. **Context:** 005I five-minute PM reversal and the PRED-006-inspired SIG repricing hazard remain context/PAPER-only.

## What makes V6 materially harder

Raw fills are never treated as iid. Report market-cluster bootstrap intervals, 5-minute episode de-duplication, leave-one-market-out results, positive-market fraction, max-market share and HHI. A result that depends on one market is concentration, not general alpha.

Every strategy reports both **economic PM markout** and **SIG-executable monetisation**. For buys, executable recycle is the future SIG bid minus entry; for sells it is entry minus the future SIG ask. Report 60s, 300s and 1800s recycle rates and P&L. This directly tests the concern that a PM-relative edge may not show up in SIG account value until later.

Passive maker fills get three views:

- **TOUCH/optimistic:** a trade reaches our quote.
- **PRINT-THROUGH/strict:** a trade prints at least one SIG tick beyond our quote.
- **RECYCLE:** after the hypothetical fill, can inventory be flattened at the SIG touch for non-negative / positive P&L?

If actual PAPER events exist, they are a fourth, preferred evidence surface and must be kept separate from synthetic fills.

## Required diagnostics

For each primary rule: event count, independent 5-minute episodes, markets/races, notional/capacity, mean, median, 10% trimmed mean, market-cluster 95% interval, positive-market fraction, worst leave-one-market-out mean, max market share, HHI, pre-event PM movement, and 1/5/15/30/60/300/1800s markouts.

Slice results by probability bucket, PM spread/depth, SIG spread, residual magnitude, trade size, recent SIG flow, market family, and time-of-day. Slices are explanatory unless pre-frozen; they do not overwrite the primary OOS score.

Run cost stress at 0c, 0.25c, 0.5c and 1c per unit.

## Morning procedure

1. Verify capture health and identify the latest timestamp with complete SIG and PM data.
2. Set evaluation end to at least 30 minutes before that timestamp so 1800s horizons are mature.
3. Build a compact V6 package from the live DB + PM parquet archive. Do not stop capture.
4. Run the frozen primary score locally first.
5. Upload the compact package to Kaggle and run the deeper five-lane V6 battery in parallel.
6. Only after the frozen score is sealed may a discovery pass search new thresholds/regimes.
7. Compare overnight OOS with the 1 October opening-window battery.

## Decision labels

- **SURVIVES_OOS:** all frozen gates pass.
- **PAPER_EXTEND:** economics positive but sample, concentration, queue, or recycle gate fails.
- **CONTEXT_ONLY:** predictive/conditioning value but no direct execution case.
- **FAILS_OOS:** primary economic lower bound <=0, adverse selection appears, or economics vanish under basic costs.
- **QUARANTINE:** semantics/mapping/data-quality concern.

No label is an automatic LIVE approval.
