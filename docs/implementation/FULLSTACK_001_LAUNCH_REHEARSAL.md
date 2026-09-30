# FULLSTACK-001 — Launch Rehearsal / Service Composition

**Branch:** build/fullstack-001-launch-rehearsal  
**Base at lane start:** 0b91e5c4e8ed461e7e5e8fcc880a094029f58891  
**Safety posture:** the standard FULLSTACK installation is SHADOW/rehearsal only and cannot authorize LIVE orders.

## Mission

FULLSTACK-001 composes the accepted launch stack without introducing a second trading orchestrator.
systemd remains the process supervisor. BUILD-009 remains the only execution/risk boundary.
Candidate math remains behind SHADOW candidate/provider contracts.

Operator surface:

~~~bash
bash scripts/cupctl status
bash scripts/cupctl health
bash scripts/cupctl snapshot
bash scripts/cupctl safe-restart
bash scripts/cupctl rehearse
~~~

The health command is machine-readable JSON. The rehearsal command emits PASS, DEGRADED or BLOCKED
plus machine-readable evidence.

## Process composition

Separate supervised processes:

- predictions-cup-sig-capture.service: accepted CAPTURE/BUILD-007 SIG collector.
- predictions-cup-polymarket-capture.service: accepted mapping-bounded Polymarket collector.
- predictions-cup-maker.service: MAKE process containing MAKE, BUILD-009 Risk/execution planning,
  account recovery and SHADOW.
- predictions-cup-live-learn.service: optional EXTERNAL_SERVICE adapter only.
- predictions-cup-observe.service: optional EXTERNAL_SERVICE adapter only.
- predictions-cup-runtime.target: grouping only; the installer renders the configured active units.

MAKE, central Risk and SHADOW deliberately remain in one process. The accepted MakerService already
passes the exact immutable MakerMarketSnapshot used for MAKE decisions into SHADOW through a
non-blocking observer. SHADOW must not create another SIG or Polymarket subscriber.

LIVE-LEARN and OBSERVE are capabilities with explicit ownership. IN_PROCESS capabilities ride with their accepted owner and never spawn a duplicate adapter; EXTERNAL_SERVICE remains available for a deliberate standalone provider. For reviewed #67/#66, LIVE-LEARN is owned by MAKE/SHADOW and OBSERVE by MAKE/SIG. CAPTURE remains separately supervised so useful evidence can continue when safe. CAPTURE first-hours forensics remains an analysis/reporting process rather than a new daemon.

## Capability adapter boundary

The deployment layer deals with capabilities, readiness and shutdown/recovery hooks, never candidate
IDs or candidate math. Capability mode is explicit: IN_PROCESS or EXTERNAL_SERVICE. IN_PROCESS reads real health from its owner and starts no adapter service. EXTERNAL_SERVICE may use a real command supplied by PREDICTIONS_CUP_LIVE_LEARN_COMMAND or PREDICTIONS_CUP_OBSERVE_COMMAND. Fixtures remain harness-development only. All modes publish/read the common health surface under PREDICTIONS_CUP_FULLSTACK_STATUS_DIR.

Known health records are account.json, shadow.json, risk.json, live-learn.json and observe.json.
Final acceptance with --require-real rejects fixture-backed LIVE-LEARN/OBSERVE and missing real
account/SHADOW health. When upstream lanes land, replace adapters with their accepted providers;
do not duplicate their logic here.

## Installer and secret boundary

scripts/install_runtime_services.sh extends BUILD-007 and renders every unit on the destination host.
It preserves runtime.env at ~/.config/predictions-cup/runtime.env and never sources or prints secrets.

The standard FULLSTACK installer rejects:

- PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL in runtime.env;
- PREDICTIONS_CUP_TRADING_ENABLED=true;
- PREDICTIONS_CUP_EXECUTION_MODE=LIVE;
- enabled Polymarket capture without an explicit supervised universe;
- EXTERNAL_SERVICE LIVE-LEARN/OBSERVE without a real command unless fixtures are explicitly allowed;
- IN_PROCESS LIVE-LEARN/OBSERVE without the required owner process enabled;
- any service referencing trade.env;
- any service that fails to strip the SIG trade credential or runtime dotenv boundary;
- any standard MAKE unit containing --live.

This is deliberate. A separately reviewed LIVE deployment must still satisfy BUILD-009 explicit
LIVE interlocks; FULLSTACK rehearsal never weakens them.

## Graceful restart

All units use SIGTERM and SendSIGKILL=no. A service that refuses graceful shutdown is an operator-
visible failure rather than permission to blindly SIGKILL execution state.

Safe restart order:

~~~text
STOP:  EXTERNAL_SERVICE LIVE-LEARN / OBSERVE only
       -> MAKE
       -> SIG / PM CAPTURE

START: SIG / PM CAPTURE
       -> MAKE
       -> EXTERNAL_SERVICE LIVE-LEARN / OBSERVE only
~~~

IN_PROCESS LIVE-LEARN/OBSERVE are restarted exactly once with their owner and never appear as independent systemd actions.

Stopping MAKE invokes its accepted kill/cancel drain and closes SHADOW/journal state. Capture then
gets its own publication window. On startup BUILD-009/MAKE owns authoritative account reconciliation
and unresolved-operation recovery. FULLSTACK never declares an UNCERTAIN operation resolved.

## Status and health

One status command reports exact Git SHA, service state/PID/uptime/restart count, environment/profile,
trading/MAKE/SHADOW enablement, Risk halt state, SIG/PM latest observations, account/SHADOW health
when exposed, execution journal state, pending/UNCERTAIN counts, capture queue health, disk, and
LIVE-LEARN/OBSERVE health.

