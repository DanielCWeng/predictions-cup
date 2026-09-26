# UI Research Report — Predictions Cup Terminal

**Deliverable:** UI_RESEARCH_REPORT.md  
**Research date:** 26 September 2026  
**Repository:** `DanielCWeng/predictions-cup`  
**Audited `main`:** `885039602dfbef3ee4e3bd161a4de12d0b0ff75e`  
**Status:** Research deliverable only. This document deliberately does not contain the final terminal specification, mockups, or implementation tickets.

---

## Executive conclusion

The right product is not a generic dashboard and it is not yet an execution terminal.

The accepted system is strongest in four areas:

1. **live read-only market observation** — SIG tournament Realtime, broad SIG scalar/BBO state, explicitly tracked SIG depth, and supervised Polymarket market-data capture;
2. **data trust and runtime provenance** — explicit SIG trust states, gap/reconnect handling, Polymarket liveness and recovery state, observable timestamps, and governed REST state;
3. **deterministic replay** — observable-time reconstruction of quotes, trades, depth, trust and health;
4. **empirical research** — implemented LEADLAG, RV, LOO-PRICE and LOO-FAMILY replay experiments with executable-crossing markouts and explicit invalidity.

The system does **not** currently have a production fair-value service, live opportunity scanner, shadow-order service, risk engine, portfolio accounting, order submission/cancellation, or live trading path. Those concepts must not be presented as if they are live capabilities.

That leads to a clearer information architecture than the initial sketch:

```text
01 MONITOR      live universe + selected market + event tape
02 BOOKS        deep paired-market inspection
03 RESEARCH     replay experiment outputs + relationship/family analysis
04 REPLAY       historical session playback through the same market widgets
05 SYSTEM       feed trust, collector health, governor, storage and acceptance state
```

There should be **no EXECUTION workspace** until execution exists. In live operation the global mode should initially be **OBSERVE**. Replay should switch the whole shell unmistakably to **REPLAY**. "SHADOW", "LIMITED" and "LIVE" should not be actionable modes until their backends exist.

The visual direction in the brief is supported by the research: serious trading tools gain speed from dense tables, linked workspaces, fixed numerical alignment, semantic colour, persistent market depth and keyboard-efficient navigation. The useful lesson from older Bloomberg/Reuters screens is not "make it ugly"; it is **compress the distance between observation, context and action**. For Predictions Cup, that means hundreds of real data points on screen, extremely little ornamental chrome, and uncertainty shown as first-class market data.

---

# 1. Repository capability audit

## 1.1 Repository state and acceptance boundaries

The repository's canonical read order now begins with `ORCHESTRATOR.md`, followed by `CURRENT_STATE.md`, `BUILD_LEDGER.md`, architecture, contracts and operations. GitHub merge state and actual `main` contents remain authoritative.

At research completion:

- `main` is `885039602dfbef3ee4e3bd161a4de12d0b0ff75e`;
- PR #21 / DOCS-ORCH-001 has reconciled the canonical state documentation;
- BUILD-007 / PR #20 is **MERGED / ACCEPTED** and its ARM64 EC2 runtime/storage gate passed on the actual host using a deliberately bounded 3-market / 6-token public test universe;
- EXPERIMENT-002 / PR #16 is **MERGED / ACCEPTED** as experiment machinery, with no empirical edge claimed until accepted real data is run through it;
- the live 2026 SIG ↔ Polymarket production crosswalk is still separately gated by LIVE-MAPPING-GATE-001;
- the later production mapping-bounded paired capture soak, including final SSH/reboot operational checks, remains downstream of that accepted crosswalk.

This distinction is important to the terminal. It should be capable of separating:

```text
CODE AVAILABLE
LIVE-VALIDATED
DATA AVAILABLE
TRUSTED NOW
```

A subsystem can be accepted code and even pass a bounded runtime gate while the current production data universe is still unavailable or untrusted.

## 1.2 Technical shape

The repository is currently a **Python 3.12 modular monolith** using a `src/` package layout.

Core dependencies are:

- aiohttp;
- httpx;
- Pydantic / pydantic-settings;
- PyArrow;
- Supabase.

There is **no frontend framework and no UI-facing HTTP/WebSocket application server** in the repository. There is no React/Next.js/Vue application, FastAPI/Flask service, or established browser API contract.

Therefore the future web UI needs a **thin read-only presentation boundary**, but it does not justify an infrastructure rewrite. The collectors, persistence, replay and experiment machinery should remain canonical. A browser adapter should expose those existing states rather than recreate them.

## 1.3 Capability inventory

Legend:

- **YES** — accepted code/data contract exists on current `main`;
- **DERIVABLE** — underlying data exists, but the value is not currently a first-class live contract;
- **REPLAY / RESEARCH** — implemented for deterministic experiment/replay use, not a persistent live signal service;
- **GATED** — machinery exists but current production use depends on an acceptance/data gate;
- **NO** — do not represent as available.

