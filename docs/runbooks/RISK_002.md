# RISK-002 Operator Runbook

## Purpose

RISK-002 is the persistent capital-control layer attached to BUILD-009 central Risk. Use this
runbook for configuration, startup, incident halt, restart/reconciliation, and explicit reset.

RISK-002 never makes a LIVE order by itself.

## Before enabling

Keep execution in SHADOW until all required risk inputs are configured and the full CI/rehearsal
battery is green.

Production LIVE requires all existing BUILD-009 gates plus RISK-002:

- explicit LIVE execution mode;
- trading enabled;
- explicit trade credential;
- explicit tournament ID and slug;
- complete base risk caps;
- `PREDICTIONS_CUP_RISK_CAPITAL_CONTROL_ENABLED=true`;
- an explicit positive session-loss limit;
- an explicit positive drawdown limit;
- trusted account state;
- reconciled/trusted RISK-002 capital state before fresh economic admission;
- global configuration kill switch disabled;
- explicit `--live` invocation.

The loss/drawdown values are operator choices; production does not invent defaults. SHADOW and
research operation may remain RISK-002-disabled.

## Core configuration

The durable state path defaults to:

```text
PREDICTIONS_CUP_RISK_STATE_PATH=data/risk_002.sqlite3
```

Enable capital control explicitly:

```text
PREDICTIONS_CUP_RISK_CAPITAL_CONTROL_ENABLED=true
```

Set a stable profile identity:

```text
PREDICTIONS_CUP_RISK_PROFILE_NAME=competition
PREDICTIONS_CUP_RISK_PROFILE_VERSION=risk-002-v1
PREDICTIONS_CUP_RISK_PROFILE_MODE=STANDARD
```

Do not casually change the profile version while reusing an existing state file. A durable profile
mismatch blocks startup.

## Required base caps

Example only; choose actual launch limits deliberately:

```text
PREDICTIONS_CUP_RISK_MAX_ORDER_SIZE=10
PREDICTIONS_CUP_RISK_MAX_GROSS_EXPOSURE=100
PREDICTIONS_CUP_RISK_MAX_PER_MARKET_EXPOSURE=25
PREDICTIONS_CUP_RISK_MAX_OPEN_ORDER_EXPOSURE=50
PREDICTIONS_CUP_RISK_MAX_CONCURRENT_OPEN_ORDERS=10
```

Advanced caps:

```text
PREDICTIONS_CUP_RISK_MAX_PER_STRATEGY_EXPOSURE=25
PREDICTIONS_CUP_RISK_MAX_EVENT_GROUP_EXPOSURE=50
PREDICTIONS_CUP_RISK_MAX_TOURNAMENT_EXPOSURE=100
PREDICTIONS_CUP_RISK_SESSION_LOSS_LIMIT=20
PREDICTIONS_CUP_RISK_DRAWDOWN_LIMIT=15
```

Freshness:

```text
PREDICTIONS_CUP_RISK_MAX_MARK_AGE_MS=12000
PREDICTIONS_CUP_RISK_MAX_ACCOUNT_AGE_MS=2000
```

MAKE composition currently uses account-stream trust/reconciliation boundaries for account
continuity and uses the explicit mark-age contract for hot-path revaluation.

## Correlated event groups

If `RISK_MAX_EVENT_GROUP_EXPOSURE` is set, also configure:

```text
PREDICTIONS_CUP_RISK_EXPOSURE_GROUPS_PATH=/absolute/or/runtime/path/risk_groups.json
```

Document shape:

```json
{
  "version": "cup-2026-v1",
  "memberships": [
    {
      "market_id": "market-id",
      "tournament_id": "tournament-id",
      "group_ids": ["event:example"]
    }
  ]
}
```

Every currently exposed market and every proposed market must be classified while the group cap is
enabled. Missing classification blocks new exposure.

Do not use this file to encode a guessed statistical correlation matrix. It is an explicit
relationship/group cap only.

