# RISK-002 — Capital Control / Loss Containment

## Status

Branch: `build/risk-002-capital-control`

Base SHA: `0b91e5c4e8ed461e7e5e8fcc880a094029f58891`

RISK-002 extends the existing BUILD-009 central Risk boundary. It does not create a second
order manager, execution engine, or strategy-specific risk path.

Authoritative path remains:

```text
observable state
-> strategy / MAKE
-> Opportunity
-> BUILD-009 central evaluate_risk()
-> RiskDecision
-> ExecutionPlan
-> SHADOW / LIVE sink
```

RISK-002 enriches the immutable `RiskContext` consumed by `evaluate_risk()`.

## Safety invariant

A strategy may be malicious, stale, duplicated, or simply wrong. It still cannot acquire new
economic exposure when any required capital fact is absent, stale, contradictory, unresolved, or
over limit.

The central evaluator performs no HTTP, SQLite, filesystem, systemd, Polymarket, or strategy I/O.
Durability and authoritative reconciliation happen in the I/O shell. The normal synchronous
approval/rejection path is in-memory only.

## Main modules

- `src/predictions_cup/risk/core.py` — BUILD-009 central evaluator plus RISK-002 limits/profiles.
- `src/predictions_cup/risk/capital.py` — immutable P&L/exposure/halt contracts and pure accounting.
- `src/predictions_cup/risk/service.py` — small state/reconciliation coordinator and hot-path
  `RiskContextSource`.
- `src/predictions_cup/risk/store.py` — SQLite/WAL, `synchronous=FULL`, one durable state row plus
  append-only risk events.
- `src/predictions_cup/risk/sig.py` — SIG normalization, transaction-cursor cash-flow scan,
  FIFO-lot cost-basis reconstruction, mark adapters.
- `src/predictions_cup/risk/attribution.py` — BUILD-009 journal + authoritative fill attribution by
  strategy ID.
- `src/predictions_cup/risk/groups.py` — explicit versioned market-to-event grouping provider.
- `scripts/risk002_control.py` — explicit offline operator halt/reset surface.
- `scripts/benchmark_risk002.py` — 237-market central-risk benchmark.

## Authoritative SIG semantics

The implementation is anchored to the accepted `api-1.json` artifact used by BUILD-009
(SHA-256 `8825112d9413f3b7361773800400704078968c412c47526b251e8b57e982448a`).

RISK-002 uses the explicit tournament surfaces:

- `GET /tournaments/{slug}/portfolio/positions`;
- `GET /tournaments/{slug}/portfolio/pnl?period=all`;
- `GET /tournaments/{slug}/portfolio/transactions`;
- `GET /portfolio/fills?tournamentId=...`;
- BUILD-009 open-order reconciliation.

Important semantics used directly:

- tournament position `quantity` is signed: positive YES, negative NO;
- tournament `currentPrice` is the tournament valuation price;
- nonzero tournament holdings without valuation fail at the API rather than receiving a neutral
  invented price;
- `totalAccountValue` is the authoritative tournament account-equity input;
- fill quantities are outcome-signed;
- position rows expose FIFO lots and authoritative cost basis.

No missing action, price, quantity, mark, or account field is silently synthesized.

## P&L accounting

### Session identity

A RISK-002 session persists:

- `session_id`;
- session start equity;
- session-start unrealised P&L;
- current equity;
- peak session equity;
- realised P&L;
- unrealised P&L;
- drawdown;
- external cash-flow accumulator;
- transaction cursor.

A fresh session establishes a stable fence around the tournament P&L read by reading the newest
transaction identity before and after the P&L snapshot. A restart loads the existing durable
session; it does not replace the saved start equity with a new baseline.

### External cash flows

Tournament transaction history is walked newest-first until the durable transaction cursor is
found. Deposit events are separated from trading P&L. Incomplete transaction coverage or a missing
durable cursor fails reconciliation.

Economic session equity is:

```text
risk_equity = authoritative_total_account_value - net_external_cash_flow
session_pnl = risk_equity - session_start_equity
```

### Realised and unrealised P&L

Authoritative tournament P&L supplies current unrealised P&L. RISK-002 also persists the
session-start unrealised P&L so starting with pre-existing inventory does not misclassify old
mark-to-market gains/losses as new session P&L.

Session realised P&L is the residual identity:

```text
unrealised_change = current_unrealised_pnl - session_start_unrealised_pnl
realised_pnl = session_pnl - unrealised_change
```

This is accepted only when:

```text
realised_pnl + unrealised_change == session_pnl
```

within the configured accounting tolerance.

### Cost basis

Quantity is authoritative from the SIG position row.

Surviving cost basis is reconstructed locally from the authoritative FIFO lots. For each nonzero
position RISK-002 verifies:

- every lot has the same side as the signed position;
- summed lot quantity equals absolute authoritative position quantity;
- summed lot cash cost equals authoritative position `costBasis`;
- NO-side lot entry prices are converted into YES-denominated entry values only for mark-to-market
  math.

The locally reconstructed position retains:

- authoritative signed quantity;
- YES-denominated average entry used for valuation;
- cash cost basis used for reconciliation.

Contradictory lot/position state fails closed.

### Valuation

`RiskMark` retains:

- exchange ID and market ID;
- price;
- source;
- monotonic observation time;
- trust;
- version;
- method.

The production hot-path provider is `SigRealtimeRiskMarkProvider`, which reads the existing SIG
realtime/bulk-price state. The reconciled REST position price is the baseline. Realtime marks
revalue only the open inventory between authoritative account reconciliations.

For signed quantity `q`, YES-denominated entry `p0`, and current YES mark `p`:

```text
unrealised_position_pnl = q * (p - p0)
```

This works for NO inventory because NO quantity is negative and its entry is converted to the
equivalent YES price.

A missing, untrusted, or stale required mark makes mark state untrusted; no new LIVE exposure is
approved.

## Exposure accounting

The conservative settlement bound remains one currency unit per share, preserving BUILD-009
semantics.

`RiskExposureSnapshot` tracks:

- gross exposure;
- net signed directional quantity;
- open-order exposure;
- UNCERTAIN-order exposure;
- per-market exposure;
- per-strategy exposure;
- per-tournament exposure;
- explicit event/group exposure.

Open and UNCERTAIN orders remain risk-bearing until authoritative reconciliation proves they are
gone.

### Strategy attribution

Per-strategy exposure is reconstructed from:

```text
BUILD-009 SUBMISSION/ACK identity
+ exchange order ID
+ authoritative tournament fills
+ authoritative current positions/open orders
```

Fill quantity is normalized with its absolute quantity because SIG fill quantity is outcome-signed;
direction comes from the canonical journaled side/action. If current authoritative position or open
order exposure cannot be completely attributed to journal strategy IDs, the strategy exposure state
is untrusted and a configured per-strategy cap fails closed.

### Correlated-event groups

No statistical correlation is estimated.

`ExposureGroupProvider` is replaceable. The supplied JSON/static implementation maps
`(market_id, tournament_id)` to one or more configured group IDs. The generic evaluator has no US
election topology embedded in it.

If an event-group cap is enabled:

- all currently exposed markets must be classified;
- every proposed market must be classified;
- aggregate group exposure must remain at or below the cap.

## Limit profiles

`RiskLimits` supports:

- maximum order size;
- maximum per-market exposure;
- maximum gross exposure;
- maximum open-order exposure;
- maximum concurrent open/UNCERTAIN orders;
- maximum per-strategy exposure;
- maximum event/group exposure;
- maximum tournament exposure;
- session loss limit;
- peak-to-current drawdown limit.

Exact boundary is allowed. One unit beyond is rejected.

### LIVE admission

Production LIVE admission requires all of the following in addition to the existing BUILD-009
interlocks:

- `risk_capital_control_enabled=true`;
- an explicit positive session-loss limit;
- an explicit positive drawdown limit;
- a reconciled/trusted capital state before fresh economic exposure.

The loss/drawdown values have no invented production defaults; the operator must choose them.
SHADOW/research operation does not require RISK-002. Optional strategy/event-group/tournament caps
remain optional.

Startup uses a recovery-only LIVE capability before fresh admission exists. That capability may
perform authoritative recovery and cancellation, plus BUILD-009's exact durable idempotent recovery
replay, but `SigLiveSink.dispatch()` rejects any ordinary fresh placement under it.

### Exploratory mode

`RiskProfile(mode=EXPLORATORY)` contains both:

- an exploratory limit set;
- a mandatory hard/global limit set.

The evaluator runs both. Exploratory limits therefore cannot disable or exceed global safety:
whichever limit is tighter rejects first.

## Strategy halt

Durable `HaltState` supports:

- `STRATEGY_ID`;
- `STRATEGY_FAMILY`.

A matching strategy/family receives `strategy_halt`. Capture and unrelated candidates continue.

Operator halt/reset updates are append-only evidence in the RISK-002 store. Reset requires an
explicit operator identity.

For MAKE, a matching strategy-ID or `MAKE` family halt is also a quote-lifecycle force-cancel
condition. Existing resting quotes are withdrawn through the existing MAKE -> BUILD-009
cancellation machinery before any placement decision. An unrelated strategy halt does not cancel
MAKE. If a cancellation becomes UNCERTAIN, the quote/order remains risk-bearing until authoritative
reconciliation proves it is gone.

## Global halt

There is one persistent RISK-002 capital halt in `CapitalRiskState.global_halt`.

It trips on hard session-loss or drawdown breach and is latched. A feed reconnect, fresh mark, or
successful reconciliation does not clear it.

