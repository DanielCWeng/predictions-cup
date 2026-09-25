# RESEARCH-ECOLOGY-001 — Opponent Ecology & Bot Archetypes

**Project:** 2026 Susquehanna Predictions Trading Cup  
**Research cutoff:** 25 September 2026  
**Status:** Canonical research handoff — documentation only; no strategy, simulator, execution or trading capability is added.

---

## Executive conclusion

Opponent ecology is important enough to **measure from day one**, but not important enough to justify a large pre-launch opponent simulator.

The historical evidence is stronger than a purely theoretical argument. Public IMC Prosperity write-ups repeatedly show strong teams identifying stable bot or simulator behaviours and adapting around them:

- Linear Utility, 2nd globally in Prosperity 2024, identified a large stable market-making bot, used that bot's midpoint as a cleaner fair-price reference, and separately discovered a recurring taker whose predictable behaviour created a large arbitrage.
- A top Prosperity 2025 team identified the recurring trader `Olivia` as an informative signal and conditionally followed her trades.
- Prosperity 2026 write-ups describe multi-bot fingerprinting, hidden-flow discovery, a bot consistently quoting at a fixed distance from fair, and teams stepping one tick inside predictable bots.
- Other 2026 teams used disclosed counterparty IDs to distinguish persistent market makers, one-sided informed traders, persistent buyers/sellers and active takers.

That is direct evidence that **recurring participant behaviour can become part of the tradable state** in competition markets.

The transfer to SIG is narrower than those competitions. We should not assume counterparty identity is exposed, nor that the same simulator-style bots exist. The relevant SIG hypothesis is therefore:

> Observable market behaviour — quote persistence, replenishment, response lag, one-tick improvement, sweep cadence, external-following and inventory-clearing patterns — may predict future price, fill or markout behaviour even when participant identity is unavailable.

This remains an **empirical hypothesis**, not an assumed source of alpha.

The current recommendation is:

```text
PUSH THE RESEARCH NOW
CAPTURE ECOLOGY FEATURES FROM DAY ONE
TEST WHEN LIVE SIG DATA EXISTS
DO NOT BUILD A LARGE "SHADOW FIELD" SIMULATOR PRE-LAUNCH
```

---

## 1. Evidence taxonomy

This report deliberately separates four evidence classes.

### 1. Historical competition evidence

Public participant or organizer material describing behaviour that actually occurred in a competition.

This is the strongest evidence in this report.

### 2. Observable-market inference

Behaviour we could infer from SIG books, trades and timing without identifying a named participant.

This is a testable transfer hypothesis.

### 3. LLM-corpus convergence

Recurring bot designs in the raw repository file:

`docs/research/AI Ideas`

This file contains four independent/general-purpose model outputs:

- Claude Free Sonnet;
- ChatGPT online / no account;
- ChatGPT with account;
- ChatGPT Pro with account.

These outputs are **not evidence about actual SIG competitors**. They are useful only as a sample of strategies that are obvious enough for generic LLM-assisted entrants to converge on.

### 4. Speculation

Plausible archetypes for which we currently have neither SIG evidence nor strong transferable historical evidence.

These remain low confidence until live data supports them.

---

## 2. Strong historical evidence

### 2.1 IMC Prosperity 2024 — stable market maker as a better fair-value anchor

Linear Utility's public Prosperity 2024 repository describes a Starfruit market containing two recurring participant types:

- a large, relatively stable market maker;
- a smaller participant that occasionally crossed fair.

The team found that the large market maker's midpoint was materially less noisy than the ordinary inside-market midpoint and generated more P&L in backtests.

They also inferred that the competition website itself appeared to mark P&L against that market-maker midpoint, strengthening their belief that they had found an important simulator-state anchor.

**Transferable lesson:** a recurring liquidity provider can be more informative than the raw midpoint. The behaviour itself can become a fair-value feature.

**Evidence strength:** HIGH for the IMC environment; UNKNOWN transferability to SIG.

Source: https://github.com/ericcccsliu/imc-prosperity-2

---

### 2.2 IMC Prosperity 2024 — recurring taker / mechanics exploitation

The same team discovered a large recurring taker in the local Orchids market. Sell orders placed slightly above the best bid were repeatedly taken for full size. Combined with the external market, this created a large arbitrage.

After the idea leaked and became crowded, the team adapted its quoting edge based on observed fill volume rather than keeping a fixed price offset.