## Exploratory profile

Exploratory mode is intentionally bounded:

```text
PREDICTIONS_CUP_RISK_PROFILE_MODE=EXPLORATORY
PREDICTIONS_CUP_RISK_EXPLORATORY_MAX_ORDER_SIZE=...
PREDICTIONS_CUP_RISK_EXPLORATORY_MAX_GROSS_EXPOSURE=...
PREDICTIONS_CUP_RISK_EXPLORATORY_MAX_PER_MARKET_EXPOSURE=...
PREDICTIONS_CUP_RISK_EXPLORATORY_MAX_OPEN_ORDER_EXPOSURE=...
PREDICTIONS_CUP_RISK_EXPLORATORY_MAX_CONCURRENT_OPEN_ORDERS=...
```

Optional exploratory strategy/group/tournament caps are also available.

The normal/global cap set is still evaluated. Exploratory configuration never overrides the hard
global caps or hard loss/drawdown limits.

## Startup sequence

On a fresh session or restart the runtime:

1. loads durable RISK-002 identity, including any latched halt;
2. revokes loaded account/mark/reconciliation trust;
3. issues recovery-only LIVE authority, which cannot send an ordinary fresh placement;
4. reconciles BUILD-009 unresolved execution, including safe cancellation/reconciliation and only
   exact durable idempotent placement replay where BUILD-009 recovery requires it;
5. fetches authoritative tournament account state;
6. establishes/reconciles the stable transaction/P&L baseline;
7. reconstructs surviving FIFO cost basis;
8. reconciles strategy attribution if its cap is enabled;
9. validates event-group classification if its cap is enabled;
10. validates authoritative P&L, exposure and mark state;
11. publishes reconciled capital state;
12. if a global or matching strategy halt is active, keeps it latched and withdraws resting MAKE
    quotes through the existing cancellation path;
13. otherwise issues fresh-admission LIVE authority and permits new economic exposure.

On restart the saved session start equity, peak equity, halts and cursor are reused. A durable halt
never prevents authoritative recovery/cancellation and never auto-clears because recovery succeeds.

## Inspect state

Read-only inspection may be performed while the service is running:

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  status
```

Or specify a state path explicitly:

```bash
python scripts/risk002_control.py \
  --state-path data/risk_002.sqlite3 \
  status
```

Inspect:

- `session_start_equity`;
- `session_start_unrealised_pnl`;
- `current_equity`;
- `realised_pnl`;
- `unrealised_pnl`;
- `peak_session_equity`;
- `drawdown`;
- `global_halt`;
- `strategy_halts`;
- trust/reconciliation flags;
- limit profile version;
- transaction cursor.

## Emergency global halt

If the process itself is healthy and already running LIVE, use the existing service/BUILD-009 kill
mechanism to stop new exposure and cancel quotes.

For a durable offline RISK-002 capital halt:

1. stop the trading service;
2. verify it is stopped;
3. run:

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  halt-global \
  --reason operator_emergency \
  --ack-service-stopped
```

4. restart only after investigating and reconciling the account.

The halt persists across restart. Restart is still allowed to perform authoritative recovery and
cancel existing risk. For MAKE, an active global halt force-cancels resting quotes on the next
risk-aware lifecycle cycle. If a cancellation outcome is UNCERTAIN, that order remains risk-bearing
until authoritative reconciliation proves it is gone.

## Halt one strategy or family

Stop the trading service before mutating durable state.

Strategy ID:

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  halt-strategy \
  --scope id \
  --value make-direct-pm \
  --reason adverse_selection \
  --ack-service-stopped
```

Strategy family:

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  halt-strategy \
  --scope family \
  --value MAKE \
  --reason operator_review \
  --ack-service-stopped
```

Capture and unrelated strategies are not halted by a strategy-scoped RISK-002 halt. A matching
`make-direct-pm` strategy-ID halt or `MAKE` family halt force-cancels MAKE's resting quotes through
the normal BUILD-009 cancellation machinery; an unrelated strategy halt leaves MAKE quotes alone.

