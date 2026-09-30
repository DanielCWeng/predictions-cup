# R3-FV-001M Mathematical Challenger Handoff

## Status

- **R3 FINAL accessed:** NO
- **DATA-003 used:** YES — accepted current SIG-universe direct/reference chronology and evaluation target.
- **DATA-004 used:** YES — accepted P0/P1 ETS structural observations.
- **DATA-001 used:** NO.
- **External mathematical handoff consumed:** `research/r3-math-001-external-literature` at `e46eac3cb8aa713728407ef2f71d96f71f31e41e`.

This challenger does **not** support promotion of a historical structural fair-value trading model. Its strongest contribution is a stricter separation between identification, completion and information-transfer monitoring.

## What was tested

### Identification layer

Implemented / exercised:

- state-space LP / partial-identification bounds;
- minimal uniform coherence tube around asynchronous noisy source prices;
- reconciled-surface LP dual sub/super-replication diagnostics;
- leave-one-source identified-set information value at a fixed ambiguity radius;
- component rank / identifiability audits.

The LP layer remains useful as a **structural diagnostic**, not as a demonstrated predictive alpha. Broad or weakly identified components can remain mathematically informative while being too wide to trade.

### Completion / point-FV layer

Tested or cross-checked:

- coherent quadratic projection;
- Bernoulli-KL projection;
- maximum-entropy / KL I-projection;
- reference-measure sensitivity;
- direct-prior blends;
- calibrated error-correction variants.

The tested point completions did not robustly beat DATA-003 persistence. This agrees with the corrected parent R3 evidence and should be treated as a closed historical path for the 2026 launch.

### Aggregate-to-constituent layer

The challenger implemented a low-dimensional governor inverse:

- common Republican-governor logit factor;
- target direct prior removed in the primary LOO version;
- conditional Bernoulli independence;
- exact Poisson-binomial count likelihood;
- TRAIN-selected unobserved-race base probability;
- direct-prior blend retained only as a secondary diagnostic.

The final implementation is **episode-native**: repeated target ticks under the same national count surface are not counted as independent structural observations. The one-dimensional factor solve uses a bounded deterministic search rather than repeated unconstrained optimisation.

**Final V9 result:** reject. The lean governor inverse produced only 14 DEV episodes across 8 SIG markets and was decisively worse than DATA-003 persistence: MAE `0.24509` versus `0.01450`, for an absolute MAE deterioration of `0.23059`. The day-cluster bootstrap improvement interval was entirely negative (`[-0.33957, -0.09886]`). There were no eligible TRAIN episodes under the episode-native/freshness gate, so this path is both under-supported and directionally bad. Do not spend launch time tuning it.

### Dynamic / information-transfer layer

Two deliberately different post-hoc TRAIN/DEV explorations were added after static FV failed.

1. **Episode pressure**
   - unit = one distinct structural source-state episode;
   - source change, unabsorbed pressure, level residual and interval-violation signals;
   - target-only reversal is an explicit comparator.

   A Senate T50 unabsorbed-pressure anomaly appeared on DEV, but it did not robustly beat target reversal on TRAIN and DEV. It is not promoted.

2. **Cross-P0 Republican wave factor**
   - Kamala-state Republican-win count;
   - “Democrats win all core-four Senate races” joint;
   - “Republicans win any Biden-Trump Senate/Governor election” joint;
   - TRAIN-fitted absorption and isotonic calibration;
   - factor-specific source-state episodes.

   No sufficiently supported factor passed the requirement of positive TRAIN + DEV response **and** incremental value over target-only reversal. Broad wave-factor deployment is rejected.

## What the parent R3 programme independently found

The parent TRAIN/DEV programme converged on the same answer:

- persistence / accepted direct reference remains the production baseline;
- LP bounds remain diagnostic;
- LOO-family QP/KL are negative;
- MaxEnt is valid only as an explicit completion rule, not a supported point-FV signal;
- calibrated seat ECM is negative;
- frozen seat/simplex lead-lag tests have no qualified historical candidate;
- the fast lead-lag tests are materially **support limited** because very few DEV source shocks receive a direct-target fill response inside 300 seconds;
- flow/hazard were not opened as a rescue search.

This agreement is important: the challenger is not contradicting the parent by finding a different parameterisation of the same dead mechanism.

## What survives

### 1. Identification diagnostics

Keep:

- hard/robust identified intervals;
- dual replication diagnostics;
- contract/family information value;
- explicit semantic uncertainty;
- source ancestry.

These are useful for monitoring structural consistency, understanding which markets matter, and deciding when a structural object is informative enough to deserve attention.

### 2. Live sparse-shock shadowing

Historical data cannot determine the short-horizon information-leadership question cleanly because the target venue has too few rapid response fills.

The correct deployment is therefore **shadow-only live learning**, not historical promotion.

Frozen live configuration:

- Senate count shock -> SIG Republican Senate;
- core-four Senate joint shock -> SIG Democratic Senate;
- Biden-Trump “Republican any” joint -> Senate targets as a low-support curiosity;
- 5s / 30s / 300s observation horizons;
- source-observable timestamp, SIG book timestamp, pre-shock bid/ask/mid, first response and executable side-aware markouts;
- target-only reversal comparator;
- QP/KL directional agreement where applicable.

Minimum live promotion gate:

- at least 20 independent shocks;
- at least two distinct days/event windows;
- positive incremental performance over target-only reversal;
- positive side-aware markout after fees/slippage;
- no single-shock dominance.

A pass only permits a **micro-size challenger**. MAKE/RISK remain authoritative.

## Exact code/config to absorb

- `scripts/kaggle/r3_fv_001_math_challengers/run.py`
  - component identification;
  - robust LP bounds;
  - LP dual diagnostics;
  - fixed-radius information value;
  - I-projection sensitivity;
  - episode-native governor inverse.
- `data/research/r3_fv_001_math_challengers/LIVE_SHOCK_CONFIG.json`
  - launch-time shadow signal definitions and gates.
- `data/research/r3_fv_001_math_challengers/EVENT_PRESSURE_EVIDENCE.json`
  - post-hoc episode-pressure evidence and non-promotion decision.
- `data/research/r3_fv_001_math_challengers/WAVE_FACTOR_EVIDENCE.json`
  - corrected cross-P0 wave-factor evidence and non-promotion decision.
- `data/research/r3_fv_001_math_challengers/GOVERNOR_V9_EVIDENCE.json`
  - final episode-native Poisson-binomial governor inverse rejection.

## Scientific conclusion

The main failure was conceptual rather than computational: overlapping election markets contain genuine structural information, but that does not imply that their coherent probability level is a better **next SIG price** than SIG persistence.

With V9 complete, **there is no remaining historical structural point-FV challenger pending in this lane.** The useful separation is now:

[
\text{direct SIG/reference baseline}
+
\text{structural identified set / diagnostics}
+
\text{live sparse-shock monitor}
]

rather than:

[
\text{structural surface}
\rightarrow
\text{continuous point FV}
\rightarrow
\text{trade every residual}.
]

That is the challenger’s recommended R3 handoff for launch.
