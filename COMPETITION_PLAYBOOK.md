# COMPETITION_PLAYBOOK.md

**2026 Susquehanna Predictions Trading Cup — Canonical Strategy & Execution Checklist**

**Status date:** 25 September 2026  
**Competition window:** 1 October 2026, 12:00 ET → 4 November 2026, 12:00 ET  
**Objective:** finish with the highest settled SUSQie account value.  
**Purpose:** answer **what we do**, not preserve every mathematical derivation. `MATHS_LEDGER.md` remains the canonical quantitative memory.

## Checkbox semantics

- `[ ]` not ready
- `[x]` complete / evidenced
- `[~]` active / partially complete
- `[-]` deliberately deferred

A discussion, equation, branch, or proposed design does **not** count as complete. `[x]` requires evidence that the operational item itself exists and works.

---

# 1. Competition thesis

The competition should be treated as a short-horizon market-microstructure and relative-value contest, not as a contest to build the fanciest election model.

Serious teams should be assumed capable of obtaining public prediction-market prices, polls, election information, LLM assistance, basic market-making theory and obvious cross-market relationships. The approximately 237 known SIG election contracts were found to have direct or near-direct external analogues; access to Polymarket or Kalshi is therefore not proprietary by itself.

The working competitive question is:

> **Can we identify and monetize temporary discrepancies faster, more safely and more reliably than other teams?**

The likely durable differentiators are therefore not “having an external price” or “knowing an equation.” They are:

- semantic mapping quality;
- synchronized, observable-time data;
- detecting whether external or structural information genuinely leads SIG;
- depth-aware execution rather than midpoint fantasy;
- fast cancellation and stale-quote control;
- realistic fill/queue modelling;
- inventory and correlated-risk discipline;
- capital efficiency;
- instrumentation and rapid experimental iteration;
- adapting quickly to the actual ecology of the tournament venue.

Luck still matters. We do not control whether large dislocations appear, whether a passive order gets filled before a move, whether competitors crowd the same signal, or how much exploitable activity exists in a 34-day sample. The system should therefore maximize the **number and quality of evidence-based decisions** rather than assume that a mathematically elegant model produces deterministic victory.

The first live days are part of the research process. October is not just a waiting room for election night; it is where we learn the venue, establish which edges actually survive execution, and compound only what earns the right to receive capital.

---

# 2. What is probably NOT an edge

Treat the following as commoditized, baseline, or non-proprietary until evidence says otherwise:

- raw Polymarket prices;
- raw Kalshi prices;
- a direct SIG ↔ external market lookup;
- basic polling averages;
- generic LLM election analysis;
- obvious public election news;
- simple midpoint differences;
- calling midpoint incoherence “arbitrage” without executable bid/ask/depth proof;
- basic implication, complement, exclusivity and partition identities;
- generic mean reversion;
- generic momentum;
- standard market-making formulas copied literally from textbooks;
- literal Avellaneda–Stoikov as a production recipe;
- standard inventory skew without correlated-election risk;
- raw external-market volume as a quality score;
- a hand-made “toxicity” score without future markout evidence;
- an enormous coherent probability model merely because it is sophisticated;
- a generic election forecaster that does not improve executable trading decisions;
- an LLM-generated strategy catalogue.

Common knowledge can still be useful infrastructure. It should not be mislabelled as proprietary alpha.

---

# 3. Potential real edges