**Transferable lessons:**

1. repeated taker behaviour can be directly monetisable;
2. the optimal response may be **provide liquidity to the predictable taker**, not race it;
3. strategy value can decay rapidly once many competitors converge on the same mechanic;
4. fill-rate adaptation can matter more than a theoretically elegant pricing model.

**Evidence strength:** HIGH for IMC; MODERATE as a general competition-mechanics lesson.

Source: https://github.com/ericcccsliu/imc-prosperity-2

---

### 2.3 IMC Prosperity 2025 — informed-trader following

A high-ranked Prosperity 2025 write-up reports that, once trader identities were available, the team visualised all trading activity and found one bot, `Olivia`, repeatedly trading around meaningful daily extremes in several products.

They tested following the signal. The signal was not automatically best everywhere: in one product their ordinary market-making/taking strategy remained better, while in another they changed behaviour after Olivia appeared.

**Transferable lessons:**

- a recurring participant can be informative;
- the correct response may differ by market;
- even a genuine participant signal must beat the existing baseline;
- identification should be treated as a challenger signal, not an automatic override.

**Evidence strength:** HIGH for the competition data; LOW/MODERATE direct transferability to SIG if identities are absent.

Source: https://github.com/chrispyroberts/imc-prosperity-3

A separate public Prosperity 2025 retrospective reports that identifying Olivia directly reduced false positives compared with inferring her activity indirectly.

Source: https://github.com/TimoDiehm/imc-prosperity-3

---

### 2.4 IMC Prosperity 2026 — six-bot fingerprints and hidden flow

A 2026 Prosperity research repository describes:

- decomposing one product into six recurring bots;
- discovering a hidden bot through live probing;
- finding that a large share of fills came from flow invisible in the visible book snapshot;
- identifying another bot quoting at a fixed distance from fair and stepping one tick inside it;
- later using disclosed counterparty identities to map recurring behaviours.

The reported workflow is especially relevant: visualise, fingerprint, test, then exploit only the behaviour that survives live validation.

**Transferable lesson:** apparent order-book state may be generated by a small number of repeatable reaction functions. Behavioural decomposition can improve both fair-value estimation and execution.

**Evidence strength:** HIGH for Prosperity; transfer to SIG must be verified.

Source: https://github.com/saksham10arora-dotcom/imc-prosperity-4

---

### 2.5 IMC Prosperity 2026 — counterparty-role classification

Another public 2026 write-up reports profiling named bots into roles such as:

- dedicated two-sided market maker;
- informed buy-only participant;
- passive bidder;
- active two-way taker;
- uninformed sell-only participant.

The stable role fingerprint mattered more than generic labels like "smart bot".

**Transferable lesson:** the useful state variable is often **behavioural role**, not identity.

**Evidence strength:** MODERATE/HIGH for Prosperity.

Source: https://github.com/WindelsArthur/imc-prosperity4

---

### 2.6 Prosperity 2026 — informed-bot filtering

A separate 2026 retrospective explicitly warns against assuming that a bot that merely looks profitable is informed. The team bucketed trades by context and evaluated which bots were actually directionally useful in specific regions before following them.

**Transferable lesson:** opponent classification needs a falsifiable predictive target. "This bot makes money" is not enough; we need incremental future markout or execution value.

**Evidence strength:** MODERATE.

Source: https://github.com/Leo-Hawking/IMC-Prosperity-4-Review

---

### 2.7 Jane Street ETC

Jane Street's Electronic Trading Challenge explicitly places participant programs and organizer-provided marketplace bots in the same simulated marketplace. Public winning and participant repositories show simple market-making and cross-instrument strategies competing directly against this ecology.

The public evidence for **specific opponent fingerprint exploitation** is weaker than in Prosperity, so this report does not claim the same strength of evidence.

**Transferable lesson:** competition markets can be strategic ecosystems where other programs are part of the state, but the best direct behavioural evidence currently comes from IMC.

Sources:

- https://github.com/EnriqueKhai/JaneStreetETC22
- https://github.com/P254/jane-street-trading-bot

---

### 2.8 Optiver Ready Trader Go

Ready Trader Go runs several participant autotraders against one another on the same simulated exchange and makes speed, pricing and market-making quality explicit parts of competition performance.

Again, the direct public evidence for stable opponent fingerprint exploitation is weaker than in Prosperity.

**Transferable lesson:** latency, queue competition and other bots can materially affect strategy economics even when there is no named opponent signal.

