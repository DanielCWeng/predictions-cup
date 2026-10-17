# Lane A — Master-review closure evidence (13 October UK / 12 October UTC)

Research PR #225; issue #201; independent safety audit #207.
No LIVE maker/risk cap/config/journal modifications, no orders, no cancellations.

## P0: authoritative 568 venue fills versus LIVE journal

**Source:** 2026-10-12 21:22:16Z authenticated tournament REST GET, complete
568-fill pagination (1–6 Oct). Journal `execution_events` was queried through
SQLite URI **mode=ro**, including current WAL; neither DB nor WAL edited.
Repro: `scripts/research/reconcile_inventory_fills.py --snapshot SNAPSHOT.json
--journal execution_journal.sqlite3 --tournament-id EXPLICIT_ID
--out PRIVATE_DIR`.

Deterministic matching tiers: exact venue order/exchange, signed quantity
and price within 0.000001, plus realtime source timestamp within 2 seconds.
The journal event ID is matched one-to-one and never credited twice:
| Category | Venue fills | Interpretation |
|---|---:|---|
| Exact authoritative ID, one journal row | 76 | Fill-level confirmed |
| Exact authoritative ID, duplicate identical journal event rows | 9 | Fill-level confirmed ONCE; duplicate journal records are not two executions |
| Unique realtime source event (order/exchange/signed quantity/price, clock) | 104 | Fill-level economic match; no shared native venue fill ID |
| Has matching journal order with FILL_SUMMARY | 312 | Order-level evidence only; 191 match summary qty/price, 121 not exact |
| Has matching journal order/exchange event without valid fill match | 66 | Order-only evidence, not filled-event confirmation |
| Matching order ID with NULL exchange identity (ACK) | 1 | Order-only reference; venue/exchange origin unresolved |
| No journal order-ID reference anywhere | 0 | Every venue order ID appears in journal event scope |
| **Total** | **568** | **189 fill-level confirmed, 379 not confirmed at fill level** |

The previously reported *483 IDs absent from journal `fill_id`* is accurate
as a raw field comparison, but **not** 483 missing fills.
An earlier 52 unmatched order-ID count was computed **only against
fill-related journal event types**. Across all journal events, 0 venue fills
have an absent order reference. The previous result should not be used
to assert missing history.

Journal includes 94 AUTHORITATIVE_FILL rows (nine duplicate IDs),
301 REALTIME_FILL rows, 316 FILL_SUMMARY rows, and other ACK/cancel/
reconciliation events. All 67 order-only/no fill-proof records occurred
on 1–2 Oct; summary-only records span the entire sample. The 104
one-to-one realtime matches were all YES-side and preserve the same
signed quantity as venue; NO-side realtime ID/economics confirmation
remains incomplete. This is a **provenance gap**, not 379 proven errors.
Third-party actions, early ingestion coverage and differing NO-side
side/price semantics require independent follow-up; do not erase,
deduplicate or backfill anything based on this report.

Privacy: per-fill `fill_provenance.csv`, event IDs, order IDs,
strategy IDs and SHA256 input manifest are retained only privately on West
under `/home/ec2-user/alpha_lane_a_outputs/fill_provenance_20261013/`;
public GitHub receives the reproducible code, tests and aggregate counts.

## P0: contemporaneous active risk-cap proof

**23:26:05–23:26:14 UTC 12 Oct:** verified active
`predictions-cup-maker-live.service` PID 578967 running. Its own **loaded
process environment**, inspected only for seven safe-listed risk/runtime keys
before and after separate read-only authenticated SIG portfolio requests,
contains:
- `PREDICTIONS_CUP_RISK_CAPITAL_CONTROL_ENABLED=true`;
- `PREDICTIONS_CUP_RISK_MAX_GROSS_EXPOSURE=100000`;
- `PREDICTIONS_CUP_RISK_MAX_TOURNAMENT_EXPOSURE=100000`;
- `PREDICTIONS_CUP_RISK_MAX_OPEN_ORDER_EXPOSURE=50000`;
- LIVE=true, trading=true.

SIG REST read 23:26:11–13 UTC: 61 positions, abs signed YES **100000**,
net signed −39752, **zero** open orders, cash **34458.045**,
marked holdings **65963.09**, equity **100421.14**, unrealised
**389.19** SUSQies. With current 100000 active cap, unencumbered
new-gross headroom is **0 shares** despite cash. Account snapshot did not
prove internal unjournaled uncertain reservations; never infer positive
free headroom if unresolved operations or trust exists. No cap change.

## P1: paired economics and no-action table

Replaying the private 21:21Z individual books **at 23:26:13Z** with
an eight-second observation TTL and independently confirmed 100000 risk cap
results in **0/61 currently fresh+complete depth**. No new execution
read was made in this replay. Whole account simultaneous executable value
remains UNESTIMABLE. A 61-contract exposure table and 21 race-linked pair
review candidates are generated privately.

| Scenario-only screening group | Potential shares released | Executable now | Economic ranking |
|---|---:|---|---|
| FL-22 matched NO/NO | 5000 | NO | NO ACTION |
| MT-01 matched NO/NO | 2996 | NO | NO ACTION |
| VA-01 matched NO/NO | 768 | NO | NO ACTION |
| FL-16 matched NO/NO | 516 | NO | NO ACTION |
| FL-07 directional doubled | 5000 | NO | NO ACTION |
| AL-02 directional doubled | 5000 | NO | NO ACTION |
| TX-15 directional doubled | 5000 | NO | NO ACTION |
| IA-03 directional doubled | 5000 | NO | NO ACTION |

NO/NO **total 9280 gross shares**; directional four **20000**.
These are NOT EV-ranked, and the separate 21-group screening inventory
also includes smaller same-race paired positions. Current NO-side
exit costs, two-party status, third-party/void outcomes and latency-correct
two-leg orphan price bounds remain unverified. Earlier 140.34
SUSQies NO/NO strict-two-party unwind disadvantage is a *dated*
pre-cost theoretical comparator, not current price.

## Remaining independent acceptance gates

1. #207 reproduce fill provenance classes and inspect historical source
   ingestion/NO-side transformations, journal retention and duplicate events;
   do not mutate the journal.
2. Canonically prove per-race SIG settlement equivalence and signed
   NO close/pair cancellation with actual mapped outcomes; title regex
   is **screening only**, never contractual proof.
3. #220 furnish prospectively continuous, jointly fresh, full depth
   SIG books for both legs and realistic fee/latency/orphan bounds.
4. Independent fair probability intervals, marginal correlated scenario
   stress, verified opportunity-specific reinvestment net EV and competitor
   distributions needed before an actual top-ten by incremental portfolio
   benefit or estimated podium probability is meaningful.

**Final recommendation now: HOLD / NO ACTION.** No market is approved for
risk-reducing order simulation as an executable-now trade, no LIVE promotion.
