# ASTRA 004C HYPOTHESIS SET

**Independent proposal for MASTER preregistration · 27 September 2026 · v1.0**

## Decision

Prioritise **conditional response mechanisms**, not the largest 004B directed edges. Two primary questions survive the evidence review: whether pre-existing target quote age changes the subsequent response to family price innovations, and whether family activity predicts genuine target quote renewal beyond its own clock and common activity. Two secondary questions concern distributed source participation and spread-dependent response timing.

These are falsifiable prospective hypotheses, **not discoveries, alpha claims, or authorisation to unseal data**. No new response model, threshold search, experiment or trading evaluation was run. The proposed primary price endpoint is **magnitude**, not signed direction: 004B's aggregate absolute correlations and unstable edge signs do not justify assigning transferable directional signs. A successful timing or magnitude result must retain that label.

| Priority | ID | Question | Proposed confirmatory regime |
|---|---|---|---|
| PRIMARY | ASTRA-C01 — Age-conditioned family response | Does target age modify the incremental magnitude response to a family price innovation, beyond additive price/activity/age controls and asynchronous common news? | PRE_ELECTION |
| PRIMARY | ASTRA-C02 — Conditional quote renewal | Does family update intensity interact with target inactivity to predict a genuine next quote change, after common intensity and the target's own renewal process? | PRE_ELECTION |
| SECONDARY | ASTRA-C03 — Distributed co-shock response | At a given aggregate shock size, does participation by several family members add predictive information beyond additive source effects and one dominant source? | PRE_ELECTION; dormant unless graph/support gates pass |
| SECONDARY | ASTRA-C04 — Spread-dependent response delay | Does pre-shock target spread reduce short-horizon responsiveness to family innovations after age, activity, own volatility and quote-repair controls? | PRE_ELECTION |
| SECONDARY | Separate ACTIVE versions of C01–C04 | Do the same preregistered mechanisms work within ACTIVE_RESULTS? | Separate coefficients, tests and support gates; no pooling with PRE |
| REJECTED for this battery | R01–R06 below | Global factor reversion, semantic-gap convergence, sparse depth propagation, fitted causal network/cascade claims, unsupported trade/participant signals, and unfrozen threshold/lag searches | No confirmatory tests allocated |

C03 is a **reserve specification**, not a recommendation to rescue sparse topology. If discovery-only feasibility cannot support it, its reserved test slot remains inactive and no smaller, better-performing graph is substituted.

## 1. Independence, provenance and acceptance boundary

The independent prior is `ASTRA_PRE_004C_QUANT_RESEARCH_DOSSIER.md`, frozen before 004B inspection at SHA-256:

`3c8403c8a69d46486aa60074a89349afc1625abb02db47f694ad14f183fd7a93`

Its bytes remain unchanged. The paused checkpoint also remains unchanged; its earlier access blocker is historical. This document is the separate evidence-conditioned addendum it requested.

Evidence was read from `DanielCWeng/predictions-cup` at main commit **`4e094d13b06d9e558cbc404cc4d472316d7d5bbb`**, including the 004B integration merged through PR #30. The integration branch was `5b49d2ad13ea538fe9222a41643af7cf7d477c86`; PR #30's merge commit is `7fd19a1e4259e01a30d172475184d15cf45f8725`. The originally supplied short ref `37f2b18` still did not resolve; I used the now-present canonical handoff and integrated audit package, rather than assuming that ref had been verified.

The frozen empirical implementation is recorded by both lanes as `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`, tree `462b4589a13782662b69db879f77e8ba22d8a9ab`. I independently reproduced the discovery-spec SHA from the integrated manifest using the implementation's canonical JSON serialization, including its trailing newline:

`409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`

I inspected the frozen Kaggle v3 output directory `/home/ec2-user/experiment004b_v3_download`, read the summary and selected frozen output rows, and independently recomputed **all ten** hashes recorded in `kaggle_run_summary.json`, including the 149,537,341-byte full response matrix. All matched. The summary records `phase=complete`, 585,900 response rows, 46,035 contemporaneous rows, 20 null rows, and `sealed_event_empirical_loads=0`. Hashing the full matrix is not a claim that every response was interpreted or independently recomputed.

No raw sealed-event data, sealed empirical outputs, Opus work, other researchers' hypothesis documents or 004C battery outputs were opened. Only the permitted 004B discovery evidence and relevant implementation were used. Metadata/tree listings and inherited project status are not empirical validation. No shared EC2 branch, worktree, experiment, or running job was changed.

**MASTER caveat retained:** the B handoff still explicitly requests disposition of the Hungary ACTIVE circular-null shortfall: 648/1,000 valid contemporaneous draws and 833/1,000 valid directed draws. PR #30 is merged, but its body repeats that caveat; no disposition appeared in its issue comments or formal reviews. A merge is not treated as a scientific waiver. This proposal uses no affirmative Hungary ACTIVE null claim. The shortfall does not invalidate every other event/regime's independently reported statistic, and it is not silently repaired here.

The candidate set is ready for MASTER to preregister. Execution remains conditional on an implementation manifest, the stated support/null gates, and a documented determination of which evaluation events remain untouched by the research team. I can attest to my own blindness, not certify all other workers' access.

## 2. Evidence ledger

All repository references below are pinned to `4e094d13b06d9e558cbc404cc4d472316d7d5bbb`. Frozen CSV names refer to the hash-verified v3 directory above. Exact event/regime/resolution keys identify rows, avoiding reliance on incidental row ordering.

