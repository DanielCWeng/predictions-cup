# Lane B — maker depth mirror v1, offline source audit and controlled trial

**14 October 2026.** Research-only companion to #220, draft #226, Lane C #223 and independent safety #207. This document and `maker_depth_audit.py` make **no producer changes**. `NO_TRADE` remains the only independently admissible trade decision. No authority to merge/deploy #226, restart services, poll SIG, change maker settings, place orders or alter capture databases/WAL.

## Source contract and evidence boundary

The recorder exports `maker-depth-mirror-v1` JSONL, schema/event, a 32-hex run ID, EXACT exchange/market/tournament identity, maker-local `received_at`, triggering *realtime* revision (not book source revision), and exact decimal bid/ask quantity ladders. `venue_timestamp` and `venue_revision` are `null`; `source_clock_verified`, `source_sequence_verified` and `trusted_for_paired_paper` are **false**. Local maker's trusted reconciliation is not independent exchange event-time proof.

SHA-256-pinned, verified EXACT crosswalk is necessary but not sufficient for independent settlement rule equality. The frozen *research-screening* IDs are `879,947,948,957,958,959,960,969,970`, subject to exact input-mapping SHA and actual maker/exchange availability; 968 is excluded from PM usable depth in the prior Lane B evidence. Mapping artifact SHA reported by the current MAPPING-001 acceptance: `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`. **This is a candidate universe, not measured coverage or a set of independently settlement-certified opportunities.** Freeze precise mapping and acceptance file bytes in every manifest. If maker actually tracks fewer IDs, report the difference without adding reads.

Never rename `received_at` as a source/venue timestamp. A local sequence of maker snapshots can quantify observed spreads, local arrival gaps and quote changes, **not** the age of a quote at SIG, unseen updates between samples, FIFO queue priority, or causal source lead-lag. Trigger realtime revision does not restore the missing full-book sequence. Exactly one run ID per JSONL file is required; a reconnect can occur *within* a run and cannot be certified from v1. Absence of a gap does not prove continuity.

### Reproducible no-order invocation

Run only on an **immutable, offline copy** of a future approved recorder file outside all active data roots. First obtain an independent checksum (`sha256sum` or platform equivalent) and archive the accepted mapping and acceptance bytes. The following is a template; the SHA placeholder must be replaced with the actual externally recorded 64-character digest. The auditor rejects mismatched bytes and outputs that already exist.

```sh
PYTHONPATH=src python -m predictions_cup.research.maker_depth_audit \
  --input /isolated/raw/maker-run-001.jsonl \
  --expected-sha256 ACTUAL_EXTERNALLY_RECORDED_SHA256 \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 879,947,948,957,958,959,960,969,970 \
  --haircut 0.5 --gap-seconds 20 \
  --output /isolated/reports/coverage-001.json \
  --ledger /isolated/reports/rejections-001.jsonl
sha256sum /isolated/reports/coverage-001.json /isolated/reports/rejections-001.jsonl
```

The default order sizes are **1, 5, 10, 20 whole lots**. Depth is conservatively **halved and rounded down at each price level**, then walked to a marginal IOC limit and VWAP for SIG BUY YES and BUY NO (`1 - SIG YES bid`). The 50% depth haircut is a predeclared research stress, not an observed capacity estimate. Tick size is **unknown unless an independently checked `--tick-size` is supplied**; absence of that check must remain explicit. `--fee-per-share` is an optional externally validated aggregate all-in fee/stress assumption, never an empirical fee observation. If omitted, after-cost fields remain null. An IOC book walk is **not** a fill or a queue probability. Absolute cash/portfolio caps and future exits are not inferred.

For optional PM comparison, use separate independent PM receipt-ordered full-book JSONL with `venue: PM`, `instrument_id` equal to accepted mapped token, `received_at` UTC offset, and bid/ask `price,size` strings. Provide its externally recorded SHA with `--pm-input /isolated/pm.jsonl --pm-expected-sha256 ... --lane-b-paper-output /isolated/reports/b-to-c-no-trade.jsonl`. A PM snapshot must be timestamped **no later** than the SIG local receipt and within the default 2s local skew. Output contains two `lane-b-paper-v1` BUY_YES/BUY_NO rows per valid SIG snapshot, all `decision:NO_TRADE`, `paired_depth_trusted:false`, `sig_exchange_at:null`, `observed_pnl:null`, `observed_fill_qty:null`, and `fill_probability:null`. It can be passed into Lane C `bridge_lane_b` solely to preserve a rejection ledger; do **not** change the bridge trust gate. Its `conditional_only` comparison is a *hypothetical price differential across nonfungible currencies*, not executable arbitrage, a profit prediction, or a PnL figure. PM per-epoch seed/continuity and SIG native event time remain unverified, regardless of caller-supplied booleans.