| Candidate | Classification | Why | Evidence needed before promotion |
|---|---|---|---|
| Better semantic mapping / resolution-rule handling | **HIGH CONFIDENCE** operational edge | Bad mappings can create false signals and false arbitrage; exact semantics are necessary for every downstream model. | Machine mapping, rule signatures, audited samples, zero false hard edges in reviewed sample. See M-142. |
| Depth-aware structural / logical opportunities | **HIGH CONFIDENCE** mechanism; frequency unknown | If a verified payoff relationship is violated at executable prices and size, the edge is economically real rather than a midpoint curiosity. | Real-book replay and live certificates using bid/ask/depth. M-019, M-136. |
| Faster stale-quote cancellation / safer execution | **HIGH CONFIDENCE** operational edge | Reduces giving away free optionality during fast information moves. | Live cancel/requote latency, stale-fill rate, post-fill markouts. |
| Better instrumentation / faster learning | **HIGH CONFIDENCE** meta-edge | The Cup is short; teams that identify false hypotheses quickly waste less capital and engineering time. | Automated markouts, attribution, health metrics, champion/challenger results. |
| Conservative queue/fill modelling | **HIGH CONFIDENCE** research/execution edge | Prevents fake paper profitability and improves maker decisions. | Predicted vs realised fills and calibration on SIG. M-075–M-077. |
| Capital efficiency / selective market making | **HIGH CONFIDENCE** operational edge | Capital is finite and the objective is final account value, not quote count. | Market-level MMEV, capital/time return density and realised P&L. M-109–M-111. |
| Direct external lead/lag | **PLAUSIBLE** | Polymarket/Kalshi may update before isolated SIG books, but serious competitors can observe the same venues and common-news moves can create false lead. | Observable-time synchronized OOS tests at 1s/5s/30s/1m/5m using executable SIG returns. M-087–M-089. |
| Cross-market structural residual / mean reversion | **PLAUSIBLE** | Relative dislocations may revert even when outright price direction is hard to forecast. | OOS residual decay and executable P&L. M-121. |
| LOO-FAMILY structural fair value | **PLAUSIBLE** and strategically important | Seat distributions, thresholds, chamber control, joints and related races may contain genuinely indirect information. | Must beat the direct-equivalent baseline after excluding the entire target information family. M-133, M-140, M-141. |
| Combined maker/taker decision policy | **PLAUSIBLE** | A signal may be valuable in different ways depending on urgency, spread, depth and fill risk. | Shadow confusion matrix and net economics by action class. M-112. |
| Latency / server placement | **PLAUSIBLE**, regime-dependent | Speed matters only when the signal half-life is short enough and venue/network location makes latency material. | End-to-end benchmarks from candidate regions plus signal half-life. M-127. |
| Flow / OFI / adverse-selection prediction | **UNPROVEN** | Useful in conventional microstructure, but no SIG evidence yet. | Fill-conditioned future markout lift OOS. M-063, M-065. |
| One-factor election / seat-distribution inference | **UNPROVEN** | Structurally sensible and may improve LOO-FAMILY inference, but could add model risk without incremental trading value. | OOS calibration and target-repricing improvement. M-137, M-138. |
| Full global coherent probability surface | **UNPROVEN** | Could improve consistency, but may be needless complexity before simpler LOO-FAMILY and hard-constraint tests succeed. | Incremental OOS value over simpler champion. M-021. |
| Detailed competitor archetype / “Shadow Field” simulation | **LOW PRIORITY** | Useful for thinking about crowding, but it is speculation until live ecology is observable. | Only revisit if first-live-day behaviour suggests a measurable crowding pattern. |
| Hawkes / elaborate flow models | **LOW PRIORITY** pre-launch | Simple OFI/EWMA/fill models have not yet failed. | Only pursue after simpler flow predictors leave unexplained OOS edge. M-126. |

**Rule:** sophistication does not upgrade confidence. Evidence does.

---

# 4. Initial strategy family freeze

For the first empirical loop, active experimentation is limited to **three** families. This is an operating freeze, not a claim that the backlog is worthless.

## [~] A. Direct external lead/lag

**Hypothesis:** an observable move on a mapped Polymarket/Kalshi contract predicts a subsequent executable repricing on SIG.

**Minimum data:** synchronized external and SIG books/trades, exchange-event time where available, observable-by-us time, mapping confidence and executable depth.

**Success metric:** positive OOS executable forward return after measured latency, depth and slippage.

**Falsifier:** apparent lead disappears with observable timestamps, executable prices, common-news controls or realistic latency.

## [~] B. Simple residual / relative value

**Hypothesis:** a SIG market that deviates from the simplest direct or related-market reference tends to move toward that reference.

Start with the smallest defensible reference before LOO-FAMILY or a global probability model.

**Success metric:** residual sign/magnitude predicts future executable repricing OOS and positive net economics.

**Falsifier:** residuals do not decay OOS, only exist at midpoints, or are explained by stale/illiquid reference legs.

## [~] C. Simple selective market making

**Hypothesis:** selected SIG markets have enough spread and benign enough post-fill markouts to earn positive passive expected value.

Use a deliberately simple baseline.

**Success metric:** positive realised/shadow spread capture minus adverse selection, risk and operations cost under calibrated fill assumptions.

**Falsifier:** adverse-selection markouts dominate spread capture, fills are too sparse, or capital is trapped for poor return density.

## Challenger / research backlog — do not block launch

Preserve, but do not allow these to delay the first empirical loop:

- LOO-FAMILY structural FV;
- hard structural constraints / executable certificates;
- combined MAKE / TAKE / STRUCTURAL routing;
- global coherent probability surfaces;
- advanced flow / toxicity models;
- complex election-factor / seat-distribution models.

Promotion requires evidence from the captured-data → replay → test loop.

## Competition-research operating additions — 25 September 2026