| Key | Exact source and selector | Observation | Consequence for 004C |
|---|---|---|---|
| E01 | `review/MASTER_HANDOFF_004B_B.md`; `review/dynamics_stats_audit.json`, `checks`, `response`, `hash_checks`; frozen run summary | 585,900 unique preregistered cells; frozen grids retained; unsupported cells explicit; no sealed loads reported | Valid discovery inventory, not 585,900 statistically confirmed effects |
| E02 | `null_results.csv` and `review/null_audit_summary.csv`; `DIRECTED_30S_RESPONSE_MEAN_ABS_CORR`, resolution 30 | HUN PRE observed 0.0305821; circular/block means 0.0053370/0.0056282; both p=0.000999001, q=0.00133200 | Aggregate temporal dependence motivates conditional tests; it does not select an edge |
| E03 | Same selector, Peru PRE | Observed 0.0137481; circular/block means 0.00885669/0.00896103; both p=q=0.000999001 | Small aggregate departure from these nulls; no evidence yet of extra information after common clocks |
| E04 | Same selector, Peru ACTIVE | Observed 0.0488162; circular/block means 0.0328895/0.0334988; p=0.00599401/0.00299700; q=0.00599401/0.00399600 | Separate ACTIVE question is justified, with severe support restrictions |
| E05 | Same selector, HUN ACTIVE | Observed 0.107461; p=0.345324/0.148851; q=0.345324/0.198468; circular valid draws 833 | A larger raw statistic need not beat its null. No general election-night leadership claim |
| E06 | `review/structural_core_audit.json`, `panel_coverage`, each group, key `30` | Midpoint support HUN PRE/ACTIVE 99.97%/36.39%; Peru PRE/ACTIVE 91.32%/33.83%. Full-depth support 14.62%/23.61%/12.06%/10.94% | Missingness and asynchronous observation are central to the estimand; sparse depth is not a primary feature |
| E07 | Same audit, `contemporaneous`, Peru groups, key `30`, `price_change` | PRE 45/55 unordered pairs; ACTIVE 103/780; ACTIVE mean pair coverage 0.10857 | Forty admitted ACTIVE markets do not constitute a complete covariance panel |
| E08 | Same selectors, family summaries | Peru PRE same/cross-family mean absolute correlations: price 0.03142/0.00903, BBO count 0.31647/0.06392, fill count 0.30372/0.01247. ACTIVE price 0.17240/0.02222; BBO 0.51920/0.14081 | Family-local activity and price structure motivate C01–C03; these are unadjusted associations |
| E09 | Same audit, Peru ACTIVE `30`, `depth_update_intensity` | Same-family 0.51703, cross-family 0.55675; all-market mean 0.54132 | Archive cadence can be more coherent than economic relationships; snapshot timing is a mandatory placebo |
| E10 | `review/response_audit_summary.csv`; resolution=30, horizon=30 | Peru PRE available directed pairs: price 90/110, BBO activity 100/110, imbalance 72/110; ACTIVE 206/1,560, 585/1,560, 84/1,560 respectively | Activity and price are available enough to motivate small conditional models; coverage is not significance |
| E11 | `review/dynamics_stats_findings.md`, full-grid coverage; frozen response summaries | Full-grid price 6,254/58,590; imbalance and liquidity each 3,351/58,590; BBO/depth activity each 14,350/58,590; fill variants each 9,350/58,590; venue-trade variants unavailable | Do not compare these families as though they share one sample. No non-price cell-level FDR results exist |
| E12 | `review/stability_audit_summary.csv`; Peru, price-to-price, resolution 30 | At horizons 30/60/120/300: 72 shared ordered edges; sign agreement 0.4861/0.5278/0.5556/0.4861; effect correlation −0.0552/−0.0805/−0.2762/+0.0456 | Do not transport fitted edge signs or infer PRE→ACTIVE stability |
| E13 | `review/structural_core_audit.json`, `structural_graph`; frozen residual rows | 0 mechanical edges, 10 semantic edges, 827 unverified, 0 hard identities. Semantic edges HUN=0, Peru PRE=1, ACTIVE=9 | A semantic connection is not an arbitrage constraint or equilibrium gap |
| E14 | `structural_residuals.csv`; Peru PRE,30, pair `0x83b661…100e4` / `0xa44566…f3bc` | 7,199 overlapping bins; 331 fixed-2σ changes; subsequent mean absolute-gap change +0.000422961 | Widening, not automatic convergence. Do not select the one contrary edge from ACTIVE |
| E15 | Same CSV, Peru ACTIVE,30; four usable rows | 78 large changes total; median next absolute-gap change +0.000185185; individual signs mixed. Median absolute spread association 0.698759 versus depth 0.161884, activity 0.132871 | Spread deserves a conditional measurement/friction question, not a causal liquidity conclusion |
| E16 | `factor_spectrum.csv`; Peru PRE,30,ranks 1/2 | 11 nominal markets, 9 varying; 366 complete bins; rank-1 EV 0.181505, rank-2 cumulative 0.324186, effective rank 8.64022 | Evidence opposes a dominant event-wide factor. High effective rank does not prove stable economic latent factors |
| E17 | Same CSV; HUN and Peru ACTIVE, all frozen resolutions | HUN has two markets, insufficient cross-section; Peru ACTIVE has zero complete all-market bins | Do not manufacture ACTIVE factors via a retrospectively selected subset |
| E18 | `null_results.csv`, matched controls; runner `matched_controls` | Four descriptive matched-control rows, one PRE with no same-family match; no empirical p-values | Insufficient basis for claiming network/semantic specificity already passed a realistic graph null |
| E19 | Runner `quote_series`, `binned_activity`, `build_panel` | Quote age is age of latest observed book-change record; BBO intensity counts rows, including repeated same-state observations; valid BBOs are interior and conflicts invalidate a timestamp; depth is current-bin only | Genuine quote-change counts and time since price change must be separately defined. Raw record intensity is not information intensity |
| E20 | Runner `null_rows`, `matrix_stat`, `bh` | Directed null transforms source changes while keeping future target changes fixed; masks move with transformed source; available edge set can change. BH has four price-structure p-values per event/regime: two statistics × two nulls | BH values independently reproduced from the audit CSV. This remains a test of timing structure, not a conditional no-transmission test preserving shared news and observation masks |

Important distinction: the frozen `stability_summary.csv` is a **contemporaneous unordered-pair** comparison (Peru 36 shared pairs). The B lane's `stability_audit_summary.csv` supplies the directed comparison (72 ordered edges). Their different counts and correlations are not conflicting estimates of the same statistic.

### Frozen output integrity record

| Output | Independently matched SHA-256 |
|---|---|
| `contemporaneous_structure.csv` | `2a17b2bd3f58a14fd685c923ea737c3485a1e8257d874aeff0c3d6e406e7d191` |
| `factor_spectrum.csv` | `8c0f51bc7819e72ba805bea7a0d74a1bfd0d2007720a1449593bb9c62faf6af6` |
| `null_results.csv` | `92ac620f0d3b200fc383a85ae32f1727ac4700b4266e63e53a5b470a47edc77a` |
| `panel_summary.json` | `925de202ec390fcae3c7a650b2b9fefb5f7c71df178b0d5e8b6b3a28be1fff8a` |
| `response_matrix.csv` | `3c0993275e0f2fca171650f497a7c729aaa7984b7081f7f5df9e6e633bc5e357` |
| `response_matrix_external.json` | `76c1774ae5fdacc19283440b5915cb74b2943feafc4b56c53697e1da7471eb16` |
| `response_matrix_full.csv` | `d7090e882d2abcbfe2a7aef0e8aa3bf7e8b84478605a9ea7e9faf7280795fdea` |
| `stability_summary.csv` | `0843640d61345f28a695baf949eea6413410bf78e36c1cabdc21048ee3431bb7` |
| `structural_residuals.csv` | `922904afaea9795a823f073157ae5b5fcd8cc76c36e8a2b6f5b5f37ad4b4dc67` |
| `structure_summary.json` | `d2dbb7ef07f07601f86ad01d39cb7c87a8da3ed0235807da1d195a389d69ea2b` |

