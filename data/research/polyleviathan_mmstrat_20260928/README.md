# Polyleviathan research package: market-making and profitable strategies in three election families (2026-09-28)

**Source:** this package was produced by the **Polyleviathan team** (repository https://github.com/DanielCWeng/polymarketwhale). It is contributed here as external research input. It is **not** a predictions-cup experiment, it has no row in `RESEARCH_LEDGER.md` or `RESEARCH_HANDOFFS.md`, and nothing in it has been promoted.

**Status: UNRECONCILED.** Three analyst runs are preserved side by side, with a separate set of orchestrator review notes. Their disagreements are listed below and deliberately **not** synthesised or resolved.

**005 discovery boundary:** the reports contain wallet leaderboards, top-strategy rankings and PnL magnitudes. Under `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md` these are **Layer 2** material: a discovery lane should not open `reports/` before freezing its own design. The method text, briefs, manifests and provenance are Layer 1.

## Question

Across three Polymarket election families, which wallets behaved like market makers? The working definition is: low or zero fees on fee-eligible fills, two-sided trading, and short-hold inventory recycling. On-chain maker/taker order role is explicitly **not** treated as real-world liquidity provision. The questions asked were:
- which of those market makers earned the most;
- what they did;
- what other (non-MM) strategies were profitable.

| Family | Election | Markets | Fills |
|---|---|---|---|
| HUN_2026 | 2026 Hungarian parliamentary | 200 | 840,134 |
| COL_2026 | 2026 Colombian elections | 380 | 693,573 |
| PER_2026 | 2026 Peruvian general | 393 | 1,212,643 |

## Layout

| Path | Contents |
|---|---|
| `reports/codex_mmstrat_v1/REPORT.md` | Analyst run 1 (first pass). Superseded in part by its own author's fix pass; kept unchanged. |
| `reports/codex_mmstrat_v2/REPORT.md` | Analyst run 1 fix pass: the "REPORT V2" section on top, the author's V1 text retained below it. |
| `reports/codex_mmstrat_phase2/` | Analyst run 2 (independent session): the 24-question strategy-anatomy deep-dive and 3 charts. |
| `reports/orchestrator_grades/GRADES.md` | Orchestrator review notes on the three runs, verbatim. |
| `briefs/` | The exact task briefs and addenda each run received. |
| `scripts/market_inventory/` | Scripts that built the election market universe. |
| `scripts/fills_export/` | Export of per-participant OrderFilled rows from the Polyleviathan trades lake. |
| `scripts/fee_extraction/` | Custody fee/refund leg attribution per fill. |
| `scripts/phase2/` | Phase-2 analysis scripts, as run. |
| `manifests/` | Fills manifest (row counts, sha256), fee corpus stats, data dictionary, fee notes, fee regimes. |
| `provenance/provenance.json` | sha256 and size for every input corpus and every uncommitted output, plus run identities and known reproducibility gaps. |

**Not committed:**
- raw campaign directories and agent run logs;
- per-fill corpora (~2.7M rows);
- wallet-metric parquets;
- result CSVs.

They are pinned by sha256 in `provenance/provenance.json`.

## Known disagreements between runs (unresolved)

1. **Rebates.** v1 adds wallet rebate payouts to PnL, allocated pro rata to in-family maker notional ($1.43M allocated against $292k fees). v2 and phase 2 exclude rebates from PnL and show them only as platform-wide, non-attributable context.
2. **"Zero-fee" test.** v1 computes zero-fee share over all fills. v2 and phase 2 restrict it to fee-eligible fills (custody-scanned, on/after the 2026-03-30 fee rollout); HUN is only ~28% eligible.
3. **MM classifier and population.**
   - v1 uses a loose screen and gets 2,127 wallets at 80%.
   - v2 uses a strict behavioural funnel over all 121,639 wallets and gets 668 passers.
   - Phase 2 applies a similar strict screen to a 783-wallet candidate subset only and reports 26 passers, so its funnel counts are not a census.
4. **Leaderboard composition.** v1's top "MM-like" wallets (e.g. one with a 4,103h median hold) do not appear in v2's strict leaderboard. v2's strict leaders are present in phase 2's roster.
5. **Holding-time and markout measures** differ in definition between runs (FIFO VW median vs distribution percentiles; phase 2 uses a next-own-fill price proxy for markouts because no order-book data was used).

## Reproducibility gaps

- The phase-1 (v1) and fix-pass (v2) analysis scripts were deleted by the analyst agent during its own scratch cleanup. Those numbers are pinned only by the method text and output hashes.
- Scripts hard-code absolute paths on the Polyleviathan host and read credentials from its environment; none are included here.
- Resolution payouts came from the Polyleviathan Postgres `outcome_resolutions` table (read-only).
