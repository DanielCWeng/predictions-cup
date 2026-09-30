# Scripts

`install_runtime_services.sh` is the BUILD-007 EC2 systemd installer/updater for the accepted
read-only SIG collector and the mapping-bounded Polymarket candidate collector.

Run it from the repository checkout with:

```bash
sudo bash scripts/install_runtime_services.sh
```

It renders absolute runtime paths into the two templates under `deploy/systemd/`, validates the
`runtime.env` safety boundary, reloads systemd and manages only the services that are configured
to run.

SIG is always enabled/restarted by this installer. Polymarket is conditional:

- if `PREDICTIONS_CUP_POLYMARKET_CAPTURE_ENABLED` is false/unset, its installed unit is explicitly
  disabled/stopped;
- if it is true, `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS` must be non-empty and the service
  starts with `--require-explicit-universe`;
- if an old capture-enabled runtime.env lacks that new setting, the installer first
  `disable --now`s the existing Polymarket unit, then fails rather than leaving the old broad
  collector running;
- production IDs remain strict and explicit: generate them with
  `python -m predictions_cup.external.polymarket.supervised_universe`, which unions the accepted
  EXACT+DERIVED SIG ↔ Polymarket mapping tokens with the reviewed capture-only R3 structural-shadow
  YES-token allowlist; the broad election heuristic remains forbidden for the supervised service.

The installer never sources or overwrites `trade.env`, never prints credential values, and does
not delete capture data. BUILD-007's Polymarket high-frequency research data is written to ZSTD
Parquet under `data/polymarket_research/`; its fresh operational SQLite is
`data/polymarket_operational.sqlite3`.

See `OPERATIONS.md` for prerequisites, update commands, storage-soak evidence and the EC2
validation runbook.