## 3. What changed from the independent prior

| Prior mechanism | Evidence-conditioned disposition |
|---|---|
| M01 asynchronous adjustment + M09 state-dependent delay | Retain as C01; separate age heterogeneity from generic lead–lag and keep observation-clock explanation live |
| M02 directed book-pressure response | Reject as a primary test: sparse true depth, no aggressor OFI, no conditional or cell-level null evidence |
| M03 update excitation | Retain C02's small renewal model; withhold multivariate Hawkes and causal excitation language |
| M04 latent-factor residual correction | Reject event-wide version for this battery; no stable target-excluded factor established |
| M05 family-local propagation | Narrow to C03's fixed family aggregation, with a genuine additive baseline and mandatory graph controls |
| M06 liquidity conditioning | Retain only pre-shock BBO spread as C04; no depth substitute or post-shock liquidity predictor |
| M07 activity conditioning | Combine with C02, rather than count an overlapping mechanism as a second independent discovery |
| M08 magnitude without direction | Use as C01/C03's explicit endpoint; avoid inventing transported signs from mean absolute dependence |
| M10 structural residuals | Reject mechanical convergence and uncalibrated semantic-gap mean reversion |
| M11 location versus uncertainty surface | Reject for now: no verified, adequately overlapping threshold surface in accepted 004B evidence |

The prior's 22 questions resolve as follows. Units/masks/horizons and multiplicity are identifiable (E01,E06,E10,E20); only aggregate 30s structure has empirical p/q support (E02–E05). Timing beyond collector uncertainty, age attribution, conditional signed marks, own-market adjustment, burst concentration and causal family specificity are **not measured by 004B**. Feature meanings are identifiable (E19). Exact constraints are not established (E13). Stable factors and target-excluded factor identification are unsupported (E16–E17). Stale common-state versus residual correction is unresolved (E14–E15). Joint interaction support must be assessed without searching thresholds. PRE/ACTIVE stability is weak on shared pairs (E12), and HUN/Peru topology is not comparable. The canonical slice is a predeclared statistic, not independent replication. P-value resolution is 1/1,001 for complete-draw nulls, adequate for the four-test local BH family but not evidence for an edge-level family of thousands. Sparse depth, absent venue trades, factor failures and limited event counts remove candidate classes. The supplied run/audit record reports an intact seal; no seal was opened here.

## 4. Common prospective protocol

This section is part of each hypothesis card. It specifies a restrained **proposal**, not parameters estimated or optimised in this research. MASTER should freeze its adopted version and implementation hash before evaluation.

### 4.1 Universe, clocks and claim units

Use the frozen exact-Yes canonical condition axis and regime-specific eligibility. Keep PRE_ELECTION and ACTIVE_RESULTS separate. Exclude ELECTION_DAY_PRE_RESULTS and late-count diagnostics. Exclude complements, direct duplicates and failed-closed conditions; do not infer hard identities from broad family labels.

The graph is a fixed **market-family membership graph** from the registry, not the empirical 004B response network. For target j, N(j) is other admitted conditions with the same registry family. This is a coarse information-neighbourhood hypothesis, not verified payoff equivalence. Freeze membership and duplicate exclusions from contract metadata before outcomes. Do not choose sources by 004B correlation, significance, centrality or later performance.

Hungary's eligible universe consists of two SEAT_COUNT_OR_RANK conditions. Peru PRE has nine PRESIDENTIAL_RESULT conditions and one each in HOUSE_OR_LOWER_CHAMBER and SENATE_OR_UPPER_CHAMBER; ACTIVE has 22/9/9 respectively. These are discovery registry counts, not promised sealed-event coverage. Hungary cannot support a many-source graph or a leave-neighbourhood-out common-information control. Treat it as a limited two-book diagnostic, not independent confirmation of those mechanisms.

Sample on the inherited 30s grid, with the same right-edge-minus-1ns as-of convention and 300s freshness ceiling. The ceiling is an inherited validity rule, not a state threshold to optimise. All start/end quotes must be valid. A missing quote, an invalid conflict or a logging outage is unavailable, not a zero move or a no-update event.

For records at the same observable timestamp, collapse identical BBO states; conflicting states are invalid until an unambiguous observation. Define a **genuine quote change** as a valid bid or ask price change from the preceding valid observed state, with no intervening known invalidity/outage. Repeated identical records do not count. No claim is made about exchange-side event order that the archive cannot establish.

Maintain three separate clocks: latest observable record age, time since a genuine BBO change, and time since midpoint change. They need not agree. If no valid preceding state/change is observed within authorised history, duration is unknown rather than zero. Never bridge invalidity to invent a genuine change. Prices are midpoint probability units. Logit returns are a diagnostic only; do not clip probabilities or add a second primary price transform.

Price-response inference is explicitly conditional on valid observed start/end quotes; it is not an unconditional claim about unavailable markets. Report future-endpoint availability by predictor/state and retain the same masks in nulls. If differential censoring cannot be represented by the declared observation model, withhold the broader claim rather than select only eventual responders. The inferential unit is an event-time block containing the entire eligible cross-section, not each pair-row independently. Multiple targets sharing sources and outcomes are dependent. Sealed Peru runoff is related to Peru discovery; Colombia first round and runoff also share a country/election cycle. Report country-family limitations explicitly. Three event files are not three unrelated economic replications.

### 4.2 Fixed variables and baselines

Let r_i(t)=p_i(t)−p_i(t−30). Use valid pairs of endpoints only. Let I_j(t) be the available members of N(j), with n_j(t)=|I_j(t)|. Define E_j(t)=mean over I_j(t) of |r_i(t)|. Missing sources are not zero-filled. Record the available source mask, count and fraction explicitly; contrasts use the same eligible observations in baseline and challenger. A dynamic available basket is a conditional estimand, not an assertion that unavailable sources were inactive.

Define pre-shock age A_j(t)=log(1+a_j(t−30)/30), and pre-shock spread S_j(t)=ask_j(t−30)−bid_j(t−30). Thus these states precede the source-innovation interval (t−30,t]. There is no searched age, spread or shock cutpoint. Count transforms use log(1+count); prices/spreads remain in probability units. Predictor scaling, if needed for regularisation, uses discovery-training constants only.

