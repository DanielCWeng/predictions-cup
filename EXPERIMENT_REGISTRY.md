# Experiment Registry

This registry is intentionally small. It records the near-term empirical programme without
claiming that an experiment has evidence, edge, or production readiness before the replay and
data gates support that claim.

| Experiment | Question | Status | Immediate inputs / maths |
|---|---|---|---|
| LEADLAG-001 | Does an observable external/related-market price move precede executable target repricing? | `IMPLEMENTED / WAITING FOR DATA` | M-087, M-088, M-089, M-127; thresholded probability/logit impulses; executable response curves |
| RV-001 | Does a simple external/reference fair-value residual predict later executable target repricing? | `IMPLEMENTED / WAITING FOR DATA` | M-121; direct, weighted-probability and weighted-logit baselines |
| LOO-PRICE-001 | Can related/direct-family markets predict a target when its own quote is removed from the predictor? | `IMPLEMENTED / WAITING FOR DATA` | M-027/M-028; explicit predictor configuration; target quote excluded from predictor set |
| LOO-FAMILY-001 | Can non-mechanical related markets predict a target after its direct information family is removed? | `IMPLEMENTED / WAITING FOR DATA` | M-027/M-028; hard leakage guard against direct equivalent/complement/mechanical sibling inputs |
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
