# MAKE-001 — Low-Latency Modular Market Maker

**Status:** DRAFT / IN REVIEW  
**Branch:** `make/001-low-latency-modular-market-maker`  
**PR:** #56  
**Base:** current canonical `main` at branch creation  
**LIVE default:** disabled  
**Real SIG orders required for acceptance:** no

## Mission

MAKE-001 is the first production market-making layer on top of BUILD-009. It does **not** create a second execution stack. The invariant remains:

`RuntimeSnapshot -> maker/plugin calculation -> Opportunity -> central Risk -> ExecutionPlan -> SHADOW/LIVE sink`

The maker owns fair value, quote mathematics, quote lifecycle and maker-local observability. BUILD-009 remains authoritative for central risk, payload identity, journal-before-write, idempotency, execution uncertainty and LIVE interlocks.

## Runtime shape

The production shape is deliberately small:

- one Python process;
- one asyncio I/O shell;
- one coalescing maker worker;
- no task-per-market polling;
- no Kafka/Redis;
- no database/filesystem/network calls in quote mathematics;
- integer SIG ticks at the execution boundary;
- immutable hot-path records;
- bounded in-memory telemetry.

SIG state comes from the existing governed Realtime/REST state engine. Polymarket state comes from an in-memory `OrderBookStore` seeded from the public CLOB REST surface and maintained by the public market WebSocket. MAKE supplies a no-op SIG state recorder so CAPTURE persistence is not an execution dependency. LIVE execution durability is still provided by the BUILD-009 SQLite/WAL execution journal.

## Plugin contracts

`predictions_cup.maker.contracts` defines small typed protocols:

- `FairValueProvider`
- `PredictiveAdjuster`
- `ToxicityProvider`
- `InventoryModel`
- `SpreadPolicy`
- `SizePolicy`
- `EligibilityPolicy`

Every plugin receives explicit `MakerMarketSnapshot` state. Plugins do not receive network clients, filesystems, databases or hidden global state.

Future modules therefore plug into the maker calculation surface rather than the execution plumbing:

- ETS can implement `FairValueProvider`;
- PRED-006 can implement `PredictiveAdjuster`;
- 005F update hazard can implement `ToxicityProvider`;
- future portfolio/inventory research can replace `InventoryModel`;
- future quote optimizers can replace spread/size/eligibility policies.

`maker/examples.py` contains fail-closed stubs for ETS, PRED-006 and 005F. They deliberately return untrusted outputs until implemented.

## Canonical direct Polymarket fair value

The initial provider is `DirectPolymarketFairValueProvider`.

It loads the canonical accepted crosswalk once at process startup. No file I/O occurs in `fair_value()`.

Current accepted mapping classes are handled as follows:

- **EXACT:** midpoint of the aligned mapped token;
- **NEAR:** same mechanical source path, retaining the mapping confidence supplied by the accepted crosswalk;
- **DERIVED:** sum only when the accepted mapping explicitly states mutually-exclusive partition/union semantics;
- **NO_TRADE / MODEL_ONLY:** fail closed.

The provider respects mapping orientation. Complement mappings use `1-p`.

External FV requires:

- a mapped token book;
- trusted source state;
- both bid and ask;
- finite probabilities with `0 <= bid <= ask <= 1`.

The provider preserves the source observation timestamp. It does **not** relabel an old-but-trusted book as fresh. The central eligibility policy is the single authority for FV freshness and returns `fv_stale` when the actual observation age reaches the configured limit. Trust and freshness are deliberately separate.

The canonical mapping currently contains 237 SIG exchange records. Six are NO_TRADE, leaving 231 directly maker-eligible records under accepted mapping semantics. All 87 DERIVED records in the current artifact use the explicit partition/union language required by the baseline provider.

## Quote mathematics

### Inventory reservation value

MAKE follows `MATHS_LEDGER.md` rather than promoting literal Avellaneda-Stoikov.

The production baseline is M-041, the exact binary-CARA reservation probability:

`r(q) = sigmoid(logit(p) - gamma*q)`

where:

- `p` is adjusted fair probability;
- `q` is signed YES inventory;
- `gamma` is the configured per-share CARA risk-aversion coefficient.

Positive inventory therefore lowers reservation value; negative inventory raises it. Hard inventory boundaries separately force one-sided quoting.

