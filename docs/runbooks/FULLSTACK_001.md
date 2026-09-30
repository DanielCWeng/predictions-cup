# FULLSTACK-001 Operator Runbook

The standard FULLSTACK installation is a production-shaped SHADOW rehearsal. It does not authorize
real SIG orders.

## 1. Preflight

~~~bash
git status --short --branch
git rev-parse HEAD
stat -c '%U %a %n' ~/.config/predictions-cup ~/.config/predictions-cup/runtime.env
~~~

Require config directory mode 700, runtime.env mode 600, runtime-user ownership, no SIG trade
credential in runtime.env, TRADING_ENABLED=false and EXECUTION_MODE=SHADOW.

Never copy rendered systemd units from another VM. Re-run the installer on the destination host.

## 2. runtime.env

Minimum launch-rehearsal shape:

~~~text
PREDICTIONS_CUP_ENVIRONMENT=production
PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<secret>
PREDICTIONS_CUP_TOURNAMENT_ID=<uuid>
PREDICTIONS_CUP_TOURNAMENT_SLUG=<slug>
PREDICTIONS_CUP_TRADING_ENABLED=false
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=true

PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH=data/sig_realtime.sqlite3
PREDICTIONS_CUP_SIG_RESEARCH_PATH=data/sig_research

PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true
PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH=data/polymarket_operational.sqlite3
PREDICTIONS_CUP_POLYMARKET_RESEARCH_PATH=data/polymarket_research
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<accepted IDs>

PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_SHADOW_ENABLED=true
PREDICTIONS_CUP_MAKER_MAPPING_PATH=data/mappings/sig_polymarket_2026.json
PREDICTIONS_CUP_SHADOW_JOURNAL_PATH=data/live/shadow_002/events.jsonl

PREDICTIONS_CUP_FULLSTACK_PROFILE=REHEARSAL
PREDICTIONS_CUP_FULLSTACK_CONFIG_SCHEMA_VERSION=fullstack-001-v1
PREDICTIONS_CUP_FULLSTACK_STATUS_DIR=data/runtime/status
PREDICTIONS_CUP_FULLSTACK_SNAPSHOT_PATH=data/runtime/launch_snapshot.json
PREDICTIONS_CUP_FULLSTACK_EVIDENCE_ROOT=data/runtime/rehearsals
PREDICTIONS_CUP_FULLSTACK_MAX_FEED_AGE_SECONDS=60
PREDICTIONS_CUP_FULLSTACK_QUEUE_WARN_FRACTION=0.75
PREDICTIONS_CUP_FULLSTACK_QUEUE_BLOCK_FRACTION=0.95
PREDICTIONS_CUP_FULLSTACK_WARN_FREE_DISK_GIB=8
PREDICTIONS_CUP_FULLSTACK_MIN_FREE_DISK_GIB=3

PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_ENABLED=false
PREDICTIONS_CUP_FULLSTACK_OBSERVE_ENABLED=false
PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_MODE=IN_PROCESS
PREDICTIONS_CUP_FULLSTACK_OBSERVE_MODE=IN_PROCESS
PREDICTIONS_CUP_FULLSTACK_LIVE_LEARN_OWNER_SERVICES=predictions-cup-maker.service
PREDICTIONS_CUP_FULLSTACK_OBSERVE_OWNER_SERVICES=predictions-cup-maker.service,predictions-cup-sig-capture.service
PREDICTIONS_CUP_FULLSTACK_ALLOW_FIXTURES=false
# Only for an authoritative EXTERNAL_SERVICE provider:
# PREDICTIONS_CUP_LIVE_LEARN_COMMAND=<real command>
# PREDICTIONS_CUP_OBSERVE_COMMAND=<real command>
~~~

Fixtures are harness-development only. Final --require-real acceptance must use merged real providers.

## 3. Render/install services

~~~bash
sudo -E bash scripts/install_runtime_services.sh
bash scripts/cupctl status
bash scripts/cupctl snapshot
~~~

