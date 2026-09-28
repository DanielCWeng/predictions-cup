# EXPERIMENT-004C — Final Programme Handoff

**Programme status:** COMPLETE / FROZEN  
**Canonical integration base:** `6aa6a6c9f4108e582584bc141cc68fa5383e522c`

EXPERIMENT-004C tested four distinct interpretations of historical election-market structure:

1. internal price propagation;
2. structural family redistribution;
3. external-venue mapped price discovery;
4. conditional age/activity/renewal response.

No robust executable alpha was established. The only positive discovery worth carrying forward is the narrow 004C-B soft competitive-family structure. This does **not** imply that prediction markets contain no alpha, price information is useless, broad common-event state is non-predictive, all structural relationships fail, or all cross-venue information fails. 004C tested specific mechanisms, not the complete predictive information set.

## 004C-A — Information Propagation / Freshness

**Disposition:** `INCONCLUSIVE / NO PROMOTED EDGE`

Generic source→target 30-second price propagation did not survive the stronger incremental controls. The headline A3 full-grid test did not establish incremental source-market transmission.

Observed-record A1/A2 update timing must not be interpreted as genuine economic quote renewal: repeated unchanged BBO observations and capture timing remain plausible explanations. Generic pairwise internal lead-lag is therefore demoted, while genuine-change renewal remains a separate unresolved mechanism.

Canonical evidence:
- `data/experiments/experiment_004c/a/MASTER_HANDOFF_004C_A.md`
- `data/experiments/experiment_004c/a/FINAL_RESEARCH_REPORT.md`

## 004C-B — Structural / Competitive-Family Redistribution

**Disposition:** `SOFT_COMPETITIVE_EFFECT_ONLY`

No hard structural/exhaustive-family alpha promoted.

One Colombia first-round competitive-family effect survived the frozen soft criteria at 30 seconds and persisted through 60-second and 120-second robustness, but comparable Peru structures did not replicate. This is an interesting narrow discovery, not a general Cup edge.

Coverage limitation: the richest Hungary seat-bin/threshold topology was largely not 004A.2-usable and therefore was not meaningfully tested. The lack of a promoted hard family must not be generalized into rejection of coherent fair-value or all structural models.

Canonical evidence:
- `data/experiments/experiment_004c_b/review/MASTER_HANDOFF_004C_B.md`
- `data/experiments/experiment_004c_b/review/FINAL_REPORT_004C_B.md`

## 004C-C — Polymarket → SIG Cross-Venue Price Discovery

**Disposition:** `INCONCLUSIVE / NO PROMOTED EDGE`

Raw mapped PM→SIG 30-second prediction was positive, but the mandatory wrong-contract control was substantially stronger. No EXACT contract survived FDR. Shorter-horizon / next-update evidence did not support direct transmission, and executable crossing economics were strongly negative.

The raw result is therefore evidence of broad/common event-state structure rather than mapping-specific PM→SIG price discovery.

Canonical evidence:
- `data/experiments/experiment_004c/crossvenue/MASTER_HANDOFF_004C_C.md`
- `docs/experiments/EXPERIMENT_004C_C_CROSSVENUE_PRICE_DISCOVERY_RESULTS.md`

## 004C-D — Conditional Response / Genuine Quote Renewal

**Disposition:** `NO_CONDITIONAL_EDGE`

In the two adequately supported Colombia PRE challenge events:

- D1 age-conditioned magnitude interaction had the wrong sign and negative predictive gain;
- D2 genuine-renewal timing interaction had the wrong sign and negative Brier gain;
- Holm-adjusted PRE slot p-values were 1.0;
- ACTIVE did not rescue the mechanisms;
- D4 did not achieve predictive-gain support.

Peru runoff PRE was untestable under the frozen outside-family-control requirement. Do not generalize the negative result beyond supported evaluation.

Canonical evidence:
- `data/experiments/experiment_004c_d/results/MASTER_HANDOFF_004C_D.md`
- `data/experiments/experiment_004c_d/results/FINAL_RESEARCH_REPORT.md`
- `data/experiments/experiment_004c_d/POST_RUN_REVIEW_004C_D.md`

## Integration provenance

| Lane | PR | Final experiment head | Merge SHA |
|---|---:|---|---|
| 004C-A | #34 | `2782277a46ad3b2147a6e85788c85f2ce77405fd` | `117a584c990c2a5488094200c1522de4261b66be` |
| 004C-B | #35 | `65bd8a81a7e4ef36e920df5e092a37b51cc00eb7` | `777354f9aa6b56012b193853a2ae544e08be5ce3` |
| 004C-C | #37 | `fbc8c2214969c8d9c7bd7afa7a7b20d248ecb742` | `751118f3edc818a113656e9a9f31951d29d195d3` |
| 004C-D | #39 | `3a27d1d54f392b860358f950e558ff5f7f433b70` | `6aa6a6c9f4108e582584bc141cc68fa5383e522c` |

004C-D's final head includes one engineering-only CI commit, `3a27d1d54f392b860358f950e558ff5f7f433b70`, which changes Actions checkout to full history so the frozen provenance commit can be validated in CI. It does not alter formulas, features, preregistration, empirical evidence, thresholds or results.

## Boundary to EXPERIMENT-005

PR #38 / EXPERIMENT-005A remains unmerged. No 005A empirical run is part of this integration. Future discovery work should use `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md` to avoid duplicate work without pre-freeze anchoring to prior result magnitudes.
