# Operations

## Current state

- Read-only SIG/Polymarket collector deployment capability exists and has been live-validated on EC2.
- No production trading daemon or autonomous execution deployment exists.
- Normal application startup remains finite, network-free and non-trading.
- The accepted repository includes a separate, explicitly invoked public read-only Polymarket research recorder.
- BUILD-006 SIG live REST governance/tracked-depth correction is accepted on `main`; its accepted 60-second credentialed smoke passed at the merged head.
- BUILD-007 / PR #20 is merged/accepted. Its ARM64 EC2 PyArrow/Parquet runtime/storage gate passed on 26 September 2026.
- The live 2026 SIG ↔ Polymarket crosswalk is accepted on `main`; the remaining production-runtime gate is the mapping-bounded paired soak plus SSH independence/reboot recovery using accepted IDs.
- PR #47 is merged; routine Kaggle work uses the GitHub Actions manifest runner rather than EC2.

The accepted EXPERIMENT-001A baseline used local SQLite/WAL research persistence. BUILD-007, now accepted on `main`, changes the live/supervised storage shape after the EC2 soak: market/token metadata and health remain in a small operational SQLite, while high-frequency panel/book-change/trade/depth history is written as immutable ZSTD Parquet shards.


## CAPTURE-001 launch runbook — PR #57 branch only until accepted

CAPTURE-001 reuses the BUILD-007 supervised collectors. Before deploying this branch, put the
launch-specific research roots in `runtime.env` while keeping trading disabled:

```text
PREDICTIONS_CUP_SIG_RESEARCH_PATH=data/launch_20261001/sig
PREDICTIONS_CUP_POLYMARKET_RESEARCH_PATH=data/launch_20261001/polymarket
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<accepted mapping IDs>
PREDICTIONS_CUP_TRADING_ENABLED=false
```

The SIG service still uses the accepted full-universe Realtime + broad scalar/BBO model. Full depth
remains explicitly bounded by `PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS`; do not turn
all Cup markets into tracked depth merely for research. Dirty tracked books use HIGH-priority
governed REST, expiry-safety refresh uses NORMAL, and broad scalar/BBO sweeps use BACKGROUND.

After install/restart, verify both process liveness and evidence:

```bash
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo journalctl -u predictions-cup-sig-capture -n 100 --no-pager
find data/launch_20261001/sig -type f -name '*.parquet' | tail
find data/launch_20261001/polymarket -type f -name '*.parquet' | tail
sqlite3 data/sig_realtime.sqlite3 \
  "select observed_at,payload_json from capture_health order by id desc limit 3;"
```

The SIG health JSON must show a bounded research queue with `dropped_rows=0` and
`storage_failures=0`. Published shards must remain readable across service restart. A hard crash may
lose only the not-yet-published in-memory shard; it must never mutate an already-published shard.

Generate the first-hours package directly from capture artifacts:

```bash
python -m predictions_cup.analysis.first_hours \
  --input data/launch_20261001/sig \
  --polymarket-root data/launch_20261001/polymarket \
  --execution-journal data/execution_journal.sqlite3 \
  --mapping data/mappings/sig_polymarket_2026.json \
  --output data/launch_20261001/first_hours
```

The output includes `summary.json`, `report.md`, market activity, economic BBO/update metrics,
trade/markout diagnostics, tracked-depth summaries, 15-minute activity tables and direct mapped
cross-venue response/latest-discrepancy tables. Direct PM values are aligned through the accepted
`SAME`/`COMPLEMENT` mapping direction before comparison. Treat response lags as nearest-subsequent
economic changes rather than causal evidence. Treat aggressor classification as
price-vs-prior-BBO inference with a freshness rule, not participant identity. No passive queue
position is inferred.

The final production gate for PR #57 is a mapping-bounded paired soak on the intended launch host,
followed by collector restart, SSH disconnect/reconnect, operator-controlled reboot, readable-shard
checks and a successful `first_hours` run. Existing BUILD-006/007 evidence validates the underlying
governor/systemd/Parquet mechanisms but does not by itself validate the new SIG immutable layer.

