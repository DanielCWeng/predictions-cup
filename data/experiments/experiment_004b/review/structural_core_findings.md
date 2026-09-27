# EXPERIMENT-004B-A — Structural/Core Audit

Lane A audits the frozen Kaggle v3 discovery output only. It does not interpret directed lead/lag matrices, null calibration, FDR, or cross-event/cross-regime stability; those belong to 004B-B. No sealed event empirical path was inspected.

## Integrity gate

- Frozen implementation HEAD: `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`
- Frozen discovery-spec SHA: `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`
- Kaggle v3: `COMPLETE`.
- Spec hash matched exactly; `sealed_event_empirical_loads = 0`.
- DATA-001 / 004A / 004A.2 upstream hashes match the frozen manifest.
- Ten published v3 output hashes were recomputed and matched; the external full response matrix also matched its declared SHA and row count. Lane A did not interpret its contents.
- Discovery events are exactly Hungary + Peru first round; regimes are PRE and ACTIVE and are never pooled.
- Empirical universe is exact-`Yes` canonical only; no complementary-token duplication or failed-closed condition was admitted.

### Static panel audit

- Quote scans are half-open `[start,end)`. Panel sampling queries each right edge minus 1 ns and uses the latest `observed_at` not after that query, with the frozen 300 s freshness limit.
- Depth uses full `depth_snapshots` keyed by `recorded_at`, requires a valid full snapshot in the current bin, and does not carry full depth across bins.
- Venue-trade and fill activity are unsigned. The runner does not load `source_side` as aggressor direction.
- The v3 label `ofi_imbalance` is a terminology issue: it is implemented as **full-depth imbalance change**, not order-flow/aggressor imbalance. All findings below use the former meaning.
- The universe exclusion label `*_NOT_ASSESSED_USABLE` should be read only as “frozen regime-specific usable flag was not true”; it is not the special election-day `ELIGIBILITY_NOT_ASSESSED` state.

## Discovery universe and coverage

| Lane | Eligible / represented | Frozen exclusions | 30s midpoint/logit | 30s full depth | Activity evidence |
|---|---:|---:|---:|---:|---|
| HUN PRE | 2 / 2 | 172 | 100.0% | 14.6% | BBO 715,956; fills 13,325; venue trades 0 |
| HUN ACTIVE | 2 / 2 | 172 | 36.4% | 23.6% | BBO 28,787; fills 1,926; venue trades 0 |
| PER R1 PRE | 11 / 11 | 316 | 91.3% | 12.1% | BBO 1,843,562; fills 24,365; venue trades 0 |
| PER R1 ACTIVE | 40 / 40 | 287 | 33.8% | 10.9% | BBO 582,831; fills 16,705; venue trades 0 |

All admitted conditions are represented in the contemporaneous output. Midpoint and logit coverage are identical here because every valid midpoint is strictly inside `(0,1)`; boundary incidence is zero. Count-type activity arrays exist for every bin, but venue-trade count/notional are degenerate zero in all four lanes.

Frozen exclusion counts are: HUN PRE/ACTIVE each 172 exclusions (27 exact-`Yes` canonical failures + 145 not usable in that regime); Peru PRE has 316 (43 canonical failures + 273 not usable); Peru ACTIVE has 287 (43 + 244).

## Contemporaneous structure

### 30-second raw dependence summary

| Lane | Price Δ mean | Logit Δ mean | Spread Δ mean | BBO-update mean | Depth-update mean | Fill-count mean | Price pairs available |
|---|---:|---:|---:|---:|---:|---:|---:|
| HUN PRE | 0.258 | 0.248 | 0.006 | 0.535 | 0.258 | 0.157 | 1/1 |
| HUN ACTIVE | 0.665 | 0.351 | 0.318 | 0.730 | 0.291 | 0.307 | 1/1 |
| PER R1 PRE | 0.023 | 0.022 | 0.025 | 0.229 | 0.292 | 0.203 | 45/55 |
| PER R1 ACTIVE | 0.075 | 0.065 | 0.061 | 0.288 | 0.541 | 0.069 | 103/780 |

- **Hungary:** only two markets are eligible, so every matrix reduces to one pair. Price dependence rises with coarser aggregation (PRE absolute correlation ~0.094 at 1s to ~0.467 at 300s; ACTIVE ~0.175 at 1s to ~0.805 at 60s, with the 300s price cell unavailable). This is descriptive only; two markets cannot establish broad common structure or family clustering.
- **Peru PRE:** price/logit co-movement is weak in aggregate but clearly clustered by market family. At 30s, same-family mean absolute price correlation is ~0.031 versus ~0.009 cross-family; BBO-update ~0.317 vs ~0.064; fill-count ~0.304 vs ~0.013. The same qualitative clustering appears across the resolution grid.
- **Peru ACTIVE:** raw dependence is stronger among the subset with overlap, but coverage is restrictive: at 30s only 103/780 price pairs are estimable and mean pair coverage is ~10.9%. Same-family price correlation (~0.172) exceeds cross-family (~0.022). BBO-update activity also clusters (~0.519 vs ~0.141).
- **Depth-update intensity is different:** Peru ACTIVE depth-update correlations are broad across families (30s same-family ~0.517, cross-family ~0.557). Because full snapshots are captured by a common archive process, this is consistent with synchronized capture/update cadence and must not automatically be interpreted as economic information flow.
- Full-depth imbalance/liquidity dependence is severely coverage-limited at fine resolutions. For example, Peru PRE 30s full-depth cells cover ~12.1% and Peru ACTIVE ~10.9%; only 28/55 and 15/780 depth-imbalance pair cells are estimable respectively.
- Lane A does **not** decide whether these raw synchronous effects are stronger than a calibrated naive-noise null. That comparison is explicitly reserved for 004B-B.

