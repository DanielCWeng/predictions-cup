# Experiment Registry

This registry is intentionally small. It records the near-term empirical programme without
claiming that an experiment has evidence, edge, or production readiness before the replay and
data gates support that claim.

| Experiment | Question | Status | Immediate inputs / maths |
|---|---|---|---|
| LEADLAG-001 | Does an observable external/related-market price move precede executable target repricing? | `EXPERIMENT-003 COMPLETE / INCONCLUSIVE` | frozen 30s-grid/5s-lookback same-venue screen was weak; not a decisive event-time lead/lag test |
| RV-001 | Does a simple external/reference fair-value residual predict later executable target repricing? | `EXPERIMENT-003 COMPLETE / INCONCLUSIVE` | naive equal-weight logit-level-gap specification was weak; calibrated structural FV was not tested; stability caveat recorded |
| LOO-PRICE-001 | Can related/direct-family markets predict a target when its own quote is removed from the predictor? | `EXPERIMENT-003 COMPLETE / DIAGNOSTIC` | same-event sibling logit-level-gap diagnostic; not exact exhaustive-partition reconstruction |
| LOO-FAMILY-001 | Can non-mechanical related markets predict a target after its direct information family is removed? | `EXPERIMENT-003 COMPLETE / INCONCLUSIVE` | M-027/M-028; explicit INDIRECT inventory with hard leakage separation |
| MICROSTRUCTURE-001 | Do simple observable book-state features add short-horizon predictive information beyond own-price/state controls? | `EXPERIMENT-003 COMPLETE / INCONCLUSIVE` | stability passed; no nominal BH rejection; imbalance and implemented microprice displacement are algebraically redundant |
| PARTICIPANT-001 | Does train-frozen participant identity condition future repricing beyond identity-blind fill activity? | `EXPERIMENT-003 COMPLETE / CONFIRMATORY INVALIDATED` | baseline/placebo deviation plus dominant protocol-contract identities; current signal is participant/protocol-identity exploratory structure, not trader-skill evidence |
| LOWRANK-001 | Does a restrained leave-target-out common factor improve on own-price plus equal-weight cross-market baselines? | `EXPERIMENT-003 COMPLETE / INCONCLUSIVE` | pre-result first-three-reference amendment documented; usable evidence only from Peru first round, so severely underpowered |
| 004C-A | Does generic internal source→target propagation add predictive information after freshness/activity/common-state controls? | `COMPLETE / INCONCLUSIVE / NO PROMOTED EDGE` | observed-record timing is not genuine quote-renewal evidence; demote generic pairwise lead-lag |
| 004C-B | Does coherent/competitive family redistribution predict correction? | `COMPLETE / SOFT_COMPETITIVE_EFFECT_ONLY` | one narrow Colombia PRE competitive-family effect survived; no hard/exhaustive-family edge promoted |
| 004C-C | Does mapped Polymarket price discovery predict subsequent SIG repricing? | `COMPLETE / INCONCLUSIVE / NO PROMOTED EDGE` | raw 30s effect failed mapping-specific controls; wrong-contract control stronger; crossing economics negative |
| 004C-D | Do age/activity/genuine-renewal conditions rescue the historical structure? | `COMPLETE / NO_CONDITIONAL_EDGE` | supported Colombia PRE cells failed predictive-gain gates; Peru PRE untestable under frozen outside-family-control rule |
| 005A | Does fee-role / aggressive-flow structure add predictive information? | `SCAFFOLD IN REVIEW / EMPIRICAL GATE CLOSED` | PR #38 remains draft/unmerged; no 005A empirical discovery run is part of the 004C close-out |
| MM-001 | Are selective market-making economics positive after spread/fill assumptions? | `PLANNED — REQUIRES FILL MODEL` | executable spread economics; no maker-fill model in BUILD-005 |
| ECO-001 | Does external impulse plus observable local reaction improve on direct lead/lag? | `DEFER UNTIL LIVE ECOLOGY DATA` | challenger to LEADLAG-001, not a primary build target |

## Registry discipline

- BUILD-005 supplies deterministic observable-time replay and executable markouts; EXPERIMENT-002
  adds experiment code, not evidence of edge.
- Instrument relationships are explicit configuration. Similar titles never create a relationship
  automatically.
- LOO-PRICE and LOO-FAMILY are separate experiment families. LOO-FAMILY rejects predictor sets
  containing the target, configured direct/excluded family members, or references classified as
  direct equivalents, complements or mechanical siblings.
- Train/development/holdout separation remains chronological and holdout data is not used for
  parameter selection.
- Maker fills, queue position, production trading, strategy optimisation and complex ML remain
  outside this registry's current implementation scope.