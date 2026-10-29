# West — Rotation candidate-starvation investigation and recovery (29 October 2026)

## Objective

Restore executable **positive-value capital rotation** without weakening the existing 8-percentage-point taker net-edge floor, 100-SUSQie minimum rotation improvement, independent RISK-002 admission, BUILD-009 reconciliation or two-phase confirmed-fill replacement process. Only West is in scope. Historical market data and existing journal records are not modified.

## Reproduced live baseline — read-only, 23:05–23:26 UTC

- West service PID `640124` started at 23:05:07 UTC, running merged rotation preclear commit `aedfc4ee`. The **running process environment** (not merely a file) confirmed `PREDICTIONS_CUP_ROTATION_ACTIVE_EXITS_ENABLED=true`, passive recycling true, 2,500-share tranche cap, minimum rotation gain 100 SUSQies, and minimum taker net edge 0.08.
- Every minute, `ROTATION shadow entries=0 exits=0 shortlisted=[] sightings=0 history=0`; no preclear, exit or entry submissions observed since restart. Taker reported high `economics_stale_data` / `economics_edge_or_depth_insufficient` counts; no taker executions.
- Authoritative account read showed **71 unsettled holdings**, total **154,276 shares**, and **0.075 SUSQies** available cash. The read-only capital analyser estimated approximately **101,574** marked equity and rank **543**. Marks are not executable proceeds or an expected strategy return.
- The currently loaded maker mapping is a **76-market exact/same-orientation** file, and the live subscription tracks those 76 markets. **70 held markets** overlap; the one additional held market is not subscribed. The full separate repo mapping contains **140 VERIFIED EXACT SAME** entries. Broadening subscription requires verified feed and latency capacity, not just a larger identifier list.
- A read-only authoritative order scan counted **15 resting orders in six markets**. Thus most held tracked markets had no resting orders at that instant, refuting the hypothesis that **quote conflict alone** explains the empty allocator.
- Account reconciliation continued succeeding, but `order_update_for_unknown_order` caused intermittent temporary UNTRUSTED transitions. `RISK marks fresh=71 fallback=0 unmarked=0` did not itself prove all other capital-risk gates were ready.
- An attempted *independent* unauthenticated public CLOB book lookup from West returned HTTP 403, so it did **not** independently establish whether external book size or freshness explains the zero candidates. Existing live websocket observations remain the appropriate source.

## Code audit — why previous logs could not locate the defect

`LiquidityWeightedAllocator.observe` returns early, without recording an explanation, for mapping, RISK-002 and account trust, uncertainty, own-market resting orders, SIG depth trust/age, missing/crossed/stale PM books, invalid depth, and absent marginal executable edge. A distinct limit in `rank` then rejects all candidate pairs whose **gain after liquidation concession, spread and fee buffers** is below 100 SUSQies.

The previous heartbeat reported only final `entries=0 exits=0`. This is insufficient to tell whether a candidate is blocked by a data/coordination problem, genuinely weak economics, or central risk. Both strict and advisory rotation allocators shared the same missing reasons.

Additionally, valid market observations arrive asynchronously. Each candidate cache expires after the configured fair-age window, normally five seconds. A market observation can expire before the other side of a potential rotation is examined. Clearing all caches through transient trust loss amplifies this. The standard `runtime.notify_sig` method can re-evaluate cached SIG, PM and account snapshots in one bounded cycle without any new REST calls.

## Delivered implementation

### A. Bounded eligibility/rejection telemetry

Both `LiquidityWeightedAllocator` instances maintain per-market last reason/timestamp and lifetime aggregated reason counters. Each `observe` records one primary reason:

- `mapping_not_exact_same`
- `capital_risk_not_ready`
- `account_not_trusted`
- `uncertain_orders_or_cash`
- `resting_order_on_market` (strict only)
- `sig_depth_untrusted`, `sig_book_missing_or_untrusted`, `sig_depth_timestamp_missing`, `sig_depth_stale`, `sig_bbo_untrusted`
- `pm_quote_missing_or_untrusted`, `pm_quote_stale`, `pm_two_sided_book_missing`, `pm_book_crossed`
- `sig_depth_invalid`, `sig_executable_depth_empty`, `pm_depth_size_missing`, `pm_depth_size_below_one`
- `no_marginal_entry_edge_or_reducing_exit`
- `eligible_entry_only`, `eligible_exit_only`, `eligible_entry_and_exit`