| Information | Status | Current source | Native cadence / trigger | Trust caveat |
|---|---|---|---|---|
| SIG tournament catalogue | YES | SIG REST + `SigRealtimeStateEngine.market_states` | startup/resync/universe refresh | explicit tournament required |
| SIG broad latest/BBO/spread | YES | governed bulk price REST state | default 10s refresh, chunks <=100 exchange IDs | scalar/BBO state is not full-depth trust |
| SIG Realtime trade events | YES | tournament Realtime | event-driven | YES/NO is outcome-side metadata, not aggressor BUY/SELL |
| SIG `bookDirty` events | YES | tournament Realtime | event-driven | invalidation/reconciliation signal, not book contents |
| SIG revision continuity | YES | Realtime delivery provenance | every batch | gaps force recovery |
| SIG full depth | GATED PER EXCHANGE | authoritative REST order book | explicit tracked IDs; default depth 20 | untracked exchange must be shown as UNTRACKED, not zero-depth |
| SIG depth trust | YES | `DepthState` + trust transitions | invalidation/recovery/expiry | TRACKED_TRUSTED vs TRACKED_UNTRUSTED vs UNTRACKED_DEPTH |
| SIG tracked-depth freshness | YES | state + local observation time | default max trusted age 30s | upstream REST book has no server timestamp |
| SIG REST governor | YES | `RestGovernorSnapshot` | request-driven | current health export omits a few internally available governor fields |
| SIG REST p50/p95 latency | NO | individual log records only | per request logs | no current aggregate contract; do not fabricate |
| Polymarket supervised market metadata | GATED | Gamma + operational SQLite | default Gamma refresh 300s | production IDs should come from accepted mapping |
| Polymarket BBO / scalar panel | YES WHEN CAPTURED | local book + 1s observations | default 1s | only selected/supervised tokens |
| Polymarket book changes | YES WHEN CAPTURED | WebSocket -> Parquet | event-driven | disconnect invalidates local trust until reseed |
| Polymarket public trades | YES WHEN CAPTURED | WebSocket -> Parquet | event-driven | hashed trades de-duplicated in canonical replay; unhashed at-least-once |
| Polymarket bounded depth snapshots | YES WHEN CAPTURED | local book -> Parquet | default 60s, default depth 20 | selected/supervised tokens only |
| Polymarket feed health | YES | `IngestionHealth` + operational SQLite | live state / periodic persistence | degraded Gamma after startup may preserve last-good capture |
| Mapping class/status/direction/confidence | YES AS CONTRACT | `MarketMapping` | artifact/version driven | live 2026 production crosswalk not yet accepted |
| SIG ↔ PM basis | DERIVABLE | mapped BBO + mapping direction | as prices update | only meaningful with verified semantic mapping; COMPLEMENT is side-aware |
| 1m / 5m market change | DERIVABLE | captured observations/replay | windowed | requires enough retained history; not a native live field |
| Market activity / recent movement ranking | DERIVABLE | trades + BBO history | windowed | define metric explicitly |
| LEADLAG observations | REPLAY / RESEARCH | EXPERIMENT-002 | experiment run | implemented / waiting for real mapped data evidence |
| RV residual observations | REPLAY / RESEARCH | EXPERIMENT-002 | experiment run | not a production fair value |
| LOO-PRICE observations | REPLAY / RESEARCH | EXPERIMENT-002 | experiment run | explicit reference configuration |
| LOO-FAMILY observations | REPLAY / RESEARCH | EXPERIMENT-002 | experiment run | hard leakage guards; no inferred relationship graph |
| Executable forward markouts | REPLAY / RESEARCH | replay + experiment evaluation | configured horizons | crossing economics, not fills |
| Strategy hit rate / mean / median markout | REPLAY / RESEARCH | evaluation summaries | experiment output | only valid samples; not live P&L |
| Production fair value | NO | placeholder only | — | do not show |
| Live opportunity scanner | NO | placeholder only | — | do not show "opportunity tape" as live |
| Maker fill / queue model | NO | registry says planned | — | do not show fill probability |
| Shadow orders / shadow P&L | NO | no runtime path | — | do not show |
| Live orders / cancels / fills | NO | canonical data types only | — | data model existence is not execution capability |
| Position / portfolio accounting | NO | canonical `Position` type only | — | no portfolio runtime |
| EC2 uptime | NO APP CONTRACT | OS/systemd | OS-driven | could be added through a small ops adapter |
| Service active/restart state | NO APP CONTRACT | systemd/journal | OS-driven | ingestion health is available, unit state is not yet an app contract |
| Disk growth / filesystem free space | NO APP CONTRACT | OS filesystem | sampled | add explicitly if operationally useful |
| General event queue depth | NO | none | — | REST governor queues are not a general event queue |
| Deterministic replay | YES | `predictions_cup.replay` | offline/session-driven | browser transport not yet implemented |

## 1.4 SIG data that is genuinely useful to the terminal

SIG has unusually good **trust semantics** for a young system. Preserve them.

The UI can accurately expose:

- Realtime connected/disconnected;
- last Realtime receive;
- last valid batch;
- last REST reconciliation;
- revision gap count;
- reconnect count;
- reconciliation failure count;
- bounded book refresh count;
- known exchange count;
- market count;
- tracked depth count;
- tracked trusted / tracked untrusted count;
- untracked depth count;
- bulk price refresh/missing counts;
- full-book refresh count;
- oldest tracked-book age;
- governed REST rate;
- total REST requests;
- 429 count;
- shared cooldown count;
- pending high/background reads.

Internally, the REST governor also has pending normal reads and cooldown remaining. If the UI needs those, expose them explicitly rather than scraping logs.

The most important limitation is semantic: **SIG broad BBO does not mean SIG full depth is trusted**. The interface should make that distinction obvious. An untracked market can still have useful BBO state while its DOM must say `DEPTH UNTRACKED`.

## 1.5 Polymarket data that is genuinely useful

BUILD-007 gives the UI a better data shape than the original broad SQLite design:

```text
operational SQLite
    market metadata
    token metadata
    ingestion health

Parquet/ZSTD research history
    1s observations
    book_changes
    trades
    depth_snapshots
```

The UI can expose current or reconstructed:

- bid/ask;
- midpoint;
- spread;
- bounded depth;
- last trade;
- book hash where useful for diagnosis;
- source timestamp when supplied;
- local observed timestamp;
- tick/minimum order metadata;
- recent public trades;
- quote/book changes;
- WebSocket liveness;
- reconnects and reconnect reason;
- parse failures;
- unknown events;
- uninitialized-delta count;
- Gamma refresh state;
- snapshot state;
- storage failure count;
- subscribed market/token count.

A live terminal should not depend on scanning historical Parquet for every paint. The eventual UI boundary needs a current-state cache or compact latest-state query path, while Parquet remains the historical source.

## 1.6 Mapping reality

The mapping model is strong enough to drive honest UI states:

- EXACT;
- NEAR;
- DERIVED;
- MODEL_ONLY;
- NO_TRADE;
- SAME / COMPLEMENT / DERIVED direction;
- VERIFIED / REVIEW_REQUIRED / UNRESOLVED;
- persisted confidence;
- semantic notes;
- resolution notes.

However, the production crosswalk is not yet accepted. Until that gate is cleared:

- do not imply that the whole 237-ish SIG universe has live Polymarket equivalents;
- do not calculate a cross-venue basis for unresolved rows;
- do not silently pair markets by title;
- use `NO MAPPING` or `REVIEW` visibly;
- use mapping version/acceptance status as part of system health.

For a COMPLEMENT mapping, cross-venue comparison must transform the external quote correctly; bid and ask sides swap under `1 - p`.

## 1.7 Replay and research reality

Replay is a first-class strength and should influence the product architecture, not be bolted on later.

Replay events already distinguish:

- book observations;
- book changes;
- depth snapshots;
- trades;
- trust transitions;
- health transitions.

State is advanced only by **observable local time**, not future/source time. The replay model also has explicit invalid reasons such as:

- SIG_UNTRUSTED;
- SIG_STALE;
- EXTERNAL_STALE;
- DATA_GAP;
- NO_EXECUTABLE_START;
- REFERENCE_UNAVAILABLE;
- INSUFFICIENT_PREDICTOR_COVERAGE;
- INVALID_SPREAD.

This gives the UI enough semantics to make replay look like the live terminal while still showing why a value was not usable.

EXPERIMENT-002 implements:

- LEADLAG;
- RV;
- LOO-PRICE;
- LOO-FAMILY;
- probability/logit movement;
- midpoint/microprice/bid/ask/last-trade references;
- response horizons 1s, 2s, 5s, 10s, 30s, 60s, 300s;
- executable crossing markouts;
- optional explicit cost inputs;
- NORMAL/EVENT regime tagging;
- response curves and evaluation summaries.

The registry says those experiments are **IMPLEMENTED / WAITING FOR DATA**. The terminal should therefore present them as **research evidence**, not as live alpha.

---

# 2. External terminal research

## 2.1 Research approach

Official vendor/exchange material was preferred. Marketing claims about superiority or performance were not treated as evidence; workflow and interface descriptions were.

The useful references were:

1. Bloomberg Professional — Bloomberg Terminal / Launchpad;
2. Bloomberg UX — customer-centric design ethos and consistency;
3. Bloomberg — keyboard workflow;
4. Thomson Reuters — Reuters 3000 Xtra launch material;
5. LSEG Workspace — Monitor, Quote Line, Blended Order Book, Time & Sales and execution-workflow integration;
6. Trading Technologies — MD Trader static price ladder, depth and workspace linking;
7. Nasdaq Trader — market-maker workflow and historical ACES workstation/order-routing functions.

Sources are listed at the end of this section.

## 2.2 Bloomberg: density is a workflow decision, not a style gimmick

Bloomberg's own product material describes Launchpad as a customizable workspace combining dynamic monitors, alerts, charting and news. Bloomberg UX material is even more relevant: it describes a function-over-form approach, high-contrast colour to pick information from noise, and the black/amber palette as a deliberate recognizable system.

Useful lessons:

- **customizable linked monitors beat giant dashboard cards**;
- a consistent interaction grammar matters more as density rises;
- high contrast is useful when colour has stable meaning;
- the product can look distinctive without large branding elements;
- keyboard shorthand works because repeated tasks get compressed into learned commands;
- power-user density and learnability are not opposites if conventions are consistent.

For Predictions Cup, "Bloomberg-like" should mean **dense, consistent and commandable**, not copying Bloomberg function names or reproducing amber everywhere.

## 2.3 Reuters / LSEG: put live, historical and reference context together

The 1999 Reuters 3000 Xtra launch described personalized views integrating news, real-time, historical and reference data in a single screen. Modern LSEG Workspace expresses the same idea through explicit workflow tools such as:

- MON — Monitor;
- QLI / Quote;
- BOB — Blended Order Book;
- TAS — Time & Sales;
- market movers / alerts;
- charts and analytics.

The lesson is architectural: professional terminals separate **monitoring**, **depth**, **prints**, **analysis** and **execution**, then link them around the same selected instrument.

Predictions Cup should copy that separation. Selecting a market in MONITOR should update BOOKS and context panes without forcing every possible detail into one mega-screen.

## 2.4 Trading Technologies: the order book should be spatially stable

TT's MD Trader is the strongest direct reference for the BOOKS workspace.

Key patterns:

