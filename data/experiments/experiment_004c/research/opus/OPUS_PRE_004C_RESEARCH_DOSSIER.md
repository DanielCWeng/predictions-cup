# OPUS PRE-004C RESEARCH DOSSIER

**Project:** SIG Predictions Trading Cup — `DanielCWeng/predictions-cup`
**Lane:** market structure, economic mechanism, probability structure, information aggregation, aggressive falsification
**Status:** FROZEN PRIOR — written blind to all EXPERIMENT-004B outputs, findings, Lane A/B results, response matrices, nulls, factor and stability results, and to all other 004C agents' work.
**Written:** 2026-09-27 (Europe/London)

## 0. Blindness and provenance record

Repository material inspected (all pre-004B, accepted `main` at `3601bea`, which contains 004A.2 as its latest merge and no 004B branch or package):

- `docs/research/HISTORICAL_ELECTION_ANALOGUES_2024_2026.md` and `data/research/historical_elections/*`
- `docs/mappings/MAPPING_001_SIG_POLYMARKET.md`, `data/mappings/sig_polymarket_2026*.json`
- `docs/implementation/EXPERIMENT_002_RELATIONSHIP_EXPERIMENTS.md`
- `docs/experiments/EXPERIMENT_003_HISTORICAL_ALPHA_BATTERY.md`, `data/experiments/experiment_003/relationship_inventory.csv` (structure only)
- `docs/experiments/EXPERIMENT_004A_EVENT_TIME_COVERAGE.md`, `docs/experiments/EXPERIMENT_004A2_VALIDATION_REDESIGN.md`
- `docs/implementation/DATA_001_HISTORICAL_REPLAY_CORPUS.md` (timestamp, grade and known-hole sections)
- `docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md` (SIG Cup rules/latency sections), `docs/research/OPPONENT_ECOLOGY_AND_BOT_ARCHETYPES.md` (outline only)

Deliberately **not** opened: `scripts/kaggle/experiment_003_posthoc/exp004b/*` (name collision risk with 004B), anything outside `main`, any 004B spec/output. The frozen 004B implementation commit `e9a95fb…` is not reachable from the public remote (`not our ref`), so the 004B discovery spec (`4095123…`) was not read. My understanding of 004B's design is therefore limited to what 004A.2 says about it.

External research: live web search and fetch of primary papers; shallow clones and code reading of five public repositories (Section 2).

---

## 1. Executive synthesis