Official source: https://readytradergo.optiver.com/how-to-play/

---

### 2.9 UChicago Trading Competition 2026 — crowding and response-window compression

A detailed 2026 UChicago participant write-up provides unusually relevant evidence about ecology changing during the competition rather than remaining fixed.

Through the first six live rounds, the team's event-driven Stock C strategy performed strongly. Later, the participant reports that:

- opposing bots reacted to news faster;
- the top of book became thinner;
- spreads widened;
- the window between a CPI print and prediction-market repricing compressed;
- fixed trade sizes and fixed edge thresholds that had worked earlier became materially worse execution choices.

The participant's diagnosis was not that the fair-value model stopped working. The underlying model continued to track price discovery; the executable opportunity degraded because competing reactions became faster and liquidity conditions changed.

**Transferable lessons:**

1. an information edge can decay because the market ecology adapts even when the underlying signal remains valid;
2. external-response latency, depth and executable edge should be re-estimated through the Cup rather than calibrated once;
3. crowding can convert an alpha problem into an execution problem;
4. opponent ecology can be economically important without identifying any participant.

This is particularly relevant to a possible Polymarket/Kalshi → SIG signal: the external information may remain useful while the stale-price window shrinks as competitors converge on the same reaction.

**Evidence strength:** HIGH for the observed competition dynamics; MODERATE transferability to SIG.

Source: https://www.cs.utexas.edu/~kavish/blog/uchicago-trading-competition-2026.html

---

## 3. What the raw AI corpus actually tells us

The raw file `docs/research/AI Ideas` should remain unchanged.

It is useful precisely because the models were asked sincerely for a competitive bot rather than to generate intentionally weak bots.

Across all four responses, the following motifs recur:

| Design motif | Corpus presence | Interpretation |
|---|---:|---|
| External prediction-market anchor | 4 / 4 | Very obvious entrant design |
| Local book / midpoint or microprice input | 4 / 4 | Very obvious |
| Related / structural market information | 4 / 4 | Very obvious |
| Passive market making | 4 / 4 | Very obvious |
| Inventory skew / reservation-price adjustment | 4 / 4 | Very obvious |
| Explicit minimum edge / threshold gate | 4 / 4 | Very obvious |
| Cancel / widen after external shock or stale quote | 4 / 4 | Very obvious |
| Fixed or seeded source weights / parameters | 4 / 4 | Common implementation shortcut |
| Toxic-fill / post-fill adaptation | 3 / 4 | Common but not universal |
| Queue-priority / one-tick improvement logic | 2 / 4 | More sophisticated but still common |

This does **not** imply that SIG competitors will use these designs.

It does imply that the following reaction functions are plausible enough to include in our mental model of the field:

```text
external move
→ recompute FV
→ cancel stale quote
→ trade if |edge| > fixed threshold
→ otherwise re-quote around inventory-adjusted FV
```

and:

```text
fill
→ update inventory
→ widen / skew / cooldown if recent fills look toxic
```

### Important corpus errors

The raw outputs also contain examples of confident but faulty reasoning.

One answer labels the mutually-exclusive set:

```text
45% + 40% + 25%
```

as internally coherent even though it sums to 110%.

That is useful evidence about **implementation failure modes** of generic AI-assisted entrants:

- arithmetic slips;
- title-level semantic assumptions;
- unjustified fixed weights;
- arbitrary thresholds that sound reasonable;
- copying conventional market-making formulas into binary markets without validating the model.

Do not "correct" these errors in the raw source file.

---

## 4. Canonical bot-archetype catalogue

The archetypes below describe **observable behaviour**, not identities.

### A1 — Stable anchor / replenishing market maker

**Evidence:** strong historical evidence from Prosperity.

**Generating rule:** quote persistent two-sided liquidity around a stable or slowly moving internal fair, often with recurring size modes.

**Observable signature:**

- persistent bid/ask distances;
- recurring size bands;
- rapid replenishment after trades;
- low short-horizon quote noise relative to the rest of the book.

**Likely weakness / opportunity:**

- its midpoint may reveal a cleaner fair than the raw inside market;
- predictable fixed offsets may be undercut when economics justify it.

**False-positive risk:** ordinary deep liquidity from multiple agents can look similar.

**Data required:** depth snapshots, trades, quote persistence/replenishment timing.

**Falsifier:** the apparent anchor has no incremental predictive value for future executable prices after controlling for current midpoint/depth.

