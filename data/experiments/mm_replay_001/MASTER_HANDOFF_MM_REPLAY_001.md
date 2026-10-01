# MASTER HANDOFF — MM-REPLAY-001

Branch: `research/mm-replay-001-spread-capture`

## Current state

```text
IMPLEMENTATION_READY
DATA_STATUS=BOUND
PRIMARY_SCOPE=baseline_sep
EXECUTION_UNIVERSE=140 EXACT / SAME
EXTERNAL_FV=BINARY_COMPLEMENT_BOOK_MID_ASOF
005F=FROZEN_PRE_ELECTION_UPDATE_HAZARD
SCIENTIFIC_RESULT=PENDING_RUNNING_REPLAY
REAL_SIG_ORDERS_SENT=NO
```

The landed DATA-003-linked order-book corpus is bound. Scientific interpretation remains pending until a strict-as-of full replay completes and its outputs are reviewed.

## Primary scientific replay

- Source dataset: `polyleviathan/sig-cup-data003-orderbooks` (dataset id 12299178).
- Primary period: `baseline_sep`, 2026-09-01 through 2026-09-28 UTC.
- Raw primary workload: 672 hourly parquet files, approximately 11.96 GB / 596,718,418 rows.
- Execution universe: 140 verified `EXACT`, `SAME` SIG↔Polymarket mappings.
- Primary compaction: 280 tokens total — 140 local execution tokens and their 140 binary complements.
- External FV: strictly-as-of `1 - midpoint(complement token book)`; local mid is never substituted for B1/B2/B3 external FV.
- Fill evidence: only `last_trade_price` aggressive-trade rows can fill the passive quote. `price_change` is a book update, not a trade.
- Fill side convention was checked empirically against preceding BBO on the landed corpus.
- Queue-aware execution remains unavailable unless explicit queue-ahead data exists.
- Maker fees and terminal unwind cost are intentionally unbound, so net P&L must remain null. Gross spread capture and markouts are diagnostics, not fee-complete profitability.
- This is a same-venue proxy laboratory for SIG maker mechanics. It is not historical PM→SIG latency evidence.

## Attempt registry

### Attempts 1–2

Immediate engineering failures before scientific computation:

- failure: `ModuleNotFoundError: predictions_cup`;
- cause: staged repository package was not visible inside the Kaggle script environment;
- scientific result: none.

### Attempt 3 — `mm-replay-001-spread-capture`

First full bound run to clear source staging.

- launch commit: `14d09c31ba0680f3b2e58d6ad52bdcacab8aa535`;
- useful as an engineering canary for shared B0/B1/B2 mechanics;
- superseded scientifically after identifying a frozen-005F 15-second grid-boundary ordering issue;
- do not use its B3/005F result as canonical evidence.

### Attempt 4 — `mm-replay-001-strict-as-of`

Canonical strict-as-of scientific design.

- launch commit: `79d94372737bccac8849512e1aea18a41c38afa8`;
- canonical Kaggle slug: `polyleviathan/mm-replay-001-strict-as-of`;
- 005F boundary semantics fixed so a non-boundary event cannot leak into the preceding 15-second scoring boundary;
- economics, universe, fill assumptions and predeclared parameter grid otherwise unchanged.

### Attempt 5 — `mm-replay-001-strict-as-of-fast`

Performance-only equivalent retry.

- optimization commit: `50b179fdf1e39692539b24b530f895b818e563b5`;
- launch/config head: `43da4356820ed6fc880b27bae92792870ddeba15`;
- Kaggle slug: `polyleviathan/mm-replay-001-strict-as-of-fast`;
- optimization: memoization of identical pure quote builds within a timestamp;
- intended economics and scenario grid are unchanged from attempt 4;
- queued behind Kaggle CPU quota when recorded here.

Attempt 5 must not be treated as a different research specification. If both strict-as-of attempts finish, compare their shared outputs for consistency before using either as evidence.

## Frozen 005F boundary

- candidate: `PRE_ELECTION|clock|UPDATE_HAZARD`;
- strongest accepted features: `genuine_15`, `genuine_60`, `genuine_age_s`;
- original frozen model/scaler artifacts are hash-gated against the accepted 005F fit-freeze manifest;
- no model refit;
- no rescue tuning on FINAL.

## Predeclared replay axes

- Markouts: 1s, 5s, 15s, 30s, 60s, 300s.
- Cancellation/reaction latency: 0, 25, 50, 100, 250, 500, 1000 ms.
- Fill assumptions: conservative observed-aggressive-trade and strict trade-through; queue-aware only if the data supports it.
- Policies: B0 local-mid; B1 external FV; B2 external FV + inventory; B3 external FV + frozen toxicity.
- External-edge sensitivity: 0–4 ticks at the predeclared reference latency.
- Chronological split labels: TRAIN / DEV / FINAL = 60 / 20 / 20.

## Required result bundle

The scientific result is not complete until these artifacts are retrieved and reviewed:

- `INPUT_AUDIT.json` / `INPUT_AUDIT.md`
- `COMPACT_AUDIT.json`
- `MARKET_INPUT_AUDIT.json`
- `005F_TRANSFER_RESULTS.csv`
- `005F_TRANSFER_SUMMARY.json`
- `MM_FILL_RESULTS.parquet`
- `MM_POLICY_RESULTS.csv`
- `MM_MARKOUTS.csv`
- `MM_TOXICITY_BUCKETS.csv`
- `FV_CONVERGENCE.csv`
- `LATENCY_SENSITIVITY.csv`
- `MARKET_BREAKDOWN.csv`
- `FINAL_REPORT.md`
- `MASTER_HANDOFF_MM_REPLAY_001.md`

The runnable job intentionally does not need to be edited merely to retrieve `MM_FILL_RESULTS.parquet`. Safe post-run output-manifest examples live beside this handoff and can be copied into `kaggle/jobs/` only after the target kernel is COMPLETE and a Kaggle slot is available.

## Post-run decision procedure

1. Confirm kernel terminal state and retrieve the standard result bundle.
2. Retrieve `MM_FILL_RESULTS.parquet` separately.
3. Run `scripts/mm_replay_001_split_summary.py` to keep TRAIN / DEV / FINAL evidence separate.
4. Compare B0/B1/B2/B3, latency sensitivity, markouts, FV convergence and 005F toxicity buckets.
5. Treat FINAL as evaluation only; do not tune or rescue a weak result against it.
6. Keep net P&L null until maker-fee and terminal-unwind assumptions are actually bound.
7. Record caveats before any live-candidate interpretation.

No DATA-001 / Hungary / Colombia / Peru / unrelated sample is used as scientific evidence for this branch.
