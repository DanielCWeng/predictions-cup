# Experiment Registry

This registry is intentionally small. It records the near-term empirical programme without
claiming that an experiment has evidence, edge, or production readiness before the replay and
data gates support that claim.

| Experiment | Question | Status | Immediate inputs / maths |
|---|---|---|---|
| LEADLAG-001 | Does an observable external price move precede executable SIG repricing? | `WAITING FOR BUILD-005 / DATA` | M-087, M-088, M-089, M-127; standardized executable markouts |
| RV-001 | Does a simple external/reference fair-value residual predict later executable SIG repricing? | `PLANNED` | M-121; simple direct external FV baseline |
| MM-001 | Are selective market-making economics positive after spread/fill assumptions? | `PLANNED — REQUIRES FILL MODEL` | executable spread economics; no maker-fill model in BUILD-005 |
| ECO-001 | Does external impulse plus observable local reaction improve on direct lead/lag? | `DEFER UNTIL LIVE ECOLOGY DATA` | challenger to LEADLAG-001, not a primary build target |

## Registry discipline

- BUILD-005 supplies measurement infrastructure, not a claim that any row has trading edge.
- Instrument pairing is explicit experiment configuration until an accepted live crosswalk exists.
- Holdout data is not used for parameter selection.
- Maker fills, queue position and strategy optimisation remain outside this registry's current
  implementation scope.
