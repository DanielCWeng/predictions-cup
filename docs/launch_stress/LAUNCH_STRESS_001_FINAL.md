# LAUNCH-STRESS-001 FINAL — EC2 West

Host: `ip-172-31-19-67.us-west-1.compute.internal` (`west-execution`).
Starting canonical test SHA: `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`.
Latest canonical SHA observed: `7e726b32649bfb8450418fefbf95260ad25ba493`.
The five files changed between those SHAs are non-runtime evidence/docs; no `src/`, `deploy/`, `scripts/`, tests, or `.env.example` file changed.

## Summary

- 207 launch-critical deterministic tests passed on the latest canonical tree across execution/recovery, RISK-002, MAKE, FULLSTACK, SIG realtime/CAPTURE, Polymarket recorder/storage/websocket/health, and SUPERVISOR.
- Actual SIGKILL destruction was performed against SIG capture, Polymarket capture, and SUPERVISOR together with LIVE disabled.
- Both capture SQLite stores survived `PRAGMA quick_check`; unresolved execution remained zero.
- SIG recovered immediately. Polymarket recovery was deliberately loud rather than falsely green and eventually restored fresh websocket evidence.
- No economic-safety invariant breach was found in the accepted synthetic execution/risk harnesses.
- West is not an operational full trading composition: MAKE systemd is not installed, deployed RISK capital control is disabled, OBSERVE/LIVE-LEARN are not running, and LIVE authorization remains NOT_READY.

## Process destruction

Pre-kill PIDs: SIG `155441`; Polymarket `148623`; SUPERVISOR `150017`.
All three were SIGKILLed together. Recovery produced new PIDs with exactly one live capture process of each type and no execution uncertainty.
SIG and Polymarket operational SQLite databases both returned `quick_check=ok`.
Do not use `systemctl is-active` or `NRestarts` alone as recovery proof; require fresh process-owned evidence and durable-store continuity.

## Feed / stale-truth destruction

After the kill, PM could be process-active while its evidence aged. The canonical status eventually classified the feed stale and SUPERVISOR held rather than advertising false health.
PM cold recovery is materially slower than SIG because recorder initialization performs Gamma/universe discovery before websocket health is fresh; observed outage-to-fresh recovery was roughly 11.5 minutes.
The important invariant held: the gap was loud and bounded, not silently bridged.
Current SIG capture has a composition-specific `FEED_SIG_REST_PROGRESS_STALE` HOLD: CAPTURE performs initialization REST/bulk refreshes but periodic all-universe refresh ownership belongs to MAKE.
Do not clear this HOLD cosmetically by restarting SIG; that only resets freshness temporarily without fixing ownership.

## Execution lifecycle and RISK

Focused lifecycle coverage passed for duplicate/reordered evidence, partial/late fills, cancellation races, placement ambiguity, UNCERTAIN persistence, authoritative reconciliation, restart recovery and idempotency.
RISK coverage passed for stale/untrusted state rejection, unresolved exposure reservation, gross/per-market/open-order/advanced caps, session-loss and peak-drawdown halts, durable halt persistence and explicit reset semantics.
No real order was sent. West has no unresolved execution journal state.
Deployment differs from the harness: `PREDICTIONS_CUP_TRADING_ENABLED=false`, deployed capital control is not armed, and LIVE remains unauthorized. Harness-proven RISK must not be confused with deployed production admission.

## Full composition

Authenticated SIG tournament metadata confirmed tournament ID `bda92870-621e-47b0-bc3c-3602c5c26f55` maps to slug `midterm-elections`, scheduled to start at `2026-10-01T16:00:00Z`.
That verified slug was added to West runtime configuration.
With temporary non-persisted safe overrides enabling MAKE/SHADOW/LIVE-LEARN and disabling the config kill switch, canonical `cupctl check maker-shadow` returned `READY`; SIG and Polymarket component checks were also `READY`.
The accepted FULLSTACK systemd maker/status/target units are not installed on West. The remote execution safety layer rejected privileged installation; no privilege bypass, ad-hoc daemon, or parallel user-service workaround was attempted.
The temporary safe-shadow overrides were rolled back after the installer was blocked. West remains capture-only with the global kill switch active and LIVE disabled.

## Lessons

1. Freshness and process ownership are stronger health evidence than `systemctl is-active`.
2. Recovery cooldowns must account for normal component cold-start RTO; PM discovery can legitimately outlast generic remediation timers.
3. A connected websocket flag is not enough: post-restart event freshness must advance before recovery is accepted.
4. Component ownership matters. A capture-only SIG process cannot permanently satisfy a periodic REST-progress invariant owned by MAKE.
5. Harness-proven economic safety is not equivalent to deployed/armed RISK.
6. Operator tooling must use the production runtime HOME explicitly; a different remote-shell HOME can make canonical state appear missing.
7. When the canonical composition requires privileged installation, fail closed rather than recreating it in tmux/user-systemd or weakening controls.
8. Never manufacture a green launch gate by repeatedly restarting a component whose underlying ownership/configuration is wrong.

## Remaining risks

- Full MAKE/SHADOW/OBSERVE/LIVE-LEARN composition is not installed/running on West.
- RISK capital control and explicit economic hard limits are not deployed/armed on West; this is safe only because LIVE remains disabled.
- SUPERVISOR correctly remains HOLD on stale SIG REST progress under the capture-only composition.
- No real-order canary was performed.
- PM cold recovery is slow enough that additional deliberate destruction close to freeze is not justified.

## FREEZE VERDICT

Proven operational: read-only SIG capture, read-only Polymarket capture including loud stale detection and recovery, SUPERVISOR fail-closed behavior, durable capture stores, clock/storage health, and deterministic execution/RISK invariants.

Not proven operational on West: full MAKE/SHADOW composition, deployed RISK capital admission, OBSERVE, LIVE-LEARN, LIVE execution, or a real-order canary.

GO only for the currently enabled capture capability. Full trading/LIVE remains NO-GO until the accepted FULLSTACK units are installed, safe composition is started, RISK is explicitly armed/reconciled with real configured limits, and the intended Supervisor gate is genuinely PASS.

## Quiet burn-in / final clean-state evidence

A ten-sample burn-in ran from `15:15:56Z` through `15:20:32Z` with no destructive actions.
Across all samples: SIG freshness remained below 1 second; PM freshness remained below 6 seconds; PM websocket stayed connected; unresolved execution count stayed zero; global halt stayed active; both capture services stayed active; storage stayed HEALTHY.
SIG service memory remained approximately 71–72 MB. PM service memory declined from approximately 656 MB to 640 MB, which is inconsistent with a continuing post-restart leak during this window.
`cupctl rehearse` returned `SIMULATION_PASS`: component imports, mapping availability, SHADOW-safe configuration, status aggregation, systemd verification and installer shell syntax all passed, and `economic_order_sent=false`.
Systemd verification emitted a warning on the separate `rdc-west.service` unit (`StartLimitIntervalSec` placed under `[Service]`); no Predictions Cup runtime unit failed verification.
At the end of burn-in the running capture PIDs remained SIG `159450` and Polymarket `159456`; SUPERVISOR remained `158379`.
