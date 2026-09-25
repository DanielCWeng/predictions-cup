# RESEARCH — Major Quant Trading Competitions, Winners & Transferable Edge (2021–2026)

**Project:** 2026 Susquehanna Predictions Trading Cup  
**Research cutoff:** 25 September 2026  
**Status:** Canonical competition research + immediate operating guidance — documentation only; no trading code changed, no production state changed  
**Requested output path:** `docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md`

---

## 1. Executive findings

### Bottom line

Historical trading competitions did **not** uncover a materially new mathematical alpha family that is missing from the current Predictions Cup maths stack.

The strongest public evidence instead changes **priority**:

1. **Competition mechanics are part of the market.** Strong teams repeatedly learned the simulator, matching behavior, marking rules, bot ecology, message limits, order lifecycle and failure modes before adding mathematical sophistication.
2. **Simple, testable models repeatedly beat fragile sophistication.** Fixed or slowly moving fair values, market making, pair/basket spreads, ETF replication, simple regressions, options parity/volatility and event-response rules dominate public winning/finalist write-ups.
3. **Backtesting quality is itself an edge.** The best teams built replay tools, synchronized dashboards, grid/parameter tests, strategy versioning and full-environment simulators early.
4. **Execution complexity must be charged against theoretical edge.** Several strong competitors explicitly did *not* deploy mathematically valid ideas because live multi-leg execution, thin books, congestion or volatility made the practical expected value unattractive.
5. **Opponent / bot behavior is a legitimate observable state variable.** IMC, Jane Street and Optiver competitors repeatedly inferred recurring participant or simulator behaviors and exploited them. For SIG this must be tested using lawful public order-book/trade behavior only; do not assume counterparty identities will be available.
6. **Latency matters when the game mechanics make it matter.** The strongest direct evidence comes from Optiver Ready Trader Go and some Jane Street ETC write-ups. It is not evidence that the SIG Cup is an HFT race. The Cup needs measurement before engineering around tens of milliseconds.
7. **Operational resilience is unusually important for this Cup.** SIG's current changelog explicitly documents safe-retry/idempotency work and `503` responses when the trading engine is briefly overloaded during cancellation. Acknowledgement, cancellation uncertainty and retry state therefore belong in the execution model, not merely in HTTP plumbing.
8. **The matching-engine location remains UNKNOWN.** Prior project observations are consistent with a Vercel application/function path involving `sfo1`, but that is not proof that the trading engine itself is in Northern California.
9. **Provisional hosting choice:** start with a lightweight **Northern California / us-west-1** node and an **Ashburn / us-east-1** comparator during the first 48 hours. Do not make a permanent deployment decision until authenticated REST/order acknowledgement and realtime measurements are collected simultaneously.
10. **Do not rewrite the bot in C++ now.** If end-to-end stale-price windows are seconds or hundreds of milliseconds, Python is not the bottleneck. If measured windows are consistently tens of milliseconds and local compute is a meaningful share of the budget, revisit the critical path only then.

### Direct answer to the working hypothesis

The hypothesis was:

> We may already have most of the mathematically useful ideas, and the remaining variance may come from implementation, backtesting quality, execution, calibration and some luck.

The research tried to disprove this. It did **not** find a hidden mathematical technique used repeatedly by high-ranked teams that falls outside the current ledger's major families.

What it did find is that “implementation, backtesting and execution” should not be treated as secondary engineering after the model. In these competitions they are often the mechanism by which an otherwise ordinary model becomes competitive.

---


## Canonical Lessons for the 2026 Predictions Cup

**Evidence status:** this section is an operating synthesis of the historical evidence documented below. Historical claims remain sourced in the competition inventory, winner/finalist sections and source catalogue. The launch schedule and priority choices are **our project decisions**, not claims about what historical winners did.

### A. No hidden mathematical silver bullet was found

The competition research did **not** identify a major mathematical alpha family missing from the existing Predictions Cup research stack.

The repeatedly successful public strategy families are already represented in our work:

- simple market making;
- relative value;
- pairs and baskets;
- synthetic replication;
- lead/lag;
- simple regression;
- inventory and risk control;
- queue/fill awareness;
- event response.

> **Canonical decision: more maths is no longer the default next action.**

A new model now needs a concrete reason to exist: a failed baseline, a measured venue effect, a live implementation ambiguity, or evidence that the existing model family cannot explain a repeatable opportunity.

### B. Mechanics are part of the strategy

Strong competition teams repeatedly learned the game they were actually trading before adding sophistication. Relevant mechanics included:

- matching behaviour;
- order lifecycle;
- message limits;
- queue mechanics;
- cancellation behaviour;
- simulator quirks;
- position limits;
- competitor/bot ecology;
- operational failure modes.

For SIG, **first-live-day venue discovery is a first-class research activity**. We should measure the actual market rather than build around assumptions imported from Polymarket, equities, options or another competition.

### C. Backtesting and replay quality are an edge

> **A mediocre strategy tested honestly can be more valuable than a sophisticated strategy tested with unrealistic fills.**

Replay, synchronized observable timestamps, queue/fill assumptions, executable depth, post-fill markouts and reproducibility are core trading infrastructure.

A strategy does not become promising because a midpoint chart looks good. It becomes promising when the result survives observable-by-us timestamps, executable bid/ask and depth, conservative fills, realistic order state, chronological holdouts and restart/replay reproducibility.

### D. Theoretical edge must pay an implementation tax

Use the following concept when admitting a strategy:

~~~
net usable edge
=
theoretical executable edge
− slippage
− residual-leg risk
− latency risk
− operational / integration complexity
~~~

A mathematically valid four-leg opportunity is not automatically preferable to a simpler one-leg opportunity. Complexity consumes fill certainty, request budget, cancellation capacity, risk capacity, engineering attention and recovery surface.

### E. API and cancellation capacity are economic variables

Track these as trading inputs:

~~~
request budget
cancel budget
retry behaviour
429/503 tails
ack latency
uncertain-order state
stale exposure
~~~

The SIG documentation reviewed in the underlying research specifically makes retry/idempotency and overloaded cancellation behaviour operationally relevant. These variables belong in execution economics and attribution, not only in HTTP logs.

### F. Explicit uncertain order states are mandatory

Future execution should understand at least:

~~~
PENDING
ACKED
PARTIALLY_FILLED
CANCEL_PENDING
CANCELLED
UNCERTAIN
RECONCILED
~~~

A timeout, transport error or 503 must never be interpreted automatically as:

~~~
order did not happen
~~~

or:

~~~
cancel succeeded
~~~

REST reconciliation remains the authoritative recovery path unless later platform evidence requires a more precise rule.

### G. Market ecology is an empirical feature family

The research supports testing **observable behavioural archetypes** without requiring counterparty identity.

Candidate behaviours include:

~~~
stable quote replenisher
fast external-price follower
stale liquidity
periodic sweeper
inventory-clearing behaviour
event-driven burst trader
~~~

> **This is an empirical feature family to test, not an assumed edge.**

It should be promoted only if observable behaviour predicts future price, fill, markout or response timing out of sample after controlling for basic spread/depth/external-FV state.

### H. Edge decay and crowding must be measured

This is especially important for obvious Polymarket → SIG and Kalshi → SIG lead/lag.

Track through the Cup:

~~~
trigger count
gross edge
future markout
fill probability
SIG response time
signal half-life
~~~

A strong day-one external lead does not imply a strong week-three lead. Competitors may converge quickly on the same public reference prices.

### I. C++ is available, but not currently justified

We can implement latency-critical components in C++ if the evidence eventually requires it.

The current decision is:

> **Stay in Python unless measurement proves local compute is a material bottleneck.**

If stale-price windows are measured in seconds or hundreds of milliseconds, Python/asyncio is likely adequate. Revisit a compiled critical path only if live timing shows local compute is a meaningful share of an exploitable window.

---

# Six-Day Launch Plan — 25 September to 1 October 2026

**Status:** project operating decision. This schedule is not a historical research finding.

## Friday 25 September — CLOSE THE FOUNDATION

**Primary objective:** end broad research and close the remaining foundational plumbing.

- [ ] BUILD-003 receives its requested review corrections.
- [ ] BUILD-003 is independently re-reviewed.
- [ ] Merge BUILD-003 only when clean.
- [ ] Review EXPERIMENT-001A as soon as it lands.
- [ ] Begin the research freeze.

### Research freeze

No new broad mathematical or strategy research unless it answers:

~~~
a failed test
an implementation ambiguity
a live venue observation
a specific architectural decision
~~~

Interesting ideas alone are no longer sufficient reason to open a workstream.

## Saturday 26 September — START COLLECTING REAL DATA

**Primary objective:** begin accumulating data that can actually falsify our ideas.

- [ ] Polymarket recorder running continuously.
- [ ] Polymarket market metadata stored.
- [ ] Polymarket live books stored.
- [ ] Polymarket trades stored where available.
- [ ] source_timestamp and observed_at are distinguished.
- [ ] SIG tournament/context selection functioning.
- [ ] SIG REST snapshots recordable.
- [ ] Recording is persistent and restart-safe.

**End-of-day standard:** we own real replayable data.

## Sunday 27 September — TRUSTWORTHY LIVE STATE

**Primary objective:** build confidence that our state is correct before strategies depend on it.

- [ ] SIG realtime ingestion.
- [ ] REST-authoritative reconciliation.
- [ ] Reconnect/recovery path.
- [ ] Realtime state invalidated correctly on disconnect.
- [ ] Freshness/heartbeat monitoring.
- [ ] Persistent SIG market-state recording.
- [ ] Observable timing instrumentation.