Baseline B_price includes: intercept; p_j(t−30), p_j(t−30)(1−p_j(t−30)); signed r_j(t), |r_j(t)|; trailing 300s mean absolute own return; A_j; S_j; time since genuine target quote change (log transform above); target trailing-30s and trailing-300s genuine-change counts; E_j; mean source pre-shock age; family update intensity; outside-neighbourhood mean absolute price change and update intensity; available-neighbour count/fraction; and linear elapsed regime time. Additive terms only, except the stated probability-variance control. Use the same transformations across baseline/challenger. Collinear nuisance columns are dropped by a fixed QR tolerance/order, never by significance.

Outside-neighbourhood means exclude target, its duplicate payoff representations, and N(j). If no varying outside source is available, mark the external common-information control unavailable: such rows can support a two-book diagnostic but cannot support the stronger family-specific/common-information-adjusted claim. Do not silently replace that control with the target or with the tested source itself.

B_clock is a discrete-time Bernoulli hazard model for at least one genuine target quote change in (t,t+30], with target record/change ages, own trailing-30s/300s counts, probability level/variance, spread, own movement/volatility, family and outside-family activity main effects, source count/mask controls and linear regime time. A time-bin is at risk only while observation is demonstrably live. Right censor at loss of observability or regime end, not at an inferred successful response. Collector liveness must come from capture/coverage evidence; silence alone does not establish an observed zero.

Use ordinary linear regression for the magnitude conditional-mean baseline and logistic regression for the hazard. If the fixed nuisance design is unidentifiable or logistic separation prevents a finite fit, the default is INCONCLUSIVE, not a broad model search. A fixed ridge logistic/linear implementation may be adopted in the final preregistration instead, but its scaling, penalty and intercept treatment must be fixed before any sealed access. This document does not imply that a penalty has already been chosen or tested.

Neither model uses a learned latent factor. Family/outside-family aggregates are observable nuisance proxies. They are not asserted to span all common information. Their inadequacy is addressed by the stronger null and cautious claim language below.

### 4.3 Horizons, fitting and confirmation

The sole primary horizon is **30s**, inherited from the predeclared 004B calibration slice, not selected from its largest response. **60s and 120s** are fixed shape diagnostics. A first genuine target update within 120s is an additional censored timing/mark diagnostic. No 1s, 5s, 15s or 300s performance sweep is allowed in this proposed confirmatory family. No diagnostic horizon can replace a failed 30s primary endpoint.

Fit separately by regime using discovery events only. Evaluate discovery feasibility on a fixed chronological first-two-thirds / last-third split within each event/regime, purging labels across the boundary and embargoing 300s. No tuning grid or winner selection is needed for the one-interaction models. Freeze coefficients and transforms after a prescribed refit on eligible discovery data. New sealed market IDs receive no ID-specific fitted coefficients. No sealed outcomes enter coefficients, nuisance transformations, graph selection or calibration.

The eventual sealed evaluation is a test of that **frozen procedure**. For every eligible event/regime, report frozen-baseline versus frozen-challenger paired loss: squared error for magnitude, Brier score for update probability, plus calibration diagnostics. Negative probability predictions are not permitted for hazard; the logistic link enforces this. The linear magnitude model is a conditional-mean projection, not a full probability distribution.

Separate prediction from mechanism checks. A positive discovery interaction coefficient alone is insufficient. Confirmation requires the expected signed interaction contrast in evaluation, positive held-out loss improvement, valid dependence-aware uncertainty, and survival of the relevant conditional null/control gates. A failure can be substantive (wrong sign or deterioration) or inconclusive (insufficient support/power/null adequacy); do not equate non-rejection with proof of zero.

No universal economic minimum effect is invented from 004B. Before unsealing, report discovery-only simulation power and the minimum detectable interaction/loss gain under the frozen sample plan; if the scientific claim is smaller than resolvable uncertainty, retain INCONCLUSIVE. Any numerical minimum-effect or equivalence margin MASTER wants must be documented before evaluation, not reverse-engineered from it.

### 4.4 Nulls and uncertainty

**N0 — nested no-interaction/no-increment model.** Set the card's added term to zero, retaining all main effects, target own dynamics, shared observable activity, probability-dependent variance and observation masks. Test the incremental term and frozen prediction difference; do not test correlation against zero while omitting the baseline.

**N1 — time-dependent conditional null.** Generate null responses under the restricted model, using centred residual vectors resampled jointly across the entire cross-section in contiguous 300s blocks within the same event/regime. For binary hazards use a restricted conditional event-process model with target renewal history and common intensity rather than adding Gaussian residuals to a binary response. Retain observed exposure/censoring and reconstruct lagged target variables consistently. Predictors/coefficients used for the confirmatory frozen forecasts remain fixed. An ordinary paired bootstrap of the unmodified observed series gives uncertainty, not an imposed no-effect null.

**N2 — common-information plus asynchronous-observation null.** Fit, using discovery only, a restrained no-cross-market model with own dynamics, time-varying common event/family shocks, contemporaneously dependent innovations and market-specific observation clocks. Cross-market lag/transmission coefficients are constrained to zero. Replay generated latent prices through the observed masks/quote clocks and rounding. For C02/C04 use a common-intensity target-renewal version preserving own duration dependence and collector batching. This null must reproduce the discovery distribution of availability, durations, own autocorrelation, synchronous co-movement and broad activity bursts without cross-excitation. Predeclare those diagnostics and evaluate fit before unsealing.

N2 is a **negative explanation**, not a claimed stable economic factor or a forecasting challenger. The factor failures in E16–E17 prohibit assuming its adequacy. If no parsimonious null can reproduce these nuisances on the available cross-section, the information-transmission interpretation is INCONCLUSIVE. Do not lower the null's standard or fit a high-rank hidden state to obtain rejection. Surviving N2 still supports conditional predictive dependence, not proof of a causal information path.

**N3 — matched graph controls**, for C03 and family-specific interpretations: outcome-independent family/edge-label randomisation preserving neighbourhood size and matched discovery activity, availability, age and probability range. Freeze matching strata/algorithm from discovery, with no matching on response. If adequate matched non-neighbour sets do not exist, fail that claim's support gate. Do not cite E18's four controls as enough.