The historical competition review adds only the following genuinely new operating requirements:

| Addition | Current decision |
|---|---|
| **Market ecology** | Test observable behavioural archetypes such as quote replenishment, sweep cadence and external-following. Do not assume this is alpha. |
| **API/message budget** | Treat request/cancel capacity, retry rate, 429/503 tails and acknowledgement latency as trading variables. |
| **Mechanics discovery harness** | First live days must explicitly test matching, partial fills, cancel races, reconciliation and recovery under documented behaviour. |
| **Integration-complexity haircut** | A strategy's theoretical edge must pay for residual-leg, latency and operational complexity before admission. |
| **Edge decay / crowding** | Track trigger count, gross edge, fill rate, markout, response lag and signal half-life through time. |
| **Position-capacity opportunity cost** | Measure good signals skipped because inventory/risk/capital is already consumed. |
| **Uncertain order state** | Execution must represent PENDING, ACKED, PARTIALLY_FILLED, CANCEL_PENDING, CANCELLED, UNCERTAIN and RECONCILED; timeout/503 never proves non-execution or successful cancellation. |

### Monday-night gate

> **By the end of Monday 28 September, at least the three initial simple strategy hypotheses must be runnable through replay using data captured by our own infrastructure.**

If not, stop adding research, maths and strategy families until the data → replay → experiment pipeline works.

### Research freeze

No new broad strategy or mathematical workstream unless it answers a failed test, implementation ambiguity, live venue observation or specific architectural decision.


# 5. Build checklist

## SIG plumbing

- [~] Authenticated read-only REST client — implemented on `build/003-sig-rest-client`, currently not merged to `main`.
- [ ] Explicit tournament resolver / context selection.
- [~] Market and exchange discovery — present on BUILD-003 branch, but dependent on explicit context.
- [~] REST orderbook snapshots — present on BUILD-003 branch; persistent/live market-state engine absent.
- [ ] Realtime/WebSocket ingestion.
- [ ] REST-authoritative reconciliation loop.
- [ ] SIG book/trade recorder.
- [~] Reliable timestamp foundation — canonical aware datetimes exist; observable network timestamps are not yet captured.
- [ ] API error/rate-limit/health instrumentation.
- [ ] Restart/recovery path that reconstructs authoritative state.

## External data

- [ ] Polymarket metadata ingestion.
- [ ] Polymarket live books.
- [ ] Polymarket live trades.
- [ ] Kalshi equivalent data where it adds useful structure or confirmation.
- [~] SIG ↔ external mapping research — approximately 237 contracts manually reviewed; production mapping store not built.
- [ ] Machine-readable mapping classes: EXACT / NEAR / DERIVED / MODEL_ONLY / NO_TRADE.
- [ ] Semantic family grouping for LOO-FAMILY.
- [ ] Resolution/void/runoff/expiry rule normalization.
- [ ] 2025/2026 historical replay data ingestion where available.
- [-] Treat 2022 data as more than a sanity check; it is not the preferred calibration regime.

## Research infrastructure

- [ ] Synchronized observable-time timestamps across SIG and external venues.
- [ ] Standard 1s/5s/30s/1m/5m forward markouts.
- [ ] Direct external lead/lag test harness.
- [ ] Structural residual / mean-reversion test harness.
- [ ] LOO-PRICE reconstruction experiment.
- [ ] LOO-FAMILY indirect-alpha experiment.
- [ ] Hard-constraint executable certificate replay.
- [ ] Conservative maker paper-fill simulation.
- [ ] Queue/fill calibration.
- [ ] OOS / chronological champion-challenger framework.
- [ ] Strategy-level P&L and capital attribution.
- [ ] Multiple-testing / parameter-stability controls.

## Execution

- [ ] Maker quoting.
- [ ] Taker logic.
- [ ] MAKE / TAKE / STRUCTURAL / NO_TRADE router.
- [ ] Cancel/requote safety and stale-quote timeout.
- [ ] Queue/fill estimates.
- [ ] Executable depth/VWAP calculations.
- [ ] Idempotent order intent / submission handling.
- [ ] Order/fill reconciliation.
- [ ] Kill switch.
- [ ] Fail-closed behaviour on stale/unreliable data.

## Risk

- [ ] Per-market exposure.
- [ ] Correlated / factor exposure.
- [ ] Inventory limits.
- [ ] Strategy limits.
- [ ] Drawdown limits.
- [ ] Stale-data / feed-health limits.
- [ ] Capital utilization / concentration limits.
- [ ] Scenario-shock view for election-wide risk.
- [ ] Central Risk approval wired before any autonomous order path.

---

