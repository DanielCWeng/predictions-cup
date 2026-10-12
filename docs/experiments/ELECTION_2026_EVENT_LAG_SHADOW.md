# Election event-to-SIG lag: pure SHADOW research scorer (2026)

**Status: RESEARCH-ONLY / NO LIVE FEED / NO EXECUTION / NO PROMOTED ALPHA.**
This first slice implements a pure, side-effect-free scorer and adversarial tests; it is not a deployed strategy, not connected to SIG, and cannot send orders.

## Hypothesis

If newly published, *verified* public election information changes a conservatively calibrated probability interval for a SIG contract, its executable bid/ask may not yet have updated. Detect the change quickly, but do **not** equate the apparent price difference with attainable profit.

The scorer lives at `predictions_cup.observe.election_lag.evaluate_election_lag`; it does not register with StrategyRegistry and does not bypass MAKE, RISK-002 or BUILD-009. A later integration must use the existing MakerRuntimeLoop and approved sink rather than introducing a second order sender.

## Input contract

A validated source adapter (not implemented by this PR) supplies:
- `ElectionProbabilityUpdate`: official result or verified media call, source identity, publication and local receive timestamps, pre-calibrated conservative lower/upper **SIG YES settlement-payoff** probability, authenticity flag, accepted EXACT SIG mapping flag, independently verified YES-payout semantic alignment and calibration flag. The source and SIG quote must share a stated monotonic-clock domain; raw monotonic values from separate processes or hosts must not be compared.
- `SigExecutableTop`: same-market YES-denominated best bid and ask in exact 0.005 ticks, visible shares, local receive monotonic time, open/tradeable, trusted BBO and depth.
- `ShadowCapacity`: trusted account, liquid cash, free gross exposure and owned YES inventory.
- Policy with bounded receive age (5s), book age (1s), publication-to-receive lag (10s), a **hypothetical placeholder** 0.02 per-share cost haircut, minimum edge 0.01, and max size 100. **These numbers are test fixture policy, not empirically calibrated or approved for LIVE.**

No raw vote lead is accepted directly as evidence for a confident outcome; an independently validated event-to-probability model and exact resolution rule must be reviewed before `probability_interval_calibrated=True` can be set. A media call by itself does not prove legal certification or final settlement.

## Candidate math / limitations

- BUY YES: `lower_probability - SIG_ask - hypothetical_cost`
- SELL *held* YES: `SIG_bid - upper_probability - hypothetical_cost`
- Size limited to visible opposite-side top depth; for BUY also cash/ask and currently free gross shares; for SELL also currently owned shares. No selling shares not owned.
- Source/mapping/account/depth/status/time failures emit `Abstain`. Locked or crossed YES books (`bid >= ask`) fail construction and cannot produce candidates. The scorer also requires an aware `now_utc` no earlier than event receipt and refuses candidate actionability from **2026-11-04 16:59:55 UTC**; this SHADOW research clock check never replaces the independent live HTTP cutoff in PR #202.
- All results are informational. Top-of-book depth does not establish queue/fill/latency, full-depth economics, or true remaining liquidity; order flow and cancellation can invalidate it before arrival.

A real economic qualification must integrate fee schedule, variable depth, opportunity-cost model, size-conditioned slippage, network RTT jitter, conservative adverse-selection haircut, per-race correlation, RISK-002 reservations, full balance/order account reconciliation, and the existing dual LIVE gates.

## Evidence plan

1. Verify official results feed licences, authenticity, event-time and publication lag; freeze exact SIG contract resolutions and call/official-count semantics. Maintain separate source receive time and publication time.
2. In read-only production capture, measure quote age and `source publication -> receive -> decision` p50/p95/p99/worst, matched by exact IDs. Test under stale and conflicting calls, revisions, late publications and 10x bursts.
3. Join quote traces with fresh follow-up book/real fills and quote withdrawals; classify **false signals**, quote size leakage, open-order rejections and false-profit/fill assumptions. Prospective paper and economic holdout are mandatory.
4. Benchmark independently of 005I five-minute reversal and OFI. A state association is not a transferable executable edge.
5. Only after prospective economic evidence: propose a separate, independently reviewed minimal integration behind existing risk/sinks. Neither LIVE gate nor this research contract grants permission to trade.

## Reproducibility / status

This branch has synthetic adversarial unit tests only: no live candidate sample, no fill conditioned markouts, no tested SHADOW deployment, no measured latency improvement, and **0 economically confirmed signals**. Reject any claim that passing unit tests proves alpha.

No systemd/config change, backfill, data deletion, risk cap change, order creation/cancellation or production deployment was performed.
