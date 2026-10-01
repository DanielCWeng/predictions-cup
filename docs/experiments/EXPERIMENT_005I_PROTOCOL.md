# EXPERIMENT-005I — Regime Dynamics + TradFi Microstructure

Status: INPUT SEMANTICS / PROVENANCE GATE

## Mission
Discover observable market states in the fresh DATA-003-linked Cup order-book corpus, study their transitions, and test which conventional electronic-market microstructure objects transfer to prediction markets. TradFi concepts are questions, not assumed truths.

## Data boundary
Primary dataset: `polyleviathan/sig-cup-data003-orderbooks`.
Supporting data: accepted DATA-003 fills and current SIG↔Polymarket mapping only where needed.
Accepted crosswalk SHA-256 at start: `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`.

Do not substitute DATA-001 or the historical EXPERIMENT-005F corpus. EXPERIMENT-005F is prior evidence only. 005G owns broad current-universe order-book predictive discovery; 005H owns fill × order-book interaction economics. Reuse their objects when available rather than fork them.

## Research order
1. Audit provenance, schemas, timestamp semantics and same-time ordering.
2. Construct observable-time state vector: price/logit price, spread/depth/shape, OFI/flow, activity/age, replenishment/resilience, volatility/stress.
3. Build transparent empirical regimes before latent states.
4. Estimate regime occupancy, durations, transition matrices/hazards, and price-region/tick conditionality.
5. Test simple TradFi challengers: OFI variants, microprice, impact coefficients, flow persistence, queue/event hazards, resilience, droughts, volatility/jumps, LOB shape and common liquidity.
6. Consider HMM/Hawkes only if simpler representations leave stable incremental structure.
7. TRAIN discovers; DEV kills/freezes; HOLDOUT is accessed once after PRE_HOLDOUT_FREEZE.
8. Falsify every major claim with alternate normalisation/windows, dependence-aware resampling and market/event/family leave-outs.

## Deferred axis
Time-to-resolution is economically important but is not the central programme in 005I. Record enough metadata for a later expiration-focused experiment.

## Promotion boundary
005I emits candidate mechanisms and diagnostics only. It may route survivors to 005H, MM-REPLAY, LIVE-DIAG or SHADOW. It does not modify MAKE and cannot send SIG orders.

REAL SIG ORDERS SENT: NO
