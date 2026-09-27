# BUILD-007 — EC2 Runtime Supervision / Always-On Capture

**Status:** MERGED / ACCEPTED — PR #20  
**Branch:** `build/007-ec2-runtime-supervision`  
**Base main at creation:** `6340428a1c486c66990853164aecf97c27d4d719`  
**Live EC2 validated:** yes for pre-merge ARM64 runtime/storage gate; production mapping-bounded soak remains downstream of LIVE-MAPPING-GATE-001

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
<repo-root>/.venv/bin/python -m predictions_cup.external.polymarket.recorder --runtime-env-only --require-explicit-universe
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

## Polymarket supervised universe

The broad EXPERIMENT-001A election heuristic remains a useful explicit/manual research mode, but
the BUILD-007 systemd service no longer permits it as the always-on Cup lane.

The service passes `--require-explicit-universe`. If Polymarket capture is enabled, the installer
requires non-empty `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS`; the recorder resolves those IDs
strictly against active Gamma markets. IDs may be market IDs, condition IDs or token IDs:

- market/condition match -> all aligned tokens for that market;
- token match -> only the explicit matching token;
- any unresolved configured ID -> fail closed;
- no heuristic additions are permitted in strict mode.

No Cup identity is hard-coded. The intended production input is the independently accepted
LIVE-MAPPING-GATE-001 SIG ↔ Polymarket crosswalk. Until that exists, Polymarket capture can remain
disabled; the installer leaves its unit disabled/stopped while SIG continues normally.


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

Live EC2 validation exposed HTTP 429 rate limiting during Gamma keyset discovery. The accepted implementation
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

The live soak invalidated the original broad high-frequency SQLite deployment shape.

Measured broad-soak evidence:

- 3,160 selected markets / 6,320 tokens;
- 1-second panel = 6,320 rows/second = 546,048,000 scalar rows/day;
- 60-second depth cadence = 9,100,800 depth rows/day before deltas/trades;
- main SQLite grew 1,145,905,152 -> 1,159,487,488 bytes in ~22.15 seconds;
- that interval corresponds to roughly 2.21 GB/hour / 53 GB/day at that measured point;
- WAL was already ~2.61 GB;
- the EC2 root volume is roughly 30 GiB.

BUILD-007 does not solve that by weakening research cadence. It separates operational state from
research history.

Fresh operational SQLite:

```text
data/polymarket_operational.sqlite3
```

contains only:

- market metadata;
- token metadata;
- ingestion health.

High-frequency history:

```text
data/polymarket_research/
    observations/
    book_changes/
    trades/
    depth_snapshots/
```

is stored as immutable PyArrow Parquet shards with ZSTD compression. Defaults are 60-second time
buckets and a 100,000-row per-shard cap. Each publication uses:

```text
write temporary file -> fsync file -> os.replace final .parquet -> fsync directory
```

so readers never see a partially published final shard. Published shard names are unique and never
reopened for append. Graceful shutdown flushes resident buffers. A hard process/host loss can lose
the bounded not-yet-published in-memory shard, but cannot corrupt a previously published shard.

Source/event timestamps and local observation/sample timestamps remain separate. Financial
price/size values are represented as exact decimal text rather than binary floats. Depth levels
use typed nested structures rather than JSON blobs.

Raw Parquet trade delivery is at-least-once. Hashed trades carry a deterministic event identity
derived from token ID + transaction hash. Canonical replay validates that identity and keeps only
the first observation across duplicates within one shard or across later shards/process restarts.
Unhashed trades remain at-least-once, matching the historical partial SQLite uniqueness rule.

The pre-correction broad `data/polymarket_capture.sqlite3*` soak artifacts are legacy evidence.
BUILD-007 neither deletes nor migrates them automatically, and the supervised service uses a fresh
operational SQLite path.

Replay remains backward-compatible with legacy Polymarket SQLite captures. New Parquet research
directories are readable directly, with the operational SQLite supplied separately when health /
disconnect events are required.

## Installer/update behavior

`scripts/install_runtime_services.sh`:

1. resolves runtime user/home, repo root and Python;
2. validates collector entrypoints and Python imports;
3. validates `runtime.env` existence, ownership, permissions and read-only safety;
4. renders absolute paths into both unit templates;
5. rejects unresolved placeholders, trade-secret references or a tracked-ID argument in the SIG unit;
6. installs units to `/etc/systemd/system` by default;
7. runs `systemctl daemon-reload`;
8. always enables/restarts SIG;
9. enables/restarts Polymarket only when capture is explicitly enabled with a non-empty strict
   supervised universe; otherwise disables/stops its installed unit;
10. if an upgrade finds capture enabled but the new strict universe missing, it first issues
   `disable --now` for the existing Polymarket service and only then exits non-zero, so an old
   broad-universe candidate cannot keep running after a failed migration;
11. verifies active configured services and returns non-zero on restart/active failure.

The script is idempotent and supports test-only/tooling overrides for the runtime user/home,
Python, systemd destination and systemctl executable. It never deletes runtime data or secrets.

## Automated validation

Final head CI #685 is green for lint, shell validation, strict mypy, pytest and the application smoke.

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
- the Polymarket unit has a slower 30-second restart cadence after unrecoverable startup failure;
- supervised Polymarket startup requires an explicit universe and unresolved IDs fail closed;
- disabled Polymarket capture leaves the unit disabled/stopped while SIG remains supervised;
- an old capture-enabled env missing new supervised IDs is stopped/disabled before installer
  failure;
- fresh operational SQLite contains no high-frequency panel/delta/trade/depth tables;
- ZSTD Parquet shards preserve source/observed timestamps and depth structure;
- Parquet publication is atomic and leaves no final partial shard;
- deterministic hashed-trade identity is persisted and canonical replay de-duplicates duplicates
  within one shard and across shard/restart boundaries;
- replay reads the new Parquet research format and can combine separate operational health.

CI also runs `bash -n` and runs `shellcheck` when it is available on the runner.

## Live acceptance boundary

BUILD-007's pre-merge ARM64 EC2 runtime/storage gate is **accepted** at exact reviewed head
`1fc3383ac2471466ef440b5f050559ba0a37deed`. The branch merged as PR #20 with merge commit
`153116bb84bc64f202b4cd6dc7748e11d1a84e8b`.

The accepted live evidence includes:

- actual host architecture `aarch64` with PyArrow 25.0.1 importable;
- deliberately bounded strict test universe of 3 markets / 6 tokens;
- service remained active through the bounded soak;
- all four intended research streams exercised: observations, book changes, depth snapshots and
  trades;
- ZSTD Parquet files published and read back successfully;
- small operational SQLite footprint rather than high-frequency row history;
- scheduled Gamma refresh completed successfully while capture remained healthy;
- observed HTTP 429 retries used the corrected positive `retry_in_seconds=1.000` floor;
- manual service restart succeeded;
- post-restart WebSocket capture returned healthy with 3 markets / 6 tokens,
  `gamma_last_status=OK` and `storage_failures=0`;
- Parquet file count continued advancing after restart and previously published shards remained
  readable.

The temporary 3-market / 6-token universe was a compatibility test only. It is not production
mapping evidence and must not be promoted into runtime production configuration.

The later production-intended Polymarket gate remains separate and depends on the accepted live
crosswalk from LIVE-MAPPING-GATE-001. Once those production IDs exist, the mapping-bounded gate
must prove:

1. installed Polymarket unit contains `--require-explicit-universe`;
2. runtime.env contains the accepted mapped IDs and no trade credential;
3. subscribed market/token scope matches the accepted mapping universe;
4. operational SQLite stays small and research history appears only in ZSTD Parquet shards;
5. Parquet bytes/files advance through a soak window;
6. Gamma 429/degraded refresh does not kill last-good capture;
7. manual restart resumes capture;
8. SSH disconnect/reconnect does not affect the services;
9. reboot returns both configured services and writing resumes.

Until then, Polymarket may remain disabled/stopped while SIG supervision continues.

The exact operator procedure is in `OPERATIONS.md`.