---

### A2 — Recurring taker / sweep process

**Evidence:** strong historical evidence.

**Generating rule:** repeatedly cross available liquidity under a stable or state-dependent trigger.

**Observable signature:**

- repeated one-sided aggressive trades;
- similar sweep sizes;
- repeated consumption at characteristic distances;
- short refill → sweep cycles.

**Possible counter:** provide liquidity when expected post-fill markout remains positive.

**False-positive risk:** genuine information flow can also produce one-sided sweeps.

**Falsifier:** fills against the pattern are adversely selected after spread and reference-FV controls.

---

### A3 — Informative leader / follow target

**Evidence:** strong only in environments where trader identity was disclosed; transfer requires identity-free proxies.

**Generating rule:** trade from information or a stronger valuation process before the broader market adjusts.

**Observable signature without identity:**

- recurring directional trade bursts shortly before wider repricing;
- characteristic size/timing clusters;
- consistent positive future markouts conditional on the pattern.

**Possible counter:** follow only when the observable pattern adds predictive value beyond external/reference moves.

**False-positive risk:** market-wide information shock.

**Falsifier:** no OOS incremental markout after controlling for Polymarket/Kalshi/SIG state.

---

### A4 — One-tick priority improver / pennying bot

**Evidence:** historically plausible and directly observed in competition settings.

**Generating rule:** improve the current best quote by exactly one tick whenever remaining theoretical edge stays positive.

**Observable signature:**

- repeated one-tick improvements;
- immediate response to best-price changes;
- stable size;
- high quote churn.

**Possible counter:** do not enter a blind priority race. Test whether waiting, joining or selectively stepping inside has higher fill-conditioned value.

**False-positive risk:** several unrelated agents following the same basic execution rule.

**Falsifier:** one-tick patterns have no stable effect on fill probability or future markout.

---

### A5 — External-reference threshold taker

**Evidence:** LLM-corpus convergence; project hypothesis; not historical SIG evidence.

**Generating rule:**

```text
if external_FV - local_ask > threshold:
    buy
if local_bid - external_FV > threshold:
    sell
```

**Observable signature:**

- clustered local aggressive trades shortly after external moves;
- response begins only above a fixed-size divergence;
- response latency approximately stable;
- stronger bursts around 2–5 tick discrepancies.

**Possible counter:** trade earlier if our observable path is faster, or use the response as confirmation if it is not.

**False-positive risk:** the underlying public information causes both venues to move independently.

**Falsifier:** opponent-response features add no predictive value beyond the external impulse itself.

---

### A6 — External-anchored inventory market maker

**Evidence:** LLM-corpus convergence.

**Generating rule:** external FV + local book + inventory skew, with passive quoting outside an edge buffer.

**Observable signature:**

- two-sided quotes migrate after external moves;
- quote center shifts asymmetrically after fills;
- size tapers as one-sided inventory accumulates;
- stale quotes disappear rapidly after external shocks.

**Possible counter:** detect deterministic reaction latency or inventory stress, but only trade if observable economics are positive.

**False-positive risk:** competent generic market makers will look similar.

**Falsifier:** inferred response pattern is unstable across days/markets.

---

### A7 — Fixed-spread / fixed-parameter maker

**Evidence:** plausible novice/default strategy; conventional competition strategy.

**Generating rule:** quote fixed offsets around midpoint or a simple fair with fixed size.

**Observable signature:**

- discrete repeated spread values;
- weak adaptation to probability level, volatility or information state;
- recurring equal sizes.

**Possible counter:** select against stale or mis-scaled quotes during regime changes.

**False-positive risk:** stable conditions can make an adaptive bot look fixed.

**Falsifier:** spread/size values move materially with hidden state or external information.

---

### A8 — Shock / cooldown bot

**Evidence:** LLM-corpus convergence.

**Generating rule:** cancel/widen when external market moves more than a preset threshold, then remain inactive for a fixed cooldown.

**Observable signature:**

- liquidity disappears after threshold-sized reference moves;
- reappears at recurring time intervals;
- abrupt state changes rather than continuous adaptation.

**Possible counter:** exploit temporary liquidity vacuums only if executable price economics justify it; do not trade merely because the bot is absent.

**False-positive risk:** exchange-wide event response.

**Falsifier:** no repeatable threshold or cooldown duration.

---

### A9 — Stale / under-reactive liquidity

