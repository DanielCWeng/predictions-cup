# FULLSTACK-002 current-main launch composition

Starting `main` is frozen at:

`4a040a7d309df26098af5e81e96c7f1108d03117`

This lane composes only contracts present on that SHA. It does not merge sibling work and it does not add a LIVE authorization path.

## Effective runtime

The long-lived composition is intentionally small:

- `predictions-cup-sig-capture.service` — read-only SIG capture.
- `predictions-cup-polymarket-capture.service` — optional supervised Polymarket capture.
- `predictions-cup-maker.service` — MAKE forced to SHADOW. Existing SHADOW, OBSERVE,
  LIVE-LEARN and RISK contracts remain in-process with MAKE.
- `predictions-cup-status.timer` — 15-second one-shot refresh of machine-readable status.
- `predictions-cup-alert@.service` — durable append-only failure event on systemd failures.

No extra market-data, shadow, observation or learning daemon is introduced.

## Safety boundary

FULLSTACK-002 cannot enable LIVE.

The maker service always injects:

```text
PREDICTIONS_CUP_TRADING_ENABLED=false
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
```

and strips `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL`.

The installer also rejects a runtime environment containing a trade credential or an enabled
trading flag. The operator `flatten` command returns `NOT_READY`; there is no FULLSTACK-002
LIVE unit, LIVE drop-in or live-start command.

This is deliberate. On the frozen base, real-order/intended-host acceptance remains unperformed,
and issue #84 still describes a real LIVE telemetry defect: the fresh-admission `SigLiveSink`
constructor omits its observation emitter. FULLSTACK-002 reports that as a launch blocker instead
of bypassing or repairing sibling-owned execution code.

## Operator surface

Use:

```bash
scripts/cupctl status --json
scripts/cupctl status --json --write
scripts/cupctl start-shadow
scripts/cupctl stop
scripts/cupctl restart
scripts/cupctl halt --reason <reason>
scripts/cupctl rehearse
scripts/cupctl flatten
```

`flatten` exits non-zero and reports `NOT_READY`.

`halt` first stops MAKE and confirms it is no longer active, then delegates to the existing
RISK-002 durable `halt-global` operator contract. This avoids mutating durable halt state beneath
a still-running process that owns an in-memory capital state.

## Status contract

Default path:

`data/runtime/status/latest.json`

The location follows `PREDICTIONS_CUP_SIG_RESEARCH_PATH`: its parent directory becomes the
runtime root. It can be overridden with `PREDICTIONS_CUP_FULLSTACK_STATUS_PATH`.

The JSON includes:

- frozen starting-main SHA and current code SHA;
- host identity;
- non-secret configuration hash;
- mapping hash/version;
- risk registry hash/version;
- session identifier when available;
- effective enabled component set;
- installed/configured/enabled/healthy/authorized state for each launch component;
- systemd process state and restart counts;
- SIG and Polymarket capture freshness;
- OBSERVE health;
- LIVE-LEARN health proxy;
- risk/global-halt state;
- unresolved execution operation count;
- clock/NTP state;
- storage free-space state;
- explicit LIVE authorization state and blockers.

The status file is written atomically.

## Durable alerts

Default path:

`data/runtime/alerts/events.jsonl`

Alerts are append-only JSONL. They are emitted for:

- systemd `OnFailure`;
- transition into global halt;
- transition to stale SIG capture;
- transition to stale Polymarket capture;
- storage danger;
- unhealthy NTP/clock state;
- service failure/start-limit failure observed by the status poller.

FULLSTACK-002 deliberately does not add a third-party network notification dependency. The alert
journal is durable and machine-readable; external notification can consume it without entering the
trading hot path.

## Installation

The installer does not start services:

```bash
sudo scripts/install_fullstack002.sh
```

It validates the runtime environment, imports, unit templates and systemd syntax where
`systemd-analyze` is available, renders units, reloads systemd and enables only the runtime target
and status timer.

Start explicitly only after configuration is correct:

```bash
scripts/cupctl start-shadow
```

## Rehearsal

`scripts/cupctl rehearse` is simulation-only. It validates imports, mapping presence, safe SHADOW
configuration, status aggregation, systemd unit syntax and installer shell syntax.

Its evidence is intentionally labelled:

```json
{
  "economic_order_sent": false,
  "real_host_evidence": "NOT_RUN",
  "real_result": "NOT_RUN"
}
```

A simulation pass must never be reported as a real intended-host or real-order pass.

## Recovery

After a process failure:

1. inspect `scripts/cupctl status --json`;
2. inspect the durable alert journal;
3. inspect the relevant journalctl unit log;
4. do not restart MAKE if unresolved execution operations, global halt, bad clock, storage danger,
   or unsafe configuration is reported;
5. use `scripts/cupctl restart` only for the safe SHADOW composition.

For a global halt, leave MAKE stopped until the existing RISK-002 reset procedure has been
explicitly completed with the service stopped.

## Known limitations on the frozen base

- LIVE is not authorized or exposed.
- Real SIG order/cancel/recovery rehearsal is `NOT_RUN`.
- Intended-host acceptance is `NOT_RUN`.
- The frozen base still contains issue #84's LIVE observation-emitter gap.
- FULLSTACK-002 does not claim a flatten implementation where no accepted current-main operator
  contract exists.

Those are surfaced as blockers rather than hidden behind a green simulation result.