## Kaggle execution — canonical GitHub Actions route

Routine batch compute is repository-controlled:

1. commit the owning experiment code and `kernel-metadata.json`;
2. add or update `kaggle/jobs/<job>.json`;
3. use `"action": "run"` for new compute;
4. push the manifest and monitor the **Kaggle Runner** GitHub Action;
5. retrieve compact logs/status/results through Actions/GitHub;
6. keep bulky raw outputs on Kaggle unless Git needs them.

Supported manifest actions are `auth_check`, `run`, `status`, and `output`. The workflow uses
the repository `KAGGLE_API_TOKEN` secret and permits up to five concurrent jobs. Never print,
request or commit Kaggle credentials. EC2 is reserved for persistent runtime, live capture,
EC2-resident datasets or tasks unavailable through this path.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

The recorder writes a lean 1-second scalar panel, normalized event-time book changes and public trade events. Top-20 depth snapshots default to every 60 seconds rather than every second. Feed receive/PONG liveness is bounded; an unhealthy connection is closed and the existing reconnect path invalidates books and REST-reseeds them before accepting new deltas.

On BUILD-007 the low-volume operational database defaults to
`data/polymarket_operational.sqlite3`; high-frequency research history defaults to
`data/polymarket_research/`. The old broad-soak `data/polymarket_capture.sqlite3*` files are
legacy evidence and are not deleted or reused automatically. Storage failures surface rather than
being silently ignored.

Gamma discovery is rate-limit aware. HTTP 429 retries stay on the current keyset cursor, honor
`Retry-After` when present, otherwise use bounded exponential backoff with jitter, and stop after
a bounded attempt budget with a visible error. A successful earlier page is not discarded and
pagination is not restarted from page 1 after a transient 429. A parsed `Retry-After` of zero
or an already-expired HTTP date is floored to a small positive delay so repeated 429 responses
cannot be retried in a millisecond-scale burst.

Initial startup remains fail-closed: without a valid selected universe the recorder exits visibly.
After startup has established a resident universe, periodic Gamma discovery/selection failures are
fail-soft: the recorder logs/records the Gamma failure, keeps the existing universe and active
WebSocket/book/snapshot capture, and retries at the next normal Gamma refresh interval. Local
post-discovery failures such as SQLite persistence still propagate rather than being hidden.

## SIG Realtime capture — BUILD-006 accepted runtime

The SIG capture process is explicit; normal application startup does not launch it. A read credential and an explicit tournament UUID are required.

Enumerate accessible tournaments for operator selection:

    python -m predictions_cup.sig.capture --list-tournaments

BUILD-006 keeps tournament-wide Realtime and broad scalar/BBO capture while making full-depth maintenance opt-in. A conservative smoke with a small explicit tracked set is:

    python -m predictions_cup.sig.capture \
      --tournament-id <TOURNAMENT_UUID> \
      --tracked-exchange-id <EXCHANGE_ID_1> \
      --tracked-exchange-id <EXCHANGE_ID_2> \
      --run-seconds 60 \
      --print-health

With no --tracked-exchange-id arguments, the process deliberately maintains no resident trusted full depth and logs that fact. It still records the full tournament Realtime tape and broad bulk-price observations.

The accepted BUILD-006 credentialed smoke ran for 60 seconds at the exact head later merged in PR #19. It observed 237 known exchanges with 1 tracked and 236 untracked, produced 0 HTTP 429s and 0 reconciliation failures, made 2 full-book reads, and kept the tracked book inside the 30-second freshness bound. That BUILD-006 live gate is complete; it is separate from BUILD-007's still-outstanding live systemd/SSH/reboot validation.

The BUILD-006 live path uses one governed REST client. The default is 2 requests/second. The earlier blocking curl + sleep probe only demonstrated roughly 2.0–2.4 request starts/second, so 3 requests/second remains unvalidated until a fixed-cadence live probe is run. SIG does not publish a numeric REST limit in the supplied contract. HIGH tracked dirty/recovery work can overtake BACKGROUND bulk-price work, and a 429 creates shared cooldown for callers using the same governed client.

