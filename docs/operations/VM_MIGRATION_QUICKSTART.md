# VM migration / replacement-host quickstart

This is the operator handoff for moving the Predictions Cup read-only launch collectors to a new VM.
It is deliberately explicit so a cold-start agent does not need prior chat history.

The capture services are **read-only**. Keep trading disabled throughout this procedure. The
repository's rendered systemd units embed absolute paths, so **do not copy the old
`/etc/systemd/system/predictions-cup-*.service` files to a new VM**. Re-run
`scripts/install_runtime_services.sh` on the destination host.

## 0. Source-of-truth rules

- Deploy the commit/ref currently accepted by MASTER/review. Do not blindly reuse an old SHA just
  because it appears in this document. CAPTURE-001's final production acceptance happened on
  `0d9ef9eb7cadc123d7719d3b35db49d631d04d81`.
- Canonical SIG ↔ Polymarket identity is
  `data/mappings/sig_polymarket_2026.json`.
- Read-only runtime configuration lives outside Git at
  `~/.config/predictions-cup/runtime.env`.
- `trade.env` is separate. Never source or copy its trade credential into the capture runtime.
- The systemd installer is the authority for rendering absolute repo/home/Python paths:
  `scripts/install_runtime_services.sh`.
- Remote Desktop Commander is **out-of-band** from the collectors. Its failure does not imply the
  collectors failed, and collector health does not imply the Remote Desktop Commander agent is
  reachable.

## 1. Record the old host before moving

On the existing host:

```bash
cd /home/ec2-user/predictions-cup   # or the actual repo root
git rev-parse HEAD

sudo systemctl is-enabled \
  predictions-cup-sig-capture \
  predictions-cup-polymarket-capture
sudo systemctl is-active \
  predictions-cup-sig-capture \
  predictions-cup-polymarket-capture
sudo systemctl show -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
  predictions-cup-sig-capture
sudo systemctl show -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
  predictions-cup-polymarket-capture

grep -E '^(PREDICTIONS_CUP_(TOURNAMENT_ID|SIG_REALTIME_STORAGE_PATH|SIG_RESEARCH_PATH|SIG_REALTIME_TRACKED_EXCHANGE_IDS|POLYMARKET_CAPTURE_ENABLED|POLYMARKET_STORAGE_PATH|POLYMARKET_RESEARCH_PATH|TRADING_ENABLED|EXECUTION_MODE|GLOBAL_KILL_SWITCH))=' \
  ~/.config/predictions-cup/runtime.env
```

Do **not** print credentials into tickets, PRs, logs or chat.

If historical capture continuity matters, record the current data roots and free disk space:

```bash
df -h /
du -sh data/* 2>/dev/null | sort -h
```

The safest byte-for-byte migration of operational SQLite is to stop both collectors before the
final copy so SQLite and WAL are moved consistently. Immutable published Parquet shards can be
copied independently; an unpublished in-memory shard may be lost on hard shutdown by design.

## 2. Prepare the destination VM

Clone the repo and install the project in a local virtual environment:

```bash
git clone <predictions-cup-repository-url> predictions-cup
cd predictions-cup
git fetch --all --prune
git checkout <accepted-ref-or-sha>

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

The deployment is path-independent: `/home/ec2-user/predictions-cup` is the current launch-host
path, not a hard requirement. The installer renders the destination VM's actual absolute path into
the units.

Create the runtime-config location with the required permissions:

```bash
install -d -m 700 ~/.config/predictions-cup
# Securely copy runtime.env from the old host or secret store.
chmod 600 ~/.config/predictions-cup/runtime.env
```

Required safety properties:

```text
PREDICTIONS_CUP_SIG_READ_CREDENTIAL=<read-only credential>
PREDICTIONS_CUP_TOURNAMENT_ID=bda92870-621e-47b0-bc3c-3602c5c26f55
PREDICTIONS_CUP_TRADING_ENABLED=false
PREDICTIONS_CUP_EXECUTION_MODE=SHADOW
PREDICTIONS_CUP_GLOBAL_KILL_SWITCH=true
PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED=true
```

`runtime.env` must **not** contain `PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL`.

Set explicit storage paths suitable for the destination disk. Relative paths are resolved under the
repo's systemd `WorkingDirectory`:

```text
PREDICTIONS_CUP_SIG_REALTIME_STORAGE_PATH=data/sig_realtime.sqlite3
PREDICTIONS_CUP_SIG_RESEARCH_PATH=data/launch_20261001/sig
PREDICTIONS_CUP_POLYMARKET_STORAGE_PATH=data/polymarket_operational.sqlite3
PREDICTIONS_CUP_POLYMARKET_RESEARCH_PATH=data/launch_20261001/polymarket
```

If a persistent/data volume is mounted elsewhere, prefer explicit absolute paths rather than
filling a small root disk.

## 3. Rebuild the accepted supervised Polymarket universe

Production capture intentionally uses the accepted **EXACT + DERIVED mapped-token** universe. It
does not subscribe to the four `NEAR` contracts.

Generate the current canonical token list from the checked-out mapping rather than pasting a stale
list from chat:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

mapping = json.loads(Path("data/mappings/sig_polymarket_2026.json").read_text())
tokens = set()

for record in mapping["records"]:
    if record["mapping_class"] not in {"EXACT", "DERIVED"}:
        continue
    legs = []
    if record.get("direct_polymarket"):
        legs.append(record["direct_polymarket"])
    legs.extend(record.get("polymarket_components") or [])
    for leg in legs:
        token = leg.get("mapped_token_id")
        if token:
            tokens.add(token)

print(f"supervised_token_count={len(tokens)}")
print(",".join(sorted(tokens)))
PY
```

