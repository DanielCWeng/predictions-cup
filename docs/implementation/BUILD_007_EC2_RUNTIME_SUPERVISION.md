# BUILD-007 — EC2 Runtime Supervision / Always-On Capture

**Status:** IN REVIEW — PR #20  
**Branch:** `build/007-ec2-runtime-supervision`  
**Base main at creation:** `6340428a1c486c66990853164aecf97c27d4d719`  
**Live EC2 validated:** no

## Scope

BUILD-007 adds a small read-only deployment layer around the already accepted SIG and Polymarket
collectors. It does not add strategy, execution, order submission/cancellation, portfolio logic or
a trading service.

The two service templates are:

- `deploy/systemd/predictions-cup-sig-capture.service`;
- `deploy/systemd/predictions-cup-polymarket-capture.service`.

The default rendered ExecStart values are:

```text
<repo-root>/.venv/bin/python -m predictions_cup.sig.capture --runtime-env-only
<repo-root>/.venv/bin/python -m predictions_cup.external.polymarket.recorder --runtime-env-only
```

The installer may use an explicitly supplied `PREDICTIONS_CUP_PYTHON` instead of the default
repo-local virtual environment.

## Runtime environment boundary

Both units contain only the `@@RUNTIME_ENV@@` placeholder. The installer resolves that to the
runtime user's absolute:

```text
/home/<runtime-user>/.config/predictions-cup/runtime.env
```

Both ExecStart commands pass `--runtime-env-only`, so the collectors disable their normal repo-local `.env` source while supervised. No unit references `trade.env`; both units also use `UnsetEnvironment=PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL` as a final process-environment guard. The installer refuses `runtime.env` if it contains
`PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL` or explicitly enables trading. It also checks the existing
secret-layout policy: config directory mode `700`, runtime file mode `600`, and runtime-user
ownership.

The installer never sources the environment file, prints credential values, or overwrites either
runtime secret file.

## SIG tracked depth

The SIG unit has no `--tracked-exchange-id` argument and contains no exchange identifiers.
BUILD-007 adds the optional external runtime setting:

```text
PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS=
```

It is comma-separated when explicitly configured and empty by default. The capture entrypoint
deduplicates values from that setting and any explicit CLI `--tracked-exchange-id` arguments.
Therefore systemd startup preserves BUILD-006's safe default of no resident tracked full depth.

## systemd behavior

Both units use:

```text
Wants=network-online.target
After=network-online.target
Restart=on-failure
RestartSec=5s (SIG) / 30s (Polymarket)
KillSignal=SIGTERM
TimeoutStopSec=30s
StandardOutput=journal
StandardError=journal
UMask=0077
NoNewPrivileges=true
WantedBy=multi-user.target
```

They are foreground Python processes; there is no daemonization wrapper or custom logging daemon.

The SIG collector already handles SIGINT/SIGTERM through its stop event and closes the state engine
and SQLite recorder in `finally`.

BUILD-007 adds equivalent process-level SIGINT/SIGTERM handling to the Polymarket recorder. On
shutdown it asks the WebSocket transport to stop, cancels the long-running recorder task, and lets
its context-managed aiohttp/SQLite work unwind. Polymarket storage opens short-lived SQLite
connections per operation, so there is no persistent writer handle to invent or manage here.

Live EC2 validation exposed HTTP 429 rate limiting during Gamma keyset discovery. The candidate
now retries the current page/cursor in place with a bounded attempt budget, honors Retry-After,
uses exponential fallback delay plus jitter when the header is absent, and fails visibly after
exhaustion. Successful earlier pages are retained within the same discovery pass. Retry-After
values resolving to zero are floored to a positive delay. The Polymarket unit uses a 30-second
RestartSec so an ultimately unavailable Gamma API cannot create a tight systemd restart loop.

A second live attempt proved the corrected startup/core collector path: 3,160 markets / 6,320
tokens discovered, WebSocket connected, 6,320-row snapshots written, WebSocket reconnect recovered,
and zero storage failures. It also exposed that a later scheduled Gamma 429 exhaustion propagated
through the TaskGroup and killed otherwise healthy capture. Periodic post-startup Gamma
discovery/selection is therefore now fail-soft when a valid resident universe already exists:
health/logging records the failure, the current universe/capture remains active, and the next normal
refresh can recover. Initial discovery and local post-discovery failures such as SQLite persistence
remain fail-closed.

## Storage

The units set `WorkingDirectory` to the repository root, preserving the existing relative
defaults:

```text
data/sig_realtime.sqlite3
data/polymarket_capture.sqlite3
```

BUILD-007 does not move, delete, migrate or truncate capture data.

## Installer/update behavior

`scripts/install_runtime_services.sh`:

1. resolves runtime user/home, repo root and Python;
2. validates collector entrypoints and Python imports;
3. validates `runtime.env` existence, ownership, permissions and read-only safety;
4. renders absolute paths into both unit templates;
5. rejects unresolved placeholders, trade-secret references or a tracked-ID argument in the SIG unit;
6. installs units to `/etc/systemd/system` by default;
7. runs `systemctl daemon-reload`;
8. enables both services;
9. restarts both services;
10. verifies active state and returns non-zero on restart/active failure.

The script is idempotent and supports test-only/tooling overrides for the runtime user/home,
Python, systemd destination and systemctl executable. It never deletes runtime data or secrets.

## Automated validation

CI validation is green for lint, shell validation, strict mypy, pytest and the application smoke.

BUILD-007 adds tests proving:

- both templates point at the single runtime environment placeholder and force `--runtime-env-only`;
- neither unit references `trade.env` or the SIG trade credential;
- the SIG unit contains no tracked-exchange CLI argument;
- network-online ordering and `Restart=on-failure` are present;
- SIGTERM, journal stdout/stderr and `NoNewPrivileges` are explicit;
- the installer can run twice against a fake systemd destination without changing rendered units;
- daemon-reload, enable and restart are invoked;
- the rendered EnvironmentFile is absolute;
- secret values are not emitted;
- an accidental trade credential in `runtime.env` is rejected without printing its value;
- Polymarket shutdown stops the WebSocket transport and cancels its long-running task;
- Gamma mid-pagination 429 recovery retries the same cursor and honors Retry-After;
- zero/expired Retry-After values use a positive retry-delay floor;
- persistent Gamma 429s exhaust a bounded retry budget and fail visibly;
- initial Gamma failure remains fail-closed;
- periodic Gamma failure after a valid universe is resident is fail-soft and later recovery is covered;
- local storage failure during refresh still propagates;
- the Polymarket unit has a slower 30-second restart cadence after unrecoverable startup failure.

CI also runs `bash -n` and runs `shellcheck` when it is available on the runner.

## Live acceptance boundary

EC2 validation is partial. The SIG service/env-file side is healthy. Polymarket has now proven
successful startup, 3,160-market / 6,320-token discovery, WebSocket capture/recovery, 6,320-row
snapshot output and zero storage failures. The second live attempt exposed the periodic-refresh
TaskGroup failure described above. After this correction passes CI/re-review, the Polymarket
service must be rerun through a scheduled refresh window and shown to stay active with SQLite
output advancing; SSH-disconnect and reboot validation still follow before merge.

The exact conservative operator procedure is in `OPERATIONS.md`. It verifies enabled/active
state, resolved EnvironmentFiles, journals, capture output, manual restart, SSH disconnect
survival and an operator-controlled reboot without any trading action.