**Evidence:** generic microstructure hypothesis; needs SIG validation.

**Generating rule:** slow polling, slow external refresh or infrequent requoting.

**Observable signature:**

- quotes survive meaningful external moves;
- fills consistently have negative future markouts for the stale maker;
- reaction-lag distribution is stable.

**Possible counter:** taker lead/lag strategy.

**False-positive risk:** the external reference may itself be wrong or non-equivalent.

**Falsifier:** apparent stale quotes do not generate positive executable future markouts.

---

### A10 — Inventory-clearing / capacity-stressed participant

**Evidence:** competition mechanics make this plausible; direct SIG evidence absent.

**Generating rule:** once position/capital limits bind, aggressively reduce inventory or stop quoting one side.

**Observable signature:**

- persistent one-sided crosses after prior accumulation;
- abrupt disappearance of one quote side;
- escalating willingness to cross;
- response clustered near repeated exposure episodes.

**Possible counter:** provide liquidity only if the flow is genuinely capacity-driven rather than informed.

**False-positive risk:** new directional information.

**Falsifier:** post-trade markouts remain strongly adverse after controls.

---

### A11 — Structurally naive independent-market bot

**Evidence:** plausible because simple entrants may trade each market independently; no direct SIG evidence yet.

**Generating rule:** independent single-contract FV with no hard relationship constraints.

**Observable signature:**

- slow correction of exact complement/partition/threshold relationships;
- repeated structural inconsistencies after information shocks.

**Possible counter:** only via audited executable structural relationships.

**False-positive risk:** semantic mismatch, different resolution rules, thin books.

**Falsifier:** violations disappear after correct bid/ask/depth and semantic controls.

---

### A12 — Last-trade / momentum follower

**Evidence:** conventional/simple strategy family; weak evidence in the current AI corpus.

**Generating rule:** recent prints or short-window price direction drive FV.

**Observable signature:**

- local trades trigger further same-direction quote changes even without external movement;
- potential overshoot/reversion.

**Possible counter:** only after proving predictable overreaction.

**False-positive risk:** true informed continuation.

**Falsifier:** conditional continuation/reversion is not stable OOS.

---

## 5. Detection features worth recording from day one

Do not create an opponent-specific data system.

The normal SIG + external recorder should preserve enough information for these features:

### Timing

- external source event timestamp;
- external observed timestamp;
- SIG realtime observed timestamp;
- SIG REST book observation timestamp;
- trade timestamp where supplied;
- time between external impulse and first local response;
- time between local response and subsequent repricing.

### Quote / depth behaviour

- best bid/ask;
- bounded depth;
- quote age where reconstructable;
- price-level persistence;
- replenishment interval;
- recurring size modes;
- one-tick improvement frequency;
- spread regime;
- disappearance/reappearance after shocks.

### Trade behaviour

- trade side/outcome where documented;
- size;
- burst/sweep cadence;
- repeated one-sided flow;
- forward executable markouts.

### State / health controls

- data freshness;
- revision gaps;
- reconnects;
- reconciliation periods;
- book trust state.

A behavioural signal is invalid if it is really a feed gap or stale local state.

---

## 6. Five experiments only

### ECO-001 — External impulse → local response function

**Priority:** HIGHEST PRIORITY ECOLOGY TEST.

**Hypothesis:** some local SIG activity is a predictable reaction to external-market changes, and the observed local reaction may add incremental predictive/economic value beyond the external impulse itself.

**Inputs:** synchronized Polymarket/Kalshi where available, SIG books/trades, observable timestamps.

**Features:**

- external logit impulse;
- time to first SIG trade/book response;
- response magnitude;
- threshold/nonlinearity;
- market/regime.

**Baseline:** direct external lead/lag using external impulse plus ordinary contemporaneous SIG book state.

**Test:** compare chronologically out of sample:

```text
Model A:
external impulse + ordinary SIG book state
→ subsequent executable SIG move

Model B:
external impulse + ordinary SIG book state + observed local reaction
→ subsequent executable SIG move
```

The reaction window must be timestamp-safe: if a reaction is observed after the external impulse, any hypothetical trade must use the executable SIG state available **after** that reaction is observable to us, not the pre-reaction book.

**Falsifier:** Model B produces no stable incremental OOS executable markout, trade-selection or P&L improvement over Model A.

**Dependency:** synchronized SIG recorder + mapping.

---

