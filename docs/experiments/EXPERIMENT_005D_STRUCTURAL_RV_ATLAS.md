# EXPERIMENT-005D — Structural / Relative-Value / Coherent Surface Atlas

**Status:** PRE-OUTCOME DESIGN FROZEN  
**Base:** main @ ba938bedcf63f562be8b26c9502e828391123867  
**Branch:** experiment/005d-structural-rv-atlas  
**Empirical namespace:** 005d_structural_rv_atlas

## Research question

Across the economically related prediction-market structures available to the project, which representations of structural inconsistency or relative value contain information about future prices, and which merely reconstruct mechanically related prices?

This experiment is deliberately broader than a single residual mean-reversion test. It searches a frozen atlas of raw, calibrated, coherent, threshold, seat/chamber and sparse-graph representations. It preserves a hard boundary between logical identities, statistical economic relationships and unknown semantics.

## Prior-work boundary and exposure record

The design follows docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md. Before this freeze the researcher inspected methodology, data semantics, MATHS_LEDGER.md, the accepted relationship inventory, the structural family registry and historical topology/data-readiness documentation. No result-ranked 004B/004C tables, best horizons, coefficients or post-hoc candidate rankings were intentionally inspected.

One broad GitHub code-search response incidentally returned a short 004B review excerpt containing aggregate semantic-edge counts and event-specific availability commentary. That excerpt was not requested, was not used to choose models, horizons, targets, splits or screening rules, and is recorded in prior_exposure_manifest.json. The frozen universe below is derived from the permitted maths/semantic inventories and the user-specified 005D brief.

## Data and evidence layers

DATA-001 supplies 2026 as-of BBO state, book trade prints, fills and depth where available for Colombia, Hungary and Peru. It is suitable for probability surfaces and, only when all semantic and book requirements pass, executable hard-structure diagnostics. It is not full event replay: same-millisecond ordering can be ambiguous, archive receive time is not possession time, and queue or cancellation history is incomplete.

DATA-002 supplies the project-derived economic fill/price stream for US_2024, CAN_2025, COL_2026, HUN_2026 and PER_2026. 005D uses price/notional/time only; fee-role evidence belongs to 005A. Economic prints are deduplicated on (family, token_id, tx_hash) after restricting to the YES-side representation. Conflicting duplicate prices fail closed.

U.S. 2024 and Canada 2025 are included wherever their available metadata supports a defensible family. A shared event_id is enough to define a **soft same-event statistical family**, not a hard identity. Cross-event constituent→aggregate or seat→control structure is used only when an explicit audited mapping exists. Missing semantics are classified UNKNOWN, not guessed.

## Structural inventory classes

### HARD / logical

Eligible forms are true complements, implications, mutual exclusion, exhaustive partitions, nested thresholds, exact count/threshold relations and exact joint/intersection relations. Admission requires explicit semantic proof or an already-audited accepted family. A similar title, common event_id, covariance or historical co-movement is never proof.

The YES/NO pair inside one binary market is a hard complement but also a direct equivalent. It may support LOO-PRICE/reconstruction diagnostics and executable consistency checks; it is always removed from LOO-FAMILY.

### SOFT / economic

Eligible forms include same-event candidate/party/chamber groups, audited indirect relationships, race-to-aggregate relationships, correlated regional markets and seat-distribution relationships. They are statistical objects only. Soft constraints never enter a hard-arbitrage certificate.

### UNKNOWN

If payoff semantics, outcome completeness, threshold ordering or settlement compatibility cannot be established, the relationship fails closed. It may remain in the universe report as UNKNOWN, but no structural signal is manufactured from it.

## LOO-PRICE versus LOO-FAMILY

LOO-PRICE removes the literal target quote but may retain equivalent/synthetic/mechanical information. It answers whether the price can be reconstructed and is labelled RECONSTRUCTION_ONLY unless a separate non-mechanical mechanism is proven.

LOO-FAMILY removes the target, direct equivalent, complement, duplicate representation, NegRisk/mechanical sibling and every other member of the target's direct information family. Only explicitly INDIRECT references remain. This is the clean structural-alpha lane.

These two outputs are never pooled.

## Frozen model atlas

### A. Raw relative-value coordinates

For every semantically eligible target/reference set, test probability spread, logit spread, target minus equal-weight family mean, target minus TRAIN-reliability-weighted family mean, target minus leave-target-out aggregate, and target minus leave-target-out common-event factor.

### B. TRAIN-calibrated relationships

Fit using TRAIN only: affine probability mapping, affine logit mapping, ridge logit mapping with lambda in {0.1, 1, 10}, deterministic Huber logit mapping with delta=1.5, and error-correction residual from the frozen TRAIN mapping. Stationarity/AR(1)/half-life diagnostics are descriptive gates. Bounded probability series are not declared cointegrated merely because an equity-style test can be computed.

### C. Coherent probability projections

For verified hard constraint families: equal-weight quadratic projection (M-021), TRAIN-reliability-weighted quadratic projection, Bernoulli-KL projection (M-025), and bid/ask interval coherence where DATA-001 book evidence supports it.

For explicitly soft constraints only, a slack-penalized challenger uses lambda in {0.25, 1, 4}. Soft projection output is never called no-arbitrage fair value.

### D. Threshold/distribution surfaces

