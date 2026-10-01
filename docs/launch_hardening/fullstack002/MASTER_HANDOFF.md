# FULLSTACK-002 master handoff

- `STARTING_MAIN_SHA=4a040a7d309df26098af5e81e96c7f1108d03117`
- `FINAL_BRANCH_SHA=see draft PR head / grading comment` (a commit cannot embed its own final SHA without changing that SHA)
- Branch: `build/fullstack-002-current-launch`

## Components composed

- Existing SIG capture.
- Optional supervised Polymarket capture.
- MAKE as the single current-main process boundary for MAKE + embedded SHADOW + OBSERVE + LIVE-LEARN + RISK + BUILD-009.
- `predictions-cup-runtime.target`.
- 15-second one-shot canonical status timer.
- systemd `OnFailure` -> durable append-only alert journal, with optional bounded HTTP(S) push delivery.

## Components intentionally absent

- No duplicate SHADOW / OBSERVE / LIVE-LEARN / RISK daemons.
- No LIVE maker unit or LIVE drop-in.
- No synthetic flatten implementation.
- No replacement recovery architecture.

## Effective safe-default posture

- The FULLSTACK-002 maker unit forces `PREDICTIONS_CUP_TRADING_ENABLED=false` and `PREDICTIONS_CUP_EXECUTION_MODE=SHADOW`.
- The trade credential is stripped from supervised units.
- The installer rejects a trade credential, enabled trading, or `EXECUTION_MODE=LIVE` in the runtime environment.
- Unit templates are rendered and verified before installation.
- Installing does not start services.
- LIVE remains `NOT_READY` on this frozen base.

## LIVE authorization path

There is no FULLSTACK-002 LIVE start command. Status always returns `authorized=false` and enumerates blockers, including frozen-base issue #84, intended-host acceptance not run and real-order rehearsal not run.

## systemd units

- `predictions-cup-sig-capture.service`
- `predictions-cup-polymarket-capture.service`
- `predictions-cup-maker.service`
- `predictions-cup-status.service`
- `predictions-cup-status.timer`
- `predictions-cup-alert@.service`
- `predictions-cup-runtime.target`

## Control commands / status path

- Operator surface: `scripts/cupctl`
- Canonical status: `<sig-research-parent>/runtime/status/latest.json` (default `data/runtime/status/latest.json`)
- Machine-readable schema: `docs/launch_hardening/fullstack002/STATUS_CONTRACT.schema.json`
- Explicitly non-runtime sample: `docs/launch_hardening/fullstack002/STATUS_SAMPLE.json`
- Durable alerts: `<sig-research-parent>/runtime/alerts/events.jsonl`
- Pinned CI rehearsal evidence: `docs/launch_hardening/fullstack002/REHEARSAL_EVIDENCE_20261001.json`
- Operator card: `docs/runbooks/LAUNCH_OPERATOR_CARD.md`

## Tests / evidence

- Ruff, shell validation and strict mypy are CI gates.
- `tests/test_fullstack002.py` covers fail-closed unit posture, SHADOW admission, no-LIVE status, halt sequencing and truthful flatten behavior.
- `scripts/cupctl rehearse` labels synthetic evidence `SIMULATION_PASS` / `BLOCKED` and leaves real host/order evidence `NOT_RUN`.
- Rehearsal performs component imports, mapping/config checks, status aggregation, rendered `systemd-analyze verify`, and installer shell syntax.
- Final full-repository CI result is recorded on the draft PR / grading comments.

## Resource considerations

- No new resident Python supervisor.
- Status is a bounded systemd one-shot every 15 seconds.
- No research datasets or historical scans are loaded.
- Status uses read-only bounded SQLite queries for risk/execution state.
- No duplicate venue REST polling is introduced.

## Issues / host tests still required

- #80: code-side current launch/rehearsal plumbing implemented. Intended-host real canary/recovery acceptance remains `HOST_ACCEPTANCE_NOT_RUN`.
- #87: service failure/StartLimit, global halt, stale capture, storage danger, clock failure and unresolved execution state are durable alerts; optional webhook push delivery is code-complete but still requires intended-host configuration/acceptance.
- #84: open on the frozen base; not owned or modified by this lane.

Known blockers: frozen-base #84, no intended-host acceptance, no real-order lifecycle rehearsal and no accepted flatten primitive.

`EC2_STRESS_TESTED=NO`

`INTENDED_HOST_ACCEPTED=NO`

`REAL_SIG_ORDERS_SENT=NO`