# 6. Pre-Cup priorities

## MUST HAVE before 1 October

- [~] Finish review and integrate BUILD-003 authenticated read-only SIG REST.
- [ ] Build explicit tournament resolver/context selection.
- [ ] Add realtime SIG ingestion with REST reconciliation.
- [ ] Record SIG books/trades with exchange-event and observable-by-us timestamps.
- [ ] Record at least Polymarket metadata/books/trades for directly mapped markets.
- [ ] Materialize production mapping + semantic/rule checks for the first tradable universe.
- [ ] Compute standardized forward markouts and executable-depth measures.
- [ ] Implement minimal shadow execution with conservative fill assumptions.
- [ ] Implement basic exposure/inventory/stale-data risk gates and kill switch.
- [ ] Implement safe maker/taker/cancel plumbing before autonomous live orders.
- [ ] Benchmark end-to-end latency from realistic server regions.
- [ ] Run a full restart/recovery/reconciliation dry run.

## SHOULD HAVE

- [ ] Kalshi ingestion for useful equivalent/structural markets.
- [ ] LOO-PRICE and first LOO-FAMILY experiment harness.
- [ ] Executable hard-constraint scanner.
- [ ] Simple market-making baseline in shadow mode.
- [ ] Fill-conditioned markouts and first queue-depletion baseline.
- [ ] Automated daily strategy attribution and champion/challenger report.

## NICE TO HAVE

- [ ] One-factor race/seat model if LOO-FAMILY needs it.
- [ ] Scenario-shock correlated-risk view.
- [ ] Better empirical fill-hazard model after real fills arrive.
- [ ] Secondary external/reference feeds only where they add measured value.

## DO NOT BUILD YET

- [-] Giant global probability/factor graph before LOO-FAMILY beats the direct baseline.
- [-] Hawkes or similarly elaborate flow models before simple OFI/fill models earn value.
- [-] Bespoke large election-forecasting stack before venue/execution evidence says forecasting is the bottleneck.
- [-] Microservices/distributed infrastructure without a demonstrated need.
- [-] PolyLeviathan-scale UI/analytics product for a short competition.
- [-] Endless new strategy families.
- [-] Detailed competitor “Shadow Field” simulation before live ecology is observed.

---

# 7. First 1–3 live days — reconnaissance checklist

The purpose is to convert unknowns into decisions. Every metric below must answer “what changes if this is high or low?”

| Metric | Measure | Decision if high / low |
|---|---|---|
| Spread distribution | Median/tails by market, time and regime; executable not midpoint-only | Wide + benign markouts → maker opportunity; tight → favour taker/structural/selective quoting. |
| Depth | Size at best and cumulative depth/VWAP bands | Deep → larger executable size and better external hedging; thin → smaller size, stricter edge threshold. |
| Trade frequency | Trades/events per market and active-time distribution | High → richer learning and potentially faster queue turnover; low → do not fit elaborate microstructure models. |
| External-vs-SIG divergence | Direct mapped price/bid-ask differences | Frequent/persistent → prioritize direct RV; rare → reduce engineering attention on simple external anchoring. |
| External lead/lag | 1s/5s/30s/1m/5m response using observable timestamps | Positive executable lead → latency/taker engine rises in priority; absent → demote pure speed thesis. |
| Relationship-violation frequency | Verified hard/soft relationship breaches | Frequent → structural scanner deserves resources; rare → keep as opportunistic module. |
| Violation duration | Time from first executable violation to closure | Short → latency critical; long → semantic accuracy/depth matter more than colocation. |
| Fill probability | By queue state, quote age, spread, market and event regime | High → maker viable; low → widen selection or shift toward taker/structural. |
| Post-fill markouts | 1s/5s/30s/1m/5m after passive/active fills | Adverse → quote less/wider or pause; benign/positive → scale cautiously. |
| Cancel/requote latency | Signal/change → cancel accepted / quote replaced | Slow vs signal half-life → reduce passive exposure and improve placement/network path. |
| Stale-fill frequency | Fills after FV/data changed beyond allowed tolerance | High → hard operational blocker; fix before scaling. |
| Capital utilization | Capital used, tied up, turnover and expected edge per capital/time | Low with scarce opportunity → broader market coverage; high but low return density → cut trapped positions. |
| Market-making profitability | Realised/shadow spread capture less adverse selection and risk/ops cost | Positive/stable → expand; negative → reduce or retire maker strategy. |
| Bot-like competitor behaviour | Repeating sub-second reactions, synchronized repricing, predictable quote patterns | Strong → assume crowding/fast competition; weak → speed may be less decisive than selection/semantics. |
| Liquidity-provider behaviour | Quote persistence, replenishment, cancellation around news | Stable LPs → queue modelling useful; flighty liquidity → stronger event/stale controls. |
| SIG API/realtime health | Dropouts, lag, REST/realtime disagreement, rate limits | Poor → reduce automation and prioritize reconciliation/recovery. |
| Mapping breaks | Price moves inconsistent because contracts are not truly equivalent | Any material rate → halt affected signals and fix semantics before trading. |
| Reference freshness | Time since external heartbeat/book/trade independently | Stale references → invalidate external FV rather than interpret quiet books as broken feeds. |
| Strategy P&L attribution | P&L/markout by strategy and market, not aggregate only | Concentrated winners/losers → reallocate; ambiguous aggregate P&L → do not scale. |
| Leaderboard / venue ecology | Account-rank changes only as context, not as alpha | Use to understand risk appetite and crowding; never chase rank with negative-EV trades. |

