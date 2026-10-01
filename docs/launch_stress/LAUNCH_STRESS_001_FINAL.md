# LAUNCH-STRESS-001 FINAL — EC2 West

Host: `ip-172-31-19-67.us-west-1.compute.internal` (`west-execution`).
Starting canonical test SHA: `662969df4e481f1cbd9417c923ae35d3a0aa9f9f`.
Final canonical SHA observed: `7e726b32649bfb8450418fefbf95260ad25ba493`.
The intervening commits touched research evidence/docs only; no runtime files changed.

## Summary

- 209 focused deterministic tests passed across execution/recovery/risk, FULLSTACK/MAKE/SIG/Polymarket, and SUPERVISOR/CAPTURE.
- Actual SIGKILL destruction was performed against SIG capture, Polymarket capture, and SUPERVISOR together with LIVE disabled and global kill active.
- Both capture SQLite stores survived `PRAGMA quick_check`; SIG recovered fresh; unresolved execution remained zero.
- Stale truth failed closed: PM stayed process-active but stale after restart, canonical status became STALE, and SUPERVISOR emitted CRITICAL HOLD rather than false GREEN.
- PM eventually recovered fresh websocket evidence. No silent bridge of the outage was observed.
- No economic-safety invariant breach was found in the accepted synthetic lifecycle/risk harnesses.
- Full trading composition is not launch-operational on West: MAKE is disabled/not installed, RISK capital control is disabled with hard limits unset, OBSERVE/LIVE-LEARN are not running, and LIVE authorization is NOT_READY.

## Process and feed destruction

Pre-kill PIDs: SIG 155441; Polymarket 148623; SUPERVISOR 150017. All three were SIGKILLed together.
Recovered service PIDs changed, stores remained readable, and no execution uncertainty appeared.
`NRestarts` alone is not sufficient recovery evidence because SUPERVISOR-triggered `systemctl restart` can change PID while that counter remains zero; use PID identity plus fresh process-owned evidence and store continuity.

PM cold recovery is slow because recorder initialization performs full Gamma keyset discovery before websocket and health loops start.
Original service start took about 8m46s to first healthy websocket evidence.
During this run: destruction at 14:46:54 UTC; first SUPERVISOR restart at 14:46:58; bounded second automatic restart at 14:51:59; fresh PM evidence at 14:58:26.
Observed outage-to-fresh recovery was about 11m32s. The outage was loud: SUPERVISOR held throughout stale evidence.
SUPERVISOR's generic 300s restart cooldown is shorter than PM's normal cold discovery RTO. The 2/hour budget prevents an infinite livelock, but recovery churn is avoidable.

## Execution and risk torture

LAUNCH-HARDENING-002 / BUILD-009 focused tests passed for composed restart/reconciliation, duplicate/reordered evidence, delayed fills, placement uncertainty, authoritative recovery, and conservative unresolved exposure.
No real orders were sent. Execution journal remained absent on the capture-only host and unresolved operation count stayed zero.

Risk harness tests passed for exact cap boundaries, stale/untrusted capital rejection, session-loss/drawdown halts, halt persistence, unresolved exposure reservation, advanced exposure caps, and reconciliation.
Deployment state differs: West is SHADOW with `trading_enabled=false`, `global_kill_switch=true`, `risk_capital_control_enabled=false`, and economic hard limits unset.
Therefore RISK-002 is proven in the accepted harness but is not armed as a production admission layer on this host.

## Full composition

`scripts/cupctl rehearse` returned `SIMULATION_PASS` and explicitly sent no economic order.
`scripts/cupctl start-shadow` failed closed with `{"state":"BLOCKED","reason":"maker_disabled"}` before touching systemd.
West currently has capture and SUPERVISOR services installed, but the accepted FULLSTACK-002 maker/status/runtime target is not deployed as an operational composition.

SIG has a composition-specific HOLD: CAPTURE intentionally disables periodic all-universe bulk-price refresh because MAKE owns that work.
With MAKE disabled, SUPERVISOR eventually raises `FEED_SIG_REST_PROGRESS_STALE`; restarting SIG only refreshes once during initialization and cannot permanently clear the condition.
This is a deployment/composition mismatch, not evidence that the SIG socket is dead.

## Final clean state observed at 15:04:01 UTC

- Git: exact `origin/main` at `7e726b32649bfb8450418fefbf95260ad25ba493`, clean.
- SIG capture: ACTIVE/RUNNING; canonical freshness HEALTHY.
- Polymarket capture: ACTIVE/RUNNING; websocket connected; canonical freshness HEALTHY; storage failures zero.
- SUPERVISOR: ACTIVE/RUNNING; launch gate HOLD because intended full composition/SIG periodic bulk-price progress is not satisfied.
- MAKE: inactive / not installed by current host composition.
- RISK: deployment state DISABLED; global kill active.
- Execution: unresolved count zero; no local execution journal.
- Clock: NTP synchronized.
- Disk: 30G volume, about 18G free, 42% used.
- Mapping: `sig_polymarket_2026.json`, hash `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`.

## Lessons

1. Never use `systemctl is-active` as proof of feed truth; freshness and process ownership are the real gate.
2. Remediation cooldown must account for normal cold-start RTO or become startup-aware.
3. A generic freshness view can be greener than the launch gate; SUPERVISOR's deeper recovery semantics should remain authoritative.
4. Component ownership matters: CAPTURE-only SIG cannot satisfy a health invariant owned by MAKE.
5. Harness-proven RISK is not the same as deployed armed RISK.
6. Operator tooling should be independent of production capture; the remote-control daemon briefly disconnected and recovered while capture continued.

## FREEZE VERDICT

Proven operational: read-only SIG capture; read-only Polymarket capture including loud stale detection and eventual recovery; SUPERVISOR fail-closed detection; clock/storage; synthetic execution and RISK invariants.

Disabled or not operational: MAKE/SHADOW composition, deployed RISK capital control/hard limits, OBSERVE, LIVE-LEARN, LIVE execution, and real-order canary.

**GO only for the currently enabled read-only capture capability. NO-GO for full trading composition or LIVE authorization until MAKE/FULLSTACK is deployed, RISK is armed with explicit hard limits and reconciled state, and SUPERVISOR returns PASS on the intended composition.**
