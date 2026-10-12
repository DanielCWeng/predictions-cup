# LANE B / #220 — trusted paired depth research gate

**Mode:** read-only offline PAPER. **No LIVE sender, no market orders, no maker/environment changes.**
This branch does **not** claim 24 hours of prospective capture or any profitable trades.

## Evidence boundary (12 October 2026)

Repository evidence:
- `src/predictions_cup/sig/launch_storage.py` persists authoritative SIG `DEPTH_SNAPSHOT` events with `observed_at`, `venue_timestamp`, `revision`, `exchange_id`, `evidence_label`, and JSON bid/ask quantity ladders, **but only for configured tracked full-depth exchanges**.
- Issue #220's West snapshot says independent standalone SIG capture has **zero** tracked exchanges. The supplied sample therefore cannot certify active SIG depth. Treat source as BLOCKED until new isolated measurements exist.
- `external/polymarket/recorder.py`: `_seed_books` initialises only subscribed tokens, `price_change` ignores unknown token IDs but increments `book_uninitialized_delta_count`. Websocket updates may include tokens outside the 140 mapped subscription set; this is a **hypothesis**, not a verified explanation for the ~9.3m counter. Counters currently omit missing-token breakdown and per-epoch attribution. Independently capture token-level ratios before declaring the delta stream safe.
- PM Parquet `depth_snapshots` has `source_timestamp`, `state_observed_at`, `recorded_at`, depth and `book_hash`, **but lacks connection epoch, seed coverage, delta continuity and dropped-message evidence per snapshot**. `last_valid_book_update_at`, `book_valid`, or file count alone cannot prove full-book trust. Report `trusted_paired_coverage=null` until correlated independent health evidence exists.
- The issue reports 1,473 PM depth shards and 1,994 book-change shards at a dated West snapshot. These **are not measured here**. A 10-second health interval reporting `depth_rows=0` does not imply no depth when depth snapshots are scheduled every 60 seconds.
- The existing SIG capture uses a bounded observation queue; audit queue drops, high-water, state transitions and source clock drift before any attestation.
- PM NO on a binary mapped token is the complement of its YES book **only if** the two token outcomes are exhaustive/contract-equivalent; SIG NO is not an independent exchange. An EXACT mapping is necessary but not sufficient for event-level independent settlement certification.

### Provisional 10-exchange candidate universe

`879,947,948,957,958,959,960,968,969,970`.

From reviewer-accepted MAPPING-001 EXACT rows: Pennsylvania governor; Georgia Senate two party legs; Louisiana Senate two; Maine Senate two; Nebraska Senate three. These are **candidates for coverage measurement**, not liquidity-ranked trading opportunities or automatically valid complementary baskets. Georgia and Maine mapped party legs may correspond to distinct PM markets. Require side-by-side settlement terms and event-level adjudication. Exclude any market with stale resolution semantics, incomplete token side, missing active book or weak residual rate budget. A smaller subset is preferable to API starvation.

### Mandatory pre-deployment safeguards

1. Obtain explicit operator approval for a *separate* research-only observation path; read production databases **without modifying** SQLite/WAL, and never restart maker/capture. Prefer consuming existing files/shards; if more SIG REST depth is required, use existing governed client and host-shared token budget with a reserved high-priority allowance. Account-wide 100 reads/min is a stated nominal ceiling, **not measured spare capacity**. No polling before contemporaneous maker+collector budgets, 429 cooldowns, disk, CPU and API headroom are attested.
2. Place research output outside either active capture root. The included tools refuse overwrites. Preserve SHA-256 and provenance for every input shard and mapping JSON. Never write into a production SQLite path.
3. Collect per token: *subscribed* vs *received* token IDs, uninitialised updates, time since last successful full seed, reconnect episode, missing/dropped event or parse errors, per-epoch book hash, full-depth snapshot frequency, max stale duration and outage spans. Do not infer valid depth from ignored events.
4. Independent #207 reviewer to grade all no-trade/partial-fill/429/deadline, stale and timestamp leakage cases. #205/#206 may read the tape but must not treat PAPER marks as observed fills.

## Read-only coverage audit

After installing the project's existing dependencies into a **research-only environment**, execute against frozen/read-only source shards, preferably copied/hardlinked on the same host only where filesystem semantics and access permit:

```bash
python scripts/research/lane_b_coverage_audit.py \
  --sig-research-root /path/to/read_only_sig_research \
  --pm-research-root /path/to/read_only_pm_research \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 879,947,948,957,958,959,960,968,969,970 \
  --output /path/to/new_lane_b_output/coverage_001.json
```

The scorecard includes per-exchange counts, first/last receipt, longest seen inter-snapshot gap, crossed/zero-depth count, missing SIG revision count, PM state age, file SHA-256 and individual unreadable shards. **The word 'trusted' is intentionally never inferred by this audit.** If no SIG full-depth exists, the status is `BLOCKED_MISSING_DEPTH`. This scan does not measure API requests/429s; attach separate authoritative governance logs.

