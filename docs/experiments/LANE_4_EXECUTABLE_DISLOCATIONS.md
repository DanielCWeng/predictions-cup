# LANE-4 — Executable cross-market dislocations / event-to-SIG delay

**Issue:** #206 · **Owner branch:** `lane/executable-dislocations` · **Authority:** SHADOW ONLY

## Result as of 2026-10-12

**NO PROMOTED ALPHA; no prospective fills or executable economic sample has been produced.**
This PR adds a strictly read-only certificate gate around existing
`analysis/structural_certificates.py` (STRUCT-SCAN-001), NOT a second streaming
consumer or order sender. Test positives are SYNTHETIC, not profitable real trades.

### Why not reuse previous models

- PRED-007 synthetic event index: worse than own-market and fixed top-source
  controls across 1s/2s/5s/10s/30s; forbidden as an alpha prior.
- R3-FV-001/M: no independent structural *point* fair-value improvement
  validated; sparse structural shock observations remain SHADOW learning only.
- 004C-C mapped PM→SIG: wrong-contract improvement stronger than real EXACT
  mapping, time-shift p=0.3696, crossing markout approximately
  -0.07984/share (250ms). SIG scalar BBO also lacked depth. No lag alpha.
- The draft #203 `observe.election_lag` has a useful pure event interface,
  but **only TOP of book** with fixture costs and no verified live source.
  It must not be treated as executable proof or duplicated as another listener.

## Scope and architectural separation

`audit_opportunity` accepts a *single* already-observed,
same-clock `SourceReceipt`, existing multi-level `ExecutableBook` receipts,
a reviewer-approved terminal-state relationship, a canonical `MappingDocument`,
and a conservative account budget. The function has no I/O, network,
StrategyRegistry, MakerRuntimeLoop, order intents, or LIVE credential access.

Existing `STRUCT-SCAN-001` does the bid/ask full-depth walk and exact
terminal-state min-payoff arithmetic. The new wrapper supplies missing strict
eligibility gates and an HMAC-SHA256 digest for the entire certificate and
evidence hash (signing key is passed by the caller; never committed). HMAC
proves possession of that key, **not** independent correctness of the claimed
settlement rule. Human proof acceptance must be genuinely documented first.

### Positive gate (necessary, never sufficient for LIVE)

1. Every instrument is a **distinct SIG exchange ID** bound to a `VERIFIED
   EXACT` canonical MAPPING-001 record. Every direct PM identity must belong
   to the independently observed source event; no NEAR, DERIVED, inferred
   mappings, unmatched tokens, or contradictory event groups.
2. A separately approved settlement rule from an immutable HTTPS source,
   reviewer identity and independent terminal-state vector hash must match
   the given relationship exactly. Hashes bind content; do not establish
   source authenticity or correct legal interpretation by themselves.
3. Local monotonic clock domain must match for event and SIG book receipts;
   source publication ≤ receipt ≤ decision, no stale source/quotes or future
   rows. Book wall-clock timestamps are receive timestamps; reject crossed,
   untrusted, missing, or non-tradeable depth. Strict quote TTL and source TTL
   are both carried forward, and the shorter residual TTL wins.
4. Only **BUY** legs are allowed in this version. The pre-existing
   `STRUCT-SCAN-001` SELL maths does not alone verify owned inventory or
   shorting permission. Selling would need a separate inventory-proof design.
5. Available cash and gross headroom must come from one fresh, trusted,
   reconciled account observation through `project_available_capital`.
   The adapter consumes `RuntimePortfolio` and canonical RISK-002
   `build_exposure_snapshot`: `gross_headroom_units = max(0, gross_limit -
   (position_gross + open_or_uncertain_order_exposure))`. The external
   `cash_available` argument means *authoritative spendable cash after
   venue holds*, **not** total equity or cash prior to open-order reservation.
   No reservations are subtracted again. Account snapshot identity, monotonic
   timestamp and shared clock domain are mandatory; proxy/untrusted, stale and
   active open/uncertain orders abstain. The adapter cannot authenticate a
   caller's cash/limit inputs: these must come from the authoritative account
   and active RISK-002 policy at the same reconciliation point. No direct
   realtime wiring is included in this research PR.
6. SIG orders require integer shares. The entire payoff vector must have a
   compatible bounded rational leg ratio (denominator up to 1000); compute its
   common whole-share lot, floor affordable+visible depth to whole lots,
   evaluate a full-depth certificate, then floor any continuous optimal size
   again to the feasible lot and **recompute** price, fees and worst-state
   edge. Fractional on-book SIG depth, an incomplete unit, and near-zero
   net edge are rejected. Each candidate includes the explicit signed
   `executable_leg_shares` integer amounts.
7. Full opposite-side asks at the valid whole-share size, exact worst-case
   payoff in every settlement state, fee rate, slippage, latency haircut and
   minimum edge must produce strictly positive conditional net economics.
   **Not** a risk-free profit claim: separate `worst_orphan_unwind_loss`
   stresses zero future exit liquidity when only a subset of legs fills.