- depth is displayed against a **static vertical price axis**;
- bid/ask quantities move around the stable price ladder rather than the ladder itself constantly shifting;
- the inside market has a strong spatial anchor;
- recenter/freeze are explicit operator controls;
- detailed depth is available on demand rather than making every level permanently enormous;
- LTQ / LTP / volume-at-price are separate semantic columns;
- columns, row height and font size are configurable;
- multiple widgets can be linked from a broader market grid.

For Predictions Cup, the dual venue book should preserve a stable price coordinate where practical. It should not be two generic scrolling tables with the top row always meaning "best price".

The user is not executing yet, so the ladder should be **inspection-first**: no clickable order-entry affordances that suggest trading exists.

## 2.5 Market makers: quote state changes continuously

Nasdaq describes market-maker work as entering, retrieving, monitoring and adjusting quotations as market conditions change. Historical ACES documentation also emphasizes execution/order/reject alerts and Time & Sales.

The relevant lesson now is not to add order buttons. It is that an operator screen must continuously answer:

- what changed;
- whether my current quote/book view is valid;
- whether a market moved before another;
- whether the system has acknowledged the new state.

This supports a dense event tape and visible trust/reconciliation state.

## 2.6 What is functional versus theatrical

### A — operationally necessary

- live/disconnected feed state;
- observable timestamp / age;
- trust state;
- market universe;
- bid/ask/spread;
- mapped venue identity;
- book depth where actually tracked;
- stale / gap / reconciling state;
- system health;
- replay/live mode separation.

### B — analytically useful

- basis;
- recent change;
- recent prints;
- activity ranking;
- compact price/basis trace;
- experiment residuals and markouts;
- relationship matrices;
- response-curve summaries.

### C — drill-down / situational

- raw event payload/provenance;
- mapping semantic notes;
- deep individual order-level detail if ever available;
- Gamma metadata;
- governor queue details;
- source-vs-observed timestamp diagnostics;
- Parquet shard/storage diagnostics.

### D — LARP / atmosphere that is still honest

- terminal number/session ID;
- UTC clock;
- compact uptime once sourced;
- bordered command/status line;
- keyboard function labels;
- transient changed-cell flashes;
- scrolling real event tape;
- terse fixed-width state abbreviations;
- monitor/workspace numbers;
- small network/storage activity counters when backed by real data.

The D-class layer must disappear gracefully if the underlying data is unavailable. The screen should feel alive because the collectors are alive.

### External research sources

- Bloomberg Terminal / Launchpad: https://bloomberg.com/professional/solution/bloomberg-terminal
- Bloomberg UX — customer-centric design ethos: https://www.bloomberg.com/company/stories/bloombergs-customer-centric-design-ethos/
- Bloomberg UX — consistency: https://www.bloomberg.com/ux/2020/08/11/consistency-more-than-just-a-buzzword/
- Bloomberg keyboard: https://www.bloomberg.com/company/stories/get-ready-for-bloombergs-summer-of-puzzles-2025/
- Thomson Reuters — Reuters 3000 Xtra launch: https://ir.thomsonreuters.com/news-releases/news-release-details/reuters-launches-reuters-3000-xtra-service
- LSEG Workspace equities: https://www.lseg.com/en/data-analytics/products/workspace/equities
- LSEG workflow integration: https://www.lseg.com/en/data-analytics/products/workspace/redi-on-workspace-oems/extend-workspace-into-trading-workflow/connect-workspace-workflows-to-redi
- LSEG Workspace for sales/traders: https://www.lseg.com/en/data-analytics/products/workspace/sales-traders
- Trading Technologies — Market Data in MD Trader: https://library.tradingtechnologies.com/trade/basic-order-entry/md-trader/description-md-trader/market-data-in-md-trader/
- Trading Technologies — MD Trader overview: https://library.tradingtechnologies.com/trade/basic-order-entry/md-trader/description-md-trader/md-trader-overview/
- Trading Technologies — MD Trader configuration: https://library.tradingtechnologies.com/trade/basic-order-entry/md-trader/task-md-trader/configuring-md-trader/
- Nasdaq Trader — Market Maker Process: https://nasdaqtrader.com/Trader.aspx?id=MarketMakerProcess
- Nasdaq Trader — historical ACES interface: https://nasdaqtrader.com/Trader.aspx?id=ACES

---

# 3. Functional requirements

## 3.1 Five-second operator test

From any workspace the operator must be able to determine, without opening a modal:

```text
MODE        OBSERVE or REPLAY
SIG         LIVE / STALE / DOWN
PM          LIVE / DEGRADED / DOWN / DISABLED
MAPPING     ACCEPTED / PARTIAL / GATED / NONE
DEPTH       how many SIG markets are tracked/trusted
DATA AGE    age of selected quote/book
SYSTEM      healthy / degraded
```

This should live in a persistent shell/status strip rather than a KPI-card row.

## 3.2 MONITOR requirements

The primary market universe should prioritize **scan speed**.

Core live columns:

```text
MARKET
OUTCOME
SIG BID
SIG ASK
SIG SPR
SIG AGE
PM BID
PM ASK
PM SPR
PM AGE
MAP
BASIS
MOVE
DEPTH
STATE
```

Conditional/derived columns may be toggled.

Rules:

- PM cells are blank/`—` with `NO MAP` if mapping is unavailable;
- basis is not computed for unresolved semantic mappings;
- COMPLEMENT mappings transform prices correctly;
- untracked SIG depth is `UNTRACKED`, not `0`;
- stale/untrusted states override cosmetic positive/negative colouring;
- all numbers use tabular alignment;
- market name text truncates, but identity/status columns do not;
- sorting must be deterministic and keyboard-accessible.