Use at least 9,999 deterministic simulation attempts in the proposed new protocol; this is a new 004C design, not a modification of 004B. Record attempted, valid and invalid statistics for every test. Undefined draws are not silently discarded. If validity depends on the resample's changing support, fail the affected inferential gate or use a conservative predeclared bound that counts invalid draws as exceedances. Do not keep drawing until significance appears. Report Monte Carlo resolution and uncertainty.

Use a common cross-sectional time-block resampling schedule for losses and contrasts so dependence among tests is retained. The primary uncertainty block is 300s, inherited from the discovery null design; 600s is a fixed sensitivity. If residual dependence demonstrably extends beyond both, mark the interval/test unreliable rather than shopping for a favourable block length. Time-block inference is conditional on these observed events; it does not estimate a population of elections from two discovery cases.

### 4.5 Multiplicity and support

Reserve **eight mechanism/regime slots**: C01–C04 × PRE/ACTIVE. C01-PRE and C02-PRE are priority questions, not a different uncorrected family. For each slot require all relevant claim components (expected contrast, predictive improvement and conditional-null survival); use a conservative intersection-union construction, such as the maximum of valid component p-values, and then Holm familywise correction across the eight slots at 0.05. This needs valid underlying p-values; Holm does not repair a misspecified null. Dormant or unsupported slots receive no rejection, with p=1 in the declared denominator.

For a claim to replicate across eligible evaluation events, require the same expected contrast and positive frozen prediction gain in every predesignated testable event; form the slot p-value conservatively as the maximum event-level p-value. This is deliberately stricter than pooling millions of rows. Event-specific inference, if wanted, is a separate family with the full event × mechanism × regime denominator fixed before unsealing, not a fallback after failed replication. A country-generalisation claim needs evidence from both a Colombia event and Peru runoff; even then, report shared-round dependence and very few country families.

The 60/120s curves, first-update marks, bid/ask decomposition and ablations are prespecified diagnostics, not additional routes to declaration of success. If MASTER makes any diagnostic claim confirmatory, it must allocate an additional test slot before evaluation. Richer challengers are not part of the eight tests unless expressly activated and multiplicity-expanded in advance.

Minimum feasibility gates proposed independently of outcomes: full-rank residualised added feature; at least 20 observations per fitted coefficient in discovery training; at least 40 occupied, disjoint 300s time blocks per evaluated event/regime with valid baseline/challenger comparisons; nonzero residualised added-feature variation in at least 20 such blocks; and no single 300s block supplying over half of that feature's sum of squares. These are conservative design gates, not a theorem of sufficient power. They cannot be relaxed after seeing sealed results. Hazard tests additionally require both change and no-change observations and finite identifiable fits. C03 has stronger motif/peer requirements below. Passing a raw row count alone is insufficient.

Logging support, available graphs and design-matrix diagnostics may be checked after preregistration without selecting on endpoint effect. They determine TESTABLE versus INCONCLUSIVE; they do not select a new threshold, source set, horizon or model. Evaluate whole predefined event windows, retaining unsupported cells and reasons.

## 5. PRIMARY hypothesis cards

### ASTRA-C01 — Age-conditioned family response

**System/mechanism intuition.** A related family innovation may update a target whose displayed quote is older. A constant pairwise slope averages over targets that already incorporated information and those that have not. The nonlinearity of interest is innovation × pre-existing age. Observing it alone can still be common news viewed through stale quotes; that is the essential rival explanation.

**Precise 004B motivation.** E02–E04 show aggregate 30s temporal dependence. E06–E07 establish strong regime-dependent missingness under a 300s quote-validity rule. E12 defeats stable edge-sign transport. E19 confirms that observable record ages can be constructed but were not conditioned on in the response matrix. No age interaction was measured in 004B.

**Claim regime.** Primary PRE only; ACTIVE is a separate secondary slot. Claim conditional magnitude response, not directional forecasting, leadership, economic causality or profit. Hungary is only a two-book diagnostic where external common-state controls are unavailable.

**Predictor and target.** Add Z01=E_j(t)×A_j(t) to B_price, retaining E and A separately. Target Y01=|p_j(t+30)−p_j(t)|. The hypothesis is a positive incremental interaction and improved frozen prediction. Source and target states use definitions in §4; state is measured at t−30, source change ends at t, and response begins strictly after t.

**Expected temporal signature.** Larger conditional 30s adjustment after a family innovation when the target entered the source interval older. A coherent 60/120s response or first-update mark is supportive, but not required to peak at a searched lag. If all response appears only after a logging gap or first newly observed record, prefer an observation explanation. Do not infer a half-life from missing-bin-compressed paths.

**Permitted horizons/parameters.** 30s primary; 60/120s and first-update-within-120s diagnostics. One interaction, continuous log-age transform, no bins/cutpoints, no fitted lag kernel, no per-edge coefficients.

**Simplest baseline.** B_price, including own same-bin movement, own volatility, family innovation main effect, age, source age/activity, spread and outside-family common proxies. Compare on exactly the same observations.

**Richer challenger.** A two-part update-hazard plus conditional-magnitude model could decompose timing from marks. It is not justified as a primary model by 004B and remains inactive unless separately preregistered. A sign model would be a different hypothesis.

**Null.** N0/N1 with Z01 coefficient zero, and especially N2: common information with heterogeneous asynchronous observation, no cross-market transmission. Raw independent source shifts are insufficient.

**Falsifier.** Nonpositive age interaction with adequately precise uncertainty, no frozen loss gain, or replication failure falsifies the stated predictive mechanism. Reproduction by a well-calibrated N2 falsifies the stronger incremental-transmission interpretation even if raw age dependence remains. Low precision produces INCONCLUSIVE.

**Negative controls.** Activity/age/probability-matched outside-family sources; a source series thinned by a fixed every-second-genuine-update rule to test clock sensitivity; same-record versus genuine-change ages; source events shifted within a declared diagnostic schedule. Backward-lag association is a warning, not by itself a causal falsifier because common news can produce it.

**Required interactions/ablations.** Remove Z01 while retaining E and A; compare record age with genuine-change age using one fixed diagnostic; remove target already-completed same-bin movement control only as an explanatory ablation, never as the accepted result; add common activity; repeat using bid and ask movements separately. Report unchanged-midpoint records and gaps. Do not substitute a more flattering age definition for the primary.

**Multiple-testing family.** C01-PRE and C01-ACTIVE slots in the eight-slot Holm family. All targets aggregate under fixed equal-target/time weighting; they are not separate discoveries. Diagnostic sign or horizon findings cannot rescue failure.

**Data sufficiency.** §4.5 plus at least one valid family source per target, actual pre-shock quote timestamps, repeated nonzero innovations over varying ages, valid target follow-up and varying outside-family controls for the stronger claim. The 004B grid supplies none of these interaction-support counts; feasibility remains prospective.

