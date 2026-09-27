# EXPERIMENT-004C-C — Polymarket → SIG Cross-Venue Price Discovery

## Status

**PREREGISTERED / SHADOW ONLY.** No live order placement is enabled or permitted by this battery.

Branch: `experiment/004c-c-crossvenue-price-discovery`  
Base: `4e094d13b06d9e558cbc404cc4d472316d7d5bbb`  
Freeze timestamp: `2026-09-27T21:18:49Z`

The scientific design is frozen before predictive outcomes are inspected. `preregistration.json`,
the accepted mapping hashes, and the frozen EXACT/DERIVED universes are the canonical freeze record.

## Starting evidence

The implementation is anchored to the accepted LIVE-MAPPING-GATE-001 registry, BUILD-005
observable-time replay, BUILD-008 evaluation primitives, 004A/004A.2 regime semantics,
EXPERIMENT-002 as-of/leakage rules, EXPERIMENT-003 relationship controls, canonical 004B evidence,
the maths ledger, and the canonical Opus/Astra/Sol 004C research artifacts.

004B is not modified. Its useful implication for this lane is methodological: non-random directed
structure exists in some event/regime aggregates, but that does not establish a stable historical
leader. Cross-venue PM→SIG must therefore beat SIG's own state and freshness rather than inherit a
leader/follower claim from 004B.

## Phase 0 — clock and observability audit

Primary causal ordering uses **our local observation time**, never raw venue/source timestamps.

### SIG

The current full-tournament surface is `price_observations`: authoritative REST bulk-price BBO
sampled locally. The configured/default refresh is 10 seconds. The recorder also stores Realtime
delivery/trade receive times and tracked-book reconciliation/trust transitions, but the current
runtime has no configured tracked full-depth exchanges. `book_observations` therefore cannot be
treated as the full-universe SIG surface.

For the broad BBO:
- venue/source time: not available;
- local observation time: `rest_observed_at`;
- replay ordering time: `rest_observed_at`;
- reconciliation/trust: tracked-depth only; broad scalar BBO is an authoritative REST observation;
- decision time: the PM local observation time that triggers an eligible innovation.

### Polymarket

The recorder separates source timestamps from local observation/sample times:
- WebSocket changes/trades: `source_timestamp` plus local `observed_at`;
- panel observations: source/state timestamp plus local sample `observed_at`;
- depth snapshots: source/state timestamps plus local `recorded_at`.

Primary ordering uses local `observed_at`/`recorded_at`. Source timestamps are diagnostic only.

### Frozen horizon

A one-second full-universe SIG response is **not defensible** because the observable broad SIG BBO
refreshes at a nominal 10-second cadence. Without inspecting predictive performance, the fixed
primary horizon is therefore **30 seconds**. The next observable trusted SIG BBO update is a
separate event-time target. Five seconds is a robustness diagnostic only where a post-decision SIG
update actually exists. There is no one-second promotion claim.

### Existing overlap

A legacy broad PM capture exists from approximately `2026-09-26T12:41:23.636149Z` through
`2026-09-26T14:33:51.923862Z`. SIG scalar BBO observations begin before that, so the period is a
usable retrospective overlap. It is discovery evidence only. True forward evidence begins after
this preregistration freeze.

The currently supervised PM service before this freeze is still the old 3-market / 6-token ARM64
smoke universe and touches only two NEAR chamber-control mappings. It is not eligible EXACT
evidence and must not be mixed into the primary result.

## Frozen mapping families

Primary EXACT: all 140 `VERIFIED` EXACT records in the accepted mapping registry. All are
`mapping_direction=SAME`. NEAR, NO_TRADE and unresolved contracts are excluded.

Secondary DERIVED: all 87 `VERIFIED` reviewed union mappings. Every one is documented as the sum
of mutually exclusive Polymarket Yes margin buckets for the corresponding SIG party-win contract.
No empirical-correlation-derived synthetic contract is allowed.

The frozen universe artifacts are generated deterministically from the accepted registry by
`scripts/experiment_004c_c/freeze_universe.py`.

## Decision construction