High-value sorts:

- absolute mapped basis;
- recent movement;
- spread;
- age/staleness;
- data/trust failure;
- recent event activity.

"Signal" should **not** be a default live column until a live signal service exists.

## 3.3 Selected-market context

The selected market pane should be compact and answer:

- what exactly is this contract;
- what is the mapping class/direction/status;
- current SIG/PM BBO;
- quote age and trust;
- last trade;
- recent basis/change;
- whether full depth exists;
- why a cross-venue comparison is unavailable, if it is unavailable.

Resolution/mapping notes belong one keystroke away, not permanently expanded.

## 3.4 BOOKS requirements

BOOKS is a priority workspace.

Where a verified direct mapping exists:

- show SIG and PM side-by-side;
- retain a stable price ladder if price increments permit;
- show bid size / price / ask size;
- show BBO;
- show spread;
- show last trade;
- show book age;
- show source-observed timing;
- show trust state;
- show at least the configured depth levels;
- provide recenter and freeze;
- visually distinguish "no liquidity at this level" from "depth not tracked".

If SIG depth is untracked, keep the SIG BBO summary visible but replace the ladder body with an explicit `DEPTH UNTRACKED` state.

Do not add order-entry click targets.

## 3.5 Event tape requirements

The live tape should be based on real events only.

Candidate event classes:

```text
SIG TRADE
SIG BOOK_DIRTY
SIG RECONCILE
SIG TRUST
SIG SETTLED
SIG REV_GAP
PM BOOK
PM TRADE
PM RECONNECT
PM HEALTH
MAP STATUS
SYSTEM WARN
```

Each line should carry enough provenance to inspect:

```text
time | venue | event | market | price/size or state | observed age
```

Controls:

- pause/freeze;
- resume;
- autoscroll on/off;
- venue filter;
- event-class filter;
- selected-market-only toggle;
- inspect raw provenance.

A paused tape must say `PAUSED` visibly; the data feed itself continues.

## 3.6 RESEARCH requirements

This workspace should be named **RESEARCH**, not live "STRATEGY", until a persistent live decision service exists.

It may display loaded experiment results for:

- LEADLAG;
- RV;
- LOO-PRICE;
- LOO-FAMILY.

Useful fields:

- experiment ID;
- dataset ID;
- target;
- reference set;
- decision time;
- regime;
- direction;
- signal value;
- threshold;
- spread/depth/quote age;
- residual;
- active reference count;
- horizon;
- entry price;
- future bid/ask;
- gross markout;
- net markout only when explicitly costed;
- valid/invalid;
- invalid reason.

Summary panels may show:

- sample count;
- mean executable markout;
- median executable markout;
- hit rate;
- p25/p75;
- total gross markout;
- total net markout if complete.

Do not show Sharpe. Do not label replay markout as P&L.

## 3.7 Relationship/family analysis

A relationship view is useful, but current data does not support an automatically inferred graph.

Initial family analysis should be driven by **explicit experiment/mapping configuration** and use a matrix/table first:

```text
TARGET | REFERENCE | RELATION | MAP | AGE | RESIDUAL | HORIZON | MARKOUT | N
```

A graph can be added later only if it answers a real question better than the matrix.

## 3.8 REPLAY requirements

Replay should reuse MONITOR, BOOKS and tape components.

Global differences:

- unmistakable `REPLAY` mode;
- session/dataset ID;
- replay timestamp;
- wall-clock playback speed;
- seek position;
- no ambiguity with current live feeds.

Controls required at product level:

```text
PLAY
PAUSE
STEP EVENT
1x
5x
20x
100x
SEEK
NEXT SIGNAL
NEXT GAP
NEXT TRUST CHANGE
```

The existing replay engine is deterministic, but the browser transport/controller for those interactions is not implemented yet.

## 3.9 SYSTEM requirements

SYSTEM should be trading-data operations, not a generic cloud dashboard.

Primary SIG rows:

- Realtime connected;
- last receive;
- last valid batch;
- last reconciliation;
- revision gaps;
- reconnects;
- reconciliation failures;
- known markets/exchanges;
- tracked depth;
- trusted/untrusted;
- oldest tracked book;
- bulk refreshes/missing;
- full-book refreshes;
- REST rate;
- request count;
- 429 count;
- cooldown count;
- pending governed reads.

Primary PM rows:

- capture enabled/disabled;
- WebSocket connected;
- last message/pong/book/trade;
- market/token subscriptions;
- reconnects/reason;
- parse failures;
- unknown event count;
- uninitialized deltas;
- Gamma refresh status;
- snapshot status;
- storage failures.

Secondary/OS metrics should only appear after a real adapter exists:

- systemd unit state;
- process uptime;
- restart count;
- CPU/memory;
- disk free/growth;
- Parquet shard write rate.

---

# 4. LARP requirements

The desired 15% "trading desk theatre" is productive when it reinforces real state.

## Keep

- `TERM 01` / workspace number;
- UTC clock;
- session/dataset identifier;
- compact mode banner;
- bottom command/status line;
- fixed-width abbreviated status codes;
- subtle 1px panel borders;
- small amber section identifiers;
- cyan reference/external labels;
- transient cell flash on a real changed value;
- real event tape;
- visible message/revision counters;
- actual capture/storage throughput once measured;
- keyboard hint strip;
- terse audible alert option for genuine severe degradation, default off.

