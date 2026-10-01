# SUPERVISOR-001 — Launch Sentry Runbook

Codename: **Grand Wizard of the Soul and Alpha Harvesting Reaper**.

The name is silly. The runtime is deliberately boring.

## Purpose

SUPERVISOR-001 is a lightweight deterministic sentry over the existing Predictions Cup
runtime. It does not subscribe to SIG or Polymarket and does not create parallel Risk,
execution, SHADOW, CAPTURE, OBSERVE or learning infrastructure.

It reads the durable/status surfaces those systems already own, publishes a canonical
current snapshot, records transitions, builds bounded forensic bundles, and may perform
a small allowlisted set of host-preservation actions.

The supervisor is **not** an execution dependency. Its failure must not impair Risk,
MAKE, CAPTURE, SHADOW or LIVE-LEARN.

## Hard safety boundary

SUPERVISOR has no action capable of:

- placing or cancelling an order;
- enabling LIVE or changing execution mode;
- loading the SIG trade credential;
- resetting a global/strategy risk halt;
- increasing or changing capital limits;
- changing maker/FV/signal parameters;
- mutating the execution journal or RISK state;
- modifying Git or production strategy.

The systemd unit explicitly unsets `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL`.

## Existing integration surfaces

The sentry consumes read-only:

- OBSERVE-001 atomic `data/runtime/status/observe.json`;
- RISK-002 durable `risk_state`;
- BUILD-009 execution lifecycle rows;
- SIG CAPTURE health;
- Polymarket `ingestion_health`;
- SHADOW append-only JSONL;
- LIVE-LEARN latest JSON report;
- systemd service state/memory;
- filesystem capacity;
- host NTP synchronization.

SQLite inputs are opened with `mode=ro` plus `PRAGMA query_only=ON`.

## Exact outputs

Default root:

```text
data/supervisor/
```

Canonical current state:

```text
data/supervisor/latest.json
```

Append-only evidence:

```text
data/supervisor/events.jsonl
data/supervisor/actions.jsonl
data/supervisor/snapshots/YYYY/MM/DD.jsonl
```

Forensic bundles:

```text
data/supervisor/bundles/events/<bundle_id>/
data/supervisor/bundles/hourly/<bundle_id>/
```

Analyst/remediation spool:

```text
data/supervisor/analysis/<bundle_id>.json
data/supervisor/remediation_requests/*.json
data/supervisor/remediation_processed/*.json
```

## Severity and fail-closed gate

Deterministic severity:

```text
OK
WARN
CRITICAL
UNKNOWN
```

Independent launch gate:

```text
PASS
HOLD
```

Missing, stale or invalid required state => `HOLD`.

WARN is descriptive unless an owning Risk/execution contract says otherwise. The
supervisor never changes execution mode itself.

## Host roles

One implementation supports:

```text
west-execution
east-archive
```

Role is read from `PREDICTIONS_CUP_SUPERVISOR_HOST_ROLE`, otherwise
`~/.config/predictions-cup/host-role`.

West expects enabled execution/learning surfaces and may prune expired immutable hot
Parquet when authorized. East expects capture/archive state; disabled MAKE/SHADOW/LEARN
does not alarm merely because it is absent.

## Cadence

Defaults:

- core poll: 1 second;
- systemd resource probe cache: 5 seconds;
- NTP probe cache: 30 seconds;
- append-only summary snapshot: 60 seconds;
- hourly forensic bundle: UTC-hour rollover;
- event bundle: reason/severity transition, debounced;
- a newly appearing CRITICAL reason bypasses event-bundle debounce.

No historical Parquet scan occurs in the health loop.

## Stable reason families

Reasons include:

- required source missing/invalid/stale;
- expected service failure;
- high or rapidly growing service memory;
- disk warning/critical/emergency pressure;
- NTP synchronization not confirmed;
- capture drops/writer/storage/queue failure;
- OBSERVE degraded/drop/sink failure;
- global/strategy RISK halt;
- untrusted RISK/account/mark/exposure state;
- incomplete RISK reconciliation;
- configured exposure limit proximity/breach;
- market concentration;
- BUILD-009 PENDING/CANCEL_PENDING/UNCERTAIN/RECONCILING state;
- `EXECUTION_AGE_UNAVAILABLE` when wall-clock operation age cannot be proven;
- SHADOW candidate failure evidence;
- LIVE-LEARN economics threshold violations only with minimum support.

Never compare process-local monotonic timestamps across process identities or reboots.

## Economics

SUPERVISOR does not recalculate LIVE-LEARN. Economic alarm thresholds are disabled
unless explicitly configured:

```text
PREDICTIONS_CUP_SUPERVISOR_MAX_ADVERSE_SELECTION
PREDICTIONS_CUP_SUPERVISOR_MIN_REALISED_SPREAD
PREDICTIONS_CUP_SUPERVISOR_MIN_POST_FILL_MARKOUT
PREDICTIONS_CUP_SUPERVISOR_MIN_ECONOMIC_SAMPLES=20
```

Thin evidence does not generate a confident economics alarm.

## Remediation authority

`PREDICTIONS_CUP_SUPERVISOR_MAX_REMEDIATION_LEVEL`:

```text
0 OBSERVE
1 HOUSEKEEPING
2 SERVICE_RECOVERY
3 HOST_PROTECTION
4 ECONOMIC (reserved; deliberately no economic actions exist)
```