## Structural relationship graph

- Mechanical edges: **0**.
- Semantic/non-mechanical edges: **10**.
- Unverified edges: **827**.
- Hard probability identities: **0**.
- All 10 semantic edges map back to the accepted EXPERIMENT-003 relationship inventory and retain non-empty semantic basis.
- Hungary has no semantic/mechanical edge. Peru PRE has 1 semantic edge; Peru ACTIVE has 9. All others stay `UNVERIFIED`. No identity is inferred from title similarity.

## Structural residuals

All v3 residuals are **semantic price gaps, not hard probability-identity residuals**. There is therefore no mechanical convergence claim.

- Peru PRE has one semantic edge. At 30s it has 7,199/7,200 overlapping bins, absolute level ~0.0210 and volatility ~0.0786. The fixed 2σ diagnostic identifies 331 large residual changes; the mean next-bin change in absolute gap is only +0.000423 (slight widening, not contraction). Across 1/5/30/60/300s the next-bin sign alternates around zero.
- Peru ACTIVE has 9 semantic edges but only 4 are usable at 30s. Across those usable rows, median absolute level is ~0.0136, median volatility ~0.0103, and the fixed 2σ diagnostic produces 78 large changes in total. Median next-bin absolute-gap change is ~+0.00019; individual edges have both widening and contraction. There is no uniform reversion pattern.
- At 30s, PRE absolute-gap association is modest with spread (~0.205), depth (~0.104), and activity (~0.328). ACTIVE usable edges show stronger association with spread (median absolute correlation ~0.699) than depth (~0.162) or activity (~0.133), but only four edges are available.
- **Half-life caveat:** v3 computes residual persistence after dropping missing observations, which can make non-adjacent valid bins adjacent. Lane A therefore does not treat the reported half-life as an exact time constant. This is especially unsafe for sparse ACTIVE edges; it is not a blocker if master simply omits exact half-life claims.

## Latent-factor structure

| Lane | 30s status | Cross-section / effective | Complete bins | Rank-1 EV | Rank-2 cumulative | Effective rank | Interpretation |
|---|---|---:|---:|---:|---:|---:|---|
| HUN PRE | INSUFFICIENT_CROSS_SECTION | 2 / n/a | 0 | n/a | n/a | n/a | unavailable |
| HUN ACTIVE | INSUFFICIENT_CROSS_SECTION | 2 / n/a | 0 | n/a | n/a | n/a | unavailable |
| PER R1 PRE | OK | 11 / 9 | 366 | 0.182 | 0.324 | 8.64 | MULTI_FACTOR_LIKE |
| PER R1 ACTIVE | INSUFFICIENT_COMPLETE_BINS | 40 / n/a | 0 | n/a | n/a | n/a | unavailable |

- **Hungary PRE and ACTIVE:** always `INSUFFICIENT_CROSS_SECTION` because only two markets are eligible. No Hungary factor result is manufactured.
- **Peru PRE:** estimable at all five resolutions. Rank-1 explained variance ranges from ~12.9% to ~19.3%; rank-2 cumulative from ~24.1% to ~35.7%; effective rank remains high (~8.6–9.9). The frozen 70% rule therefore classifies every estimable resolution as `MULTI_FACTOR_LIKE`, not one-factor-like.
- **Peru ACTIVE:** 40 markets are admitted, but there are zero complete all-market logit-change bins at every resolution, so factor analysis fails closed with `INSUFFICIENT_COMPLETE_BINS`. No reduced post-hoc subset is selected.

## Lane-A limitations / integration notes

1. ACTIVE_RESULTS quote coverage is low and pairwise overlap is highly uneven, especially in Peru; raw pair distributions are not a complete 40-market covariance system.
2. Full-depth measures are sparse at fine resolutions and snapshot-update intensity may reflect capture cadence as well as market activity.
3. Venue-trade activity is absent in these discovery panels; only BBO/depth-update/fill activity is informative.
4. `ofi_imbalance` must be described as depth-imbalance change, not aggressor OFI.
5. Exact residual half-life values should be omitted from the integrated report unless a separate gap-aware diagnostic validates panel adjacency.
6. Null-adjusted “stronger than synchronous noise” claims, lead/lag direction, FDR and cross-regime/event stability belong to 004B-B and are intentionally not duplicated here.

**Lane-A disposition:** structural/core evidence is internally consistent and ready for master integration, subject to the terminology and half-life caveats above. No sealed event was opened, no threshold was changed, and no trading/P&L conclusion is made.
