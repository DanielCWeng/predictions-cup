# PRED-007 — Event-Triggered Election Index → Held-Out Market Repricing — Final Report

## Disposition

**NO_MACHINE_SPEED_INDEX_SIGNAL**

PRED-007 tested the user's aggregate/“ETF-like” shortcut as a separate historical mechanism lane:

`many related external markets → aggregate/index shock → held-out market`

rather than generic pairwise market-to-market lead/lag.

No 1s, 2s, 5s or 10s horizon passed the frozen gate. The 30s diagnostic also failed.

This lane is closed as specified. No post-result threshold, source-universe, weighting, horizon or model rescue is permitted.

## What was genuinely different from prior work

EXPERIMENT-003 LOWRANK-001 used a 30-second clock grid and TRAIN-only SVD factor. PRED-007 instead used sparse source-update event time:

- a decision timestamp existed only when a cross-event source market genuinely changed;
- source updates were coalesced to one-second bucket ends;
- the target never created its own decision timestamp;
- same-Polymarket-event siblings were excluded, preventing partition reconstruction;
- the target was held out of the source index;
- the aggregate had to beat both target-own history and a fixed outcome-blind single-source comparator.

The primary aggregate was attention weighted using TRAIN-only source BBO-change counts.

## Frozen execution

- Base main: `249e3ec4cebcb2e6495e88f9ab93d0c8ce90326a`
- Launch commit: `0ab156f7569362b310160d09d0bc599618aea971`
- GitHub Actions run: `36635588276`
- Kaggle kernel: `polyleviathan/pred007-event-triggered-index`
- Kaggle status: `COMPLETE`
- Actions artifact: `11064940492`
- Artifact ZIP SHA-256: `bdcd4dd6c9fb8dd8f7c2ef109f0162f92a5b50bbfd557ab95f85da6167eed537`
- Normal CI on the launch commit: PASS

The Kaggle canonical-slug fix was ported before launch, so the workflow followed the actual kernel identity and completed normally.

## Primary results

| Horizon | Shock eval rows | Unique targets | ATTN relative MSE vs OWN | ΔMSE vs OWN | 95% cluster bootstrap vs OWN | ΔMSE vs top source | Gate |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1s | 867,897 | 17 | -0.0287% | -4.08e-08 | [-5.02e-08, -3.28e-08] | -3.60e-08 | FAIL |
| 2s | 867,493 | 17 | -0.0302% | -8.76e-08 | [-1.12e-07, -6.79e-08] | -6.74e-08 | FAIL |
| 5s | 866,816 | 17 | -0.0688% | -4.42e-07 | [-5.72e-07, -2.88e-07] | -2.66e-07 | FAIL |
| 10s | 866,146 | 17 | -0.1695% | -2.03e-06 | [-2.43e-06, -1.69e-06] | -1.13e-06 | FAIL |
| 30s | 864,267 | 17 | -0.1570% | -4.96e-06 | [-6.71e-06, -3.73e-06] | -4.89e-06 | FAIL |

Positive ΔMSE would favour the index. Every pooled point estimate is negative and every bootstrap interval versus target-own history is entirely below zero.

The attention index also loses to the fixed highest-attention single source at every horizon. Its bootstrap interval versus that single-source comparator is entirely negative at 1s, 2s, 5s, 10s and 30s.

The attention index additionally loses to its own 300-second-delayed control at every horizon. The delayed-control comparison is fully negative under the same cluster bootstrap.

This is not a marginal gate miss.

## Event-family breadth

The evaluated families were Colombia and Peru. Their pooled ΔMSE versus OWN was negative at every horizon:

| Horizon | COL_2026 | PER_2026 |
|---:|---:|---:|
| 1s | -9.12e-08 | -2.07e-09 |
| 2s | -1.99e-07 | -1.88e-09 |
| 5s | -1.00e-06 | -1.00e-08 |
| 10s | -4.63e-06 | -2.73e-08 |
| 30s | -1.14e-05 | -2.17e-08 |

There were zero positive evaluated event families at every horizon.

## Coverage and limitations

The frozen universe produced 94 coverage rows and 420 target/horizon cells. Ninety-five cells were evaluable and 325 failed minimum-support rules.

Actual evaluated target/regime pairs were:

- Colombia runoff: 2 targets × 5 horizons;
- Peru first round: 14 targets × 5 horizons;
- Peru runoff: 3 targets × 5 horizons.

That corresponds to 19 target/regime observations per horizon but 17 unique target markets.

Hungary had no target with at least three cross-event sources after the primary same-event-sibling exclusion, so it was not evaluable. Colombia first-round generated source/target constructions but had zero common shock-evaluation rows after the frozen feature/future-quote support rules.

A further limitation is that several source pools had a TRAIN 90th percentile of absolute 1-second aggregate movement equal to zero. The frozen shock rule therefore degenerated to a broad event-time sample for those pools. This threshold was **not** changed after seeing results.

As a bounded post-result demotion diagnostic only, the seven target/regime cells per horizon whose frozen TRAIN threshold was strictly positive were inspected. Across roughly 142k–144k evaluation rows per horizon, the row-weighted attention-index improvement versus OWN was still negative at 1s, 2s, 5s, 10s and 30s, and its average ΔMSE versus the top source was also negative at every horizon. This does not promote any result; it only shows that the zero-threshold limitation does not provide an obvious rescue.

## Interpretation

The tested synthetic election-level attention index does not produce useful near-term price discovery beyond information already present in the target itself or in the single highest-attention cross-event source.

The adverse effect is small in relative MSE terms, but it is directionally consistent across horizons and tightly negative under the frozen event-minute cluster bootstrap. The aggregate appears to add noise rather than sharpen next-repricing forecasts.

This result should **not** be generalized into “all aggregate markets are useless.” PRED-007 tested one frozen synthetic cross-event index construction on historical Polymarket data. It did not test every possible directly traded aggregate contract, nor did it test a future observed aggregate contract directly against live SIG prices.

## Production boundary

No PRED-007 signal should be integrated into MAKE.

There is no future-confirmation candidate from this lane because no historical primary horizon survived.

Any future work on an aggregate→SIG idea must be a genuinely different preregistered hypothesis—for example a specific directly traded aggregate contract with a clear semantic relationship to a SIG target—not a rescue of PRED-007's failed synthetic index.
