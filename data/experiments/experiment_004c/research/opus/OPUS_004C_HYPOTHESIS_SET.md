# OPUS 004C HYPOTHESIS SET

**Lane:** market structure · economic mechanism · probability structure · staged-election logic · information aggregation · structural residuals · falsification
**Prior framework:** `OPUS_PRE_004C_RESEARCH_DOSSIER.md`, SHA-256 `438e695cb02a0644aa5ee060b500760378aa3e011a6b5ae1e37eda918dd77be4`, frozen 2026-09-27T19:40:39Z, unchanged. Mechanism and trap IDs below (M#, T#) refer to that dossier.
**Independence:** Astra's and other 004C agents' work not inspected. No sealed event touched. No backtest or profitability optimisation performed.

---

## 0. Evidence actually available to this set

**Read:**

- `experiment/004b-dynamics-stats` @ `c63fab0` — adds only `data/experiments/experiment_004b/review/MASTER_HANDOFF_004B_B.md` on top of `main` `3601bea`.
- Accepted 004A.2 condition-usability matrix (pre-004B, on `main`) — used for coverage counts.

**Not available on the remote at time of writing:**

- Dynamics commit `37f2b18`: GitHub returns 404; the pushed branch parent is `3601bea`.
- Review artefacts named in the handoff: `dynamics_stats_findings.md`, `dynamics_stats_audit.json`, `response_audit_summary.csv`, `null_audit_summary.csv`, `stability_audit_summary.csv`.
- The 004B Structural/Core branch and its results.
- The frozen discovery spec (`4095123…`) and implementation commit `e9a95fb…`.

**Consequence.** Every "004B evidence" field below cites the handoff text or accepted 004A.2 coverage. No field claims edge-level or structural findings I have not seen. Fields that need Structural/Core evidence are marked **[PENDING S/C]**. The ranking is designed to be robust to what those artefacts can plausibly show.

### 0.1 Handoff facts this set relies on

| # | Fact (paraphrased from handoff) | Interpretation (mine) |
|---|---|---|
| E1 | Canonical 30s aggregate directed `PRICE_STRUCTURE` response beats circular-shift and block-permutation nulls and survives BH in HUN PRE, PER PRE, PER ACTIVE. HUN ACTIVE fails both nulls. | Evidence that cross-market *dynamic dependence* exists. It is **not** evidence of leadership (see §1). |
| E2 | Only aggregate 30s `PRICE_STRUCTURE` has empirical null p/q values. Individual edges and imbalance/liquidity/activity families are descriptive. | No edge-level inference is licensed. |
| E3 | Price→price availability 6,254 / 58,590 cells (~10.7%). Depth-imbalance and liquidity 3,351 / 58,590. BBO/depth activity 14,350 each. Fill activity 9,350 each. | Coverage is sparse and non-random; availability is selected by liquidity. |
| E4 | Imbalance is `depth_imbalance_change`, not aggressor-signed OFI. | Consistent with T-13/T-14: no signed flow exists. |
| E5 | HUN vs PER R1 is `INSUFFICIENT_COMPARABLE_TOPOLOGY`. | No cross-family replication of any edge. |
| E6 | Peru PRE vs ACTIVE price→price sign agreement is 0.486–0.556 across 30/60/120/300s; effect correlation is ≈ −0.276 to +0.046. HUN has 2 comparable ordered edges. | Edge directions do **not** persist across regimes. This is coin-flip agreement. |
| E7 | HUN ACTIVE circular null: 648/1,000 valid contemporaneous draws and 833/1,000 valid directed-30s draws (MASTER blocker). | Valid-draw conditioning may bias that null. HUN ACTIVE is a fail regardless. |

### 0.2 Coverage facts from accepted 004A.2 (usable canonical conditions)

| Regime | PRE_ELECTION | ACTIVE_RESULTS |
|---|---|---|
| hungary_election | 2 (both SEAT_COUNT_OR_RANK) | 2 (both SEAT_COUNT_OR_RANK) |
| peru_first_round | 11 (9 presidential result, 1 House, 1 Senate) | 40 (22 presidential result, 9 House, 9 Senate) |
| peru_runoff | 5 (presidential result) | 23 (15 presidential result, 2 House, 6 Senate) |
| colombia_first_round | 9 | 23 (17 first round, 4 result, 2 qualification/pair) |
| colombia_runoff | 3 | 4 |

Three inferences follow:

1. **HUN PRE "surviving BH" rests on at most two conditions** — effectively a single ordered pair. It cannot carry a family-level claim.
2. **Peru has no usable first-round or qualification market**, so staged-migration logic cannot be tested in the discovery data at all.
3. **Colombia is absent from the handoff and presumably sealed.** It is the only family with usable first-round → qualification → overall structure.

---

## 1. Central interpretation of E1 — why it is not a leadership result

A circular-shift null keeps each series' own autocorrelation and destroys cross-series alignment. Block permutation does the same at block level. Consider data with **no lead at all**: two markets react to a common shock at the same instant, but the target is quoted sparsely and "catches up" at its next update. A directed statistic (predictor at t, response after t) then exceeds both nulls, because contemporaneous alignment plus target staleness is exactly what the nulls destroy. This is the Lo–MacKinlay non-synchronous-trading mechanism (dossier §6, T-5).

So E1 establishes *dependence*, not *direction* or *information transmission*. E6 — sign agreement ≈ 0.5 between PRE and ACTIVE — is what one expects if the directed statistic mostly reflects asynchrony and common shocks rather than stable economic leadership.

Every retained hypothesis therefore either **decomposes E1**, or is **built on a mechanical or conditional probability relationship** fixed before seeing 004B. None promotes an edge.

---

## 2. Ranked research priority

| Group | ID | Name | One-line reason |
|---|---|---|---|
| **PRIMARY** | H1 | Freshness / update-time decomposition of the directed response | Gate: decides whether E1 is information or asynchrony |
| **PRIMARY** | H2 | Mechanism-predicted direction asymmetry | Tests whether dependence has the economic direction the probability graph predicts |
| **PRIMARY** | H3 | Cross-institution transmission vs common factor (Peru ACTIVE) | The only regime and graph with enough usable conditions to identify anything |
| **PRIMARY (live-gated)** | H4 | Polymarket → SIG propagation on EXACT mappings | Most economically plausible Cup mechanism; untestable historically, so it must be preregistered now for live |
| **SECONDARY** | H5 | Staged conditional migration (first round → qualification → overall) | Strong a-priori structure; zero discovery coverage; sealed-family test only |
| **SECONDARY** | H6 | Executable partition residual convergence | Mechanical, cheap; likely a diagnostic, not alpha |
| **SECONDARY** | H7 | External-clock reaction-latency ordering in ACTIVE | Clean identification device; single ACTIVE family |
| **REJECTED** | R1–R9 | See §4 | |

---

## 3. Hypotheses

### H1 — PRIMARY — Freshness / update-time decomposition of the directed price response

- **Economic mechanism.** M3 (latent swing reaching stale constituents) and M8 (attention staleness) predict that information reaches some markets later. The alternative — non-synchronous observation (T-5) — predicts the same directed statistic with no economic lag. The two separate on a single observable: **whether the target had a fresh quote update inside the lookback**. If a genuinely fresh target still under-reacts and then moves, that is information lag. If the effect exists only for targets that had not updated, it is catch-up.
- **004B evidence.** E1 (aggregate dependence survives nulls that do not remove asynchrony); E6 (unstable directions); E3 (only ~10.7% of price→price cells available — availability is liquidity-selected, which is where staleness varies most).
- **Claim regime.** Separate claims `H1__PRE_ELECTION` and `H1__ACTIVE_RESULTS`. HUN ACTIVE excluded (E1 fail + E7 blocker). HUN PRE reported but non-inferential (≤2 conditions).
- **Applicable market family.** All usable related pairs in the frozen 004B inventory. No new pairs.
- **Predictor.** Same as the canonical 004B `PRICE_STRUCTURE` predictor (source logit move over the 30s lookback), unchanged.
- **Target.** Same 004B response. Estimated in three strata, preregistered:
  - (a) target had ≥1 fresh book update in (t−30s, t];
  - (b) target had no update in that window;
  - (c) the target's own **update-time** clock: response measured over the target's next *k* updates rather than clock seconds.
- **Expected direction.** Information-lag hypothesis: stratum (a) effect > 0, and effect in (c) > 0. My prior expectation (not the hypothesis) is that most of E1 sits in (b).
- **Permitted horizons / parameter family.** Canonical 30s primary. 60/120/300s as preregistered sensitivities, consistent with 004B. Freshness window = the 004B lookback. *k* ∈ {1, 3} updates, fixed ex ante. No other tuning.
- **Null hypothesis.** H0: aggregate directed effect in stratum (a) = 0, and in update-time (c) = 0.
- **Strongest alternative explanation.** Pure asynchrony: the whole effect is target catch-up (b), with fresh targets showing none.
- **Falsifier.** Stratum (a) effect ≤ the preregistered smallest meaningful effect, with an equivalence test (TOST) passing, while (b) carries the aggregate. Or the update-time effect (c) is null.
- **Negative controls.**
  - Same decomposition using the 004B circular-shift null (effects must vanish in all strata).
  - Matched-liquidity unrelated pairs (same volume, spread band, price band, regime) — dossier §9.6.
  - 300s-delayed predictor (003 control, retained).
- **Required ablations.**
  - Remove mechanical siblings (complements, partition members) from predictors.
  - Exclude tail targets (<3% / >97%) (T-10/T-11).
  - Probability-space co-primary alongside logit.
  - Drop the V1/V2 splice hour for HUN/PER first round (T-27).
- **Minimum evidence scope.** `WITHIN_EVENT_SUPPORTED` in PER first round and PER runoff separately. For any cross-family claim, `CROSS_FAMILY_FORWARD_SUPPORTED` on the sealed family.

  > **Chronology constraint.** Under 004A.2 forward-family rules, a COL holdout may train only on data before 29 May. So HUN and PER first round may train; PER runoff (5–10 June) may not.

- **Execution / microstructure risk.** If (a) survives, the tradable quantity is the fresh target's *executable* repricing. Fresh, liquid targets are exactly where the lag window is shortest and competition highest.
- **Difference from EXPERIMENT-003.** 003 LEADLAG-001 tested an equal-weight mean of related moves on a 30s grid with a 5s lookback, no freshness conditioning and no update-time clock. It could not distinguish asynchrony from information. H1 is a decomposition of an existing positive dependence result, not a new signal.

### H2 — PRIMARY — Mechanism-predicted direction asymmetry

- **Economic mechanism.** If dependence reflects information flow along the probability graph, the dominant direction should be fixed a priori by where hard information arrives first:
  - constituent/seat-level → headline during ACTIVE (M2, pivotality);
  - headline/aggregate → constituent during PRE (M3, common swing revealed first in the most-attended market).

  Under pure common-factor + asynchrony, forward and reverse aggregate statistics are equal in expectation once staleness is matched.
- **004B evidence.** E1 (dependence exists); E6 (edge signs flip between regimes — consistent either with regime-specific direction, which H2 predicts, or with noise, which H2 is designed to expose). **[PENDING S/C]** edge-level direction tables.
- **Claim regime.** `H2__PRE_ELECTION` (predicted: aggregate → constituent) and `H2__ACTIVE_RESULTS` (predicted: constituent → aggregate), as separate claims with opposite predicted signs. This turns E6's regime instability into a falsifiable prediction instead of a nuisance.
- **Applicable market family.** Peru: presidential result ↔ chamber (House/Senate) aggregates where the inventory contains them. HUN seat-count pair (descriptive only). Direction labels assigned from market family *before* reading any edge result.
- **Predictor / target.** For each unordered related pair {A, B}, compute the canonical directed statistic A→B and B→A. The hypothesis variable is the asymmetry Δ = stat(predicted direction) − stat(reverse).
- **Expected direction.** Δ > 0 in both regimes, with the predicted direction reversing between PRE and ACTIVE.
- **Permitted horizons.** 30s primary; 60/120/300s sensitivity.
- **Null hypothesis.** H0: E[Δ] = 0 within regime.
- **Strongest alternative explanation.** A **liquidity leader**: the more liquid market of each pair "leads" in both regimes, regardless of economic direction (Chordia–Swaminathan).
- **Falsifier.** Δ ≤ 0 in either regime. Or Δ is fully explained by the liquidity differential between the two legs — regress Δ on relative update intensity and spread; the mechanism label must add explanatory power.
- **Negative controls.**
  - Pairs with *randomised* direction labels (label-permutation null over pairs within regime).
  - Matched-liquidity unrelated pairs, which must show Δ explained by liquidity alone.
- **Required ablations.** Stale-matched version (H1 stratum (a) only). Exclude pairs where one leg is a mechanical sibling of the other.
- **Minimum evidence scope.** `WITHIN_EVENT_SUPPORTED` in both Peru rounds with the same sign. A cross-family claim needs the sealed family.
- **Execution / microstructure risk.** Low relevance until proven. Direction knowledge matters only if the follower is tradable within its lag.
- **Difference from EXPERIMENT-003.** 003 never tested direction asymmetry and pre-assigned all 72 edges as REFERENCE_TO_TARGET. H2 makes direction the hypothesis and ties it to regime-specific information arrival.

### H3 — PRIMARY — Cross-institution transmission vs common factor (Peru ACTIVE)

- **Economic mechanism.** M7. Presidential and legislative outcomes share ballots and the same count releases. The dossier prior is that their relationship is a shared latent state (common factor), not transmission. Transmission would require a *named reporting asymmetry*: for example, presidential tallies published before congressional tallies, so chamber markets learn from presidential prices. H3 tests whether any lagged cross-institution effect survives once the contemporaneous common factor is controlled.
- **004B evidence.** E1 (PER ACTIVE survives nulls). Accepted coverage: PER first-round ACTIVE has 22 presidential-result, 9 House and 9 Senate usable conditions, and PER runoff ACTIVE has 15 / 2 / 6. This is the only place in the discovery corpus with a multi-institution graph large enough to test anything. **[PENDING S/C]** whether cross-institution edges drive the PER ACTIVE aggregate.
- **Claim regime.** `H3__ACTIVE_RESULTS` only. PRE lacks coverage (1 House / 1 Senate usable in PER first-round PRE).
- **Applicable market family.** PER_2026: PRESIDENTIAL_RESULT ↔ HOUSE_OR_LOWER_CHAMBER / SENATE_OR_UPPER_CHAMBER. Same-party links from the frozen inventory only.
- **Predictor.** Source-institution logit move over the lookback. Added **contemporaneous** controls: the leave-target-out factor move over the *response* window, plus the 004A result-release anchors.
- **Target.** Target-institution logit move over (t, t+h].
- **Expected direction.** Transmission hypothesis: positive incremental coefficient on the lagged source move after contemporaneous controls. My prior is that it will be ≈ 0.
- **Permitted horizons.** 30s primary; 60/120/300s sensitivities.
- **Null hypothesis.** H0: incremental lagged cross-institution coefficient = 0, given the contemporaneous factor.
- **Strongest alternative explanation.** Common count releases absorbed at different speeds (M12). This is still exploitable only if the lag exceeds decision latency; it is not information transmission.
- **Falsifier.** Incremental coefficient ≤ smallest meaningful effect (TOST). Or the sign reverses between PER first round and runoff ACTIVE.
- **Negative controls.**
  - Cross-institution pairs with *different* parties (predicted sign opposite or zero).
  - Presidential-result moves predicting chamber markets with the predictor lagged 300s.
  - Schedule-shuffle placebo on result anchors.
- **Required ablations.** H1 freshness stratification. Drop tail targets. Drop minutes containing a 004A RESULTS or ADMINISTRATIVE milestone, to test that the effect is not a single discrete event.
- **Minimum evidence scope.** `WITHIN_EVENT_SUPPORTED` in PER first round, plus same sign in PER runoff (`SAME_FAMILY_FORWARD_SUPPORTED`). There is no independent-family test available for cross-institution structure (COL has no legislative usable markets). **Any positive result stays family-specific and must be labelled non-transferable to SIG.**
- **Execution / microstructure risk.** Election-night spreads, depth withdrawal and adverse selection (dossier §7).
- **Difference from EXPERIMENT-003.** 003 included same-party cross-chamber edges but pooled them into an equal-weight mean, with no contemporaneous factor control and mostly post-election holdouts. H3 is an identification test of transmission vs common factor, in the one regime with coverage.

### H4 — PRIMARY (live-gated) — Polymarket → SIG propagation on EXACT mappings

- **Economic mechanism.** M1. The contracts are economically identical, but the venues differ: real-money, deep, professional Polymarket versus play-money, endowment-constrained, bot-populated SIG. Price discovery should concentrate on Polymarket (Aktug & Torul; Ng et al.). SIG should follow with a lag, stale quotes are hit by informed bots, and the residual lag is exploitable only if it exceeds our end-to-end latency.
- **004B evidence.** None possible: DATA-001 is Polymarket-only. Indirect support only: E1 shows cross-market dependence even within one venue. MAPPING-001 gives 140 EXACT records, the only class where the identical-security price-discovery toolkit applies (dossier §6).
- **Claim regime.** `H4__PRE_ELECTION` (live, 1 Oct → 3 Nov) and `H4__ACTIVE_RESULTS` (live, 3–4 Nov), separately.
- **Applicable market family.** SIG EXACT records only. NEAR, DERIVED and NO_TRADE are excluded from the primary claim because of basis risk. DERIVED may be a secondary claim.
- **Predictor.** Polymarket executable-mid logit change over lookback L, stamped by *our* receive clock.
- **Target.** SIG executable crossing markout (future bid − current ask for BUY) over horizon h, measured from our decision time.
- **Expected direction.** Same sign as the Polymarket move, with positive net markout beyond measured latency.
- **Permitted horizons.** The dossier grid only, to be fixed at preregistration, e.g. L ∈ {5, 30}s and h ∈ {5, 30, 60, 300}s. Latency is measured, not tuned.
- **Null hypothesis.** H0: SIG executable markout conditional on Polymarket impulse ≤ 0 after costs. Plus symmetric leadership: ILS(PM) = 0.5.
- **Strongest alternative explanation.** Both venues react to the same public news. SIG bots are already anchored to Polymarket, so the lag is shorter than our loop, or the edge is crowded out.
- **Falsifier.** No positive executable markout beyond latency. Or SIG→Polymarket asymmetry ≥ Polymarket→SIG. Or the effect is confined to the nightly Super Signal refresh window.
- **Negative controls.**
  - Polymarket impulses on NO_TRADE/unmapped markets predicting mapped SIG contracts.
  - Time-reversed Polymarket reference.
  - Polymarket quote changes at liquidity-reward epoch boundaries (non-informational; T-20).
- **Required ablations.** Exclude Super Signal refresh windows. Exclude tail contracts. Report the result using the SIG mid (diagnostic) separately from the SIG executable price (primary).
- **Minimum evidence scope.** Live out-of-sample: a PRE claim needs an initial burn-in window for measurement only, then a frozen forward window. ACTIVE is a single live event, so the claim is necessarily within-event and must be labelled as such.
- **Execution / microstructure risk.** High. SIG depth at the stale quote may be one lot. Cancel returns 503 under load. Position limits apply. Crowding by other bots anchored to Polymarket. Election-night data outages (T-6/T-7).
- **Difference from EXPERIMENT-003.** 003 was Polymarket-internal and same-venue. H4 is cross-venue, between identical contracts, and venue-specific. It must be preregistered now so the live test is not designed after seeing live data.

### H5 — SECONDARY — Staged conditional migration

- **Economic mechanism.** M6. P(win) = Σ_pairs P(pair qualifies) · P(win | pair), and P(win) ≤ P(qualify). After first-round counts, overall-winner markets should move as the qualification-probability change implies, weighted by head-to-head strength. That can be **opposite in sign** to the candidate's own first-round move — for example, when a weak rival qualifies instead of a strong one.
- **004B evidence.** None in discovery. PER has no usable first-round or qualification market (§0.2), and HUN is not staged. The motivation is purely a-priori structural. The discovery data does not argue against it.
- **Claim regime.** `H5__ACTIVE_RESULTS` (first-round night).
- **Applicable market family.** COL_2026 first round (sealed): 17 first-round, 2 qualification/pair and 4 overall-result usable conditions in ACTIVE.
- **Predictor.** Change in each candidate's P(qualify), from qualification/pair markets, over the lookback. Structural term = Δ[P(qualify) × prior conditional], where the prior conditional is frozen from pre-election overall/qualify ratios, TRAIN-only.
- **Target.** Overall-winner logit move over (t, t+h].
- **Expected direction.** The target moves in the direction of the structural term. The structural term must beat a **naive same-sign rule** (the candidate's own first-round move).
- **Permitted horizons.** 30/60/120/300s, primary 60s. This must be declared now, before any look at COL.
- **Null hypothesis.** H0: the structural term adds nothing over the naive same-sign rule plus target own-lag.
- **Strongest alternative explanation.** Both react to the same count releases. Or head-to-head beliefs themselves change on the night, making the frozen conditional wrong.
- **Falsifier.** Incremental power over the naive rule ≤ smallest meaningful effect. Or the bound P(win) ≤ P(qualify) is violated executably and does not close (mechanism absent).
- **Negative controls.** Candidates whose P(qualify) was <3% or >97% before the count, so there is no information to migrate.
- **Required ablations.** Drop the 97%-counted RESULTS_MILESTONE minute. Freshness stratification (H1).
- **Minimum evidence scope.** Test on sealed COL only, as a single-family confirmatory test. PER cannot contribute. The result is `WITHIN_EVENT_SUPPORTED` at best — hence SECONDARY.
- **Execution / microstructure risk.** Qualification markets are thin; 2 usable conditions.
- **Relevance to SIG.** Weak: U.S. midterm staging exists only via runoff triggers and the top-two/RCV races that MAPPING-001 marks NO_TRADE.
- **Difference from EXPERIMENT-003.** 003 treated first-round ↔ overall as a generic indirect lead/lag edge with the same sign. H5 replaces the sign assumption with the conditional-probability identity and tests it against the naive rule.

### H6 — SECONDARY — Executable partition residual convergence

- **Economic mechanism.** M4. Within an exhaustive negative-risk partition, target ≈ 1 − Σ(other members). An *executable* residual beyond combined half-spreads should close, and the less-liquid side should do the closing.
- **004B evidence.** None directly. **[PENDING S/C]** — Structural/Core may contain partition-consistency evidence. For context: 003 RV-001 (naive equal-weight indirect logit gap) was weak, and LOO-PRICE was a diagnostic only.
- **Claim regime.** PRE and ACTIVE as separate claims.
- **Applicable market family.** Neg-risk candidate partitions (PER presidential result, COL first round), where all named members plus "Other"/placeholders are usable. HUN seat bins are excluded: most were not recorded (DATA-001 hole 2).
- **Predictor.** LOO partition residual computed at *executable* prices on all legs.
- **Target.** Target executable repricing toward the partition-implied value.
- **Expected direction.** Convergence when |residual| exceeds costs.
- **Null hypothesis.** H0: no convergence beyond spread bounce.
- **Strongest alternative explanation.** Mid-in-spread artefact. A non-exhaustive partition (placeholders). Asynchronous legs.
- **Falsifier.** Executable residuals never exceed costs, or convergence is fully explained by spread bounce.
- **Negative control.** The same residual computed on a deliberately incomplete subset (should show no convergence).
- **Required ablations.** Exclude placeholder/"Other" legs; require all legs fresh.
- **Minimum evidence scope.** `WITHIN_EVENT_SUPPORTED` in ≥2 events.
- **Execution / microstructure risk.** Legging risk across many outcomes; the negative-risk adapter makes one direction cheap and the other costly.
- **Why only SECONDARY.** This is expected to be a small, rare, crowded consistency trade (Saguillo et al.). Its main value is as the Cup's DERIVED-mapping consistency monitor.
- **Difference from EXPERIMENT-003.** 003 used naive indirect levels. H6 uses an exact identity at executable prices.

### H7 — SECONDARY — External-clock reaction-latency ordering (ACTIVE)

- **Economic mechanism.** M12 plus M8. At externally timestamped result anchors (004A first-meaningful-results and RESULTS/ADMINISTRATIVE milestones), leaders react first. The latency ordering across market families identifies source vs sink without relying on cross-correlation.
- **004B evidence.** E1 (PER ACTIVE survives; HUN ACTIVE does not). 004A anchors exist for both Peru rounds.
- **Claim regime.** `H7__ACTIVE_RESULTS`.
- **Applicable market family.** PER presidential vs chamber markets. COL (sealed) for confirmation.
- **Predictor.** Market family label and pre-anchor liquidity.
- **Target.** Time to first ≥ preregistered logit move after each anchor.
- **Expected direction.** Presidential/headline markets react first in ACTIVE. Latency is not fully explained by liquidity.
- **Null hypothesis.** H0: latency ordering is independent of family after conditioning on liquidity.
- **Strongest alternative explanation.** Liquidity alone orders latency.
- **Falsifier.** The family effect disappears given liquidity. Or ordering reverses between PER rounds.
- **Negative control.** Shuffled anchor timestamps within the regime.
- **Required ablations.** Anchor-by-anchor leave-one-out, because there are few anchors.
- **Minimum evidence scope.** Within-event; very few anchors, hence SECONDARY.
- **Execution / microstructure risk.** Descriptive/identification use primarily.
- **Difference from EXPERIMENT-003.** 003 had no external clock.

---

## 4. Rejected

| ID | Candidate | Reason for rejection |
|---|---|---|
| R1 | Trading or testing individual directed price edges from the 004B matrix | E2: edges are descriptive with no null. E6: PRE/ACTIVE sign agreement 0.486–0.556 and effect correlation ≈ −0.28 to +0.05. Selecting "strong" edges is post-selection (T-15) |
| R2 | `depth_imbalance_change` → price | E4: not signed flow. 003 MICROSTRUCTURE was null. Polymarket L1 is contaminated by liquidity-reward quoting (T-20). Depth is roughly uniform across 10 levels (Dubach), so top imbalance is a weak state variable. Descriptive only (E2) |
| R3 | Liquidity → price and BBO/depth/fill activity → price as *directional* signals | Activity is unsigned, so it predicts volatility, not direction. No economic reason for a sign. Descriptive only. A volatility/spread hypothesis may belong to another lane |
| R4 | Any HUN ACTIVE claim | Fails both nulls (E1). Null shortfall is an open blocker (E7). 2 usable conditions |
| R5 | HUN PRE as independent-family support for anything | 2 usable conditions — effectively one pair. Survival cannot be read as a family effect |
| R6 | HUN ↔ PER replication language | E5: insufficient comparable topology |
| R7 | Transferring PRE-estimated coefficients into ACTIVE (or the reverse) | E6 directly contradicts regime stability. 004A.2 forbids pooling |
| R8 | Fill-activity or participant-identity flow as a 004C hypothesis in this lane | Unsigned (E4). 003 PARTICIPANT invalidated. Exchange/adapter-contract contamination. Identities unavailable on SIG (M13) |
| R9 | Historical constituent → chamber-control pivotality (M2) | No district→chamber topology in discovery data. HUN seat bins largely unrecorded. Defer to live SIG. It is testable only once live Polymarket + SIG data exist |

---

## 5. Preregistration notes for MASTER

1. **Smallest meaningful effect.** Every hypothesis needs a preregistered smallest meaningful effect and an equivalence test. 003 lacked one, and without it the H1/H3/H5 falsifiers cannot fire.
2. **Separate FDR families.** H1–H3 and H5–H7 historical claims form one lane-specific FDR family per regime. H4 is a separate live family.
3. **E7 is a MASTER decision.** My set does not depend on HUN ACTIVE, so any disposition of the valid-draw shortfall leaves these hypotheses unchanged. One flag: if the invalid circular shifts are non-random, the same issue may affect PER nulls at a smaller rate. The null audit should report valid-draw fractions for every regime.
4. **Sealed-family chronology.** COL forward-family folds may train only on data before 29 May 2026, so HUN and PER first round are allowed and PER runoff is not.
5. **Needed from the remote before final preregistration.** Structural/Core results, the five review artefacts, and the frozen spec. Fields marked [PENDING S/C] must be completed or confirmed. The PRIMARY/SECONDARY/REJECTED grouping is not expected to change unless Structural/Core shows (a) partition-consistency evidence (could promote H6) or (b) freshness-conditioned effects already computed (could merge H1 into 004B's record).

```text
OPUS 004C HYPOTHESIS SET READY FOR MASTER PREREGISTRATION
```
