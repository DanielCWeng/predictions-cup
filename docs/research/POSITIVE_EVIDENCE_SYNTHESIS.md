# POSITIVE EVIDENCE SYNTHESIS

**RESEARCH-SYNTHESIS-001**  
**Starting main:** `611b048ce0ee4630c01a9500cb021a70afba8474`  
**Evidence snapshot:** 2026-10-01  
**Canonicality refresh main:** `662969df4e481f1cbd9417c923ae35d3a0aa9f9f` (005G merged via PR #114)  
**005H B0 refresh:** PR #94 head `5d37a2f2a7b64685c4a012408fdca53e6bf75307` (historical B0 complete; PR remains unmerged)

## Executive summary

The repository history supports the central hypothesis **with an important exception**.

The strongest, most replicated and most current-universe findings are overwhelmingly about **WHEN a market/book is likely to update, transition, become active, remain stressed, or experience an unsigned move**. The clearest chain is `genuine_age_s → 300s BBO update hazard`: it passed the 005F sealed HOLDOUT and then, on the fresh DATA-003 orderbook corpus, the exact frozen mechanism replicated at **+26.50% relative MSE improvement across 658 markets with 48/48 positive blocks**. 005G also found `state_dwell_s → state_transition_h300` at **+40.30%**. PR #114 has since merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`, so this evidence is now canonical.

That is not the whole story. Merged 005I supplies the first substantial canonical directional layer: **67.26% five-minute reversal** across 55,340 qualifying sampled HOLDOUT minute-states, every temporal worker above 66%, plus a frozen depth-normalised OFI challenger that improves next-minute directional log loss by **0.004165** across 1,195,563 non-zero observations with **5/5 workers positive**. Narrower directional evidence also exists in 005D late-count structural convergence and 005E's secondary signed-flow diagnostics.

EXPERIMENT-005H now adds a distinct historical interaction layer. On the untouched B0 holdout, frozen **C04 Arrival-State** distinguished actual fill moments from matched non-fill states at **AUC 0.6882** (token-cluster bootstrap 95% interval **0.6777–0.7000**; chronological halves **0.6913 / 0.6852**), while **C05 Direction-State** predicted BUY versus SELL aggressor conditional on a fill at **AUC 0.6745** (bootstrap **0.6398–0.7192**; halves **0.6973 / 0.6547**). Nothing was refit on B0. PR #94 remains unmerged, so these are strong historical B0 survivors requiring prospective confirmation, not yet canonical main-branch evidence.

The missing bridge is economics. There is **no positive E4 or E5 result**. The one clean attempt to convert the strongest state-hazard family into passive-execution decisions failed: all 28 frozen 005G WIDTH/SIZE/WAIT/REFRESH policies underperformed the DEV baseline. The post-hoc mechanism review showed why: the hazard still predicted BBO renewal and state transition, but it did **not** rank adverse fills (AUC ~0.456). High activity/transition hazard is not the same thing as toxic-to-quote.

The project therefore has useful pieces of a trading model, but they sit at different layers:
- strong change/transition timing;
- some credible direction;
- structural/fair-value diagnostics;
- weak/secondary participant-flow quality;
- historical B0 evidence for **market interaction/fill-arrival state and aggressor direction** from 005H, but **no validated own-quote fill-probability/toxicity bridge**;
- **no demonstrated post-cost economic conversion**.

---

# 1. What actually worked

## A. Renewal and transition hazard — strongest family

### 005F `genuine_age_s`

Sealed HOLDOUT:
- ACTIVE 300s jump hazard: **+16.04%** relative MSE, 14,037 obs.
- ACTIVE 300s update hazard: **+35.16%**, 14,177 obs.
- PRE 300s update hazard: **+35.35%**, 297,384 obs.
- market/event/family leave-outs positive on the strongest update results.
- capture-only controls do not explain the effect.

The delayed-feature tests show this is primarily a **persistent staleness/renewal state**, not a fleeting lead-lag impulse.

### 005G fresh replication — canonical

The exact frozen 005F PRE_UPDATE mechanism was retested on fresh DATA-003:
- `genuine_age_s → update_h300`;
- 222,567 holdout rows;
- 658 markets;
- **+26.5024%** relative MSE;
- **48/48** positive blocks.

This is the programme's cleanest E3 result.

### 005G state dwell — canonical

- `state_dwell_s → state_transition_h300`: **+40.2969%**, 48/48 blocks.
- `state_dwell_s → state_transition_h60`: **+20.7621%**, 48/48 blocks.

The effect is larger at 300s than 60s, which is consistent with a regime-duration variable rather than a precise next-tick trigger.

### PRED-006

Current-universe next-price-change hazard:
- C01 1800s: **+8.18%** relative Brier, AUC **0.712**.
- C02 600s: **+8.36%**, AUC **0.697**.
- both pass the one-shot FINAL.
- both are explicitly unsigned: they predict **whether** a price changes, not **which way**.

**Bottom line:** this family is real, repeated and broad enough to be treated as a genuine research asset. It is still not a trading rule.

## B. Directional information — real, but newer and less replicated

### 005I five-minute mean reversion

Merged, frozen HOLDOUT:
- 55,340 qualifying sampled minute-state/move observations;
- **67.26% reversal**;
- every temporal worker >66%;
- frozen price/move/spread slices remain >63–64%;
- PRICE_DISCOVERY itself reverses ~68.1%.

The observations overlap in time, so 55,340 is not an independent-episode count. The result nevertheless survives every frozen temporal worker and the preregistered falsification slices.

### 005I depth-normalised OFI

- 1,195,563 non-zero HOLDOUT observations;
- log-loss improvement **0.004165**;
- **5/5** temporal workers positive.

This is not “OFI sign = direction.” Raw OFI sign is often contrarian outside QUIET. The multivariate frozen construction contains the increment.

### 005D late-count structural direction

Two relationships survive the sealed HOLDOUT:
- Peru Fuerza Popular Senate → Chamber at 5s/60s;
- Colombia Abelardo overall → Antioquia runoff at 5s.

Both are **100% LATE_COUNT**. Peru has mixed longer-block robustness; Colombia is statistically stable but extremely small economically.

### 005E participant-flow quality

The primary unconditional participant alpha failed. Secondary frozen diagnostics show:
- signed-markout gains at 60s/300s, positive in all five families;
- improved next-change classification.

These are useful hypotheses, not promoted alpha.

**Bottom line:** we do possess credible directional information. It is not yet as replicated across genuinely fresh datasets/events as the renewal/change-hazard family.

## C. Structural/coherence information

005D reconstruction can recover held-out same-event prices extremely well:
- median HOLDOUT RMSE improvement roughly **47%–99.8%** across five families.

This proves that the market set contains large amounts of structural/coherent information. It does **not** prove that a coherent level beats the target's own persistence as a future price forecast.

R3-FV-001 made that distinction explicit: extensive LP/QP/KL/MaxEnt/count-surface work did **not** produce an independent structural point-FV model that beat persistence. Structural information survives as a **diagnostic/identified-set/scanner layer**, not as a continuous point-FV alpha.

## D. Market interaction and aggressor direction — 005H (unmerged)

005H links accepted DATA-003 economic fills to strictly pre-fill V3 order-book state using `timestamp_received` plus sequence ordering. Its two frozen B0 candidates both passed without holdout refitting:

- **C04 Arrival-State:** AUC **0.6882**, 95% token-cluster bootstrap **0.6777–0.7000**, chronological halves **0.6913 / 0.6852**, 14,396 fills matched to 14,396 controls across 1,101 token clusters.
- **C05 Direction-State:** AUC **0.6745**, bootstrap **0.6398–0.7192**, halves **0.6973 / 0.6547**, 12,661 scored fills across 1,074 token clusters.

The supported interpretation is narrower than own-order fill modelling: observable pre-fill state contains reproducible information about **when market interaction/fills occur** and, conditional on a fill, **which side aggresses**. It does not establish queue position, whether our own passive quote fills, post-fill toxicity, fees, inventory economics or P&L. C01 relative size, C02 failed replenishment and C03 fill-beyond-state were rejected before B0.

**Bottom line:** 005H materially narrows the old fill-layer gap, but it does not close the execution/economics gap. Its correct route is prospective LIVE-DIAG / PAPER context before any execution-policy change.

---

# 2. Evidence strength

| Finding family | E0 | E1 | E2 | E3 | E4 | E5 |
|---|---:|---:|---:|---:|---:|---:|
| 005F genuine-age renewal hazard | ✓ | ✓ | **✓** | **✓ via 005G fresh replication** |  |  |
| 005G state-dwell transition hazard | ✓ | ✓ | **✓** |  |  |  |
| PRED-006 price-change hazard | ✓ | ✓ | **✓** |  |  |  |
| 005I mean reversion | ✓ | ✓ | **✓** |  |  |  |
| 005I depth-normalised OFI | ✓ | ✓ | **✓** |  |  |  |
| 005I liquidity/regime/resilience | ✓ | ✓ | **✓** |  |  |  |
| 005H C04 arrival-state† | ✓ | ✓ | **✓** |  |  |  |
| 005H C05 aggressor-direction† | ✓ | ✓ | **✓** |  |  |  |
| 005D late-count structural convergence | ✓ | ✓ | **✓** |  |  |  |
| 005D reconstruction/coherence | ✓ | ✓ | **✓** | broad families, but reconstruction not predictive replication |  |  |
| 005E signed-flow secondary | ✓ | ✓ | **✓*** |  |  |  |
| 004C-B soft competitive family | ✓ | ✓ |  |  |  |  |
| 005A same-family 5s | ✓ | ✓ |  |  |  |  |

\* Secondary frozen HOLDOUT diagnostic; not a promoted primary result.  
005G PR #114 merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`; its sealed-holdout evidence is canonical.  
† 005H PR #94 remains unmerged; C04/C05 are untouched historical B0 passes requiring future confirmation.

There are **zero E4 positives** and **zero E5 positives** in the research history inspected.

---

# 3. What our signals actually predict

The evidence clusters into a small number of latent phenomena.

## MARKET_TRANSITION_HAZARD

Signals:
- 005G `state_dwell_s`;
- PRED-006 hazard candidates;
- 005I PRICE_DISCOVERY exit/dwell;
- 005F ACTIVE jump hazard.

Answers:
- **WHEN:** strong.
- **WHAT:** market state or price likely to change.
- **DIRECTION:** mostly none.
- **ECONOMICS:** none established.

## ORDERBOOK_RENEWAL_HAZARD

Signals:
- 005F/005G `genuine_age_s`;
- 005G `spread_x_distance`;
- `price_change_age_s`;
- `volatility_x_liquidity`;
- `distance_from_0_5`.

Answers:
- **WHEN:** very strong.
- **WHAT:** BBO/orderbook likely to update.
- **DIRECTION:** none.
- **FILL TOXICITY:** explicitly not established.
- **ECONOMICS:** 005G conversion failed.

## LIQUIDITY STATE / RESILIENCE

Signals:
- 005I LIQUIDITY_STRESS;
- withdrawal→replenishment resilience;
- 005F small PRE spread-change state.

Answers:
- **WHEN:** liquidity stress persists/exits; replenishment appears.
- **WHAT:** liquidity condition / future absolute movement.
- **DIRECTION:** no robust signed price direction.
- **ECONOMICS:** not tested.

## DIRECTIONAL REVERSION / FLOW

Signals:
- 005I five-minute mean reversion;
- 005I depth-normalised OFI;
- 005E secondary signed participant flow.

Answers:
- **WHEN:** conditional on a recent move/current flow/participant action.
- **WHAT:** signed continuation vs reversal / next change.
- **DIRECTION:** yes.
- **ECONOMICS:** not yet.

## PARTICIPANT INTERACTION / AGGRESSOR DIRECTION

Signals:
- 005H C04 Arrival-State;
- 005H C05 Direction-State.

Answers:
- **WHEN:** C04 identifies observable states associated with actual fill moments versus matched non-fill states.
- **WHAT:** elevated market interaction / fill-arrival propensity; conditional aggressor side.
- **DIRECTION:** C05 supplies BUY-versus-SELL aggressor direction conditional on a fill.
- **OUR FILL PROBABILITY:** not established; C04 is about market fill moments, not queue-position-aware fills of our own quote.
- **TOXICITY / ECONOMICS:** not established.

## STRUCTURAL / RELATIVE VALUE

Signals:
- 005D late-count convergence;
- 005D reconstruction;
- 004C-B soft competitive family;
- 005A same-family 5s.

Answers:
- **WHEN:** usually regime/family-specific.
- **WHAT:** cross-contract coherence/repair.
- **DIRECTION:** narrow yes for a few relationships.
- **ECONOMICS:** not established.
- **GENERALITY:** limited; R3 rejects a broad point-FV interpretation.

---

# 4. Movement vs direction

## Test of the central hypothesis

> Is it true that nearly all of our replicated positive signals predict impending movement/change rather than direction?

**Answer: the dominant pattern is true, but “nearly all” is now too strong if it is meant literally.**

| Signal family | Predicts movement/state? | Predicts direction? | Predicts economics? |
|---|---:|---:|---:|
| Genuine-age renewal hazard | **Yes** | No | No; conversion failed in 005G bundle |
| State-dwell transition hazard | **Yes** | No | No; conversion failed |
| PRED-006 price-change hazard | **Yes** | No | No |
| PRICE_DISCOVERY / LIQUIDITY_STRESS | **Yes** | No | No |
| Withdrawal/replenishment resilience | **Yes** | No | No |
| 005H C04 arrival-state† | **Interaction/fill arrival** | No | No |
| 005H C05 direction-state† | Conditional on fill | **Yes — aggressor side** | No |
| Five-minute mean reversion | Context is move-triggered | **Yes** | No |
| Depth-normalised OFI | Some state context | **Yes** | No |
| 005D late-count structural convergence | Some | **Yes, narrow** | No |
| 005E signed participant flow | Conditional | **Yes, secondary** | No |
| 004C-B / 005A family effects | Some | **Yes, narrow** | No |


The important asymmetry is not “zero direction.” It is:

> **Change/hazard evidence is broader, more independently repeated and more current-universe replicated than directional evidence. Economic evidence is weaker than both.**

That is the accurate form of the current narrative.

---

# 5. Economic conversion

This is the sharpest negative finding in the whole synthesis.

## 005G — prediction survived; conversion failed

The frozen state-hazard bundle was passed into four simple economic response families:
- WIDTH_ONLY 0/8;
- SIZE_ONLY 0/8;
- WAIT_ONLY 0/4;
- REFRESH_ONLY 0/8.

Best net deltas versus the DEV baseline:
- width **-0.7800**;
- size **-0.7500**;
- wait **-1.5000**;
- refresh **-0.7650**.

The mechanism review preserved the scientific result:
- BBO-update AUC ~**0.675**;
- state-transition AUC ~**0.740**.

But it falsified the economic shortcut:
- adverse-fill AUC ~**0.456**;
- highest-hazard quintile was actually the most valuable DEV bucket in the proxy;
- all 28 frozen interventions discarded more favourable-fill value than bad-fill loss avoided.

This is not a predictive failure. It is an **economic-conversion failure**.

It also supplies a general warning:

> Do not map “market about to change” mechanically to “do not quote” or “this fill will be toxic.”

## Other conversion states

- 004C-C: crossing economics strongly negative.
- 005I: no completed accepted MM-REPLAY result at snapshot.
- 005H: C04/C05 pass historical B0 for fill-arrival state and conditional aggressor direction, but no own-quote fill, toxicity or P&L conversion has been established.
- PRED-006: no economic replay; future confirmation still required.
- 005D: no executable P&L.
- 005E: no economic conversion.
- 004C-B/005A: no economic conversion.

**Current economic-positive count: zero.**

---

# 6. Redundant signal families

Several experiments have almost certainly rediscovered different measurements of the same latent state.

## Renewal/transition cluster — high redundancy risk

Likely overlapping:
- `genuine_age_s`;
- `state_dwell_s`;
- `price_change_age_s`;
- PRED-006 change hazard;
- PRICE_DISCOVERY transition/exit;
- parts of `spread_x_distance`;
- quote/update-age diagnostics.

These should **not** be counted as six independent alphas.

The strongest evidence for this interpretation:
1. delayed genuine-age variants remain predictive;
2. 005G state dwell is stronger at 300s than 60s;
3. spread×distance survives both renewal and small unsigned-movement targets;
4. 005G economic conversion fails when the common hazard is interpreted as toxicity;
5. PRED-006 explicitly identifies the target as next-price-change hazard.

A sensible latent label is:

**“market currently in a state with elevated probability of observable renewal/change over the next few minutes.”**

## Common-event/family cluster

Potential overlap:
- 004B aggregate directed structure;
- 004C-C raw cross-venue response that loses to wrong-contract control;
- 005A same-family effect;
- 004C-B competitive-family state.

These contain some real common/family information, but prior history warns that common-event state can masquerade as source-specific leadership.

## Directional layer — less obviously redundant

005I mean reversion and depth-normalised OFI are not obviously the same:
- mean reversion is path/return-state information;
- depth-normalised OFI is current flow/book information;
- raw OFI sign itself is not the promoted result.

They may still correlate, but the current evidence does not justify collapsing them.

---

# 7. Complementary signal families

These are **research combinations worth testing later**, not strategy prescriptions.

## A. Transition hazard + mean reversion

**Signal A tells us:** whether a market is likely to change/reprice soon.  
**Signal B tells us:** after a qualifying move, reversal is more common than continuation.  
**Why complementary:** WHEN + signed directional tendency are different pieces of information.  
**What would falsify it:** the directional increment disappears after conditioning on frozen hazard/state variables, or any apparent improvement is entirely a duplicated regime label.

## B. Transition hazard + depth-normalised OFI

**Signal A tells us:** imminence of state/price change.  
**Signal B tells us:** small next-minute directional information from current book flow.  
**Why complementary:** timing and sign are mechanically distinct targets.  
**Falsifier:** OFI adds no incremental directional likelihood once hazard/state features are included prospectively.

## C. Renewal hazard + structural relative value

**Signal A tells us:** book/price is likely to renew.  
**Signal B tells us:** where a coherent/family-relative level says the target sits.  
**Why complementary:** timing + level/direction.  
**Falsifier:** structural residual adds no signed response beyond target persistence/mean reversion when the renewal event actually occurs.

## D. Liquidity state + participant signed-flow quality

**Signal A tells us:** whether liquidity is stressed/persistent/replenishing.  
**Signal B tells us:** whether observed participant direction is associated with future signed markout.  
**Why complementary:** market-condition context + conditional flow direction.  
**Falsifier:** participant-flow evidence vanishes outside the particular liquidity regimes that generated it, or fails a fresh primary-target test.

## E. Structural reconstruction + mean reversion

**Signal A tells us:** coherent same-event relative state / possible inconsistency.  
**Signal B tells us:** short-horizon path tendency.  
**Why complementary:** slow level scaffold + fast path dynamics.  
**Falsifier:** reconstruction residuals are completely explained by mechanically close sibling prices and add no future signed response.

---

# 8. What failed and should be retired

## THINGS WE SHOULD STOP TRYING

### Generic same-venue pairwise lead/lag as a primary idea
003 failed to promote it. 004C-A did not recover incremental source-market transmission after stronger controls. Do not reopen the same generic mechanism with another minor grid.

### Synthetic broad election index → target at machine speed
PRED-007 failed 1s, 2s, 5s, 10s and 30s against own history, a top-source comparator and delayed control. A genuinely different directly traded aggregate contract could be a new hypothesis; the failed synthetic-index construction should not be rescued.

### Historical 005B movement models as current-universe predictors
The exact frozen set transferred **0/7** to DATA-003. Historical validity is not current SIG-universe validity.

### Broad continuous structural point FV from the R3 family
R3's LP/QP/KL/MaxEnt/count-distribution programme did not beat persistence. Keep identified sets/coherence diagnostics; stop treating every coherent probability surface as a superior next-SIG-price estimate.

### Broad participant identity/behaviour as unconditional 60s alpha
005A and 005E do not support that story. 005E's useful residue is narrower: conditional signed-flow quality, which needs a fresh primary experiment.

### Generic microprice / raw event OFI / generic LOB shape / generic common-liquidity direction
005F/005I have already done enough hostile screening to deprioritise these generic versions. The promoted OFI result is specifically the frozen **depth-normalised multivariate** challenger, not raw sign.

### “High transition hazard = toxic passive fill”
005G falsified this shortcut directly. Do not rerun the same monotone WIDTH/SIZE/WAIT/REFRESH suppression family as a rescue.

### Broad reduced-rank joint-panel alpha from 005C
The descriptive result does not survive familywise dependence correction. Keep it as a historical comparator, not a central promoted factor.

---

# 9. Current signal stack

```text
EVENT / INFORMATION ARRIVAL
    004B common/directed event structure
    005E participant-flow context (secondary)
    005I PRICE_DISCOVERY / activity state
            ↓
TRANSITION / MOVEMENT HAZARD          ← strongest layer
    005F genuine_age update/jump hazard
    PRED-006 600s/1800s price-change hazard
    005G state_dwell + renewal state
    005I PRICE_DISCOVERY / LIQUIDITY_STRESS / replenishment
            ↓
DIRECTION / FAIR VALUE                ← exists, less replicated
    005I 5m mean reversion
    005I depth-normalised OFI
    005D late-count structural convergence
    005D structural reconstruction / coherence
    005E conditional signed flow (secondary)
            ↓
MARKET INTERACTION / FILL ARRIVAL    ← 005H C04 historical B0 pass†
    C04 arrival-state
    C05 conditional aggressor direction
            ↓
OWN-QUOTE FILL PROBABILITY            ← STILL MISSING
            ↓
ADVERSE SELECTION / FILL TOXICITY     ← MISSING
    005G specifically shows state hazard is not toxicity
            ↓
EXECUTION CHOICE                      ← infrastructure exists; research mapping unproven
            ↓
REALIZED ECONOMICS                    ← MISSING / no positive E4/E5
```


This is the most important architectural result of the archaeology: **we are good higher up the causal/economic chain than we are at the bottom of it.**

---

# 10. Missing pieces

## Fill probability

005H now establishes that pre-fill V3 order-book state can distinguish actual market fill moments from matched non-fill states (C04) and predict aggressor BUY/SELL direction conditional on a fill (C05). This is meaningful progress on participant-interaction modelling, but it is **not** a queue/cancel/passive-fill model for our own quote. Queue position, our displayed price/size, cancellation latency and own-order selection remain unresolved.

## Fill toxicity

005G is useful precisely because it separates this from market activity. Predicting a state transition does not imply that our fill in that state is adverse.

## Signed fair value that replicates freshly

005I has strong canonical directional evidence, but it has not yet been independently replicated across a fresh future capture the way genuine-age renewal hazard has.

## Post-cost economic conversion

No positive E4/E5. Spread, fees, latency, queue/fill selection, cancellation, turnover and inventory can all reverse a predictive result.

## Independent-event uncertainty

Several large row counts represent overlapping observations, repeated states or many rows from few event windows. The programme has improved substantially here, but future live research should count independent shocks/episodes explicitly.

---

# 11. Implications for live learning

The launch-time evidence collector should treat the positive families as **diagnostic/context variables first**:

- renewal/transition hazard;
- recent-return/reversal context;
- frozen depth-normalised OFI inputs/output;
- transparent liquidity/discovery state;
- withdrawal/replenishment state;
- structural/coherence residuals;
- participant-flow descriptors where legally/operationally observable;
- 005H C04 arrival-state and C05 conditional aggressor-direction scores when exact runtime parity is available.

The live-learning question is not “does the old backtest still have a good metric?” It is:

1. does the frozen signal fire with the same calibration on future data;
2. does it add information beyond the other latent-family members;
3. does it predict the next missing layer — fill, toxicity or signed markout;
4. does any increment survive realistic costs and execution constraints.

Nothing in this synthesis justifies direct promotion to LIVE execution.

---

# 12. Smallest sensible post-launch research question

The smallest useful question is:

> **On genuinely future capture, conditional on frozen renewal/change hazard and 005H interaction state, do frozen 005I direction and 005H aggressor-direction add incremental signed-markout and own-fill economic information beyond state-only baselines?**

Why this question:
- it tests the most plausible complementary pair rather than inventing a new signal;
- it directly probes the current missing bridge between **WHEN** and **WHICH DIRECTION**;
- it can be preregistered without tuning a strategy;
- failure is informative: if the direction layer does not add incrementally, the apparent stack is more redundant than it looks;
- success still would **not** be enough for LIVE execution; fill/cost conversion remains a separate gate.

Do not reopen another broad feature search before answering this narrower incremental question on genuinely future data.

---

# Direct answers to the ten required questions

1. **Strongest positive findings:** renewal/transition hazard (005F, PRED-006, 005G), 005I mean reversion and depth-normalised OFI, 005H C04 fill-arrival state and C05 conditional aggressor direction, 005I liquidity/resilience states, and 005D structural reconstruction/convergence.
2. **Sealed-holdout evidence:** 005F, PRED-006, 005D, 005E secondary diagnostics, 005I, and 005G.
3. **Cross-dataset/event replication:** the clearest is 005F `genuine_age_s → update_h300` freshly replicated in 005G DATA-003†. Other families have cross-market/event breadth but not equally clean fresh-dataset replication.
4. **Economic value:** **none established as positive E4/E5**. 005G conversion failed; 004C-C crossing was negative.
5. **Movement/state-only signals:** genuine-age update/jump hazard, state-dwell transition hazard†, PRED-006 price-change hazard, liquidity/discovery states and replenishment resilience.
6. **Credible direction:** **yes** — especially 005I five-minute mean reversion and depth-normalised OFI; narrower evidence exists in 005D and 005E secondary diagnostics.
7. **Likely redundancy:** genuine age, state dwell, price-change age, PRED-006 hazard, parts of PRICE_DISCOVERY and other renewal features likely share a transition/renewal latent factor.
8. **Genuine complementarity:** hazard + mean reversion/OFI; hazard + structural relative value; liquidity state + conditional participant flow.
9. **Obvious missing pieces:** own-quote fill probability/queue selection, fill toxicity, prospective confirmation of 005H/005I directional layers, and post-cost economic conversion.
10. **Smallest next research question:** on prospective live data, do frozen 005I direction and 005H aggressor-direction add incremental signed-markout / own-fill economic information conditional on frozen hazard and interaction state?

005G PR #114 is merged into `main` at `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`; canonicality has been refreshed without changing the scientific conclusions.

**The Predictions Cup research programme is currently best at predicting when a market or order book is likely to change state or reprice.**
