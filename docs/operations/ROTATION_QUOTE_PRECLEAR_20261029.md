# West Rotation Preclear — implementation and deployment plan (29 October 2026)

## Mission and observed baseline

The SIG competition ends 4 November 2026. The West account was last verified at about 101,548 SUSQies marked equity, rank 543, with roughly 0.075 spendable SUSQies and no observed rotations. An earlier release fixed account-reconciliation instability; the subsequent cash-aware maker patch reduced unfundable quote submissions. These readings are **historical baselines**, not claims of current live profitability.

The production repository already has a strict, journalled active exit-to-entry sequence in `maker/residual_live.py`. Replacing that sequence would introduce unnecessary venue and journal risk. The major remaining code deficiency is **candidate starvation**: the sole `LiquidityWeightedAllocator.observe` drops any market with a resting order. When ordinary maker quoting is active on both sides, valuable exit and replacement pairs cannot get into the candidate ranking at all, so there is never a reason to clear the quotes.

## Architecture: dual allocator and expiring quote preclear

1. **Strict allocator (existing, unchanged):** continues refusing markets with resting orders; supplies only order-free, trusted, fresh candidates to `_execute_rotation_exit`. Existing independently evaluated RISK-002, BUILD-009 durable claim, fresh full-depth, no self-cross, cancel remainder and attributed fill guards stay intact.
2. **Advisory screen (new, opt-in):** use the same depth-aware `LiquidityWeightedAllocator` with `advisory_include_resting_orders=True`. It may evaluate a counterfactual switch *conditional on those maker orders being cleared*. Record blocked-by-order market IDs; its candidates **never** flow into an order sender.
3. **Preclear selector:** only when LIVE active exits are explicitly enabled and the fully trusted account and capital risk state are current, screen at most 3 independently profitable rotation pairs per 500ms. If either leg has a resting order, request a quote pause on both legs. Cap the lease to at most 8 exchange IDs.
4. **Maker quote lifecycle:** `MakerEngine.set_rotation_quote_pause_targets` installs a short 5-second expiry. The existing maker lifecycle receives `GateMode.CANCEL`, which cancels **only the maker's own resting quotes** through standard journalled cancellation. It does not cancel third-party orders, globally cancel the portfolio, or open a new position.
5. **Fresh admission:** once the canonical SIG account confirms all conflicting orders are gone, the strict allocator must observe both markets again. The existing `_execute_rotation_exit` independently reranks, checks same proposed replacement and size-specific EV, fresh executable depth, cash/account/risk and absence of any open/uncertain conflicting order. Only then may it claim a durable rotation ID and submit a reducing exit.
6. **Post-exit:** the existing separate entry path waits for a later trusted account observation, a verified signed inventory reduction and an attributed deduplicated exit fill. It independently reprices and reruns central risk before funding a replacement. If no replacement is justified it does not spend the released cash.
7. **Failure/expiry:** no trust, no fresh externally mapped probabilities, failed reconciliation or stale books means no new preclear; the lease is cleared or expires automatically. An uncertain cancellation must remain a blocker, never a synthetic terminal fill. A process restart also naturally clears all in-memory advisory state.

## Correctness boundaries

- **No synthetic alpha.** A PM-vs-SIG mismatch is only a candidate; all normal mapping, liquidity, cost, price and risk restrictions remain.
- **Never use an advisory result as live permission.** Its assessment is a conditional calculation, *not* a confirmation of no conflicting orders. A clean re-observation of the strict allocator is indispensable.
- **Cash scarcity:** no new maker bids without verified cash; previously installed quote filter remains. Active replacement still requires independent central RISK and venue funding, after a confirmed exit.
- **Mismatched contracts:** rotation candidates use only verified exact, same-orientation contract mappings.
- **Inventory:** neither exit nor entry may use a stale or missing account snapshot; do not double-count maker quotes or unresolved journal reservations.
- **Cost barriers:** maintain the configured `rotation_min_total_gain` (currently staged at 100 SUSQies in the previous West configuration) and the 8-cent minimum residual taker net edge. Never lower these automatically just to generate trades.

## Tests and acceptance gates

1. Unit: ordinary allocator remains blocked by resting quotes, advisory screen finds the same *conditional* candidate, and its market IDs are marked dirty.
2. Unit: once both markets have **fresh clean** snapshots, the ordinary allocator alone produces an executable rotation proposal.
3. Unit: preclear causes CANCEL, not PLACE; its TTL expires; a disabled feature never changes quotes.
4. Unit: preclear publishing is bounded and clears immediately upon trust loss; no active order send follows from an advisory result.
5. Regression: original liquidity allocator, maker engine and live rotation recovery tests remain green; full pytest, Ruff, mypy, maker benchmark and simulation pipelines pass on final commit.
6. Pre-deployment: verify live process, commit and environment flag. A Git checkout update is **not** a service restart. Do not assume active exits are enabled if the live environment still overrides the flag to false.
7. Post-deployment: trust success ratio, unresolved journal count, quote-cancel latency, advisory preclear counts, strict rotation candidate counts, attributable exit fills, cash released, replacement fills, rejected orders, and risk-halt reasons.
8. Rollback: set `PREDICTIONS_CUP_ROTATION_ACTIVE_EXITS_ENABLED=false` and restart safely; the advisory screen will not run, and the prior strict passive market-maker path remains.

## Operational decision rules

- If advisory candidate count remains zero, examine strict mapping coverage, external quote depth/size, freshness and net-gain hurdles. **Do not** paper over this with fabricated fair values.
- If candidates exist but cancellations fail, keep the operation blocked and investigate the standard maker cancellation/reconciliation state machine.
- If exits fill but entries cannot clear risk, retain released cash and report `RELEASED_ONLY`; do not force replacement or bypass portfolio caps.
- If the account becomes untrusted, revoke the quote-pause request and let the normal maker safety gate handle the untrusted account.
- If the gain becomes marginal before dispatch, immediately drop the candidate. Preserve meaningful economics over turn-count targets.

## Explicitly out of scope

No change to election outcome forecasting, external API sources, risk-profile limits, historical data retention, tournament configuration, SIG REST rate limits, or settlement rules. No unbounded parallel exits. Persistent rotation-entry recovery across process restarts is a separate follow-up requiring durable exit attribution and restart-replay tests.