This model is appropriate to a Bernoulli settlement payoff under the CARA assumption. It does **not** assume Brownian mid-prices or stationary Poisson fills. Gamma remains an operating/model parameter requiring shadow calibration; the current default is conservative infrastructure, not a claimed optimal value.

### Spread

The baseline spread is intentionally auditable rather than clever. Half-width is an additive probability-space combination of:

- minimum/base tick width;
- external-FV uncertainty;
- optional volatility;
- optional toxicity/update-hazard widening.

The spread policy is replaceable. The maths ledger's log-odds/information-spread models remain valid challengers; MAKE does not hard-code the baseline as universal truth.

### Size

Size is scaled by:

- mapping/FV confidence;
- optional predictive confidence;
- toxicity;
- directional inventory headroom.

Hard inventory boundaries remove the side that increases inventory.

### Tick safety / passive behavior

Quote prices are rounded to legal SIG ticks and constrained to remain passive relative to observable SIG BBO. A quote that cannot be represented without crossing fails closed.

## Freshness and trust

MAKE tracks distinct observation times/trust for:

- SIG BBO;
- trusted SIG depth;
- Polymarket FV;
- account state;
- inventory;
- predictive plugin;
- toxicity plugin.

The source bridge converts each source's **actual** wall-clock observation into the current monotonic clock domain at snapshot construction. Trusted/connected state never resets the observation time. Future/clock-anomalous observations map to a negative monotonic age and fail closed.

The eligibility policy computes the nearest exact freshness expiry for every live quote. `MakerRuntimeLoop` stores one deadline per quoted exchange and waits on the single nearest deadline alongside normal feed notifications. When a deadline expires, only the affected exchange is enqueued for reevaluation. This means stale expiry itself cancels resting exposure even if no new feed event arrives, without task-per-market polling or a fixed high-frequency global sweep.

Default fail-closed actions:

- stale/untrusted FV -> cancel/suspend;
- stale/untrusted SIG BBO -> cancel;
- stale account/inventory -> cancel;
- account trust loss -> cancel;
- required depth stale/untrusted -> cancel;
- market no longer open -> cancel;
- mapping not tradeable -> NO_TRADE;
- plugin exception/malformed output/NaN/infinite probability -> suspend;
- PM feed disconnect -> external quotes are untrusted; global maker reevaluation cancels affected quotes.

## Quote lifecycle

`QuoteLifecycleManager` compares desired versus active state with explicit tick/size materiality thresholds.

Actions are:

- KEEP;
- PLACE;
- CANCEL;
- WAIT_RECONCILIATION.

Replacement is deliberately two-phase:

1. cancel the old quote;
2. do **not** create replacement exposure in that cycle;
3. wait until cancellation/account state is sufficiently authoritative;
4. only then allow a later event to place the replacement.

PENDING, ACKED, CANCEL_PENDING, UNCERTAIN and RECONCILING states block replacement exposure.

Partial fills remain active/resting; subsequent account reconciliation updates inventory and triggers a global maker reevaluation.

## Execution uncertainty

MAKE inherits BUILD-009 semantics without modification:

- fixed logical operation identity, namespaced by runtime session and exchange so restart/same-timestamp opportunities cannot collide;
- deterministic idempotency;
- exact payload identity;
- journal-before-write;
- all post-dispatch 5xx/transport-unknown outcomes are UNCERTAIN;
- UNCERTAIN exposure remains reserved;
- authoritative reconciliation clears uncertainty;
- no replacement is placed while the old economic state is unresolved.

Startup LIVE recovery first resolves BUILD-009 journal state, then reconciles authoritative account state, then reconstructs only quote state proven to belong to MAKE from journal strategy attribution.

## Central risk and kill switch

MAKE never implements private risk substitutes.

Every placement becomes a BUILD-009 `Opportunity`, is evaluated by central `evaluate_risk()`, and only an approved `RiskDecision` can become an `ExecutionPlan`.

This retains:

- max order size;
- gross exposure;
- per-market exposure;
- resting/uncertain exposure;
- open-order count;
- tournament identity;
- duplicate logical intent protection;
- account trust;
- stale runtime state;
- BUILD-009 LIVE interlocks.

