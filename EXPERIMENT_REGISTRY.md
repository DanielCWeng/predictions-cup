# Experiment Registry

This registry is intentionally small. It records the near-term empirical programme without
claiming that an experiment has evidence, edge, or production readiness before the replay and
data gates support that claim.

| Experiment | Question | Status | Immediate inputs / maths |
|---|---|---|---|
| LEADLAG-001 | Does an observable external/related-market price move precede executable target repricing? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | M-087, M-088, M-089, M-127; target-autoregressive baseline plus explicit indirect references |
| RV-001 | Does a simple external/reference fair-value residual predict later executable target repricing? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | M-121; equal-weight logit structural residual |
| LOO-PRICE-001 | Can related/direct-family markets predict a target when its own quote is removed from the predictor? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | M-027/M-028; mechanical same-event reconstruction diagnostic |
| LOO-FAMILY-001 | Can non-mechanical related markets predict a target after its direct information family is removed? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | M-027/M-028; explicit INDIRECT inventory with hard leakage separation |
| MICROSTRUCTURE-001 | Do simple observable book-state features add short-horizon predictive information beyond own-price/state controls? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | top-book imbalance; microprice displacement; spread/depth/freshness controls |
| PARTICIPANT-001 | Does train-frozen participant identity condition future repricing beyond identity-blind fill activity? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | maker/taker role identity only; same-block-second exclusion; label-permutation placebo |
| LOWRANK-001 | Does a restrained leave-target-out common factor improve on own-price plus equal-weight cross-market baselines? | `EXPERIMENT-003 PREREGISTERED / RUN PENDING` | TRAIN-only rank-1 SVD; rank-2 sensitivity; no broad factor search |
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
