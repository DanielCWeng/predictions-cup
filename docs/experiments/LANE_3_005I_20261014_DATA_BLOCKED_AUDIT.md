# Frozen 005I — executable economic gate | 14 October 2026

**Verdict: DATA BLOCKED. Promotion: NO.** This is a read-only SOURCE-READINESS and measurement audit, **not** a 005I executable return or an E4/E5 replay. Exact frozen 005I / OFI coefficients, 6-minute causal history, 5-minute reversal definition, 2026-09-01–10 TRAIN / Sep 11–20 DEV / Sep 21–28 sealed HOLDOUT, accepted EXACT mapping and one-SIG-exchange YES/NO complement remain unchanged. No refit, new orders, extra SIG calls, capture activation, service changes or DB/WAL writes. The active West deploy remained `fb4f6c66`, not the merged recorder source.

## Decision-critical measured denominator

West read-only SQLite `mode=ro; PRAGMA query_only=ON`, inspected at **2026-10-14 20:01–20:02 UTC**. Current mapping and acceptance bytes were SHA-256 verified against their acceptance manifest. Source files: `data/sig_realtime.sqlite3`, `data/004c_c_crossvenue/polymarket_operational.sqlite3`, `data/004c_c_crossvenue/polymarket_research/`.

| Step | Count | Interpretation |
| --- | ---: | --- |
| Frozen historical 005I reversal | 55,340 at 67.26057102% | Dependent holdout PM state samples; **not a prospective economic denominator** |
| Mapping records | 237 | Checked-in SIG crosswalk |
| VERIFIED, accepted EXACT pairs | **140** | 140 unique SIG exchanges / mapped PM tokens. Other 97 records are not EXACT |
| PM subscribed tokens / markets | 140 / 140 | Collector health, not continuity proof |
| PM full-depth Parquet snapshot shards | 4,245 | File count, **not** 4,245 independent usable market episodes |
| PM book-change Parquet shards | 5,711 | File count, not independently accepted deltas |
| PM cumulative uninitialised book deltas | **25,098,926** | Collector's latest counter; seed/epoch/gap validity uncertified |
| SIG `book_observations` | **0 rows** | No captured SIG full-depth records on active collector |
| SIG tracked depth / untracked depth exchanges | **0 / 237** | Latest capture health; `full_book_refresh_count=0` |
| Prospectively frozen qualifying PM signals | **UNKNOWN** | No independently reconciled continuity and completed-minute feature tape |
| Source-certified SIG/PM entry pairs | **0** | **Source-capability zero**, not a measured absence of true opportunities |
| Trusted post-latency arrival / exit depth | **0 / 0** | No admissible SIG snapshots for entry or exits |
| Actual 005I fill probability, net SUSQies, capacity, lower bound | **NOT ESTIMABLE** | Not zero and certainly not positive |

The PM full-depth shard counts by collector day were Oct 11: 277, Oct 12: 1,405, Oct 13: 1,398, Oct 14 through approximately 20:01 UTC: 1,165. **No per-day market/event signal counts can be honestly inferred from shard counts.** An important distinction: #220's nine prioritised screening IDs are a *subset* of the 140 accepted EXACT mappings; they are not the entire research universe. Acceptance SHA `fa59331a45897f7971454835e86f67a65313a2efff8ca387a3b2cf1ea99b19e5`; mapping SHA `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`.

SIG health at `2026-10-14T20:01:52.899918+00:00` reported `tracked_depth_exchange_count=0`, `untracked_depth_exchange_count=237`, `tracked_trusted_count=0`, no full/ bounded book refresh, and an active scalar price collector. PM health at `2026-10-14T20:01:47.381387+00:00` reported 140 tokens, a connected websocket and 25,098,926 orphan/uninitialised deltas. Existing PM shards contain book ladders; this does **not** authenticate their seed/epoch continuity. SIG's default-OFF maker mirror has no native exchange book event clock/revision even if activated, so locally received books cannot silently graduate to trusted fast cross-venue execution.

## Frozen economic scorecard — all four whole-lot sizes

| Quantity | Decision marginal/VWAP, IOC limit | Independently observed latency-arrival fillable depth | 5s / 30s / 5m / 1h net liquidations | 1x / 2x / 5x slippage, fees, partial/locked / zero-recovery | Clustered 95% lower bound |
| ---: | --- | --- | --- | --- | --- |
| 1 share | UNKNOWN | UNKNOWN | NOT ESTIMABLE | NOT ESTIMABLE | NOT ESTIMABLE |
| 5 shares | UNKNOWN | UNKNOWN | NOT ESTIMABLE | NOT ESTIMABLE | NOT ESTIMABLE |
| 10 shares | UNKNOWN | UNKNOWN | NOT ESTIMABLE | NOT ESTIMABLE | NOT ESTIMABLE |
| 20 shares | UNKNOWN | UNKNOWN | NOT ESTIMABLE | NOT ESTIMABLE | NOT ESTIMABLE |