**Confounders.** Endogenous quote age; frozen quotes in low-probability markets; bursty shared information; missing messages; price rounding; source changes reflecting their own stale catch-up; same-bin target response; basket composition and collector congestion. Age is not random treatment.

**Overfitting risk.** Moderate under one pooled interaction; high if separate thresholds, source pairs, horizons or age clocks are selected. Mitigate with fixed transformations, no empirical edge ranking and whole-event evaluation.

**Implementation complexity.** Medium: price/age variables exist conceptually in the audited runner, but event-clock reconstruction, censoring and shared-state null validation are the material work. This is a research implementation estimate, not a development ETA.

### ASTRA-C02 — Conditional quote renewal

**System/mechanism intuition.** A family activity burst may increase the chance that an inactive target updates. The potential mechanism is coordinated attention/quote renewal, distinct from direction or return size. The question is whether activity adds timing information after common news intensity and the target's own renewal process.

**Precise 004B motivation.** E08 shows stronger family clustering in BBO counts than prices, with PRE same/cross-family 0.31647/0.06392. E10 reports 100/110 available PRE and 585/1,560 ACTIVE activity→price pairs; this demonstrates variable availability, not hazard significance. E09 warns that snapshot cadence can create broad synchrony. E19 shows raw BBO counts need de-duplication.

**Claim regime.** PRE primary; ACTIVE secondary with its own nonstationary baseline. Claim predictive quote timing only. A timing result with no directional mark remains a timing result.

**Predictor and target.** F_j(t)=mean log(1+C_i(t−30,t]) across available family peers, where C counts genuine BBO changes. Let D_j(t−30) be log(1+seconds since the last genuine target change /30). Add Z02=F_j(t)×D_j(t−30) to B_clock, including F and D main effects. Target U_j(t)=1 if at least one genuine valid target BBO change occurs in (t,t+30], otherwise 0 only under verified observation exposure. Predict positive Z02 interaction.

**Expected temporal signature.** Greater target 30s change probability after high family activity when the target previously remained unchanged longer; effects should not require same-timestamp archive batches. First-update survival over 60/120s is a diagnostic. No expected price sign is assigned.

**Permitted horizons/parameters.** 30s discrete hazard, source counts over 30s and nuisance counts over 300s; one F×D interaction. Fixed 60/120s cumulative-incidence diagnostics. No learned excitation kernel or burst threshold.

**Simplest baseline.** B_clock with family activity main effect, target duration and own-count history, price/volatility/spread, outside-family intensity, collector exposure and elapsed regime time. This tests conditional renewal, not merely whether family updates predict anything.

**Richer challenger.** At most a pooled exponential-kernel renewal/Hawkes comparison could follow a successful hazard test, with decay fixed prospectively. It is not activated here: the archive's row counts and 004B's absent hazard tests do not justify estimating a full excitation matrix.

**Null.** N0/N1 and N2's nonstationary common-intensity renewal process with all cross-excitation coefficients zero. Preserve target self-excitation/duration, common bursts and observation/batch schedules. Randomising times uniformly would destroy precisely the rival mechanism that must survive.

**Falsifier.** No positive conditional interaction or held-out Brier improvement; an effect confined to identical timestamp batches/repeated same-state records; or reproduction by the fitted no-cross common-intensity process. If collector exposure cannot distinguish silence from outage, the test is unidentifiable, not negative.

**Negative controls.** Full-depth snapshot counts as a capture-cadence placebo; repeated unchanged BBO-record counts; matched outside-family activity sharing the collector; raw-row intensity versus genuine-change intensity; count-preserving timestamp-batch controls. Fill counts may be reported as an independently defined diagnostic, never interpreted as aggressor direction or substituted for a failed BBO hypothesis.

**Required interactions/ablations.** F×D against F+D; genuine-change versus raw-row counts; target own renewal history; common event activity; eliminate tied source/target timestamps; add preceding midpoint-change history. Evaluate calibration by predeclared continuous-state plots, not searched bins defining a new test.

**Multiple-testing family.** C02-PRE and C02-ACTIVE in the eight slots. C01 and C02 are related mechanisms, not independent replications; a two-part decomposition must not count them twice.

**Data sufficiency.** §4.5 plus distinct valid before/after BBO states, identifiable target exposure, both positive and zero-update intervals, multiple bursts separated in time and adequate counts for every hazard parameter. BBO record totals are an upper bound on usable events. No finite inferential conclusion is possible from capture counts alone.

**Confounders.** Periodic capture, batch release, WebSocket reconnection, common news, market suspension, target update saturation, own-duration misspecification and counts that include the target itself. Family activity always leaves target out.

**Overfitting risk.** Moderate for one interaction, high for flexible kernels/state thresholds. The biggest risk is measurement misclassification, not model capacity.

**Implementation complexity.** Medium: de-duplication, risk-set/exposure construction and a small logistic model; a validated common-intensity null is harder than the regression. No full Hawkes network is required.

## 6. SECONDARY hypothesis cards

### ASTRA-C03 — Distributed co-shock response

**System/mechanism intuition.** Several related markets moving can signal a broader information episode than one isolated quote move of the same aggregate size. The claim is nonlinear participation: the distribution of innovations across a fixed neighbourhood matters beyond their additive contributions. It is not a claim that an empirical central node causes a cascade.

**Precise 004B motivation.** E08 supplies same-family price/activity clustering, while E16 opposes a dominant global factor. E18 has too few matched controls to establish semantic specificity. Peru PRE has nine presidential-family conditions, but only one lower- and one upper-chamber condition; this supports a possible within-family fan-in while sharply limiting matched motifs. No multi-source interaction was measured.

**Claim regime.** Secondary, PRE and ACTIVE separately; activation requires feasible multi-source and matched-control support using discovery-only diagnostics. No Hungary graph claim. No latent-factor, exact-payoff or causal diffusion interpretation.

**Predictor and target.** With v_i=|r_i(t)| over available peers, define effective participation n_eff=(sum v_i)^2 / sum(v_i^2), and B=(n_eff−1)/(n−1). Require n≥3; define B=0 if all v_i=0. Thus B is continuous from an isolated source to distributed participation. Add Z03=E_j×B to a strengthened B_price containing B, E², B², max(v_i), source-count/availability controls and the standard main effects. Target |p_j(t+30)−p_j(t)|. Expected added contrast is positive.

