# LANE B #220 — authorisation, default-deny source acquisition and PAPER runbook

**12–13 October 2026**. This is a deliberately inert research runbook. No LIVE maker/risk settings or trading credentials may be changed; zero production orders. **Do not start SIG polling on West yet**.

## Hard fact from live West read-only checks at 23:22 UTC, 12 Oct

| Source | Observed |
|---|---|
| Maker service | ACTIVE; 76 tracked SIG full-depth exchanges; SIG process governor configured 1.0 req/sec |
| Standalone SIG capture | ACTIVE; 0 tracked SIG full-depth IDs; research SQLite reports 0 dropped rows, queue high-water 220/16,384 and 1,196,737 research rows at sample time |
| PM capture | ACTIVE; 140 tokens; 10,370,132 uninitialised deltas and reconnect count=2, zero parser/storage failures |
| Account-wide shared REST budget | **UNSET in maker AND capture process environment** |
| New SIG probe permission | **DENIED** until *existing* services already participate in an approved shared budget and residual reads are independently measured |
| Valid full-depth standalone SIG–PM paired rows | **0 supported in inspected 60 most recent SIG normalised shards**; no `DEPTH_SNAPSHOT` events, so data-readiness gate is FAIL |
| Prospective duration and empirical PnL | **NOT MEASURED** |

This identifies the bottleneck more precisely than a generic request to add SIG IDs. Do **not** modify production environment or restart LIVE maker/capture merely to make this adapter run. The host's 1.0 req/sec maker and separate capture REST client governors alone cannot prove the venue's shared 100-reads/min limit. No verifiable residual account budget was supplied.

## 1. Preflight only, zero SIG API requests

Use an isolated research worktree based on draft PR #222 (never run the unmerged code in the active maker tree). The preflight accepts a small, frozen SHA-pinned EXACT subset such as `947,948,957,958,959,960` (Georgia, Louisiana and Maine Senate party legs). These are initial *measurement* candidates, not settlement-certified arbitrage legs. SIG 968 is excluded from the usable universe.

```bash
python -m predictions_cup.research.paired_sig_probe \
  --preflight-only \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 947,948,957,958,959,960 \
  --output /research/isolate/lane_b_sig_001.jsonl
```

**Expected on current West:** `BLOCKED`, `SHARED_BUDGET_NOT_CONFIGURED` plus host-service budget-missing reasons, **zero reads**. A green exit on `--preflight-only` indicates a completed check, not admission. Do not supply a fabricated approval file or set the shared path only in the new process: the code also reads the two active service process environments and requires identical paths.

For any approved future research SIG REST run, publish a short-lived **operator-signed in change control** JSON approval file *outside version control*, with `measured_at`, `expires_at`, `operator_approved:true`, `approval_ticket`, `shared_budget_path` (actual current shared flocked bucket), and `verified_free_reads_per_minute` **after** observed maker/capture/API 429 consumption, reserve and cross-key utilization. Never publish credentials or unrestricted process environment output. The local JSON is an audit input, not a cryptographic proof of permission. Independent #207 should verify the actual process env/budget state. This branch *cannot* itself enable a shared budget for the two production services, so SIG admission will remain blocked until a separate approved operational action.

If authorisation exists, the probe is capped at six accepted exchanges, at least 20s inter-round, maximum 6 reads/min, at most 60 minutes/180 attempts and background governor priority with a fixed one-attempt request policy. It aborts on REST errors and 429-like failures; append-only isolated `x` output with START/ERROR/STOP is mandatory. No order-capable API or second sender is created. **SIG `/orderbook` has no native exchange event-time or revision field.** The adapter explicitly writes `exchange_at:null`, `continuity:false`, `trusted:false`, and network start/receipt duration rather than fabricating source event clocks. Such rows show *visible REST depth*, not complete source-causal eligibility for latency claims.

## 2. Isolated Polymarket provenance tap