## Reject

- fake alpha confidence;
- fake orders;
- fake fills;
- fake P&L;
- invented "risk score";
- random CPU-style meters with no source;
- animated waveforms unrelated to data;
- permanent blinking;
- decorative candlesticks;
- ticker text that is not a real event stream;
- latency numbers with fabricated precision;
- "LIVE TRADING" language before execution exists.

A screen full of authentic revisions, quote ages, depth, prints and health changes will already look far more convincing than synthetic animation.

---

# 5. Anti-patterns

## 5.1 Generic SaaS dashboard

Avoid:

- card grids;
- 32px+ KPI numbers;
- large empty gutters;
- rounded floating surfaces;
- marketing headings;
- hero banners;
- oversized filters;
- soft gradients;
- glass panels.

## 5.2 Crypto-exchange imitation

Avoid:

- huge central candlestick chart;
- decorative green/red everywhere;
- order ticket pretending to be usable;
- wallet/account chrome;
- P&L theatre;
- bright neon.

## 5.3 Colour without semantics

Green/red should not mean "SIG/PM".

Recommended semantic hierarchy:

- **white/light grey** — primary neutral market values;
- **cyan** — external/reference venue identity/data;
- **amber** — headings, warning, attention;
- **green** — healthy / positive movement when context calls for it;
- **red** — unhealthy / negative movement when context calls for it;
- **grey** — inactive/unavailable metadata.

Trust/failure must also have text/state markers so colour is never the only signal.

## 5.4 Treating missing data as zero

Never render:

```text
missing depth -> 0
no mapping -> basis 0
untrusted book -> normal-looking BBO
no net cost inputs -> net = gross
no recent trade -> trade = 0
```

Use `—`, `NO MAP`, `UNTRACKED`, `UNTRUSTED`, `STALE`, or `N/A` with a reason.

## 5.5 One giant page

A single page containing 237 rows, two DOMs, charts, family graph, all strategy metrics, replay controls and system health simultaneously will be impressive for thirty seconds and bad to operate.

Use linked workspaces with a persistent shell and shared selected-market context.

---

# 6. Proposed information architecture

## Persistent shell

Always visible:

```text
PREDICTIONS CUP // TERM 01
[1 MONITOR] [2 BOOKS] [3 RESEARCH] [4 REPLAY] [5 SYSTEM]
MODE OBSERVE
SIG LIVE
PM DISABLED/LIVE/DEGRADED
MAP GATED/OK
UTC 16:xx:xx.xxx
```

A thin bottom line carries keyboard help, filter state and last warning.

## 01 MONITOR

Purpose: scan the whole tournament and decide what deserves attention.

Layout concept:

```text
+--------------------------------------------------------------------------+
| universe table                                                           |
|                                                                          |
|                                                                          |
+--------------------------------------+-----------------------------------+
| selected market compact context      | live event tape                   |
+--------------------------------------+-----------------------------------+
```

This is the default workspace.

## 02 BOOKS

Purpose: understand one mapped market deeply.

```text
+--------------------------+--------------------------+--------------------+
| SIG DOM                  | PM DOM                   | recent prints      |
|                          |                          | + book events      |
|                          |                          |                    |
+--------------------------+--------------------------+--------------------+
| compact basis / BBO / timing trace + mapping/trust context               |
+--------------------------------------------------------------------------+
```

If mapping/depth is unavailable, the absence is a visible state, not a broken layout.

## 03 RESEARCH

Purpose: inspect experiment evidence and related-market structure.

Tabs/panes:

- observations;
- response curves;
- market/reference matrix;
- invalidity/data quality;
- experiment metadata.

Do not show this as a live execution screen.

## 04 REPLAY

Purpose: load a dataset/session and run the same operator interface through history.

Replay owns the timeline/controller, but embeds the same MONITOR/BOOKS/tape components. This preserves perceptual transfer: the operator learns one market language.

## 05 SYSTEM

Purpose: answer "can I trust the screen?"

Groups:

- SIG;
- Polymarket;
- mapping/acceptance;
- storage;
- later: host/systemd.

Failures remain visible until recovered/acknowledged; do not rely on transient toasts.

## Why no separate FAMILIES workspace yet

LOO-FAMILY and explicit relationships exist at experiment-configuration level, but there is no canonical live relationship engine. A dedicated FAMILIES workspace today would imply more live product capability than exists.

Start family/relationship analysis inside RESEARCH. Promote it to its own workspace only when live relationship state becomes a canonical service.

---

# 7. Proposed visual system

This section defines a direction, not the final pixel specification.

## 7.1 Typography

Primary:

```css
font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
font-variant-numeric: tabular-nums;
```

Use monospace for:

- tables;
- prices;
- quantities;
- timestamps;
- state labels;
- command/status chrome.

A restrained sans-serif may be used for long-form help/tooltips if it materially improves reading.

Target desktop sizing:

- 11–12px dense market rows;
- 12–13px normal terminal text;
- 13–14px section titles;
- no oversized display typography.

## 7.2 Spacing

Use a compact 2/4/6/8px rhythm.

Typical table row target: roughly 20–24px depending on monitor DPI.

There should be enough density that a 1440p screen can show dozens of markets without scrolling, while still preserving row selection and state markers.

## 7.3 Surfaces and borders

Suggested starting tokens:

```text
BG_0          #070909
BG_1          #0B0D0E
BORDER        #2A2F31
TEXT_PRIMARY  #D6D9D9
TEXT_MUTED    #747C7F
AMBER         #E6A23C
CYAN          #55BFD2
GREEN         #67C27C
RED           #E06464
```

