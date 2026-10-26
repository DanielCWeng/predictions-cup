# Israel election rehearsal: WATCH → APPROACH → GO-WATCH
**Started 26 October 2026 09:44 UTC; research only. Stops 28 October 2026 08:00 UTC.**

## Why
The SIG US election-night trading pipeline remains LIVE; historical SIG/PM capture services are deliberately inactive. We want to measure *how* a prediction market reprices around a major result, not predict a political winner or run another historical alpha training project. Israel's Knesset election on **27 October**, polls close at **22:00 Israeli time / 20:00 UK (GMT)**, allows a scoped dry run ahead of the US 3–4 November window.

## Active collector
- Source: `scripts/research/israel_election_rehearsal_collector.py` on branch `feat/catalyst-ingress`.
- Running independently of the live West maker as low-priority Python on **East** (migrated 26 October 10:24 UTC): `/home/ec2-user/catalyst-lane-isolated/israel_rehearsal_collector.py`. Does **not** import maker runtime, use authenticated SIG APIs, change risk limits, dispatch orders or restart any production service.
- Public Gamma GET at startup resolves 6 Polymarket YES asset IDs; public Polymarket CLOB market WebSocket subscribes to 6 outcomes across **Israeli Legislative Election Winner** (Likud, Yashar, Together) and **Prime Minister after the next election** (Benjamin Netanyahu, Gadi Eizenkot, Naftali Bennett). **These are two different settlement questions**, and NOT directly mapped to SIG US Cup contracts.
- WS records timestamped meaningful bid/ask quotes, provider timestamp vs receipt time, >50-share trade activity, market tick-size changes, eventual resolutions, price movements >=3 percentage points within 1min or >=6pp within 5min. WS application heartbeat PING each 10 seconds and bounded reconnection.
- Google News RSS for “Israel 2026 Knesset election results exit polls” is checked at **150s in WATCH**, **45s in APPROACH / GO-WATCH**. Records first-seen time, publisher-reported publish time, headline and link; bounded dedup. **Aggregator is not an independent, authenticated results source.** Two copies of one AP/Reuters dispatch do not constitute confirmation.
- Sources not usable as production evidence from West as of 26 October: official CEC site blocked/404 in network probe, and `cdn-votes26.bechirot.gov.il` visibly marked **סביבת בדיקות = test environment**, with simulated pre-election vote totals. NEVER label its mock totals as real. Public election-night reporting/exit polls also do not equal certified results.
- Log (bounded, rotated): `/home/ec2-user/catalyst-lane-isolated/israel_rehearsal_events.jsonl`; `.jsonl.1` previous segment. **Max ~12MB combined**, independent of disabled historical collection. Process log `israel_rehearsal.log`, status JSON `israel_rehearsal_status.json`.

## Timed transitions (all GMT/UK)
- WATCH: current time until **27 Oct 19:50**. Establish baseline bid/ask changes, spread, provider→receipt lag, headline arrival timing and trade activity.
- APPROACH: **19:50–20:00**. Higher-density news checks; websocket is already realtime (no rate change).
- GO-WATCH: **20:00–22:00**. Realistic exit-poll / early count observation window. An observed rapid price move is **NOT** a verified settlement probability or permission to trade. Alerts store `verified_catalyst=false, actionable=false`.
- POST-CLOSE: **22:00–28 Oct 08:00**. Follow post-exit-poll count and liquidity changes. Collector then shuts down automatically.

## Synthetic and live evidence
- Initial public Gamma API 2-event discovery and public WS subscription succeeded directly from West, then were repeated successfully from East after migration. Google News RSS returned XML. First baseline successfully captured **6 instruments** and 25 headlines. Unauthenticated official results were **not** available as a trusted producer.
- Deterministic collector tests: 4 passing: phase transition, invalid/crossed quote ignored, acceleration always observation-only, RSS de-dup/unverified and bounded tape rotation. A manual external WS smoke test received valid book messages.
- The separate `election_catalyst` research gate had previously passed 30 deterministic tests, but **remains undeployed and unconnected to authenticated live election reporting**.

## Rehearsal retrospective — no trade signals
Compare *after the fact*: time of first credible published news (where provable) vs RSS first sighting vs first meaningful bid/ask displacement; cumulative changes, spread widening, matched trade activity, book/rate interruptions and latency. Segment WATCH/APPROACH/GO-WATCH. Identify false starts and whipsaws; do not infer that an Israeli proportional election's probability patterns will transfer to US district elections. Treat the user/agent's judgment as independent; the tape proves timing and mechanics, not alpha or profits.

## Operator commands (RESEARCH ONLY)
Inspect current status: `cat /home/ec2-user/catalyst-lane-isolated/israel_rehearsal_status.json`
Inspect recent events: `tail -20 /home/ec2-user/catalyst-lane-isolated/israel_rehearsal_events.jsonl`
Find process: `pgrep -af israel_rehearsal_collector.py`
Stop the collector gracefully: `pkill -TERM -f '^/home/ec2-user/predictions-cup/.venv/bin/python -u /home/ec2-user/catalyst-lane-isolated/israel_rehearsal_collector.py$'` (not maker/supervisor).

The East collector's one active process holds a flock lock to prevent duplicate collectors; don't run it twice. On host restart the detached research process does **not** auto-start: check status and relaunch if needed. No live maker processes, inventory limits, or exchange write privileges are involved.

## Next release gate
Before any election-night signal is considered actionable, it still needs authenticated independent reporting, same-contract mapping, a defensible conservative fair-probability interval, fresh trusted SIG depth, available liquid capital, and existing taker/RISK-002/BUILD-009 approval. A market price move alone is not evidence that fresh independent information exists.

Sources:
- Israeli Central Elections Committee schedule: https://www.gov.il/en/pages/time--table-26
- Poll closing/exit-poll timing: https://www.abc.net.au/news/2026-10-23/israel-election-2026-explained/107287156
- Polymarket public CLOB websocket: https://github.com/Polymarket/agent-skills/blob/main/websocket.md
- Gamma market metadata: https://gamma-api.polymarket.com/docs

## Verified West → East cutover (26 October 2026)
- **East** current collector runs under `/home/ec2-user/catalyst-lane-isolated/` (public WebSocket + RSS only), confirmed receiving bids/asks and trade-activity messages, ~33 MB RSS, very low CPU, timed auto-stop 28 Oct 08:00 UTC. The West research collector stopped cleanly at **10:25:34 UTC**; West maker/supervisor stayed active.
- The full West baseline is archived on East: `israel_rehearsal_west_archive_20261026.jsonl` **494 records / 195245 bytes**. SHA-256 `e4e92868e6002c4337c13a99c30c7fabb80ad736adb65671c1a8ae8c5cf21563` matches the stopped West original exactly. East's current observations append separately to `israel_rehearsal_events.jsonl`. Preserve both files when analysing the transition; overlapping timestamps near cutover may contain duplicate observations.
- **Capacity concern:** East also runs the pre-existing SIG and Polymarket historical capture services, ~1.1 GB combined RSS; after rehearsal start, ~190 MB RAM available and **no swap**. Those services were left untouched; if memory declines, protect East stability and the collector's no-trade fail-closed behaviour. Do not restart old data collection on West.
- The historical log is retained on West as backup, but West no longer has a live Israel rehearsal process.