MAKE adds a one-way process kill latch. Once activated it cannot be cleared within the running coordinator. Activation:

- prevents new maker placements through the same central risk context;
- converts desired maker state to no quotes;
- requests cancellation of all maker quotes on a global reevaluation.

Reset requires process reconstruction/restart.

## SHADOW / LIVE

SHADOW and LIVE use the same:

- source bridge;
- fair value;
- prediction/toxicity plugins;
- reservation value;
- spread;
- sizing;
- eligibility;
- lifecycle state machine;
- central risk;
- `ExecutionPlan`.

Only the final adapter differs.

SHADOW uses BUILD-009 `ShadowSink` and does not invent queue priority. Passive quotes remain OPEN unless observable crossing/depth justifies a simulated fill under BUILD-009's conservative rules.

LIVE requires every existing BUILD-009 interlock **plus** the service's explicit `--live` CLI flag. MAKE does not weaken those checks.

## Event-driven multi-market runtime

`MakerRuntimeLoop` is a single worker with coalescing notifications.

- SIG event -> affected exchange IDs only;
- PM token event -> pre-indexed affected SIG exchanges only;
- account/inventory update -> global reevaluation;
- health/trust transition -> global reevaluation;
- freshness deadline -> affected exchange only;
- kill switch -> global reevaluation.

Repeated updates before the worker runs collapse into one bounded set. There is no task per market and no fixed-cadence global freshness poll. The worker sleeps until either a real event, stop request or the nearest required freshness deadline.

## Observability

`MakerTrace` exposes, without persistence dependency:

- strategy ID/version;
- FV source/version;
- raw FV;
- predictive shift;
- adjusted FV;
- uncertainty/confidence;
- update hazard/adverse selection;
- signed inventory;
- reservation price;
- half-spread;
- desired bid/ask ticks;
- sizes;
- gate/reason;
- decision monotonic timestamp.

`HotPathTelemetry` remains bounded and in-memory. CAPTURE may consume cycle/trace information but quote generation does not require CAPTURE to succeed.

## Production service

Entry point:

`python -m predictions_cup.maker.service`

The service:

- refuses startup unless `PREDICTIONS_CUP_MAKER_ENABLED=true`;
- uses existing governed SIG REST/Reatime state;
- maintains account Realtime with authoritative REST recovery;
- seeds and maintains only canonical mapped Polymarket token books;
- opens no SIG capture SQLite database;
- opens the BUILD-009 execution journal only in LIVE mode;
- exits/kill-latches on critical feed/state task failure;
- handles SIGTERM/SIGINT via a stop event and kill/cancel path.

A no-network service smoke is part of CI.

## Tests inherited from BUILD-009

MAKE relies intentionally on BUILD-009 regression coverage for transport/recovery mechanics, including:

- malformed/5xx execution uncertainty;
- exact payload reuse;
- revision gaps;
- account fill invalidation;
- startup unresolved recovery;
- local reservation overlay;
- gross/open/uncertain risk;
- LIVE permit;
- rate governor/cooldown;
- conservative shadow depth.

MAKE-specific tests add fair-value mapping semantics, actual source-age preservation, exact-deadline stale cancellation without a source event, inventory skew/boundaries, plugin failure, quote materiality, two-phase replacement, placement/cancel uncertainty, restart quote reconstruction, coalescing runtime behavior, source-loss cancellation and SHADOW/LIVE adapter state.

## Performance acceptance

The benchmark harness is:

`python scripts/benchmark_make001.py`

It reports separately:

- direct-PM FV;
- null predictive/toxicity plugin overhead;
- total maker quote calculation;
- lifecycle decision;
- central risk;
- execution-plan serialization;
- durable journal pre-dispatch;
- realistic full-universe burst.

The exact committed SHA must be benchmarked on the target EC2 host. EC2 source is never edited manually; an exact Git commit is staged/checked out for measurement.

Network timing evidence is recorded separately from internal microseconds. No real economic SIG order is created for latency benchmarking.

## Current non-goals

MAKE-001 does not promote:

- ETS research;
- PRED-006 research;
- 005F learned signal values;
- fill-hazard/queue models;
- structural hedging;
- correlated portfolio-CARA;
- automatic loss/session limits without a verified P&L/account contract.

Those remain plugin/risk extensions, not reasons to change execution plumbing.