Unlike SIG REST, the bounded PM transcript uses a public websocket subscription and **no CLOB REST seeds**. This must be host-resource reviewed (CPU, disk floor, sockets), and an operator should supervise the first limited 30-second diagnostic. It has an explicit `--enable-network` gate, six-token cap, maximum 32 MiB output, 60-minute absolute duration cap, exclusive new output path, and records all MESSAGE, CONNECTED/DISCONNECTED/API_ERROR boundary epochs, including malformed events. No maker/capture restart or configuration change.

```bash
python scripts/research/lane_b_pm_ws_tap.py \
  --preflight-only \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 947,948,957,958,959,960 \
  --output /research/isolate/pm_raw_001.jsonl
```

On explicitly authorised start, use the same command replacing `--preflight-only` with `--enable-network --duration 30`, then **separately** replay the immutable transcript:

```bash
python -m predictions_cup.research.pm_provenance \
  --input /research/isolate/pm_raw_001.jsonl \
  --output /research/isolate/pm_provenance_001.jsonl \
  --mapping data/mappings/sig_polymarket_2026.json \
  --acceptance data/mappings/sig_polymarket_2026_acceptance.json \
  --exchange-ids 947,948,957,958,959,960
```

The second stage classifies each ignored delta as `SUBSCRIBED_UNSEEDED`, `UNSUBSCRIBED_OPPOSITE`, or `OUTSIDE_ACCEPTED_TOKEN_SET`, records all such rejected rows, independently labels full seeded snapshots and every unbroken local WS epoch, clears book state on disconnect or malformed partial source, and never fills missing depth. This is a *new limited prospective research listener*, not proof that the existing collector's historical 10-million counter contains exactly the same ratios. To certify the existing collector's own counter, instrumentation/event transcript *from that producer* would require independent reviewed capture changes. Any extrapolation from this separate listener must be clearly labelled.

The PM CLOB event shape does **not** supply a guaranteed monotonically increasing per-token source sequence. Therefore `source_sequence_verified:false`, `continuity:false` and `trusted:false` remain explicit even after a valid full websocket snapshot plus uninterrupted local receipt. This prevents the downstream PAPER join from mistaking local connectivity for provably lossless exchange depth.

## 3. Scientific graduation — staged measurement

1. **Present blocker (now):** supported valid paired SIG–PM full-depth count **0** in sampled canonical source. This is not an observed economic opportunity count. Record all lack-of-source decisions.
2. **First limited diagnostic:** under independently approved source/resource budgets, collect initial 30–60 minutes of PM raw epoch/seed/orphan data and SIG source observations, preserving hashes and failed/no-trade episodes. Do not extend after 429, missing account-wide control, gap or source mismatch.
3. **24h gate:** only after #207 trust review, run a bounded prospective PAPER recorder with accepted EXACT SHA, event/day IDs, full YES-denominated SIG ladders, PM mapped token/both orientation, source/receipt/as-of times, raw execution/ACK/cancel/fill/fee joins when available and *actual* post-latency arrival/exit observation timestamps. Report every 5-second denominator bucket (including no-data), valid coverage %, p50/p99 observable source-to-receipt latency where source timestamps exist, 0/actual API 429, reconnects, queue/storage drops and longest gaps. Missing source timestamp means `LATENCY_UNKNOWN`, not fabricated zero.
4. **Promotion remains forbidden:** hypothesis/model #205/#206/#223 may consume a read-only adapter when independently approved, but candidate features/arrival/exit records must be separately generated prospectively and never turn hypothetical fills into realised PnL.

## 4. Safe stop / rollback

Stop **only** the isolated research process, let its exclusive JSONL close and flush, and inspect its SHA-256 and budget/stop reason. No `systemctl restart`, signal against LIVE maker/capture, database VACUUM, WAL deletion, changes to systemd overrides, or production key use outside existing approved read-only scoped API. Failure to measure residual budget is a decisive **NO SIG RESEARCH POLLING** decision, not permission to guess.