## PAPER as-of join contract

`predictions_cup.research.paired_depth` consumes an immutable, strictly *receipt-time-ordered* JSONL tape of **full book observations** with:

```json
{
  "venue": "SIG",
  "instrument_id": "947",
  "received_at": "2026-10-12T20:00:00Z",
  "exchange_at": "2026-10-12T20:00:00Z",
  "bids": [{"price": "0.48", "size": "4"}],
  "asks": [{"price": "0.51", "size": "3"}],
  "complete": false,
  "connected": false,
  "continuity": false,
  "trusted": false,
  "source": "UNATTESTED_SAMPLE",
  "connection_epoch": 0,
  "revision": null,
  "previous_revision": null
}
```

The illustrated frame is **intentionally rejected**, not a replay result or real price. Set each attestation to true **only after** independently establishing it from source logs/connection epoch, complete snapshot and valid continuity; changing booleans manually is not empirical proof. Convert SIG capture `payload_json` to these fields using exchange IDs, venue timestamps, received times and receipt-episode data. Convert PM capture `depth_snapshots` only together with its independently audited WebSocket lifecycle and seed status. Do **not** reconstruct dropped depth from last known scalar BBO.

Fields emitted per decision: SIG and PM source ID, market ID, condition and event, exact SHA-attested mapping/orientation, two full books, source/receipt timestamps, per-venue epoch/revision/trust flags, as-of decision clock, side, reason for rejection, executable whole-lot size, marginal price, VWAP, explicit cost assumptions, worst limit-price cash reserve and gross share requirement. Every accepted input updates **both** YES and NO assessments. A missing counterparty remains `NO_TRADE`. Duplicates and out-of-order rows produce explicit negative records.

Example invocation **only with an independently valid JSONL tape**:

```bash
python -m predictions_cup.research.paired_depth \
  --input /path/to/immutable_forward_frames.jsonl \
  --output /path/to/new_lane_b_output/paper_001.jsonl \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 947,948 \
  --sig-fee-per-share 0.001 --pm-fee-per-share 0.001 --stress-per-share 0.02
```

The cost numbers in that *illustrative* command are assumptions, **not venue fee findings**; no-cost inputs are rejected. The output's PM sell-side price is a **contextual reference only**; SUSQies and USDC are not fungible, and a cross-venue price discrepancy alone is not a tradable arbitrage. There are **no actual SIG fill timestamps, queue observations or observed returns** in this tape. No signal or positive value may be called a trading opportunity without account, lock, executable order limits, source latency and real fills.

## Prospective scorecard contract

The recorder may be counted as prospective only when source collection began *before* the decision and rows were irreversibly persisted with monotonically valid local receipt clocks. Record:

| Metric | Status at repository-only review |
|---|---|
| Accepted EXACT mappings in canonical summary | 140 |
| Provisionally selected IDs | 10 |
| Live SIG independent tracked full-depth (issue snapshot) | 0 |
| PM full-depth shard presence (issue snapshot) | Reported; independent trust unknown |
| PM orphaned-delta causes / fraction | Unknown; token-level attribution required |
| Decision-time valid paired coverage | **NOT MEASURED** |
| Clean continuous prospective elapsed hours | **NOT MEASURED** |
| Account-wide residual read budget, 429s | **NOT MEASURED** |
| Per-market 5s full-depth usable rate | **NOT MEASURED** |
| Empirically positive executable PnL | **NOT MEASURED** |
| Empirical venue fills / fill probability | **NOT MEASURED** |

**24-hour graduation:** with a new `run_id` and signed mapping SHA, publish per-venue source receipt freshness, continuous paired usable time / scheduled observation time, percent of 5s buckets with *both* trusted complete depth and costs, maximal consecutive gap and downtime episodes, 429, drops and host-resource load; retain no-trade rows and raw-input SHA hashes. 24 calendar hours elapsed with 2% valid paired coverage is **not** a 24-hour high-quality evidence set. Require #207 independent validation before any economic claims.

### Downstream eligibility

- **#205 / 005I:** frozen feature generation and causal unit fixtures can be tested. **BLOCKED** for post-cost prospective executable economic evidence until accepted paired depth, costs, cash/gross headroom, arrival-time book and actual fill calibration exist.
- **#206 / structural:** full-depth certificate arithmetic can be tested independently. **BLOCKED** for real opportunities until precise settlement-rule proof, *same-decision-time* full depth on all legs, source health, partial-leg loss and queue/capital constraints exist.
- **#207 / adversarial:** can run deterministic negatives immediately. Should challenge falsified trust booleans, reconnect-seeded book provenance, signed risk/economic certificates and open-book validity.
- **#201 / tournament:** no inventory-recycling executable close economics may be inferred from missing standalone SIG depth.

**Rollback:** stop only the isolated PAPER tool. Preserve raw source databases, WALs and unrelated systemd services; delete nothing. All output writes are new-file exclusive. `NO_TRADE` and `NOT MEASURED` are intentional first-class research results.
