# LANE-2 — official election-count SHADOW nowcast (issue #204)

**Status: NO VERIFIED FORECAST ADVANTAGE. SHADOW-ONLY.** Isolated research package; no maker/risk/execution changes, no collectors, credentials, deployment or live orders.

## Mandatory historical boundaries

- R3-FV-001 (data/research/r3_fv_001/MASTER_HANDOFF_R3_FV_001.md): static coherence, QP/KL/MaxEnt and error-correction models failed persistence. Favorable Senate one-row DEV result is insufficient. FINAL never opened. Do not promote R3 point FV.
- PRED-007 (docs/experiments/PRED_007_FINAL_REPORT.md): the synthetic election index underperformed own-target, best-source and delayed controls at all 1/2/5/10/30-second horizons. Mechanism closed; do not rescue or retune.
- 005D (docs/experiments/EXPERIMENT_005D_FINAL_REPORT.md): positive relationships 100% confined to LATE_COUNT; Peru has weak longer-block robustness and Colombia has tiny economic effect. Neither establishes executable value.
- Positive Evidence Ledger: no accepted E4/E5 post-cost economics.

This lane is independent official election counts + ballot-composition modelling, NOT prediction-market synthetic indices or R3 structural FV.

## Contract inventory, provisional

The existing canonical SIG/Polymarket crosswalk (data/mappings/sig_polymarket_2026.csv) is not proof of official results mapping or exact SIG settlement rules.

| SIG exchange ID | Contract | Binary engine status |
|---|---|---|
| 840, 841 | Democratic/Republican House control | BLOCKED: not a single binary popular-vote contest |
| 842, 843 | Democratic/Republican Senate control | BLOCKED: not a single binary popular-vote contest |
| 844 | Democratic Alabama Governor | DISCOVERY ONLY: EXACT crosswalk, but original SIG settlement rules, candidates and third-party ballot count must be reviewed |

This is not a complete market inventory. **Approved source-to-contract pairs: ZERO.** The source code requires exact reviewed race/outcome ID, rule hash, approved official source and binary-contest proof before emitting even SHADOW candidates. Test fixture exchange 844 is synthetic, not an endorsement of this contract.

## Official feed discovery (NOT approved active collectors)

- U.S. EAC: https://www.eac.gov/where-do-i-find-election-results
- EAC official count vs canvass/certification: https://www.eac.gov/election-officials/election-results-canvass-and-certification
- Pennsylvania Department of State: https://www.pa.gov/agencies/vote/elections/election-results and https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics/election-data
- Georgia Secretary of State: https://results.sos.ga.gov/
- Wisconsin election officials: https://myvote.wi.gov/en-us/
- NCSL on mail/early reporting: https://www.ncsl.org/elections-and-campaigns/voting-outside-the-polling-place

**Unverified:** 2026 general-election actual feed endpoints, legal permitted polling/reuse, API/licensing costs and ceilings, original per-revision publication timestamps, all outstanding ballots by type and county, raw archives, and official settlement matching. No claim of live feed latency. Election-night returns remain unofficial, including some sources labelled 100% reporting. Media calls must never be treated as certified results.

## Point-in-time model and fail-closed contracts

Input objects: approved OfficialSource; exact reviewed MarketRule; CountObservation containing distinct genuine source publication time, local received time, immutable source reference, source-body SHA256, source revision and unreported ballot inventory; FrozenPrior with pre-event freeze, distinct training election families, frozen cohort-specific beta priors and a predeclared calibration-error allowance.

No inference from precincts reporting. Model remaining MAIL, EARLY, ELECTION_DAY, PROVISIONAL, OVERSEAS (and explicit UNKNOWN if evidenced) by independent geography/method cohort. For up to six strata evaluate every lower/upper outstanding-ballot count corner with beta-binomial simulations plus common shock. Envelope adds Monte Carlo error and declared calibration allowance. This is *model-dependent sensitivity*, not a validated forecast interval or settlement call.

Reject missing inventory/prior, unknown status, fake official HTTPS host, invalid settlement identity, overlapping training family, unverified market, future/naive clocks, unlabeled downward revisions, missing source-document provenance, transport >5 minutes or expired candidate. Revisions become observable only at receive time. Output SHADOW payload contains source_time, receive_time, target_exchange_id, p_interval, confidence, expiry, reason and source_refs plus audit metadata. mode=SHADOW; execution_authority=false.

## Independent validation and economics

The offline evaluator accepts predeclared HOLDOUT event families disjoint from TRAIN. Count receipt, SIG bid/ask as-of and SIG quote receipt must all precede the decision; finalized election outcomes cannot be known at decision. Discard crossed/stale/unreceived quotes with explicit rejection reasons. Report Brier improvement against decision-time SIG midquote, shuffled-control diagnostic and whole-event-family bootstrap CI, alongside source lag p50/p95 and quote age. At least five independent family clusters and 50 valid decisions merely allow a *research* positive label, never live authorization.

Future preregistered checks: chronological train/DEV/held-out family isolation, independent 2024/2025 election episodes, official revision archives, 60/300s delay controls, shuffled event and source timestamps, wrong-contract controls, naive-precinct comparator, calibrated uncertainty and source outages. Actual economic superiority requires observable SIG executable depth, spread, fees, acknowledgement/fill selection, stale/cancellation/slippage risk, and account-correlated exposure. Historical Polymarket fill-only records do not reconstruct complete books.

## Evidence handed to the master orchestrator

| Metric | Measured status |
|---|---|
| Independently validated government-feed / SIG matched episodes | 0 |
| Publication-to-receipt latency | NOT MEASURED |
| Held-out Brier improvement / calibrated uncertainty | NOT MEASURED |
| Net executable EV / post-cost PnL | NOT MEASURED |
| SHADOW synthetic test outcomes | Defensive software validation only |
| Decision | NO VERIFIED FORECAST ADVANTAGE; DO NOT PROMOTE |

Blockers: individually vetted public-source interfaces and terms; signed SIG settlement rule inventory; append-only election-night official sources/receipt log; genuinely event-disjoint prior estimation; matched contemporaneous SIG quotes and conservative executable replay. Do not count code delivery as alpha.