The broad universe uses GET /exchanges/prices in batches of at most 100. At 237 exchanges one complete scalar/BBO sweep is three requests. Those observations are stored separately from authoritative full books and can never make depth trusted. A missingIds result clears any prior scalar latest-price/BBO/spread values for that exchange rather than leaving stale fallback state resident.

Tracked books remain fail-closed. A tracked bookDirty removes trust before HIGH-priority authoritative reconciliation. An untracked bookDirty is persisted but does not trigger a full-book request. Reconnect, token refresh, socket error and revision-gap recovery reseed only tracked full depth and refresh broad scalar state through the bulk endpoint.

SIG documents that order expiry emits no Realtime event. Because the documented aggregate exchange-orderbook response has no per-order expirationDate, the 30-second tracked-book refresh default remains a project expiry-safety fallback rather than a SIG requirement. It applies only to tracked open books and removes trust before waiting for the refresh.

The default recorder path is data/sig_realtime.sqlite3. SQLite/WAL persistence includes Realtime deliveries/trades/invalidations, authoritative market/full-book observations, compact broad price/BBO observations and depth/trust transitions.

See docs/implementation/BUILD_004_SIG_REALTIME.md for the accepted historical baseline and docs/implementation/BUILD_006_SIG_REST_GOVERNOR.md for the accepted corrective runtime contract.


## AWS EC2 runtime secret layout

The current AWS runtime host keeps SIG credentials outside the repository under the operator's
home directory:

```text
~/.config/predictions-cup/runtime.env
~/.config/predictions-cup/trade.env
```

The intended split is:

- `runtime.env` — read credential plus fail-closed runtime settings such as
  `PREDICTIONS_CUP_TRADING_ENABLED=false`;
- `trade.env` — `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL` only.

The directory is expected to be mode `700`; both files are expected to be mode `600`.
Read-only capture, replay and research processes must load `runtime.env` only. The trade-secret
file must remain unsourced unless a separately approved execution path explicitly requires it.
Code, logs, CI, GitHub and operator documentation must never contain the credential values.

## EC2 collector runbook — BUILD-007 accepted

BUILD-007 installs two read-only system services:

- `predictions-cup-sig-capture.service`;
- `predictions-cup-polymarket-capture.service`.

Both templates are stored under `deploy/systemd/`. The installer resolves the runtime user,
repository root, Python executable and runtime home to absolute values before copying units to
`/etc/systemd/system/`. The installed `EnvironmentFile` therefore resolves to:

```text
/home/<runtime-user>/.config/predictions-cup/runtime.env
```

rather than a literal `~`. Both ExecStart commands use `--runtime-env-only`, which disables the application's normal repo-local `.env` support for these supervised processes. Neither unit loads `trade.env`. The units also strip `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL` from the final process environment, and the installer refuses a
`runtime.env` containing `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL` or an enabled trading flag,
and requires the existing secret-layout permissions: directory mode `700`, file mode `600`.

The default rendered commands are:

```text
<repo-root>/.venv/bin/python -m predictions_cup.sig.capture --runtime-env-only
<repo-root>/.venv/bin/python -m predictions_cup.external.polymarket.recorder --runtime-env-only --require-explicit-universe
```

Set `PREDICTIONS_CUP_PYTHON` only if the EC2 runtime intentionally uses a different Python.
The SIG command does not contain any tracked exchange ID. Tracked depth remains external runtime
configuration through:

```text
PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS=
```

in `runtime.env`. Empty/unset means no tracked full depth. When explicitly needed, supply a
comma-separated set of exchange IDs in that external file; the installed unit remains unchanged.

The runtime environment must provide a non-empty
`PREDICTIONS_CUP_SIG_READ_CREDENTIAL` and `PREDICTIONS_CUP_TOURNAMENT_ID`, and must keep
`PREDICTIONS_CUP_TRADING_ENABLED=false` (or omit it).