At the 29 September 2026 accepted mapping this resolves to **689 tokens**: 140 EXACT direct tokens
plus 549 DERIVED component tokens. If the mapping changes later, the checked-out artifact outranks
this historical count.

Place the resulting comma-separated IDs in:

```text
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<canonical generated list>
```

The Polymarket service runs with `--require-explicit-universe` and must fail closed if configured
IDs cannot be resolved.

## 4. Install the two systemd collectors

From the destination checkout:

```bash
sudo bash scripts/install_runtime_services.sh
```

The installer:

- resolves the runtime user/home/repo/Python to absolute paths;
- refuses a trade credential in `runtime.env`;
- refuses `PREDICTIONS_CUP_TRADING_ENABLED=true`;
- requires the SIG read credential and tournament ID;
- requires an explicit supervised Polymarket universe when PM capture is enabled;
- installs/enables the configured collectors and restarts them;
- does not overwrite `runtime.env` or delete capture data.

Verify rendered paths rather than assuming the old VM layout:

```bash
sudo systemctl show -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
  predictions-cup-sig-capture
sudo systemctl show -p WorkingDirectory -p ExecStart -p EnvironmentFiles \
  predictions-cup-polymarket-capture

sudo systemctl is-enabled \
  predictions-cup-sig-capture \
  predictions-cup-polymarket-capture
sudo systemctl is-active \
  predictions-cup-sig-capture \
  predictions-cup-polymarket-capture
```

## 5. Cold-start expectations

SIG should discover the full Cup tournament:

- `market_count=237`
- `known_exchange_count=237`
- `dropped_rows=0`
- `storage_failures=0`
- bounded research queue/high-water
- `rest_429_count=0` under normal conditions

Do not enable full tracked depth for all 237 exchanges. Empty
`PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS` still gives tournament-wide Realtime plus
broad scalar/BBO capture.

Polymarket strict-universe startup is comparatively expensive. On the accepted 689-token launch
host it took roughly four minutes to complete Gamma discovery/book seeding before the first health
line. Do not redesign or kill it merely because the process is quiet during that normal seed
window. Confirm that it remains CPU/network active and then expect:

- `markets_subscribed=689`
- `tokens_subscribed=689`
- `websocket_connected=true`
- `storage_failures=0`

Check logs:

```bash
sudo journalctl -u predictions-cup-sig-capture -n 100 --no-pager
sudo journalctl -u predictions-cup-polymarket-capture -n 100 --no-pager
```

Check that immutable shards are publishing:

```bash
find data -type f -name '*.parquet' -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | tail -30
```

## 6. Migration acceptance on the new VM

Before declaring the replacement host production-ready:

1. Run SIG and mapped PM together through the installed systemd units.
2. Verify SIG is 237/237 and PM is the explicit accepted supervised universe.
3. Verify zero dropped rows/storage failures and sane queue/WAL/disk growth.
4. Open/read at least one published Parquet shard from both venues.
5. Restart both collector services and verify old shards remain intact and new shards publish.
6. Disconnect/reconnect SSH; collectors must be independent of the shell session.
7. Reboot the VM; both units must return automatically after `network-online.target`.
8. Run the first-hours report against the new capture roots.

Example:

```bash
.venv/bin/python -m predictions_cup.analysis.first_hours \
  --input data/launch_20261001/sig \
  --polymarket-root data/launch_20261001/polymarket \
  --mapping data/mappings/sig_polymarket_2026.json \
  --output data/launch_20261001/first_hours
```

A genuine SIG `market_batch`, when available, should appear in `raw_events` with coherent
normalized evidence. Never manufacture activity just to satisfy a quiet pre-launch gate.

## 7. Remote Desktop Commander survives VM moves separately

Remote Desktop Commander is **not installed by the Predictions Cup collector installer**.

The current launch host demonstrated why this matters: the collectors auto-recovered after reboot
while the ChatGPT-visible remote-control agent required separate restoration. Therefore a VM move
must explicitly set up the desired Remote Desktop Commander account as a boot service.

Important account rule: the Remote Desktop Commander identity/authentication is tied to its
`HOME`. If you use multiple RDC accounts, give each one a separate home directory and systemd
service. An `rdc-alt.service` authenticated to an alternate account does not automatically make
the primary ChatGPT/RDC account reachable.

The current working service pattern is:

```ini
[Unit]
Description=Desktop Commander Remote MCP (<account label>)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ec2-user
Group=ec2-user
WorkingDirectory=/home/ec2-user/<rdc-home>
Environment=HOME=/home/ec2-user/<rdc-home>
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/npx --yes @wonderwhy-er/desktop-commander@<tested-version> remote
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
```

Authenticate the desired RDC account under that exact `HOME` first, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rdc-<account>.service
sudo systemctl is-enabled rdc-<account>.service
sudo systemctl is-active rdc-<account>.service
```

Finally perform a reboot and verify **both** layers independently:

```bash
sudo systemctl is-active predictions-cup-sig-capture
sudo systemctl is-active predictions-cup-polymarket-capture
sudo systemctl is-active rdc-<account>.service
```

Do not infer collector failure from loss of the RDC control channel; reconnect through another
authorized path and inspect systemd/journald directly.
