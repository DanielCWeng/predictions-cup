# EXPERIMENT-005I — MASTER HANDOFF

## Final status

**COMPLETE — five of five preregistered HOLDOUT confirmation gates supported.**

The result is useful, but the execution boundary matters: this experiment identifies robust price-path and observable-state structure. It does **not** establish post-fee executable alpha, queue priority, passive fill probability, or a reason to modify MAKE directly.

## Confirmed mechanisms

### 1. Five-minute mean reversion

HOLDOUT sampled move episodes: **55,340**.

Reversal rate: **67.26%**. Every temporal HOLDOUT worker was above **66%**.

The effect survives the frozen falsification slices:
- every move-size bucket is above 63%;
- every price region is above 63%;
- every relative-spread bucket is above 64%;
- PRICE_DISCOVERY still reverses about 68.1%;
- DIRECTIONAL_PRESSURE is the least mean-reverting transparent state, but reversal still exceeds continuation.

**Route:** MM_REPLAY -> LIVE_DIAG -> SHADOW only after replay demonstrates execution value after spread, fees, latency, fill selection and inventory effects.

### 2. Depth-normalised OFI

The frozen challenger improves full-panel HOLDOUT log-loss by **0.004165** over **1,195,563** non-zero next-minute observations, with **5/5** temporal workers positive.

This is not conventional trend-following OFI. The simple OFI sign is often contrarian outside QUIET, while the multivariate frozen model carries the incremental information.

**Route:** MM_REPLAY + LIVE_DIAG. Do not wire raw sign directly into MAKE.

### 3. PRICE_DISCOVERY

HOLDOUT occupancy: **281,619 minute-states**.

Exit hazard: **53.56% per minute**. Worker p90 dwell: **3-4 minutes**. **47.38%** of exits go directly to POST_SHOCK.

Interpret it as a fast repricing/transition state rather than a generic continuation/momentum regime.

**Route:** LIVE_DIAG + MM_REPLAY. A quoting policy may eventually respond differently to this state, but only after replay.

### 4. LIQUIDITY_STRESS

HOLDOUT occupancy: **723,144 minute-states**.

Exit hazard: **4.93% per minute**. Worker p90 dwell: roughly **26-38 minutes**. **23.15%** of exits go to REPLENISHMENT.

This is a materially persistent stressed-liquidity state.

**Route:** LIVE_DIAG + MM_REPLAY. Candidate for a future SHADOW quoting/risk gate after replay.

### 5. Resilience after withdrawal

After a large visible withdrawal, episodes that visibly replenish in the next minute have mean subsequent abs-5m movement **0.000842** versus **0.001272** when they do not replenish: a **33.83% reduction** on HOLDOUT, with the same direction in **5/5** workers.

This is a predictive association. It is not evidence that replenishment causally stabilises price, nor that our passive quote would fill.

**Route:** MM_REPLAY + LIVE_DIAG.

## Useful descriptive transfers

- **Fill-direction persistence:** stable same-side run structure on TRAIN/DEV; broad size-weighted lag-1 correlation is negative. Fill HOLDOUT was intentionally preserved. Route to 005H/LIVE_DIAG if needed.
- **Quote/update lifetime:** DEV median quote/BBO interarrival is roughly 29-35ms. Historical state can change on the same order as realistic 25-100ms transport/exposure. Replay should not assume a static BBO during exposure.
- **Common liquidity:** useful descriptive context, but it did not earn a generic directional factor.
- **Coarse clustering:** a few broad observable axes exist, but there is no clean universal finite latent-state taxonomy.

## Dead or non-promoted ideas

Do not spend more pre-launch research time on generic microprice, raw event OFI, generic visible multi-level flow, generic LOB shape, generic common-liquidity direction, generic book-age direction, or VPIN from this lane.

HMM/Markov-switching and Hawkes challengers did not earn their extra complexity.

Kyle/Amihud-style impact was not cleanly observable enough on the sampled trade/book surface for a reliable promoted result.

Spread decomposition remains a 005H boundary.

## Operational recommendation

The highest-value live features from this lane are:

1. transparent state label;
2. recent 5-minute return / reversal context;
3. frozen depth-normalised OFI model inputs;
4. liquidity-stress / discovery flags;
5. withdrawal and next-minute replenishment state;
6. quote/BBO update-age / update-hazard diagnostics.

Log these in LIVE_DIAG immediately. Feed them to MM_REPLAY before any execution-policy change. Promote to SHADOW only when replay shows post-cost value and stable inventory behaviour.

Expiration-focused regime research remains **DEFERRED**.

MAKE MODIFIED: NO

REAL SIG ORDERS SENT: NO