Polymarket is now separately gated. If
`PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=false` (or unset), the installer still installs its
unit but explicitly leaves it disabled/stopped while SIG remains enabled. If Polymarket capture is
enabled, `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS` is mandatory and must contain an explicit
bounded market/condition/token set. For production this must come from the accepted mapping
crosswalk. Missing IDs trigger an operational fail-closed migration: the installer first
`disable --now`s any existing Polymarket service, then exits non-zero. Unresolved IDs fail recorder
startup. The service never falls back to the broad election heuristic.

### Install / update

From the repository checkout:

```bash
git pull --ff-only
python -m pip install -e '.[dev]'
sudo bash scripts/install_runtime_services.sh
```

The installer is idempotent: it re-renders/copies the same two units, runs `daemon-reload`,
enables/restarts SIG and, only when explicitly configured with a supervised Polymarket universe,
enables/restarts Polymarket. Otherwise Polymarket is disabled/stopped. Active configured services
are verified and restart/active-state failure returns non-zero. It never overwrites `runtime.env`, never deletes capture data,
and never prints credential values.

### Status

```bash
sudo systemctl status predictions-cup-sig-capture
sudo systemctl status predictions-cup-polymarket-capture
sudo systemctl is-enabled predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
```

### Follow logs

```bash
sudo journalctl -u predictions-cup-sig-capture -f
sudo journalctl -u predictions-cup-polymarket-capture -f
```

### Restart / stop / start

```bash
sudo systemctl restart predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl stop predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl start predictions-cup-sig-capture predictions-cup-polymarket-capture
```

Both units use `Restart=on-failure`, wait for `network-online.target`, write stdout/stderr to
journald, and send normal `SIGTERM` with a 30-second stop timeout. SIG uses a 5-second restart
delay. Polymarket uses a 30-second restart delay so persistent upstream Gamma failure cannot create
a tight discovery/restart loop. SIG capture already closes its
state engine/recorder on shutdown. BUILD-007 adds equivalent SIGTERM handling to the Polymarket
process: stop the WebSocket loop, cancel long-running tasks and let short SQLite context-managed
writes/connections unwind normally.

The services are enabled for `multi-user.target`, so an accepted/live-installed BUILD-007
deployment should restart them after an EC2 reboot once systemd reaches the network-online
dependency. Relative capture paths are deliberately preserved by `WorkingDirectory=<repo-root>`:

```text
data/sig_realtime.sqlite3
data/polymarket_operational.sqlite3
data/polymarket_research/
```

The Polymarket research directory contains per-stream immutable ZSTD Parquet shards for the
1-second scalar/BBO panel, normalized book changes, public trades and periodic depth snapshots.
Fresh operational SQLite contains only markets, tokens and health.

### Troubleshooting

```bash
sudo systemctl cat predictions-cup-sig-capture
sudo systemctl cat predictions-cup-polymarket-capture
sudo journalctl -u predictions-cup-sig-capture -n 100 --no-pager
sudo journalctl -u predictions-cup-polymarket-capture -n 100 --no-pager
```

If installation fails before restart, check the runtime user/home, `.venv/bin/python`, the
`700/600` permissions, required runtime variables and collector entrypoints. If a service fails
after restart, the installer exits non-zero and the journal commands above are the first
diagnostic step.

### Conservative live EC2 validation — corrected bounded lane required

The broad soak is useful evidence but is **not** an acceptable production lane. It selected 3,160
markets / 6,320 tokens. At that size, the 1-second panel implies 546,048,000 scalar rows/day and
the 60-second depth schedule implies 9,100,800 depth rows/day before deltas/trades. During the soak,
the main SQLite file grew from 1,145,905,152 to 1,159,487,488 bytes in about 22.15 seconds, roughly
a 53 GB/day point estimate, while the WAL was already about 2.61 GB. That falsified broad
high-frequency SQLite as an always-on design for the roughly 30 GiB EC2 root volume.