These are starting points, not brand commitments.

Rules:

- square corners;
- 1px borders;
- no box-shadow glow;
- no gradients;
- panel titles sit in the border/header band, not in floating cards.

## 7.4 Venue semantics

Do not use green/red to identify venues.

Recommended:

- SIG primary market data: neutral white/light grey;
- Polymarket/reference data: cyan accent;
- derived basis/research values: neutral with sign colouring only where appropriate;
- mapping/trust warnings: amber/red state treatment.

## 7.5 Tables

- left-align market text;
- right-align all numeric fields;
- fixed decimal precision per field;
- stable columns;
- compact headers;
- row selection via border/inverse strip rather than giant highlight;
- stale/untrusted row state may desaturate values and add a state code;
- changed cells flash briefly, then return to baseline;
- no continuously pulsing cells.

## 7.6 Charts

Charts are secondary.

Use:

- compact line traces;
- basis traces;
- quote/BBO step traces;
- experiment response curves;
- sparse markers for signals/trust events.

Avoid:

- giant candlestick charts;
- 3D;
- gradients;
- animated axes;
- default TradingView imitation.

For probability contracts, an optional fixed 0–1 scale is useful when comparing absolute probability; zoomed microstructure views may use a local scale but must label it clearly.

## 7.7 Trust states

Every market-data pane supports:

```text
LIVE
STALE
UNTRUSTED
RECONCILING
DISCONNECTED
NO MAPPING
PARTIAL
UNKNOWN
UNTRACKED DEPTH
```

Trust states affect both colour and text. A stale numeric value can remain visible for diagnosis but must be visually marked as stale and carry its age.

---

# 8. Interaction system

## 8.1 Principle

Keyboard use should reduce pointer travel, not create a secret game.

Mouse interaction remains complete. Keyboard interaction is learned progressively through a persistent compact hint line and `?` help.

## 8.2 Workspace navigation

Prefer browser-safe bindings over relying on F1–F12, which are often intercepted by browsers/OS.

Suggested starting model when focus is not inside a text field:

```text
1             MONITOR
2             BOOKS
3             RESEARCH
4             REPLAY
5             SYSTEM
/             search/filter
?             shortcut help
Esc           clear modal/filter / return focus
```

An alternate modifier scheme such as Alt+1…5 may be added where raw number keys conflict with input.

## 8.3 Market navigation

```text
↑ / ↓ or j/k  next/previous row
Enter          inspect/select
b              BOOKS for selected market
r              RESEARCH context for selected market
Home/End        first/last visible row
```

Selection should be shared across linked panes/workspaces.

## 8.4 Tape controls

```text
Space or p     pause/resume tape when tape has focus
a              toggle autoscroll
f              filter
Enter          inspect selected event
```

Pausing the view never pauses capture.

## 8.5 BOOKS controls

```text
r              recenter ladder
Space          freeze visual ladder when BOOKS has focus
↑ / ↓          move ladder cursor
PgUp/PgDn      move by larger price block
```

No order-entry hotkeys until execution capability exists.

## 8.6 REPLAY controls

```text
Space          play/pause
.              step one event/frame
[ / ]          slower/faster
g              seek
n              next research signal
x              next invalid/gap/trust event
```

Final bindings should be tested against browser defaults and accessibility needs before specification.

## 8.7 Command palette versus terminal command line

A lightweight command input is appropriate because the product has stable entities and workspace actions.

Examples:

```text
/MARKET michigan
/GOTO SYSTEM
/FILTER stale
/FILTER venue:pm trade
/REPLAY 2026-09-28
```

This should supplement direct shortcuts, not replace obvious controls.

---

# 9. Data requirements

## 9.1 Presentation boundary requirement

The largest technical gap between current backend and browser UI is **transport**, not market logic.

The future UI layer needs a read-only contract that can provide:

1. current compact SIG universe state;
2. current PM state for selected/supervised tokens;
3. mapping state/version;
4. bounded recent event stream;
5. current health snapshots;
6. replay session control + frames;
7. experiment result loading.

Do not make the browser read SQLite/Parquet directly. Do not duplicate capture logic in JavaScript.

The presentation boundary should be thin enough that the existing Python code remains the source of truth.

## 9.2 Element-by-element data table

