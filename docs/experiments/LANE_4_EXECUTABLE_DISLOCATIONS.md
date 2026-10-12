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
5. Available cash and gross exposure must be trustworthy and bounded *after
   pending reservations*. Any unresolved order is an unconditional abstain.
   Admission uses worst-case $1/share cash reservation plus fees and
   adverse fill/latency haircuts; no hypothetical borrowing or leverage.
6. Full opposite-side asks at the proposed size, exact worst-case payoff in
   every settlement state, fee rate, slippage, latency haircut and minimum
   edge must produce strictly positive conditional net economics. This is
   **not** a risk-free profit claim: separate `worst_orphan_unwind_loss`
   stress sums adverse exit losses if only a subset of legs fills or cancels.

### Cross-venue caveat

EXACT SIG↔PM *semantic* mapping is **not** a cash-convertible arbitrage by
itself: SIG tournament SUSQies and external-market collateral are distinct,
and an unhedged PM→SIG signal relies on a discretionary forecast. External
venue prices may be decision-time context, never synthetic executable
cross-venue proceeds. The only event-to-SIG route intended is **PR #203's
pure SHADOW event scorer**, once its independently validated official-source
adapter and depth/latency evidence exist. No second realtime consumer.

### Candidate fields

`AuditedOpportunity` records review/mapping/evidence SHA-256 hashes,
reference structural-certificate ID plus all signed economics, full-depth
size, conditional all-leg net edge, worst orphan unwind loss, residual TTL,
explanation, HMAC and **realised_pnl=None**. Recipients must verify the
signature with their own protected key, not trust a self-certified reviewer
flag or title.

### Falsification and prospective replay plan

Mandatory negatives: wrong PM contract/event, shuffled event identity,
changed settlement text, fake reviewed vector, mismatched canonical mapping
hash, stale/future source, stale/foreign-clock/crossed book, missing/cancelled
leg, pending/uncertain orders, reduced cash/gross, and spread/fee/latency
that destroys midpoint edge. Deterministic partial-fill stress *does not*
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