### Live-day operating sequence

- [ ] Start in measurement/shadow mode where dependencies remain unproven.
- [ ] Validate that REST and realtime agree before trusting derived state.
- [ ] Validate direct mappings on live contracts before enabling cross-venue signals.
- [ ] Establish empirical fill/markout baselines before aggressive market making.
- [ ] Run direct lead/lag before assuming speed is an edge.
- [ ] Run hard-constraint scanner and separate true executable violations from midpoint noise.
- [ ] Produce end-of-day strategy attribution and explicitly promote/demote hypotheses.

---

# 8. October objective

October has three objectives:

> **PROFIT + LEARN THE VENUE + CALIBRATE THE SYSTEM**

Profit matters from day one, but “building a cushion” is not a reason to make a negative-expectancy market. A strategy that appears profitable before post-fill markouts, queue realism, stale-fill losses or capital cost is not yet profitable.

## Expand a strategy when

- OOS/shadow or live executable economics are repeatedly positive;
- the result survives conservative fill/slippage assumptions;
- performance is not concentrated in one accidental episode;
- risk/capital usage is acceptable;
- operational failure rate is low;
- parameter performance has a broad stable plateau rather than a knife-edge optimum.

## Reduce a strategy when

- edge remains positive but deteriorates with size;
- adverse-selection markouts worsen;
- capital is trapped too long;
- crowding compresses spreads or response time;
- mapping/data uncertainty rises;
- correlated inventory becomes the binding risk.

## Pause a strategy when

- data/realtime/reconciliation is unreliable;
- mapping or settlement semantics are uncertain;
- stale-fill rate spikes;
- the model enters an unseen event regime;
- realised behaviour deviates materially from paper/shadow assumptions.

## Retire a strategy when

- properly synchronized OOS tests show no incremental edge;
- profitability disappears under executable prices and conservative fills;
- the direct/simple baseline matches or beats the complex version;
- the signal half-life is consistently shorter than achievable end-to-end latency;
- semantic or operational complexity creates more loss than gross edge.

Reference: M-131 strategy graduation criterion.

---

# 9. Election-night preparation

Election night is a **different market regime**, not an automatic jackpot.

- [ ] Freeze production model versions or require explicit change control.
- [ ] Verify all external feeds and heartbeat/freshness logic.
- [ ] Re-benchmark end-to-end latency close to election night.
- [ ] Review per-market, correlated, strategy, inventory and drawdown limits.
- [ ] Replay event-jump scenarios and liquidity-withdrawal regimes.
- [ ] Reserve capital for opportunities; do not enter the event over-inventoried.
- [ ] Confirm current inventory and worst plausible correlated settlement exposure.
- [ ] Prove stale-quote cancellation under simulated fast repricing.
- [ ] Test restart/recovery/reconciliation paths under load.
- [ ] Confirm server placement using measured end-to-end performance, not guessed SIG geography.
- [ ] Verify maker/taker decision thresholds under wider spreads and faster information.
- [ ] Ensure raw-results / election-data inputs, if used, have observable timestamps and failure handling.
- [ ] Predefine conditions that force TAKE-only, MAKE-only, reduced-size or NO_TRADE operation.
- [ ] Preserve full decision/order/fill logs for post-event attribution.

Do not assume race calls are the only or best information event. If live-results inference is used, its value must be judged by observable-time lead over tradable SIG prices.

---

# 10. Competitor assumptions

These are working hypotheses, not facts.