## Coverage schema, denominators and thresholds

The immutable report uses `lane-b-maker-depth-audit-v1`. Core keys:

| Group | Fields and interpretation |
| --- | --- |
| Input integrity | `input.sha256`, `bytes`, checked external digest, final newline, max 32 MiB cap, `mapping_sha256` |
| Identity | `run_id`, `sessions_detected`, `universe`, per-exchange first/last local receipts; `session_boundaries_verified:false` |
| Record denominator | `record_lines = valid_two_sided_snapshots + rejected_snapshots`, reasons/line hashes in append-only rejection ledger |
| Offline research | `offline_liquidity_eligible_observations` = locally well-formed, accepted EXACT, nonduplicated, two-sided SIG books |
| Paired causal research | `independently_certified_causal_paired_paper_observations:0` **for v1 recorder alone**, independent of offline liquidity count |
| Market liquidity | Per exchange `spreads` quantiles; 1/5/10/20 size success counts, stressed VWAP and slippage quantiles, best bid/ask and total visible-depth quantity changes, repeated identical ladders |
| Coverage | Per exchange local inter-receipt gaps, observed >20s gaps, maximal local gap; *scheduled coverage, missing segments and native book ages null* |
| Producer data | Optional final accepted/written/dropped/error/pending counters; if available still `independently_attested:false` without separately checked complete logs/manifest |
| Unknowns | `native_sig_event_time:null`, `native_sig_revision:null`, `source_book_age_ms:null`, `reconnect_count:null`, `coverage_rate:null`, real fills/PnL `null` |

A 30-second gap is **not** proof of dropped samples without the maker's actual fetch schedule and attempted-poll denominator; zero recorded errors is not zero uncovered time. When file is partial/capped, preserve exact bytes, stop claims of complete session, link external counters and incident time range. Producer `accepted`, `written`, `dropped` and `queue_pending` must reconcile with exact run/terminal status; counter JSON alone is unauthenticated (especially across restart). Nothing in v1 carries a native SIG snapshot age distribution, so it remains `null`, **not** zero. Pair-local receipt age can be calculated conditionally but is not source book age.

## Research result available before activation