The installer renders absolute paths, reloads systemd, and enables only configured capabilities.

## 4. Health and one-command rehearsal

~~~bash
bash scripts/cupctl health
bash scripts/cupctl rehearse
~~~

Health/rehearsal exit semantics: 0 PASS, 1 DEGRADED, 2 BLOCKED.

After all upstream lanes are integrated:

~~~bash
bash scripts/cupctl health --require-real
sudo -E bash scripts/cupctl rehearse --require-real --safe-restart
~~~

The rehearsal first line is PASS, DEGRADED or BLOCKED; the JSON detail and persisted evidence explain
every reason.

## 5. Safe restart

~~~bash
sudo -E bash scripts/cupctl safe-restart
~~~

Expected sequence: stop EXTERNAL_SERVICE capabilities, stop MAKE, stop CAPTURE; then start CAPTURE, MAKE and any EXTERNAL_SERVICE capabilities. IN_PROCESS LIVE-LEARN/OBSERVE restart exactly once with their owner and are never separate stop/start actions. The units use SendSIGKILL=no. A hung graceful stop is BLOCKED and must be investigated.

## 6. Feed reconnect exercises

Run only in SHADOW/rehearsal mode:

~~~bash
sudo systemctl stop predictions-cup-sig-capture
bash scripts/cupctl health
sudo systemctl start predictions-cup-sig-capture
bash scripts/cupctl health

sudo systemctl stop predictions-cup-polymarket-capture
bash scripts/cupctl health
sudo systemctl start predictions-cup-polymarket-capture
bash scripts/cupctl health
~~~

The stopped feed must become BLOCKED and then recover with a fresh observation. Other safe observation
and capture should remain alive.

When LIVE-LEARN/OBSERVE are IN_PROCESS, do not stop a separate adapter unit. Exercise their real typed health/failure surface inside the owner and verify the capability degrades without creating a second consumer. Only EXTERNAL_SERVICE capabilities are independently stop/restarted.

## 7. SSH disconnect survival

Before disconnecting:

~~~bash
bash scripts/cupctl checkpoint ssh
~~~

After reconnecting:

~~~bash
bash scripts/cupctl verify ssh
~~~

PASS requires all configured services active with the same process identity.

## 8. Authorized reboot recovery

A reboot is disruptive and must be separately authorized.

~~~bash
bash scripts/cupctl checkpoint reboot
sudo reboot
~~~

After reconnecting:

~~~bash
bash scripts/cupctl verify reboot
bash scripts/cupctl health
~~~

PASS requires a changed boot ID and all configured services active.

## 9. Journal/reconciliation

Never edit or delete the live execution journal to make a gate green. Any PENDING, CANCEL_PENDING,
ACKED, OPEN, PARTIALLY_FILLED, UNCERTAIN or RECONCILING operation remains risk-bearing until BUILD-009
authoritative recovery resolves it. FULLSTACK reports unresolved state as BLOCKED.

Unresolved-order rehearsal must use deterministic fixtures/shadow execution. Do not create a real
economic SIG order to prove recovery.

## 10. Failure expectations

- SIG Realtime death: economic path fails closed; SIG freshness BLOCKED; safe other capture continues.
- PM feed death: external FV cannot be trusted; PM freshness BLOCKED.
- Account trust loss: real account/Risk provider BLOCKED; no economic resume.
- OBSERVE failure: health degrades/blocks in the owning MAKE/SIG runtime; no duplicate observer is started.
- Disk low: DEGRADED then BLOCKED at configured thresholds.
- Capture queue high: DEGRADED then BLOCKED; any drops/storage failure BLOCKED.
- Unresolved journal operation: BLOCKED until BUILD-009 reconciliation.
- Stale external FV: real MAKE/Risk lane must fail closed.
- Risk global halt: no new economic exposure.
- Candidate throws: SHADOW isolates/quarantines that candidate; peers continue.
- LIVE-LEARN backlog: in-process learner health degrades while MAKE's economic hot path remains protected; no second scoring consumer is started.

