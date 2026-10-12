# Lane B — first prospective PM-only 18-second read-only microproof

**Capture window:** 13 October 2026 just after 00:20 UK time (12 October UTC evening). The host command ran for 26.24s inclusive of setup, targeting 18s of websocket capture. This is **not** a 30–60min or 24-hour study.

## Raw artifact

- West host isolated path: `/home/ec2-user/lane_b_pm_20261013/pm_microproof_001_raw.jsonl`
- **Verified SHA-256 of bytes on disk:** `f826ae1deb68d544901622aa9494df9ffc7b7826abb9b6e332595b46ecd98ad9`
- Length 38,547 bytes; 40 JSONL records total.
- No modification to the existing PM/SIG SQLite/Parquet capture; no systemd restart; no SIG REST calls, no SIG order/fill action.
- Six subscribed mapped token IDs from accepted EXACT mapping: SIG exchanges **947,948,957,958,959,960** (Georgia, Louisiana and Maine Senate).

## Measured receiver observations

| Recorded event | Count |
|---|---:|
| WS CONNECTED boundary | 1 |
| WS DISCONNECTED boundary | 1 |
| Full `book` snapshots for mapped subscribed tokens | 6 (6/6 initialised) |
| Received `price_change` envelopes | 32 |
| Delta entries applied to subscribed mapped tokens | 32 |
| Opposite-outcome token delta entries ignored as uninitialised | **32**, spanning 4 distinct opposite token IDs |
| Invalid/crossed/one-sided reconstructed snapshots in this sample | 0 observed |
| Source-timestamped frames | 38 |
| Source-to-local-receipt sample p50 | 97.39 ms |
| Source-to-local-receipt sample p99 | 13,384.68 ms |
| Samples >500ms / >5000ms | 6 / 5 |

**Interpretation:** The isolated listener subscribed to just the mapped tokens but observed deltas on opposite tokens. This supports a *specific mechanism* for harmless ignored-delta inflation, and shows the original recorder code can count ignored deltas while retaining populated subscribed books. It **cannot prove** the unknown breakdown of its historic cumulative >10m count without records from that actual collector. No source-sequence continuity was available in the observed payload and no claim of 100% lossless token depth is justified.

## Valid paired-depth economics scorecard

| Gate | Result |
|---|---|
| SIG timestamp-valid independent full depth in sampled current canonical collector | **0** |
| PM fresh/full depth with auditable seed and same-epoch local connectivity | 6 initially seeded in this short isolated listener |
| PM independent source sequence continuity | **NOT VERIFIED** |
| SIG/PM paired trusted full depth at a decision clock | **0 supported by available empirical evidence** |
| Empirical executable opportunity count | **NOT MEASURED** |
| Observed prospective SIG fills | **0 research fills; none were attempted** |
| Net PnL / calibrated fill probability | **NOT MEASURED** |
| Live activation | **FORBIDDEN** |

Source-to-receipt p99 includes initial snapshots carrying older source timestamps and is **not** an independently calibrated clock-sync/transport-delay statistic. Report separate source event time and receipt time where supplied, retain source timestamp absence instead of substitution, and do not infer low-latency alpha from p50 alone.

## Required next quality proof

The next sample must preserve per-token reconnect epochs, seed times, missed frame detection where possible, new full snapshots after reconnect and the complete **rejection denominator**. Independently establish a single host-wide account read budget before sending a SIG depth REST request; current maker/capture do not share one. PR #222 ships a default-deny SIG full-depth adapter that will refuse to run with current West shared-budget settings. This is intentional. Extend to 30–60min only after resource and safety review, then to 24h only with strict no-trade accounting and #207 independent trust approval.
