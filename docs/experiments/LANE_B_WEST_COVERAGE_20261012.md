# LANE B — West read-only data coverage checkpoint (2026-10-12 UTC)

**Evidence class:** on-host read-only audit of the already-running West collector and operational Parquet/SQLite. **No new SIG or PM API call, no order, service restart, environment change, WAL mutation, or capture writer.** Observations are snapshots, not a 24-hour prospective study.

## Host / process status

- Host: `ip-172-31-19-67.us-west-1.compute.internal`, read-only Desktop Commander connection.
- Root volume: 80 GB total, 29 GB used, 52 GB available (36% used); XFS/EBS expansion state from #199 unchanged.
- `predictions-cup-sig-capture.service`: ACTIVE.
- `predictions-cup-polymarket-capture.service`: ACTIVE.
- `predictions-cup-maker-live.service`: ACTIVE; **never touched**.
- Canonical working tree `/home/ec2-user/predictions-cup` head `fb4f6c66`, 1 pre-existing git-status entry; work performed on GitHub branch only, with no changes to West worktree.

## Storage availability as observed around 21:05 UTC

| Venue/stream | Parquet shard count | Latest shard UTC | Notes |
|---|---:|---|---|
| SIG `data/sig_research/normalized_events` | 541 | 21:05:00 | **0 DEPTH_SNAPSHOT** in the latest 60 shards checked |
| SIG `data/sig_research/raw_events` | 72 | 21:05:02 | Data arriving |
| PM `data/004c_c_crossvenue/polymarket_research/depth_snapshots` | 1,512 | 21:05:00 | Snapshot cadence ~60 seconds |
| PM `data/004c_c_crossvenue/polymarket_research/book_changes` | 2,043 | 21:05:00 | Delta shards arriving |
| PM `data/004c_c_crossvenue/polymarket_research/observations` | 1,548 | 21:05:00 | Panel observation shards arriving |

The SIG last-60-shard sample yielded `DELIVERY=63,790`, `BOOK_DIRTY=63,790`, `BBO_SNAPSHOT=39,603`, `TRADE=2,237`, `MARKET_SNAPSHOT=1,446`, `CAPTURE_HEALTH=1,944`, `CONNECTION_BOUNDARY=6`, `DEPTH_SNAPSHOT=0`. These are *sample counts in 60 most recently modified shards*, not totals over 24 hours. They reinforce but do not independently prove the reported zero configured tracked full-depth exchange IDs. **BBO != executable depth.**

PM latest-35-depth-shard sample: 140 token IDs observed; 4,900 rows inspected; 35 had an empty bid or ask side. Largest time since locally observed book state was 1,113.4s, not an acceptable 5s fast-signal freshness guarantee. This is a **sample**; not a claim that 35 independent tokens are permanently untradeable.

## PM operational health (read-only SQLite `mode=ro`)

At **21:07:44.661 UTC** (`ingestion_health` id 119448):

- Websocket connected=true; subscribed markets=140, subscribed tokens=140.
- `book_uninitialized_delta_count=9,590,771`; versus **9,590,127** at 21:07:34.658 UTC, an increase of 644 in ~10 seconds. These are unexplained *ignored update counts* until a token-level diagnosis exists, not 9.59m independently proven corrupt subscribed books.
- `reconnect_count=1` since service start; `last_reconnect_reason='ConnectionError: websocket stream ended'`.
- Latest valid/book update and last message both around 21:07:44.658 UTC.
- `storage_failures=0`, `parse_failures=0`, `unknown_event_count=0`.
- Latest snapshot last status `OK panel_rows=140 depth_rows=0`. Immediately previous 21:07:34.658 health reported `OK panel_rows=140 depth_rows=140` (scheduled depth snapshot). The zero is therefore expected cadence behavior, not evidence of missing depth.

## Quality verdict and next safe test

- **SIG full-depth executable evidence: FAIL/NOT AVAILABLE in sampled current standalone capture**. Must establish an operator-approved bounded subset via isolated, governed, read-only path before an edge can be sized.
- **PM per-token full-depth trust: UNPROVEN**. Full CLOB snapshots and changing book frames are observed for subscribed tokens, but Parquet snapshots do not carry per-token session/seed/delta-drop provenance. The orphaned counter may be dominated by updates for unsubscribed opposite-outcome tokens. This has *not been established* because orphan-token IDs are not persisted in this stream.
- **Pairwise decision-time coverage and 24h hours: NOT MEASURED.**
- **Rate budget: UNKNOWN** until account-wide governor/path usage, 429 logs and maker high-priority reserve are checked. Do not start SIG depth polling based on the published nominal ceiling.
- **Observed fills, economic PnL, queue calibration: NONE MEASURED IN THIS AUDIT**. No research order was placed or simulated as observed.

Recommended narrow next step: read-only token-level diagnostic from an **isolated** PM websocket listener or a future approved capture instrumentation, gated on available RAM/CPU and no extra subscribed market traffic. Determine orphan-count fraction where `token_id in exact_subscribed_set` vs complement/untracked, prior and post reconnect, and seed completeness. Simultaneously quantify budget slack before a 5–10-ID SIG full-depth bounded SHADOW probe. Report both negative and positive episodes. Zero SIG full-depth currently forbids claiming paired executable research readiness.

### Provenance cautions

This audit did not sign/provide a raw-file hash manifest for every active West shard; new #222 coverage script can do so *offline* outside live capture, without modifying it. The on-host repo SHA differs from the PR HEAD, and none of the research branch software was executed on West. No 24-hour run has begun.