Health checks use explicit states. Hard blockers include inactive required services, stale enabled
feeds, unresolved execution state, capture drops/storage failure, critical queue pressure and
critical disk pressure. Missing upstream health is explicit DEGRADED during harness development and
BLOCKED under --require-real.

## Launch snapshot

The snapshot is written atomically and contains:

- exact Git SHA;
- FULLSTACK config schema version;
- SHA-256 of non-secret configuration only;
- mapping path/hash/version;
- enabled candidate IDs/versions observed in the append-only SHADOW journal;
- non-secret risk profile;
- service versions;
- hostname, platform, architecture, Python version, region and runtime profile.

Credential-, secret-, password-, private-key- and API-key-like settings are excluded from the hash
input and never emitted.

## Rehearsal gate and evidence

bash scripts/cupctl rehearse writes append-only rehearsal evidence under data/runtime/rehearsals by
default. --safe-restart exercises the ordered graceful restart. --require-real is the final
integration gate and prevents temporary fixtures from satisfying acceptance.

SSH/reboot survival uses before/after checkpoints. Real feed reconnect tests are operator-controlled
systemd stop/start exercises documented in the runbook; no real order is required or permitted.

## Failure injection

Deterministic harness tests cover unresolved/UNCERTAIN journal state, fixture rejection under
--require-real, ordered graceful restart, SSH service-identity survival, secret-safe config hashing
and systemd LIVE-interlock invariants.

Production rehearsal additionally exercises SIG/PM service death and reconnect, observer/learner
death, SSH disconnect and authorized reboot. Account-trust loss, stale external FV, Risk global halt,
candidate exceptions and LIVE-LEARN backlog use the real RISK/LIVE-LEARN/OBSERVE/CANDIDATE-RUNTIME
health contracts after those branches merge. FULLSTACK must not reimplement those lanes.

## Upstream integration

When RISK-002, LIVE-LEARN-001, OBSERVE-001 and CANDIDATE-RUNTIME-001 merge:

1. rebase this branch onto merged main;
2. preserve #67 LIVE-LEARN as the in-process SHADOW mirror and #66 OBSERVE as in-process MAKE/SIG instrumentation unless an authoritative standalone entrypoint is deliberately added;
3. preserve both #69 evaluator-backed candidate wiring and #67 learner mirror in shadow/live.py;
4. expose real Risk/account/SHADOW and capability health, including ownership mode and owner services;
5. remove fixture enablement from the intended-host runtime.env;
6. run bash scripts/cupctl health --require-real;
7. run bash scripts/cupctl rehearse --require-real --safe-restart;
8. preserve BUILD-009 as the only execution/risk boundary.

Final acceptance is not satisfied by mocks.

## Verification

~~~bash
pytest -q tests/test_fullstack001.py tests/test_runtime_services.py
ruff check src/predictions_cup/runtime tests/test_fullstack001.py tests/test_runtime_services.py
mypy
~~~

Intended-host evidence must additionally follow docs/runbooks/FULLSTACK_001.md.

REAL SIG ORDERS SENT: NO

## Real acceptance evidence versus simulation

Deterministic failure injection and the local MakerKillSwitch probe are CI/scaffold
validation only. They emit `SIMULATION_PASS` when successful. They can block a
rehearsal if broken, but they can never satisfy `--require-real`.

Final host acceptance uses explicit evidence states:

- `SIMULATION_PASS`: deterministic/local-only validation;
- `REAL_PASS`: actual composed-runtime exercise passed;
- `NOT_RUN`: required real exercise is absent;
- `NOT_RUN_REQUIRES_AUTHORIZATION`: disruptive exercise intentionally not run
  because authorization is absent;
- `BLOCKED`: real exercise or provider failed.

Under `--require-real`, FULLSTACK requires `REAL_PASS` for:

- real Risk/global-halt integration;
- real MAKE kill/cancel path, non-economic;
- unresolved-order recovery through the real BUILD-009 recovery path;
- SIG service stop/reconnect with a strictly newer post-restart observation;
- Polymarket service stop/reconnect with a strictly newer post-restart observation
  when Polymarket capture is enabled;
- the ordered safe restart, including resumed feed observations;
- capture continuity across the real exercise;
- real LIVE-LEARN health;
- real OBSERVE health;
- SSH survival.

The local synthetic matrix and local kill-switch probe remain in rehearsal evidence
under `simulation_validation`, but are excluded from the real-acceptance credit.

Reboot evidence is separately authorization-gated. With
`PREDICTIONS_CUP_FULLSTACK_REBOOT_AUTHORIZED=false`, the evidence explicitly
records `NOT_RUN_REQUIRES_AUTHORIZATION` and does not silently pass. Once set
true, reboot survival becomes a required `REAL_PASS` item.

Real upstream provider exercises may be connected through the narrow command hooks
in runtime.env. Each command must emit a final JSON object containing
`evidence_state=REAL_PASS`, `provider_mode=real`, and
`economic_order_sent=false`. FULLSTACK records the returned evidence but does not
duplicate Risk, MAKE or BUILD-009 recovery logic.

## Capability ownership contract

For the reviewed #67/#66 shapes, configure LIVE-LEARN as IN_PROCESS owned by predictions-cup-maker.service and OBSERVE as IN_PROCESS owned by predictions-cup-maker.service plus predictions-cup-sig-capture.service. Status and launch snapshots record mode and owners. Under --require-real, missing real health, a mode mismatch, or owner mismatch is BLOCKED. EXTERNAL_SERVICE remains supported for future authoritative standalone providers.

This avoids a second LIVE-LEARN replay/scoring consumer and duplicate OBSERVE instrumentation. Final #67/#69 integration in shadow/live.py must preserve both frozen evaluator wiring and the LIVE-LEARN ShadowEventStore mirror.