| UI element | Data required | Current source | Availability | Native update | Fallback |
|---|---|---|---|---|---|
| global SIG state | connected, last receive, gaps, reconnects | SIG health snapshot | YES | event/maintenance | DOWN/UNKNOWN |
| global PM state | WS, last msg/pong/book/trade, failures | `IngestionHealth` | YES when collector runs | event/periodic | DISABLED/DOWN |
| market universe identity | market/exchange IDs, title, outcome, status | SIG REST runtime state | YES | refresh/resync | keep identity, mark state unknown |
| SIG BBO | bid, ask, spread, observed time | bulk price runtime/persistence | YES | ~10s default | STALE with age |
| SIG full depth | levels + REST observed time | tracked order-book state | GATED PER EXCHANGE | dirty/reconcile/<=30s policy | DEPTH UNTRACKED or UNTRUSTED |
| SIG last trade | price/qty/outcome label | Realtime | YES | event | — |
| PM identity | market/condition/token/outcome | Gamma + op SQLite | GATED production universe | ~300s metadata refresh | last-good metadata + DEGRADED |
| PM BBO | bid/ask/mid/spread | 1s obs/local book | YES when captured | 1s + deltas | STALE/DOWN |
| PM depth | price/size levels | local book + snapshots | YES when captured | event + 60s persisted snapshot | PARTIAL/STALE |
| PM prints | price/size/side/time | trade events | YES when captured | event | — |
| mapping badge | class/direction/status/confidence | mapping artifact | CONTRACT YES, production GATED | artifact version | NO MAP |
| basis | mapped executable/reference prices | derived | DERIVABLE | price updates | blank if map/quote invalid |
| 1m/5m move | history window | captures | DERIVABLE | sliding window | insufficient history |
| quote age | observed_at | both venues | YES | local clock | UNKNOWN |
| event tape | normalized events | SIG/PM/trust/health | YES underlying | event-driven | bounded historical buffer |
| research observation | signal/features/entry/future/validity | EXPERIMENT-002 output | REPLAY/RESEARCH | batch/run | no result |
| research summary | count/mean/median/hit/p25/p75 | evaluation | REPLAY/RESEARCH | batch/run | no result |
| family matrix | explicit reference relationships | experiment specs + mapping | RESEARCH | config/result | no inferred edges |
| replay mode | events/state/timestamp | replay engine | YES backend | event/frame | browser adapter required |
| service unit status | active/restarts/uptime | systemd | NOT EXPOSED | OS | omit |
| disk free/growth | filesystem stats | OS | NOT EXPOSED | sampled | omit |
| REST p50/p95 | latency samples/aggregation | logs only | NOT CONTRACTED | request | omit until instrumented |
| 503 aggregate | response counters | logs only | NOT CONTRACTED | request | omit until instrumented |
| shadow decisions | proposed action state | none | NO | — | omit |
| P&L/position | portfolio state | none | NO | — | omit |
| order state | order/execution service | none | NO | — | omit |

## 9.3 Update/rendering requirements

A dense terminal will become unusable if every incoming event rerenders the whole page.

The future implementation should assume:

- virtualized universe rows;
- bounded in-memory tape buffers;
- coalescing visual updates to animation frames;
- selective subscriptions by workspace/selected market;
- memoized derived basis/move values;
- separate data ingestion cadence from visual flash cadence;
- no unbounded DOM node growth;
- no chart library repaint of hundreds of hidden charts;
- canvas only for components where DOM/SVG demonstrably becomes a bottleneck.

A value can update at high frequency without animating continuously. The operator needs to see **change**, not motion for its own sake.

---

# 10. Open questions

These are the genuinely unresolved product/technical decisions after research.

## Q1 — What is the browser presentation boundary?

There is no current UI API/server. Decide whether to add a small read-only Python web gateway inside the monolith or a separate thin process. It should consume existing runtime/persistence contracts, not duplicate venue logic.

## Q2 — Where does accepted mapping/version state live operationally?

The terminal needs to know:

- crosswalk version;
- acceptance status;
- generated timestamp;
- coverage;
- whether the supervised PM IDs correspond exactly to that accepted artifact.

The mapping framework has the data model, but the live accepted artifact gate is still outstanding.

## Q3 — Should the first live UI compute research signals at all?

Current experiments are deterministic replay/batch research. A persistent live "signal" service would be a new capability. The first UI can remain honest by showing raw dislocations/basis in OBSERVE and experiment outputs only in RESEARCH/REPLAY.

## Q4 — How should SIG tracked depth be selected during operation?

Today the supervised service reads externally configured tracked exchange IDs and safely defaults to none.

Possible future policies:

- static operator configuration;
- bounded rotating research set;
- selected-market-driven tracking;
- strategy-selected tracking.

This is not merely UI behaviour because each choice affects governed REST budget and trust.

## Q5 — Which host metrics deserve first-class contracts?

Systemd state, EC2 uptime, process memory and disk growth are useful but currently OS facts outside the application contract. Decide which ones matter enough to instrument explicitly rather than scraping shell output.

## Q6 — What retention/query SLA should the UI assume?

SIG has a default 14-day realtime retention policy. Polymarket research uses Parquet shards and a different storage shape. The product needs an explicit expectation for:

- recent live query window;
- replay session discovery;
- long historical retention;
- storage compaction/archive.

## Q7 — What is the primary monitor target?

Final density depends heavily on the real operating environment:

- 1920x1080;
- 2560x1440;
- 4K;
- one monitor;
- two/three monitor layout.

The terminal should degrade on a laptop, but the primary design should be optimized for the actual competition desk.

## Q8 — How much workspace customization is worthwhile?

Professional terminals allow extensive layout customization, but configuration itself can become work. Decide whether v1 is:

- one strongly opinionated layout per workspace; or
- a small set of resizable panes / saved layouts.

Full drag-anything-anywhere layout management is not justified before the core terminal proves useful.

---

# Research recommendation

Proceed to the terminal specification **only after accepting these product boundaries**:

1. first live mode is `OBSERVE`;
2. five primary workspaces: MONITOR, BOOKS, RESEARCH, REPLAY, SYSTEM;
3. no execution/order/P&L UI until backend capability exists;
4. cross-venue metrics fail closed on mapping/trust;
5. RESEARCH is explicitly separate from live strategy claims;
6. the future browser layer is a thin read-only presentation boundary over existing Python state;
7. the retro look is implemented through density, typography, fixed spatial structure, keyboard fluency and real event flow — not fake data.

If those boundaries are accepted, the next deliverable should be the exact **terminal specification**: panel geometry, component contracts, table columns, state machines, keyboard behavior, failure/empty/loading states, and desktop/responsive rules.
