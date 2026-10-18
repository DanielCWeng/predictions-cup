# Alpha 2: zero-SIG-fee archived four-pair spread / latency reader

**RESEARCH-ONLY / OFFLINE / STDOUT ONLY.** This extension of existing DRAFT PR #319 does not call a venue, open a network connection, read a live maker journal, write any file, place orders, install a collector, change services or assert source/execute admission. It reads only paths explicitly passed by the local operator. SIG Predictions Trading Cup fees are **zero**; source-linked paid spread, size, latency, adverse selection and missing fills are the economically relevant quantities. Prior #318/#319 functionality stays intact.

## Repeatable command on an owner-permitted LOCAL existing archive

```bash
python -m predictions_cup.research.zero_fee_spread_latency --help
python -m predictions_cup.research.zero_fee_spread_latency \
  --sig-root /LOCAL/EXISTING/data/sig_research \
  --pm-root /LOCAL/EXISTING/data/polymarket_research \
  --start 2026-10-18T12:00:00Z --end 2026-10-18T14:00:00Z
```

The command prints JSON to STDOUT without creating outputs or transferring raw data. **Never paste raw archive output publicly**; share only cleared aggregate counts/rank. The optional `--pm-events /LOCAL/EXISTING/approved_pm_bridge_events.jsonl` is **not** equivalent to existing periodic Polymarket `depth_snapshots`. Use it only when actual recorded `alpha2-source-bridge-v1` REST_SEED_COMPLETE, every epoch-local ordered WS_EVENT (including no-change) and DISCONNECT are available; do **not** fabricate JSONL to force a match. Read-only command has bounded 1,000 Parquet shards and 250,000 rows per input stream; if exceeded, narrow the UTC window or prepare separate authorised local aggregate. Paths are local placeholders, not verified host paths. UTC input windows require explicit timezone.

For a row-level PM bridge input each JSONL item must carry `schema=alpha2-source-bridge-v1`, `venue=PM`, `kind=REST_SEED_COMPLETE|WS_EVENT|DISCONNECT`, a genuine saved UTC `received_at`, positive `connection_epoch`, and the exact frozen PM YES `token_id` for a seed or `full_books` keyed by actual allowed token IDs for a WS event. Standard producer outputs `issuer_authenticated=false`, `source_admitted=false`, `paper_admitted=false`; this analyser never upgrades them. Seed depth is a complete two-sided `full_depth={bids:[[price,quantity],...],asks:[[price,quantity],...]}`; WS post-apply complete snapshots use `full_books={token_id:full_depth}`, plus `ws_local_ordinal` and `gap_or_unanchored`/uninitialized information. A failed clock/ordinal/seed gate invalidates continuing same-epoch pairing until a real new epoch and per-token REST seed. This is a decoded source-claim proof screen, **not** original issuer wire proof.

## Existing source schemas understood

- SIG `data/sig_research/raw_events/**/*.parquet`: persisted `topic`, `tournament_id`, `session_id`, `connection_epoch`, `local_receive_at` and `raw_json` of original decoded batch. Only exact `tournament:<frozen-id>:market:{223,224,270,271}` and native `books[].exchangeId/asOf.at/asOf.sequence/bids/asks` count as source-shaped FULL. Require two-sided sorted uncrossed depth, conservative publisher→receipt freshness <=5s with clock uncertainty (research filter, **NOT** venue SLA), native sequence and publisher monotonicity within session/epoch, topic delivery revision continuity; first unanchored frame counts as an observed claim but is excluded from conditional match until continuity begins. `books=[]` count separately, never borrowed as depth. Raw missing/invalid source does not silently convert to valid book.
- PM `observations/**/*.parquet` and `depth_snapshots/**/*.parquet`: actual recorder periodic observed scalar values and **possibly truncated** sampled ladders. The reader counts/labels these `pm_periodic_two_sided_unproven` or incomplete, and never calls them genuinely completed REST-seeded source or uses them for a size-aware paired trade. `normalized_events` SIG BBO and PM `observations` scalar-only pairs may be shown in `scalar_proxy`, ALWAYS `NONEXECUTABLE_PROXY_NO_DEPTH_NO_FILL`.
- Real PM seed/WS bridge JSONL is optional, and currently has **no asserted deployed/on-host capture path**. Without it `n_pm_complete_seed=0` for the provided input, `n_matched_pairs=0`; neither number proves such events never existed outside the supplied input. The command does not open live SQLite/WAL, maker data or another websocket.

## Interpretation, ranking, sizes and failure modes