The heartbeat shows the last **60 seconds of distinct markets**, lifetime counts, and separate strict/advisory distributions. The RISK context is also independently classified as `kill_switch`, `capital_state_missing`, `risk_account_untrusted`, `risk_reconciliation_incomplete`, `risk_marks_untrusted`, `risk_exposure_untrusted`, `risk_uncertain_order_exposure`, or `risk_halt`.

No per-event logging and no persistent historical store are introduced; each hot-path evaluation performs a constant-size in-memory update.

### B. Executable economics frontier below the fixed hurdle

At each `rank`, the allocator records the number of entry and exit candidates, priced executable-depth candidate slices, the **best gain after costs** (even when it is below the hurdle) and the unchanged configured `gain_hurdle`.

A value of `best_gain=28.5, gain_hurdle=100` is a **diagnostic**, not authority to lower the hurdle or trade. A `best_gain=None` with entries/exits present means no adequately priced pair was formed; investigate identity, depth and capacity. No scoring formula or actual trade decision changes.

### C. Coordinated cache-only cross-market evaluation

If active rotation is enabled, account and RISK inputs are trusted, and at least one side of the strict candidate cache is empty, request a simultaneous cached-source recheck of at most **256 verified tracked exchange IDs**, at most **once every 15 seconds**.

This uses the existing `runtime.notify_sig` queue and `MakerSourceBridge.build_many` and **makes zero additional API calls**. It does not extend quote freshness limits, create fictitious quotes, cancel orders or place trades. Both allocators and the normal maker/taker still independently reject stale/untrusted prices and go through every existing RISK and journal gate.

The recheck is disabled when active rotation is off, when trust is absent, or when the allocator already has both entry and exit market observations.

## Tests required before merge

- Enumerate exact mapped, RISK, account, resting order, SIG depth, PM depth/staleness and insufficient-edge rejection reasons.
- Ensure strict and quote-aware advisory allocators remain different; advisory results never grant execution.
- Ensure an invalidating or stale observation clears a previous entry rather than keeping it tradeable.
- Verify an economically attractive pair with gain below 100 reports its frontier but is **not** admitted.
- Verify periodic recheck is bounded at 256 IDs, throttled at 15 seconds, trust-gated and disabled without live active rotation.
- Preserve all existing allocator/rotation/maker/recovery tests. Run Ruff, mypy, the complete test suite, full maker/candidate benchmarks and integrated simulation via the repository CI.

## Deployment/verification

1. Merge only when full CI passes. Fast-forward West's local `main` checkout. **Do not modify the active service's running memory or restart without authorised permissions.**
2. The authorised operator restarts `predictions-cup-maker-live.service` at a safe boundary.
3. Confirm new PID/commit, live flag values, healthy trusted refreshes, unchanged venue rejection rate and no new journal uncertainty.
4. Watch at least several `ROTATION diagnostics` heartbeats and the `ROTATION coordinated cached-source recheck` count.
5. Select the next fix from measured dominant reasons. Examples: persistent `risk_reconciliation_incomplete` requires authoritative risk trace; `pm_quote_stale` requires feed-quality investigation; `sig_depth_untrusted` requires book repair; `eligible_entry_only` / `eligible_exit_only` suggests one-sided universe or capacity; `best_gain<100` means price economics—not software—inhibit that trade.
6. A rotation is counted only from a journal-confirmed attributable reduction, spendable cash release and independently admitted replacement fill. Shadow sightings or maker cancels are not realised P&L.

## Rollback and exclusions

Revert the merge if new latency or false cancellations appear and restart under normal operator authority; alternatively disable active rotation in the actual service environment file and restart. No changes to historical data, risk limits, fee parameters, tournament settlement rules, PM-to-SIG mapping orientation, election result forecasting or East servers.