Run soak tests. Intentionally disconnect and restart components and prove recovery.

## Monday 28 September — FIRST REAL EXPERIMENTS

**Primary objective:** run strategies through replay against data captured by our own infrastructure by Monday night.

### Strategy 1 — Direct external lead/lag

Test external move → future SIG executable repricing at:

~~~
1s
5s
30s
1m
5m
~~~

using observable timestamps.

### Strategy 2 — Simple residual / relative value

Test target minus the simplest direct / related-market reference before building a large structural probability model.

### Strategy 3 — Selective simple market making

Do not deploy live yet. Start evaluating:

~~~
spread capture
future markouts
depth
quote age
inventory
~~~

under simple, explicit assumptions.

### Standard research outputs

Standardize markouts, executable depth, VWAP, signal timestamps, future price movement and strategy attribution.

> **MONDAY-NIGHT GATE — By the end of Monday 28 September, the project must be capable of running at least the first simple strategy hypotheses through replay using data captured by our own infrastructure.**

If that is not true:

~~~
stop commissioning research
stop adding mathematics
stop adding strategy families
~~~

and focus exclusively on fixing the data → replay → experiment pipeline.

## Tuesday 29 September — EXECUTION SIMULATION

**Primary objective:** stop evaluating strategies using imaginary fills.

Build conservative paper execution with at least:

- [ ] maker simulation;
- [ ] taker simulation;
- [ ] optimistic / normal / conservative fill assumptions;
- [ ] queue context;
- [ ] executable-depth VWAP;
- [ ] order lifecycle state machine;
- [ ] cancellation state;
- [ ] idempotency model;
- [ ] uncertain-order handling;
- [ ] capital usage;
- [ ] strategy attribution.

Replay previously captured data through it. Aggressively reject strategies whose paper economics disappear under realistic execution.

## Wednesday 30 September — LAUNCH REHEARSAL

**Primary objective:** prove that the system can fail safely before the Cup begins.

Target **minimal live-capable execution plumbing**, not every research feature.

Test:

- [ ] place-order lifecycle;
- [ ] cancellation;
- [ ] reconciliation;
- [ ] retries;
- [ ] idempotency;
- [ ] 429 handling;
- [ ] 503 handling;
- [ ] stale-data trading blocks;
- [ ] exposure limits;
- [ ] kill switch;
- [ ] restart during operation;
- [ ] reconnect during operation;
- [ ] state rebuild.

Run deployment-region latency comparison where practical:

~~~
Northern California / us-west-1
Northern Virginia / us-east-1
London as control
~~~

Do not choose the final server location from Vercel headers alone.

## Thursday 1 October — OBSERVE FIRST, THEN TRADE

Competition begins.

**Initial objective:** measure the actual SIG ecology.

Verify REST ↔ realtime consistency, spread, depth, trade frequency, external/SIG lag, quote persistence, cancellation latency, fill probability, post-fill markouts, relationship violations, competitor response speed, rate limits and 503 behaviour.

Then enable only the smallest execution path supported by evidence:

~~~
OBSERVE
↓
SHADOW
↓
LIMITED LIVE
↓
SCALE ONLY AFTER EVIDENCE
~~~

---

## Initial Strategy Family Freeze

Active experimentation begins with only:

1. **Direct external lead/lag** — fastest and cheapest hypothesis to falsify.
2. **Simple residual / relative value** — direct or related-market residual before global structural complexity.
3. **Simple selective market making** — only where spread and post-fill markouts justify it.

Keep the following preserved in the research backlog / challenger set:

~~~
LOO-FAMILY structural FV
hard structural constraints
global coherent probability surface
advanced flow / toxicity
complex election-factor models
~~~

They may be promoted later. They must **not block the first empirical trading loop**.

## What we are deliberately not doing

Until evidence changes the decision:

- no new broad research by default;
- no C++ rewrite;
- no expensive HFT infrastructure;
- no giant global solver blocking launch;
- no endless strategy generation;
- no optimistic paper fills;
- no assumed SIG matching-engine geography.

---

## 2. Competition inventory

### Reading the table

- `UNKNOWN` means the public evidence reviewed did not support a confident value.
- “Live” below means real-time simulated exchange interaction, not real-money trading.
- Jane Street ETC events are regional/campus events; multiple “winners” in the same calendar year may therefore refer to different ETCs.
- University competitions are included where major quant firms sponsor, judge, design cases or recruit through them.
- The inventory is not intended to imply that every sponsoring firm designed every case.