| Assumption | Why plausible | What would prove/disprove it | Our response |
|---|---|---|---|
| Many serious teams anchor to Polymarket/Kalshi | Access is public and direct equivalents are obvious | SIG follows external moves rapidly and consistently; similar repricing patterns across markets | Treat raw external price as baseline, search for execution/structural increment. |
| Many teams use LLM research | Low barrier and common in student/professional workflows | Similar narrative reactions; no unique benefit from generic text analysis | Do not spend time building generic LLM election commentary. |
| Some teams market-make | Isolated books and spreads invite passive quoting | Persistent two-sided quotes, replenishment, stable queue competition | Measure maker economics and avoid racing to quote every market. |
| Some teams react slowly | Manual teams / weak infra may exist | Long-lived external/SIG divergence after public moves | Direct lead/lag/taker may be viable; measure duration before assuming. |
| Some teams are fast / automated | Quant competition attracts technically strong teams | Sub-second coordinated quote changes and short-lived violations | Latency and stale-quote safety become more important; demand stronger edge. |
| Some teams are over-engineered | Short competition window encourages building too much | Complex behaviour without consistent pricing; slow adaptation to venue | Keep architecture small and promote only evidenced modules. |
| Some teams trade manually | Entry barrier may permit discretionary teams | Human-scale reaction times, clustered activity around salient news | Automated consistency may help, but do not assume all counterparties are slow. |
| Competitors share the same “obvious” signals | Same public data and literature | Crowded reactions, spreads collapsing immediately after external moves | Prefer less-crowded structure, better semantics and execution rather than signal duplication. |
| Some competitors take excessive tournament risk | Winner-take-most incentives can encourage variance | Large directional inventory / abrupt rank swings | Do not imitate negative-EV risk; exploit mispricing only when measured. |
| SIG or other LP liquidity may shape the book | Tournament venue may have systematic liquidity provision | Stable repeated quote behaviour / identifiable replenishment patterns | Model the observed venue, not a theoretical peer-only market. |

---

# 11. Our biggest failure modes

1. **Over-engineering before data exists.** We can spend the remaining pre-Cup days building beautiful models that have never seen a SIG fill.
2. **Late implementation.** A mathematically superior strategy that is not reliable by 1 October is irrelevant.
3. **Bad semantic mapping.** A near-equivalent contract can turn a “risk-free” trade into basis risk.
4. **Fake paper fills.** Optimistic maker simulation can manufacture a strategy that disappears live.
5. **Slow cancellation.** A stale passive quote is a free option granted to faster traders.
6. **Unverified server assumptions.** Optimizing for an imagined SIG location can waste time while real end-to-end bottlenecks remain elsewhere.
7. **Data gaps / timestamp lookahead.** Exchange event time is not the same as information observable by us.
8. **Wrong competitor assumptions.** We should measure venue ecology rather than build around an imagined field.
9. **Overfitting.** Many markets × horizons × parameters can create false discoveries in 34 days.
10. **Too many strategies.** Fragmented implementation prevents any strategy from becoming reliable.
11. **Correlated capital traps.** “Different markets” can be the same election risk in disguise.
12. **Trading noise instead of edge.** Choppy prediction-market prices can make movement look informative when it is spread/staleness/illiquidity.
13. **Confusing reconstruction with alpha.** LOO-PRICE can succeed because equivalent siblings contain the same information; it does not prove LOO-FAMILY edge.
14. **Treating midpoint violations as executable arbitrage.**
15. **Assuming election night will rescue weak October economics.**
16. **Ignoring operational recovery.** A restart, dropped feed or reconciliation bug during a fast regime can dominate model quality.

---

# 12. STOP-DOING list

The Orchestrator should push back when we start doing any of the following:

- building mathematically elegant systems before simple baselines fail;
- adding strategy ideas faster than we test the frozen six;
- calling midpoint inconsistencies arbitrage;
- treating OFI, toxicity or flow prediction as proven;
- treating LOO-FAMILY as “our edge” before it beats direct FV OOS;
- optimizing election forecasts while ignoring execution;
- fitting sophisticated models to 2022 as if it were representative of 2026 venue quality;
- trusting minute candles for second-level lead/lag;
- using external volume as a proxy for market quality;
- assuming a Polymarket shadow trade proves SIG fill probability;
- optimizing server placement around guessed SIG geography instead of measured latency;
- spending time on competitor simulations instead of observing real competitors on 1–3 October;
- making markets simply to “build a cushion”;
- chasing leaderboard rank with negative-EV risk;
- assuming 3 November will rescue poor October performance;
- building giant infrastructure;
- extending PolyLeviathan into this project;
- promoting a model because its maths is impressive rather than because its executable OOS economics are better.

---

# 13. Canonical master checklist

The counts reported at the end refer **only to this master checklist**, so repeated planning items in earlier sections do not inflate status statistics.

## DATA