**Expected temporal signature.** Broader source participation produces a larger subsequent target magnitude at the same aggregate scale, with a 30s primary effect; 60/120s diagnostics should not show that the result is solely contemporaneous alignment. No claim about multi-hop lag ordering is made.

**Permitted horizons/parameters.** The common 30/60/120s policy. One continuous E×B interaction. Fixed family graph; no learned weights, discovered communities, neighbour-count tuning or graph neural network.

**Simplest baseline.** Strengthened additive B_price above. E² is essential: otherwise E×B can masquerade as generic shock-size curvature. Max(v_i) is essential: otherwise participation can masquerade as one dominant source. When using a within-family fixed-node diagnostic, also compare against a regularised additive model with every source main effect and fixed training-only regularisation; it is diagnostic unless separately allocated.

**Richer challenger.** None activated. A sparse fixed one-hop graph model is a later comparison only if the one-term participation contrast passes; attention weights and multi-hop propagation are not justified by 004B.

**Null.** N0/N1 retaining marginal nonlinear scale effects but no participation interaction, N2 shared-family shocks without network transmission, and N3 matched graph labels. The source-vector joint distribution must be retained; independently shuffling each source would falsely make genuine shared news look like network synergy.

**Falsifier.** Added effect disappears after E²/max/source-main-effect controls, shows no frozen loss gain, or matches random graph labels after activity/availability control. Support that exists only for one source configuration or one information burst does not establish a network mechanism. Absence of an admissible matched graph leaves the family-specific claim INCONCLUSIVE.

**Negative controls.** Same-degree matched non-neighbour baskets; leave-one-source-out diagnostics performed for every source under a fixed rule; target/duplicate exclusion; fixed-mask evaluation; source concentration and raw available-node count as nuisance alternatives. Do not select the best leave-one-out version.

**Required interactions/ablations.** E×B versus E+B; add E²/B² and max; compare all fixed source main effects where feasible; remove target-equivalent payoff representations; add source activity/age and common event intensity; run graph-label controls. Report whether Z03 has any residual variation after these controls.

**Multiple-testing family.** C03-PRE and C03-ACTIVE reserved in the eight slots. If inactive, retain p=1; do not replace with a pairwise winner. A motif-specific success would need a separately preregistered family.

**Data sufficiency.** §4.5, at least three contemporaneously valid peers, repeated variation in participation at comparable E, identifiable Z03 beyond baseline curvature, and at least two distinct target-neighbour configurations plus admissible size-matched outside-family controls. Discovery PRE's single broad presidential family may fail this requirement; it must remain dormant if so. Forty nominal ACTIVE nodes do not satisfy it automatically.

**Confounders.** Same news moving all nodes, mechanically related outcomes, source volatility differences, changing basket membership, family-size effects, near-boundary prices, and correlated data capture. This is why the label remains a conditional participation effect.

**Overfitting risk.** Medium–high even with one term: graph definition, availability and nonlinear controls create hidden degrees of freedom. Primary promotion is not justified before support gates.

**Implementation complexity.** Medium for the feature/regression, medium–high for graph controls and common-shock null adequacy. Simpler than estimating a network, but not a cheap significance screen.

### ASTRA-C04 — Spread-dependent response delay

**System/mechanism intuition.** A wide pre-existing target spread may accompany less frequent quote adjustment, changing how quickly a family innovation appears in target quotes. Alternatively, a midpoint move can be one-sided spread repair. The proposed mechanism must distinguish these explanations rather than call every spread association liquidity transmission.

**Precise 004B motivation.** E15: the four usable Peru ACTIVE semantic gaps have median absolute spread association 0.698759, versus 0.161884 for depth and 0.132871 for activity; response signs are mixed. E06 shows BBO information is far more available than full depth. These are selection-limited, contemporaneous associations, not proof that spread causes delay.

**Claim regime.** Secondary PRE and ACTIVE separately. It is an observed-spread response-friction hypothesis; no price-gap equilibrium, depth mechanism or execution claim.

**Predictor and target.** Add Z04=E_j(t)×S_j(t) to B_clock, with E and S main effects, C01/C02's state variables and own volatility retained. Target U_j(t), genuine target BBO change in the next 30s under valid exposure. The preregistered expected interaction is **negative**: a wide pre-shock spread weakens immediate quote responsiveness to the same family price innovation. No post-shock spread enters Z04.

**Expected temporal signature.** Lower immediate response probability with wider spread, potentially followed by delayed 60/120s renewal. Delay without later adjustment remains only reduced short-term responsiveness; do not assert incorporation of information that was never observed. Bid/ask diagnostics must show that any economic interpretation is not exclusively one-sided width repair.

**Permitted horizons/parameters.** 30s primary; 60/120s diagnostics. One continuous spread interaction in probability units. No tight/wide spread threshold, reciprocal-spread singularity, winsorisation search, or depth fallback.

**Simplest baseline.** B_clock augmented with E main effect and mean source pre-shock age; all models use the same rows. Own target spread, duration, probability level and volatility are mandatory to avoid relabelling slow/wide books as a shock interaction.

**Richer challenger.** A small joint bid/ask state-transition model could separate spread repair from common quote translation, but is not activated here. Use the bid/ask decomposition as a required diagnostic first.

**Null.** N0/N1 with zero E×S interaction, keeping spread-dependent baseline hazard and volatility, plus N2's common-intensity/state-dependent observation null. A null with constant target update rates would be too permissive.

**Falsifier.** Adequately estimated nonnegative interaction or no frozen Brier gain rejects the stated delay mechanism. If only one-sided spread changes explain it, reject the interpretation as information-incorporation delay and report quote-maintenance behaviour separately. Reproduction by the observation-only null removes the economic transmission claim.

**Negative controls.** Source pre-shock spread interaction in place of target spread, fixed as a diagnostic; target repeated same-state records; unchanged-midpoint spread changes; matched outside-family price innovations; snapshot-count placebo. Source-spread diagnostics are not additional opportunities for success.

**Required interactions/ablations.** E×S versus E+S; add age and own-duration controls; repeat with genuine midpoint-change outcome as a diagnostic; classify endpoint bid/ask moves as same-direction translation, one-sided move, or opposite-direction width change. Restrict this classification to recorded states, with no invented intra-bin path. No assumption that a BBO change necessarily contains directional information.

**Multiple-testing family.** C04-PRE/C04-ACTIVE reserved in the eight slots. The stronger bid/ask-information interpretation is a conjunctive gate, not an alternative endpoint replacing the hazard.

**Data sufficiency.** §4.5, valid pre-shock bid and ask, meaningful within-regime spread variation, identifiable E×S after main effects/age, and repeated valid responses across independent time blocks. Four 004B semantic edges cannot by themselves supply this support; test the predefined eligible target population rather than select those four.