**Actual prospective mirror snapshots: NOT MEASURED** (as of this PR's repository-only evidence). Accordingly empirical spread/lot/slippage distributions, dropout rates and maximum realistic size are **NOT MEASURED**. Do not publish synthetic test ladders as SIG findings. The reader deterministically measures them after future evidence arrives; the synthetic tests demonstrate calculation and rejection only. The mathematically forced causal paired-v1 certificate count is **0** because native SIG event timing/sequence provenance does not exist in this source. This is a source-capability finding, *not* a real-time measured opportunity rate.

Baseline: NO_TRADE uses zero new exposure and zero incremental order PnL, not an invented total portfolio return. Any apparent positive price differential receives an indicative flag even after conservative haircut/marginal prices. More invasive strategy models wait until prospective, epoch-causal observations; #223 currently correctly reports null empirical strategy PnL, fill rates and deployable capacity.

For market triage after authentic files arrive, rank by **eligible local snapshot count and share of 5/10/20-lot walks**, p90 spread, p90 quote variation and p90 interreceipt gap. Use per-event diversity and PM per-token validity as penalties. A market is worth *further measurement* if it has repeated clean depth, conservative capacity at more than one order size and low unexplained gaps. **Do not rank by highest apparent midpoint dislocation alone.** Hold out separate days and do not use the resulting markets to imply actual fill capacity.

## Controlled approval-gated 30–60-minute rehearsal

**Gate A (not yet passed):** independent #207 code and A/B process PASS against exact #226 commit, including maker startup fail-injection, queue-full callback worst tails, writer ENOSPC/blocked IO, 0.2s synchronous join, maker cancels/ACK, memory growth and 429 handling. Green CI is not this approval. **Gate B:** named SIG operator change-control approval for an isolated non-LIVE maker replica and path, approved resource limits and abort owner. Without BOTH gates, do not activate #226 anywhere; no change to active maker settings or services.

1. Archive immutable `git` exact heads (#226, this reader, relevant PM source), installed package locks, crosswalk and acceptance digests. Freeze 9 research IDs; check actual maker's 76 tracked IDs from read-only evidence. Rehearsal inherits the *existing maker's naturally fetched REST cadence*, not a new polling schedule. No extra SIG REST requests or shared-budget claims without independent proof.
2. First exercise a **fully synthetic/offline maker state stream** with mirror OFF/ON A/B, then a separately approved **non-LIVE read-only replica** if operationally possible, not the active producer. Collect exact named session and run UUID, `START`/`STOP` UTC receipt clocks, maker attempt/fetch counters, output SHA256/bytes, producer accepted/written/dropped/error/pending, and per-market first/last observed receipts. Archive from a copied immutable directory, not the active WAL.
3. Capacity preflight: default #226 single output **32 MiB**, **128 queue slots**, no automatic rotation. Storage model: `bytes ≈ sum(exchange cadence × actual p90 encoded book bytes × duration)` plus 30% headroom. For a one-hour trial require forecast **<22 MiB**; otherwise shorten/abort or ask for separately reviewed bounded rotation. Host must retain **at least 10 GiB free and 20% free on target filesystem**, no active data-root output. Validate 0600/no symlink/EXCL behavior. No rewriting existing files.
4. Capture resource every 5s at minimum: RSS, CPU, load, event-loop p50/p95/p99/p99.9/max, ACK/cancel decision latency, rest governor request counters, 429s/timeouts, GC pauses, disk write rate, file bytes, queue high-water, accepted/written/drop counters, file cap/errno and service health. Compare paired OFF/ON A/B across typical 1/20/200-level books and heavy burst/blocked writer/CPU pressure. `record()`'s ~63–146ms observed synthetic tails in earlier #226 comments are **unresolved**, not maker-loop measurements.
5. Automatic stop on *any* event of: unexplained incremental SIG REST calls; 429/resource starvation; health/reconnect regression; one nonbaseline p99.9/max ACK/cancel/decision latency breach of the **preapproved** hard latency budget; p99.9 deterioration >10% vs controlled OFF A/B; sustained queue utilisation >80% for 10s; any lost observations not accounted; disk free <10 GiB or <20%; RSS >preapproved safe ceiling or >10% sustained increase vs OFF A/B; writer error, cap >24 MiB, truncation, stall >2s, or output provenance failure. Breaching any threshold fails the rehearsal. Do not auto-restart live services as an abort action.
6. Independent no-new-call proof: instrument the non-LIVE maker's **network-governor request entry counters / mocked SIG client**, compare identical synthetic book-fetch input with recorder OFF and ON, and inspect DNS/HTTP wire attempts and 429s. Counting only mirror module imports is insufficient. Publish before/after exact REST call transcripts and prove no separate depth poller.
7. Post-run, allow writer to close under operator observation; preserve any incomplete/capped file as *negative evidence*. SHA256 every raw file, producer counter/health file, PM source and report, with size, start/stop times, git hashes and manual reviewer signatures in an **external immutable manifest**. Explicitly log any missing session STOP/reconnect. The reader's report is a reproducible *analysis artifact*, not a signed capture itself.

## Later 24-hour prospective study — blocked pending new approval

The current #226 v1 file is one-shot capped at 32 MiB and cannot by itself guarantee 24h continuous depth. Require separate #207-reviewed **bounded rotation** and atomic per-file manifest/checksum/session accounting or a verified natural lower capture rate proven below cap. Do not schedule 24h recording, loop restarts or manually change LIVE env to bridge this gap. After Gate A/B plus rehearsal PASS, freeze the exact universe, cadence/budget, max retention (suggest no more than **2 GiB** of isolated research artifacts unless expressly approved) and output permissions. Continuously measure 5s time buckets with observed successful source reads versus expected reads, gaps, drops and PM seeded continuity; include 10/50/90-percentile per-side liquidity and 5s/30s/5m/1h *newly observed* arrival/exit ladders only when independently causal. Abort on any trigger in rehearsal and preserve negatives.

**Promotion requirements:** independently auditable SIG source-native timestamp/sequence (or an independently validated bounded staleness/complete-book receipt equivalence sufficient for the proposed slower strategy), full PM token epoch/seed/orphaned-delta continuity, same-event settlement and opposite side mapping, independent as-of decision and actual arrival/exit ladder, fee and FX/currency/capital economics, fill/ACK/cancel journal IDs with matched partial/no-fill denominators, event/day clustered significance and #207 source/economic approval. Without them Lane C consumes only `NO_TRADE` rejection or conditional descriptive liquidity economics. Do not set `paired_depth_trusted=true` based on any value of this audit.

## Local deterministic tests

```sh
PYTHONPATH=src python -m pytest -q tests/test_maker_depth_audit.py
ruff check src/predictions_cup/research/maker_depth_audit.py tests/test_maker_depth_audit.py
mypy src/predictions_cup/research/maker_depth_audit.py
```

All tests use synthetic local files and accepted-EXACT fixtures. Neither the runbook nor the module opens active capture DBs, invokes a SIG or PM API, changes maker state, or issues trades. Follow the dependency order of #209 -> #214 -> #223, and preserve the independent #207 review boundary.