- [x] Authenticated read-only SIG REST client is merged on `main`.
- [~] Preserve working SIG market/exchange discovery and REST orderbook snapshots from BUILD-003.
- [ ] Resolve/select the active tournament explicitly.
- [ ] Ingest SIG realtime/WebSocket events.
- [ ] Reconcile realtime state to authoritative REST state.
- [ ] Record SIG books/trades with exchange-event and observable-by-us timestamps.
- [ ] Ingest Polymarket metadata/books/trades for mapped markets.
- [ ] Ingest Kalshi data where it provides useful direct or structural evidence.
- [ ] Build preferred 2025/2026 historical replay datasets.
- [ ] Detect/alert data gaps, heartbeat failure, stale reference and clock problems.

## MAPPING

- [~] Preserve the reviewed ~237-contract direct-equivalence research as a controlled mapping input.
- [ ] Materialize EXACT / NEAR / DERIVED / MODEL_ONLY / NO_TRADE mappings.
- [ ] Add normalized rule signatures and explicit semantic proof for hard edges.
- [ ] Build information-family grouping for LOO-FAMILY exclusions.
- [ ] Audit resolution timing, runoff, sworn-in-vs-wins, candidate/party, expiry and void semantics.
- [ ] Gate signals/trading by mapping confidence and disable ambiguous mappings.

## FAIR VALUE

- [ ] Implement direct external FV baseline.
- [ ] Weight references by depth/spread/freshness/mapping quality/predictive evidence rather than raw volume.
- [ ] Run LOO-PRICE reconstruction experiment.
- [ ] Run LOO-FAMILY indirect-alpha experiment.
- [ ] Compare direct vs structural vs combined FV OOS.
- [-] Defer full global coherent-surface optimisation until simpler structural tests justify it.

## STRUCTURAL

- [ ] Build audited hard-constraint graph.
- [ ] Produce executable bid/ask/depth-aware arbitrage certificates.
- [ ] Implement partition/NegRisk executable intervals.
- [ ] Implement threshold/joint/implication/exclusion checks where semantics are exact.
- [ ] Record every structural false positive and its cause.
- [-] Never promote soft statistical relationships into hard arbitrage constraints.

## MARKET MAKING

- [ ] Implement simple maker baseline.
- [ ] Add inventory-aware quoting only after the baseline works.
- [ ] Shadow maker fills with optimistic/normal/conservative assumptions.
- [ ] Measure fill-conditioned future markouts.
- [ ] Select markets using maker expected value / capital efficiency.
- [-] Defer advanced flow/Hawkes-style maker models until simple models fail.

## EXECUTION

- [ ] Implement taker order path.
- [ ] Implement maker order path.
- [ ] Implement MAKE / TAKE / STRUCTURAL / NO_TRADE router.
- [ ] Implement cancel/requote and stale-quote safety.
- [ ] Estimate queue/fill probability.
- [ ] Calculate executable depth/VWAP for intended size.
- [ ] Enforce idempotent order intents/submissions.
- [ ] Implement operational kill switch.

## RISK

- [ ] Enforce per-market exposure limits.
- [ ] Enforce correlated/factor exposure limits.
- [ ] Enforce inventory limits.
- [ ] Enforce strategy-level limits.
- [ ] Enforce drawdown limits.
- [ ] Enforce stale-data/feed-health trading blocks.
- [ ] Track capital utilization, concentration and expected edge per capital/time.

## LEARNING

- [ ] Standardize 1s/5s/30s/1m/5m markouts.
- [ ] Test external lead/lag OOS with observable timestamps.
- [ ] Test structural residual mean reversion OOS.
- [ ] Attribute P&L/markouts by strategy and market.
- [ ] Calibrate maker fill model against real SIG fills.
- [ ] Run champion/challenger comparisons on identical opportunities.
- [ ] Control overfitting with chronological holdouts, parameter stability and multiple-testing discipline.
- [ ] Review strategy promote/reduce/pause/retire decisions daily during early Cup trading.

## INFRASTRUCTURE

- [x] Modular-monolith repository foundation, Python tooling and CI exist on `main`.
- [x] Typed configuration, secret handling and canonical domain models are merged on `main`.
- [ ] Benchmark end-to-end server/network latency from candidate regions.
- [ ] Add live health/latency/reconciliation metrics.
- [ ] Test restart/recovery and authoritative state rebuild.
- [~] Canonical `DecisionRecord`/data contracts exist; wire them into actual strategy/execution logging.

## LIVE RECON

