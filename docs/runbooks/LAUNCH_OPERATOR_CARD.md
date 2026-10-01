# Launch operator card — FULLSTACK-002

Safe SHADOW composition only. No FULLSTACK-002 command authorizes LIVE trading.

```bash
# status
scripts/cupctl status --json
scripts/cupctl status --json --write

# start configured safe stack
scripts/cupctl start-shadow

# global halt: stop MAKE first, then latch existing RISK-002 state
scripts/cupctl halt --reason operator_global_halt

# flatten
scripts/cupctl flatten          # NOT_READY on this frozen base

# stop / restart safe composition
scripts/cupctl stop
scripts/cupctl restart

# capture-only rollback
sudo systemctl stop predictions-cup-maker.service
sudo systemctl restart predictions-cup-sig-capture.service

# logs / durable status and alerts
journalctl -u 'predictions-cup-*' -n 100 --no-pager
cat data/runtime/status/latest.json
tail -n 50 data/runtime/alerts/events.jsonl

# optional out-of-band push delivery (runtime.env only; never commit the URL)
# PREDICTIONS_CUP_FULLSTACK_ALERT_WEBHOOK_URL=https://your-alert-endpoint.example/path

# existing durable risk state
python scripts/risk002_control.py --runtime-env-only status

# local no-order rehearsal
scripts/cupctl rehearse
```

LIVE enable/disable: **NOT_READY** on the frozen FULLSTACK-002 base. Do not hand-edit the safe maker unit to add `--live`.

Risk state defaults to `data/risk_002.sqlite3`; execution recovery state defaults to `data/execution_journal.sqlite3`.

**DO NOT DELETE** risk SQLite/WAL, execution journal/WAL, capture stores, SHADOW journal, LIVE-LEARN outputs, canonical status history/alerts, or reconciliation evidence during an incident.