| Year | Competition | Host / sponsor | Venue | Asset / game | Structure | Team | Duration | Scoring | Initial capital | Data | Live/sim | MM | RV | Latency | Risk | Podium / public placement | Public technical write-up? | Source |
|---|---|---|---|---|---|---:|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026 | Susquehanna Predictions Cup — Midterms | SIG / TheSuper.Market | Online | Binary election contracts | P2P order books, market/limit, API bots | Individual | 1 Oct–4 Nov | Final SUSQie account value | 100,000 SUSQies | Cup market data/API | Simulated | Yes | Yes | Potential | Yes | Pending | N/A | [S1–S4] |
| 2023 | Ready Trader Go | Optiver | Online | ETF + future | Simulated exchange; hedge + regular orders | Individual | Qualifiers + top-16 final | Hard-limit compliance then P&L; tie criteria | UNKNOWN | Test + historic order/trade data | Live simulated | Yes | Yes | **Yes** | **Yes** | 1 KPW / Jakub Szulc; 2 JKK; 3 FrankfurtHedgehogs | **Excellent** | [O1–O4] |
| 2023 | Prosperity | IMC | Online | Synthetic products | Multi-round Python algo + manual | Team | Multi-round | Virtual P&L | UNKNOWN | Historical round data | Simulated | Yes | Yes | Limited | Yes | 1 StartupVacuumBubble; 2 Stanford Cardinal; 3 Unger | **Excellent** | [I1–I4] |
| 2024 | Prosperity 2 | IMC | Online | Synthetic products | Multi-round Python algo + manual | Team | ~15 days | Virtual P&L | UNKNOWN | Historical round data | Simulated | Yes | Yes | Limited | Yes | 1 Milo Knell's team (team name not recovered); 2 Linear Utility; 3 UNKNOWN | **Excellent** | [I5–I6] |
| 2025 | Prosperity 3 | IMC | Online | Synthetic products | Multi-round Python algo + manual | Team | ~15 days | Virtual P&L | UNKNOWN | Historical round data | Simulated | Yes | Yes | Limited | Yes | 1 Heisenberg; lower global podium not confidently recovered | **Strong** | [I7–I10] |
| 2026 | Prosperity 4 | IMC | Online | Synthetic products | Five rounds, algo + manual | ≤5 | Multi-round | Virtual P&L | UNKNOWN | Round data | Simulated | Yes | Yes | Limited | Yes | 1 Seven Deuce Capital; lower podium UNKNOWN | **Strong** | [I11–I14] |
| 2022 | Electronic Trading Competition — Seattle | Jane Street | Regional | ETFs / related instruments | Team electronic trading | Team | Day event incl. final hour | P&L | UNKNOWN | Sim exchange state | Live simulated | Yes | **Yes** | Some | Yes | Final Hour 1st team included Hazel Sudzilouski; another public post reports overall 2nd | Moderate | [J1–J2] |
| 2023 | Electronic Trading Competition | Jane Street | Regional | Multi-instrument | Electronic trading | Team | Day event; ~5h + final hour in one write-up | P&L | UNKNOWN | Sim exchange | Live simulated | Yes | Yes | Some | Yes | Scott Loo's team reports 1st / 29 and 1st in both rounds | Moderate | [J3] |
| 2024 | Electronic Trading Competition | Jane Street | Regional | ETFs / related instruments | Electronic trading | Team | ~6h in public repo | P&L | UNKNOWN | Sim exchange | Live simulated | Yes | **Yes** | Some | Yes | Tentacool reports 1st in one regional event; Poliwag reports second-highest P&L in another | **Strong** | [J4–J6] |
| 2025 | ETC — Adelaide | Jane Street | Adelaide | Multi-instrument / ETF | Electronic trading under simulated congestion | Team | Day event | P&L | UNKNOWN | Sim exchange | Live simulated | Yes | Yes | **Yes** | Yes | Asrith Atluri's team reports 1st | **Strong** | [J7] |
| 2021 | Rotman International Trading Competition | Rotman | Toronto / virtual-era format | Multiple cases | RIT Market Simulator | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | Yes | Yes | Some | **Yes** | 1 Baruch A; 2 Baruch B; 3 Ottawa | Moderate | [R1–R2] |
| 2022 | RITC | Rotman | Toronto / virtual-era | Multiple cases | RIT Market Simulator | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | Yes | Yes | Some | Yes | Baruch 1st and 2nd; 3rd not confidently recovered | Moderate | [R3] |
| 2023 | RITC | Rotman | Toronto | Options, electricity, ETF/liquidity, algo | Multi-case | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | Yes | Yes | Some | **Yes** | 1 Baruch; 2 Warsaw; 3 Calgary | Moderate | [R4] |
| 2024 | RITC | Rotman | Toronto | Commodities, algo forecast, liquidity, electricity, options | Multi-case | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | Yes | Yes | Some | **Yes** | 1 Baruch; 2 Toronto; 3 Calgary | Moderate | [R5] |
| 2025 | RITC | Rotman | Toronto | M&A, commodities, sales/trading, CAPM, quant arb | Multi-case | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | Yes | Yes | Some | **Yes** | 1 Calgary; 2 Baruch; 3 Warsaw | Moderate | [R6] |
| 2026 | RITC | Rotman | Toronto | Volatility, liquidity risk, algo MM, electricity, merger arb | Multi-case | Team | 2 days | Multi-case score | Case-specific | Case files/news | Simulated | **Yes** | **Yes** | Some | **Yes** | 1 Baruch; 2 Warsaw; 3 LUISS Rome | Moderate | [R7–R8] |
| 2022 | UChicago Trading Competition | UChicago + industry | Chicago | Market-making / quant cases | Team case competition | Team | ~2 days | Aggregate case performance | UNKNOWN | Case data | Simulated | Yes | Yes | Some | Yes | 1 UChicago; 2 Michigan; 3 Duke | Limited | [U1] |
| 2023 | UChicago Trading Competition | UChicago + industry | Chicago | Market-making / quant cases | Team case competition | Team | ~2 days | Aggregate case performance | UNKNOWN | Case data | Simulated | Yes | Yes | Some | Yes | 1 Colorado Boulder; 2 UChicago; 3 Johns Hopkins | Limited | [U1] |
| 2024 | UChicago Trading Competition | UChicago + CTC/Citadel/DRW/Flow/HRT/IMC/JS/2S/OMS/SIG/Tower etc. | Chicago | MM, options, time series | Team case competition | Team | 2 days | Aggregate case performance | UNKNOWN | Case data | Simulated | **Yes** | Yes | Some | Yes | 1 UPenn; 2 Princeton; 3 UChicago | Moderate | [U1–U2] |
| 2025 | UChicago Trading Competition | UChicago + industry | Chicago | Trading cases | Team | 2 days | Aggregate case performance | UNKNOWN | Case data | Simulated | Yes | Yes | Some | Yes | 1 Stanford/UT; 2 Colorado Boulder; 3 Cornell/MIT/UVA grouping as published | Limited | [U1] |
| 2026 | UChicago Trading Competition | UChicago + sponsors | Chicago | Live MM + portfolio optimisation; stocks/options/ETF/Fed-rate prediction markets | 12×15m live rounds + OOS portfolio case | Team | 3h live + case work | Case score / P&L | UNKNOWN | Live case feeds + historical/OOS | Simulated | **Yes** | **Yes** | Some | **Yes** | Podium not recovered in this review | **Excellent participant write-up** | [U3] |
| 2024 | University Trading Challenge | CME Group | Online | CME futures | CQG realtime simulation | 3–5 | ~4 weeks | Simulated account results | UNKNOWN | Futures prices + news | Simulated | Some | Yes | Some | Yes | 1 Indiana; 2 Tec de Monterrey; 3 ESLSCA Paris | Limited | [C1–C2] |
| 2025 | University Trading Challenge | CME Group | Online | CME futures | CQG realtime simulation | 3–5 | ~4 weeks | Simulated account results | UNKNOWN | CME/Dow Jones/Hightower | Simulated | Some | Yes | Some | Yes | 1 Universidad de Monterrey; 2 UChicago; 3 UIC | Limited | [C1,C3] |
| 2024 | Virtual Quant Trading Challenge | Akuna Capital | Online | Market making | Python bot vs simulated exchange/models | Individual | ~week / 3–5h stated workload | Profitability | UNKNOWN | Challenge data | Simulated | **Yes** | Some | Some | Yes | Winner UNKNOWN | Limited | [A1] |
| 2026 | Virtual Quant Trading Challenge | Akuna Capital | Online | Market making | Bot vs Akuna models and competitors | Individual | 17–21 Aug; 5–8h stated workload | Most profitable | UNKNOWN | Simulated feed | Simulated | **Yes** | Some | Some | Yes | Winner UNKNOWN publicly | Limited | [A2] |
| 2026 | Market Madness | DRW | Online | NCAA advancement contracts | Futures-like event contracts, API permitted | Individual/team rules vary | Tournament window | P&L | UNKNOWN | Event market data | Simulated | Some | **Yes** | Event-speed possible | **Yes** | Top-3 not recovered | Good rules, no technical winner write-up found | [D1–D2] |
| 2024 | Trading Invitational | Citadel / Citadel Securities | Invitational | Trading games | Manual + algorithmic rounds per participant account | Individual / teams | Event | UNKNOWN | UNKNOWN | Simulated | Simulated | Yes | Yes | Some | Yes | Winner public archive not recovered | Limited | [CT1–CT2] |
| 2026 | Probability Cup | Jump Trading | SportsPredict | Soccer prediction | Forecast tournament, not order-book trading | Individual | 11 Jun–19 Jul | Forecast performance | N/A | Sports data | Forecast | No | No | No | Calibration risk | Winner not recovered here | Moderate, **adjacent only** | [JP1] |
| 2026 | Trading League / Cricket League | Da Vinci | IIT Bombay / online | Market-making / game theory | Trading games | Student | Short event | Game-specific | UNKNOWN | Game state | Simulated | Yes | Some | Speed/game theory | Yes | Winners not recovered | Limited | [DV1–DV2] |
| 2026 | Quant Trading Days | Flow Traders | Flow trading floor | Price / arbitrage / hedge simulations | Live simulations | ~15 selected students | 2 days | Exercise-specific | UNKNOWN | Simulated markets/news | Simulated | Yes | Yes | Some | Yes | No public leaderboard | Limited; recruiting event rather than open contest | [F1] |
| 2026 | Berkeley Trading Competition | Traders at Berkeley + sponsors incl. Citadel, JS, Optiver, Jump, DRW, SIG, Five Rings, HRT, Flow, Virtu | Berkeley | Live trading + quant research | Multiple games | Student | 2 days | Top scorers across games | UNKNOWN | Game data | Simulated | Yes | Yes | Some | Yes | Winners not recovered | Rules/format public; strategy archive sparse | [B1] |
| 2026 | Yale Undergraduate Trading Competition | Yale SQFO, presented by HRT + sponsors | Yale | Sponsor-designed trading games | Two-day simulated games | Student | 2 days | Game-specific | UNKNOWN | Game state | Simulated | Yes | Yes | Some | Yes | Winners not recovered | Sparse | [Y1] |
| 2024 | Limestone Data Challenge | Tower Research | IIT Bombay + IIT Delhi | Quant/data problem | Data challenge | Team | UNKNOWN | Challenge score | N/A | Dataset | Offline | No | Maybe | No | Model risk | Named winner groups published | Sparse; adjacent rather than trading sim | [T1] |

### Firm-by-firm coverage of the requested list

| Firm | Public competition evidence found? | Interpretation |
|---|---|---|
| Susquehanna / SIG | **Yes** | Current Predictions Cup; also sponsor of UChicago/Berkeley/Yale ecosystem. |
| Jane Street | **Yes** | ETC regional events; strong participant evidence. |
| IMC | **Yes** | Prosperity is the richest public technical archive in the sample. |
| Optiver | **Yes** | Ready Trader Go 2023 provides unusually detailed mechanics + winner interview. |
| Citadel / Citadel Securities | **Yes, but sparse** | Trading Invitational exists; public detailed winner/strategy archive is weak. Major sponsor of university comps. |
| Hudson River Trading | **Yes as presenter/sponsor; no comparable standalone archive found** | Presented YUTC 2026; active sponsor of university trading competitions. |
| DRW | **Yes** | Market Madness plus major university sponsorship. |
| Jump Trading | **Yes, adjacent** | Probability Cup is forecasting rather than exchange execution; major university sponsor. |
| Akuna Capital | **Yes** | Virtual Quant Trading Challenge, simulated MM. |
| Flow Traders | **Yes, academy-style** | Quant Trading Days; sponsor of university comps. |
| Two Sigma | **No standalone public trading contest with usable winner strategy archive found** | Strong UChicago involvement / campus programming. |
| Virtu | **No comparable standalone contest found** | Repeatedly sponsors Baruch RITC participation and Berkeley. |
| Five Rings | **Trading games + university sponsorship; no comparable standalone public archive found** | Campus Trading Games and Berkeley/Traders competitions. |
| Tower Research | **Adjacent challenge found** | Limestone Data Challenge; UChicago sponsor. |
| XTX Markets | **No meaningful public trading contest found** | Public education support skews toward maths/science talent rather than a trading competition. |
| Maven Securities | **No sufficiently documented standalone public competition found** | Public student activity exists, but not enough primary evidence for a competition inventory row. |
| Da Vinci | **Yes** | Trading League / Cricket League student market-making games. |
| Old Mission | **No standalone public archive found** | Active UChicago sponsor / case ecosystem participant. |
| Headlands | **No meaningful public competition found** | Careers/campus presence found; no primary competition archive sufficient for this task. |
| QuantCo / similar | **No directly relevant public trading competition found** | Excluded generic maths/data competitions without direct trading relevance. |

The absence statements above mean **“not found in this research after targeted searches”**, not proof that a private/internal event never occurred.

---

## 3. Winner / finalist research

### 3.1 Optiver Ready Trader Go 2023 — the cleanest latency/mechanics case

Optiver's game is unusually informative because the rules expose the microstructure constraints.

Documented mechanics included:

- an ETF and future;
- simulated exchange with price-time priority;
- maker/taker economics;
- strict position and unhedged exposure rules;
- a maximum order-operation/message rate;
- limits on active orders and active volume;
- provided simulator and historic order/trade data;
- hard-limit compliance as a ranking prerequisite before P&L.

Winner **Jakub Szulc / KPW** described a development process that is much more instructive than the final formula:

- start with a simple top-of-book strategy;
- produce many benchmark versions;
- trial and error rather than commitment to one elegant model;
- continually rewrite based on actual tournament behavior;
- treat speed as important;
- observe that competitors could trade between exchange ticks / probe future prices, consuming scarce message capacity;
- recognize that cancellation lag was costly;
- change order lifecycle behavior toward more aggressive fill-and-kill behavior rather than relying on slower resting/cancel cycles.

**Transfer to SIG:** the mathematical idea is not new. The lesson is to model **message budget + lifecycle + acknowledgement + cancellation state as economic variables**.

### 3.2 IMC Prosperity 2023 — Stanford Cardinal, 2nd / 7007

The Stanford Cardinal public repository is a particularly good negative-evidence source.

Documented strategies:

- fixed fair value + market-taking / market-making;
- short linear regression for a drifting product;
- pair trading;
- basket replication;
- trader-behavior signal;
- backtester added during later rounds.

Documented failures / rejected ideas:

- online regression did not work well and interacted badly with platform/runtime bugs;
- momentum underperformed pair trading;
- a market-making idea looked good on average but a sharp regime move destroyed it;
- a momentum strategy varied from large loss to large gain under small parameter changes, so they removed it;
- the team explicitly became more rigorous about avoiding overfit / complicated models given little test data.

**Transfer to SIG:** this is almost a direct argument for champion/challenger simplicity, parameter-sensitivity checks and kill rules.

### 3.3 IMC Prosperity 2024 — Linear Utility, 2nd globally

Linear Utility's repository contains one of the best public descriptions of how competition alpha is actually found.

They built:

- a backtester designed to replicate the trading environment;
- parameter injection + grid search;
- a synchronized visual dashboard to inspect local anomalies and missed/undesirable trades;
- versioned strategy files by round.

Their alpha evolution repeatedly moved from generic statistical ideas toward game-specific structure:

- a fixed-value product was traded around a stable fair;
- a drifting product used a local moving fair;
- they noticed a large market-making bot whose quotes were less noisy than the ordinary midpoint and used its midpoint as a better reference;
- they tested the website marking behavior and inferred the simulator's internal economics;
- supplied sunlight/humidity/tariff data produced correlations/regressions that they ultimately judged unconvincing;
- understanding the *trading mechanism* revealed a large arbitrage against a recurring taker;
- after the arbitrage became crowded, they optimized/adapted the edge;
- basket vs synthetic spread mean reversion became another main family.

**Transfer to SIG:** a mechanics-discovery harness and adversarial simulator validation are first-class research tasks. “Given data” may be a distraction; venue behavior can dominate.

### 3.4 IMC Prosperity 2025

Public repositories and finalist posts continue the same pattern:

- market making around simple fairs;
- basket / ETF-style statistical arbitrage;
- options/volatility pricing and delta hedge where the game supported it;
- trader/bot behavior as a signal;
- explicit decision not to trade a component when replication did not show practical profitability;
- infrastructure and replay tooling receiving significant attention.

One highly useful public theme is that a strategy can be mathematically coherent yet fail the competition because fills, position limits or the simulated counterparty process do not support the expected P&L.

### 3.5 IMC Prosperity 2026

The strongest public participant reports emphasize:

- repeated iteration;
- simple/robust systems;
- honest validation;
- clean metrics and integration;
- aggressive but controlled hedging;
- killing ML/correlation ideas when validation is weak;
- recognizing regime changes.

One public account explicitly describes an overfit cross-correlation/basket approach that performed badly in the final round after lacking a defensible economic rationale.

**Transfer to SIG:** require both OOS evidence and a plausible mechanism before promoting a model.

### 3.6 Jane Street ETC

Public material is fragmented because ETCs are regional, but the repeated ideas are recognizable:

- market-making spread capture;
- bid/ask arbitrage;
- ETF / basket arbitrage;
- inter-market spreading;
- pennying / price priority;
- trend following in some teams;
- opponent adaptation from end-of-round positions / behavior;
- execution speed under simulated network congestion;
- risk discipline: one 2025 winning participant describes withholding a high-variance ETF strategy during volatility to preserve an existing lead.

**Transfer to SIG:** a tournament objective is not identical to maximizing isolated trade EV. Drawdown / rank / remaining horizon can rationally change the strategy admission threshold.

### 3.7 Rotman International Trading Competition

RITC is less useful for discovering a secret algorithm, but extremely strong evidence for **robustness across cases**.

Baruch's repeated success matters because the team changes, while the program repeatedly performs well. Public sources emphasize:

- preparation;
- role specialization / trust;
- rapid but precise decision-making;
- liquidity risk;
- volatility trading;
- algorithmic market making;
- merger arbitrage / electricity / commodities depending on year;
- consistency across multiple independent cases.

In 2026 Baruch won volatility trading and liquidity risk, placed second in algo market making, and placed top-five in additional cases.

**Transfer to SIG:** avoiding a catastrophic strategy family may matter more than squeezing the last few basis points out of the best backtest.

### 3.8 UChicago 2026 — unusually relevant execution negative evidence

A detailed 2026 participant write-up describes:

- 12 live 15-minute rounds over about three hours;
- a live market-making case;
- a separate pre-submitted/OOS portfolio-optimization case;
- stocks, options, an ETF and a Fed-rate prediction-market element.

The participant built options parity/box logic and ETF arbitrage logic but did not deploy some of it because:

- the theoretical spread did not adequately compensate for execution complexity;
- thin books made multi-leg execution unattractive;
- operational risk exceeded likely edge.

**Transfer to SIG:** this should become an explicit **integration-complexity haircut** in strategy admission, especially for multi-leg structural trades.

---

## 4. LinkedIn findings

LinkedIn was useful primarily as a way to locate participant descriptions, not as a source of secret code.

High-value public posts found include:

- **Optiver RTG third-place FrankfurtHedgehogs participants:** discussion of speed, implementation language, studying previous matches/other teams and iterative adaptation.
- **IMC Prosperity winners/finalists:** repeated descriptions of iteration, simple systems, team division, infrastructure and anti-overfit decisions.
- **Jane Street ETC regional winners:** bid/ask + ETF arbitrage, adaptation to other teams, and execution under simulated congestion.
- **RITC participants/coaches:** preparation, consistency, liquidity risk, algo MM and multi-case performance.
- **UChicago / sponsor posts:** confirm sponsor ecosystem and case categories.

### What LinkedIn did **not** reveal

It did not uncover a credible recurring “secret” mathematical technique absent from the existing ledger.

The signal in the LinkedIn corpus is overwhelmingly:

```text
build something simple
→ simulate it honestly
→ inspect what actually happened
→ change fast
→ control risk
→ exploit game mechanics only when verified
```

That is useful, but it is an execution/research-process finding rather than a new pricing model.

---

## 5. GitHub / public write-up findings

### Repositories inspected in depth

#### A. `ericcccsliu/imc-prosperity-2` — Linear Utility, IMC Prosperity 2024, 2nd globally

Most useful assets:

- full round-by-round research;
- backtester;
- visualization/dashboard tooling;
- raw data/log workflow;
- strategy versions;
- documented failed correlations;
- documented environment/mechanics discoveries.

This is the most transferable public repository for our purposes.

#### B. `ShubhamAnandJain/IMC-Prosperity-2023-Stanford-Cardinal` — 2nd / 7007

Most useful lessons:

- simple strategies first;
- momentum lost to cleaner pairs/RV;
- backtester eventually became central;
- reject parameter-fragile strategies;
- avoid complicated models with insufficient test data.

#### C. `denfujita/Jane-Street-ETC` — Poliwag, 2024 ETC

Brief but useful confirmation of the strategy family:

- market making;
- trend following;
- inter-market spreading;
- arbitrage;
- pennying;
- Python implementation.

### Additional public repositories / technical archives surfaced

At least three additional useful Prosperity repositories/write-ups were located for 2025–2026, including high-ranked US/global competitors and infrastructure-oriented reports.

### GitHub conclusion

Public code confirms that high-ranking strategies are not generally exotic. The differentiator is often:

- faithful replay;
- parameter robustness;
- fill logic;
- position handling;
- opponent/simulator inference;
- disciplined removal of strategies that look good only under one backtest interpretation.

---

## 6. Repeating winning patterns

I used the 12 most technically useful participant/winner/finalist sources as a qualitative sample rather than manufacturing percentages from a weak universe.

| Pattern | Explicit evidence in technical sample | Interpretation |
|---|---:|---|
| Market making / relative-value / replication families | at least 9 / 12 | Core repeated family; already in our stack |
| Fast iteration / repeated testing / replay | at least 8 / 12 | Very strong process signal |
| Explicit simplicity / anti-overfit / robustness | at least 7 / 12 | Stronger than “use more maths” |
| Opponent / bot / simulator-mechanics inference | at least 6 / 12 | Important potential Cup-specific edge |
| Inventory / position / risk controls materially affecting P&L | at least 6 / 12 | Core production requirement |
| Latency / code speed / congestion explicitly material | about 3 / 12 | Real, but **not universal**; must be measured |
| Team specialization / coordinated workflow | common in team events, but not consistently documented enough for a clean count | Relevant process lesson, less relevant to an individual Cup entry |

### Pattern 1 — simple robust fair values

Examples include:

- fixed fair around a stable synthetic;
- local rolling fair;
- large-liquidity participant quote as better fair;
- ETF synthetic;
- basket spread;
- pair ratio;
- options parity / implied volatility.