## Reset global halt

A global hard halt never auto-clears because a feed reconnects or a mark recovers.

Reset procedure:

1. stop the trading service;
2. inspect the durable state and identify the exact active reason;
3. reconcile the execution journal and authoritative account state;
4. confirm the loss/drawdown condition is understood;
5. reset with the exact expected reason and operator identity:

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  reset-global \
  --expected-reason peak_drawdown_limit \
  --operator daniel \
  --ack-service-stopped
```

If the stored reason changed, the command refuses the stale reset.

6. restart the service; restart still performs fresh execution/account/P&L/mark reconciliation before
new exposure is possible.

## Reset strategy halt

```bash
python scripts/risk002_control.py \
  --runtime-env-only \
  reset-strategy \
  --scope id \
  --value make-direct-pm \
  --operator daniel \
  --ack-service-stopped
```

Use `--scope family` for a family halt.

## Failure interpretation

`capital_state_missing`:
capital control is required but no state was published.

`risk_state_unreconciled`:
startup/recovery has not completed; do not bypass it.

`risk_state_untrusted`:
account/exposure state lost trust.

`risk_marks_untrusted` or `risk_mark_state_stale`:
required valuation state is absent/stale/untrusted.

`strategy_exposure_untrusted`:
journal/fill/account attribution is incomplete. Reconcile; do not guess ownership.

`exposure_group_state_untrusted` / `exposure_group_unclassified`:
the configured group document does not cover required exposure.

`global_capital_halt`:
a persistent capital halt is active.

`strategy_halt`:
the current strategy ID/family is disabled.

`session_loss_limit` or `peak_drawdown_limit`:
the hard loss control is at/beyond its boundary.

`execution_state_unresolved`:
BUILD-009 has PENDING/CANCEL_PENDING/UNCERTAIN/RECONCILING economic state. Resolve through
authoritative recovery; never delete the journal to make the warning disappear.

## Transaction cursor failure

If startup reports that the durable transaction cursor cannot be found or transaction coverage is
incomplete:

- keep LIVE disabled;
- preserve both execution and risk databases;
- verify the SIG endpoint/coverage;
- do not create a new RISK-002 session merely to suppress the error unless you deliberately intend
  to reset the session P&L baseline.

## Strategy attribution failure

If per-strategy caps are enabled and attribution is incomplete:

- inspect BUILD-009 journal ACK/SUBMISSION identity;
- inspect authoritative tournament fills and open orders;
- resolve orphan/unknown economic exposure;
- rerun authoritative reconciliation.

Do not assign unknown exposure to a convenient strategy merely to restore trading.

## Mark failure

RISK-002 does not use a neutral 0.50 fallback.

When required marks become stale/untrusted:

- new exposure is blocked;
- existing capture continues;
- authoritative account reconciliation and/or the existing SIG realtime/bulk-price path must restore
  a trusted mark.

## Restart implications

No schema migration service is required. RISK-002 creates its small SQLite tables if absent.

The state file must survive service restart. Deleting it starts a new risk session baseline and is
therefore an economic/safety action, not routine cleanup.

If the limit profile version changes, use an explicit reviewed migration/session-reset procedure
rather than overwriting the old version in place.

## Validation commands

Repository CI includes the full suite and RISK-002 benchmark.

Focused local validation:

```bash
ruff check src/predictions_cup/risk tests/test_risk002_capital_control.py \
  scripts/benchmark_risk002.py scripts/risk002_control.py
mypy
pytest -q tests/test_risk002_capital_control.py tests/test_make001.py \
  tests/test_build009_core.py tests/test_build009_recovery.py tests/test_build009_live_sink.py
python scripts/benchmark_risk002.py --iterations 20000
```

## Order safety

Tests, benchmarks, status inspection and halt/reset commands do not place SIG orders.

**REAL SIG ORDERS SENT: NO**
