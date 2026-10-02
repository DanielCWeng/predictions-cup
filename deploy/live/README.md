# LIVE MAKE deploy configuration (host-only until 2026-10-01)

These files mirror what runs on `predictions-cup-west1`. None of them contain secrets: credentials stay
in `~/.config/predictions-cup/{runtime,trade}.env` on the host.

| File | Host path | Purpose |
|---|---|---|
| `live_band.env` | `~/fs002_stage/live_band.env` | Current LIVE MAKE: EXACT markets with fair value in 0.20–0.80, 50/side, RISK caps sized for about 100 markets |
| `sig_polymarket_2026_band.json` | `~/fs002_stage/` | Mapping subset for the band universe, built 2026-10-01 20:00Z from Polymarket CLOB midpoints |
| `canary.env`, `canary960.json` | `~/fs002_stage/` | Single-market LIVE canary (size 10, caps 25) |
| `systemd/*.conf` | `/etc/systemd/system/predictions-cup-maker.service.d/` | SHADOW-only drop-ins for the installed maker unit (kill switch on) |
| `polymarket-capture-exact.conf` | `/etc/systemd/system/predictions-cup-polymarket-capture.service.d/50-exact.conf` | Capture only the direct Polymarket token for each EXACT SIG mapping; trims the capture websocket and book cache |

LIVE runs as a transient unit:

```
sudo -n systemd-run --unit=predictions-cup-maker-live --uid=ec2-user --gid=ec2-user \
  --working-directory=/home/ec2-user/predictions-cup \
  -p EnvironmentFile=/home/ec2-user/.config/predictions-cup/runtime.env \
  -p EnvironmentFile=/home/ec2-user/.config/predictions-cup/trade.env \
  -p EnvironmentFile=/home/ec2-user/fs002_stage/live_band.env \
  -p MemoryMax=320M -p OOMScoreAdjust=800 -p KillSignal=SIGTERM -p TimeoutStopSec=90 \
  -p Restart=on-failure -p RestartSec=5s \
  /home/ec2-user/predictions-cup/.venv/bin/python -m predictions_cup.maker --runtime-env-only --live
```

An internal MAKE state failure first latches the process kill switch and attempts a bounded
best-effort cancel drain, then exits non-zero. `Restart=on-failure` starts a new process; the
kill switch is never cleared in the failing process, and startup recovery/interlocks still apply.

Stop it with `sudo -n systemctl stop predictions-cup-maker-live`. SIGTERM lets the active one-exchange
batch finish, then attempts the existing cancellation drain within a total 15-second budget. A
request cancelled at the bound is journaled UNCERTAIN for startup recovery. Startup reads each
acknowledged order and its fills, cancels resting orders owned by unresolved placements, and resolves
them only after closed-order and complete-fill evidence. Transient 429/503 failures receive bounded
backoff. If evidence is still unavailable, MAKE starts with those exchange IDs blocked and continues
other markets. `scripts/ops/recover_one_operation.py <logical_operation_id>` remains a read-only
inspection/reconciliation tool: it never resends an acknowledged placement, and uses `/orders?status=all`
and `/portfolio/fills` when individual order projections return 503.