This is not evidence against sophisticated models in general. It is evidence that **model complexity needs to earn its keep under tiny samples and game-specific microstructure**.

### Pattern 2 — mechanics before sophistication

Repeatedly:

- understand fill rules;
- understand how P&L is marked;
- understand position/hedging constraints;
- understand message limits;
- understand simulator/bot behavior;
- understand whether a theoretical arbitrage can actually be executed.

### Pattern 3 — replay and visualization

The best public teams repeatedly built:

- local backtest/replay;
- exact or near-exact platform log formats;
- dashboards;
- synchronized timestamps;
- parameter sweeps;
- per-round/versioned strategies.

For the Cup, the analogue is a deterministic market-state replay with all externally observed timestamps preserved.

### Pattern 4 — exploit behavior, not identity

The transferable idea is not “copy trader X.”

It is:

> If an observable order-flow archetype has persistent conditional predictive value, model the archetype.

Examples from IMC include recurring bots / traders whose behavior was informative. Jane Street teams also discuss adapting to opponent inclinations.

For SIG, public/anonymous behavior may still support features such as:

- repeated quote size;
- replenishment pattern;
- sweep timing;
- direction after external shocks;
- cancel/reprice cadence.

Do not assume identifiable counterparty labels.

### Pattern 5 — position capacity has option value

A subtle but repeated lesson:

- carrying inventory can block future high-EV trades;
- sometimes a zero-EV or small-cost inventory-clearing action raises future opportunity capacity;
- hard position limits change optimal trade timing.

Our binary-CARA / covariance risk stack captures the pricing side, but the **opportunity cost of consumed position capacity** deserves explicit measurement.

---

## 7. Repeating failure patterns

### 7.1 Overfit correlation without mechanism

Seen explicitly in Prosperity reports:

- correlations in provided environmental data looked interesting but failed to hold;
- cross-correlation / basket models could collapse when regime changed;
- small parameter changes could flip P&L dramatically.

**Cup response:** every statistical challenger needs:
1. chronological OOS,
2. parameter perturbation,
3. fill-model sensitivity,
4. a defensible mechanism,
5. a kill threshold.

### 7.2 Sophisticated models with insufficient test data

Public competitors repeatedly abandoned:

- complicated online regression;
- ML without stable validation;
- high-dimensional correlation ideas.

**Cup response:** no model promotion on in-sample fit.

### 7.3 Incorrect simulator assumptions

This is one of the most dangerous competition-specific failures.

Potential examples:

- assuming a fill that queue priority would not grant;
- using midpoint liquidation;
- mis-modeling P&L marking;
- not reproducing market-order semantics;
- ignoring partial fills;
- ignoring cancel acknowledgement delay.

**Cup response:** maintain optimistic / normal / conservative replay and reconcile all live discrepancies.

### 7.4 Multi-leg “arb” whose execution risk eats the edge

UChicago's 2026 participant evidence is a clean example.

**Cup response:** compute an **execution-complexity haircut** before admitting a strategy:
- number of legs;
- available executable depth;
- expected fill synchronization;
- residual exposure if only some legs fill;
- cancellation uncertainty;
- API round trips / batch semantics;
- opportunity cost of capital/position.

### 7.5 Slow or expensive quote maintenance

Optiver shows the danger when message/cancel constraints bind.

SIG's current changelog independently raises the same general concern: cancel/cancel-all may temporarily return `503` when the trading engine is overloaded, and the platform has added safe retry/idempotency behavior.

**Cup response:** “order state” must include:
- intended state;
- last sent action;
- acknowledgement state;
- retry token/idempotency key where supported;
- last confirmed exchange state;
- uncertainty flag;
- timeout/reconciliation action.

### 7.6 Crowded public alpha

Linear Utility's Prosperity account describes an arbitrage becoming widely known, reducing relative advantage and increasing luck/implementation variance.

**Cup response:** when an obvious Polymarket/Kalshi lead becomes common:
- estimate response-lag decay over time;
- do not assume day-1 edge survives week 2;
- search for second-order structural and execution advantages.

---

## 8. Transferability to prediction markets

| Lesson | Classification | Why |
|---|---|---|
| Deterministic replay / honest fill simulation | **DIRECTLY TRANSFERABLE** | Universal to electronic competition trading |
| Parameter sensitivity / OOS / kill rules | **DIRECTLY TRANSFERABLE** | Guards tiny-sample overfit |
| Inventory / position-capacity management | **DIRECTLY TRANSFERABLE** | Cup has bounded positions/capital |
| Structural RV / baskets / logical constraints | **DIRECTLY TRANSFERABLE** | Prediction markets have explicit semantic relationships |
| External lead–lag measurement with observable timestamps | **DIRECTLY TRANSFERABLE** | Directly matches Cup hypothesis |
| Idempotent retry / confirmed order state | **DIRECTLY TRANSFERABLE** | Current SIG changelog makes this explicit |
| Opponent/bot archetype inference | **LIKELY TRANSFERABLE** | Order-flow behavior can matter, identities may not be exposed |
| API/message-budget economics | **LIKELY TRANSFERABLE** | Rate limits and engine overload exist; exact binding constraints need measurement |
| Adaptive edge based on realized fill rate | **LIKELY TRANSFERABLE** | Natural MM calibration |
| Rank/horizon-aware risk threshold | **LIKELY TRANSFERABLE** | Finite tournament objective differs from infinite-horizon expected value |
| Queue priority modelling | **REQUIRES SIG-SPECIFIC VALIDATION** | Need live matching/fill observations |
| Millisecond external stale-price race | **REQUIRES SIG-SPECIFIC VALIDATION** | Stale window unknown |
| Copying identifiable “good traders” live | **REQUIRES SIG-SPECIFIC VALIDATION** | Public identity/tape fields may not exist; Super Signal is nightly |
| ETF creation/redemption mechanics | **NOT TRANSFERABLE** | No equivalent unless a Cup group has an explicit synthetic relationship |
| Equity/options-specific Greek hedging | **NOT TRANSFERABLE** except structurally analogous cases | Binary event contracts differ |
| C++ rewrite solely for speed | **NOT TRANSFERABLE YET** | No evidence local Python compute is bottleneck |
| Exchange colocation / bare metal | **NOT TRANSFERABLE YET** | No evidence Cup stale windows are microseconds |

### Prediction-market-specific advantage we already have

The Cup stack is already more mathematically tailored to binary event markets than almost every public competition write-up:

- `[0,1]` bounded probabilities;
- complements;
- implication / exclusivity;
- partitions;
- thresholds;
- Fréchet bounds;
- coherent-state prices;
- leave-one-out structural fair value;
- binary-CARA reservation pricing;
- multi-state inventory risk;
- dynamic external venue fusion.

The historical competition research therefore strengthens the case for **testing these existing objects rather than adding more objects**.

---

## 9. Comparison with our current models

The current `MATHS_LEDGER.md` already captures a large fraction of what competition winners publicly use.

| External lesson | Already captured? | Where in current stack | New information? | Action |
|---|---|---|---|---|
| Complement / basket / structural arb | Yes | M-001 onward; coherent polytope / executable arb | No | **KEEP** |
| Relative-value / LOO structural fair | Yes | M-027–M-030 | No | **KEEP / TEST** |
| Correlated inventory risk | Yes | M-031–M-052 | No | **KEEP** |
| MM reservation/inventory pricing | Yes | M-039–M-057 | No | **KEEP / TEST** |
| Fill markouts / adverse selection | Yes | M-058 onward | No | **KEEP / TEST** |
| OFI / microprice / micro-FV | Yes | M-064 onward | No new evidence that fancy toxicity measures are necessary | **TEST primitives first; DEFER fancy variants** |
| Queue / fill hazard | Yes | M-075–M-077 | Competition evidence raises priority | **TEST** |
| Depth-aware taker and passive value | Yes | M-078–M-081 | No | **KEEP** |
| Quote churn | Yes | M-082 | Optiver + SIG ops evidence raises priority | **TEST EARLY** |
| Event regime state | Yes | M-083–M-086 | No | **KEEP** |
| External lead–lag | Yes | M-087–M-089 | Competition evidence supports importance of measurement | **TEST FIRST 48H** |
| Multi-venue FV fusion | Yes | M-090 onward | No | **KEEP** |
| Simulator / matching-engine mechanics discovery | Partial | Architecture has adapters/state, but not a dedicated competition-mechanics research harness | **Yes — process priority** | **ADD** |
| Opponent / bot ecology as observable state | Partial | Some flow/toxicity objects; not clearly a first-class “market ecology” track | **Yes — modest** | **ADD / TEST** |
| API/message-budget economics | Partial | Churn/queue + architecture, but not explicit scarce API/cancel budget | **Yes — important** | **ADD** |
| Confirmed/unconfirmed order-state uncertainty | Partial | Execution architecture planned; no live path yet | **Yes — current SIG evidence** | **ADD before live trading** |
| Integration-complexity haircut | Implicit | Depth/risk constraints, but not explicit model-admission penalty | **Yes — useful framing** | **ADD** |
| Strategy-crowding / edge-decay monitor | Not explicit | Could live in attribution / regime layer | **Yes — useful** | **ADD after data begins** |
| C++ / low-level rewrite | No | Python modular monolith | No evidence yet | **IGNORE until latency data says otherwise** |
| Global fancy coherence solver before basics | Captured conceptually | M-016 onward | No evidence it should jump queue | **DEFER until simpler residuals prove value** |
| Generic high-dimensional ML | Captured only as potential challenger | Research state | Public failure evidence dominates | **DEFER / likely IGNORE** |