### ECO-002 — Stable replenisher / quote-anchor test

**Hypothesis:** recurring stable liquidity can provide a cleaner short-horizon fair or execution anchor than raw midpoint.

**Inputs:** SIG depth observations and trades.

**Features:**

- recurring size bands;
- quote persistence;
- replenishment speed;
- stable bid/ask offsets.

**Baseline:** ordinary midpoint / microprice.

**Test:** compare future executable-price error and maker economics.

**Falsifier:** no stable incremental value OOS.

**Dependency:** trustworthy depth capture.

---

### ECO-003 — Predictable aggressive-flow test

**Hypothesis:** recurring sweeps or one-sided bursts sometimes reflect mechanical/capacity flow rather than new information, creating positive liquidity-provision economics.

**Inputs:** trades, depth, external FV, future markouts.

**Baseline:** generic one-sided flow indicator.

**Test:** classify repeated sweep patterns, then measure fill-conditioned future markouts after external-FV and volatility controls.

**Falsifier:** flow remains informed/toxic or pattern is unstable.

**Dependency:** replay + conservative maker evaluation.

---

### ECO-004 — Crowding / edge-decay monitor

**Hypothesis:** obvious public signals become faster and less profitable as the Cup progresses.

**Inputs:** strategy triggers, gross executable edge, fill probability, response lag, future markout.

**Metrics by day:**

- median trigger edge;
- signal half-life;
- SIG response latency;
- fill rate;
- realised/shadow markout.

**Falsifier:** no material time trend.

**Dependency:** normal strategy attribution.

This should be implemented as measurement, not as a separate strategy.

---

### ECO-005 — Identity-free behavioural clusters

**Hypothesis:** a small number of recurring aggregate behaviour patterns exist even without participant IDs.

**Inputs:** quote timing, size modes, trade bursts, external-response lag, spread/depth state.

**Baseline:** simple regime features only.

**Test:** fit simple rule-based or low-dimensional clusters and test whether cluster state predicts future price/fill/markout OOS.

**Falsifier:** unstable cluster assignments or no incremental economic prediction.

**Dependency:** enough live data.

Do not begin with complex ML or hidden-state models.

---

## 7. Future bot-farm specification — deferred

A simulator may later instantiate a small set of opponent archetypes, but only after live SIG data tells us which ones are plausible.

Candidate minimal simulated agents:

1. fixed-spread maker;
2. midpoint maker;
3. external-reference threshold taker;
4. delayed external follower;
5. external-anchored inventory maker;
6. one-tick priority improver;
7. fixed cooldown shock bot;
8. capacity-constrained inventory clearer.

The purpose would be:

- adversarial testing of our execution logic;
- understanding aggregate ecology;
- checking whether proposed detectors recover known generating rules.

It would **not** prove that real competitors behave like the simulated bots.

### Promotion gate for building the bot farm

Build only if at least one of the following becomes true:

- live SIG data exhibits a stable unexplained behavioural signature;
- an ecology feature improves OOS markout/fill prediction;
- execution testing needs a controlled adversarial counterparty population;
- first-live-day observations show obvious strategy crowding.

Until then, replay and real live observation have higher expected value.

---

## 8. Relationship to the maths ledger

No new maths **workstream** is required now.

The one potentially distinct hypothesis identified by this research is the ECO-001 question: whether an observable post-external SIG reaction contains incremental information beyond the external impulse itself. Treat this as a candidate **opponent-mediated lead/lag** estimator/hypothesis, not as a new modelling programme.

Do not add a new `MATHS_LEDGER.md` item yet. Only promote it if live data shows stable chronological OOS executable value beyond M-087–M-089 and ordinary spread/depth/flow controls.

Most necessary objects already exist:

- M-058 onward — market-making economics / markouts;
- M-063 — fill-conditioned adverse-selection target;
- M-064/M-065 — order-flow primitives;
- M-075–M-077 — fill/queue modelling;
- M-079–M-082 — passive quote value, action selection and quote churn;
- M-087–M-089 — external lead/lag and observable-timestamp discipline;
- M-116 — champion/challenger evaluation;
- M-127 — signal half-life vs latency;
- historical competition research already introduced market ecology and edge-decay/crowding as project concepts.

**Decision:** do not add archetype names as separate mathematical objects.

Only add a new ledger item later if live data produces a genuinely new estimator, objective or mathematical hypothesis.

---

## 9. Safety / rules boundary

