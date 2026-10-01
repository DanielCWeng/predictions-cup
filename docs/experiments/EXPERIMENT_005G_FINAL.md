# EXPERIMENT-005G Final Report

## Decision

EXPERIMENT-005G is complete through a frozen, one-shot sealed holdout.

Twelve candidates were frozen before any holdout read. The sealed holdout evaluated all twelve without candidate replacement or post-holdout tuning.

- **1/1 strict replication candidate replicated**
- **9/11 discovery candidates were independently supported**
- **2/11 discovery candidates were not confirmed**
- **10/12 frozen candidates passed the complete holdout gate**

The central result is evidence for **state-dependent renewal and transition hazard** in the fresh DATA-003 Polymarket orderbook corpus. This is predictive evidence about orderbook state evolution. It is **not** evidence of equivalent trading P&L, Sharpe, expected return, or executable alpha.

No MAKE policy was modified and no real SIG orders were sent.

## Scientific hygiene

The pre-holdout candidate set was frozen at commit `a9219762fb49faa7c98fd8f7a3c7a888a5352243`.

Immutable freeze SHA-256:

`887592885c02ae9219232c40a8b9ce0b263f9a62e04da62c524b16162895cc1c`

The sealed holdout was launched from commit `44a7cfc0120d778676a38e68a4863029078641b8` and read the fixed window:

- 2026-09-05T00:00:00Z
- through 2026-09-06T00:00:00Z

GitHub Actions workflow run: `36843408540`  
Job: `110307699563`  
Artifact: `11153497494` / `kaggle-run-0-1`

Artifact SHA-256:

`2c5bb46ebcf5b8d79e7368afdb309c849e55992ad44a771ab849740a219ef4d0`

The exact raw artifact is preserved at:

`data/experiments/experiment_005g/holdout/kaggle-run-0-1.zip`

Inside it, the two principal evidence files hash to:

- `HOLDOUT_RESULTS.json`: `c0d567ace0dd9f1f801add05f69e673b9abb2689e4c9527650be49797587233f`
- `HOLDOUT_AUDIT.json`: `bb93acbdbf78cbf9e4072a7c633db8c73b04a8e4736fd3d1b7badbbecd65cb33`

The holdout audit records `selection_or_tuning_after_holdout=false`, `make_modified=false`, and `real_sig_orders_sent=false`.

## Holdout results

| Candidate | Target | Feature | Relative MSE improvement | Positive blocks | Markets | Disposition |
|---|---|---:|---:|---:|---:|---|
| 005F_REPL_PRE_UPDATE | update_h300 | genuine_age_s | **+26.5024%** | 48/48 | 658 | FRESH_REPLICATION |
| 005G_DISCOVERY_SEQ h300 | state_transition_h300 | state_dwell_s | **+40.2969%** | 48/48 | 295 | SUPPORTED |
| 005G_DISCOVERY_SEQ h60 | state_transition_h60 | state_dwell_s | **+20.7621%** | 48/48 | 295 | SUPPORTED |
| 005G_DISCOVERY bbo h60 | bbo_update_h60 | spread_x_distance | **+4.9550%** | 44/48 | 692 | SUPPORTED |
| 005G_DISCOVERY bbo h300 | bbo_update_h300 | spread_x_distance | **+1.7933%** | 38/48 | 692 | SUPPORTED |
| 005G_DISCOVERY bbo h60 | bbo_update_h60 | distance_from_0_5 | **+1.2378%** | 48/48 | 692 | SUPPORTED |
| 005G_DISCOVERY bbo h300 | bbo_update_h300 | volatility_x_liquidity | **+0.7058%** | 48/48 | 692 | SUPPORTED |
| 005G_DISCOVERY volatility h60 | abs_h60 | spread_x_distance | **+0.1929%** | 36/48 | 692 | SUPPORTED |
| 005G_DISCOVERY bbo h300 | bbo_update_h300 | price_change_age_s | **+0.1477%** | 41/48 | 658 | SUPPORTED |
| 005G_DISCOVERY bbo h60 | bbo_update_h60 | price_change_age_s | **+0.0810%** | 45/48 | 658 | SUPPORTED |
| 005G_DISCOVERY volatility h60 | abs_h60 | distance_from_0_5 | +0.0085% | 31/48 | 692 | NOT CONFIRMED |
| 005G_DISCOVERY bbo h60 | bbo_update_h60 | ofi_acceleration | **-0.0351%** | 16/48 | 692 | NOT CONFIRMED |