Do not reduce the 1-second cadence to accommodate SQLite. BUILD-007 instead requires the intended
competition universe and Parquet storage.

### Accepted mapping runtime configuration

The production crosswalk now exists. When Polymarket supervised capture is enabled, populate
`PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS` only from the accepted crosswalk artifacts under
`data/mappings/`.

If the production IDs have not yet been installed on a host, leave Polymarket disabled rather than
using guessed, temporary or heuristic IDs:

```text
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=false
```

The legacy broad-soak database/WAL should be preserved unless an operator deliberately archives or
removes it after extracting needed evidence. BUILD-007 performs no destructive cleanup.

### Accepted ARM64 Parquet smoke/soak — 26 September 2026

The required pre-merge runtime/storage compatibility gate passed on the actual ARM64 EC2 host at
reviewed head `1fc3383ac2471466ef440b5f050559ba0a37deed`.

Temporary test configuration used a deliberately bounded explicit public universe: 3 markets /
6 tokens. This was **not** mapping acceptance and those IDs must not be reused as production
configuration.

Observed acceptance evidence:

- host architecture `aarch64`; PyArrow 25.0.1 imported successfully;
- both supervised collectors were active;
- strict Polymarket universe remained 3 markets / 6 tokens;
- ZSTD Parquet shards were produced for observations, book changes, depth snapshots and trades;
- published Parquet files read back successfully;
- research storage remained in the hundreds of KB during the bounded test rather than showing the
  prior SQLite/WAL explosion;
- scheduled Gamma refresh completed with `gamma_last_status=OK`;
- live 429 evidence showed `retry_in_seconds=1.000`, validating the positive retry floor;
- `storage_failures=0` throughout the accepted health samples;
- manual Polymarket service restart succeeded;
- the post-restart process returned connected and continued writing new readable shards.

The temporary test universe must not be treated as a production default. The accepted crosswalk is
now the only production identity source.

### Mapping-bounded production soak — still required

Populate `runtime.env` from the accepted crosswalk:

```text
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<accepted market/condition/token IDs, comma-separated>
```

Then install/restart the exact reviewed head:

```bash
cd <repo-root>
sudo bash scripts/install_runtime_services.sh

sudo systemctl is-enabled predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl show -p EnvironmentFiles predictions-cup-sig-capture
sudo systemctl show -p EnvironmentFiles predictions-cup-polymarket-capture

sudo journalctl -u predictions-cup-sig-capture -n 100 --no-pager
sudo journalctl -u predictions-cup-polymarket-capture -n 100 --no-pager
ls -lh data/sig_realtime.sqlite3 data/polymarket_operational.sqlite3
find data/polymarket_research -type f -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | tail -20
du -sh data/polymarket_research

sudo systemctl restart predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
```

For Polymarket acceptance, verify all of the following rather than process liveness alone:

- the logged/subscribed universe corresponds to the accepted mapping scope, not the 3,160-market
  heuristic;
- new Parquet shards continue appearing and total research bytes advance;
- `polymarket_operational.sqlite3` stays small and contains operational metadata/health rather
  than high-frequency research tables;
- a scheduled Gamma failure, if encountered, leaves the existing capture alive and a later refresh
  can recover;
- no rapid 429 retry burst occurs when `Retry-After` is zero/expired.

After that bounded soak is green, disconnect SSH, reconnect and repeat active/log/shard checks.
Then perform the operator-controlled reboot, reconnect, and verify both configured services return
and resume writing. `EnvironmentFiles` must show only the resolved `runtime.env`; neither unit
may source `trade.env`.

## Eventual operating expectations

BUILD-007 / PR #20 is accepted on `main` as the supervised-process and automatic-restart collector
layer. The ARM64 runtime/storage gate is live-validated. The remaining production claim is narrower: the mapping-bounded deployment still needs the final
paired soak plus SSH independence/reboot recovery run with accepted production IDs. Collector-native reconciliation and
journald visibility remain authoritative; BUILD-007 does not introduce a separate logging daemon or
trading service.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