- [ ] Measure spread, depth and trade-frequency distributions.
- [ ] Measure external-vs-SIG divergence and lead/lag.
- [ ] Measure structural violation frequency and duration.
- [ ] Measure fill probability and post-fill markouts.
- [ ] Measure cancel/requote latency and stale-fill frequency.
- [ ] Characterize bot-like competitor and liquidity-provider behaviour.
- [ ] Measure capital utilization and market-making profitability.

## ELECTION NIGHT

- [ ] Freeze/control production model versions.
- [ ] Verify external feed reliability and freshness.
- [ ] Re-measure latency in the final deployment setup.
- [ ] Review all risk/capital limits.
- [ ] Test jump/liquidity-withdrawal event regimes.
- [ ] Enter with deliberate capital and inventory allocation.
- [ ] Prove stale-quote cancellation under fast repricing.
- [ ] Prove failure/restart/reconciliation paths under load.

---

# Contradictions / source tensions that must remain visible

1. **Accepted baseline vs future branch state.** BUILD-003 and EXPERIMENT-001A are merged on `main`. Future branch work must continue to distinguish proposed capability from accepted repository state; GitHub merge state and actual `main` contents remain authoritative.

2. **“Primary edge” language vs evidence.** Structural probability-graph / LOO-FAMILY work was discussed as the most promising candidate edge, but the research also explicitly requires it to beat direct-equivalent FV after costs. Therefore this playbook classifies it as **PLAUSIBLE, not proven**.

3. **Latency importance vs unknown venue geography.** Speed may be a major edge if violations/signals decay quickly, but SIG server location was not verified. The correct action is end-to-end benchmarking plus signal-half-life measurement, not assuming a colocated region.

4. **Historical data usefulness.** 2022 US midterm data was identified as a possible sanity-check dataset, but later discussion explicitly downgraded it because older prediction-market price action may be choppy and less representative. Prefer 2025/2026 data; use 2022 for robustness/sanity, not as the primary calibration regime.

5. **Market making as opportunity vs commodity.** Generic MM theory is commoditized and may be negative after adverse selection, yet venue-specific selective MM can still be profitable. The contradiction disappears only after real fill/markout data: make markets because measured MMEV is positive, not because “market making” is strategically impressive.

---

# Canonical maths references

Use the ledger rather than re-copying the equations:

- **M-019 / M-136:** depth-aware executable structural certificates.
- **M-021 / M-121:** coherent probability projection and structural residuals.
- **M-063 / M-065:** fill-conditioned adverse selection and OFI.
- **M-075 / M-076 / M-077:** fill hazard, queue depletion and conservative paper fills.
- **M-087 / M-088 / M-089:** external lead/lag, executable forward return and observable timestamp rule.
- **M-109 / M-110 / M-111:** capital efficiency and market-selection economics.
- **M-112:** MAKE / TAKE / STRUCTURAL / NO_TRADE policy.
- **M-116:** champion/challenger shadowing.
- **M-127:** signal half-life vs latency.
- **M-129:** scenario-shock risk.
- **M-131:** strategy graduation.
- **M-132:** LOO-PRICE.
- **M-133 / M-140 / M-141:** LOO-FAMILY structural FV and residual.
- **M-137 / M-138:** simple election factor and seat-distribution challenger.
- **M-142:** semantic rule-signature hard-edge gate.

---

# Current priority summary

## Five highest-priority incomplete items

1. **Build tournament selection + realtime + REST reconciliation** so we have trustworthy live SIG state.
2. **Stand up synchronized SIG recording with observable timestamps alongside the accepted Polymarket recorder**; without this, lead/lag, markouts and execution research are mostly fiction.
3. **Materialize semantic mapping / rule signatures / family grouping** so direct FV, hard constraints and LOO-FAMILY cannot leak or mis-map.
4. **Build minimal shadow execution + markouts + conservative fill model** before spending time on sophisticated strategy logic.
5. **Add live health/latency/reconciliation metrics and measure the end-to-end path** before optimizing infrastructure or sophisticated strategy logic.

## Three things currently over-prioritized

1. **Sophisticated global maths before the live data path exists.**
2. **Election-specific modelling before proving that forecasting, rather than venue mechanics/execution, is the bottleneck.**
3. **Exact server-location / colocation speculation before measuring real end-to-end latency and signal half-life.**

## Three things currently under-prioritized

1. **Synchronized observable-time recording and operational instrumentation.**
2. **Conservative fill/queue modelling plus post-fill markouts.**
3. **Semantic mapping, rule auditing and information-family de-duplication.**

---

# Operating rule

For the rest of the build:

> **Instrument first. Baseline second. Test third. Complexity only after the baseline loses.**

The Cup does not reward the most equations. It rewards the highest final account value.