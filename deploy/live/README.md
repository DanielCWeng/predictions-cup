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
  -p MemoryMax=320M -p OOMScoreAdjust=800 -p KillSignal=SIGTERM -p TimeoutStopSec=60 \
  /home/ec2-user/predictions-cup/.venv/bin/python -m predictions_cup.maker --runtime-env-only --live
```

Stop it with `sudo -n systemctl stop predictions-cup-maker-live`. A SIGTERM during an order batch can
leave an unresolved journal operation that blocks the next start. Resolve it with
`scripts/ops/recover_one_operation.py <logical_operation_id>`. It replays the same idempotency key, so
SIG returns the stored response and no new order is placed.