### Architecture implication

`ARCHITECTURE.md` and `CURRENT_STATE.md` show the project is still at the foundation/domain-model stage with no HTTP/WebSocket client, market-state engine, strategy, risk path or execution path yet.

That makes the priority unusually clear:

> The marginal value of another model is currently lower than the marginal value of a correct, timestamped, replayable, failure-aware execution/data path.

---

## 10. Genuine new ideas discovered

These are the items that should actually enter the project backlog as a result of the historical competition study.

### NEW-1 — Market ecology / opponent-archetype state

Create features for recurring **observable behavior patterns**, not identities.

Candidate archetypes:

- large stable quote replenisher;
- fast external-price follower;
- stale resting liquidity;
- periodic market-order sweeper;
- inventory-clearing bot;
- event-driven burst trader.

Required proof:

- behavior is detectable without privileged identity;
- conditional future SIG price / fill / markout changes OOS;
- effect survives basic spread/depth/external-FV controls.

### NEW-2 — API and cancellation budget as an economic resource

Track:

- requests/sec and burst usage;
- order amendments/cancels per decision;
- duplicate/retry rate;
- 429/5xx rate;
- cancel acknowledgement lag;
- time spent in uncertain-order state;
- stale exposure caused by failed/slow cancellation.

Optimize strategy with a cost for consuming scarce request/cancel capacity.

### NEW-3 — Mechanics discovery harness

During practice / first 48h, lawfully test:

- price-time priority;
- partial fills;
- self-cross handling;
- cancel-before-fill races;
- market order depth behavior;
- batch / multileg atomicity if documented;
- idempotency behavior;
- response/ack timestamps;
- REST vs realtime state reconciliation;
- whether quote/fill timestamps are server or client time;
- position/account update ordering.

The harness is not an exploit kit. It is a controlled conformance test against documented public behavior.

### NEW-4 — Integration-complexity haircut

For a candidate strategy define:

`Net admission edge = theoretical executable edge − slippage − residual-leg risk − latency risk − operational complexity cost`

The “operational complexity cost” can start heuristic. The key is that a 4-leg strategy with 1.5¢ theoretical edge should not automatically outrank a one-leg strategy with 1.0¢ robust edge.

### NEW-5 — Edge-decay / crowding monitor

For each strategy family measure by date:

- trigger count;
- median gross edge at trigger;
- median realized markout;
- fill probability;
- competitor response time / SIG response lag;
- post-trigger half-life.

Obvious external lead–lag could decay as competitors converge on the same reference source.

### NEW-6 — Position-capacity opportunity cost

Estimate whether inventory blocks better future opportunities.

A practical statistic:

`lost_edge_due_to_position_limit`

measured as the sum of otherwise-valid signals skipped because position/risk limits were consumed.

---

## 11. Things we already knew

Historical winners strongly reinforce — but do not newly discover — the following:

- simple market making;
- inventory skew;
- mean reversion;
- basket / synthetic spreads;
- lead–lag;
- price-time / queue importance;
- risk limits;
- event-driven regime changes;
- fair-value fusion;
- parameter tuning;
- robust backtesting;
- avoiding overfit;
- simple regression before ML;
- depth-aware execution.

This is important because it is **independent external validation of project priorities**, but it should not trigger new mathematical workstreams.

---

## 12. Latency / infrastructure research

### 12.1 What the current public SIG platform tells us

Current official Cup material supports the following:

- the Midterms competition runs 1 Oct 2026 12:00 ET to 4 Nov 2026 12:00 ET;
- participants can connect unlimited bots to one account, subject to API authentication, rate limits, position limits and technical controls;
- trades are peer-to-peer matched;
- market and limit orders are supported;
- self-crossing behavior is explicitly handled;
- automation through the API is allowed;
- the Super Signal refreshes **nightly**, so it is not a low-latency live signal;
- the 23 Sep changelog added/clarified idempotent retries for market-maker liquidity sweeps;
- cancel and cancel-all can return `503` when the **trading engine** is briefly overloaded, with retry semantics.

The existence of an explicitly named “trading engine” in the changelog is useful, but the docs do **not** state its physical region.

### 12.2 Fresh active-probe limitation

A fresh `curl`/DNS probe from the research sandbox could not resolve the target host, so I did **not** fabricate a new multi-region latency matrix.

This report therefore separates:

1. **current public documentation** verified on 25 Sep 2026;
2. **prior project observations** collected on 24 Sep 2026;
3. **tests still required** once we control external probe nodes.

### 12.3 Prior project network observations

Prior project work recorded:

- Vercel-style route/header patterns including `lhr1::sfo1`, `iad1::sfo1`, `fra1::sfo1` and `sfo1::sfo1`;
- globally fronted Supabase-health-style measurements with low tens-of-ms responses from several US/EU locations and much worse Singapore results;
- authenticated read probes that returned the same account state from multiple global locations.

Interpretation:

- Vercel documents `sfo1` as San Francisco / `us-west-1`, `iad1` as Washington D.C. / `us-east-1`, and `lhr1` as London / `eu-west-2`.
- Vercel functions can run in a compute region distinct from the ingress point.
- Therefore the repeated trailing `sfo1` observation is **consistent with** application/function execution in Northern California.
- It is **not proof** that the matching/trading engine or primary database is in `sfo1`.
- The fast globally distributed health responses are evidence of edge/fronting, **not** origin location.

### 12.4 Matching-engine location

**Current conclusion: UNKNOWN.**

Evidence level:

| Candidate fact | Confidence |
|---|---|
| Public web/API sits behind globally distributed infrastructure | High |
| Vercel is involved in the web/application path | Medium–high from prior headers / current behavior |
| Some application function execution has involved `sfo1` | Medium from prior observations |
| Primary DB is in Northern California | Low / unsupported |
| Trading engine is in Northern California | **Unknown / unsupported** |
| Matching engine equals Vercel function | **No evidence** |

Do not state “SIG matching engine is in San Francisco.”

### 12.5 What actually matters: end-to-end decision latency

The objective is not:

`ping(SIG)`

It is:

```text
external venue move becomes observable
→ our feed receives it
→ normaliser/book state updates
→ mapping/FV signal updates
→ risk approves
→ order serialises
→ request reaches SIG
→ SIG confirms / matches
```

A Northern California node that is 15 ms closer to SIG but 60 ms slower to the external signal can be worse.

### 12.6 Conceptual latency budget

| Component | Likely scale before measurement | Controllable? | Priority |
|---|---:|---|---|
| External venue event → publicly observable feed | Unknown; potentially dominant | Partly | **Measure** |
| Internet path external venue → our host | ~single-digit to 100+ ms depending route | Yes via region/provider | **Measure** |
| Receive + parse | sub-ms to few ms in Python if efficient | Yes | Low initially |
| Book/state update | sub-ms to few ms | Yes | Low–medium |
| FV / relationship calculation | sub-ms to several ms depending model | Yes | Medium |
| Risk check | usually sub-ms / low ms | Yes | Medium |
| Serialization | sub-ms | Yes | Low |
| Host → SIG network | likely tens of ms by geography | Yes | **Measure** |
| Vercel/app/API processing | Unknown | No | **Measure p50/p95** |
| Trading-engine queue / overload | Unknown; can spike | No | **Measure** |
| Matching / fill / acknowledgement | Unknown | No | **Measure** |
| Cancel retry / uncertain state | potentially very large tail | Partly | **Model explicitly** |

The key warning:

> Do not spend a day shaving 200 microseconds from Python while the p95 API/order path is 40–150 milliseconds or the exploitable external→SIG lag is seconds.

### 12.7 Does speed plausibly create edge?

**Yes, plausibly. Not yet proven to be decisive.**

Reasons it may matter:

- many serious competitors may observe the same Polymarket/Kalshi changes;
- external price shifts can make SIG resting prices stale;
- Optiver/Jane Street competition evidence shows speed can decide otherwise identical strategies;
- API users can automate response.

Reasons not to overreact:

- prediction-market information often diffuses more slowly than equity HFT;
- Cup markets may be thin, so fill availability rather than compute speed may dominate;
- SIG's platform/API/trading-engine processing can dwarf local compute;
- event-market relationships can remain mispriced for seconds/minutes rather than milliseconds;
- external mapping/semantic certainty can be more valuable than raw speed.

### 12.8 First-48-hours latency experiment

For every meaningful external move, persist monotonic-clock and UTC timestamps:

```text
t_external_exchange       # if source supplies it
t_external_observed       # first byte/event observable by us
t_external_book_ready     # local normalized state updated
t_signal_ready
t_risk_ready
t_order_sent
t_http_first_byte
t_ack
t_SIG_book_change_observed
t_fill_observed
```

Derive:

```text
external_observed → signal_ready
signal_ready → order_sent
order_sent → ack
external_observed → SIG_market_moves
external_observed → our_ack
our_ack − SIG_market_moves
```

For each region collect:

- n;
- p50;
- p90;
- p95;
- p99;
- variance / MAD;
- timeout rate;
- 429 rate;
- 5xx/503 rate;
- retry count;
- fill rate;
- realized markout after external trigger.

Test at least:

1. Northern California / `us-west-1`;
2. Northern Virginia / Ashburn / `us-east-1`;
3. London / `eu-west-2` as comparator.

Do not compare sequentially if the market is moving. Run them concurrently against the same **read-only** endpoints first. For order-path tests, use controlled tiny valid simulated orders and documented cancellation semantics.

### 12.9 Decision thresholds

If median exploitable external→SIG stale window is:

- **>2 seconds:** ordinary Python/asyncio is ample; optimize data quality and decision logic.
- **250 ms–2 s:** region, persistent connections, serialization and event loop discipline matter; Python still likely fine.
- **50–250 ms:** network region, realtime path, connection reuse and lean critical path become material.
- **<50 ms consistently:** consider compiled/isolated critical-path components only after proving local processing is material.
- **<10 ms:** a serious low-latency architecture discussion becomes justified; current evidence does not support assuming this.

These are engineering decision bands, not claims about the current Cup.

---

## 13. Hosting-region recommendation

### Recommendation now

**Primary provisional node: Northern California (`us-west-1` / San Francisco–San Jose vicinity).**  
**Comparator node: Northern Virginia / Ashburn (`us-east-1`).**  
**London: measurement node / fallback, not default primary.**

Confidence: **LOW–MEDIUM**.

Why Northern California gets the provisional nod:

- prior application-route observations repeatedly ended in `sfo1`;
- Vercel maps `sfo1` to San Francisco / `us-west-1`;
- if that reflects the API compute path, proximity may reduce the SIG leg.

Why this is *not* a final recommendation:

- matching engine remains unknown;
- external reference venues may route better from Ashburn;
- Vercel edge ingress can hide origin geography;
- REST order acknowledgement and realtime event paths may differ.

### Cheap server options

| Provider | Candidate region | Rough small-node cost | Fit | Caveat |
|---|---|---:|---|---|
| **AWS EC2** | `us-west-1` + `us-east-1`; London available | t3.micro base-class roughly single-digit USD/month before storage/IPv4; region pricing varies | **Best A/B test choice** because same provider across target regions | More setup than a simple VPS; watch public IPv4/storage charges |
| DigitalOcean | SFO / NYC / London where currently available | Basic droplets start around $4; 1 GB class around $6 | Simple deployment | Confirm exact current region/SKU before use |
| Vultr | US West / East / London variants | ~$5-class entry compute advertised | Good simple comparator | Confirm exact city and current availability |
| Oracle Cloud | region dependent | Always Free can cover small ARM/x86 resources where capacity exists | Cheapest if available | Free-tier capacity/reliability is not ideal as sole tournament infrastructure |
| Hetzner Cloud | Ashburn / Hillsboro and EU locations | Very low cost | Excellent value as comparator | No direct Northern California location in the reviewed location list |
| Fly.io | multi-region | small shared CPU roughly mid-single-digit USD/month depending region/RAM | Easy multi-region experiments | Additional platform/network abstraction is undesirable if latency measurement is the purpose |

### Practical deployment

For the first 48 hours, run:

```text
Node A: AWS us-west-1
Node B: AWS us-east-1
Node C: London only if cheap / useful as control
```

Feed the same read-only measurements into one log schema.

Then pick the region with the best:

`external-feed arrival + compute + SIG acknowledgement`

not the best ICMP ping.

After selection, one modest VM is enough for the bot plus one cheap standby/recorder if desired.

### What not to buy

Do **not** buy:

- dedicated bare metal;
- exchange colocation;
- FPGA hardware;
- premium low-latency networking products;

unless live measurements prove a sub-50-ms stale window and network/local processing is the binding constraint.

---

## 14. Experiments required once SIG opens

### EXP-1 — External→SIG response lag

For every ≥ configurable external logit move:

- timestamp observation;
- record corresponding SIG quote/book response;
- compute lag distribution by market and time-of-day.

Output:

- p50/p90/p95;
- probability stale window exceeds 50/100/250/500/1000 ms;
- executable edge after SIG depth.

### EXP-2 — Region A/B

Simultaneously probe from NCal and Ashburn:

- authenticated read RTT;
- order acknowledgement RTT on controlled orders;
- cancel RTT;
- 503/timeout rate;
- realtime receive timestamps.

Promote one region only after at least hundreds of samples across normal and busy periods.
### EXP-3 — Order lifecycle / uncertain state

Deliberately exercise valid documented cases:

- place;
- partial fill;
- cancel;
- cancel-all;
- retry same idempotency key where supported;
- retry after timeout;
- reconcile REST authoritative state.

Requirement:

> No strategy may assume cancellation succeeded until confirmed/reconciled.

### EXP-4 — Queue / fill calibration

For passive orders log:

- price level;
- visible size ahead;
- age;
- marketable flow;
- cancels;
- fill time;
- external FV movement.

Calibrate the simple queue-depletion model before any complex survival model.

### EXP-5 — Opponent-archetype test

Cluster observable order-flow behavior without identity assumptions.

Test whether clusters predict:

- short-horizon SIG price;
- fill probability;
- maker markout;
- external-response timing.

### EXP-6 — Strategy crowding / decay

Re-estimate external lead–lag weekly/daily.

If median stale edge falls quickly, assume competitors learned the same signal and lower allocated risk.

### EXP-7 — Integration-complexity haircut

For every multi-leg structural strategy compare:

- theoretical no-friction edge;
- executable all-leg edge;
- realized partial-leg cost;
- missed-fill cost;
- cancellation tail;
- implementation incidents.

Do not keep a structural strategy merely because the probability identity is correct.

### EXP-8 — Position-capacity value

Track signals rejected because of:

- market position limit;
- portfolio risk limit;
- available SUSQies;
- pending/unconfirmed orders.

Estimate the P&L lost to consumed capacity and whether low-cost inventory clearing improves future opportunity capture.

---

## 15. Final implications for the Cup

### Q1. Did historical trading competitions uncover any meaningful strategy/model idea that our existing research stack does not already contain?

**No major mathematical alpha family.**

The most common public winner/finalist strategies — market making, pair/basket RV, synthetic/ETF replication, mean reversion, simple regression, lead–lag, inventory/risk control, options-style parity where applicable — are already represented in the current research stack, often in more prediction-market-native form.

The genuinely new material is mostly **meta-model / execution process**:

- market ecology / opponent archetypes;
- API/message budget;
- mechanics conformance testing;
- integration-complexity haircut;
- edge-crowding/decay;
- explicit position-capacity opportunity cost.

### Q2. Did the research uncover implementation, testing, execution, latency or team-process lessons that should change our priorities?

**Yes. Materially.**

The build order should become:

```text
correct authenticated data/execution path
→ timestamp integrity
→ deterministic recorder/replay
→ reconciliation + idempotent retry
→ simple baseline strategies
→ live calibration of fills/markouts/lead-lag
→ only then richer challengers
```

This is stronger than the generic advice “do more backtesting.” It is directly consistent with repeated winning/finalist behavior across Optiver, IMC, Jane Street, RITC and UChicago.

### Q3. How important does latency currently appear, and what must we measure before spending heavily on it?

**Potentially important, currently unproven.**

Current confidence that *some* latency advantage exists: **medium**.  
Current confidence that the Cup requires HFT-style sub-10-ms engineering: **very low**.  
Confidence in the matching-engine location: **low / UNKNOWN**.

Before spending heavily measure:

1. external-observed → SIG-move lag distribution;
2. external-observed → our-ack by region;
3. order/cancel acknowledgement p50/p95/p99;
4. realtime event arrival by region;
5. 429/503/timeout tails;
6. fill probability conditional on response delay;
7. realized P&L advantage of being 25/50/100/250 ms faster.

---

# Action checklist

## DO BEFORE CUP

- Finish the authenticated REST path with authoritative state reconciliation.
- Add monotonic + UTC timestamps to every external event, internal decision, order send, acknowledgement, book update and fill.
- Build deterministic recorder/replay and conservative fill assumptions.
- Make order lifecycle explicit: pending / acked / partially filled / cancel-pending / cancelled / uncertain / reconciled.
- Implement retry/idempotency exactly as the API documents it; test 5xx/503 paths.
- Keep the first production strategy set small: direct external lead–lag, structural residual/RV, hard executable constraints, simple MM.
- Provision one `us-west-1` and one `us-east-1` measurement node.

## TEST DURING FIRST 48 HOURS

- External→SIG lag distribution.
- NCal vs Ashburn end-to-end latency.
- REST vs realtime timing.
- Queue/fill behavior.
- Cancel acknowledgement tails / overload behavior.
- Opponent/order-flow archetypes.
- Position-capacity opportunity cost.
- Strategy crowding / decay.

## DO ONLY IF DATA SUPPORTS IT

- More complex toxicity models.
- Global coherent-surface optimizer in the hot path.
- C++/Rust critical-path rewrite.
- More servers / provider diversification.
- Complex ML.
- Aggressive opponent-behavior models.
- Fine-grained queue optimization.

## IGNORE

- Bare-metal/HFT infrastructure before measurement.
- Fancy mathematical models added only because they sound sophisticated.
- Generic momentum without Cup-specific validation.
- Copying options/equity competition mechanics that have no binary-event analogue.
- Any attempt to bypass platform access controls or recover restricted database data.

---

# Five concrete actions for the Cup

1. **Add a latency/ack telemetry schema now** so BUILD-003 does not ship a client that cannot answer the most important first-48h questions.
2. **Make execution reconciliation and uncertain order state non-negotiable**, especially around cancel/cancel-all `503` behavior.
3. **Turn replay into the research operating system**: every live anomaly should be reproducible locally from captured data.
4. **Introduce a formal strategy-admission gate** that subtracts execution/integration risk from theoretical edge.
5. **Run NCal vs Ashburn concurrently from day 1**, then collapse to the measured winner rather than arguing geography from headers.

# Five tempting ideas we should NOT chase

