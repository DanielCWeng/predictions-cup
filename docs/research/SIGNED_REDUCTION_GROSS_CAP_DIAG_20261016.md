# SIG capital recycling — gross-cap reduction screen (16 Oct 2026)

**Status: RESEARCH / DEFAULT-INERT. No LIVE execution permission.**

## Finding

At 15:49 UTC on 16 Oct, West's persisted RISK-002 risk-state database (queried with read-only SQLite) reported:

| Field | Current risk-state reading |
| --- | ---: |
| Equity | 99,821.84 SUSQies |
| Gross exposure | 100,000 shares |
| Gross cap | 100,000 shares |
| Net signed YES | −39,752 shares |
| Open-order exposure | 0 |
| Uncertain-order exposure | 0 |
| Reconciliation and account trust | True |
| Per-market absolute exposure buckets | 61 |

The risk-state account and mark timestamps were each approximately 14.5 seconds old when checked. Its 61 **absolute** per-market exposures exactly matched the 12 October authenticated portfolio. Absolute buckets cannot independently certify **signs**: the separate old snapshot had 21 positive YES exposures and 40 negative (held NO) exposures, but current signed-by-exchange quantities must be authenticated independently. The last persisted execution-envelope timestamp was 6 October; a journal is not a complete replacement for current venue confirmation.

The RISK-002 engine currently does:

1. `new_exposure_by_market[leg.market_id] += leg.quantity` on **every** candidate leg.
2. `new_gross = sum(new_exposure_by_market.values())`.
3. Denies when `current_gross + new_gross > max_gross_exposure`.

That prevents every positive-size order at exactly 100,000 gross, even one mathematically opposite to existing inventory. The existing design is **conservative by intention**, especially since a SIG SELL may canonicalise to a complementary BUY, and pending fills/cancellations are uncertain. This analysis is **not** a finding that the original cap should be bypassed.

## What is implemented here

`src/predictions_cup/research/signed_reduction_screen.py` is a **pure, research-only, never-called-by-LIVE function**. It reports whether an *independently supplied* signed position plus one hypothetical YES/NO order would **strictly lower** total gross exposure if that one order alone filled. It does not modify `risk/core.py`, maker, supervisor, services, configuration, order code, capital caps or account source.

For one exchange's signed YES inventory `q`, a whole-share hypothetical order signed delta `d` is *mathematically reducing* only if:

- `q * d < 0`;
- `abs(d) <= abs(q)` (no overshoot/position flip);
- `gross_after = gross_before - abs(q) + abs(q+d) < gross_before`;
- `gross_after <= gross_cap`.

YES BUY and NO SELL correspond to `d>0`; YES SELL and NO BUY correspond to `d<0`. Reject unrecognised sides or quantities.

**Fail-closed prerequisites:** current authoritative trusted/reconciled signed inventory; independently verified venue canonicalisation/netting; matching reported gross *and* net; no pending/uncertain orders or reservations; fresh source observation; market open; no global halt; exact exchange identity. Missing any proof returns a reject. The returned result **always** has `execution_authorised=False`, including positive mathematical screens.

Adversarial tests demonstrate opposite-direction rejection, overshoot, fractional share, unknown signed inventory, untrusted/stale account, unresolved orders and reservations, inconsistent signed gross/net, wrong outcome side, and never-authorised execution.

## Why this is not ready for LIVE

This design is intentionally insufficient to release an actual order. An independent #207 safety review must first establish:

- Current exchange-native signed lots and whether the held side can be reduced atomically through the requested YES/NO action.
- All venue order transforms and at-least-once network retries; cumulative fill quantity, parallel order reservations, cancel uncertainty and worst-case partial/duplicate fills across crash/reboot.
- A truly atomic reservation/compare-and-swap lock against all maker/residual/other senders; no re-use of the same inventory slice before authoritative reconciliation.
- Venue-verified settlement semantics, expiry, market status and source-time trust.
- An *economic* certificate that holding versus reducing has positive incremental expected final settled SUSQies after fees, fill slippage, uncertainty and replacement opportunity. A mathematically reducing trade can still destroy economic value.
- Independent source-only review, adversarial outage tests, existing two LIVE authorisations, and separate controlled deployment. No automatic promotion.

**Nothing in this PR is deployed or connected to actual execution.** It must not be imported into a production gate as a shortcut.

## Related evidence

- [Master #201](https://github.com/DanielCWeng/predictions-cup/issues/201): risk-state gross-cap diagnosis.
- [Lane A #225](https://github.com/DanielCWeng/predictions-cup/pull/225): 50%-haircut native SIG depth conditional unwind feasibility; tiny cross-PM relative quote advantage does not certify positive portfolio EV.
- [Safety #207](https://github.com/DanielCWeng/predictions-cup/issues/207): independent STOP-SHIP boundary.

## 16 Oct — added isolated durable lifecycle stress model (draft #241)

`research/reduction_reservations.py` is a standalone, **offline-only** SQLite
transactional reservation *model*, never imported by maker, execution, or RISK-002.
The model deliberately **cannot authorise orders**; each returned reservation
reports `execution_authorised=False`. It creates only a database file explicitly
named by an offline caller and must never point at LIVE account or journal DBs.

In its isolated prototype, `BEGIN IMMEDIATE` serialises allocation across
simultaneous processes and `synchronous=FULL` makes committed reserve/event
records durable. Each reservation occupies its **full original intended share
quantity** until a new authenticated signed account epoch matches all completed
cumulative order fills exactly. A partial fill, ACK, timeout, cancellation
request, duplicate replay or process restart **does not release residual capacity**.
The prototype rejects position flips, duplicate logical-intent conflicts,
negative/decreasing/oversized cumulative fills, mismatched signed ledger/hash,
reused account epochs and any unresolved order/cancel before reconciliation.

This is **not** an operational implementation of venue-authenticated,
atomic-with-order-send reservations. In particular, `SignedInventoryEvidence`
contains externally provided trust booleans rather than independently
cryptographically authenticated account facts; order event IDs and claimed
terminal status have no connector-authenticated provenance in this isolated
model. Another LIVE maker/sender does not participate in this database's lock.
No atomic commit across venue wire + database exists. A production design must
instead use one *shared* writer/serialiser and a journaled send-intent-before-wire
protocol, reserve and classify all other senders' open/uncertain orders,
reconcile via actual authoritative account/order IDs after restart, and preserve
all existing gross, per-market, order, event, strategy, drawdown and cutoff gates.
RISK-002 is **unchanged** and still denies placements at the cap.

**Economic status:** historical depth can support conditional unwind math,
not a priced exit now. Three held-NO relative-PM proxy candidates had 3/7/6
untouched-window samples, median full-size half-depth gross relative gaps of
3.50c/2.29c/1.00c per share. Sum ~160 SUSQies *gross*, illustrative extra
1c/share cost leaves ~95; a common 3pp PM proxy model error costs ~195.
The PM book is not independent calibrated settlement fair value, and no
validated positive-EV replacement exists. Four historical paired NO/NO races
have 4,640 conditional minimum payout units on 9,280 gross shares; matched
historical cost ~4,643.89, floor minus sunk cost -3.89 pre-fee, **not** current
marginal hold-vs-liquidate EV. Do not discard floor without rule verification.

**Capital decision: 0 executable reduction candidates, NO-GO for rotation**
without current authoritative per-exchange signed lots; independently audited
rules/FV and asynchronous native depth; active reservation lifecycle and fill
parity; genuine positive net replacement benefit; and #207 independent PASS.
Unwinding merely because gross exposure equals the cap is not permitted.
