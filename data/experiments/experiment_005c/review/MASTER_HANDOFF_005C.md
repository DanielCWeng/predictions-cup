# EXPERIMENT-005C — MASTER HANDOFF

**Final reviewed disposition:** **DOWNGRADE — FAMILYWISE NULL NOT REJECTED**

**Programme verdict:** 005C is a useful **discovery result**, but its strongest US model is **not a validated central alpha candidate** and should not be promoted into EXPERIMENT-006. It may remain as a shadow/research comparator.

Frozen design: `ea7ed06dd47cb8240cc8a1e5dedabf00d009843f`  
Original preregistration SHA-256: `b6505f8b07dd064cb1e4133a660c5d1f43864823b696539aa60e474eb9b8b6f5`  
Empirical implementation: `94512d3c9b785efcbf04bcabc5c57c416e5ad524`  
Pre-HOLDOUT freeze SHA-256: `20f977ffb64a56ecfe230ba45d9d0021dcc97e5218b13eb307db896d14c87933`  
Frozen DEV selections / HOLDOUT evaluations: `35 / 35`

Post-HOLDOUT review preregistration SHA-256: `17fa57eed1efdabaad1f9ed51a49a30c8eba62758795e009506142fb0ad0fc39`  
Review classification: `POST_HOC_INDEPENDENT_REVIEW_FALSIFICATION`

## Reconstruction

- US_2024: 4,468,573 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- CAN_2025: 297,906 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- COL_2026: 239,771 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- HUN_2026: 260,802 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.
- PER_2026: 346,114 economic condition-second observations; valid passive-notional fraction 1.000000; invalid role-structure groups 0.

## Original frozen headline

The strongest original HOLDOUT row is M2 on `US_2024_event_6e91434eb80f` at grid 15s / horizon 15s.

Against the frozen baselines on HOLDOUT:

- challenger MSE: `0.0391474332`
- B1 target-own-history MSE: `0.0400373881`
- B2 own-history + leave-target-out common-state MSE: `0.0418781974`
- pooled improvement vs B1: **+2.22%**
- pooled improvement vs B2: **+6.52%**
- median per-target improvement vs B1: **+4.48%**
- fraction targets helped vs B1: **75%**

So the B1 falsifier does **not** kill the result.

## Decisive post-HOLDOUT falsification

A preregistered correlation-preserving 35-cell synchronized absolute-UTC-hour cluster-wild max-t analysis was run across the complete already-exposed HOLDOUT family.

For the primary 15s/15s US cell:

- observed UTC-hour cluster t: **1.4696**
- unadjusted one-sided p: **0.0173**
- familywise max-t adjusted p: **0.8918**
- effective test count by bootstrap-correlation participation ratio: **20.52**

The result therefore **does not survive the 35-cell familywise challenge**.

This is the formal downgrade trigger. It cannot be rescued by another exposed HOLDOUT cell.

## Dependence sensitivity

For the primary 15s/15s cell:

- 5-minute moving block: 95% CI **[0.0000736, 0.0082054]**
- 15-minute moving block: 95% CI **[-0.0002491, 0.0050484]**
- 30/60/120-minute settings contain too few effective blocks for stable inference.

The result is therefore also **dependence-sensitive** under a more conservative 15-minute block assumption.

## Exact event semantics

Canonical metadata exposes no populated event title for event ID `10656`.

Economically, the five-market panel is the 2024 US joint outcome partition over **popular-vote winning party × Presidency winning party**, plus a third-party outcome:

- 3rd party candidate wins the popular vote or Presidency;
- Republican popular vote + Democrat Presidency;
- Democrat popular vote + Democrat Presidency;
- Democrat popular vote + Republican Presidency;
- Republican popular vote + Republican Presidency.

These are direct economic relatives.

For the primary 15-second grid:

- TRAIN: 2024-05-16 02:09 UTC → 2024-10-23 15:33 UTC
- DEV: 2024-10-23 16:59 UTC → 2024-11-04 12:18 UTC
- HOLDOUT: 2024-11-04 12:48 UTC → 2024-11-06 08:07 UTC

The HOLDOUT therefore spans **election eve, Election Day and election night / early post-midnight**, not a generic weeks-before-election regime.

## Temporal concentration

For the primary cell:

- active UTC-hour blocks: 34
- positive blocks: 21
- top 1 hour share of positive advantage: **63.3%**
- top 3 hours: **92.8%**
- top 5 hours: **99.3%**
- removing the strongest positive hour still leaves +2.51% pooled improvement vs B2
- removing the strongest three still leaves +1.32%

The gain is real enough to be distributed beyond one hour, but it is heavily concentrated around November 6 UTC.

## Interpretation

Frozen primary metrics:

- cross-sectional IC: **-0.022**
- median per-target time-series IC: **+0.203**
- sign accuracy: **57.7%**

The evidence is therefore primarily **per-market time-series forecasting through time**, not contemporaneous cross-sectional ranking.

Own-history remains necessary. The TRAIN coefficient matrix also uses several directly related joint-outcome markets, but the signed Ridge coefficients are descriptive conditional relationships, not causal effects or literal arbitrage links.

## Promotion rule

Do **not** promote the 15s/15s model into EXPERIMENT-006 as a central model.

Allowed use:

- shadow comparator;
- research comparator;
- falsified benchmark;
- motivation for a genuinely untouched future analogue.

Do not claim:

- a universal or cross-family joint factor;
- validated standalone alpha;
- cross-sectional ranking skill;
- independence from target own-history;
- execution/P&L profitability;
- a broad pre-election effect;
- rescue of another failed HOLDOUT cell.

## Review package

Canonical follow-up evidence:

- `data/experiments/experiment_005c/review_followup/preregistration.json`
- `data/experiments/experiment_005c/review_followup/b1_holdout_comparison.csv`
- `data/experiments/experiment_005c/review_followup/multiplicity_falsification.csv`
- `data/experiments/experiment_005c/review_followup/block_sensitivity.csv`
- `data/experiments/experiment_005c/review_followup/temporal_concentration.csv`
- `data/experiments/experiment_005c/review_followup/us_event_semantics.json`
- `data/experiments/experiment_005c/review_followup/source_target_map.csv`
- `data/experiments/experiment_005c/review_followup/review_summary.json`
- `docs/experiments/EXPERIMENT_005C_REVIEW_FOLLOWUP.md`

All protected original 005C result files remain unchanged.

## Final label

> **DOWNGRADE — FAMILYWISE NULL NOT REJECTED**

005C remains valuable as discovery, but the one genuinely interesting joint-panel result does **not** survive the final independent-review promotion bar.