An EXACT decision occurs only when an eligible mapped PM top-of-book update changes the aligned PM
midpoint. At decision time `t`, the complete SIG and PM feature state is frozen strictly as-of
`t`. No later PM quote may enter features merely because it precedes the SIG response.

Primary PM innovation is the aligned logit-mid change from the as-of state at `t-5s` to `t`.
Probability-space change is retained as a diagnostic. Tails are guarded only for the logit transform;
raw probability-space prices are not clipped.

## Baseline and challenger

The baseline is SIG-only: BBO/mid/spread, quote age, 30-second signed and absolute self-movement,
30-second update intensity, validity/reconciliation information, and regime where applicable.

The challenger adds contemporaneous PM BBO/mid/spread, 5-second signed and absolute PM innovation,
PM quote age, relative PM-versus-SIG freshness, aligned PM-SIG gap, PM 30-second update intensity,
and as-of liquidity/depth when available.

Both models use identical rows and chronological folds. Numeric normalization is estimated on the
training fold only. The model is deliberately simple: pooled linear ridge with frozen
`alpha=0.001` and mapping fixed effects. There is no contract-specific tuning.

## Targets and metrics

Primary price target: 30-second subsequent SIG midpoint-state change.

Event-time target: change to the next valid observable SIG BBO state after `t`.

Primary predictive estimand: paired out-of-sample absolute-error reduction,
`|e_baseline| - |e_challenger|`, aggregated with equal-contract weighting after explicit coverage
accounting. RMSE and direction accuracy are secondary.

Per-contract findings are secondary. The complete 140-contract EXACT hypothesis family is retained;
contracts without sufficient evaluable observations receive `p=1` rather than disappearing from
the multiplicity family. BH FDR uses alpha 0.05.

## Executable shadow markout

The hypothetical direction is the sign of the challenger forecast. Entry is evaluated after a
frozen 250 ms decision latency against the latest observable SIG BBO, crossing ask for BUY YES and
bid for SELL YES. The primary exit is the first valid SIG BBO state at or after `t+30s`, following
BUILD-005 crossing conventions. A 1-second latency stress is mandatory.

The SIG broad capture does not persist top-level quantity. The primary markout is therefore a
one-share top-of-book price markout with the size limitation disclosed; it may support an
information/economic-price result, but a strong capacity claim requires size evidence. Spread is
paid through bid/ask crossing. No additional SIG fee is currently configured.

The preregistered smallest meaningful mean net markout is 0.005 probability units per share.

## EXACT controls

Mandatory controls:
1. relative freshness is included directly;
2. SIG 30-second self-history is included;
3. PM delayed by 30 seconds;
4. 1,000 deterministic within-block PM circular shifts with absolute shift at least 300 seconds;
5. wrong-contract PM control matched within category on training-only activity/spread/price profile;
6. reverse SIG→PM diagnostic under the symmetric framework;
7. conservative PM timestamp-delay sensitivities of +250 ms and +1 s.

A raw cross-venue gap or contemporaneous correlation is never sufficient.

## DERIVED execution

For every reviewed union, each component must be observable, valid and fresh as of `t`.

At shadow size `q=1`:
- synthetic bid = sum of component executable bids;
- synthetic ask = sum of component executable asks;
- executable size = minimum component executable size;
- a missing/stale/untrusted component fails closed.

Midpoint sums are diagnostic only. EXACT and DERIVED have distinct run IDs, FDR families and
conclusions.

## Validation and promotion

Retrospective overlap uses expanding chronological walk-forward validation: 45-minute training,
15-minute development, 15-minute holdout, 15-minute step, 30-second embargo.

Forward promotion requires at least 40 EXACT contracts, 1,000 valid OOS decisions, and 20 distinct
SIG markets. If this gate is not met, the result is `INCONCLUSIVE`, not a searched rescue.

`EXACT PRICE DISCOVERY SUPPORTED` requires genuine OOS incremental prediction after mandatory
controls and a positive net crossing markout meeting the frozen economic threshold.

`EXACT INFORMATION / NOT EXECUTABLE` is used when forecasting survives but execution/capacity does
not. DERIVED is judged separately. A failed primary is not rescued by alternate horizons, NEAR
mappings or DERIVED results.