For the first overnight burn-in, Levels 1–3 may be enabled.

### Level 1

`PRUNE_SUPERVISOR_BUNDLES`

Deletes only supervisor bundles older than retention.

`PRUNE_HOT_PARQUET`

West-only. Deletes only `*.parquet` older than the configured hot-retention
window inside configured SIG/PM research roots. It cannot delete SQLite, RISK,
execution journals, mappings or arbitrary files.

`CLEAR_SAFE_CACHE`

Only an exact configured path is accepted.

### Level 2

`RESTART_SAFE_SERVICE`

Only services in `PREDICTIONS_CUP_SUPERVISOR_SAFE_RESTART_SERVICES`.
Defaults to the enabled read-only capture services. MAKE/RISK are not defaults.

Default cooldown: 300 seconds. Default maximum: two restarts/service/hour.

### Level 3

`STOP_NONESSENTIAL_SERVICE`

Only explicitly configured names are accepted. Default list is empty.

## Default disk policy for the 30 GiB hosts

```text
WARN       used >= 65% OR free <= 8 GiB
CRITICAL   used >= 80% OR free <= 4 GiB
EMERGENCY  used >= 90% OR free <= 2 GiB
```

Disk pressure can trigger supervisor-bundle cleanup and west expired-hot-Parquet
cleanup. EMERGENCY can stop only explicitly allowlisted nonessential services.

## Default memory policy

Per expected service:

```text
WARN absolute           650 MiB
CRITICAL absolute       900 MiB
WARN growth              35 MiB/min
CRITICAL growth          80 MiB/min
```

Growth requires at least 30 seconds of observations. A CRITICAL condition may
restart an allowlisted read-only capture service under Level 2. MAKE is not
autonomously restarted.

## Claude/Codex analyst boundary

`scripts/supervisor_analyst_runner.py` is a separate advisory spool consumer.
It invokes the command configured in:

```text
PREDICTIONS_CUP_SUPERVISOR_ANALYST_COMMAND
```

It sends a bounded bundle prompt on stdin and exposes the bundle path as
`SUPERVISOR_BUNDLE_DIR`.

Expected JSON response:

```json
{
  "assessment": "...",
  "novel_findings": [],
  "hypotheses": [],
  "evidence_refs": [],
  "recommended_operator_checks": [],
  "recommended_code_investigations": [],
  "requested_actions": [],
  "urgency": "NOW|NEXT_HOUR|LATER",
  "confidence": 0.0
}
```

Claude may use installed read-only subagents such as Codex/Luna to gather evidence.
It cannot directly call production remediation through this interface.

Requested actions become JSON files in `remediation_requests/`. SUPERVISOR then
revalidates action code, target, authority level, cooldown and restart budget.

There is no arbitrary-shell action code.

## First overnight burn-in

Target:

```text
00:00 BST Friday 2 October 2026
until operator wake-up
```

Run on west. The burn-in supervises the real production-shaped stack but does not
authorize LIVE.

Before sleep:

1. confirm `latest.json` advances;
2. confirm at least one event bundle;
3. confirm safe restart set contains only read-only services;
4. confirm MAKE is absent from the safe restart set;
5. confirm remediation level gating tests pass;
6. if Claude auth is ready, analyze one bundle manually;
7. confirm analyst-requested actions still pass through deterministic validation.

## Manual commands

One shot:

```bash
.venv/bin/python -m predictions_cup.supervisor.cli \
  --runtime-env-only --repo-root "$(pwd)" --once
```

Five-minute burn:

```bash
.venv/bin/python -m predictions_cup.supervisor.cli \
  --runtime-env-only --repo-root "$(pwd)" --run-seconds 300
```

Inspect:

```bash
python -m json.tool data/supervisor/latest.json
tail -n 20 data/supervisor/events.jsonl
tail -n 20 data/supervisor/actions.jsonl
```

Analyst one-shot:

```bash
.venv/bin/python scripts/supervisor_analyst_runner.py --root data/supervisor --once
```

## Install

From the exact reviewed checkout:

```bash
sudo PREDICTIONS_CUP_RUNTIME_USER=ec2-user scripts/install_supervisor_service.sh
```

Then:

```bash
systemctl is-active predictions-cup-supervisor.service
journalctl -u predictions-cup-supervisor.service -n 100 --no-pager
```

The installer does not restart SIG capture, PM capture, MAKE or Risk.

## Handoff checklist

- [ ] rebase onto current main;
- [ ] ruff green;
- [ ] mypy green;
- [ ] targeted tests green;
- [ ] full CI green or unrelated failures documented;
- [ ] healthy fixture => PASS;
- [ ] missing critical fixture => HOLD;
- [ ] RISK halt => deterministic CRITICAL;
- [ ] uncertain execution + unknown wall age represented explicitly;
- [ ] atomic latest + append-only histories verified;
- [ ] event/hourly bundles verified;
- [ ] input SQLite proven read-only;
- [ ] remediation level gating verified;
- [ ] arbitrary service restart denied;
- [ ] west hot pruning cannot touch SQLite/non-Parquet;
- [ ] supervisor failure does not stop production services;
- [ ] supervisor process has no SIG trade credential;
- [ ] exact head SHA and operator commands recorded.