8. The quote and event TTL are additionally limited by the #202 wire-level
   **2026-11-04 16:59:55 UTC safety cutoff**, not the 17:00 exchange lock.
   Any remaining action TTL below 100 ms is rejected. SHADOW eligibility
   cannot override, predict, or replace a real order-time cutoff.

### Cross-venue caveat

EXACT SIG↔PM *semantic* mapping is **not** a cash-convertible arbitrage by
itself: SIG tournament SUSQies and external-market collateral are distinct,
and an unhedged PM→SIG signal relies on a discretionary forecast. External
venue prices may be decision-time context, never synthetic executable
cross-venue proceeds. The only event-to-SIG route intended is **PR #203's
pure SHADOW event scorer**, once its independently validated official-source
adapter and depth/latency evidence exist. No second realtime consumer.

### Static accepted-mapping preflight (not arbitrage evidence)

The committed `data/mappings/sig_polymarket_2026.json` crosswalk
contains 237 SIG exchanges, including 140 EXACT; 134 of the EXACT
records have a nonblank PM event ID. Those 134 span 85 external events.
There are **48 multi-SIG-contract event groups**: 47 have two aligned
EXACT records; one (Nebraska Senate: Democratic/Independent/Republican)
has three. Examples of two-leg same-event groupings include Louisiana
Senate Democratic/Republican and AZ-01 House Democratic/Republican.

These are only **semantic review queues**. They are not exact complements
or exhaustive partitions merely because they have a shared PM event ID.
Write-ins, independents, ties, voids, recounts, term-of-office and
source-specific settlement wording must be audited contract by contract.
No live full-depth cost/fee/receipt evidence accompanies the mapping
snapshot. Do not rank the 48 by imagined midpoint edge.

### Explicit maximum orphan loss

`worst_orphan_unwind_loss` assumes every purchased leg consumes the
recorded full-depth asks but **zero later bid-side recovery**, plus fees
and conservative adverse execution haircuts, to cover a cancellation,
partial-fill or vanished-liquidity case. It is a gross adverse funding
stress, not a calibrated loss distribution, forecast expected loss,
or a promise of available exit liquidity. Negative/non-binary terminal
payoffs and candidates at/after the 2026-11-04 **16:59:55 UTC**
pre-wire safety cutoff are rejected.

### Candidate fields

`AuditedOpportunity` records review/mapping/evidence SHA-256 hashes,
reference structural-certificate ID plus all signed economics, full-depth
size and exact integer shares per leg, conditional all-leg net edge,
worst orphan unwind loss, residual TTL,
explanation, HMAC and **realised_pnl=None**. Recipients must verify the
signature with their own protected key, not trust a self-certified reviewer
flag or title.

### Falsification and prospective replay plan

Mandatory negatives: wrong PM contract/event, shuffled event identity,
changed settlement text, fake reviewed vector, mismatched canonical mapping
hash, stale/future source, stale/foreign-clock/crossed book, missing/cancelled
leg, pending/uncertain orders, fractional depth, incompatible leg
ratios, sub-integer gross/cash capacity, stale account projections, #202 wire
cutoff boundary and spread/fee/latency that destroys midpoint edge. Deterministic partial-fill stress *does not*
simulate fills; it reports a worst-orphan exposure, never assumes atomicity.

A prospective study may consume read-only capture from the existing realtime
speed owner after schema/clock-domain agreement and append candidate + abstain
events. It must deduplicate by independent event and relationship, avoid
triggering on repeated quote polls, and log post-decision book/trade/cancel
paths, acknowledgements, liquidity, outcome and 1s/5s/30s/5m markouts.
Report event count, eligible count, *all*-leg fill probability, net realised
PAPER PnL, size, capital consumption, uncertainty by event/day, and comparison
against null/no-trade and shuffled/wrong-contract controls. Do not open sealed
HOLDOUT for hyperparameter selection.

## Empirical accounting (not conflated with synthetic test cases)

| Evidence type | Real independent opportunities | Real fully filled bundles | Verified net PnL |
|---|---:|---:|---|
| Existing historical C/R3/PRED-007 | **0 qualifying full-depth certificates established** | Not measured | Not established; C crossing negative |
| This PR's deterministic proof tests | Synthetic only | 0 real | N/A |
| Prospective 2026 event-night paper study | Not started | Not started | Unknown |

`0 qualifying certificates established` means **absence of verified
evidence**, NOT a measured zero rate of true market opportunities. Historical
PM/SIG scalar BBO does not satisfy the new simultaneous full-depth + mapping
+ clock + actual costs + fill-path gate. The next meaningful measurement is
prospective read-only ingestion, which cannot be inferred from synthetic
fixture success.

## Promotion boundaries

Research pass: CI lint/type tests pass, signed certificate falsifications
hold, and feed data can be replayed causally. This does **not** promote
trading. Any advancement requires an independent red-team (#207), explicit
mapped settlement proofs, a statistically defensible positive *post-execution*
prospective economic sample, and existing dual LIVE permissions/risk systems.
No deployment, LIVE order sender, safety override, or feed registration here.
