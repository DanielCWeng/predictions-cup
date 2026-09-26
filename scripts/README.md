# Scripts

`install_runtime_services.sh` is the BUILD-007 EC2 systemd installer/updater for the accepted
read-only SIG and Polymarket collectors.

Run it from the repository checkout with:

```bash
sudo bash scripts/install_runtime_services.sh
```

It renders absolute runtime paths into the two templates under `deploy/systemd/`, validates the
`runtime.env` safety boundary, reloads/enables/restarts systemd services and verifies active
state. It never sources or overwrites `trade.env`, never prints credential values, and does not
delete capture data.

See `OPERATIONS.md` for prerequisites, update commands and the EC2 validation runbook.