**1.1 What the target actually is.** The Cup is a simulated, peer-to-peer, play-money (SUSQies) order-book venue on 2026 U.S. midterm binaries, running 1 Oct 12:00 ET → 4 Nov 12:00 ET and scored on final account value. The live SIG universe (MAPPING-001) is 237 exchanges: ~46 competitive House districts × {D, R}, Senate and Governor races × {D, R}, and four chamber-control contracts. Of these, 140 map EXACT to a Polymarket token, 87 are DERIVED (party win = sum of that party's margin buckets inside one Polymarket negative-risk event), 4 are NEAR (chamber "win" vs "control after midterms" wording), 6 are NO_TRADE (California top-two and Alaska ranked-choice races, where Polymarket lists candidates, not parties). The historical corpus the project can test on (DATA-001) is **Polymarket-only**, from three independent election families (COL_2026, PER_2026, HUN_2026) whose topology is staged presidential / parliamentary — not U.S. district→chamber. That mismatch is the single most important framing fact for 004C: most hypotheses that matter for the Cup are either (a) cross-venue (Polymarket→SIG), which has **no historical SIG data at all**, or (b) district→chamber, which the historical families do not contain.

**1.2 What the literature says about propagation.** Binary contracts are Arrow–Debreu securities, and Ostrovsky (2012) shows these are *separable*, so in equilibrium with strategic, rational traders their prices converge to the pooled-information value. Galanis, Ioannou & Kotronis (2024) show aggregation can fail for separable securities once traders are ambiguity-averse. The empirical record is more mundane: related prediction contracts routinely disagree, and disagreements persist for hours or days (Rothschild & Pennock 2014: 1–5% net cross-exchange gaps for most of the last 85 days of 2012; Aktug & Torul: ~6 pp average cross-venue gap in 2024, compressing to ~4 pp after Kalshi entered; Clinton & Huang: cross-exchange divergence peaking in the final two weeks of 2024). Persistence is explained by limits to arbitrage — capital lock-up, fees, counterparty and venue risk, thin depth — not by slow information processing. Where information *does* arrive discretely, liquid betting exchanges absorb it within seconds to minutes with little drift (Croxson & Reade 2014, soccer goals). Leadership between venues exists but is horizon-dependent and statistically fragile: Polymarket led at 5-minute frequency and Betfair at 1-hour in 2024, with a 90% bootstrap interval on Polymarket's information share running from ~2% to ~84%.

**1.3 The central identification problem.** Three things produce the same cross-correlogram: genuine information transmission (A learns, B later learns from A), a common shock absorbed at different speeds (latency heterogeneity), and non-synchronous observation (a stale B "moves" when it next updates; Lo & MacKinlay 1990). The only clean separators in this literature are (i) **externally time-stamped information events** whose arrival time is known independently of either market, and (ii) **market-specific non-fundamental shocks** — noise that exists in A only — whose transmission to B proves B is learning from A's price rather than from common news (Goldstein, Li & Wang 2026 use exactly this logic for Polymarket→financial markets). Correlation, Granger causality, and VECM information shares on non-identical contracts do not identify leadership.

**1.4 Structural probability.** Very few cross-contract relationships in the SIG graph are mechanical. The genuinely mechanical ones are within-condition complements, within-partition sums (negative-risk bucket partitions, seat bins), and logical bounds. **Linearity of expectation makes Σ p_i = E[seats] exact under any dependence, but only over the complete seat set** — the SIG universe lists ~46 of 435 House districts, so no mechanical constituent→control identity exists on SIG. Chamber-control probability depends on the joint distribution of all races, dominated by a common national swing; naive independence understates tail probabilities of sweep outcomes. The historical staged-election families have richer mechanical structure (qualification ≥ overall), but it does not transfer to U.S. general elections except via runoff-trigger rules.

**1.5 Microstructure hazards specific to prediction markets.** Public-feed trade-direction inference on Polymarket barely beats chance (Dubach 2026: ~59% volume-weighted agreement vs ~80% for Lee-Ready on equities); on-chain fills double-count unless mints/burns/conversions and exchange-contract rows are separated (Tsang & Yang: $958M naive vs $391M decomposed October volume; Polymarket-v1 Database drops ~53% of nominal trades as relayer artefacts; the project's own 003 post-hoc found its "top participants" were NegRisk exchange contracts). Polymarket's liquidity-reward programme pays quadratic scores for quotes near the midpoint and forces two-sided quoting in the tails — so the midpoint is partly an incentive artefact, not a belief. Spreads widen dramatically in the tails (median ~400 bps mid-range vs 1,300–1,800 bps in the lowest decile), and depth decays toward resolution.

**1.6 Honest prior.** The most favourable published setting for cross-venue lead/lag — Polymarket BTC binaries vs Binance, millisecond-synchronised, 2.9M lead-lag pairs — produced a model that did *not* beat the naive Polymarket mid out of sample, with negative simulated economics after fees and slippage (OpenMarket 2026). EXPERIMENT-003 promoted nothing. My prior is that same-venue Polymarket-internal lead/lag at seconds-to-minutes is mostly common-shock timing and non-synchronous observation, and is unlikely to survive executable crossing. The mechanisms most likely to be real and exploitable are **(a) Polymarket→SIG propagation for EXACT-mapped contracts, because SIG is play-money, thinner and slower**, and **(b) attention-constrained staleness of low-salience markets during ACTIVE_RESULTS**. Neither is well tested by a Polymarket-only historical corpus.

---

## 2. Source review

Credibility: H = peer-reviewed or replication package verified in code; M = working paper / preprint with transparent method; L = practitioner, platform-authored, or method weaknesses found.

| # | Source | Type | Question | Data | Method | Key finding | Relevance to us | Cred. | Important limitation |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Dubach (2026), *Anatomy of a Decentralized Prediction Market* (arXiv 2604.24366) + `philippdubach/polymarket-microstructure` | Paper + GitHub | Polymarket order-book stylized facts; direction inference validity | 600-market panel, Feb–Apr 2026; 30.3B WS events; 255M on-chain fills | Pre-registered panel; LOOSE/STRICT feed inference vs on-chain OrderFilled | Feed-inferred direction agrees with on-chain ~59% (vol-weighted); longshot spread premium; depth L1/L10 ≈ 0.137 (not top-concentrated); depth decays toward close; effective-spread sign flips in ~50–67% of markets | Invalidates any aggressor-based feature built from the public feed; spread/depth priors for tails | M/H | Mostly crypto/sports; V1 CTF exchange scraped only (NegRisk exchange appears excluded); code shows no filtering of exchange-counterparty `OrderFilled` rows (see §8, trap T-19) — the "ground truth" itself needs audit |
| 2 | Becker, *Microstructure of Wealth Transfer* + `Jon-Becker/prediction-market-analysis` | Practitioner research + GitHub | Who profits: makers vs takers; FLB; YES/NO asymmetry | Kalshi 72.1M trades 2021–25; Polymarket on-chain | Trade-level returns by price bucket | Takers −1.12%, makers +1.12% excess return; 5¢ contracts win 4.18%; NO outperforms YES at same price; politics maker–taker gap 1.02 pp | Priors on level bias in tails; makers earn spread | L/M | Maker "excess return" mechanically includes half-spread capture; resolution inferred from final prices >0.99 (code); no event clustering; Polymarket side counts every `OrderFilled` row |
| 3 | Galanis, Ioannou & Kotronis (2024), *Information Aggregation Under Ambiguity*, REStud | Paper (theory + lab) | When do markets aggregate dispersed information? | Lab markets | Theory + experiments | Separable securities fail to aggregate under ambiguity aversion; "strongly separable" securities are necessary and sufficient, but none is strongly separable for all information structures | Aggregation failure is a theoretical possibility even for simple binaries | H | Lab setting; no microstructure |
| 4 | Ostrovsky (2012), *Information Aggregation in Dynamic Markets with Strategic Traders*, Econometrica | Paper (theory) | Which securities aggregate information? | — | Theory | Arrow–Debreu and additive securities are separable → price converges to pooled-information value; non-separable securities (XOR-like payoffs) can fail | Joint/"balance of power" contracts are closer to non-separable payoffs than single-race binaries | H | Asymptotic, common prior, no frictions |
| 5 | Aktug & Torul, *Price Discovery across Political Prediction Markets* (2024 election) | Paper | Which venue leads | 9 venues, 5-min, Jan–Nov 2024 | VECM, Hasbrouck IS, GG CS, Putniņš ILS, rolling windows, jump detection | Order-book venues carry ~85% of IS; leadership horizon-dependent (PM @5min, Betfair @1h); Kalshi 41% IS within a month of launch; avg cross-venue gap 6.2 pp → 4.2 pp post-Kalshi | Only literature applying IS/ILS to identical election contracts; directly analogous to PM↔SIG | M | 90% CI on PM IS [1.8%, 84.4%]; forward-filled stale prices up to 30 min; one election |
| 6 | Tsang & Yang (2026a), *Political Shocks and Price Discovery* | Paper | Permanent vs transitory response to shocks | 3.6M PM trades 2024 | Event study, Kyle λ, Glosten–Harris, variance ratios | Debate jump largely reversed; assassination-attempt jump persisted; dropout: huge two-sided volume, small move | Shock-type heterogeneity: same size move can be information or pressure | M | Tick-rule direction (unreliable on PM per #1); single market |
| 7 | Tsang & Yang (2026b), *Anatomy of a Blockchain Prediction Market* | Paper | Correct on-chain accounting | PM 2024 presidential on-chain logs | Decompose exchange trades / mints / burns / conversions | Naive Oct volume $958M vs $391M decomposed; YES+NO parity half-life fell from hours to <1 min by Oct–Nov; Kyle λ 0.53→0.01 | Volume/flow features are wrong without decomposition; parity is tight only in mature liquid markets | M | One market family |
| 8 | Ng, Peng, Tao & Zhou (2025), *Price Discovery and Trading in Modern Prediction Markets* (SSRN 5331995) | Paper | PM vs Kalshi leadership across categories | Matched markets 2023–26 | Price discovery measures | PM leads politics (even at lower volume); Kalshi leads sports and when large-trade activity is higher; persistent cross-platform gaps | Leadership is category- and participation-conditional | M | Abstract-level access only |
| 9 | Goldstein, Li & Wang (2026), *Learning from Prediction Markets* | Paper | Does PM transmit information *and noise* to assets? | PM 2024 + "Trump trades" | Predictive regressions; informed/uninformed wallet split | 1 pp PM move → 13 bp next-day return, partly reversing; uninformed-flow component transmits and reverses | **Identification template: transmission of source-specific noise proves learning-from-price, not common news** | M | "Informed" wallets defined by ex-post profit (look-ahead); 36% unattributed |
| 10 | Rothschild & Pennock (2014), *Extent of Price Misalignment in Prediction Markets*, Algorithmic Finance | Paper + field trades | Size and persistence of misalignment | Intrade, Betfair 2012 | Observation + live arbitrage | Cross-exchange gaps above ~3% costs on 64/85 final days; logical violations within exchange; less-salient contracts lost liquidity when related primaries were active; Betfair trailed Intrade ~12h | Attention reallocation creates stale related markets; arbitrage capital is the binding constraint | H | 2012 venues; pre-HFT |
| 11 | Saguillo et al. (2025), *Unravelling the Probabilistic Forest*, AFT | Paper | Arbitrage in Polymarket | Markets resolving Apr 2024–Apr 2025 | On-chain block-VWAP; LLM dependency detection | ~$40M "realized" arbitrage, overwhelmingly within-market rebalancing; combinatorial (cross-market) arbitrage small; occurs mostly at low liquidity | Cross-market logical arbitrage is rare and small; LLM pair detection unreliable on many-outcome markets | M | Prices carried forward up to ~2.5h (stale-price trap); "realized" is inferred from holdings |
| 12 | Dudík, Lahaie, Rothschild & Pennock (2013), *A Combinatorial Prediction Market for the U.S. Elections*, EC | Paper | Enforcing cross-contract consistency | 2012 election combinatorial market | LMSR with linear-constraint relaxation | Constraint propagation improves forecasts over independent markets; state outcomes strongly coupled | Only design study of U.S. constituent→aggregate structure; independence assumption is wrong | H | Automated market maker, not CLOB |
| 13 | Snowberg, Wolfers & Zitzewitz (2007), *Party Influence in Congress and the Economy* | Paper | Election-night prediction-market event study | Tradesports House/Senate control, 30-min | Event study; Scholes–Williams for asynchronicity | 2006 Senate control swung 60%→90%→10% on individual race counts (VA, MO) | **Aggregate control prices are driven by a handful of pivotal races on results night** | H | 30-min resolution; thin House market |
| 14 | Croxson & Reade (2014), *Information and Efficiency: Goal Arrival in Soccer Betting*, EJ | Paper | Speed/completeness of reaction to discrete news | Betfair in-play | Event study around goals | Prices update swiftly and fully; no exploitable drift | Prior that discrete public news is absorbed fast in liquid books | H | Sports, deep liquidity |
| 15 | Le (2026), *Domain-Specific Calibration Dynamics* + `namanhzz/prediction-market-calibration` | Paper + GitHub | Calibration by domain/horizon/size | 353M trades, Kalshi + PM | Logistic recalibration in logit; event-clustered SEs | Politics underconfident (slopes 0.93–1.83; PM mean 1.45); compression larger at long horizons; large trades more compressed on Kalshi politics | Level-bias nuisance for residual models | M | Clusters by event ticker only; election contracts share a national swing across events, so SEs remain optimistic |
| 16 | Cardozo & Rivero-Wildemauwe (2026), *FLB: Evidence from Polymarket* | Paper | FLB on PM | 588M trades 2022–26 | Returns to payoff by weighting scheme | FLB magnitude and even sign depend on weighting (child vs pooled vs parent event) | Any "bias" result is fragile to aggregation choice | M | Hold-to-resolution returns; wallet ≠ person |
| 17 | Burgi, Deng & Whelan (2026), *Makers and Takers: Economics of Kalshi* | Paper | Maker/taker returns, fees | 300k Kalshi contracts 2021–25 | Mincer–Zarnowitz; heterogeneous-belief model | Makers outperform takers; FLB across all categories, smaller in politics; FLB reproducible from modest disagreement + fees | FLB is plausibly an equilibrium feature, not an exploitable inefficiency | M | Model identification acknowledged weak |
| 18 | Kagan & Baiocchi (2026), *Calibration in Prediction Markets* (Kalshi Research) | Platform research | Calibration | 2.24M resolved Kalshi markets | Brier, reliability | Calibration improves with volume; **election calibration must be anchored to election day, not certification/close** | Date-anchoring trap | L | Platform-authored; no COI statement; causality unclear |
| 19 | Clinton & Huang (2025), *Prediction Markets? $2.4B in 2024* | Paper | Accuracy and efficiency | 2,500+ political markets, 4 venues, final 5 weeks | Accuracy counts, autocorrelation | Cross-exchange divergence; weak/negative daily autocorrelation; arbitrage peaked in final two weeks | Late-campaign disagreement is normal | L/M | Accuracy metric crude; unequal market mixes across venues |
| 20 | Page & Clemen (2013), EJ | Paper | Calibration vs time to expiry | Intrade | Calibration curves | Well calibrated near expiry; long-horizon compression attributed to time preference | Carry/lock-up cost is an economic source of persistent level gaps | H | Intrade-era |
| 21 | Wolfers & Zitzewitz (2006), *Interpreting Prediction Market Prices as Probabilities*; Manski (2006) | Paper | When is price = mean belief? | Theory | Theory | Price ≈ mean belief under log utility; can diverge otherwise (Manski: only bounds) | Prices are not probabilities in the tails | H | — |
| 22 | Hasbrouck (1995); Gonzalo & Granger (1995); Yan & Zivot (2010); Putniņš (2013) | Papers | Price-discovery metrics | — | VECM IS/CS/ILS | IS and CS confound leadership with relative noise; ILS isolates leadership | Valid only for **identical** securities sharing one efficient price | H | Requires known cointegrating vector |
| 23 | Hasbrouck (2021), *Price Discovery in High Resolution*, JFEC | Paper | Timestamp resolution and IS | — | High-resolution VAR | Leadership conclusions depend on timestamp resolution | Our receive-time clocks limit sub-second claims | H | — |
| 24 | Hoffmann, Rosenbaum & Yoshida (2013), Bernoulli | Paper | Lead-lag from non-synchronous data | — | Hayashi–Yoshida-type contrast | Consistent lead-lag estimation without synchronising/interpolating | Correct estimator family for asynchronous PM books | H | Assumes semimartingale prices, not bounded binaries near 0/1 |
| 25 | Lo & MacKinlay (1990); Chordia & Swaminathan (2000) | Papers | Spurious vs real cross-autocorrelation | Equities | Theory + empirics | Non-synchronous trading generates spurious lead-lag; high-volume assets lead low-volume ones | "Liquidity leader" ≠ "information leader" | H | Equities |
| 26 | Hirshleifer, Lim & Teoh (2009), *Driven to Distraction*, JF; Cohen & Frazzini (2008) | Papers | Limited attention | Equities | Event studies | Underreaction when many announcements coincide; linked firms' news propagates slowly | Election night = extreme simultaneous-announcement day | H | Equities |
| 27 | Gelman, Hullman, Wlezien & Morris (2020), JDM | Paper | Correlated state errors in forecasts | 2020 models | Critique | Between-state correlation structure drives aggregate probabilities and tail sweeps | Why constituent→control is not summable | H | Presidential |
| 28 | Goldstein et al. — see #9; OpenMarket (Young 2026, arXiv 2607.26245) + `gregyoung14/openmarket` | Paper + GitHub | PM vs Binance millisecond lead-lag | 727M rows, 54 days | Source/ingest dual timestamps; paired lags | 16 ms median source-clock lag; ~347 ms quote response after large Binance moves; **model does not beat naive PM mid OOS; economics negative after costs** | Strongest available prior against cheap lead-lag alpha | M/H | BTC binaries; ±99 ms unresolved clock offset |
| 29 | Polymarket-v1 Database (arXiv 2606.04217) | Dataset paper | On-chain trade dataset | 1.2B trades 2022–26 | Relayer filtering, NegRisk normalisation | ~53% of nominal trades removed as relayer artefacts; NegRisk legs need separate normalisation | Confirms project's 003 finding that "top participants" were NegRisk exchange contracts | M | Dataset-builder claims |
| 30 | Polymarket docs: negative risk; liquidity rewards; holding rewards | Exchange documentation | Mechanics | — | — | NO on one outcome converts to YES on all others; augmented neg-risk has placeholders/"Other"; rewards score quotes quadratically by distance to adjusted mid, two-sided required in tails; midterm markets earn 3.25% holding rewards valued at mid | Mechanical partition identities; incentive-distorted quoting; carry | H (for mechanics) | Programmes change at Polymarket's discretion |
| 31 | `gordonkoehn/prediction-market-microstructure` | GitHub | Spreads vs volume | 1,200 PM/Kalshi snapshots Jan 2026 | Gamma API `spread` field | PM median spreads ~200–500 bps | Example of a folklore-generating method | L | **Synthesises bid/ask as last price ± half the API spread** — not executable prices |
| 32 | Kalshi Research–co-authored, *LLM as a Risk Manager: Lead-Lag Trading* (arXiv 2602.07048) | Practitioner/industry paper | Semantic filtering of Granger pairs | Kalshi Economics, daily | Granger on ~150k directed pairs, LLM re-rank | LLM filter reduces average loss | Cautionary: pair mining at scale | L | No multiple-testing control; zero costs; midpoint exits |
| 33 | SIG / Kalshi public material (SIG first institutional Kalshi market maker, 2024; Cup rules) | Practitioner | Venue context | — | — | Host is a professional prediction-market maker; Cup is simulated, P2P, API-driven, nightly "Super Signal" | Opponent ecology includes sophisticated liquidity; nightly scheduled public signal | L | Marketing context only |
| 34 | Socos Academy blog summarising PM wealth concentration | Practitioner | Who wins | Secondary | Secondary | Execution > insight | Repeats Becker-style claims | L | Not primary; excluded from evidence |

---

## 3. Mechanism ledger

Each class below is a *mechanism*, not a strategy. For each: economic reason; necessary assumptions; expected signature; alternatives; data; falsifier; negative control; leakage trap; execution trap; PRE vs ACTIVE; transfer across families.

### M1 — Cross-venue propagation between economically identical contracts (deep real-money venue → thin play-money venue)

- **Economic reason.** Identical payoff; different participant sets, capital, attention and latency. Real-money Polymarket attracts informed and professional capital; SIG is play money with a fixed SUSQie endowment and a competitive bot population. Price discovery should concentrate where informed capital and depth are (Aktug & Torul; Ng et al.).
- **Assumptions.** (a) Mapping is exact in resolution semantics (EXACT class only; NEAR/DERIVED carry basis risk). (b) SIG participants do not all watch Polymarket with lower latency than we do. (c) SIG depth at the stale quote is non-trivial.
- **Signature.** After a Polymarket move, SIG executable prices move in the same direction with positive lag; asymmetry (SIG→PM near zero); ILS for PM ≫ 0.5 in logit space; response larger when SIG quote age is high.
- **Alternatives.** Both venues react to the same public news with different speeds (still exploitable only if SIG's lag exceeds our loop latency); SIG moves because of SIG-specific bots anchored on PM (then crowded, edge decays); nightly Super Signal refresh.
- **Data.** Synchronised PM and SIG books with source and receive clocks. **Not available historically** (DATA-001 has no SIG).
- **Falsifier.** No positive executable markout on SIG after PM impulses beyond our measured end-to-end latency; or SIG leads PM as often as the reverse; or effect entirely confined to minutes after the nightly signal refresh.
- **Negative control.** PM impulses on *unmapped* or NO_TRADE markets must not predict SIG mapped contracts; time-shifted (future) PM reference; PM impulses from reward-epoch boundary quote reshuffles (non-informational).
- **Leakage trap.** Using PM *receive* time from a different collector than the SIG decision clock; using PM last-trade that post-dates the SIG decision.
- **Execution trap.** SIG depth at stale quote may be one lot; 503s on cancel under load; many bots chasing the same signal (crowding); SIG position limits.
- **PRE vs ACTIVE.** PRE: small, infrequent impulses; exploitable mainly around scheduled information (poll releases, nightly signal). ACTIVE: large, frequent impulses; biggest opportunity and biggest crowding.
- **Transfer.** Venue-specific; historical PM families cannot test it. Must be a live-SIG hypothesis.

### M2 — Constituent → aggregate propagation via pivotality

- **Economic reason.** ∂P(control)/∂p_i ≈ P(race i is pivotal | information). Hard information on results night arrives race by race; the aggregate should reprice by pivotal-weighted constituent moves (2006 Senate: VA and MO counts moved control 60→90→10%).
- **Assumptions.** Constituent markets receive race-specific hard information first (county returns); aggregate traders do not observe the same feeds simultaneously; the pivotal set is small and knowable.
- **Signature.** Aggregate repricing follows constituent repricing, scaled by a pivotality weight that varies over the night; zero response to non-pivotal constituents (safe or already-decided seats).
- **Alternatives.** Aggregate leads constituents (more capital and attention concentrated in the headline market — very plausible); both react to the same AP call or decision-desk projection; constituent moves are themselves responses to national-swing inference (M3), not local information.
- **Data.** Complete (or near-complete) constituent coverage, aggregate market, externally timestamped result releases.
- **Falsifier.** Pivotality-weighted constituent impulses have no incremental predictive power for aggregate beyond aggregate's own lag and an equal-weight constituent mean; or aggregate→constituent direction dominates.
- **Negative control.** Non-pivotal constituents (priced <3% or >97%) must show no lead; constituents from a *different* chamber than the aggregate target.
- **Leakage trap.** Choosing pivotal races ex post from final outcomes; weighting by final margin.
- **Execution trap.** Aggregate markets are the deepest and most watched — lag windows will be shortest exactly where this matters.
- **PRE vs ACTIVE.** Essentially ACTIVE-only; PRE constituent moves are dominated by common swing.
- **Transfer.** Historical families lack a clean district→chamber graph (HUN party-list→seat-bins is the nearest). Transfer to U.S. midterms is weak and must be stated as such.

### M3 — Latent common swing: aggregate/sibling information updating stale constituents

- **Economic reason.** Election outcomes load heavily on a national (or regional) swing. When early hard results reveal the swing, every correlated race should update. Low-salience constituents update late (limited attention).
- **Assumptions.** Common factor explains a large share of constituent variance; constituents are informationally slower than the aggregate or than early-reporting siblings.
- **Signature.** Leave-target-out factor move (or aggregate move) predicts target's subsequent move; stronger for low-attention targets; decays as target updates.
- **Alternatives.** Non-synchronous observation (target just hadn't printed); mechanical cross-quoting by one market maker across a family (a liquidity leader, not an information leader); local information that contradicts the swing (target correctly ignores swing).
- **Data.** Many constituents in one family; freshness state of each quote.
- **Falsifier.** Effect vanishes when conditioning on target having a fresh quote update within the lookback; or effect equal in size for high- and low-attention targets; or response reverses.
- **Negative control.** Factor built from an unrelated election family / different country's contracts; factor from the same family but time-shifted; matched-liquidity unrelated contracts.
- **Leakage trap.** Factor loadings estimated on full sample (003 LOWRANK used TRAIN-only; must remain so); including target's mechanical siblings (complement, partition members) in the factor.
- **Execution trap.** Stale target quotes are stale because nobody wants them — depth may be tiny and adversely selected.
- **PRE vs ACTIVE.** PRE: factor moves slowly (polls, news); ACTIVE: factor revealed by early-reporting states, strongest effect.
- **Transfer.** Plausible across families where a common swing exists (HUN national list, PER legislative); weak in staged presidential families.

### M4 — Partition and distribution identities (mechanical)

- **Economic reason.** Exhaustive mutually exclusive partitions must sum to one; a party's win equals the sum of its margin buckets (SIG DERIVED class); seat-bin distributions imply threshold contracts.
- **Assumptions.** Identical resolution source and timing; exhaustive partition (including "Other"/placeholders); no tie rules that break exhaustiveness.
- **Signature.** Residual = headline − Σ components; bounded by combined spreads when executable; residual moves are absorbed by whichever side is less liquid.
- **Alternatives.** Apparent residuals are midpoint artefacts inside spreads; placeholder outcomes; different resolution wording.
- **Data.** All partition members' books at the same observable time.
- **Falsifier.** Executable residual (cross all legs at ask/bid) never exceeds zero after costs; mid-residual reversion fully explained by spread bounce.
- **Negative control.** Same computation on a non-exhaustive subset (should not be bounded); partition from different events.
- **Leakage trap.** Using the target inside its own reconstruction (003's LOO-PRICE vs LOO-FAMILY distinction exists for this reason).
- **Execution trap.** Legging risk across 4–10 buckets; the negative-risk adapter makes one direction cheap and the other expensive.
- **PRE vs ACTIVE.** Identity holds in both; violations more likely ACTIVE when attention is scarce.
- **Transfer.** Mechanical — transfers wherever the partition exists, but economic size is venue-specific.
- **Status: retained as a constraint and diagnostic, not as an alpha source.**

### M5 — Logical inequality (Fréchet and staging bounds)

- **Economic reason.** P(A∧B) ≤ min(P(A), P(B)); P(A∧B) ≥ P(A) + P(B) − 1; P(overall win) ≤ P(qualify for runoff); P(majority) ≤ P(most seats) in multi-party systems.
- **Signature.** Violations are rare, brief, and occur at low liquidity (Saguillo et al.).
- **Falsifier.** Executable violations are absent or smaller than costs.
- **Status: retained as a bound on residual models; not a primary alpha.**

### M6 — Staged-election conditional migration

- **Economic reason.** P(win) = Σ_pairs P(pair qualifies) · P(win | pair). First-round counts resolve P(pair) quickly; runoff probability then depends on head-to-head conditional strength, which the first-round market does not price.
- **Legitimate relationships.** Bound: P(win) ≤ P(qualify). Monotone updating: news that eliminates a likely opponent should move P(win) in the direction implied by the conditional head-to-head, which may be *opposite* to the first-round move.
- **Illegitimate shortcuts.** "First-round leader ≈ overall favourite"; "first-round move ⇒ same-sign overall move"; treating first-round vote share as runoff share.
- **Signature.** After qualification resolves, overall-winner markets reprice toward head-to-head priors; residual (overall − P(qualify) × prior conditional) converges.
- **Alternatives.** Both react to the same count releases; conditional head-to-head beliefs change on the night.
- **Falsifier.** First-round impulses have no incremental power once P(qualify) is known; or response sign is predicted equally well by naive same-sign rule (then there is no conditional structure being learned).
- **Negative control.** Candidates with P(qualify) ≈ 0 or ≈ 1 before the count (no information to migrate).
- **PRE vs ACTIVE.** ACTIVE (first round) is where migration happens.
- **Transfer.** Strong within COL/PER; **weak to U.S. midterms**, except runoff-trigger rules (e.g., jurisdictions with majority requirements) and top-two/RCV races that are NO_TRADE in MAPPING-001.

### M7 — Cross-institution shared latent state

- **Economic reason.** Senate and Governor races in one state share ballots, turnout and the same county reports; House and Senate control share the national swing; presidential and legislative results share electorate.
- **Signature.** Contemporaneous co-movement dominates; lagged cross-prediction only where reporting is asymmetric (one race called earlier, one market more attended).
- **Alternatives.** Pure common factor (no transmission); split-ticket voting weakens the link.
- **Falsifier.** Lagged effect disappears after removing contemporaneous common factor and conditioning on freshness.
- **Status: retained mainly as a *common-factor control* for M2/M3, and as a transmission hypothesis only where a specific reporting asymmetry is named in advance.**

### M8 — Attention-constrained staleness during result arrival

- **Economic reason.** Election night has hundreds of simultaneous information events; attention and market-maker capacity are finite (Hirshleifer–Lim–Teoh; Rothschild & Pennock observed less-salient contracts losing liquidity during high-activity periods). Stale quotes in low-salience markets are left behind by information already priced elsewhere.
- **Signature.** Target quote age and spread rise during ACTIVE; related-market impulse predicts target repricing more strongly when target is low-volume, low-salience, and quote-old; effect decays after the target's first fresh update.
- **Alternatives.** Market makers deliberately widen or withdraw (rational adverse-selection protection) — then the "stale" quote is not stale, it is simply unfillable; non-synchronous observation.
- **Falsifier.** No executable profit at the stale quote (it is pulled or tiny); or effect equally strong in high-salience markets.
- **Negative control.** Same analysis in PRE with matched quote ages; matched-liquidity unrelated reference.
- **Execution trap.** Adverse selection: the stale quotes that survive are those left by informed makers.
- **PRE vs ACTIVE.** ACTIVE-dominant by construction.
- **Transfer.** Plausible across families and to SIG, where the bot population is small relative to the number of markets.

### M9 — Non-fundamental price pressure and its transmission

- **Economic reason.** Large uninformed or position-management flow moves a price temporarily (Tsang & Yang debate episode; Goldstein et al. uninformed component). If related markets follow the pressured market, they follow a price, not news — and should reverse together.
- **Signature.** Source moves on high two-sided volume with no external news timestamp; target follows; both partially reverse.
- **Use.** (a) Identification device separating learning-from-price from common news. (b) Possible reversal effect.
- **Falsifier.** Identified pressure episodes do not reverse; or targets do not follow them.
- **Caveat.** Requires reliable flow direction — unavailable from the public feed; requires correctly decomposed on-chain fills.

### M10 — Level bias: calibration compression and favourite–longshot bias

- **Economic reason.** Heterogeneous beliefs plus fees/capital costs produce compression toward 0.5 in politics (Le 2026) and longshot overpricing (Becker; Burgi et al.).
- **Why not a 004C propagation alpha.** It operates at days-to-resolution horizons and resolution outcomes; our horizons are seconds to minutes. Weighting choices flip its sign (Cardozo & Rivero-Wildemauwe). It is a **nuisance** for logit residual models: an equal-weight logit mean across contracts at different price levels will carry level-bias differences as "residual".
- **Status: rejected as a short-horizon hypothesis; retained as a required control.**

### M11 — Rational persistent disagreement (venue design, semantics, carry)

- **Economic reason.** Contracts with different resolution sources, wording, tie rules, fee schedules, carry (PM holding rewards 3.25% at mid; SIG zero carry), capital constraints (SIG fixed endowment), and settlement timing need not converge. **SIG's score is account value at 4 Nov 12:00 ET, before many races resolve** — convergence to eventual payoff is not required inside the contest; what matters is the SIG mark at the end.
- **Signature.** Persistent, slowly varying gap; no reversion at short horizons; gap correlates with the named structural difference.
- **Use.** Negative class: prevents calling semantic gaps "mispricing".
- **Status: retained as falsification machinery.**

### M12 — Scheduled public-information arrival

- **Economic reason.** Poll releases, the SIG nightly Super Signal refresh, and poll-closing times by time zone on election night are scheduled and public; response order among markets identifies processing speed, not superior information.
- **Signature.** Discrete impulses clustered at known timestamps; heterogeneity in response latency across markets.
- **Use.** Cleanest identification anchor for M1–M3, M8.
- **Falsifier.** Markets do not respond at scheduled times (information pre-empted) or all respond simultaneously within clock resolution.

### M13 — Participant/protocol-identity-conditioned flow

- **Economic reason.** Some traders are informed; their flow predicts.
- **Why rejected for 004C primary.** SIG does not expose identities; on PM the dominant "identities" are exchange and adapter contracts (003 post-hoc); direction is unrecoverable from the public feed; 003's lane was invalidated by protocol deviation; nulls were poorly calibrated. A separate, cleanly preregistered identity study could revisit it; it is outside my lane's comparative advantage.

### M14 — Order-book imbalance / microprice

- **Why rejected.** 003's implementation showed microprice displacement over spread = imbalance/2 (one degree of freedom); depth is roughly uniform across the top 10 levels on PM (Dubach), so top-level imbalance is a weak state variable; reward-farming quotes contaminate L1. Not a structural mechanism.

### M15 — Semantic/Granger pair mining

- **Why rejected.** Testing ~10^5 directed pairs without multiplicity control (LLM-as-risk-manager paper) produces spurious leadership; "semantically related" is not a probability relationship.

---

## 4. Falsification ledger

| Mech. | Strongest alternative explanation | Decisive falsifier | Minimum negative control |
|---|---|---|---|
| M1 PM→SIG | Common public news processed at different speeds, with SIG bots anchored to PM (crowded) | No positive **executable** SIG markout beyond measured decision latency; symmetric leadership | PM impulses on unmapped markets; time-reversed reference |
| M2 constituent→aggregate | Aggregate leads (capital/attention) or both react to the same AP call | Pivotal-weighted constituent impulse adds nothing beyond aggregate own-lag + equal-weight mean | Non-pivotal constituents; wrong-chamber constituents |
| M3 latent swing → stale constituent | Non-synchronous observation | Effect disappears conditional on fresh target quote | Unrelated-family factor; matched-liquidity unrelated pair |
| M4 partition identity | Mid-spread artefact | Executable residual ≤ costs always | Non-exhaustive subset |
| M5 logical bounds | Resolution-wording differences | No executable violation | Randomly paired contracts |
| M6 staged migration | Shared reaction to counts; naive same-sign rule | No incremental power over P(qualify) and naive rule | Candidates with P(qualify) ∈ {~0, ~1} pre-count |
| M7 cross-institution | Pure common factor | Lagged effect vanishes after contemporaneous factor removal | Different-state pairs |
| M8 attention staleness | Rational quote withdrawal | No fillable depth at stale quote | Matched quote-age in PRE |
| M9 pressure transmission | Hidden news | Pressure episodes do not reverse | Episodes with external news timestamps |
| M10 level bias | Equilibrium FLB | (rejected for short horizon) | — |
| M11 rational gap | — (this *is* the alternative) | Gap mean-reverts at short horizon unrelated to named difference | — |
| M12 scheduled arrival | Pre-emption by leaks | No response clustering at schedule | Shuffled schedule timestamps |

---

## 5. Structural probability toolkit

### 5.1 Classification of SIG / historical relationships

| Class | Relationship | Example in our universe | Defensible use |
|---|---|---|---|
| **MECHANICAL IDENTITY** | YES + NO = 1 within one condition | Every PM condition | Parity check; never count both sides as independent evidence |
| MECHANICAL IDENTITY | Σ exhaustive partition = 1 | PM neg-risk margin-bucket events; seat bins | Bounds, reconstruction (LOO-PRICE) |
| MECHANICAL IDENTITY (conditional on exhaustiveness) | Party win = Σ that party's margin buckets | SIG DERIVED (87 records) | Exact only if buckets exhaustive, same resolution source, no "Other"/placeholder ambiguity |
| **NOT an identity** | SIG "D wins race" + SIG "R wins race" = 1 | Two separate SIG markets per race | Only an inequality (≤ 1); independents/third parties, withdrawal, and tie rules break equality |
| **NOT an identity** | "D controls chamber" + "R controls chamber" = 1 | NEAR chamber contracts | Senate 50–50 control depends on the vice-presidential tie-break and caucusing independents; resolution rules must be read, not assumed |
| **MECHANICAL (linearity)** | E[seats] = Σ_i p_i, under any dependence | Only if **all** seats are priced | The SIG universe prices ~46 of 435 House seats → no mechanical constituent→seat-total or constituent→control identity on SIG |
| **LOGICAL INEQUALITY** | P(A∧B) ∈ [max(0, P(A)+P(B)−1), min(P(A), P(B))] | Balance-of-power joints vs chambers | Bounds only |
| LOGICAL INEQUALITY | P(overall win) ≤ P(qualify for runoff) | COL/PER | Bound; violations rare |
| LOGICAL INEQUALITY | P(majority) ≤ P(most seats) | HUN, multi-party | Bound |
| **CONDITIONAL** | P(control) = Σ_k P(seats = k) · 1[k ≥ threshold] | HUN seat bins → most seats; US House bins → control | Requires a traded distribution with the same resolution source |
| CONDITIONAL | P(win) = Σ_pairs P(pair) · P(win \| pair) | Staged presidential | Requires head-to-head conditionals — rarely traded; any implementation is a *model* |
| **ECONOMIC** | Venue gaps from carry, fees, capital, contest horizon | PM vs SIG | Explains persistent gaps; not a convergence signal |
| **SHARED INFORMATION** | Same ballots / same county reports | Senate & Governor in one state; districts in one state | Common-factor control; transmission only with named reporting asymmetry |
| **EMPIRICAL ASSOCIATION** | Cross-state swing correlation | All races | Must be estimated TRAIN-only, and is non-stationary |

### 5.2 Dependence and naive summation

- Var(seats) = Σ p_i(1 − p_i) + Σ_{i≠j} Cov_ij. A common national swing makes the covariance term dominate, so independence understates seat-count variance. When E[seats] sits well away from the control threshold, independence makes P(control) too extreme; when it sits near the threshold, independence still misstates the probability of sweep outcomes. The direction of the error depends on where the mean sits relative to the threshold. **Never infer control probability from constituent sums.**
- A traded seat-bin distribution carries *second-moment* information (implied dispersion ≈ implied correlation) that a headline control market does not display. That is the only sense in which "distributional shape contains information missing from the headline", and it requires the bins to be traded, exhaustive and fresh.
- Pivotality: the sensitivity of P(control) to race i equals the probability that race i is decisive given everything else. Pivotality is state-dependent and changes sharply on results night; any fixed weight is misspecified.

### 5.3 Residual checklist — before calling a gap "mispricing"

1. Same resolution source, wording, date, and tie/withdrawal/"Other" handling?
2. Exhaustive partition, including placeholders?
3. Executable on both legs at the same observable time (not mid vs mid)?
4. Both quotes fresh (not merely quiet)?
5. Gap larger than the sum of half-spreads, fees and legging risk?
6. Could the gap be explained by carry, holding rewards, capital lock-up, contest end-date marking, or play-vs-real money?
7. Is the gap in logit space driven by tail instability (prices < 3% or > 97%)?
8. Does the gap persist at a level (rational) or mean-revert (candidate mispricing)?
9. Was the pair selected before looking at the data?

---

## 6. Price-discovery toolkit

| Question | Valid method | Invalid or misleading method |
|---|---|---|
| Identical contracts across venues (PM EXACT ↔ SIG): who leads? | Information Leadership Share (Putniņš; Yan–Zivot) in logit space on synchronised executable mid; Hayashi–Yoshida / Hoffmann–Rosenbaum–Yoshida lead-lag contrast on raw asynchronous updates; report at several resolutions (Hasbrouck 2021) | Hasbrouck IS or GG CS alone (confounds noise with leadership); forward-filled 5-min grids |
| Non-identical related contracts: does A's information reach B? | Event studies around **externally timestamped** shocks (poll closings, AP calls, official releases), comparing each market's reaction latency; local-projection impulse responses conditional on identified source-specific shocks | VECM/IS (no known cointegrating vector); Granger on clock-time grids; correlation |
| Leader vs follower vs common shock | (i) Direction asymmetry test A→B vs B→A; (ii) reaction latency to the same external event; (iii) transmission of source-specific *noise* (M9) — only learning-from-price transmits noise | Cross-correlation peak location alone |
| Liquidity artefact vs information | Condition on target freshness (fresh update within lookback); measure lag in the target's own update-time; compare high- vs low-volume targets (Chordia–Swaminathan); executable crossing markouts | Midpoint markouts; tick-count sample sizes |
| Common shock | Include contemporaneous leave-target-out factor and external-event dummies; test *incremental* lagged power | Pure lagged regressions without contemporaneous controls |

**Four-way diagnostic signature (to preregister):**

- *Leader*: moves first after external events more often than chance; its source-specific noise transmits; asymmetric predictive power survives freshness conditioning.
- *Follower*: responds with a lag; response survives when conditioning on its own fresh updates; executable markout positive only if lag > our latency.
- *Common shock*: both respond within clock resolution to an external timestamp; lagged effect vanishes after contemporaneous control.
- *Liquidity artefact*: lead-lag disappears in target update-time or conditional on fresh target quote; markout vanishes at executable prices; strongest in lowest-volume markets.

---

## 7. Event-time implications

| Dimension | PRE_ELECTION | ACTIVE_RESULTS |
|---|---|---|
| Information type | Soft, diffuse, scheduled (polls, fundraising, early-vote reports, nightly Super Signal) | Hard, sequential, race-specific (county returns, poll closings by time zone, AP calls) |
| Dominant structure | Common latent swing; level biases; carry | Race-level information arrival; pivotality shifts; attention overload |
| Lead-lag plausibility at seconds–minutes | Low; most moves are common-factor or flow | Highest for M2, M3, M8 |
| Liquidity | Stable quoting, reward-farming quotes dominate L1 on PM | Spreads widen, depth withdraws, adverse selection peaks (Dubach SF8) |
| Tail behaviour | Few contracts near 0/1 | Many contracts converging to 0/1 → logit instability; partition identities bind |
| Mechanical relationships | Hold but rarely violated | Violations more likely (Rothschild–Pennock salience) but brief |
| Feed/measurement risk | Quiet-book vs stale confusion | Collector backpressure (Dubach SF6: multi-second P99), same-ms ambiguity, regime-wide silences |
| Contest-specific | Month-long horizon; SIG marks | Contest ends 4 Nov 12:00 ET — before many House races are called; end-of-contest marking dominates economic value |

Consequences: hypotheses must be claimed per regime (004A.2 already forbids pooling); a PRE result cannot support an ACTIVE claim; ACTIVE effects should be expected to be larger but less replicable and more crowded. ELECTION_DAY_PRE_RESULTS is a distinct regime (exit polls, turnout chatter) and currently `ELIGIBILITY_NOT_ASSESSED`.

---

## 8. Measurement traps (prediction-market-specific)

| ID | Trap | Why it matters | Guard |
|---|---|---|---|
| T-1 | YES/NO complement dependence | One book shown twice; YES trades appear as NO book changes via minting | One canonical side per condition (004A.2 rule); never count both |
| T-2 | Midpoint vs executable | Mid gaps inside spread are not tradable; reward-farming quotes anchor the mid | Economic outcomes at bid/ask crossing only |
| T-3 | Sparse books | Median PM depth snapshots per token as low as 9–10 in some regimes (DATA-001) | Snapshot-anchored state; report eligibility |
| T-4 | Stale vs quiet | A quiet unchanged book is not stale; a stale feed looks quiet | Separate feed-health from token silence (003 limitation) |
| T-5 | Asynchronous markets | Spurious lead-lag (Lo–MacKinlay) | HY-type estimators; freshness conditioning |
| T-6 | Common collector timestamp | Receive-time ordering conflates network path with venue order | Record source and receive times; never order by source time from a different venue |
| T-7 | Source vs receive time definitions differ across archives | DATA-001 says PMXT V1 `timestamp_received` is recorder time with no venue time; Dubach describes `timestamp_received` as exchange-side. Same field name, conflicting semantics | Verify per archive on real rows; do not import other papers' latency conclusions |
| T-8 | Missing snapshots / interpolation | Backfill manufactures prices | No interpolation, no centred windows (DATA-001 rule) |
| T-9 | Quote-age effects | Old quotes are the ones "predicted" to move | Include quote age as baseline covariate |
| T-10 | Longshot effects | Wide, asymmetric spreads in tails | Stratify by price band; exclude < 3% / > 97% from primary |
| T-11 | Bounded prices, logit tails | Logit clamp 1e-6 gives ±13.8; tail contracts dominate squared logit loss (003) | Clamp sensitivity; probability-space co-primary; tail exclusion |
| T-12 | Non-independent event families | Five rounds = three families; midterm races share one national swing | Family-level collapse; for 2026 midterms, the whole Cup is effectively **one** family |
| T-13 | Trade-direction inference | Feed inference ~59% on PM | On-chain only, after decomposition |
| T-14 | Maker/taker vs aggressor confusion | Source matching role ≠ passive/aggressive; exchange contracts appear as counterparties | 003 rule retained |
| T-15 | Post-selection of pairs | Pairs chosen after seeing co-movement | Frozen inventory from identity/semantics only |
| T-16 | Multiple testing | Many pairs × horizons × lookbacks × regimes | Lane-specific FDR over full preregistered grid; unavailable cells count |
| T-17 | Non-stationarity around results | Volatility, spread, participation shift discretely | Regime-specific models; no pooled coefficients |
| T-18 | On-chain double counting | Mints/burns/conversions; relayer and adapter rows (~53% of nominal) | Decompose per Tsang–Yang; exclude infrastructure registry addresses |
| T-19 | "Ground-truth" direction built from all `OrderFilled` rows | CTF exchange emits a taker-order fill with the exchange as counterparty; if not filtered, one match yields rows of both signs. The public Dubach join does not visibly filter these (code-reading concern, **unverified**) | Audit any borrowed direction ground truth before use |
| T-20 | Reward-farming liquidity | Quadratic reward near adjusted mid; two-sided forced in tails | Treat L1 imbalance and mid as incentive-contaminated |
| T-21 | Placeholder / "Other" outcomes | Neg-risk partitions not exhaustive over named outcomes | Include or explicitly exclude with reason |
| T-22 | Resolution inferred from final prices | Becker code marks winners as price > 0.99 | Use official resolution |
| T-23 | Date anchoring | Calibration against certification date vs election day misstates difficulty | Anchor to event time (004A clock) |
| T-24 | Aggregation weighting | FLB sign flips across weighting schemes | Preregister weighting |
| T-25 | Synthesised quotes | Bid/ask reconstructed as last ± spread/2 in some public code | Never accept derived quotes as executable |
| T-26 | Label case | 113 uppercase `YES`/`NO` conditions fail closed in 004A.2 | Preserve fail-closed; report coverage bias |
| T-27 | V1/V2 splice | V2-only markets start at first real observation | No backfill; splice-aware eligibility |
| T-28 | Contest marking | SIG final score marks at 4 Nov noon | Any "convergence" hypothesis must converge before then to matter |

---

## 9. Methods worth considering after 004B (no parameters chosen)

1. **Event studies anchored to external timestamps** (poll closings, official releases, decision-desk calls) with per-market reaction latency and permanent/transitory split.
2. **Local-projection impulse responses** of target executable price to identified source impulses, with contemporaneous leave-target-out factor control.
3. **HY / HRY lead-lag contrast** on asynchronous updates for identical or near-identical pairs; ILS only for EXACT cross-venue pairs.
4. **Freshness-conditioned tests**: repeat every lead-lag test restricted to targets with a fresh update inside the lookback.
5. **Direction-asymmetry tests** (A→B vs B→A) as a required companion to any lead claim.
6. **Matched-liquidity unrelated-pair null**: for each related pair, draw unrelated pairs matched on volume, spread, price band and regime; compare effect distributions.
7. **Schedule-shuffle placebo**: shift external event timestamps within the regime.
8. **Noise-transmission test (M9)** where decomposed on-chain flow allows.
9. **Equivalence/futility testing** against a preregistered smallest economically meaningful effect (003 lacked one).
10. **Hierarchical resampling** family → event/round → contiguous block; report per-family effects and same-direction counts (004A.2).
11. **Executable crossing markouts** net of explicit cost inputs, reported alongside logit-MSE.
12. **Romano–Wolf / BH within lane**, with the full grid counted.

## 10. Methods to avoid

- VECM information shares on non-identical contracts.
- Granger or correlation screens over large pair sets.
- Tick-level iid inference; tick counts as sample size.
- Lee–Ready, tick rule, or feed-inferred direction.
- Midpoint P&L, Sharpe ratios, profitability optimisation.
- High-capacity ML, deep nets, or post-hoc PCA factor selection with three independent families.
- Interpolated or forward-filled clock grids for lead-lag.
- Pooled PRE + ACTIVE estimation.
- Squared logit loss pooled across tail contracts without stratification.
- LLM or text-similarity pair discovery.
- Calibration regressions treating same-cycle election contracts as independent.
- Transfer entropy / DCC-GARCH / other high-parameter dependence models on this sample size.

---

## 11. Questions for the frozen 004B package (written before seeing it)

1. Which relationships were tested, and was the pair inventory frozen from identity/semantics before any outcome was examined?
2. For every lead-lag claim, is there a reverse-direction test, and what is the asymmetry?
3. Do effects survive conditioning on target quote freshness?
4. Are effects reported in executable crossing terms, and do they exceed half-spreads in the relevant price bands?
5. How do effects vary with target volume, spread and quote age (liquidity leader vs information leader)?
6. What fraction of the signal is contemporaneous vs lagged after controlling for a leave-target-out factor?
7. Are there responses clustered at externally timestamped events, and what is each market's reaction latency?
8. Are PRE_ELECTION and ACTIVE_RESULTS reported separately, per family, with same-direction counts?
9. How many independent families support each effect (maximum three), and does any effect rely on one family?
10. Were mechanical siblings (complements, partition members) excluded from predictors in "indirect" claims?
11. Are tail contracts (< 3% / > 97%) driving any logit result?
12. Were any flow features used, and how was direction obtained?
13. Were infrastructure/exchange-contract addresses excluded from any participant-level analysis?
14. What were the null distributions, and are nulls calibrated (fraction of null draws with p < 0.05)?
15. Is there a matched-liquidity unrelated-pair control?
16. Do staged-election relationships show conditional structure beyond a naive same-sign rule?
17. What coverage exists for constituent→aggregate graphs (HUN seat bins, PER legislative), and is it enough to test pivotality at all?
18. Did any effect depend on the V1/V2 splice or on the 113 fail-closed conditions?
19. What is the smallest economically meaningful effect used, if any?
20. Which effects are explicitly declared non-transferable to SIG, and why?
21. Is there any evidence about reward-farming quote behaviour contaminating L1?
22. What are the per-market eligibility counts in ACTIVE_RESULTS (004A reported BOTH-usable counts as low as 6–22 per regime)?

---

```text
OPUS PRE-004C RESEARCH COMPLETE

Sources reviewed:
Academic papers: 28 source rows in §2 (rows 1, 3–29; grouped classics in rows 21–27 counted once per row)
GitHub repositories: 5 inspected at code level (Jon-Becker/prediction-market-analysis,
  philippdubach/polymarket-microstructure, gregyoung14/openmarket,
  gordonkoehn/prediction-market-microstructure, namanhzz/prediction-market-calibration)
Practitioner/LinkedIn sources: 5 (Kalshi Research publications and team page, Kalshi-co-authored
  lead-lag paper, SIG/Kalshi market-maker announcements, Polymarket exchange documentation,
  Socos practitioner summary — the last excluded from evidence)

Mechanism classes retained:
  M1 cross-venue PM→SIG propagation (live-only)
  M2 constituent→aggregate via pivotality (ACTIVE; weak historical transfer)
  M3 latent swing → stale constituents
  M4 partition identities (constraint/diagnostic)
  M5 logical bounds (constraint)
  M6 staged-election conditional migration (historical; weak SIG transfer)
  M7 cross-institution shared state (common-factor control)
  M8 attention-constrained staleness (ACTIVE)
  M9 non-fundamental pressure transmission (identification device)
  M11 rational persistent disagreement (negative class)
  M12 scheduled public-information arrival (identification anchor)

Mechanism classes rejected:
  M10 level bias as short-horizon alpha (kept as control)
  M13 participant/protocol-identity flow (outside lane; identity unavailable on SIG)
  M14 order-book imbalance / microprice
  M15 semantic / Granger pair mining

Major unresolved questions:
  - SIG end-of-contest marking rule for unresolved contracts
  - Whether any historical family contains a testable constituent→aggregate graph with adequate coverage
  - Validity of published PM direction "ground truth" (T-19)
  - Receive-time semantics per archive (T-7)
  - How to test M1 at all before live SIG data accumulates

READY TO RECEIVE FROZEN 004B PACKAGE
```