The admissible *formula* for a buy of `q` with legal marginal limit `L` and entry fee fraction `f` is a zero-recovery downside of up to `-q×L×(1+f)`, before any additional obligations. No values of `L` or independently verified `f` were supplied by the certified tape. A touch is **not** an actual fill; no passive quote is a calibrated fill probability. Absence of authenticated ACK, fee schedule, queue conditions, capital headroom and post-latency exit prevents economic inference.

The unchanged frozen controls are **NO_TRADE = 0 incremental new-order SUSQies by definition**; direct PM FV, SIG momentum, unconditional reversal, opposite sign, frozen OFI baseline, random times all have **NULL matched economic outcomes**. A PM midpoint is not SIG executable fair value. Comparison on common event/day clusters is **NOT ESTIMABLE**. Preregistered floor stays **5 independent days, 10 independent events, 100 disjoint certified signals**, plus independent fill/economics evidence; all three certified counts are 0 under the available source, not proof that markets offered no opportunities.

## Read-only reproduction

Use the approved host only; no SIG/PM API, writes or restarts. A standard-library Python snippet reproduces the two key authoritative source gates (the live `mode=ro` connection reads the existing committed database+WAL without requesting a writable handle):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import json, sqlite3, hashlib, pathlib
d=pathlib.Path('/home/ec2-user/predictions-cup/data')
for rel,table,time_col,json_col in (
 ('sig_realtime.sqlite3','capture_health','observed_at','payload_json'),
 ('004c_c_crossvenue/polymarket_operational.sqlite3','ingestion_health','recorded_at','payload_json'),
):
 db=sqlite3.connect('file:'+str(d/rel)+'?mode=ro',uri=True,timeout=1)
 db.execute('PRAGMA query_only=ON')
 if table=='capture_health':
  print('sig_book_rows',db.execute('SELECT count(*) FROM book_observations').fetchone()[0])
 at,payload=db.execute(f'SELECT {time_col},{json_col} FROM {table} ORDER BY id DESC LIMIT 1').fetchone()
 obj=json.loads(payload)
 print(rel,at,{k:obj.get(k) for k in (
  'tracked_depth_exchange_count','untracked_depth_exchange_count',
  'full_book_refresh_count','book_uninitialized_delta_count',
  'tokens_subscribed','reconnect_count') if k in obj})
 db.close()
for name in ('sig_polymarket_2026.json','sig_polymarket_2026_acceptance.json'):
 p=d/'mappings'/name
 print(name,hashlib.sha256(p.read_bytes()).hexdigest())
doc=json.loads((d/'mappings/sig_polymarket_2026.json').read_text())
print('mapping_total',len(doc['records']))
print('verified_exact',sum(r.get('mapping_class')=='EXACT' and r.get('status')=='VERIFIED' for r in doc['records']))
PY
```

The **frozen coefficients**, run splits and historical directional metrics can be reproduced from `data/experiments/experiment_005i/OFI_TRAIN_MODEL_FREEZE.json`, `PRE_HOLDOUT_FREEZE.json`, `HOLDOUT_RESULTS.json`. Existing draft #209 corrects YES/NO on the same exchange; stacked #214 corrects VWAP-as-IOC-limit and worst-case `q×limit+fees` reserves; #223 preserves untrusted source rejection. These are important mechanical repairs, **not actual trading success**, and remain draft/not promoted.

## Decision and alternative test

**DATA BLOCKED — no frozen-005I profit claim.** There is insufficient authoritative SIG decision depth, post-latency arrival/exit depth, independently continuous PM seed/epoch, 005I causal feature parity, actual terminal fill/fee/latency calibration and available capital for any positive after-cost bound. Do not bypass source trust to hit a deadline. A **slower 30-minute/one-hour scalar-price and PM-reference directional falsification** can be tested with existing data, independently timestamping completed features and purging overlap; it can assess predictiveness or rule out an idea, **not** demonstrate executable SUSQies without full-depth or independently bounded real fills. Prioritise #223 historic authenticated fill economics and #225 inventory/release headroom instead of inventing 005I trades.

Research-only #205/#201 handoff. Keep #207 LIVE STOP-SHIP unchanged; zero venue orders were placed by this audit.