**Confounders.** Probability-dependent tick scale, one-sided quote repair, latent volatility, stale books, spread reacting before the chosen grid boundary, endogenous liquidity withdrawal and update/capture selection. Measuring spread at t−30 limits look-ahead but does not make it exogenous.

**Overfitting risk.** Moderate; high if one retrospectively chooses spread cutoffs, switches to depth, changes the outcome to price magnitude or claims only the favourable regime.

**Implementation complexity.** Low–medium incremental cost after C02's hazard/exposure pipeline. The causal interpretation remains harder than the model.

## 7. REJECTED mechanisms and reopening requirements

These are rejected research directions, not additional proposed confirmatory hypotheses. No parameter/horizon search or test allocation is authorised for them by this document.

| ID/name | Why rejected on 004B | What would be needed to propose a new, separately frozen test |
|---|---|---|
| R01 — Event-wide latent-factor residual reversion | E16: rank-1 EV only 18.15% at 30s, high effective rank; E17: HUN too small, ACTIVE no complete all-market bins. No stable loadings or target-excluded innovations demonstrated. A filtered model cannot magically create identification. | Ex ante semantic subsystem with connected overlap, stable discovery-only subspace, identifiable target-excluded factor and a strong observable-aggregate baseline. Do not reopen by selecting high-correlated sealed nodes. |
| R02 — Semantic-gap or hard-identity convergence | E13: no verified mechanical identity. E14–E15: mean/median next absolute-gap changes are slightly positive, mixed by edge; reported half-lives compress gaps. A price difference between distinct payoffs is not an equilibrium error. | Independently verified settlement/payoff constraint with complete relevant legs, or an independently calibrated semantic conditional expectation; gap-aware dynamics and asynchronous coherent-payoff null. |
| R03 — Depth/imbalance-driven nonlinear propagation | E06/E11: sparse joint depth and changing samples; `ofi_imbalance` is depth-imbalance change, not aggressor OFI. Non-price responses have no empirical cell-level nulls. | Adequate genuinely contemporaneous full-depth support and own-book controls, evaluated with fixed missingness and a conditional null. No filling missing depth with zero or carrying it across bins to manufacture power. |
| R04 — Learned centrality, multivariate Hawkes cascades or causal discovery | E09 capture synchrony; E12 unstable signs; E18 few controls; tiny/noncomparable HUN topology. Hundreds of untested edge/kernel choices would dominate the evidence. | First pass the small fixed hazard/participation tests, establish observation-process validity and enough independent motifs. Later model complexity needs its own multiplicity and held-out programme. |
| R05 — Venue-trade direction, participant skill, latent threshold location/uncertainty | Venue trades are degenerate zero; fills are unsigned; participant skill was not measured; verified overlapping same-underlying threshold surfaces are not established. | New validated provenance/coverage and semantic identification, frozen before outcomes. These cannot be inferred from names, `source_side`, sparse snapshots or two eligible Hungary contracts. |
| R06 — Best-edge/best-lag/best-state rescue | E20 only aggregate 30s tests; E12 weak stability; E05 larger raw ACTIVE effects can fail nulls. Choosing a winning cutoff after 004B or sealed results creates a new uncorrected search. | A new explicitly enumerated development search and untouched independent validation, not relabelling this battery's diagnostics as preregistration. |

## 8. MASTER decisions and execution handoff

The scientific recommendation is **C01-PRE and C02-PRE first**. They use fields that the audited archive can supply, ask questions absent from pairwise 004B, have one predeclared interaction apiece, and admit strong competing explanations. C03 is secondary because graph specificity is not yet supported; C04 is secondary because the spread evidence is selected, contemporaneous and compatible with measurement effects. No proposed mechanism has already passed its realistic conditional null.

Before running, the implementation handoff must bind: this document hash; exact discovery/evaluation manifest hashes; target/peer registry and duplicate rules; three clock definitions; collector liveness/exposure rules; frozen feature columns and transformations; model solver and singular-fit policy; discovery split/refit; the eight-slot family; null generator/diagnostics/seeds/invalid-draw treatment; block resampling; support/power gates; and the exact eligible sealed-event list under MASTER's current seal record. A change to any of these after looking at evaluation outcomes is a protocol deviation, not a harmless implementation detail.

The old Hungary ACTIVE null shortfall should receive an explicit MASTER disposition in the acceptance record. No 004B rerun is requested. These hypotheses do not require turning its non-significant directed result into support.

Do not implement the rejected latent/depth/identity mechanisms as fallback challengers. Do not read another researcher's hypothesis set to harmonise this independent document. MASTER can perform cross-researcher reconciliation afterward, without rewriting the frozen prior or this provenance record.

A valid negative outcome is useful: if clock/common-intensity nulls explain C01/C02, the 004B temporal structure is compatible with observation and common information, with no established incremental transmission. If only C02 survives, retain a quote-timing result. If only magnitude survives, retain a volatility/adjustment result. None is automatically a directional trading signal or a deployment decision.

## Source index

- [Frozen ASTRA independent prior](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004c/research/astra/ASTRA_PRE_004C_QUANT_RESEARCH_DOSSIER.md) — methodological sources and the pre-evidence mechanism atlas; this document does not claim fresh re-review of all external literature.
- [004B-B MASTER handoff](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/MASTER_HANDOFF_004B_B.md).
- [Structural/Core findings](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/structural_core_findings.md) and [machine-readable audit](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/structural_core_audit.json).
- [Dynamics findings](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/dynamics_stats_findings.md) and [machine-readable audit](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/dynamics_stats_audit.json).
- [Null audit](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/null_audit_summary.csv), [response audit](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/response_audit_summary.csv), [directed stability audit](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/review/stability_audit_summary.csv).
- [Frozen discovery manifest](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/data/experiments/experiment_004b/discovery_manifest.json), [runner](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/scripts/kaggle/experiment_004b/run_body.py.txt), [scientific-control module](https://github.com/DanielCWeng/predictions-cup/blob/4e094d13b06d9e558cbc404cc4d472316d7d5bbb/src/predictions_cup/learning/election_market_structure.py).
- [004B integration PR #30](https://github.com/DanielCWeng/predictions-cup/pull/30). Frozen external outputs are identified by the integrity table and kernel `polyleviathan/sig-cup-exp004b-election-market-structure`, v3; current mutable kernel outputs must not substitute for these hashes.

ASTRA 004C HYPOTHESIS SET READY FOR MASTER PREREGISTRATION
