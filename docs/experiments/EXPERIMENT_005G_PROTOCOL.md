# EXPERIMENT-005G — Fresh DATA-003 Orderbook Microstructure Atlas

Status: INPUT AUDIT FIRST; scientific results not yet opened.

## Mission

Create a new scientific record over the fresh DATA-003-linked current-universe order-book corpus. The original EXPERIMENT-005F remains sealed historical evidence. 005F findings are priors, not constraints on the new discovery space.

The primary question is: what observable order-book state contains robust information about what happens next, and can any of it plausibly matter economically during the SIG Cup?

## Hard data boundary

Primary data: polyleviathan/sig-cup-data003-orderbooks.

Do not substitute DATA-001, Hungary/Peru/Colombia historical tournament data, the old 005F corpus, or unrelated Polymarket samples.

Accepted SIG-to-Polymarket mapping hash at experiment start:
9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2

## Scientific stages

1. INPUT AUDIT
   - inventory every file family, including _manifests and ev18;
   - hash every mounted file;
   - inspect Parquet schemas/row counts and available identity/time columns;
   - resolve dataset/acquisition/source version evidence;
   - fail closed if provenance is ambiguous.
2. SEMANTIC INTERPRETATION
   - determine snapshot/delta/trade/event meaning;
   - classify source/exchange/capture/observed/file time;
   - define same-time ordering and continuity rules.
3. CANONICAL PANELS
   - build reusable observable-time BBO, depth, trade and transition panels;
   - cache to Parquet; do not repeatedly reload the 18 GB corpus.
4. LANE A — STRICT 005F REPLICATION
   - genuine_age_s -> 300s update hazard;
   - genuine_age_s -> 300s jump hazard;
   - trade_abs_impact_60 -> spread/liquidity change;
   - retest important old negatives where exact semantics exist;
   - exact replication or NOT_REPLICABLE_WITH_THIS_DATA, never a quiet approximation.
5. LANE B — OPEN DISCOVERY
   - freshness/renewal, depth geometry, pressure/flow, resilience/replenishment,
     state transitions, interpretable interactions, multi-timescale structure,
     price/liquidity/renewal/toxicity/resilience/regime targets;
   - directional alpha is allowed but must survive stronger adversarial checks.
6. FALSIFICATION
   - time shifts, future-information diagnostic, sign/side placebo where meaningful,
     capture/source controls, activity/freshness restrictions, source-version splits,
     leave-market/event/family-out, horizon shape, strong baseline challenge.
7. TRAIN/DEV FREEZE
   - dependence-aware split; FDR inside major discovery families;
   - freeze only candidates that pass all conjunctive promotion gates.
8. HOLDOUT
   - evaluation only; verify hashes; no new features, horizons, tuning or replacements.
9. HANDOFF
   - replication, new discoveries, strong negatives, economic routing.

## Evidence labels

Replication: FRESH_REPLICATION, REPLICATION_FAILED, REPLICATION_INCONCLUSIVE, NOT_REPLICABLE_WITH_THIS_DATA.

Discovery: DISCOVERY_ONLY until independent confirmation.

Rejections must preserve explicit reasons such as no increment, instability, capture confounding, time-structure failure, low support, leakage, or semantics failure.

## Economic boundary

005G produces candidate mechanisms. MM-REPLAY-001 owns historical maker simulation. LIVE-DIAG owns live economic diagnosis. 005G does not directly modify MAKE and never sends SIG orders.

REAL SIG ORDERS SENT: NO
