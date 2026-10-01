# EXPERIMENT-005G — Final Economic Failure Forensics

**Analysis type:** `POST_HOC_FAILURE_FORENSICS`  
**Confirmatory evidence:** No.  
**TRAIN:** 2026-09-06  
**DEV:** 2026-09-07  
**Economic HOLDOUT used:** No.  
**Frozen scientific/economic conclusions changed:** No.

## Executive answer

005G was **not nuked because the state signal vanished out of sample**. On DEV, the frozen hazard still ranked the things it was built to predict:

- future BBO update h60 AUC: **0.675**
- future state-transition h300 AUC: **0.740**

The failure happened one layer later. The hazard did **not** rank passive-fill toxicity:

- adverse-fill AUC: **0.456**
- hazard vs signed maker markout Spearman: **+0.346**
- hazard vs absolute markout Spearman: **+0.520**

The highest hazard quintile was actually the most valuable bucket in this DEV proxy: **30 fills, +0.05017 net markout per fill, 86.67% positive markouts**, and the highest fill probability.

So the simple economic translation was backwards for this sample. `WIDTH`, `SIZE`, `WAIT`, and `REFRESH` treated high transition/activity hazard as if it meant “bad to quote.” It did not. Every frozen policy family removed or attenuated far more favourable-fill value than adverse-fill loss it avoided.

**Final classification: `MIXED`, dominated by `STATE_ONLY`, with `CONDITIONAL_CONTEXT` and `PROXY_LIMITED` as secondary qualifications.**

## 1. Economic loss decomposition

The accepted DEV baseline reproduces exactly:

- net 60-second markout P&L proxy: **1.6715**
- filled sides: **100**
- adverse-selection loss: **0.8660**
- turnover proxy: **41.7280**
- max drawdown proxy: **0.2600**
- fee proxy: **0.0000**

No frozen policy beat baseline.

| Family | Frozen candidates | Best net Δ | Bad-fill value avoided range | Good-fill value foregone range |
|---|---:|---:|---:|---:|
| WIDTH_ONLY | 8 | **-0.7800** | 0.1900–0.5550 | 1.0950–1.4935 |
| SIZE_ONLY | 8 | **-0.7500** | 0.1500–0.5063 | 0.9000–1.7051 |
| WAIT_ONLY | 4 | **-1.5000** | 0.3000–0.6750 | 1.8000–2.2735 |
| REFRESH_ONLY | 8 | **-0.7650** | 0.0000–0.3150 | 0.7650–1.6540 |

Across **all 28 frozen candidates**, favourable-fill value foregone exceeded adverse-fill loss avoided.

The complete candidate-by-candidate decomposition is in:

`data/experiments/experiment_005g/FAILURE_FORENSICS.json`

## 2. The cleanest mechanism: WAIT

`WAIT_ONLY` simply removes high-hazard quote opportunities, so it gives the clearest diagnostic.

- **q50:** removed 52 baseline filled sides: **42 favourable, 9 adverse, 1 flat**. Avoided 0.6750 of adverse loss but discarded 2.2735 of favourable markout. Net Δ **-1.5985**.
- **q65:** removed 43 fills: **36 favourable / 7 adverse**. Avoided 0.6650; discarded 2.1995. Net Δ **-1.5345**.
- **q80:** removed 30 fills: **26 favourable / 4 adverse**. Avoided 0.4050; discarded 1.9100. Net Δ **-1.5050**.
- **q90:** removed 19 fills: **17 favourable / 2 adverse**. Avoided 0.3000; discarded 1.8000. Net Δ **-1.5000**.

This is not a marginal threshold miss. In the DEV proxy, the high-hazard region was economically productive.

## 3. Hazard score versus fill economics

Coarse, non-optimised hazard quintiles:

| Hazard | Fills | Fill probability / side | Net markout / fill | Adverse rate | Positive rate | BBO update rate | Transition rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 25 | 0.000215 | +0.00296 | 24.00% | 72.00% | 19.49% | 3.80% |
| Q2 | 19 | 0.000164 | +0.00316 | 5.26% | 94.74% | 28.22% | 20.87% |
| Q3 | 10 | 0.000086 | -0.00220 | 20.00% | 60.00% | 28.61% | 42.88% |
| Q4 | 16 | 0.000138 | +0.00341 | 25.00% | 75.00% | 32.10% | 47.15% |
| Q5 | 30 | **0.000258** | **+0.05017** | 13.33% | **86.67%** | **63.41%** | **54.08%** |

The state targets behave exactly as expected: update/transition frequency rises with hazard.

Fill quality does **not** deteriorate monotonically. Q5 has both the highest fill probability and by far the best conditional markout. Its expected net contribution per quoted side was about **1.30e-05**, versus **6.37e-07** in Q1.

So the high-hazard policies attacked both:

`P(fill | state)`

and

`E(markout | fill, state)`.

That is why net economics collapsed.

## 4. Does 005G predict the wrong thing?

For this economic question, yes.

005G strongly predicts:

- renewal
- BBO update
- state transition
- book activity

It did not establish:

- signed future value
- passive-fill toxicity
- adverse-selection direction

The apparent contradiction between the sealed predictive result and the economic failure is therefore not a contradiction. They are different targets.

## 5. Why WIDTH / SIZE / REFRESH lost

### WIDTH_ONLY

The least-bad width candidate still lost **0.7800**. Widening sometimes improved retained-fill entry economics by **0.025–0.110**, and some width settings reduced drawdown from **0.2600 to 0.1550**, but they removed too much profitable fill flow.