Where threshold order is semantically proven: raw monotonicity residual, weighted isotonic repair (M-122), implied PMF, smoothness challenger lambda in {0.1, 1}, entropy challenger lambda in {0.1, 1}, and log-concavity as a diagnostic challenger only. Entropy, smoothness and log-concavity are priors, not hard truths.

### E. Seat/chamber structure

Where target-excluded topology is actually available, consider the independence baseline, one-factor logistic race model (M-137), conditional seat-count simulation (M-138), exact joint-market-implied dependence (M-139), and leave-one-race inverse objective (M-140). If the repository lacks a proven constituent→count/control mapping for a historical family, the model is reported UNAVAILABLE_SEMANTICS rather than back-filled from titles.

### F. Sparse structural graph

The graph lane uses the audited INDIRECT relationship inventory. Hard identities are deterministic factors; soft edges are statistical factors. Target references are ridge-combined after removing the complete direct information family. This is intentionally a sparse structural graph, not the generic reduced-rank panel forecasting owned by 005C.

## Frozen targets and horizons

Clock-time targets are future direct probability change, future direct logit change, future structural residual change, absolute residual shrinkage and family-surface repair direction. Clock horizons are exactly **5, 15, 30, 60, 120 and 300 seconds**.

Event-time targets are exactly the next **1, 2, 5 and 10 unique economic trades** in the target token. DATA-001 book trades or deduplicated DATA-002 (token_id, tx_hash) prints define economic trades. Quote updates are not silently substituted for trades.

## TRAIN → DEV → FREEZE → HOLDOUT

Every structural family × evidence stream is split chronologically by its observed timestamp span: first 60% TRAIN, next 20% DEV, final 20% sealed HOLDOUT. A 300-second purge/embargo surrounds split boundaries, matching the maximum clock horizon. Labels that cross a boundary are removed. No random row split is permitted.

Phase 1 may inspect identity/activity metadata and load source files, but HOLDOUT price/target rows are masked before any feature fitting, scoring, aggregation, logging or output. DEV may select among only the candidates frozen above. The exact shortlist plus all TRAIN-fitted parameters is then committed as PRE_HOLDOUT_FREEZE.json and hash-bound. Phase 2 refuses to run without that hash and inspects the sealed final 20% once. No post-HOLDOUT rescue is allowed.

Cells with fewer than 100 valid TRAIN target observations or 50 DEV observations are UNAVAILABLE; the split is not changed to save them.

## TRAIN census

TRAIN is broad discovery, not confirmation. For every available cell report reconstruction error, residual distribution and tails, AR(1) persistence and implied half-life when meaningful, future response conditional on residual sign/magnitude, quantile response and monotonicity, market/family support, temporal stability, concentration and missingness. No TRAIN statistic is called alpha.

## DEV selection and multiple testing

Inferential cells are grouped for Benjamini-Hochberg FDR by leakage_mode × representation_family × target_kind × horizon_kind with alpha=0.10. Search breadth and unavailable cells remain in the audit ledger.

A predictive candidate may enter the pre-HOLDOUT shortlist only if: its predeclared DEV statistic is finite and points in the repair/FV direction; its BH q <= 0.10; the sign agrees across both DEV halves when each half has at least 25 observations; no one target contributes more than 70% of absolute score; delayed-state/block-shift controls do not reproduce at least 80% of the absolute effect; and a LOO-FAMILY claim still survives complete direct/mechanical-family exclusion.

A reconstruction-only candidate instead requires at least a 5% DEV RMSE improvement over its declared naive reconstruction baseline plus stability/concentration requirements. It is still not called indirect alpha.

Survivor labels are limited to RECONSTRUCTION_ONLY, HARD_COHERENCE_DIAGNOSTIC, SOFT_RV_CANDIDATE, REGIME_SPECIFIC_STRUCTURAL_CANDIDATE and CROSS_FAMILY_STRUCTURAL_CANDIDATE.

## Falsification

The frozen battery includes, as applicable: target-family exclusion; 60-second delayed structural state; unrelated-family matched control; relationship-label permutation; 300-second temporal block shift/permutation; equal versus TRAIN-reliability weighting; leave-one-market-out; leave-one-family-out; remove-dominant-contract; and raw-versus-coherent comparison.

If an apparent LOO-FAMILY signal dies when mechanically related siblings are properly removed, it is reclassified as reconstruction rather than rescued.

## Hard arbitrage is a separate output

Midpoint incoherence is not arbitrage. A hard opportunity requires semantic proof, valid payoff states, executable bid/ask, every required leg, adequate depth, costs, no missing outcome and compatible settlement/void rules.

Where evidence supports it, 005D may apply M-019 depth-aware max-min LP, M-020 bid/ask feasibility and M-136 partition-derived executable intervals. Historical evidence that lacks depth never gets an invented executable certificate.

## HOLDOUT reporting

The HOLDOUT report uses the frozen shortlist only. The primary effect direction must survive, bootstrap uncertainty (1,000 draws; 300-second blocks) must exclude zero for the primary score, confirmatory FDR is alpha=0.10, and negative controls must not explain the effect.

Passing HOLDOUT makes a model eligible for the later central model tournament; it does **not** enable live trading or order submission.

## Ownership boundaries

005D does not absorb 005A fee/taker flow, 005B generic price/fill feature mining, 005C generic reduced-rank panel forecasting, 005E participant ecology or 005F generic book microstructure.

The scientific rule after this file is frozen is simple: broad freedom has already been exercised. Empirical outcomes may remove candidates, but they may not add new ones to this sealed search.