Four frozen EXACT/SAME SIG/PM YES identities only (m223/ex912, m224/ex913, m270/ex959, m271/ex960), independent of another party's YES or same-market NO. Candidate directions mean `BUY_SIG_SELL_PM_YES` and `BUY_PM_SELL_SIG_YES`; both require two executable legs and inventory/short/sale access that **has not been demonstrated**, so the output is a **conditional quote reference, not a claim of executable arbitrage**. For n=1/5/10/20, floor **each level's** available quantity before any VWAP calculation; no pooling fractional shares. At paired receive t only use genuine earlier PM observation whose uncertainty interval is distinctly before SIG and at most two seconds old. Directional per-share gap = *other venue size-n bid VWAP - buy venue size-n ask VWAP*. Conditional nominal upside = n×positive gap with SIG fee 0; not risk-adjusted, not filled and not net realised PnL. `effective_whole_size` is bounded by both full-ladder sides and does NOT represent queue, latency survival or actual fill. Rank top-five distinct (market, direction, n) by **maximum OBSERVED CONDITIONAL upside** instead of median; negative/no candidate => empty list, never fabricated top-five.

For each candidate, `markout.250ms_gap_delta`, `1000ms_gap_delta` and `2000ms_gap_delta` are **NULL** unless *both* venue source series contain recorded observations bracketing that exact future horizon within a maximum 100ms frame interval, with next observation within 50ms and no epoch crossing. Observed drift = future cross-venue reference gap minus entry reference gap; it is not real order fill/ACK latency or realised adverse selection. 1Hz frames are **not** 250ms/1s resting liquidity evidence. A wrong-contract paired **placebo** swaps the frozen PM YES token to the other party's contract; any resulting gap is explicitly invalid/nontrading. `severe_adverse_25c_stress` (loss of $0.25 per share) and `unhedged_long_leg_worst_case_stress` (long buy premium entirely lost) are *hypothetical scenario losses*, not measured lower-tail quantiles; `observed_tail_p05=NULL` unless genuinely estimable. `n_attempted`, `n_fill_observed` and actual fill rate remain NULL because no order/ACK/fill event is accessed, **not zero**.

## First REAL baseline known from prior West metadata, not from this CLI

