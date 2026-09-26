# Operations

## Current state

- No production deployment exists.
- No production trading daemon exists.
- Normal application startup remains finite, network-free and non-trading.
- The accepted repository includes a separate, explicitly invoked public read-only Polymarket research recorder.
- BUILD-006 SIG live REST governance/tracked-depth correction is accepted on `main`; its accepted 60-second credentialed smoke passed at the merged head.
- BUILD-007 / PR #20 is an in-review read-only systemd supervision layer; it is not accepted or live-EC2 validated yet.

The recorder uses local SQLite/WAL append storage, idempotent market metadata upserts, invalidation plus authoritative REST reseeding after reconnect, and explicit feed/book/trade/storage health clocks. These are experimental capture properties, not production trading/recovery guarantees.

## EXPERIMENT-001A operation

Run only when intentionally enabled:

```bash
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true \
python -m predictions_cup.external.polymarket.recorder
```

The recorder writes a lean 1-second scalar panel, normalized event-time book changes and public trade events. Top-20 depth snapshots default to every 60 seconds rather than every second. Feed receive/PONG liveness is bounded; an unhealthy connection is closed and the existing reconnect path invalidates books and REST-reseeds them before accepting new deltas.

The database path defaults to `data/polymarket_capture.sqlite3`, which is ignored by Git. Storage failures surface instead of being silently ignored.

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

## EC2 collector runbook — BUILD-007 candidate

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
<repo-root>/.venv/bin/python -m predictions_cup.external.polymarket.recorder --runtime-env-only
```

Set `PREDICTIONS_CUP_PYTHON` only if the EC2 runtime intentionally uses a different Python.
The SIG command does not contain any tracked exchange ID. Tracked depth remains external runtime
configuration through:

```text
PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS=
```

in `runtime.env`. Empty/unset means no tracked full depth. When explicitly needed, supply a
comma-separated set of exchange IDs in that external file; the installed unit remains unchanged.

The runtime environment must also provide a non-empty
`PREDICTIONS_CUP_SIG_READ_CREDENTIAL` and `PREDICTIONS_CUP_TOURNAMENT_ID`, keep
`PREDICTIONS_CUP_TRADING_ENABLED=false` (or omit it), and set
`PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true`.

### Install / update

From the repository checkout:

```bash
git pull --ff-only
python -m pip install -e '.[dev]'
sudo bash scripts/install_runtime_services.sh
```

The installer is idempotent: it re-renders/copies the same two units, runs `daemon-reload`,
enables both services, restarts both, verifies they are active and returns non-zero if restart or
active-state verification fails. It never overwrites `runtime.env`, never deletes capture data,
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
data/polymarket_capture.sqlite3
```

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

### Conservative live EC2 validation — rerun required

Live validation has progressed in two stages. The SIG service/environment side is healthy. After
the first Gamma retry/backoff correction, Polymarket successfully discovered 3,160 markets / 6,320
tokens, connected the WebSocket, wrote 6,320-row snapshots, recovered from a WebSocket disconnect,
and reported zero storage failures. A later scheduled Gamma refresh then exhausted 429 retries and
tore down the recorder TaskGroup. The branch now keeps an established universe/capture alive across
that periodic metadata failure and floors zero/expired Retry-After values. Rerun the exact head and
confirm the service remains active through a scheduled Gamma failure/recovery window and that the
SQLite output continues advancing before continuing with SSH-disconnect and reboot checks.

An operator can validate without any trading action:

```bash
cd <repo-root>
sudo bash scripts/install_runtime_services.sh

sudo systemctl is-enabled predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl show -p EnvironmentFiles predictions-cup-sig-capture
sudo systemctl show -p EnvironmentFiles predictions-cup-polymarket-capture

sudo journalctl -u predictions-cup-sig-capture -n 100 --no-pager
sudo journalctl -u predictions-cup-polymarket-capture -n 100 --no-pager
ls -lh data/sig_realtime.sqlite3 data/polymarket_capture.sqlite3

sudo systemctl restart predictions-cup-sig-capture predictions-cup-polymarket-capture
sudo systemctl is-active predictions-cup-sig-capture predictions-cup-polymarket-capture
```

Disconnect SSH, reconnect, and repeat the active/log checks to confirm the collectors are
independent of the shell session. For reboot validation, use an operator-controlled
`sudo reboot`, reconnect after the host returns, then repeat `is-enabled`, `is-active`,
journal and data-file checks. `EnvironmentFiles` must show only the resolved `runtime.env`
path; neither unit should show or source `trade.env`.

## Eventual operating expectations

BUILD-007 / PR #20 implements the supervised-process, automatic-restart and SSH-independent
collector layer on its branch. Until it is accepted and the EC2 smoke/reboot sequence is actually
run, those properties remain candidate deployment capability rather than a live-validated claim.
Collector-native reconciliation and journald visibility remain authoritative; BUILD-007 does not
introduce a separate logging daemon or trading service.

## Repository state discipline

- Every implementation branch starts from current `main`.
- Every accepted merge updates canonical project state.
- Builders do not merge their own PRs.
- Active branch state must not be described as merged functionality.
- GitHub merge state and actual `main` contents outrank stale documentation.