1. A C++ rewrite before a measured latency bottleneck exists.
2. Exotic ML from tiny competition history.
3. Global high-dimensional probability optimization before simple LOO/residual signals prove incremental value.
4. Expensive bare metal/colocation.
5. “Smart trader copying” based on assumed identities rather than verified public observable behavior.

---

# Output to orchestrator

```text
document path:
docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md

primary competition/event records catalogued:
32 (plus older context excluded from primary count)

records with useful public mechanics/rules:
~20; richest: Optiver RTG, IMC Prosperity, RITC, UChicago, current SIG Cup

winner/finalist/podium records:
at least one named placement recovered for 19 competition-year/regional records;
full top-three was not public for many firm-run events

LinkedIn/public participant posts found:
14+ with some technical/process content; smaller high-value subset used for synthesis

public strategy writeups found:
12 high-value technical sources used as the qualitative core sample

GitHub repos found:
6+ relevant public strategy repositories surfaced;
3 inspected in depth through GitHub

genuinely new ideas:
- market ecology / opponent archetype state
- API/message-budget economics
- mechanics discovery harness
- integration-complexity haircut
- edge-crowding / decay monitor
- position-capacity opportunity cost

existing ideas reinforced:
- simple robust MM
- structural relative value / synthetic baskets
- lead-lag
- inventory/risk
- queue/fill realism
- markouts/adverse selection
- replay/backtesting
- parameter robustness
- event regime controls

ideas contradicted / weakened:
- sophistication for its own sake
- high-dimensional correlation without mechanism
- generic momentum as default
- trusting optimistic simulator fills
- deploying every mathematically valid multi-leg arb
- optimizing local CPU before measuring network/API path

latency findings:
- speed can materially matter in competitions
- SIG-specific exploitable stale window remains unmeasured
- current SIG docs show trading-engine overload can affect cancellation and require retry
- prior Vercel route evidence is consistent with sfo1 application execution but does not locate the matching engine

likely infrastructure region:
Northern California is the provisional first candidate only

confidence level:
LOW–MEDIUM for us-west-1 as best region
LOW for any matching-engine geolocation claim

recommended deployment region:
run us-west-1 + us-east-1 A/B for first 48h;
select on end-to-end external-feed → SIG-ack results

what remains unknown:
- matching engine location
- database origin
- real order-path p50/p95/p99
- realtime batching/transport behavior under Cup load
- true external→SIG stale-price window
- queue priority details beyond documented behavior
- rate-limit binding points under active bot trading

overall conclusion:
No hidden mathematical competition edge was found that justifies expanding the maths stack.
The research materially increases the priority of replay fidelity, venue-mechanics discovery,
order-state correctness, cancellation/retry robustness, execution-complexity gating,
opponent-behavior measurement and first-48h latency instrumentation.
```

---

# Source catalogue

## SIG / TheSuper.Market

- **[S1]** Susquehanna Predictions Cup official rules — https://predictionscup.com/rules/
- **[S2]** SIG Platform Guide: Markets & Trading — https://sig.thesuper.market/docs/markets-and-trading
- **[S3]** SIG changelog — https://sig.thesuper.market/docs/changelog
- **[S4]** SIG Super Signal guide — https://sig.thesuper.market/docs/super-signal
- API reference — https://sig.thesuper.market/api/v1/docs

## Optiver

- **[O1]** Ready Trader Go — How to Play — https://readytradergo.optiver.com/how-to-play/
- **[O2]** Ready Trader Go 2023 Terms — https://readytradergo.optiver.com/wp-content/uploads/Ready-Trader-Go-2023-Terms-and-Conditions_version_March_2023.pdf
- **[O3]** Optiver winner interview / announcement — https://optiver.com/working-at-optiver/career-hub/ready-trader-go-2023-winner-announced/
- **[O4]** Ready Trader Go leaderboard — https://readytradergo.optiver.com/leaderboard/

## IMC Prosperity

- **[I1]** IMC Prosperity — https://prosperity.imc.com/
- **[I2]** 2023 winner announcement — https://www.prnewswire.com/news-releases/imc-announces-team-of-australian-students-as-winner-of-prosperity-global-trading-competition-301796807.html
- **[I3]** Stanford Cardinal 2023 repository — https://github.com/ShubhamAnandJain/IMC-Prosperity-2023-Stanford-Cardinal
- **[I4]** Public Prosperity 2023 leaderboard archive — https://jmerle.github.io/imc-prosperity-leaderboard/
- **[I5]** Linear Utility / Prosperity 2 repository — https://github.com/ericcccsliu/imc-prosperity-2
- **[I6]** IMC Prosperity public corporate material / winner posts, 2024
- **[I7]** Prosperity 3 public repositories and finalist write-ups, including Carter Tran
- **[I8]** Carter Tran Prosperity 3 repository — https://github.com/CarterT27/imc-prosperity-3
- **[I9]** Public 2025 winner announcement for Team Heisenberg
- **[I10]** High-ranked 2025 participant repositories/write-ups surfaced in search
- **[I11]** IMC Prosperity 4 / corporate announcement — https://www.imc.com/us/corporate-news/prosperity-4
- **[I12–I14]** Public 2026 Seven Deuce Capital / finalist participant LinkedIn write-ups

## Jane Street ETC

- **[J1–J4]** Public participant LinkedIn posts for 2022–2024 regional ETC events
- **[J5]** Poliwag 2024 ETC repo — https://github.com/denfujita/Jane-Street-ETC
- **[J6]** Public 2024 Tentacool participant post
- **[J7]** 2025 Adelaide ETC winning participant write-up

## RITC

- **[R1]** Baruch 2021 winner coverage — https://newscenter.baruch.cuny.edu/news/baruch-college-victorious-again-at-the-2021-rotman-international-trading-competition/
- **[R2–R3]** Rotman / university historical results
- **[R4]** RITC 2023 results
- **[R5]** Baruch 2024 coverage — https://newscenter.baruch.cuny.edu/news/baruch-college-dominates-the-2024-rotman-international-trading-competition/
- **[R6]** 2025 results / Baruch coverage — https://newscenter.baruch.cuny.edu/news/baruch-college-clinches-second-place-at-global-trading-competition/
- **[R7]** 2026 Rotman results — https://www.rotman.utoronto.ca/faculty-and-research/education-labs/bmo-financial-group-finance-research-and-trading-lab/rotman-international-trading-competition/results/
- **[R8]** Baruch 2026 coverage — https://newscenter.baruch.cuny.edu/news/baruch-college-wins-first-place-at-2026-rotman-international-trading-competition/

## UChicago / university-industry competitions

- **[U1]** UChicago Trading Competition past winners — https://tradingcompetition.uchicago.edu/competition.html
- **[U2]** UChicago Career Advancement / sponsor competition posts
- **[U3]** 2026 technical participant write-up — https://www.cs.utexas.edu/~kavish/blog/uchicago-trading-competition-2026.html
- **[B1]** Berkeley Trading Competition — https://traders.studentorg.berkeley.edu/competition/
- **[Y1]** Yale Undergraduate Trading Competition — https://yutc.org/

## CME / other firm competitions

- **[C1]** CME University Trading Challenge — https://www.cmegroup.com/events/university-trading-challenge.html
- **[C2–C3]** CME 2024/2025 results pages
- **[A1]** Akuna 2024 Virtual Quant Trading Challenge, MIT career posting
- **[A2]** Akuna 2026 Virtual Quant Trading Challenge public posting
- **[D1]** DRW Market Madness — https://www.drw.com/market-madness
- **[D2]** DRW public engineering/training-platform material
- **[CT1]** Citadel Trading Invitational — https://www.citadel.com/careers/programs-and-events/the-trading-invitational/
- **[CT2]** Public participant account of 2024 Trading Invitational
- **[JP1]** Jump Trading Probability Cup — https://www.jumptrading.com/signals/introducing-the-jump-trading-probability-cup
- **[DV1]** Da Vinci Trading League x IIT Bombay — https://davincitrading.com/da-vinci-trading-league-x-iit-bombay/
- **[DV2]** Da Vinci Cricket League — https://davincitrading.com/da-vinci-cricket-league/
- **[F1]** Flow Traders Quant Trading Days — public careers event page
- **[T1]** Tower Limestone Trading Team Data Challenge — https://tower-research.com/towers-limestone-trading-team-second-annual-data-challenge/

## Infrastructure

- Vercel request/system headers — https://vercel.com/docs/headers/request-headers
- Vercel global regions — https://vercel.com/docs/regions
- AWS regions — https://aws.amazon.com/about-aws/global-infrastructure/regions_az/
- AWS T3 — https://aws.amazon.com/ec2/instance-types/t3/
- DigitalOcean Droplet pricing — https://www.digitalocean.com/pricing/droplets
- Oracle Always Free resources — https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Hetzner Cloud locations — https://docs.hetzner.com/cloud/general/locations/
- Fly.io pricing — https://fly.io/docs/about/pricing/

---

## Research limitations

1. LinkedIn pages are inconsistently indexed and some posts require login, so absence of a post is not proof none exists.
2. Several firm-run competitions intentionally publish little about winners/strategies.
3. Jane Street ETC is regional, so same-year placements cannot be merged into a single global leaderboard.
4. Active multi-region network probing could not be executed fresh from this research sandbox. Prior project probe data is therefore labelled separately and is not treated as a current matching-engine locator.
5. The Cup platform is changing rapidly immediately before launch; API/changelog behavior should be re-read before production deployment.
6. No causal claim is made merely because a winner used a strategy. The report treats winner write-ups as evidence about workflows and candidate techniques, not proof that any one technique caused the result.