The final four are accepted only from the real merged provider contracts, not fixture health.

## 11. Operator launch checklist

- [ ] Exact git rev-parse HEAD equals the reviewed deployment SHA.
- [ ] Launch snapshot persisted with non-secret config hash and mapping identity.
- [ ] Required systemd services are active with sensible uptime/restart counts.
- [ ] SIG observation is fresh.
- [ ] PM observation is fresh when enabled.
- [ ] CAPTURE queue has zero drops/storage failures and acceptable pressure.
- [ ] Disk is above the block threshold with runway.
- [ ] MAKE and SHADOW are in the intended profile.
- [ ] Account trust is real and healthy before any economic LIVE consideration.
- [ ] Risk halt/session state is known.
- [ ] Execution journal has zero unresolved/UNCERTAIN operations.
- [ ] LIVE-LEARN is real and healthy.
- [ ] OBSERVE is real and healthy.
- [ ] SIG reconnect exercise passed.
- [ ] PM reconnect exercise passed.
- [ ] SSH survival passed.
- [ ] Authorized reboot recovery passed, or explicitly recorded not run.
- [ ] Real Risk/MAKE kill-switch exercise passed.
- [ ] bash scripts/cupctl health --require-real is PASS.
- [ ] bash scripts/cupctl rehearse --require-real is PASS.
- [ ] No secret value appears in journal or evidence output.
- [ ] No real order was created merely to satisfy rehearsal.

If economic state is ambiguous: stop adding exposure, preserve evidence, reconcile authoritatively and
do not infer that an order failed.

REAL SIG ORDERS SENT: NO

## 12. Real-acceptance exercise sequence

The deterministic `failure-injection` command is simulation-only. A successful
result is `SIMULATION_PASS`, not real host acceptance.

After the real upstream lanes are merged and configured, run the non-economic
provider exercises:

~~~bash
bash scripts/cupctl exercise-provider risk_global_halt
bash scripts/cupctl exercise-provider maker_kill_cancel
bash scripts/cupctl exercise-provider execution_recovery
~~~

Then exercise actual feed process recovery:

~~~bash
sudo -E bash scripts/cupctl exercise-reconnect sig
sudo -E bash scripts/cupctl exercise-reconnect pm
~~~

The reconnect command does not pass merely because systemd reports the service as
active. It requires a strictly newer, fresh post-restart observation. The SIG
exercise also validates capture continuity and zero capture drops/storage failures.

Record SSH survival using the checkpoint/verify pair before final acceptance.
Reboot remains separately authorization-gated. Unless
`PREDICTIONS_CUP_FULLSTACK_REBOOT_AUTHORIZED=true` has been deliberately set,
the final evidence records `NOT_RUN_REQUIRES_AUTHORIZATION`.

The final one-command gate is still:

~~~bash
sudo -E bash scripts/cupctl rehearse --require-real --safe-restart
~~~

A real PASS requires every required launch-critical exercise/provider to have
`REAL_PASS`. Synthetic `SIMULATION_PASS` records are shown for diagnostics but
receive zero final-acceptance credit.

## 13. Capability ownership verification

For reviewed #67/#66, status must show LIVE-LEARN and OBSERVE as provider_mode=real with capability mode IN_PROCESS and their declared owner services. systemd must not run predictions-cup-live-learn.service or predictions-cup-observe.service in that mode. cupctl safe-restart must restart MAKE/SIG owners only once.

Use EXTERNAL_SERVICE only for one deliberate authoritative standalone entrypoint. A second SHADOW replay/scoring consumer or duplicate OBSERVE emitter is a deployment error, not redundancy.

Final #67/#69 integration must preserve both evaluator-backed frozen candidates and the LIVE-LEARN ShadowEventStore mirror in shadow/live.py.