| Date/UTC window | Four-market resident tracked native FULL L2 | Original raw FULL two-sided count for four IDs | PM four-token completed seed proof | Native+seeded matched books | Actionable 1/5/10/20 spread | True markout/fill |
|---|---:|---|---|---|---|---|
| 18 Oct 2026 14:31:12–14:32:37 UTC (#199/6251140399) | **0 tracked** (capture telemetry) | **UNKNOWN**; raw Parquet uninspected in that status check | **UNKNOWN**; 18,296,563 cumulative uninitialised deltas are across subscribed universe, not these four | **UNKNOWN / not certified** | **NULL** | **NULL** |

This is a *real measured capture-health baseline*, not a new archived-data scan, a 0/4 original raw event claim, a retrospective 24h window, or a verified edge. Separately #205/6218927375 observed that some genuine-looking original SIG full ladders exist in West `raw_events` despite zero normalised resident book rows. That source is not currently exposed through this chat's GitHub files, and no West host inspection is made by Alpha2. **Cheapest next step**: #199 runs this CLI or a narrower read-only aggregate on the already-authorised local original archive and reports UTC/shard/row/eligible counts **without transferring raw**; #205 then tests economics for any measured pairs. If PM REST-seed+ordered WS original archive is absent, the measured native count and scalar proxy are useful, but rank stays empty until actual paired depth is available.

## First genuinely measured 2h archive table — West #199 (NOT CLI replay)

West Ops separately read **existing local SIG/PM Parquet, no raw upload** in two completed bounded windows totaling **2026-10-18T16:50:00Z to 2026-10-18T18:50:00Z**. Exact reproducibility/evidence: [#199/6253925938](https://github.com/DanielCWeng/predictions-cup/issues/199#issuecomment-6253925938). The SIG input contained **163,293 decoded raw-event rows / 115 Parquet shards** across all exchanges; **3,533** target native FULL two-sided book frames with real original-formatted asOf sequence/UTC. PM input contained **16,240 periodic depth rows / 117 shards** across all 140 subscribed tokens; **464** target YES-token two-sided depth snapshot rows. Those PM snapshots are not original continuously seeded WS books.

| Frozen SIG market/exchange | Original SIG FULL books observed | PM two-sided periodic token snapshots | Within-segment earlier PM snapshot <=5s (**UNTRUSTED NONEXECUTABLE_PROXY**) |
|---|---:|---:|---:|
| 223 / 912 MT-01 Dem | 547 | 116 | 47 |
| 224 / 913 MT-01 Rep | 1,344 | 116 | 99 |
| 270 / 959 Maine Dem | 1,004 | 116 | 82 |
| 271 / 960 Maine Rep | 638 | 116 | 50 |
| **TOTAL** | **3,533** | **464** | **>=278** |

Observed archived target `books=[]` rows **0** in this bounded scan. **N_SIG_NATIVE_CONTINUITY=UNKNOWN; N_PM_ORIGINAL_REST_SEED=UNKNOWN; N_PM_CONTIGUOUS_WS_ORDINAL=UNKNOWN; N_QUALIFIED_PAIRS_FOR_EXECUTABLE_ADMISSION=0 (evidence decision); TRUE_REAL_FILLABLE_PAIR_COUNT=NULL.** The >=278 overlap count is not the output of the strict two-second clock-separated join in this new CLI, and not a proxy for fill probability, 250ms survival or fee-free realised PnL. No n=1/5/10/20 original two-venue depth economics can be honestly computed from current PM periodic archive without real seed/WS provenance.

Already-existing supervisor records spanning ~16:49–18:49 UTC showed **>=718,763** PM uninitialised deltas and **>=331** SIG revision-gap increments after segmenting counter resets; these are lower bounds, not all-four-target counts. A 24h **file inventory** found 1,405 SIG and 1,398 PM archive shards but its sampled row scan timed out, so there is **no measured 24h native-book total**. The lowest-cost next step is running this CLI locally only if an already recorded genuine PM REST-seed/WS bridge log exists; otherwise #199 can extract additional small *aggregate* native continuity and #205 can explicitly analyse NONEXECUTABLE scalar/directional sensitivity without promoting it. No collector restart, extra venue read or raw upload is implied.

Directives: [#206/6253691001](https://github.com/DanielCWeng/predictions-cup/issues/206#issuecomment-6253691001), [#201/6253638952](https://github.com/DanielCWeng/predictions-cup/issues/201#issuecomment-6253638952). No new PR, #318 untouched, no West service/collector change, no maker order/transfer, no off-machine source upload. #207 LIVE STOP-SHIP absolute.


## New directive #206/6254067762: GENUINE archived prices in a separate NONEXECUTABLE_PROXY mode

The strict `--pm-events` original REST seed/ordered WS path is **unchanged** and remains distinct. For locally recorded SIG native full books plus Polymarket **periodic** `depth_snapshots` (not REST-seed proof), run a bounded read-only per-market numeric bid/ask reference screen:

```bash
# This must run on the machine with BOTH real archived roots, not against invented fixtures.
# Paths below are OPERATOR placeholders; stdout aggregate JSON only, no file writes.
timeout 180s nice -n 15 ionice -c3 .venv/bin/python -B -m \
  predictions_cup.research.zero_fee_spread_latency \
  --periodic-depth-proxy \
  --sig-root /OWNER_LOCAL_SIG_ARCHIVE \
  --pm-root /OWNER_LOCAL_PM_ARCHIVE \
  --start 2026-10-18T16:50:00Z --end 2026-10-18T18:50:00Z
```

This invokes `archived_depth_proxy.analyze_archived_proxy`, isolated from `analyze` / `pm_original` / `ranked`: it **cannot increment admitted pair counts**. It reads only archived SIG `raw_events` and PM `depth_snapshots`, scans only overlapping UTC date/hour buckets, projects needed Parquet columns and keeps only four pinned market/token records in memory. Caps: **160 shards and 90 compressed MiB per stream**, 210,000 input rows per stream, maximum **3-hour** UTC request; exceeded budgets fail explicitly without retry/unbounded scan. Operator should split windows if needed. No venue requests, no maker/journal/WAL, no capture setup or service/config changes and no raw upload.

The output has four `per_market` rows with measured `n_raw_native_two_sided`, `n_pm_two_sided_periodic`, screened unique publisher revisions, rejected missing/stale/native-reversed clock counts and deduplicated publisher asOf, two nearest-earlier PM periodic matching bins **0–2 seconds** and **(2–5] seconds**, and `n_feasible` at n=1/5/10/20 in both BUY_SIG_SELL_PM_YES and BUY_PM_SELL_SIG_YES directions. Exact quantity is floored **per level** before depth VWAP (no fractional aggregation or invented partial fill). `gap_extremes_per_share` reports **max and min observed signed other-bid−buy-ask** on the price-only reference; n-feasible captures both positive and negative gaps. `top_five_measured_positive_depth_proxies` ranks distinct market/direction/n by maximum observed *n × positive price gap*; **NULL** if no positive fully-priced row. Tail fields are hypothetical -25c/share and isolated long-leg premium-loss stress, with empirical observed-loss tail and fill **NULL**. `wrong_market_placebo` deliberately swaps same-event partisan YES tokens; it is never considered tradable.

**Every field is `NONEXECUTABLE_PROXY_ONLY_NO_ADMISSION`** because PM periodic snapshots are not guaranteed complete original post-seed book innovations, clock uncertainty has not been attested, a recorded SIG native publisher frame is not evidence a quote survives the 0–5 second gap, and no order/ACK/queue/size-fill evidence has been observed. 250ms/1s/2s adverse markouts are literal **NULL**: periodic snapshots are not continuous book histories. Zero SIG platform fees do **not** remove paid spread, latency loss or queue risk. Real executable after-cost PnL stays NULL.

**Real baseline from #199/6253925938** remains the independently scanned **3,533** SIG native raw two-sided frames, **464** target periodic PM two-sided rows, and **≥278** simple prior-within-5s overlaps on 2026-10-18 16:50–18:50 UTC. These were not whole-share repriced; they are *counts only*, not an observed price gap. Until this new command runs locally against those same original private archives, every requested **genuine observed numeric gap/VWAP/top-five price rank remains NULL**. Do not invent prices from aggregate counts or from the deterministic unit tests. Coordinating with West #199 for one resource-limited on-host execution is the narrow remaining dependency.

