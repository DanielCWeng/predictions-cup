# Lane 3 — executable taker economics (25 October 2026)

**Status:** isolated branch; shadow evaluator; **NOT DEPLOYED**. No second order sender.
**Live baseline inspected:** West at `674b247c`, services active, live maker unit with RISK-002 / BUILD-009 and existing residual taker enabled at size 500, max signed inventory 2,500, 1.5pp PM-mid residual threshold, 15s cooldown, 2pp PM spread cap. West available memory was ~209–313 MB during the review; do not run concurrent heavy backfills on that host. East root filesystem was full.

## Measured historical role exposure

The 1–25 October LIVE journal contained 22,133 MAKE submissions, 503 FV-TAKE submissions, 322 role-attributed realtime taker fill events (78,699 YES-equivalent shares), 14 role-attributed realtime maker fill events (1,138 YES-equivalent shares), and 45 conflicting/unattributed realtime fill events excluded from the comparison. Submission and fill *events* are not unique orders or trades, and FV-TAKE order placement is not guaranteed execution.

The read-only `scripts/ops/lane3_fill_markouts.py` audit uses `REALTIME_FILL` plus originating strategy SUBMISSION and future SIG executable BBO, not `FILL_SUMMARY` (which can be the complement of the YES-equivalent realtime fill when SIG canonicalises a YES SELL into a NO BUY). 30-second covered taker markouts: 41 events, 7,254 shares, -0.0047877 weighted SUSQ/share, -34.73 SUSQ total. Maker: 3 events, 300 shares, -0.0033333/share, -1.0 SUSQ total. 300-second taker: 42 events, 6,886 shares, -0.0039217/share, -27.005 SUSQ total; zero covered maker events. Missing causal price observations and unmatched roles leave too little coverage for an unbiased comparison. This is **NOT realised P&L**, omits financing / any unmodelled fees, and must not be scaled to all fills.

Reproduction on West (read-only; run off-peak with low I/O priority):
```sh
nice -n 15 ionice -c3 python scripts/ops/lane3_fill_markouts.py \
 --journal /home/ec2-user/predictions-cup/data/execution_journal.sqlite3 \
 --tape /home/ec2-user/predictions-cup/data/sig_realtime.sqlite3
```

## Opportunity evaluator

`predictions_cup.maker.taker_economics.evaluate_taker_economics` is a **pure, read-only** decision prefilter. Inputs: signed YES direction, verified conservative fair bound (external best bid when buying YES, external best ask when selling YES), SIG full book levels, trusted-depth marker, book/reference ages, desired shares, available cash if known, fee and latency/adverse-selection buffers, and a minimum net edge. The evaluator:
- rejects stale/untrusted depth or external prices and malformed/off-grid books;
- sweeps true executable levels, clips to positive marginal after-cost edge and available size, recomputes VWAP and slippage, and applies conservative cash sizing at the worst touched price when a known cash amount exists (including complementary NO collateral on YES SELL, with exemptions only for provable inventory reductions);
- returns net expected value *conditional on fill*, limit tick, size, maximum settlement loss on the traded leg and maker-price alternative *conditional on a maker fill*. It never calls order APIs;
- does not assert an independent fair value, settlement guarantee, calibrated passive fill probability, or positive probability-weighted real-world edge from PM midpoint alone.

Current defaults: additional 0.005 latency + 0.005 adverse-selection per share, 0.005 minimum net edge, zero separately configured SIG fee (SIG experimental backtests also use zero fee; validate tournament fee schedule before gate activation), 1.5s SIG depth age, 5s external reference age. This is a **conservative heuristic**, not an empirically calibrated lower confidence bound.

The opt-in `PREDICTIONS_CUP_RESIDUAL_TAKER_ECONOMICS_GATE_ENABLED` setting defaults to false. When code is deployed it emits `TAKE economics shadow` decision lines for residual signals. When explicitly enabled, `TAKE economics gate` decisions run *before* the existing `evaluate_risk`, `build_execution_plan`, reservations and `SigLiveSink` call. Existing kill switch, positional caps, own-quote crossing protection, cancellation and recovery are retained. No separate process, API client or risk bypass exists.

## Deployment / evidence gates

**No live economics-gate activation and no new live orders were authorised by this branch.** Before promotion, Lane 1 must confirm trusted account balances and gross/signed exposure capacity, and Lane 2 must confirm that its maker quote changes will not cross the taker's prospective price. Required checks:
1. Merge/pin a release SHA through the existing supervisor rollout; do not overwrite another lane's live checkout.
2. Validate that SIG complete book actually populates `sig_depth_trusted`, external mapped quotes have <5s age, and all YES/NO conversions are correct in shadow logs on representative markets.
3. Audit fees, inventory and partial-fill rounding using authoritative SIG account/order records; perform an executable markout holdout with substantially improved coverage, including the 45 unattributed fills.
4. Confirm acceptable live CPU, memory, quote latency, API budgets, order rejects and no new cancel uncertainty; align with Lane 4.
5. Only then explicitly enable the economics gate in the shared environment and perform bounded live observations *through the existing maker sender*.

**Current certified executable opportunities:** NONE. There is no contemporaneous, fully verified depth + fair + cash certificate in this report supporting a risk-approved positive expected-value opportunity. Historic residual signals and 1-tick BBO differences are not proof.

## Tests

18 dedicated pure-economics tests and three coordinator integration cases added. Targeted suite: **38 passed** (25 Oct 2026), including old coordinator tests. Ruff checks passed. Existing live checkout and unit service were not changed.
