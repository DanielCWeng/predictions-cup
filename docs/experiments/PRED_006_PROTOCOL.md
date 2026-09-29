# PRED-006 — DATA-003 Hail Mary Predictive Discovery Protocol

**Status:** PHASE 0 FROZEN BEFORE PREDICTIVE SEARCH  
**Branch:** `experiment/pred-006-data003-hail-mary`  
**Parent main:** `71835eb9d6312234eacdce357b5c2be0aeedda28`  
**Dataset:** `polyleviathan/sig-cup-data-003-sig-actual-fills`

## Mission

Search DATA-003 independently from first principles for a tiny set of reproducible current-universe predictive components, while keeping one chronological final slice untouched until a frozen shortlist exists. A terminal null is an acceptable and scientifically complete outcome.

## Independence boundary

Permitted reuse: DATA-003 loaders, accepted SIG↔Polymarket mapping, Polygon chronology reconstruction, deterministic replay/evaluation utilities, bootstrap utilities and Kaggle infrastructure.

Not permitted as search inputs: 005A–F hypothesis menus, winner tables, selected horizons, selected models, selected feature lists or conclusions. Any resemblance discovered independently is documented only after candidate definition.

The ETS/fair-value graph and full 005F/order-book transfer are out of scope.

## Canonical observable chronology

Economic fills come from the accepted DATA-003 reconstruction used by the 005B block-number hard gate. Order is strictly:

1. `block_number`
2. `log_index`

`tx_hash` is allowed only as an impossible-tie guard after the observable Polygon ordering keys. Timestamp/tx-hash pseudo-ordering is forbidden.

The Phase-0 kernel must hard-fail if block coverage, block timestamps or uniqueness of `(block_number, log_index)` do not match the accepted gate.

## Split-selection rule — frozen before outcomes

No predictive target or model metric may be computed by Phase 0.

Using the canonical economic-fill chronology only:

1. Sort all accepted economic fills by `block_number, log_index`.
2. Identify the row at floor(82% × N).
3. Let the **FINAL start** be the first UTC midnight strictly after that row's timestamp.
4. All observations at or after FINAL start belong to FINAL.
5. From the remaining pre-final chronology, identify the row at floor(65% × N_pre_final).
6. Let the **DEV start** be the first UTC midnight strictly after that row's timestamp.
7. TRAIN is before DEV start; DEV is from DEV start to FINAL start.
8. A 1,800-second purge applies before DEV start and before FINAL start for any target-bearing observation.
9. Search kernels must hard-fail if any row with timestamp >= FINAL start enters feature invention, target selection, model fitting, hyperparameter selection, participant selection, market selection, threshold selection or DEV scoring.

The 82/18 choice targets an approximately 18% contiguous final slice without inspecting predictive results. Midnight snapping prevents boundary ambiguity and makes replay deterministic. Markets absent from FINAL remain absent; the split is not changed to rescue coverage.

## Search target atlas

TRAIN/DEV may explore a bounded atlas tied to executable interfaces:

- future canonical YES-price change over clock horizons in {5, 30, 120, 600, 1800}s;
- future absolute movement over the same horizons;
- direction conditional on a material future move;
- next economic price-change magnitude/direction;
- time-to-next price change / movement hazard.

The final shortlist may use only target/horizon combinations selected on TRAIN/DEV before FINAL is opened.

## Baselines

Every promoted regression candidate must beat persistence/current-price or an appropriate recent-own-history baseline on DEV. Direction classifiers must beat the corresponding base-rate/history baseline. Hazard models must beat a constant/base-rate hazard. Pretty IC without incremental forecast improvement is not sufficient.

## Dependence

No random row split. Evaluation must acknowledge clustering by condition/market, block/time burst and event/related-market group where available. Uncertainty must use condition-cluster or stronger dependence-aware bootstrap. Same-block fabricated chronology is forbidden.

## Multiple testing discipline

Search breadth is recorded by family, target, horizon, model and transformation. Dead ideas are retained. DEV winners are assumed upward biased and must survive ablation, placebo/permutation controls, time/market stability and concentration checks before shortlist freeze.

## Candidate families

The independent programme may explore, without presuming validity:

- temporal/state dynamics;
- participant/flow dynamics using past-observable state only;
- cross-market information propagation and common/residual movement using the accepted mapped universe;
- cross-sectional/low-rank state;
- controlled interactions between weak primitives and market state.

## Shortlist and one-shot FINAL

Stop search at 0–5 candidates. For every candidate freeze feature definitions, source fields, transforms, lookbacks, target, horizon, model, hyperparameters, missingness, market/participant handling, thresholds, metric, baseline and pass/fail rule.

Only after that freeze is committed may the FINAL kernel read FINAL predictive outcomes. It is opened once. No post-final rescue, retuning, subsetting or relabelling is allowed.

## Final interpretation

A survivor is labelled `CURRENT_UNIVERSE_CANDIDATE_REQUIRES_FUTURE_CONFIRMATION`. DATA-003 is then discovery/selection data and cannot establish live alpha. Any survivor must be confirmed on genuinely future data, expected from CAPTURE-001.

If none survives, disposition is `TERMINAL_NULL`.

## Compute

Canonical path only:

`branch -> kaggle/jobs/*.json -> GitHub Actions -> Kaggle -> artifacts/results -> GitHub`

New compute uses `"action": "run"`. EC2 is not used for PRED-006 research.
