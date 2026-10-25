# Lane 4 — West/East production runtime audit (2026-10-25)

**Scope:** Read-only inspection on EC2 West and East at 00:27–00:37 UTC, plus installation and execution of the bounded read-only scorecard at `scripts/ops/production_scorecard.py`. Competition ends 2026-11-04. Do not confuse an audit sample with a load-test SLA.

## Live baseline and follow-up

| Item | West initial ~00:27 UTC | West follow-up ~00:36 UTC | East initial ~00:27 UTC | East follow-up ~00:36 UTC |
|---|---:|---:|---:|---:|
| vCPU | 2 | 2 | 2 | 2 |
| 1-minute load | 11.07 | 8.68 | 0.11 | 0.01 |
| Available RAM (MB) | 310 | 255 | 1094 | 670 |
| Root disk used | 94% | 94% | 100% | 100% |
| Root disk available | 5.3 GB | 5.1 GB | 244 KiB | 220 KiB |
| CPU PSI some/10s | 94.55% | 89.68% | 0.14% | 0% |
| IO PSI some/10s | 5.74% | 6.45% | 0% | 0% |
| Memory PSI some/10s | 1.08% | 34.56% | 0% | 0% |

These changes are *observational noise/competing jobs*, not attributable optimisation gains. **Recovered production resources: zero verified.**

## Critical processes and data

West: `predictions-cup-maker-live`, SIG capture, Polymarket capture and supervisor were all active. Maker observed at ~466 MB RSS, SIG capture ~208 MB, Polymarket capture ~127 MB, supervisor ~67 MB. A sample maker journal indicated account resync `snapshot_ms=3842.4`, `recovery_ms=21845.9`, `total_ms=25688.6`; another refresh cycle took 34487.371 ms, with background queue high-water 76. No inference that these are typical latencies.

East: read-only SIG capture active, Polymarket capture in a failure/restart loop (>1200 restarts), currently raising `sqlite3.OperationalError: unable to open database file`. Repeated automatic restart writes/reads while its root is 100% full. A direct attempt to stop the nonproduction services using Desktop Commander was rejected as 'Command not allowed'; do not assert they were stopped. Separate restart/disable authority is required. **Do not try alternate tools to bypass that control.**

No production trading services were restarted, disabled or reconfigured. No DB/WAL, risk state, execution journal, historical source or archive was deleted.

## Cold SQLite transfer — independently inspected

* East offline source: `data/capture001_acceptance_20260929/sig_realtime.sqlite3`, 42,641,457,152 bytes, source not removed and WAL recorded as empty.
* West destination: `/home/ec2-user/sig-history-archives/east-sig-realtime-20261024.sqlite3.zst`, 2,245,480,094 bytes; matching manifest with SHA-256 roundtrip and `verified: true`, `verified_utc: 2026-10-24T14:34:15Z`. It is a **completed** transfer, not merely initiated.
* West archive: `west-sig-realtime-20261024.sqlite3.zst`, about 3.8 GB, and a root-owned `.manifest.json` were listed. Manifest contents could not be read (EACCES); do **not** classify it as independently verified from this check alone.
* West *live* `data/sig_realtime.sqlite3` remains about 48 GB and is being written to. Do not conflate cold archive with live data or remove it.
* Neither server has comfortable retention headroom. No deletion or consolidation was undertaken.

## Working scorecard (West, ~00:35 UTC)

Run from repo root:

```bash
python3 scripts/ops/production_scorecard.py
```

This makes **zero API requests** and opens the RISK-002 singleton and BUILD-009 journal strictly read-only. It bounds the recent-event scan to 4,000 entries by default and rejects stale/untrusted account data.

Actual West sample: equity **101634.1 SUSQies**; session baseline 100000; realised P&L **-48.47**; unrealised P&L **+1682.57**; drawdown **527.170324**; gross exposure **99956 shares**; open-order exposure 100; uncertain order exposure 100. Account age 17.68 sec; account, marks, reconciliation trusted, but **strategy attribution incomplete**.

Deduplicated journal-associated fill-event activity: MAKE 98 events / 6,213 shares; TAKE 374 events / 85,953 shares. These are **not** strategy profits, unique venue fills necessarily, actual cash flow or turnover ratio; journal-dedupe coverage does not establish authoritative full fill coverage. Cash, trading fees, actual position turnover and strategy-level P&L intentionally output `null` until supported by authoritative transaction, cash, inventory, execution-side and mark datasets. There is no sound way to infer them from notional traded.

Recent last-4,000-event sample: 525 ACK, 523 CANCEL_ACK, 138 CANCEL_UNCERTAIN, 5 CANCEL_REJECTED, 3 REALTIME_FILL, 2 FILL_SUMMARY. Dispatch-to-ACK: p50 1676.254 ms, p95 4315.038 ms (521 matches); cancel-submit-to-ACK: p50 2229.171 ms, p95 5199.876 ms (522 matches). These timings are local monotonic observed journal events, not independently measured network response-time distributions, and are biased by join/availability.

## Ranked remaining bottlenecks

1. **P0 West compute and execution:** two vCPU, CPU PSI >80%, multi-second acknowledgement/cancellation samples; investigate CPU scheduling, polling/account refresh and cancellation uncertainty **without changing execution/risk decisions**. Owner: Lane 2+3 (execution) / Lane 4 (runtime).
2. **P0 East retention and failure loop:** 100% root volume and Polymarket capture endless retries; pause non-essential research services using an explicitly authorised control, then establish at least two durable verified copies before deleting historical raw data. Owner: Lane 4/operator.
3. **P1 West headroom:** 94% root volume with 48 GB live SIG SQLite plus verified cold archives; implement durable retention to separate storage, preserve WAL and recovery journals. Owner: Lane 4/operator.
4. **P1 Performance measurement coverage:** position cash, fees, full maker/taker realised attribution and executable liquidation require authoritatively reconciled fills, lots, transactions and marks. Owner: Lane 1+3.
5. **P1 Uncertain execution transitions:** 138 CANCEL_UNCERTAIN in a recent 4,000 event tail; reconcile IDs against authoritative orders, do not declare healthy on count alone. Owner: Lane 2+3.
6. **P2 HTTP logging pressure:** repetitive verbose `httpx` 200 OK lines and large query strings are visible; defer log-level changes until controlled maker restart/deployment, with direct order-path instrumentation preserved.

## Guardrails and handoff

No restart, deployment, enable/disable, change to strategy fair value, risk limit, maker/taker price or exposure. Do not delete large databases/archives as a workaround for disk pressure before trusted SHA-256 backups and ownership/access checks. Avoid `du` recursive scans and unbounded `journalctl` on West under CPU/IO pressure. A low-priority scorecard snapshot is safer than additional real-time per-market telemetry.

**Immediate cooperation:** Lane 1 supplies fresh canonical cash/positions/lots and P&L; Lane 2 examines quote/cancel churn and p95 ACK; Lane 3 confirms taker fill identity/fee accounting; Lane 4 handles permissions/retention and reprioritisation with explicitly authorized operator support.