The supported candidates passed the frozen sign-flip, block-bootstrap, leave-market-out and, where applicable, within-family BH-FDR gates defined before the holdout read.

## What survived

### 1. State dwell is a strong transition-hazard variable

`state_dwell_s` was the strongest discovery family in the sealed holdout.

- 300-second state transition: +40.30% relative MSE improvement
- 60-second state transition: +20.76%
- 48/48 positive time blocks at both horizons
- positive block-bootstrap lower bounds
- positive leave-market-out minima

The larger effect at 300 seconds than 60 seconds is consistent with a slowly evolving regime/state-duration effect rather than a precise next-tick trigger.

### 2. The strict renewal/freshness replication survived

The frozen 005F PRE_UPDATE replication using `genuine_age_s` produced +26.50% relative MSE improvement on the untouched holdout, with 48/48 positive blocks across 658 markets.

This materially strengthens the evidence that orderbook freshness/age contains stable information about future renewal hazard.

### 3. Spread × distance is a reusable state descriptor

`spread_x_distance` independently survived for:

- 60-second BBO renewal: +4.96%
- 300-second BBO renewal: +1.79%
- 60-second absolute movement: +0.19%

The TRAIN/DEV falsification work also showed persistence under delay/shift variants. The cleanest interpretation is therefore a persistent market-state/regime descriptor, not an instantaneous causal trigger.

### 4. Smaller renewal variables survived

`distance_from_0_5`, `volatility_x_liquidity`, and `price_change_age_s` all supplied smaller but independently supported renewal information.

They are reasonable context variables for a state-hazard model. Their effect sizes do not justify standalone economic claims.

## What did not survive

`ofi_acceleration -> bbo_update_h60` failed the sealed holdout with a negative relative improvement and 16/48 positive blocks. It should not be promoted from 005G.

`distance_from_0_5 -> abs_h60` did not pass the complete frozen gate. The estimated effect was tiny, its block-bootstrap lower bound was negative, and its leave-market-out minimum was negative.

Earlier Lane A work also established:

- PRE_LIQUIDITY failed its pre-holdout gate.
- ACTIVE_JUMP was not exactly replicable because the frozen TRAIN threshold collapsed to zero.
- ACTIVE_UPDATE was directionally interesting but dependence-underpowered and remains inconclusive.
- ACTIVE_PRICE_NEGATIVE reproduced the negative direction and was not promoted.
- sequential resilience and trade-pressure/toxicity families were support-limited by sparse relevant observations rather than established economic negatives.

No failed candidate is rescued or replaced after seeing the holdout.

## Data and inference limits

The fresh corpus is large, but this experiment has important boundaries.

The holdout is one 24-hour period in September across the current mapped Polymarket universe. Many markets contribute repeated BBO states, observations are not independent iid samples, and trade events are sparse relative to book-state events.

The V3 evidence is based on received-time observability and respects continuity barriers. It does not establish a universal exchange sequence.

Most importantly, **relative predictive-loss improvement is not trading return**. A model that predicts renewal or state transition well can still fail to improve realized execution after latency, queue position, fees, cancellations, fill selection and inventory effects.

The experiment therefore supports market-state information, not a direct directional fair-value claim.

## Recommended routing

Freeze one candidate feature bundle for economic conversion testing:

`005G_STATE_HAZARD_V1`

Include:

- `state_dwell_s`
- `genuine_age_s`
- `spread_x_distance`
- `distance_from_0_5`
- `volatility_x_liquidity`
- `price_change_age_s`

Explicitly exclude `ofi_acceleration` from this frozen bundle.

Route the bundle through SHADOW / replay first. Test whether predicted renewal and transition risk improves:

- quote width and aggressiveness
- refresh/cancel timing
- quote size
- join-versus-wait decisions
- adverse-selection avoidance
- confidence/gating around other fair-value estimates

Score economic outputs rather than another predictive metric: fills, markouts, adverse selection, fees, turnover, inventory, drawdown and net P&L.

Do **not** modify MAKE directly from 005G. Promotion should require positive replay/shadow economics under the existing execution and risk boundary.

## Bottom line

005G produced a credible transferred result: orderbook age, state dwell and related structural state variables contain robust information about future renewal and regime transitions in the fresh DATA-003 corpus.

The next scientific question is no longer whether these features predict orderbook state. It is whether that information can be converted into better execution economics.