The hot-path `RiskContextSource` checkpoints the first hard halt synchronously before returning the
halted state. For MAKE, an active global capital halt is also a quote-lifecycle force-cancel
condition, so resting economic quotes are withdrawn through the existing cancellation path.

Restart loads the halt but **does not let it block authoritative recovery**. BUILD-009 may reconcile,
cancel existing orders, and, where necessary, replay only an exactly matching durable unresolved
placement through its existing idempotent `dispatch_recovery()` authority. The halt remains latched
through that process. Ordinary fresh placement remains impossible until reconciliation is trusted
and the halt has been explicitly reset.

A reset requires:

- the expected current halt reason;
- an explicit operator identity;
- a deliberate offline mutation;
- process restart and fresh reconciliation.

BUILD-009's pre-existing configuration kill switch remains an additional independent LIVE gate; it
is not a second P&L engine.

## Persistence

`SqliteRiskStateStore` uses the existing lightweight durability style:

- SQLite/WAL;
- `synchronous=FULL`;
- one singleton current-state row;
- append-only `risk_events`;
- serialized session, P&L, peak, exposure trust, halt state, profile version and transaction cursor.

There is no new database service.

## Startup / restart ordering

Production composition follows:

```text
load durable RISK-002 state (including any latched halt)
-> mark loaded account/marks/reconciliation untrusted
-> issue recovery-only LIVE authority (no fresh placement)
-> BUILD-009 recover unresolved execution journal / safe cancels
-> authoritative tournament orders/positions reconciliation
-> stable transaction/P&L fence
-> reconcile external cash flow
-> reconstruct local FIFO cost basis
-> reconcile strategy attribution (when configured)
-> validate explicit event groups (when configured)
-> validate P&L, marks and exposure
-> publish reconciled RiskContextSource
-> if a relevant halt is active: keep it latched and force-cancel resting MAKE quotes
-> otherwise issue fresh-admission LIVE authority
-> allow new economic exposure
```

Any unresolved BUILD-009 PENDING/CANCEL_PENDING/UNCERTAIN/RECONCILING operation blocks capital
reconciliation.

## Hot path

`evaluate_risk()` contains no DB/network I/O.

The normal path consumes immutable in-memory snapshots and `CapitalRiskState`. The realtime mark
provider also reads existing in-memory SIG state. Only the first hard halt performs a synchronous
durable checkpoint as a safety exception before the halted decision state is exposed.

## Failure modes

Fail-closed reasons include, among others:

- account state untrusted;
- capital state missing/unreconciled/untrusted;
- stale risk account state when an age contract is configured;
- stale/untrusted/missing mark;
- unresolved execution state;
- journal/account position disagreement;
- FIFO lot quantity/cost disagreement;
- strategy attribution incomplete;
- event-group classification incomplete;
- strategy halt;
- global capital halt;
- session loss breach;
- drawdown breach;
- any configured exposure cap breach.

## Tests

Primary hostile suite:

`tests/test_risk002_capital_control.py`

Coverage includes:

- exact boundary and one-unit-beyond order/gross/market/open-order/count caps;
- positions + open + UNCERTAIN exposure;
- multiple strategies individually safe but global gross unsafe;
- related markets individually safe but event-group unsafe;
- stale account and stale mark;
- lost trust;
- partial/duplicate fill replay;
- signed NO fill attribution;
- deterministic FIFO lot cost-basis reconstruction;
- journal/account disagreement;
- unresolved execution state;
- realised session loss and peak drawdown;
- strategy halt and global halt;
- global/matching strategy halt force-cancels resting MAKE bid/ask quotes;
- unrelated strategy halt leaves MAKE unaffected;
- UNCERTAIN cancellation remains risk-bearing;
- exploratory profile cannot bypass hard limits;
- persistent halt restart/reset while authoritative recovery remains available;
- recovery-only LIVE authority can cancel/reconcile but cannot admit a fresh placement;
- LIVE configuration rejects disabled RISK-002, missing loss/drawdown limits, and unreconciled capital state;
- transaction-cursor external cash-flow reconciliation;
- realtime drawdown latch/checkpoint.

Existing BUILD-009/MAKE/SHADOW tests remain in the normal CI battery.

## Benchmark

`scripts/benchmark_risk002.py` measures:

- normal approval;
- rejection;
- a 237-market portfolio;
- multiple strategies;
- multiple event groups and tournament cap checks.

The benchmark is calculation-only; it performs no DB or network I/O.

## Scope exclusions

RISK-002 does not:

- create alpha;
- modify PRED-006 or EXPERIMENT-005F;
- change MAKE quote math;
- optimize the portfolio;
- create a second order manager;
- redesign BUILD-009 execution;
- estimate statistical correlation;
- send an order merely to test Risk.

## Live-order statement

**REAL SIG ORDERS SENT: NO**