This work concerns lawful market interaction only.

"Exploit" means trade profitably against predictable market behaviour through permitted market activity.

This research excludes:

- hacking;
- credential theft;
- denial of service;
- API abuse;
- impersonation;
- collusion;
- rule circumvention;
- manipulation outside legitimate trading.

Do not deliberately generate misleading orders merely to induce another participant's algorithm into an artificial state.

If a counter-strategy relies on deceptive/manipulative behaviour rather than ordinary legitimate trading intent, it is outside this research scope.

---

## 10. Engineering implication

### BUILD NOW

- preserve observable timestamps;
- preserve normalized SIG trades and authoritative book observations;
- record enough depth/size information for quote persistence/replenishment analysis;
- retain data-health/reconciliation state so feed failures cannot masquerade as bot behaviour;
- standardize forward markouts.

### TEST AFTER LIVE DATA

- external-follower reaction functions;
- stable replenisher/anchor behaviour;
- predictable sweep/capacity flow;
- one-tick priority patterns;
- crowding/edge decay;
- identity-free behavioural clusters.

### DEFER

- large "Shadow Field" multi-agent simulator;
- participant attribution system;
- sophisticated HMM/Hawkes ecology models;
- adversarial bot training platform;
- strategy changes based only on the AI-idea corpus.

### REJECT

- assuming LLM answer frequency equals competitor prevalence;
- treating a named archetype as real before observing it;
- using title similarity to infer structural relationships;
- trading supposed "dumb flow" without future-markout validation;
- building around hidden participant identity if the venue does not expose it.

---

## 11. First-live-day checklist

During the first live Cup session, answer these before building anything new:

1. How quickly does SIG respond after large mapped external moves?
2. Are local responses threshold-like or continuous?
3. Are there recurring order sizes or quote distances?
4. Do best levels replenish at stable intervals?
5. Are one-tick improvements common?
6. Does liquidity vanish and reappear on repeatable cooldowns?
7. Are repeated aggressive bursts followed by continuation or reversion?
8. Do apparent ecology features survive after removing periods of feed/reconciliation uncertainty?
9. Does any ecology feature improve future executable markout beyond spread/depth/external FV?
10. Is the behaviour stable enough to survive chronological holdout?

If the answer to 9 or 10 is no, opponent ecology remains descriptive rather than tradable.

---

## 12. Source catalogue

### Canonical internal sources

- `docs/research/QUANT_COMPETITION_HISTORY_2021_2026.md`
- `COMPETITION_PLAYBOOK.md`
- `MATHS_LEDGER.md`
- `docs/research/AI Ideas`

### External historical sources

- Linear Utility — IMC Prosperity 2024, 2nd globally  
  https://github.com/ericcccsliu/imc-prosperity-2

- Chris Roberts et al. — IMC Prosperity 2025, 1st USA / 7th global write-up  
  https://github.com/chrispyroberts/imc-prosperity-3

- IMC Prosperity 2025 retrospective with Olivia detection  
  https://github.com/TimoDiehm/imc-prosperity-3

- IMC Prosperity 2026 — bot decomposition, hidden-flow and counterparty research  
  https://github.com/saksham10arora-dotcom/imc-prosperity-4

- IMC Prosperity 2026 — counterparty-role profiling  
  https://github.com/WindelsArthur/imc-prosperity4

- IMC Prosperity 2026 — follow-trading / informed-bot filtering  
  https://github.com/Leo-Hawking/IMC-Prosperity-4-Review

- Jane Street ETC winning strategy / competition environment  
  https://github.com/EnriqueKhai/JaneStreetETC22

- Jane Street ETC participant bot  
  https://github.com/P254/jane-street-trading-bot

- Optiver Ready Trader Go official competition description  
  https://readytradergo.optiver.com/how-to-play/

- UChicago Trading Competition 2026 — live execution, faster opposing reactions and compressed response window  
  https://www.cs.utexas.edu/~kavish/blog/uchicago-trading-competition-2026.html

---

## Final decision

Opponent ecology is a **real research lane but not yet a production strategy lane**.

The historical competitions support this:

> recurring behaviour can be worth more than a sophisticated model when it is stable, observable and economically testable.

But the same history also warns against overfitting simulator quirks and assuming a discovered pattern persists.

For SIG, the operating rule is therefore:

> **Capture behaviour first. Measure predictive/economic value second. Simulate only after live evidence says which behaviour is worth simulating.**