### SIZE_ONLY

Every size candidate reduced adverse exposure, but favourable exposure was larger. Across the family, favourable value sacrificed was roughly **3.31× to 6.00×** the adverse loss avoided.

### REFRESH_ONLY

Shorter quote lifetimes mostly removed fills. The least-bad candidate lost **0.7650**. For 30-second q65/q80/q90, the proxy avoided **zero** adverse-loss value while discarding **0.7650–0.9650** of favourable markout.

Generic “high state hazard → refresh faster” is therefore not an economic translation of 005G.

## 6. Direction: descriptive only

No directional strategy is inferred.

- **ASK:** 58 fills, +1.5475 total net, +0.02668/fill, mean hazard 0.594, adverse rate 15.52%.
- **BID:** 42 fills, +0.1240 total net, +0.00295/fill, mean hazard 0.492, adverse rate 19.05%.

This asymmetry is another warning against using a non-directional activity score as a symmetric standalone quote controller.

## 7. Economic support is sparse and concentrated

The DEV state panel covers **597 markets**, but the passive proxy generated only **100 filled sides across 36 markets**.

Baseline market economics are concentrated:

- top market: **47.7%** of absolute market P&L contribution
- top five: **70.1%**
- absolute-contribution HHI: **0.248**
- the largest positive market contributed **1.2750**, roughly **76% of total baseline net P&L** before losses elsewhere offset it

This supports `PROXY_LIMITED` as a secondary limitation.

It does not explain away the failure: all 28 candidates lost and every family consistently discarded more good-fill value than bad-fill loss avoided.

## 8. `state_dwell_s`

| Dwell quintile | Approx. range | Fills | Net markout / fill | Adverse rate | Future transition rate |
|---|---|---:|---:|---:|---:|
| Q1 | 0–65.9s | 21 | +0.00945 | 14.29% | 73.40% |
| Q2 | 65.9–206.6s | 18 | +0.01272 | 16.67% | 52.01% |
| Q3 | 206.6–1117.3s | 9 | +0.00611 | 22.22% | 33.64% |
| Q4 | 1117.4–9684.5s | 23 | **+0.04522** | **4.35%** | 7.94% |
| Q5 | 9684.5–86096.3s | 29 | +0.00514 | 27.59% | 1.79% |

The transition relationship is clear. The economic relationship is non-monotonic.

The correct interpretation is:

> `state_dwell_s` tells us whether the current book state is likely to end. It does not tell us that the next economically relevant move is adverse to our quote.

## 9. `spread_x_distance`

Among the 100 fills, `spread_x_distance` has Spearman **+0.646** with absolute markout and **+0.522** with signed markout.

But support collapses at larger values: quintile fill counts are **73, 10, 8, 5, 4**. Conditioning on actual spread leaves small and inconsistent cells outside the first spread quintile.

It is best retained as a market-state / unusual-spread context descriptor, not a toxicity signal or standalone maker-EV controller.

## 10. More training data?

More training data is **not ruled out as a future research improvement**, but it is not a credible rescue of 005G's current economic mapping.

The DEV failure is not simply that renewal probabilities were badly estimated: the hazard still ranks renewal/transition well. The economic label is different.

More days on the same targets could improve calibration and reduce variance. They cannot by themselves make:

`P(book changes soon)`

become:

`P(passive fill is adverse)`.

If this idea is revisited in a **new, independent experiment**, the target should be economic from the start: joint fill probability and signed maker markout, with better queue/fee realism. That should not be called another 005G rescue.

## 11. Final classification

### `MIXED` — dominant mechanism: `STATE_ONLY`

**Primary — `STATE_ONLY`:** strong activity/regime prediction; no demonstrated monotonic relationship to fill toxicity.

**Secondary — `CONDITIONAL_CONTEXT`:** useful market-state context, but the four standalone controller mappings were too blunt.

**Secondary limitation — `PROXY_LIMITED`:** only 100 DEV filled sides across 36 markets, concentrated economics, and a zero fee proxy limit resolution.

This is **not primarily `TOXICITY_BUT_NOT_EV`**. The interventions removed some bad fills, but the score did not identify bad fills preferentially. High-hazard fills were mostly favourable.

## 12. Routing

Retain:

- `RETAIN_FOR_SHADOW_CONTEXT`
- `RETAIN_FOR_LIVE_DIAGNOSTICS`
- `ARCHIVE_PREDICTIVE_EVIDENCE`

Do not:

- `DO_NOT_USE_FOR_MAKE`
- do not automatically push 005G into 005H or 005I; it may be cited as optional state-context evidence without inspecting or changing those lanes
- do not start another 005G strategy experiment

## What we should never waste time testing again

1. **Do not rerun “high 005G hazard ⇒ widen / size down / wait / refresh faster.”** All 28 frozen candidates failed, and every family discarded more good-fill value than bad-fill loss avoided.
2. **Do not add more training data to the same renewal/transition targets and call that a toxicity fix.** It may calibrate the state model; it does not change the economic target.
3. **Do not retrospectively hunt thresholds or profitable market subsets on Sep 7 or Sep 8.**
4. **Do not interpret `state_dwell_s` or `spread_x_distance` as signed adverse-selection signals.**
5. **Do not reopen the economic HOLDOUT to rescue a policy.**

The useful lesson from 005G is not “the research failed.” The research found a real state variable and then correctly discovered that **state-transition predictability is not the same thing as maker alpha**.

`005G_STATUS: CLOSED`

`LIVE_MAKE_PROMOTION: NO`

`REAL_SIG_ORDERS_SENT: NO`
